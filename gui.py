"""Оконный интерфейс для локального экспорта чатов."""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from parser import Cancelled, export_folder, parse_export, save_result, summary


class Application:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Chat Parser")
        self.root.geometry("780x620")
        self.root.minsize(640, 550)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.result_path = None
        self.source = tk.StringVar()
        self.output = tk.StringVar()
        self.transcribe = tk.BooleanVar(value=False)
        self.offline = tk.BooleanVar(value=False)
        self.model = tk.StringVar(value="small")
        self.language = tk.StringVar(value="ru")
        self.status = tk.StringVar(value="Выбери папку экспорта Telegram")
        frame = ttk.Frame(root, padding=22)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Telegram Chat Parser", font=("Segoe UI", 21, "bold")).grid(row=0, column=0, sticky="w", columnspan=2)
        ttk.Label(frame, text="HTML-экспорт → текст, Markdown или JSON. Обработка на твоём компьютере.").grid(row=1, column=0, sticky="w", columnspan=2, pady=(4, 18))
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
        self.model_box = ttk.Combobox(audio_frame, textvariable=self.model, values=("tiny", "base", "small", "medium", "large-v3"), width=12, state="readonly")
        self.model_box.grid(row=1, column=1, padx=(8, 20))
        ttk.Label(audio_frame, text="Язык").grid(row=1, column=2, sticky="w")
        self.language_box = ttk.Combobox(audio_frame, textvariable=self.language, values=("ru", "auto", "en"), width=10)
        self.language_box.grid(row=1, column=3, padx=8)
        self.offline_box = ttk.Checkbutton(audio_frame, text="Только скачанная модель (без интернета)", variable=self.offline)
        self.offline_box.grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Label(audio_frame, text="small: качественнее; tiny/base: быстрее. Первая загрузка модели требует интернета.").grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))
        controls = ttk.Frame(frame)
        controls.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(18, 10))
        self.start_button = ttk.Button(controls, text="Собрать переписку", command=self.start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(controls, text="Отмена", command=self.stop, state="disabled")
        self.cancel_button.pack(side="left", padx=10)
        self.open_button = ttk.Button(controls, text="Открыть результат", command=self.open_result, state="disabled")
        self.open_button.pack(side="right")
        self.bar = ttk.Progressbar(frame, maximum=100)
        self.bar.grid(row=8, column=0, columnspan=2, sticky="ew")
        ttk.Label(frame, textvariable=self.status, wraplength=690).grid(row=9, column=0, columnspan=2, sticky="w", pady=10)
        self.log = tk.Text(frame, height=6, wrap="word", state="disabled", font=("Segoe UI", 10))
        self.log.grid(row=10, column=0, columnspan=2, sticky="nsew")
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
        folder = filedialog.askdirectory(title="Выбери папку HTML-экспорта Telegram")
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
                raise ValueError("Выбери расширение .txt, .md или .json.")
            if not output.parent.is_dir():
                raise ValueError("Папка для сохранения не существует.")
            overwrite = output.exists()
            if overwrite and not messagebox.askyesno("Замена файла", f"Заменить существующий файл?\n{output}"):
                return
            options = dict(transcribe=self.transcribe.get(), model=self.model.get(),
                           language=self.language.get().strip() or "auto", offline=self.offline.get())
        except Exception as exc:
            messagebox.showerror("Не удалось запустить", str(exc))
            return
        self.cancel.clear()
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
                result = parse_export(folder, progress=progress, cancel=self.cancel, **options)
                path = save_result(result, output, overwrite=overwrite, cancel=self.cancel)
                self.events.put(("done", (result, path)))
            except Cancelled as exc:
                self.events.put(("cancel", str(exc)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        self.latest_progress = None
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        self.cancel.set()
        self.status.set("Отменяю… Текущее распознавание или загрузка модели должны завершиться.")
        self.cancel_button.configure(state="disabled")

    def poll(self):
        update = getattr(self, "latest_progress", None)
        if self.running and update and not self.cancel.is_set():
            done, total, status = update
            self.bar["value"] = done / max(total, 1) * 100
            self.status.set(status)
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self.running = False
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
