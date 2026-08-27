"""ffmpeg/ffprobe resolution + cancellable subprocess runner.

Resolution order: LOCALIZATOR_FFMPEG/FFPROBE env -> <app dir>/third_party/ffmpeg/ -> PATH.
"""
import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path


class CancelledError(RuntimeError):
    pass


# Windows: without this every spawned ffmpeg/ffprobe flashes a console window
# (one per synthesized line) when the parent is a windowed (no-console) app.
CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class FFmpegError(RuntimeError):
    pass


def app_dir():
    if getattr(sys, "frozen", False):  # PyInstaller onedir: resources live in _internal/
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=None)
def _resolve(name):
    env = os.environ.get(f"VOS_{name.upper()}")
    if env and Path(env).is_file():
        return env
    exe = name + (".exe" if os.name == "nt" else "")
    bundled = app_dir() / "third_party" / "ffmpeg" / exe
    if bundled.is_file():
        return str(bundled)
    found = shutil.which(name)
    if not found:
        raise FFmpegError(f"{name} not found: bundle it in third_party/ffmpeg/ or install it in PATH")
    return found


def ffmpeg_bin():
    return _resolve("ffmpeg")


def ffprobe_bin():
    return _resolve("ffprobe")


def run(args, *, tool="ffmpeg", cancel=None, capture=False, text=False):
    """Run ffmpeg/ffprobe; poll `cancel` (threading.Event) and kill on request.

    capture=True -> returns stdout (bytes, or str when text=True);
    capture="stderr" -> returns stderr as str (for filters that report there,
    e.g. volumedetect); otherwise returns ''.
    """
    binp = ffmpeg_bin() if tool == "ffmpeg" else ffprobe_bin()
    proc = subprocess.Popen(
        [binp, *args],
        stdout=subprocess.PIPE if capture is True else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=CREATIONFLAGS,
    )
    while True:
        try:
            out, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.communicate()
                raise CancelledError("cancelled")
    if proc.returncode != 0:
        tail = err.decode("utf-8", "replace").strip().splitlines()[-8:]
        raise FFmpegError(f"{Path(binp).name} failed ({proc.returncode}):\n" + "\n".join(tail))
    if capture == "stderr":
        return err.decode("utf-8", "replace")
    if not capture:
        return ""
    return out.decode("utf-8", "replace") if text else out
