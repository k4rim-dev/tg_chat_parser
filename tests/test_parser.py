import json
import tempfile
import unittest
from pathlib import Path
from threading import Event

from parser import Cancelled, media_path, parse_export, render, save_result


def message(number, body, sender="Аня", extra="", joined=False):
    author = f'<div class="from_name">{sender}</div>' if sender else ""
    return (f'<div class="message default{" joined" if joined else ""}" id="message{number}">'
            f'<div class="date details" title="19.11.2026 12:34:56 UTC+03:00"></div>'
            f'{author}{extra}<div class="text">{body}</div></div>')


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def page(self, name, text):
        (self.folder / name).write_text(text, encoding="utf-8")

    def test_pages_replies_duplicates_and_joined_sender(self):
        self.page("messages.html", message(1, "Привет<br>мир <b>жирный</b>!"))
        self.page("messages2.html", message(1, "Привет") + message(2, "Ответ", "", '<div class="reply_to"><a href="messages.html#go_to_message1">reply</a></div>', True))
        self.page("messages10.html", message(3, "Конец", "Борис"))
        result = parse_export(self.folder)
        self.assertEqual([m.id for m in result.messages], ["message1", "message2", "message3"])
        self.assertEqual(result.messages[1].sender, "Аня")
        self.assertEqual(result.messages[0].text, "Привет\nмир жирный!")
        self.assertEqual(result.duplicates, 1)
        self.assertIn("ответ на: «Привет мир жирный!»", render(result))
        self.assertEqual(json.loads(render(result, "json"))["messages"][1]["reply_to"], "message1")

    def test_media_reactions_and_audio_cache(self):
        media = '<div class="media_wrap"><a href="round_video_messages/a.mp4"><div class="media"><div class="title">Video message</div></div></a></div>'
        reactions = '<span class="reactions"><span class="reaction"><span class="emoji">👍</span><div class="initials" title="Аня"></div></span></span>'
        (self.folder / "round_video_messages").mkdir()
        (self.folder / "round_video_messages/a.mp4").write_bytes(b"test")
        self.page("messages.html", message(1, "", extra=media + reactions) + message(2, "", extra=media))
        calls = []
        result = parse_export(self.folder, transcribe=True, transcriber=lambda path: calls.append(path) or "тест речи")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.audio, 2)
        self.assertEqual(result.messages[0].text, "[Кружок: тест речи]")
        self.assertEqual(result.messages[0].reactions, ["👍 (Аня)"])

    def test_audio_items_preserve_cancelled_attachment(self):
        folder = self.folder / "voice_messages"
        folder.mkdir()
        for name in ("a.ogg", "b.ogg"):
            (folder / name).touch()
        media = ('<div class="media_wrap"><a href="voice_messages/a.ogg">a</a>'
                 '<a href="voice_messages/b.ogg">b</a></div>')
        self.page("messages.html", message(1, "Подпись", extra=media))
        items = []
        result = parse_export(self.folder, audio_items=items)
        self.assertEqual(len(items), 2)
        items[1].set_text("Вторая запись")
        self.assertEqual(result.messages[0].text,
                         "[ГС: voice_messages/a.ogg]\n[ГС: Вторая запись]\nПодпись")

    def test_missing_audio_and_unexported_media(self):
        media = '<div class="media_wrap"><a href="voice_messages/missing.ogg">voice</a><div class="media"><div class="title">Photo</div></div></div>'
        self.page("messages.html", message(1, "подпись", extra=media))
        result = parse_export(self.folder, transcribe=True)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("Photo (не скачано)", result.messages[0].text)
        self.assertIn("подпись", result.messages[0].text)

    def test_paths_and_invalid_input(self):
        for href in ("../secret.ogg", "%2e%2e/secret.ogg", "https://example.com/a.ogg", "C:/secret.ogg"):
            with self.subTest(href=href), self.assertRaises(ValueError):
                media_path(self.folder, href)
        with self.assertRaises(ValueError):
            parse_export(self.folder)

    def test_cancellation_and_no_overwrite(self):
        self.page("messages.html", message(1, "текст"))
        result = parse_export(self.folder)
        output = self.folder / "out.txt"
        save_result(result, output)
        with self.assertRaises(FileExistsError):
            save_result(result, output)
        cancel = Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            parse_export(self.folder, cancel=cancel)
        before = output.read_bytes()
        with self.assertRaises(Cancelled):
            save_result(result, output, overwrite=True, cancel=cancel)
        self.assertEqual(output.read_bytes(), before)
        self.assertFalse(list(self.folder.glob(".tg-parser-*")))

    def test_forward_reply_and_markdown_escaping(self):
        self.page("messages.html", message(1, "&lt;script&gt;*hi*", extra='<div class="reply_to"><a href="#go_to_message2">reply</a></div>') + message(2, "позже"))
        result = parse_export(self.folder)
        self.assertIn("ответ на: «позже»", render(result))
        markdown = render(result, "md")
        self.assertIn("&lt;script&gt;", markdown)
        self.assertIn(r"\*hi\*", markdown)


if __name__ == "__main__":
    unittest.main()
