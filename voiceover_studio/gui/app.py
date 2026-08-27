"""Main window: Sources → Parameters → Run + settings dialog."""
import os
import queue
from pathlib import Path
from tkinter import messagebox

import ttkbootstrap as tb

from .. import __version__, config
from ..core.job import JobParams
from ..core.translate import Translator, TranslateError
from .files_view import FilesView
from .params_view import ParamsView
from .queue_view import QueueView
from .runner import BatchRunner
from .settings_dialog import SettingsDialog


def _cache_dir():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VoiceoverStudio" / "cache"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "voiceover-studio"


class App(tb.Window):
    def __init__(self):
        super().__init__(title=f"Voiceover Studio v{__version__}", themename="darkly")
        self.geometry("1000x680")
        self.minsize(860, 560)
        self.cfg = config.load_settings()
        self.ui_queue = queue.Queue()
        self.runner = None
        self._job_paths = []

        top = tb.Frame(self, padding=(10, 8, 10, 0))
        top.pack(fill="x")
        tb.Label(top, text="Voiceover Studio",
                 font="-size 13 -weight bold").pack(side="left")
        tb.Button(top, text="⚙ Settings", command=self.open_settings,
                  bootstyle="secondary-outline").pack(side="right")

        self.nb = tb.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=10)
        self.files_view = FilesView(self.nb, self.ui_queue, self.on_structure)
        self.params_view = ParamsView(self.nb, self.cfg, self.ui_queue,
                                      _cache_dir() / "preview")
        self.queue_view = QueueView(self.nb, self.start_batch, self.cancel_batch)
        self.nb.add(self.files_view, text=" 1 · Sources ")
        self.nb.add(self.params_view, text=" 2 · Parameters ")
        self.nb.add(self.queue_view, text=" 3 · Run ")

        self.after(100, self._poll)
        if os.environ.get("VOS_SMOKE"):
            self.after(1800, self.destroy)

    # ---- events ------------------------------------------------------------

    def on_structure(self, info):
        self.params_view.set_structure(info)

    def open_settings(self):
        SettingsDialog(self, dict(self.cfg), self._settings_saved)

    def _settings_saved(self, cfg):
        self.cfg.update(cfg)

    def _poll(self):
        try:
            while True:
                kind, *rest = self.ui_queue.get_nowait()
                if kind == "probed":
                    self.files_view.on_probed(*rest)
                elif kind == "voices":
                    self.params_view.on_voices(*rest)
                elif kind == "preview_done":
                    self.params_view.preview_btn.configure(state="normal")
        except queue.Empty:
            pass
        if self.runner is not None:
            self._poll_runner()
        self.after(100, self._poll)

    def _poll_runner(self):
        try:
            while True:
                kind, *rest = self.runner.events.get_nowait()
                if kind == "file_start":
                    idx, total, name = rest
                    self.queue_view.file_start(idx, total, name)
                    self.queue_view.log_line(f"— {name}")
                    self.files_view.mark_status(self._job_paths[idx], "⏳")
                elif kind == "progress":
                    self.queue_view.progress(*rest)
                elif kind == "file_done":
                    idx, name, report = rest
                    v = report.get("verify", {})
                    pl = report.get("placement", {})
                    self.queue_view.log_line(
                        f"  ✔ {Path(report['out']).name}: tail {v.get('tail_mean_db')} dB, "
                        f"max shift {pl.get('max_shift_s', '—')} s, "
                        f"untranslated {len(report.get('untranslated', []))}")
                    self.files_view.mark_status(self._job_paths[idx],
                                                "✔" if v.get("ok") else "⚠ verify")
                elif kind == "file_error":
                    idx, name, err = rest
                    self.queue_view.log_line(f"  ✘ error: {err.splitlines()[0]}")
                    self.files_view.mark_status(self._job_paths[idx], "✘ error")
                elif kind == "file_cancelled":
                    idx, name, _ = rest
                    self.queue_view.log_line("  ■ cancelled")
                    self.files_view.mark_status(self._job_paths[idx], "■ cancelled")
                elif kind == "batch_done":
                    done, failed, _ = rest
                    self.queue_view.log_line(f"Done: {done} ok, {failed} failed.")
                    self.queue_view.set_running(False)
                    self.runner = None
                    return
        except queue.Empty:
            pass

    # ---- batch -------------------------------------------------------------

    def start_batch(self):
        if self.runner is not None:
            return
        batch = self.files_view.batch()
        if not batch:
            messagebox.showwarning("Voiceover Studio", "No files to process.")
            return
        excluded = self.files_view.excluded()
        if excluded and not messagebox.askyesno(
                "Voiceover Studio",
                f"{len(excluded)} file(s) with a different structure will be skipped. Continue?"):
            return
        p = self.params_view.params()
        if not p["voice"]:
            messagebox.showwarning("Voiceover Studio", "No voice selected.")
            return
        if p["sub"] < 0 and not p["external_srt"]:
            messagebox.showwarning("Voiceover Studio", "No source subtitles selected.")
            return
        if p["external_srt"] and len(batch) > 1:
            messagebox.showwarning("Voiceover Studio",
                                   "External subtitles work with a single file only.")
            return
        if not p["external_srt"] and not self.cfg.get("api_url"):
            messagebox.showwarning("Voiceover Studio", "Translation API is not configured.")
            self.open_settings()
            return
        # persist chosen params as new defaults
        for k in ("voice", "target_lang", "dub_format", "duck", "duck_ratio",
                  "level_mode", "fixed_gain_db"):
            self.cfg[k] = p[k]
        try:
            config.save_settings(self.cfg)
        except OSError:
            pass
        translator = None
        if not p["external_srt"]:
            try:
                translator = Translator(
                    api_url=self.cfg["api_url"], api_key=self.cfg["api_key"],
                    model=self.cfg["api_model"], style=self.cfg["api_style"],
                    prompt_template=self.cfg["prompt_template"], target_lang=p["target_lang"],
                    batch_size=int(self.cfg["batch_size"]),
                    context_lines=int(self.cfg["context_lines"]))
            except TranslateError as e:
                messagebox.showerror("Voiceover Studio", str(e))
                return
        jobs = []
        self._job_paths = []
        for path, _info in batch:
            jobs.append(JobParams(
                src=Path(path), audio=p["audio"], sub=p["sub"],
                external_srt=Path(p["external_srt"]) if p["external_srt"] else None,
                voice=p["voice"], target_lang=p["target_lang"],
                keep_audio=p["keep_audio"], keep_subs=p["keep_subs"],
                dub_format=p["dub_format"], duck=p["duck"], duck_ratio=p["duck_ratio"],
                level_mode=p["level_mode"], level_k=float(self.cfg["level_k"]),
                fixed_gain_db=p["fixed_gain_db"], max_speed=float(self.cfg["max_speed"])))
            self._job_paths.append(path)
        self.runner = BatchRunner(jobs, translator)
        self.queue_view.set_running(True)
        self.queue_view.log_line(f"Start: {len(jobs)} file(s), voice {p['voice']}, "
                                 f"ducking {'ratio ' + str(p['duck_ratio']) if p['duck'] else 'off'}, "
                                 f"format {p['dub_format']}.")
        self.nb.select(self.queue_view)
        self.runner.start()

    def cancel_batch(self):
        if self.runner is not None:
            self.runner.stop()
            self.queue_view.log_line("Cancelling…")


def main():
    config.load_dotenv()
    App().mainloop()
