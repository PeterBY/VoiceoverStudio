"""SRT parsing/writing + markup and SDH cleanup.

Cue numbering and timecodes are the pipeline's backbone: translations map by cue
number, placement anchors at cue start. Numbers are preserved end-to-end.
"""
import re
from dataclasses import dataclass
from pathlib import Path

TIME_RE = re.compile(r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})")


@dataclass
class Cue:
    num: int
    start: float
    end: float
    text: str  # may be multiline; may be "" (skipped by TTS, kept for numbering)


def ts_to_sec(ts):
    h, m, rest = ts.strip().replace(".", ",").split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def sec_to_ts(t):
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path_or_text):
    text = Path(path_or_text).read_text(encoding="utf-8-sig") if isinstance(path_or_text, (str, Path)) and "\n" not in str(path_or_text) and Path(path_or_text).is_file() else str(path_or_text)
    cues = []
    for block in re.split(r"\r?\n\r?\n", text.strip()):
        lines = block.splitlines()
        ti = next((i for i, l in enumerate(lines) if TIME_RE.search(l)), None)
        if ti is None:
            continue
        m = TIME_RE.search(lines[ti])
        num_line = lines[ti - 1].strip() if ti > 0 else ""
        num = int(num_line) if num_line.isdigit() else len(cues) + 1
        cues.append(Cue(
            num=num,
            start=ts_to_sec(m.group(1)),
            end=ts_to_sec(m.group(2)),
            text="\n".join(lines[ti + 1:]).strip(),
        ))
    return cues


def format_srt(cues):
    out = []
    for c in cues:
        out.append(f"{c.num}\n{sec_to_ts(c.start)} --> {sec_to_ts(c.end)}\n{c.text}")
    return "\n\n".join(out) + "\n"


def strip_markup(text):
    """Remove <i>-style tags and {\\an8}-style overrides."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{[^}]+\}", "", text)
    return text


# SDH noise: [door slams], (sighs), ♪ lyrics ♪ — including blocks spanning line breaks.
_SDH_BRACKETS = re.compile(r"[\[(][^\])]*[\])]", re.S)
_SDH_MUSIC = re.compile(r"♪+[^♪]*♪+|^\s*♪+\s*$", re.M)
# "BJÖRN:" / "Mann 2:" speaker labels — ≤3 words before a colon, each capitalized/uppercase/digit.
_SDH_SPEAKER = re.compile(
    r"^\s*[-–—]?\s*((?:[A-ZÀ-ÞŒ0-9][\w'.-]*\s?){1,3}):\s*", re.M | re.UNICODE
)


def sdh_strip(text):
    text = _SDH_BRACKETS.sub("", text)
    text = _SDH_MUSIC.sub("", text)
    text = _SDH_SPEAKER.sub("", text)
    return text


def normalize_ws(text, keep_newlines=False):
    if keep_newlines:
        lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
        return "\n".join(l for l in lines if l)
    return re.sub(r"\s+", " ", text).strip()


def clean_cues(cues):
    """Markup + SDH cleanup (always on); keeps every cue (possibly with empty text)."""
    out = []
    for c in cues:
        t = sdh_strip(strip_markup(c.text))
        out.append(Cue(c.num, c.start, c.end, normalize_ws(t, keep_newlines=True)))
    return out


def tts_text(text):
    """Final flattening right before synthesis: one line, no dialogue dashes."""
    t = strip_markup(text)
    t = re.sub(r"(^|\s)[-–—]\s*", r"\1", t)
    return normalize_ws(t)


def build_target(source_cues, translations):
    """Target-language cues from source timecodes + {num: text}. Missing -> source text kept
    (nothing goes silent unnoticed); returns (cues, missing_nums). Empty source cues stay empty."""
    out, missing = [], []
    for c in source_cues:
        tr = translations.get(c.num, translations.get(str(c.num)))
        if c.text and (tr is None or not str(tr).strip()):
            if tr is None:
                missing.append(c.num)
                tr = c.text  # fallback: source text, audible & visible rather than a hole
            else:
                tr = ""  # model explicitly said noise
        out.append(Cue(c.num, c.start, c.end, str(tr).strip() if c.text else ""))
    return out, missing
