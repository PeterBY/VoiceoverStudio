"""Stream inventory of a media file (ffprobe) + structure signature for batch grouping."""
import json
from dataclasses import dataclass, field

from . import ffbin


@dataclass
class Stream:
    index: int          # global stream index
    type: str           # video | audio | subtitle
    type_index: int     # per-type order (the N in 0:a:N / 0:s:N)
    codec: str = ""
    channels: int = 0
    layout: str = ""
    lang: str = "und"
    title: str = ""
    default: bool = False
    forced: bool = False

    def label(self):
        bits = [f"{self.type[0]}:{self.type_index}", self.codec, self.lang]
        if self.type == "audio":
            bits.append(self.layout or f"{self.channels}ch")
        if self.title:
            bits.append(self.title)
        if self.default:
            bits.append("[default]")
        if self.forced:
            bits.append("[forced]")
        return " ".join(bits)


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    streams: list = field(default_factory=list)

    @property
    def audios(self):
        return [s for s in self.streams if s.type == "audio"]

    @property
    def subs(self):
        return [s for s in self.streams if s.type == "subtitle"]

    def signature(self):
        """Structure fingerprint: files with equal signatures can share one batch config."""
        a = ",".join(f"{s.lang}/{s.channels}" for s in self.audios)
        s = ",".join(f"{x.lang}" for x in self.subs)
        return f"a[{a}]|s[{s}]"


def probe(path, cancel=None):
    raw = ffbin.run(
        ["-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        tool="ffprobe", cancel=cancel, capture=True, text=True,
    )
    data = json.loads(raw)
    info = MediaInfo(path=str(path), duration=float(data.get("format", {}).get("duration", 0) or 0))
    counters = {}
    for st in data.get("streams", []):
        typ = st.get("codec_type", "")
        if typ not in ("video", "audio", "subtitle"):
            continue
        ti = counters.get(typ, 0)
        counters[typ] = ti + 1
        tags = st.get("tags", {}) or {}
        disp = st.get("disposition", {}) or {}
        info.streams.append(Stream(
            index=int(st["index"]),
            type=typ,
            type_index=ti,
            codec=st.get("codec_name", ""),
            channels=int(st.get("channels", 0) or 0),
            layout=st.get("channel_layout", "") or "",
            lang=(tags.get("language") or "und").lower(),
            title=tags.get("title", "") or "",
            default=bool(disp.get("default", 0)),
            forced=bool(disp.get("forced", 0)),
        ))
    return info
