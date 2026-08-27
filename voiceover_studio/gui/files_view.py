"""Files tab: pick sources, probe them in a worker thread, group by structure.

A batch expects one uniform stream structure; files whose signature differs from
the majority are flagged and excluded at start (never silently adapted)."""
import threading
from pathlib import Path
from tkinter import filedialog

import ttkbootstrap as tb

from ..core import probe

VIDEO_TYPES = [("Video", "*.mkv *.mp4 *.m4v *.avi *.webm"), ("All files", "*.*")]


class FilesView(tb.Frame):
    def __init__(self, master, ui_queue, on_structure):
        """on_structure(info|None): called when the reference structure changes."""
        super().__init__(master, padding=10)
        self.ui_queue = ui_queue
        self.on_structure = on_structure
        self.items = {}   # path(str) -> {"info": MediaInfo|None, "sig": str|None, "ok": bool}

        bar = tb.Frame(self)
        bar.pack(fill="x", pady=(0, 8))
        tb.Button(bar, text="Add videos…", command=self.add_files,
                  bootstyle="primary").pack(side="left")
        tb.Button(bar, text="Remove selected", command=self.remove_selected,
                  bootstyle="secondary-outline").pack(side="left", padx=6)
        tb.Button(bar, text="Clear", command=self.clear,
                  bootstyle="secondary-outline").pack(side="left")
        self.summary = tb.Label(bar, text="No files")
        self.summary.pack(side="right")

        cols = ("dur", "sig", "status")
        self.tree = tb.Treeview(self, columns=cols, show="tree headings", height=14)
        self.tree.heading("#0", text="File")
        self.tree.heading("dur", text="Duration")
        self.tree.heading("sig", text="Structure")
        self.tree.heading("status", text="Status")
        self.tree.column("#0", width=430, anchor="w")
        self.tree.column("dur", width=100, anchor="center")
        self.tree.column("sig", width=280, anchor="w")
        self.tree.column("status", width=130, anchor="center")
        self.tree.tag_configure("warn", foreground="#e8a13c")
        self.tree.tag_configure("bad", foreground="#e05c5c")
        self.tree.pack(fill="both", expand=True)

    # ---- actions ----------------------------------------------------------

    def add_files(self):
        paths = filedialog.askopenfilenames(title="Select videos", filetypes=VIDEO_TYPES)
        fresh = [p for p in paths if p not in self.items]
        for p in fresh:
            self.items[p] = {"info": None, "sig": None, "ok": False}
            self.tree.insert("", "end", iid=p, text=Path(p).name,
                             values=("…", "", "probing…"))
        if fresh:
            threading.Thread(target=self._probe_worker, args=(fresh,), daemon=True).start()
        self._refresh_summary()

    def remove_selected(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.items.pop(iid, None)
        self._regroup()

    def clear(self):
        for iid in list(self.items):
            self.tree.delete(iid)
        self.items.clear()
        self._regroup()

    # ---- probing ----------------------------------------------------------

    def _probe_worker(self, paths):
        for p in paths:
            try:
                info = probe.probe(p)
                self.ui_queue.put(("probed", p, info, None))
            except Exception as e:  # noqa: BLE001
                self.ui_queue.put(("probed", p, None, str(e)))

    def on_probed(self, path, info, err):
        if path not in self.items:
            return
        it = self.items[path]
        if err or info is None:
            it["info"], it["sig"], it["ok"] = None, None, False
            self.tree.item(path, values=("—", err or "?", "error"), tags=("bad",))
        else:
            it["info"], it["sig"] = info, info.signature()
            m, s = divmod(int(info.duration), 60)
            h, m = divmod(m, 60)
            self.tree.item(path, values=(f"{h}:{m:02d}:{s:02d}", it["sig"], ""))
        self._regroup()

    def _regroup(self):
        sigs = [it["sig"] for it in self.items.values() if it["sig"]]
        ref = max(set(sigs), key=sigs.count) if sigs else None
        ref_info = None
        for p, it in self.items.items():
            if it["sig"] is None:
                continue
            it["ok"] = it["sig"] == ref
            if it["ok"] and ref_info is None:
                ref_info = it["info"]
            self.tree.item(p, tags=() if it["ok"] else ("warn",))
            vals = list(self.tree.item(p, "values"))
            vals[2] = "ok" if it["ok"] else "structure ≠"
            self.tree.item(p, values=vals)
        self._refresh_summary()
        self.on_structure(ref_info)

    def _refresh_summary(self):
        n = len(self.items)
        ok = sum(1 for it in self.items.values() if it["ok"])
        pending = sum(1 for it in self.items.values() if it["sig"] is None)
        txt = f"files: {n} · ready: {ok}" + (f" · probing: {pending}" if pending else "")
        self.summary.configure(text=txt if n else "No files")

    # ---- accessors --------------------------------------------------------

    def batch(self):
        """[(path, MediaInfo)] for files matching the reference structure."""
        return [(p, it["info"]) for p, it in self.items.items() if it["ok"]]

    def excluded(self):
        return [p for p, it in self.items.items() if it["info"] and not it["ok"]]

    def mark_status(self, path, text):
        if path in self.items:
            vals = list(self.tree.item(path, "values"))
            vals[2] = text
            self.tree.item(path, values=vals)
