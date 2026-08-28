#!/usr/bin/env python3
"""Voiceover Studio CLI — debug/parity entry point over the same core the GUI uses.

Examples:
  python run_cli.py probe video1.mkv video2.mkv
  python run_cli.py voices --lang pl
  python run_cli.py dub ep.mkv --audio 2 --sub 3 --keep-audio all --keep-subs all
  python run_cli.py dub ep.mkv --audio 2 --sub 3 --translate-only
"""
import argparse
import sys
import threading
from pathlib import Path

from voiceover_studio import __version__, config
from voiceover_studio.core import job, probe, srt, translate, tts


def cmd_probe(args):
    groups = {}
    for f in args.files:
        info = probe.probe(f)
        sig = info.signature()
        groups.setdefault(sig, []).append(Path(f).name)
        print(f"\n{f}  ({info.duration:.1f}s)")
        for s in info.streams:
            print(f"  {s.label()}")
        print(f"  signature: {sig}")
    if len(args.files) > 1:
        print(f"\nstructure groups: {len(groups)}")
        for sig, names in groups.items():
            print(f"  [{len(names)}] {sig}: {', '.join(names[:4])}{'…' if len(names) > 4 else ''}")


def cmd_voices(args):
    for v in tts.list_voices(args.lang):
        print(f"{v['ShortName']:32} {v['Gender']:7} {v.get('FriendlyName', '')}")


def _parse_keep(spec, available):
    if spec == "all":
        return [s.type_index for s in available]
    if spec in ("none", ""):
        return []
    return [int(x) for x in spec.split(",")]


def _progress(stage, done, total, msg):
    if total > 1:
        sys.stdout.write(f"\r[{stage}] {done}/{total} {msg}    ")
        if done >= total:
            sys.stdout.write("\n")
    else:
        print(f"[{stage}] {msg}")
    sys.stdout.flush()


def cmd_dub(args):
    cfg = config.load_settings()
    if args.translate_only and args.srt:
        sys.exit("--translate-only makes no sense with --srt (already translated)")
    target_lang = args.lang or cfg["target_lang"]
    work = str(args.work_dir or cfg.get("work_dir", "")).strip()
    translator = None

    def get_translator():
        # lazy: a target-language subtitle needs no translation and no API config
        nonlocal translator
        if translator is None:
            translator = translate.Translator(
                api_url=cfg["api_url"], api_key=cfg["api_key"], model=cfg["api_model"],
                style=cfg["api_style"], prompt_template=cfg["prompt_template"],
                target_lang=target_lang,
                batch_size=int(cfg["batch_size"]), context_lines=int(cfg["context_lines"]),
                lookahead_lines=int(cfg["lookahead_lines"]),
            )
        return translator

    cancel = threading.Event()
    for f in args.files:
        src = Path(f)
        info = probe.probe(src)
        p = job.JobParams(
            src=src,
            audio=args.audio,
            sub=args.sub if args.srt is None else -1,
            external_srt=Path(args.srt) if args.srt else None,
            voice=args.voice or cfg["voice"],
            target_lang=target_lang,
            keep_audio=_parse_keep(args.keep_audio, info.audios),
            keep_subs=_parse_keep(args.keep_subs, info.subs),
            dub_format=args.format or cfg["dub_format"],
            duck=not args.no_duck if args.no_duck is not None else bool(cfg["duck"]),
            duck_ratio=float(args.duck_ratio or cfg["duck_ratio"]),
            level_mode=args.level or cfg["level_mode"],
            level_k=float(cfg["level_k"]),
            fixed_gain_db=float(args.gain_db if args.gain_db is not None else cfg["fixed_gain_db"]),
            max_speed=float(cfg["max_speed"]),
            force=args.force,
            work_root=Path(work) if work else None,
        )
        if args.translate_only:
            wd = p.workdir()
            wd.mkdir(parents=True, exist_ok=True)
            src_srt = wd / "source.srt"
            from voiceover_studio.core import ffbin
            ffbin.run(["-y", "-v", "error", "-i", str(src), "-map", f"0:s:{p.sub}", str(src_srt)])
            cues = srt.clean_cues(srt.parse_srt(src_srt))
            tr = get_translator().translate_cues(
                cues, cache_path=wd / "translations.json",
                progress=lambda d, t, m: _progress("translate", d, t, m))
            target_cues, missing = srt.build_target(cues, tr)
            (wd / "target.srt").write_text(srt.format_srt(target_cues), encoding="utf-8")
            print(f"\n{src.name}: translated {len(tr)}, missing {len(missing)} -> {wd / 'target.srt'}")
            continue
        needs_translation = args.srt is None and not (
            0 <= p.sub < len(info.subs) and config.same_lang(info.subs[p.sub].lang, target_lang))
        report = job.run_job(p, translator=get_translator() if needs_translation else None,
                             progress=_progress, cancel=cancel)
        v = report["verify"]
        print(f"\n=== {src.name} ===")
        print(f"out: {report['out']}")
        print(f"placement: {report.get('placement')}")
        if "leveltrack" in report:
            print(f"leveltrack: {report['leveltrack']}")
        print(f"untranslated: {len(report['untranslated'])}")
        print(f"verify: ok={v['ok']} tail={v['tail_mean_db']}dB dur_delta={v['duration_delta_s']}s "
              f"audio={v['audio_streams']} subs={v['sub_streams']}")


def main():
    # Windows console defaults to cp1252 and crashes on Cyrillic track titles
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    config.load_dotenv()
    ap = argparse.ArgumentParser(prog="voiceover-studio")
    ap.add_argument("--version", action="version", version=f"voiceover-studio {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="show stream layout + structure signature")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("voices", help="list edge-tts voices")
    p.add_argument("--lang", default=None)
    p.set_defaults(fn=cmd_voices)

    p = sub.add_parser("dub", help="dub file(s)")
    p.add_argument("files", nargs="+")
    p.add_argument("--audio", type=int, required=True, help="base original track (0:a:N)")
    p.add_argument("--sub", type=int, default=-1, help="source subtitle (0:s:N)")
    p.add_argument("--srt", default=None,
                   help="external ALREADY TRANSLATED srt (skips translation)")
    p.add_argument("--voice", default=None)
    p.add_argument("--lang", default=None, help="target language code (default from settings)")
    p.add_argument("--keep-audio", default="all", help="all|none|comma list of 0:a:N")
    p.add_argument("--keep-subs", default="all", help="all|none|comma list of 0:s:N")
    p.add_argument("--format", choices=["stereo", "original"], default=None)
    p.add_argument("--no-duck", action="store_const", const=True, default=None)
    p.add_argument("--duck-ratio", type=float, default=None)
    p.add_argument("--level", choices=["track", "fixed", "off"], default=None)
    p.add_argument("--gain-db", type=float, default=None, help="fixed level offset (with --level fixed)")
    p.add_argument("--work-dir", default=None,
                   help="folder for work/cache dirs (default from settings; else beside the source)")
    p.add_argument("--force", action="store_true", help="ignore checkpoints, rebuild")
    p.add_argument("--translate-only", action="store_true")
    p.set_defaults(fn=cmd_dub)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
