"""Оконный интерфейс для локального экспорта чатов."""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from audio_queue import AudioQueue, model_cached
from parser import DEFAULT_MODEL, MODELS, Cancelled, export_folder, parse_export, save_result, summary


class Application:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Chat Parser")
        self.root.geometry("780x720")
        self.root.minsize(640, 550)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.audio = None
        self.prepared = None
        self.result_path = None
        self.source = tk.StringVar()
        self.output = tk.StringVar()
        self.transcribe = tk.BooleanVar(value=False)
        self.offline = tk.BooleanVar(value=False)
        self.model_labels = {
            f"{name} · {label.lower()} · ≈{size} МБ": name
            for name, (label, size) in MODELS.items()
        }
        self.model = tk.StringVar(value=next(label for label, name in self.model_labels.items() if name == DEFAULT_MODEL))
        self.language = tk.StringVar(value="ru")
        self.status = tk.StringVar(value="Выберите папку экспорта Telegram")
        frame = ttk.Frame(root, padding=22)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Telegram Chat Parser", font=("Segoe UI", 21, "bold")).grid(row=0, column=0, sticky="w", columnspan=2)
        ttk.Label(frame, text="HTML-экспорт → TXT, Markdown или JSON. Обработка локально.").grid(row=1, column=0, sticky="w", columnspan=2, pady=(4, 18))
        ttk.Label(frame, text="Папка с messages.html").grid(row=2, column=0, sticky="w")
        self.inputs = []
        entry = ttk.Entry(frame, textvariable=self.source)
        entry.grid(row=3, column=0, sticky="ew", pady=(5, 12))
        self.inputs.append(entry)
        button = ttk.Button(frame, text="Выбрать папку…", command=self.choose_source)
        button.grid(row=3, column=1, padx=(10, 0), pady=(5, 12))
        self.inputs.append(button)
        ttk.Label(frame, text="Куда сохранить (.txt, .md, .json)").grid(row=4, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.output)
        entry.grid(row=5, column=0, sticky="ew", pady=(5, 16))
        self.inputs.append(entry)
        button = ttk.Button(frame, text="Сохранить как…", command=self.choose_output)
        button.grid(row=5, column=1, padx=(10, 0), pady=(5, 16))
        self.inputs.append(button)
        audio_frame = ttk.LabelFrame(frame, text="Голосовые сообщения и кружки", padding=12)
        audio_frame.grid(row=6, column=0, columnspan=2, sticky="ew")
        audio_toggle = ttk.Checkbutton(audio_frame, text="Распознавать речь", variable=self.transcribe, command=self.audio_state)
        audio_toggle.grid(row=0, column=0, columnspan=4, sticky="w")
        self.inputs.append(audio_toggle)
        ttk.Label(audio_frame, text="Модель").grid(row=1, column=0, sticky="w", pady=10)
        self.model_box = ttk.Combobox(audio_frame, textvariable=self.model, values=tuple(self.model_labels), width=27, state="readonly")
        self.model_box.grid(row=1, column=1, padx=(8, 20))
        ttk.Label(audio_frame, text="Язык").grid(row=1, column=2, sticky="w")
        self.language_box = ttk.Combobox(audio_frame, textvariable=self.language, values=("ru", "auto", "en"), width=10)
        self.language_box.grid(row=1, column=3, padx=8)
        self.offline_box = ttk.Checkbutton(audio_frame, text="Только скачанная модель (без интернета)", variable=self.offline)
        self.offline_box.grid(row=2, column=0, columnspan=4, sticky="w")
        controls = ttk.Frame(frame)
        controls.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(18, 10))
        self.start_button = ttk.Button(controls, text="Собрать переписку", command=self.start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(controls, text="Отменить экспорт", command=self.stop, state="disabled")
        self.cancel_button.pack(side="left", padx=10)
        self.save_button = ttk.Button(controls, text="Сохранить результат", command=self.save_audio, state="disabled")
        self.save_button.pack(side="left")
        self.open_button = ttk.Button(controls, text="Открыть результат", command=self.open_result, state="disabled")
        self.open_button.pack(side="right")
        self.bar = ttk.Progressbar(frame, maximum=100)
        self.bar.grid(row=8, column=0, columnspan=2, sticky="ew")
        ttk.Label(frame, textvariable=self.status, wraplength=690).grid(row=9, column=0, columnspan=2, sticky="w", pady=10)
        self.log = tk.Text(frame, height=6, wrap="word", state="disabled", font=("Segoe UI", 10))
        self.log.grid(row=10, column=0, columnspan=2, sticky="nsew")
        self.audio_panel = ttk.Frame(frame)
        self.audio_panel.grid(row=11, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        frame.rowconfigure(10, weight=1)
        self.audio_state()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self.poll)

    def audio_state(self):
        enabled = self.transcribe.get() and not self.running
        self.model_box.configure(state="readonly" if enabled else "disabled")
        self.language_box.configure(state="normal" if enabled else "disabled")
        self.offline_box.configure(state="normal" if enabled else "disabled")

    def choose_source(self):
        folder = filedialog.askdirectory(title="Выбор папки HTML-экспорта Telegram")
        if folder:
            self.source.set(folder)
            self.output.set(str(Path(folder) / "full_chat.txt"))

    def choose_output(self):
        path = filedialog.asksaveasfilename(title="Сохранить переписку", initialfile="full_chat.txt", defaultextension=".txt", filetypes=[("Текст", "*.txt"), ("Markdown", "*.md"), ("JSON", "*.json")])
        if path:
            self.output.set(path)

    def start(self):
        if self.running:
            return
        try:
            folder = export_folder(self.source.get())
            if not self.output.get().strip():
                self.output.set(str(folder / "full_chat.txt"))
            output = Path(self.output.get()).expanduser().resolve()
            if output.suffix.lower() not in {".txt", ".md", ".json"}:
                raise ValueError("Нужно расширение .txt, .md или .json.")
            if not output.parent.is_dir():
                raise ValueError("Папка для сохранения не существует.")
            overwrite = output.exists()
            if overwrite and not messagebox.askyesno("Замена файла", f"Заменить существующий файл?\n{output}"):
                return
            transcribe = self.transcribe.get()
            model = self.model_labels[self.model.get()]
            language = self.language.get().strip() or "auto"
            offline = self.offline.get()
        except Exception as exc:
            messagebox.showerror("Не удалось запустить", str(exc))
            return
        self.cancel.clear()
        self.prepared = None
        self.running = True
        self.result_path = None
        for widget in self.inputs:
            widget.configure(state="disabled")
        self.audio_state()
        self.start_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.bar["value"] = 0
        self.status.set("Начинаю обработку…")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        def work():
            try:
                def progress(done, total, status):
                    # оставляю в очереди только последнее состояние, чтобы не тормозить окно
                    self.latest_progress = (done, total, status)
                items = [] if transcribe else None
                result = parse_export(folder, progress=progress, cancel=self.cancel, audio_items=items)
                if items:
                    self.events.put(("audio", (items, result, output, overwrite, model, language, offline)))
                else:
                    self.save(result, output, overwrite)
            except Cancelled as exc:
                self.events.put(("cancel", str(exc)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        self.latest_progress = None
        threading.Thread(target=work, daemon=True).start()

    def save(self, result, output, overwrite):
        try:
            path = save_result(result, output, overwrite=overwrite, cancel=self.cancel)
            self.events.put(("done", (result, path)))
        except Cancelled as exc:
            self.events.put(("cancel", str(exc)))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def prepare_audio(self, payload):
        items, result, output, overwrite, model, language, offline = payload
        try:
            download = not model_cached(model)
            if download and offline:
                raise ValueError(f"Модель {model} ещё не скачана.")
            if download:
                size = MODELS[model][1]
                if not messagebox.askyesno("Загрузка модели", f"Скачать модель {model}? Понадобится около {size} МБ."):
                    self.stop()
                    self.events.put(("cancel", "Загрузка модели отменена. Файл не сохранён."))
                    return
            self.prepared = (result, output, overwrite)
            self.audio = AudioQueue(self.audio_panel, items, model, language, download, self.audio_status)
            self.audio.pack(fill="both", expand=True)
            self.audio_status()
        except ImportError:
            self.events.put(("error", "Для распознавания нужны зависимости из requirements-audio.txt."))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def audio_status(self):
        audio = self.audio
        self.save_button.configure(state="disabled" if audio.busy else "normal")
        remaining = len(audio.pending) + (audio.active is not None)
        self.bar["value"] = 100 * (len(audio.items) - remaining) / len(audio.items)
        if audio.active is not None:
            self.status.set(f"Распознаётся {audio.items[audio.active].path.name}")
        elif audio.busy:
            self.status.set("Скачивается модель…" if audio.download else "Подготовка распознавания…")
        elif audio.warnings:
            self.status.set("Распознавание завершено с ошибками. Можно сохранить результат.")
        else:
            self.status.set("Можно сохранить результат или возобновить отменённые записи.")

    def save_audio(self):
        if self.audio is None or self.audio.busy:
            return
        result, output, overwrite = self.prepared
        result.warnings.extend(self.audio.warnings)
        self.audio.close()
        self.audio = None
        self.save_button.configure(state="disabled")
        self.latest_progress = None
        self.status.set("Сохранение…")
        threading.Thread(target=self.save, args=(result, output, overwrite), daemon=True).start()

    def stop(self):
        self.cancel.set()
        self.status.set("Отмена экспорта…")
        self.cancel_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        if self.audio is not None:
            self.audio.close()
            self.audio = None
            self.events.put(("cancel", "Экспорт отменён. Файл не сохранён."))

    def poll(self):
        update = getattr(self, "latest_progress", None)
        if self.audio is not None:
            self.audio.poll()
            self.audio_status()
        elif self.running and update and not self.cancel.is_set():
            done, total, status = update
            self.bar["value"] = done / max(total, 1) * 100
            self.status.set(status)
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "audio":
                    if self.cancel.is_set():
                        self.events.put(("cancel", "Экспорт отменён. Файл не сохранён."))
                    else:
                        self.prepare_audio(payload)
                    continue
                self.running = False
                self.prepared = None
                for widget in self.inputs:
                    widget.configure(state="normal")
                self.audio_state()
                self.start_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
                if kind == "done":
                    result, self.result_path = payload
                    self.status.set(summary(result))
                    self.bar["value"] = 100
                    self.open_button.configure(state="normal")
                    text = f"Сохранено: {self.result_path}\n" + "\n".join(result.warnings)
                else:
                    text = payload
                    self.status.set("Отменено" if kind == "cancel" else "Ошибка обработки")
                    if kind == "error":
                        messagebox.showerror("Ошибка обработки", text)
                self.log.configure(state="normal")
                self.log.insert("end", text + "\n")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def open_result(self):
        if self.result_path:
            try:
                if sys.platform == "win32":
                    os.startfile(self.result_path)
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(self.result_path)])
            except OSError as exc:
                messagebox.showerror("Не удалось открыть файл", str(exc))

    def close(self):
        if self.running:
            if messagebox.askyesno("Обработка идёт", "Отменить обработку? Окно можно закрыть после остановки."):
                self.stop()
            return
        self.root.destroy()


def launch():
    root = tk.Tk()
    ttk.Style(root).theme_use("clam")
    Application(root)
    root.mainloop()


if __name__ == "__main__":
    launch()
