"""Final container assembly (copy-mux) + result verification."""
import re

from . import ffbin, probe
from ..config import LANG3, LANG_NAMES

NATIVE = {
    "pl": "Polski", "ru": "Русский", "uk": "Українська", "de": "Deutsch",
    "en": "English", "es": "Español", "fr": "Français", "it": "Italiano",
    "cs": "Čeština", "pt": "Português",
}


def dub_titles(lang):
    name = NATIVE.get(lang, LANG_NAMES.get(lang, lang.upper()))
    return f"{name} (lektor)", name


# mp4/3gp timed text: Matroska can't hold these codecs — stream-copying one kills the
# whole mux at header time ("Subtitle codec 94213 is not supported")
CONVERT_TO_SRT = {"mov_text", "text"}


def mux(src, dub_track, target_srt, out_path, *, keep_audio, keep_subs,
        target_lang, sub_codecs=None, cancel=None):
    """video copy + dub AC3 (default) + kept original audio + target sub (default)
    + kept original subs. Everything stream-copied (mp4 timed text re-encoded to srt);
    -max_interleave_delta 0. sub_codecs: source codec per keep_subs entry."""
    lang3 = LANG3.get(target_lang, target_lang)
    a_title, s_title = dub_titles(target_lang)
    cmd = ["-y", "-v", "error", "-i", str(src), "-i", str(dub_track), "-i", str(target_srt),
           "-map", "0:v:0", "-map", "1:a:0"]
    for i in keep_audio:
        cmd += ["-map", f"0:a:{i}"]
    cmd += ["-map", "2:0"]
    for i in keep_subs:
        cmd += ["-map", f"0:s:{i}"]
    cmd += ["-c", "copy", "-max_interleave_delta", "0"]
    for pos, codec in enumerate(sub_codecs or [], start=1):  # kept subs land at s:1.. (s:0 = dub sub)
        if codec in CONVERT_TO_SRT:
            cmd += [f"-c:s:{pos}", "srt"]
    cmd += ["-metadata:s:a:0", f"language={lang3}", "-metadata:s:a:0", f"title={a_title}",
            "-disposition:a:0", "default"]
    for pos in range(1, 1 + len(keep_audio)):
        cmd += [f"-disposition:a:{pos}", "0"]
    cmd += ["-metadata:s:s:0", f"language={lang3}", "-metadata:s:s:0", f"title={s_title}",
            "-disposition:s:0", "default"]
    for pos in range(1, 1 + len(keep_subs)):
        cmd += [f"-disposition:s:{pos}", "-default"]  # clear default, keep e.g. forced
    cmd.append(str(out_path))
    ffbin.run(cmd, cancel=cancel)


def verify(out_path, expected_duration, voice_end=None, cancel=None):
    """The legacy pipeline once produced a file whose dub track silently died mid-episode
    (metadata showed full duration, packets ended early) — so listen for audio on the dub
    track around the LAST spoken cue (`voice_end`, seconds). A blind end-of-file check
    false-fails movies whose final ~30 s are silent credits."""
    info = probe.probe(out_path, cancel=cancel)
    report = {
        "duration_s": round(info.duration, 1),
        "duration_delta_s": round(info.duration - expected_duration, 1),
        "audio_streams": len(info.audios),
        "sub_streams": len(info.subs),
        "tail_mean_db": None,
        "ok": False,
    }
    end = min(expected_duration, info.duration)
    if voice_end:
        # [-6..+6] s around the cue end: placement may shift speech a few seconds later
        at = max(0.0, min(voice_end, end) - 6.0)
    else:
        at = max(0.0, end - 30.0)
    report["checked_at_s"] = round(at, 1)
    err = ffbin.run(["-hide_banner", "-ss", f"{at:.1f}", "-t", "12", "-i", str(out_path),
                     "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
                    cancel=cancel, capture="stderr")
    m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", err)
    if m:
        report["tail_mean_db"] = float(m.group(1))
    report["ok"] = (
        abs(report["duration_delta_s"]) <= 2.0
        and report["tail_mean_db"] is not None
        and report["tail_mean_db"] > -70.0
    )
    return report
