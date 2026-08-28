"""Settings dialog: AI API host/key/model/protocol + translation prompt, with a
connection test. Saved to the per-user settings.json. (.env overrides
at runtime — dev/testing only.)"""
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import httpx
import ttkbootstrap as tb

from .. import config
from . import icons


class SettingsDialog(tb.Toplevel):
    def __init__(self, master, cfg, on_saved):
        super().__init__(master)
        icons.apply(self)
        self.title("Settings")
        self.geometry("620x560")
        self.cfg = cfg
        self.on_saved = on_saved
        pad = {"padx": 8, "pady": 4}

        frm = tb.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self.vars = {}
        for row, (key, label) in enumerate([
                ("api_url", "AI API Host:"),
                ("api_key", "API key:"),
                ("api_model", "AI Model:")]):
            tb.Label(frm, text=label).grid(row=row, column=0, sticky="w", **pad)
            v = tb.StringVar(value=str(cfg.get(key, "")))
            self.vars[key] = v
            e = tb.Entry(frm, textvariable=v, width=48,
                         show="•" if key == "api_key" else "")
            e.grid(row=row, column=1, sticky="we", **pad)
        tb.Button(frm, text="Models ↓", command=self._fetch_models,
                  bootstyle="secondary-outline").grid(row=2, column=2, **pad)

        tb.Label(frm, text="API Protocol:").grid(row=3, column=0, sticky="w", **pad)
        self.style_cb = tb.Combobox(frm, values=["auto", "responses", "chat"],
                                    state="readonly", width=12)
        self.style_cb.set(cfg.get("api_style", "auto"))
        self.style_cb.grid(row=3, column=1, sticky="w", **pad)

        tb.Label(frm, text="Work folder:").grid(row=4, column=0, sticky="w", **pad)
        self.vars["work_dir"] = tb.StringVar(value=str(cfg.get("work_dir", "")))
        tb.Entry(frm, textvariable=self.vars["work_dir"], width=48).grid(
            row=4, column=1, sticky="we", **pad)
        tb.Button(frm, text="…", width=3, command=self._pick_work_dir,
                  bootstyle="secondary-outline").grid(row=4, column=2, **pad)

        self.cleanup_var = tb.BooleanVar(value=bool(cfg.get("cleanup_work", True)))
        tb.Checkbutton(frm, text="Delete work dir on success",
                       variable=self.cleanup_var).grid(row=5, column=1, sticky="w", **pad)

        tb.Label(frm, text="Translation prompt:").grid(
            row=6, column=0, columnspan=2, sticky="w", **pad)
        self.prompt = tk.Text(frm, height=12, wrap="word")
        self.prompt.insert("1.0", cfg.get("prompt_template", config.DEFAULT_PROMPT))
        self.prompt.grid(row=7, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(7, weight=1)
        frm.columnconfigure(1, weight=1)

        btns = tb.Frame(frm)
        btns.grid(row=9, column=0, columnspan=3, sticky="we", **pad)
        tb.Button(btns, text="Test connection", command=self._test,
                  bootstyle="info-outline").pack(side="left")
        self.status = tb.Label(btns, text="")
        self.status.pack(side="left", padx=8)
        tb.Button(btns, text="Save", command=self._save,
                  bootstyle="success").pack(side="right")
        tb.Button(btns, text="Cancel", command=self.destroy,
                  bootstyle="secondary").pack(side="right", padx=6)

    def _pick_work_dir(self):
        d = filedialog.askdirectory(title="Work folder", parent=self)
        if d:
            self.vars["work_dir"].set(d)

    def _client(self):
        key = self.vars["api_key"].get().strip()
        return httpx.Client(timeout=15,
                            headers={"Authorization": f"Bearer {key}"} if key else {})

    def _base(self):
        b = self.vars["api_url"].get().strip().rstrip("/")
        return b[:-3] if b.endswith("/v1") else b

    def _fetch_models(self):
        def work():
            try:
                r = self._client().get(f"{self._base()}/v1/models")
                r.raise_for_status()
                ids = [m["id"] for m in r.json().get("data", [])]
                self.after(0, lambda: self._models_done(ids))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: self.status.configure(text=f"error: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def _models_done(self, ids):
        self.status.configure(text=f"models: {len(ids)}")
        if ids and not self.vars["api_model"].get().strip():
            self.vars["api_model"].set(ids[0])
        if ids:
            top = tb.Toplevel(self)
            icons.apply(top)
            top.title("Select model")
            lb = tk.Listbox(top, height=min(16, len(ids)), width=40)
            for i in ids:
                lb.insert("end", i)
            lb.pack(fill="both", expand=True, padx=8, pady=8)

            def pick(_e=None):
                sel = lb.curselection()
                if sel:
                    self.vars["api_model"].set(ids[sel[0]])
                top.destroy()
            lb.bind("<Double-Button-1>", pick)
            tb.Button(top, text="Select", command=pick).pack(pady=(0, 8))

    def _test(self):
        self.status.configure(text="testing…")

        def work():
            try:
                r = self._client().post(f"{self._base()}/v1/chat/completions", json={
                    "model": self.vars["api_model"].get().strip(),
                    "messages": [{"role": "user", "content": "Reply with: ok"}],
                    "temperature": 0,
                })
                r.raise_for_status()
                txt = r.json()["choices"][0]["message"]["content"][:40]
                self.after(0, lambda: self.status.configure(text=f"ok: {txt}"))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: self.status.configure(text=f"error: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def _save(self):
        self.cfg["api_url"] = self.vars["api_url"].get().strip()
        self.cfg["api_key"] = self.vars["api_key"].get().strip()
        self.cfg["api_model"] = self.vars["api_model"].get().strip()
        self.cfg["api_style"] = self.style_cb.get()
        self.cfg["work_dir"] = self.vars["work_dir"].get().strip()
        self.cfg["cleanup_work"] = bool(self.cleanup_var.get())
        self.cfg["prompt_template"] = self.prompt.get("1.0", "end").strip() + "\n"
        try:
            config.save_settings(self.cfg)
        except OSError as e:
            messagebox.showerror("Voiceover Studio", f"Failed to save settings: {e}")
            return
        self.on_saved(self.cfg)
        self.destroy()
