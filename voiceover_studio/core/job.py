"""One-file dubbing job: stages with checkpoints, progress events, cancellation.

Work dir `<src>.work/` beside the source holds every intermediate; a stage whose
output is newer than its inputs is skipped, so an interrupted job resumes cheaply
(edge-tts clips and translations survive re-runs).
"""
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import audio, ffbin, mix, mux, probe, srt
from .translate import TranslateError
from ..config import same_lang


@dataclass
class JobParams:
    src: Path
    audio: int                      # 0:a:N of the base (original) track
    sub: int = -1                   # 0:s:N of the source subtitle (-1 = use external_srt)
    external_srt: Path = None       # ALREADY TRANSLATED subtitles: skips extract+translate
    voice: str = "pl-PL-ZofiaNeural"
    target_lang: str = "pl"
    keep_audio: list = field(default_factory=list)   # 0:a:N indexes copied into the output
    keep_subs: list = field(default_factory=list)    # 0:s:N indexes copied into the output
    dub_format: str = "stereo"      # stereo | original (source layout)
    duck: bool = True
    duck_ratio: float = 2.0
    level_mode: str = "track"       # track | fixed | off
    level_k: float = 0.6
    fixed_gain_db: float = 0.0
    max_speed: float = 1.0          # locked default: no speed-up
    force: bool = False

    def workdir(self):
        return self.src.parent / (self.src.stem + ".work")

    def out_path(self):
        return self.src.parent / f"{self.src.stem}.{self.target_lang.upper()}.mkv"


def _fresh(out: Path, *deps):
    """True if `out` exists and is newer than all its dependency files."""
    if not out.exists() or out.stat().st_size == 0:
        return False
    ts = out.stat().st_mtime
    return all(not d or not Path(d).exists() or Path(d).stat().st_mtime <= ts for d in deps)


def _params_ok(out: Path, params: dict):
    """True if `out` was built with exactly these params (stored in a stamp beside it).
    Without this, tweaking e.g. duck strength would silently reuse the stale track."""
    stamp = out.with_suffix(out.suffix + ".params")
    try:
        return json.loads(stamp.read_text(encoding="utf-8")) == params
    except (OSError, ValueError):
        return False


def _write_params(out: Path, params: dict):
    out.with_suffix(out.suffix + ".params").write_text(
        json.dumps(params, sort_keys=True), encoding="utf-8")


def run_job(p: JobParams, translator=None, progress=None, cancel=None):
    """progress(stage:str, done:int, total:int, msg:str). Returns the job report dict."""
    def emit(stage, done=0, total=0, msg=""):
        if progress:
            progress(stage, done, total, msg)

    p.src = Path(p.src)
    wd = p.workdir()
    wd.mkdir(exist_ok=True)
    report = {"src": str(p.src), "out": str(p.out_path())}

    emit("probe", msg=p.src.name)
    info = probe.probe(p.src, cancel=cancel)
    if not info.audios or p.audio >= len(info.audios):
        raise ValueError(f"audio track a:{p.audio} not found in {p.src.name}")
    base = info.audios[p.audio]
    report["base_audio"] = base.label()
    report["duration_s"] = round(info.duration, 1)

    target_srt = wd / "target.srt"
    if p.external_srt:
        # 1-3) external ALREADY TRANSLATED subtitles: no extraction, no translation
        # (SDH/markup cleanup still applies — external files are often SDH too)
        emit("extract", msg=f"external subtitles: {Path(p.external_srt).name}")
        target_cues = srt.clean_cues(srt.parse_srt(Path(p.external_srt)))
        report["cues"] = len(target_cues)
        report["cues_with_text"] = sum(1 for c in target_cues if c.text)
        report["translated"] = "external"
        report["untranslated"] = []
    else:
        # 1) extract the source subtitle
        src_srt = wd / "source.srt"
        if p.sub < 0 or p.sub >= len(info.subs):
            raise ValueError(f"subtitle track s:{p.sub} not found in {p.src.name}")
        if p.force or not _fresh(src_srt):
            emit("extract", msg=f"subtitle {info.subs[p.sub].label()}")
            ffbin.run(["-y", "-v", "error", "-i", str(p.src),
                       "-map", f"0:s:{p.sub}", str(src_srt)], cancel=cancel)

        # 2) clean (cheap, always rebuilt)
        cues = srt.clean_cues(srt.parse_srt(src_srt))
        n_text = sum(1 for c in cues if c.text)
        report["cues"] = len(cues)
        report["cues_with_text"] = n_text
        emit("extract", len(cues), len(cues), f"{len(cues)} cues ({n_text} with text)")

        # 3) translate — unless the subtitle already is in the target language
        if same_lang(info.subs[p.sub].lang, p.target_lang):
            emit("translate", 1, 1, f"skipped — subtitles already {p.target_lang}")
            target_cues = cues
            report["translated"] = "same-language"
            report["untranslated"] = []
        else:
            if translator is None:
                raise ValueError("translator is required")
            # 3a) episode brief: whole-text facts (characters, genders, glossary) that every
            # translation batch rides on; a failed brief degrades to plain translation
            brief_txt = wd / "brief.txt"
            brief_params = {"target_lang": p.target_lang}
            if p.force or not _params_ok(brief_txt, brief_params) or not _fresh(brief_txt, src_srt):
                emit("translate", msg="building episode brief")
                try:
                    brief = translator.build_brief(cues, cancel=cancel)
                except TranslateError as e:
                    if cancel is not None and cancel.is_set():
                        raise
                    brief = ""
                    emit("translate", msg=f"brief unavailable — translating without ({e})")
                brief_txt.write_text(brief, encoding="utf-8")
                _write_params(brief_txt, brief_params)
            else:
                brief = brief_txt.read_text(encoding="utf-8")
            translator.brief = brief

            # 3b) translate
            cache = wd / "translations.json"
            # "sdh": True stays in the stamp for cache compatibility (stripping is always on now)
            tr_params = {"target_lang": p.target_lang, "sdh": True, "source": f"s:{p.sub}",
                         "brief": hashlib.md5(brief.encode("utf-8")).hexdigest()}
            if cache.exists() and (p.force or not _params_ok(cache, tr_params)):
                cache.unlink()
            # stamp before translating, not after: an interrupted run must keep its
            # partial cache (else resume re-translates from scratch)
            _write_params(cache, tr_params)
            emit("translate", 0, n_text, "starting")
            translations = translator.translate_cues(
                cues, cache_path=cache,
                progress=lambda d, t, m: emit("translate", d, t, m), cancel=cancel)
            target_cues, missing = srt.build_target(cues, translations)
            report["translated"] = n_text - len(missing)
            report["untranslated"] = sorted(missing)
    target_text = srt.format_srt(target_cues)
    # write only on change: an untouched mtime lets downstream checkpoints hold
    if not target_srt.exists() or target_srt.read_text(encoding="utf-8") != target_text:
        target_srt.write_text(target_text, encoding="utf-8")

    # 4) loudness reference (only for level tracking)
    gains, ref_wav = None, wd / "ref.wav"
    if p.level_mode == "track":
        if p.force or not _fresh(ref_wav):
            emit("tts", msg="extracting loudness reference")
            mix.extract_ref(p.src, base.type_index, base.channels, ref_wav, cancel=cancel)
        gains, lt_stats = audio.compute_level_gains(
            target_cues, ref_wav, k=p.level_k, csv_path=wd / "leveltrack.csv")
        report["leveltrack"] = lt_stats
    elif p.level_mode == "fixed" and abs(p.fixed_gain_db) > 0.01:
        gains = {c.num: 10.0 ** (p.fixed_gain_db / 20.0) for c in target_cues}

    # 5) synthesize + place
    dub_wav = wd / "dub.wav"
    wav_params = {"voice": p.voice, "level_mode": p.level_mode, "level_k": p.level_k,
                  "fixed_gain_db": p.fixed_gain_db, "max_speed": p.max_speed}
    if (p.force or not _params_ok(dub_wav, wav_params)
            or not _fresh(dub_wav, target_srt, ref_wav if p.level_mode == "track" else None)):
        master, stats = audio.build_track(
            target_cues, wd / "clips", p.voice, info.duration,
            gains=gains, max_speed=p.max_speed,
            progress=lambda d, t, m: emit("tts", d, t, m), cancel=cancel)
        audio.write_wav_mono(dub_wav, master)
        _write_params(dub_wav, wav_params)
        report["placement"] = stats
    else:
        emit("tts", 1, 1, "dub.wav up to date")

    # 6) mix into an audio FILE (two-stage build: inline mux truncated audio once)
    dub_track = wd / "dub_track.mka"
    mix_params = {"audio": p.audio, "duck": p.duck, "duck_ratio": p.duck_ratio,
                  "dub_format": p.dub_format}
    if p.force or not _params_ok(dub_track, mix_params) or not _fresh(dub_track, dub_wav):
        emit("mix", msg=f"{mix.layout_kind(base.channels)} -> {p.dub_format}"
                        f"{' + duck' if p.duck else ''}")
        mix.build_dub_track(p.src, base.type_index, dub_wav, dub_track,
                            channels=base.channels, duck=p.duck, duck_ratio=p.duck_ratio,
                            out_format=p.dub_format, cancel=cancel)
        _write_params(dub_track, mix_params)

    # 7) mux + verify
    emit("mux", msg=p.out_path().name)
    mux.mux(p.src, dub_track, target_srt, p.out_path(),
            keep_audio=p.keep_audio, keep_subs=p.keep_subs,
            target_lang=p.target_lang, cancel=cancel)
    emit("verify", msg="checking result")
    report["verify"] = mux.verify(p.out_path(), info.duration, cancel=cancel)
    (wd / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    emit("done", 1, 1, p.out_path().name)
    return report
