"""edge-tts synthesis with an md5 clip cache (same key scheme as the legacy pipeline,
so existing caches keep working)."""
import asyncio
import hashlib
import time
from pathlib import Path

import edge_tts


class TTSError(RuntimeError):
    pass


def clip_path(cache_dir, text, voice, pitch="+0Hz", rate="+0%"):
    key = hashlib.md5(f"{voice}|{pitch}|{rate}|{text}".encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"{key}.mp3"


def ensure_clip(cache_dir, text, voice, pitch="+0Hz", rate="+0%", retries=3):
    """Synthesize (or reuse cached) mp3 for one line; returns the clip path."""
    out = clip_path(cache_dir, text, voice, pitch, rate)
    if out.is_file() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(retries):
        try:
            asyncio.run(edge_tts.Communicate(text, voice, pitch=pitch, rate=rate).save(str(out)))
            if out.is_file() and out.stat().st_size > 0:
                return out
            last = TTSError("empty clip")
        except Exception as e:  # noqa: BLE001 - edge-tts raises assorted network errors
            last = e
        time.sleep(2 * (attempt + 1))
    raise TTSError(f"edge-tts failed for line ({voice}): {last}")


def list_voices(lang_prefix=None):
    """[{'ShortName': 'pl-PL-ZofiaNeural', 'Gender': 'Female', ...}], optionally filtered
    by language code prefix ('pl' / 'pl-PL')."""
    voices = asyncio.run(edge_tts.list_voices())
    if lang_prefix:
        p = lang_prefix.lower()
        voices = [v for v in voices if v["ShortName"].lower().startswith(p)]
    return sorted(voices, key=lambda v: v["ShortName"])
