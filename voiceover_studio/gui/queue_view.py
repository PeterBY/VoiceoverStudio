"""Run tab: batch progress, live log, start/cancel."""
from tkinter.scrolledtext import ScrolledText

import ttkbootstrap as tb

STAGE_EN = {
    "probe": "probe", "extract": "subtitles", "translate": "translate",
    "tts": "synthesize", "mix": "mix", "mux": "mux", "verify": "verify",
    "done": "done",
}


class QueueView(tb.Frame):
    def __init__(self, master, on_start, on_cancel):
        super().__init__(master, padding=10)
        bar = tb.Frame(self)
        bar.pack(fill="x", pady=(0, 8))
        self.start_btn = tb.Button(bar, text="▶ Start", command=on_start, bootstyle="success")
        self.start_btn.pack(side="left")
        self.cancel_btn = tb.Button(bar, text="■ Cancel", command=on_cancel,
                                    bootstyle="danger-outline", state="disabled")
        self.cancel_btn.pack(side="left", padx=6)
        self.batch_lbl = tb.Label(bar, text="")
        self.batch_lbl.pack(side="right")

        self.file_lbl = tb.Label(self, text="—")
        self.file_lbl.pack(fill="x")
        self.bar = tb.Progressbar(self, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=6)
        self.stage_lbl = tb.Label(self, text="")
        self.stage_lbl.pack(fill="x")

        self.log = ScrolledText(self, height=14, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(8, 0))

    def set_running(self, running):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")

    def file_start(self, idx, total, name):
        self.batch_lbl.configure(text=f"file {idx + 1} of {total}")
        self.file_lbl.configure(text=name)
        self.bar.configure(value=0)
        self.stage_lbl.configure(text="")

    def progress(self, stage, done, total, msg):
        name = STAGE_EN.get(stage, stage)
        if total > 1:
            self.bar.configure(value=100.0 * done / total)
            self.stage_lbl.configure(text=f"{name}: {msg}")
        else:
            self.stage_lbl.configure(text=f"{name}: {msg}" if msg else name)
        if stage == "done":
            self.bar.configure(value=100)

    def log_line(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
