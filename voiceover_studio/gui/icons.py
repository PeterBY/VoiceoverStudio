"""Window icon for Tk toplevels.

Windows: Tk's own .ico parsing (iconbitmap/iconphoto) garbles the title-bar
icon, so load through the OS (LoadImage) and set via WM_SETICON — the same
path Explorer and the taskbar use. Elsewhere: iconphoto with the PNG.
"""
import os
from tkinter import PhotoImage

from ..core import ffbin

WM_SETICON, IMAGE_ICON, LR_LOADFROMFILE = 0x80, 1, 0x10
SM_CXICON, SM_CXSMICON = 11, 49


def apply(widget):
    """Best-effort: never raises, never blocks startup."""
    try:
        if os.name == "nt":
            widget.after(50, lambda: _apply_win(widget))
        else:
            img = PhotoImage(file=str(ffbin.app_dir() / "assets" / "app.png"))
            widget.iconphoto(True, img)
            widget._icon_ref = img
    except Exception:  # noqa: BLE001
        pass


def _apply_win(widget, tries=20):
    import ctypes
    try:
        u = ctypes.windll.user32
        hwnd = u.GetParent(widget.winfo_id())
        if not hwnd:  # wrapper window appears once the toplevel is mapped
            if tries > 0:
                widget.after(100, lambda: _apply_win(widget, tries - 1))
            return
        ico = str(ffbin.app_dir() / "assets" / "app.ico")
        for kind, metric, fallback in ((0, SM_CXSMICON, 16), (1, SM_CXICON, 32)):
            size = u.GetSystemMetrics(metric) or fallback
            h = u.LoadImageW(None, ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if h:
                u.SendMessageW(hwnd, WM_SETICON, kind, h)
    except Exception:  # noqa: BLE001
        pass
