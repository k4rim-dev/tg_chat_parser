import multiprocessing
import tkinter as tk
from collections import deque
from tkinter import ttk

from parser import Transcriber, TranscriptionSetupError


def model_cached(model):
    from huggingface_hub import try_to_load_from_cache

    repo = f"Systran/faster-whisper-{model}"
    files = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
    return all(isinstance(try_to_load_from_cache(repo, name), str) for name in files)


def audio_worker(connection, model, language, download):
    try:
        if download:
            from faster_whisper.utils import download_model

            download_model(model)
        connection.send(("ready", ""))
        recognize = Transcriber(model, language, offline=True)
        while True:
            path = connection.recv()
            try:
                text = recognize(path)
            except TranscriptionSetupError as exc:
                connection.send(("setup_error", str(exc)))
                return
            except Exception as exc:
                connection.send(("error", str(exc)))
            else:
                connection.send(("done", text))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        connection.send(("setup_error", str(exc)))
    finally:
        connection.close()


class AudioQueue(ttk.Frame):
    def __init__(self, parent, items, model, language, download, on_change):
        super().__init__(parent)
        self.items = dict(enumerate(items))
        self.pending = deque(self.items)
        self.cancelled = set()
        self.active = None
        self.process = None
        self.connection = None
        self.ready = False
        self.download = download
        self.model = model
        self.language = language
        self.on_change = on_change
        self.hint = None
        self.hover = None
        self.hint_timer = None

        self.table = ttk.Treeview(self, columns=("file", "status", "action"), show="headings", height=7)
        for name, title, width in (("file", "Запись", 330), ("status", "Статус", 150), ("action", "", 112)):
            self.table.heading(name, text=title)
            self.table.column(name, width=width, stretch=name == "file")
        self.table.column("action", anchor="center")
        scroll = ttk.Scrollbar(self, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.table.bind("<ButtonRelease-1>", self.click)
        self.table.bind("<Motion>", self.show_hint)
        self.table.bind("<Leave>", self.hide_hint)
        self.table.bind("<space>", self.keyboard_action)
        for key, item in self.items.items():
            self.table.insert("", "end", iid=str(key), values=(f"{item.kind} · {item.path.name}", "В очереди", "×"))
        self.warnings = []

    @property
    def busy(self):
        return self.active is not None or bool(self.pending)

    def set_row(self, key, status, action=""):
        if self.hover == str(key) and action != "×":
            self.hide_hint()
        self.table.set(str(key), "status", status)
        self.table.set(str(key), "action", action)

    def click(self, event):
        if self.table.identify_column(event.x) == "#3":
            row = self.table.identify_row(event.y)
            if row:
                self.toggle(int(row))

    def keyboard_action(self, event):
        row = self.table.focus()
        if row:
            self.toggle(int(row))
        return "break"

    def toggle(self, key):
        self.hide_hint()
        if key in self.cancelled:
            self.cancelled.remove(key)
            self.pending.appendleft(key)
            self.set_row(key, "В очереди", "×")
        elif key == self.active or key in self.pending:
            if key == self.active:
                self.stop_worker()
                self.active = None
            else:
                self.pending.remove(key)
            self.cancelled.add(key)
            self.set_row(key, "Отменено", "Возобновить")
        self.on_change()

    def show_hint(self, event):
        row = self.table.identify_row(event.y)
        if self.table.identify_column(event.x) != "#3" or not row or self.table.set(row, "action") != "×":
            self.hide_hint()
        elif self.hover != row:
            self.hide_hint()
            self.hover = row
            x, y = event.x_root, event.y_root
            self.hint_timer = self.after(400, lambda: self.open_hint(row, x, y))

    def open_hint(self, row, x, y):
        self.hint_timer = None
        if self.table.set(row, "action") != "×":
            return
        self.hint = tk.Toplevel(self)
        self.hint.overrideredirect(True)
        self.hint.geometry(f"+{x + 12}+{y + 16}")
        ttk.Label(self.hint, text="Отменить", padding=5, relief="solid").pack()

    def hide_hint(self, event=None):
        self.hover = None
        if self.hint_timer is not None:
            self.after_cancel(self.hint_timer)
            self.hint_timer = None
        if self.hint is not None:
            self.hint.destroy()
            self.hint = None

    def start_worker(self):
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(
            target=audio_worker, args=(child, self.model, self.language, self.download), daemon=True,
        )
        try:
            self.process.start()
        finally:
            child.close()

    def stop_worker(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.process is not None:
            if self.process.pid is not None:
                if self.process.is_alive():
                    self.process.terminate()
                self.process.join()
            self.process.close()
            self.process = None
        self.ready = False

    def fail(self, text):
        self.stop_worker()
        self.warnings.append(text)
        if self.active is not None:
            self.pending.appendleft(self.active)
            self.active = None
        while self.pending:
            self.set_row(self.pending.popleft(), "Ошибка")

    def poll(self):
        try:
            if self.process is not None and self.connection.poll():
                kind, text = self.connection.recv()
                if kind == "setup_error":
                    self.fail(text)
                    return
                if kind == "ready":
                    self.ready = True
                    self.download = False
                else:
                    item = self.items[self.active]
                    if kind == "done":
                        item.set_text(text)
                        self.set_row(self.active, "Готово")
                    else:
                        self.warnings.append(f"{item.path.name}: {text}")
                        self.set_row(self.active, "Ошибка")
                    self.active = None

            if self.active is None and self.pending:
                if self.process is None:
                    self.start_worker()
                if self.ready:
                    self.active = self.pending.popleft()
                    self.set_row(self.active, "Распознаётся", "×")
                    self.connection.send(self.items[self.active].path)
        except (EOFError, OSError, RuntimeError) as exc:
            self.fail(str(exc) or "Процесс распознавания завершился.")

    def close(self):
        self.hide_hint()
        self.stop_worker()
        self.destroy()
