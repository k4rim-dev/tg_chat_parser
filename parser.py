"""Локальная конвертация HTML-экспорта Telegram в TXT, Markdown или JSON."""

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Event
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

MODELS = {"base": ("Быстрая", 148), "small": ("Точнее", 486)}
DEFAULT_MODEL = "base"


class Cancelled(Exception):
    pass


class TranscriptionSetupError(RuntimeError):
    pass


@dataclass
class Message:
    id: str
    date: str
    sender: str
    text: str
    reply_to: str = ""
    reactions: list[str] = field(default_factory=list)


@dataclass
class Result:
    messages: list[Message]
    warnings: list[str]
    files: int
    duplicates: int
    audio: int


@dataclass
class AudioItem:
    path: Path
    kind: str
    message: Message
    parts: list[str]
    index: int

    def set_text(self, text):
        self.parts[self.index] = f"[{self.kind}: {text or 'тишина или неразборчиво'}]"
        self.message.text = "\n".join(self.parts)


def natural_key(path: Path):
    numbers = re.findall(r"\d+", path.stem)
    return (int(numbers[0]) if numbers else 1, path.name)


def export_folder(path):
    path = Path(path).expanduser().resolve()
    if path.is_file() and path.suffix.lower() == ".html":
        path = path.parent
    if not path.is_dir() or not list(path.glob("messages*.html")):
        raise ValueError("Нужна папка HTML-экспорта Telegram с messages.html.")
    return path


def media_path(folder, href):
    # не позволяю ссылкам из экспорта читать файлы за пределами чата
    url = urlsplit(href)
    if url.scheme or url.netloc:
        raise ValueError("внешняя ссылка на медиа")
    path = (folder / unquote(url.path)).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError("медиа за пределами папки экспорта")
    return path


class Transcriber:
    def __init__(self, model=DEFAULT_MODEL, language="ru", offline=False):
        self.model_name = model
        self.language = None if language == "auto" else language
        self.offline = offline
        self.model = None

    def __call__(self, path):
        if self.model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionSetupError(
                    "Для распознавания нужны зависимости из requirements-audio.txt."
                ) from exc
            try:
                self.model = WhisperModel(
                    self.model_name, device="cpu", compute_type="int8",
                    local_files_only=self.offline,
                )
            except Exception as exc:
                raise TranscriptionSetupError(f"Не удалось загрузить модель: {exc}") from exc
        segments, _ = self.model.transcribe(
            str(path), language=self.language, beam_size=5,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


def parse_export(source, *, transcribe=False, model=DEFAULT_MODEL, language="ru",
                 offline=False, progress=None, cancel=None, transcriber=None, audio_items=None):
    folder = export_folder(source)
    progress = progress or (lambda done, total, status: None)
    cancel = cancel or Event()
    warnings = []
    messages = []
    seen = set()
    duplicates = 0
    audio = 0
    audio_cache = {}
    recognize = transcriber if transcriber is not None else Transcriber(model, language, offline)
    pages = sorted(folder.glob("messages*.html"), key=natural_key)
    sender = "Неизвестно"

    def check_cancel():
        if cancel.is_set():
            raise Cancelled("Обработка отменена. Результат не записан.")

    for page_index, page in enumerate(pages):
        check_cancel()
        progress(page_index, len(pages), f"Читаю {page.name}")
        soup = BeautifulSoup(page.read_text(encoding="utf-8-sig"), "html.parser")
        nodes = soup.select("div.message.default")
        for index, node in enumerate(nodes):
            check_cancel()
            author = node.select_one("div.from_name")
            if author:
                sender = author.get_text(" ", strip=True)
            elif "joined" not in node.get("class", []):
                sender = "Неизвестно"
            message_id = node.get("id", "")
            if message_id and message_id in seen:
                duplicates += 1
                continue
            if message_id:
                seen.add(message_id)
            date = node.select_one("div.date.details")
            timestamp = date.get("title", "") if date else ""
            reply = node.select_one("div.reply_to a[href]")
            reply_to = ""
            if reply:
                fragment = urlsplit(reply["href"]).fragment
                reply_to = fragment.removeprefix("go_to_")
            parts = []
            attachments = []
            links = set()
            for link in node.select("div.media_wrap a[href]"):
                href = link["href"]
                if href in links:
                    continue
                links.add(href)
                classes = link.get("class", [])
                decoded = unquote(urlsplit(href).path)
                title_node = link.select_one("div.title")
                title = title_node.get_text(" ", strip=True) if title_node else ""
                if decoded.startswith(("voice_messages/", "round_video_messages/")):
                    audio += 1
                    kind = "ГС" if decoded.startswith("voice_messages/") else "Кружок"
                    label = f"[{kind}: {href}]"
                    if transcribe or audio_items is not None:
                        try:
                            path = media_path(folder, href)
                            if not path.is_file():
                                raise FileNotFoundError("файл не найден")
                            if audio_items is not None:
                                attachments.append((path, kind, len(parts)))
                            else:
                                progress(page_index + index / max(len(nodes), 1), len(pages),
                                         f"Распознаю {path.name}")
                                if path not in audio_cache:
                                    audio_cache[path] = recognize(path) or "тишина или неразборчиво"
                                label = f"[{kind}: {audio_cache[path]}]"
                        except TranscriptionSetupError:
                            raise
                        except Exception as exc:
                            warnings.append(f"{message_id}: {href}: {exc}")
                            label = f"[{kind}: {href} (ошибка распознавания)]"
                    parts.append(label)
                elif "photo_wrap" in classes or decoded.startswith("photos/"):
                    parts.append(f"[Фото: {href}]")
                elif "sticker_wrap" in classes or decoded.startswith("stickers/") or "sticker" in title.lower():
                    parts.append(f"[Стикер: {href}]")
                elif "animated_wrap" in classes:
                    parts.append(f"[GIF: {href}]")
                elif decoded.startswith("video_files/"):
                    parts.append(f"[Видео: {href}]")
                else:
                    parts.append(f"[Файл: {href}]")
            for unavailable in node.select("div.media_wrap div.media"):
                if unavailable.find_parent("a", href=True):
                    continue
                title = unavailable.select_one("div.title")
                parts.append(f"[{title.get_text(' ', strip=True) if title else 'Медиа'} (не скачано)]")
            body = node.select_one("div.text")
            if body:
                # сохраняю переносы строк, не разрывая слова вокруг жирного текста и ссылок
                for br in body.find_all("br"):
                    br.replace_with("\n")
                text = body.get_text().strip()
                if text:
                    parts.append(text)
            reactions = []
            for reaction in node.select("span.reactions span.reaction"):
                emoji = reaction.select_one("span.emoji")
                if emoji:
                    users = [item["title"] for item in reaction.select("div.initials[title]")]
                    count = reaction.select_one("span.count")
                    suffix = f" ({', '.join(users)})" if users else (f" ×{count.get_text(strip=True)}" if count else "")
                    reactions.append(emoji.get_text(strip=True) + suffix)
            if not timestamp:
                warnings.append(f"{message_id or 'без id'}: отсутствует дата")
            if not parts:
                parts.append("[Сообщение без текста или поддерживаемого медиа]")
            message = Message(message_id, timestamp, sender, "\n".join(parts), reply_to, reactions)
            messages.append(message)
            if audio_items is not None:
                for path, kind, part_index in attachments:
                    audio_items.append(AudioItem(path, kind, message, parts, part_index))
            progress(page_index + (index + 1) / max(len(nodes), 1), len(pages),
                     f"Обработано сообщений: {len(messages)}")
    check_cancel()
    progress(len(pages), len(pages), f"Готово: {len(messages)} сообщений")
    return Result(messages, warnings, len(pages), duplicates, audio)


def render(result, format="txt"):
    if format == "json":
        return json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n"
    lookup = {message.id: message for message in result.messages if message.id}
    lines = []
    for message in result.messages:
        reply = ""
        if message.reply_to:
            target = lookup.get(message.reply_to)
            excerpt = target.text.replace("\n", " ")[:60] if target else message.reply_to
            reply = f" (ответ на: «{excerpt}»)"
        reactions = f" {{Реакции: {'; '.join(message.reactions)}}}" if message.reactions else ""
        header = f"[{message.date}] {message.sender}{reply}"
        if format == "md":
            # экранирую пользовательский текст, чтобы экспорт не стал разметкой или HTML
            def escape(text):
                text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", text)
            lines.append(f"**{escape(header)}**\n\n{escape(message.text + reactions)}")
        else:
            lines.append(f"{header}: {message.text}{reactions}")
    return ("\n\n" if format == "md" else "\n").join(lines) + ("\n" if lines else "")


def save_result(result, output, *, overwrite=False, cancel=None):
    path = Path(output).expanduser().resolve()
    format = path.suffix.lower().lstrip(".")
    if format not in {"txt", "md", "json"}:
        raise ValueError("Результат должен иметь расширение .txt, .md или .json.")
    if path.exists() and not overwrite:
        raise FileExistsError(f"Файл уже существует: {path}. Выбери другое имя или --overwrite.")
    content = render(result, format)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".tg-parser-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        if cancel and cancel.is_set():
            raise Cancelled("Обработка отменена. Результат не записан.")
        if overwrite:
            os.replace(temporary, path)
        else:
            # создаю результат без перезаписи файла, появившегося во время обработки
            os.link(temporary, path)
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def summary(result):
    return (f"Сообщений: {len(result.messages)}; участников: "
            f"{len(Counter(item.sender for item in result.messages))}; "
            f"HTML-файлов: {result.files}; аудио: {result.audio}; "
            f"дублей пропущено: {result.duplicates}; предупреждений: {len(result.warnings)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", help="Папка экспорта или messages.html; без аргументов открывается окно")
    parser.add_argument("-o", "--output", type=Path, help="Файл .txt, .md или .json (по умолчанию full_chat.txt в экспорте)")
    parser.add_argument("--transcribe", action="store_true", help="Распознать ГС и кружки на CPU")
    parser.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL)
    parser.add_argument("--language", default="ru", help="Код языка или auto")
    parser.add_argument("--offline", action="store_true", help="Использовать только ранее скачанную модель")
    parser.add_argument("--overwrite", action="store_true", help="Разрешить замену результата")
    args = parser.parse_args(argv)
    if args.source is None:
        from gui import launch
        launch()
        return 0
    try:
        folder = export_folder(args.source)
        output = args.output or folder / "full_chat.txt"
        if output.exists() and not args.overwrite:
            raise FileExistsError("Результат уже существует. Укажи другое имя или --overwrite.")
        last_status = [""]

        def progress(done, total, status):
            if not status.startswith("Обработано") and status != last_status[0]:
                print(status, flush=True)
                last_status[0] = status

        result = parse_export(folder, transcribe=args.transcribe, model=args.model,
                              language=args.language, offline=args.offline, progress=progress)
        path = save_result(result, output, overwrite=args.overwrite)
        print(summary(result))
        for warning in result.warnings:
            print(f"Предупреждение: {warning}", file=sys.stderr)
        print(f"Сохранено: {path}")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Ошибка: {exc or 'обработка прервана'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
