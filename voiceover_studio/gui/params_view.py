"""Params tab: track pickers (filled from the batch's reference structure),
voice choice with preview, mixing controls."""
import os
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import ttkbootstrap as tb

from ..core import ffbin, tts


def find_ffplay():
    """PATH first, then beside the bundled ffmpeg (Windows dist ships ffplay.exe)."""
    found = shutil.which("ffplay")
    if found:
        return found
    try:
        cand = Path(ffbin.ffmpeg_bin()).parent / ("ffplay.exe" if os.name == "nt" else "ffplay")
        if cand.is_file():
            return str(cand)
    except ffbin.FFmpegError:
        pass
    return None

DUCK_PRESETS = [("Off", None), ("Soft -4 dB", 4.0), ("Medium -6 dB", 6.0),
                ("Strong -9 dB", 9.0), ("Custom", "custom")]
LEGACY_PRESETS = [("Off", None), ("Soft (1.5)", 1.5), ("Medium (2.0)", 2.0),
                  ("Strong (4.0)", 4.0), ("Custom", "custom")]
PREVIEW_TEXT = {
    "pl": "Cześć! Tak będzie brzmiał lektor w tym filmie.",
    "ru": "Привет! Так будет звучать закадровый голос.",
    "en": "Hi! This is how the narrator will sound.",
    "de": "Hallo! So wird der Sprecher klingen.",
}


class ParamsView(tb.Frame):
    def __init__(self, master, cfg, ui_queue, scratch_dir):
        super().__init__(master, padding=10)
        self.cfg = cfg
        self.ui_queue = ui_queue
        self.scratch = Path(scratch_dir)
        self.info = None            # reference MediaInfo
        self._voices = []           # edge-tts voice dicts for current lang

        left = tb.Frame(self)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = tb.Frame(self)
        right.pack(side="left", fill="both", expand=True)

        # --- tracks ---------------------------------------------------------
        lf = tb.Labelframe(left, text="Tracks", padding=10)
        lf.pack(fill="x", pady=(0, 8))
        self.audio_cb = self._row_combo(lf, 0, "Original audio:")
        self.sub_cb = self._row_combo(lf, 1, "Source subtitles:")
        srt_row = tb.Frame(lf)
        srt_row.grid(row=3, column=0, columnspan=2, sticky="we", pady=(6, 0))
        tb.Label(srt_row, text="External subtitles:").pack(side="left")
        self.ext_srt_var = tb.StringVar(value="")
        tb.Entry(srt_row, textvariable=self.ext_srt_var, width=28).pack(
            side="left", fill="x", expand=True, padx=4)
        tb.Button(srt_row, text="…", width=3, command=self._pick_srt,
                  bootstyle="secondary-outline").pack(side="left")

        # --- keep lists -----------------------------------------------------
        kf = tb.Labelframe(left, text="Keep in output", padding=10)
        kf.pack(fill="both", expand=True)
        tb.Label(kf, text="Audio:").grid(row=0, column=0, sticky="w")
        tb.Label(kf, text="Subtitles:").grid(row=0, column=1, sticky="w")
        self.keep_audio = tk.Listbox(kf, selectmode="multiple", exportselection=False, height=6)
        self.keep_audio.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.keep_subs = tk.Listbox(kf, selectmode="multiple", exportselection=False, height=6)
        self.keep_subs.grid(row=1, column=1, sticky="nsew")
        kf.columnconfigure(0, weight=1)
        kf.columnconfigure(1, weight=1)
        kf.rowconfigure(1, weight=1)

        # --- voice ----------------------------------------------------------
        vf = tb.Labelframe(right, text="Voice", padding=10)
        vf.pack(fill="x", pady=(0, 8))
        self.lang_cb = self._row_combo(vf, 0, "Language:", width=12)
        self.voice_cb = self._row_combo(vf, 1, "Voice:", width=30)
        self.preview_btn = tb.Button(vf, text="▶ Preview", command=self._preview,
                                     bootstyle="info-outline")
        self.preview_btn.grid(row=2, column=1, sticky="w", pady=(6, 0))
        self._ffplay = find_ffplay()
        if not self._ffplay:
            self.preview_btn.configure(state="disabled", text="▶ (no ffplay)")
        self.lang_cb.configure(values=[self.cfg["target_lang"]])
        self.lang_cb.set(self.cfg["target_lang"])
        self.lang_cb.bind("<<ComboboxSelected>>", lambda e: self._load_voices())
        self.voice_cb.set(self.cfg["voice"])
        threading.Thread(target=self._voices_worker, args=(True,), daemon=True).start()

        # --- mix ------------------------------------------------------------
        mf = tb.Labelframe(right, text="Mixing", padding=10)
        mf.pack(fill="both", expand=True)
        self.mix_nb = tb.Notebook(mf)
        self.mix_nb.pack(fill="x")

        ta = tb.Frame(self.mix_nb, padding=10)      # Auto: gap plan
        ra = tb.Frame(ta)
        ra.pack(fill="x")
        tb.Label(ra, text="Voice over scene:").pack(side="left")
        self.gap_spin = tb.Spinbox(ra, from_=0.0, to=60.0, increment=0.5, width=6)
        self.gap_spin.set(str(self.cfg["gap_db"]))
        self.gap_spin.pack(side="left", padx=4)
        tb.Label(ra, text="dB").pack(side="left")
        tb.Label(ta, text="Voice and ducking follow the scene").pack(anchor="w", pady=(6, 0))

        tm = tb.Frame(self.mix_nb, padding=10)      # Manual: fixed gain + const duck
        rm0 = tb.Frame(tm)
        rm0.pack(fill="x")
        tb.Label(rm0, text="Voice gain:").pack(side="left")
        self.gain_spin = tb.Spinbox(rm0, from_=-12.0, to=12.0, increment=0.5, width=6)
        self.gain_spin.set(str(self.cfg["fixed_gain_db"]))
        self.gain_spin.pack(side="left", padx=4)
        tb.Label(rm0, text="dB").pack(side="left")
        rm1 = tb.Frame(tm)
        rm1.pack(fill="x", pady=(6, 0))
        tb.Label(rm1, text="Ducking:").pack(side="left")
        self.duck_cb = tb.Combobox(rm1, values=[n for n, _ in DUCK_PRESETS],
                                   state="readonly", width=14)
        self.duck_cb.pack(side="left", padx=4)
        self.duck_cb.bind("<<ComboboxSelected>>", lambda e: self._duck_changed())
        self.duck_spin = tb.Spinbox(rm1, from_=1.0, to=60.0, increment=0.5, width=6)
        self.duck_spin.pack(side="left")

        tl = tb.Frame(self.mix_nb, padding=10)      # Legacy: 0.1.0 algorithm
        rl0 = tb.Frame(tl)
        rl0.pack(fill="x")
        tb.Label(rl0, text="Ducking:").pack(side="left")
        self.legacy_cb = tb.Combobox(rl0, values=[n for n, _ in LEGACY_PRESETS],
                                     state="readonly", width=14)
        self.legacy_cb.pack(side="left", padx=4)
        self.legacy_cb.bind("<<ComboboxSelected>>", lambda e: self._legacy_changed())
        self.legacy_spin = tb.Spinbox(rl0, from_=1.1, to=20.0, increment=0.1, width=6)
        self.legacy_spin.pack(side="left")
        tb.Label(tl, text="Scene-tracked voice, compressor duck").pack(
            anchor="w", pady=(6, 0))

        self.mix_nb.add(ta, text="Auto")
        self.mix_nb.add(tm, text="Manual")
        self.mix_nb.add(tl, text="Legacy")
        self.mix_nb.select({"gap": 0, "fixed": 1, "off": 1, "legacy": 2}.get(
            self.cfg["level_mode"], 0))
        self._set_duck_from_cfg()

        rf = tb.Frame(mf)
        rf.pack(fill="x", pady=(8, 0))
        tb.Label(rf, text="Output audio:").pack(side="left")
        self.fmt_var = tb.StringVar(value=self.cfg["dub_format"])
        tb.Radiobutton(rf, text="Original", value="original",
                       variable=self.fmt_var).pack(side="left", padx=(4, 8))
        tb.Radiobutton(rf, text="Stereo downmix", value="stereo",
                       variable=self.fmt_var).pack(side="left")

    # ---- helpers -----------------------------------------------------------

    def _row_combo(self, parent, row, label, width=34):
        tb.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        cb = tb.Combobox(parent, state="readonly", width=width)
        cb.grid(row=row, column=1, sticky="we", padx=4, pady=2)
        parent.columnconfigure(1, weight=1)
        return cb

    def _pick_srt(self):
        p = filedialog.askopenfilename(title="External subtitles",
                                       filetypes=[("SRT", "*.srt"), ("All files", "*.*")])
        if p:
            self.ext_srt_var.set(p)

    def _set_duck_from_cfg(self):
        duck_on = bool(self.cfg["duck"])
        d = float(self.cfg["duck_db"])
        named = {4.0: "Soft -4 dB", 6.0: "Medium -6 dB", 9.0: "Strong -9 dB"}
        self.duck_cb.set("Off" if not duck_on else named.get(d, "Custom"))
        self.duck_spin.set(str(d))
        self._duck_changed()
        r = float(self.cfg["legacy_ratio"])
        lnamed = {1.5: "Soft (1.5)", 2.0: "Medium (2.0)", 4.0: "Strong (4.0)"}
        self.legacy_cb.set("Off" if not duck_on else lnamed.get(r, "Custom"))
        self.legacy_spin.set(str(r))
        self._legacy_changed()

    def _duck_changed(self):
        self._preset_sync(self.duck_cb, self.duck_spin, DUCK_PRESETS)

    def _legacy_changed(self):
        self._preset_sync(self.legacy_cb, self.legacy_spin, LEGACY_PRESETS)

    @staticmethod
    def _preset_sync(cb, spin, presets):
        preset = dict(presets).get(cb.get())
        spin.configure(state="normal" if preset == "custom" else "disabled")
        if isinstance(preset, float):
            spin.configure(state="normal")
            spin.set(str(preset))
            spin.configure(state="disabled")

    # ---- structure ---------------------------------------------------------

    def set_structure(self, info):
        """Fill track pickers from the batch's reference MediaInfo (or clear)."""
        self.info = info
        for lb in (self.keep_audio, self.keep_subs):
            lb.delete(0, "end")
        if info is None:
            self.audio_cb.configure(values=[])
            self.audio_cb.set("")
            self.sub_cb.configure(values=[])
            self.sub_cb.set("")
            return
        a_labels = [s.label() for s in info.audios]
        s_labels = [s.label() for s in info.subs]
        self.audio_cb.configure(values=a_labels)
        self.sub_cb.configure(values=s_labels + ["— (external subtitles)"])
        # heuristics: original audio = title says Original, else last; SDH sub auto-check
        orig = next((i for i, s in enumerate(info.audios) if "original" in s.title.lower()), None)
        self.audio_cb.current(orig if orig is not None else len(a_labels) - 1)
        if s_labels:
            base_lang = info.audios[self.audio_cb.current()].lang
            cand = [i for i, s in enumerate(info.subs) if s.lang == base_lang]
            pick = next((i for i in cand if "sdh" not in s_labels[i].lower()), cand[0] if cand else 0)
            self.sub_cb.current(pick)
        for i, lbl in enumerate(a_labels):
            self.keep_audio.insert("end", lbl)
            self.keep_audio.selection_set(i)
        for i, lbl in enumerate(s_labels):
            self.keep_subs.insert("end", lbl)
            self.keep_subs.selection_set(i)

    # ---- voices ------------------------------------------------------------

    def _voices_worker(self, init=False):
        try:
            all_voices = tts.list_voices()
        except Exception as e:  # noqa: BLE001
            self.ui_queue.put(("voices", None, f"failed to fetch voices: {e}"))
            return
        langs = sorted({v["ShortName"].split("-")[0] for v in all_voices})
        self.ui_queue.put(("voices", (langs, all_voices), None))

    def _load_voices(self):
        lang = self.lang_cb.get()
        vs = [v for v in self._all_voices if v["ShortName"].lower().startswith(lang.lower())]
        self._voices = vs
        names = [f'{v["ShortName"]}  ({v["Gender"]})' for v in vs]
        self.voice_cb.configure(values=names)
        cur = self.cfg["voice"]
        match = next((n for n in names if n.startswith(cur)), names[0] if names else "")
        self.voice_cb.set(match)

    def on_voices(self, payload, err):
        if err:
            self.voice_cb.configure(values=[self.cfg["voice"]])
            return
        langs, all_voices = payload
        self._all_voices = all_voices
        self.lang_cb.configure(values=langs)
        if self.lang_cb.get() not in langs:
            self.lang_cb.set(self.cfg["target_lang"])
        self._load_voices()

    def _preview(self):
        voice = self.current_voice()
        if not voice:
            return
        self.preview_btn.configure(state="disabled")
        threading.Thread(target=self._preview_worker, args=(voice,), daemon=True).start()

    def _preview_worker(self, voice):
        try:
            text = PREVIEW_TEXT.get(self.lang_cb.get(), PREVIEW_TEXT["en"])
            self.scratch.mkdir(parents=True, exist_ok=True)
            mp3 = tts.ensure_clip(self.scratch, text, voice)
            subprocess.run([self._ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(mp3)],
                           check=False, timeout=30, creationflags=ffbin.CREATIONFLAGS)
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
        finally:
            self.ui_queue.put(("preview_done", None, None))

    # ---- output ------------------------------------------------------------

    def current_voice(self):
        return (self.voice_cb.get().split()[0] or "").strip()

    def params(self):
        """Collected UI state -> dict for JobParams / settings persistence."""
        mode = ("gap", "fixed", "legacy")[self.mix_nb.index(self.mix_nb.select())]
        dpre = dict(DUCK_PRESETS).get(self.duck_cb.get())
        duck_db = float(self.duck_spin.get()) if dpre == "custom" else (
            dpre if isinstance(dpre, float) else float(self.cfg["duck_db"]))
        lpre = dict(LEGACY_PRESETS).get(self.legacy_cb.get())
        legacy_ratio = float(self.legacy_spin.get()) if lpre == "custom" else (
            lpre if isinstance(lpre, float) else float(self.cfg["legacy_ratio"]))
        if mode == "gap":
            duck = True
        elif mode == "fixed":
            duck = dpre is not None
        else:
            duck = lpre is not None
        ext = self.ext_srt_var.get().strip()
        sub = self.sub_cb.current() if not ext and self.sub_cb.current() < len(self.info.subs) else -1
        return {
            "audio": self.audio_cb.current(),
            "sub": sub,
            "sub_lang": self.info.subs[sub].lang if sub >= 0 else "",
            "external_srt": ext or None,
            "voice": self.current_voice(),
            "target_lang": self.lang_cb.get(),
            "keep_audio": list(self.keep_audio.curselection()),
            "keep_subs": list(self.keep_subs.curselection()),
            "dub_format": self.fmt_var.get(),
            "duck": duck,
            "duck_db": duck_db,
            "legacy_ratio": legacy_ratio,
            "level_mode": mode,
            "gap_db": float(self.gap_spin.get()),
            "fixed_gain_db": float(self.gain_spin.get()),
        }
