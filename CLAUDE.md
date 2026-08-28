# Voiceover Studio — single-voice voiceover (lektor) dubbing app

Takes a video with embedded subtitles, translates them via an OpenAI-compatible API,
synthesizes a narrator voice with edge-tts, ducks the original under it and muxes back.
**Python + Tkinter/ttkbootstrap GUI, PyInstaller onedir, ffmpeg bundled. Linux and Windows.**
Fully CPU/local except two network calls: edge-tts (free) and the translation API.

## Layout

- `voiceover_studio/core/` — pipeline library, **no GUI imports**: `probe` (ffprobe inventory),
  `srt` (parse/clean/SDH-strip), `translate` (OpenAI-compatible client: episode brief +
  scene-aligned context batches), `tts` (edge-tts + md5 cache),
  `audio` (numpy placement + level-tracking), `mix` (ffmpeg duck/downmix graphs), `mux` (copy-mux + verify),
  `job` (stages, checkpoints, progress, cancel)
- `voiceover_studio/gui/` — ttkbootstrap UI over the same core
- `run_gui.py` / `run_cli.py` — entry points (CLI: `probe` / `voices` / `dub`)
- `packaging/` — PyInstaller spec + ffmpeg fetch scripts (`third_party/` is gitignored)
- `.venv-app/` — dev venv: numpy, edge-tts, httpx, ttkbootstrap (+ pyinstaller)
- `.env` — dev-only secrets/overrides: `AI_APIURL`, `AI_APIKEY`, `AI_MODEL`, `AI_STYLE`
- `NOTES.local.md` — untracked local notes (test assets, machine-specific paths)

## Invariants (hard-won — do not regress)

- **No TTS speed-up** (`max_speed=1.0`): fit by shifting; overlong lines are fixed at translation
  (compact phrasing, collapse repeats). Drift resets at pauses.
- Surround sources: duck **CENTER channel only** (music/effects untouched). Stereo sources: duck both channels.
- **Build the dub audio to a file, then copy-mux** (`-max_interleave_delta 0`). Producing it inline in the
  same ffmpeg run as `-c:v copy` silently truncates audio (metadata full, packets end early).
- Stereo downmix must be **clip-safe**: normalized `pan` coefficients + `alimiter` — a naive sum clips and
  players cut audio dead when the voiceover starts.
- **Always verify the result**: tail `volumedetect` on the dub track (> −70 dB), duration delta ≤ 2 s.
- Cue numbers/timecodes are the backbone (translations map by number) — never renumber.
- Translation is two-pass: `brief.txt` (whole-episode facts: characters, genders, address forms,
  glossary) is built once per file, injected into every batch and md5-hashed into the
  translations-cache stamp; a failed brief degrades to no-brief translation, never fails the job.
  The wire-format contract (`PROTOCOL` in `translate.py`) is fixed in code — prompt edits in
  Settings can't break it. The translations stamp is written BEFORE translating so interrupted
  runs resume from the partial cache.
- edge-tts clip cache key `md5(voice|pitch|rate|text)` — keep stable, caches are reused across runs.
- Subprocesses must pass `CREATE_NO_WINDOW` on Windows (`ffbin.CREATIONFLAGS`) — else every ffmpeg
  call flashes a console window under the windowed GUI.

## Versioning

Single source of truth: `__version__` in `voiceover_studio/__init__.py` (semver `MAJOR.MINOR.PATCH`).
Window title, CLI `--version` and the Windows exe resource all derive from it (spec reads it at
build time). Release procedure: bump `__version__` → commit → `git tag v<X.Y.Z>` (tag must equal
`__version__`) → build/release. Never ship binaries whose tag and `__version__` disagree.

## UI conventions

- All GUI text is **English**; labels terse — no parenthetical explanations in widget labels.
- "External subtitles" = an ALREADY TRANSLATED file: selecting it skips extraction and translation.
- A source subtitle whose language tag matches the target language skips translation (and the
  API-config requirement) automatically — `config.same_lang` handles 639-2 B/T variants; `und` never matches.
- SDH cleanup (noise tags, speaker labels) is ALWAYS on — not an option; it applies to external
  subtitles too.

## Testing

Any mkv with embedded subtitles works; local test assets and machine-specific paths live in
`NOTES.local.md` (untracked).

```
.venv-app/bin/python run_cli.py probe <file>
.venv-app/bin/python run_cli.py dub <file> --audio <a:N> --sub <s:N>
VOS_SMOKE=1 .venv-app/bin/python run_gui.py     # GUI smoke: opens and auto-closes
```

Checkpoints live in `<src>.work/` beside the source — unless the `work_dir` setting / `--work-dir`
relocates them to `<work_dir>/<stem>.<path-hash>.work` (hash keeps same-named sources apart);
`--force` rebuilds. By default the work dir is DELETED after a verified success (`cleanup_work`
setting, on; CLI `--keep-work` keeps it for one run); any failure keeps it for resume/debugging.
Output: `<src>.<LANG>.mkv` (always beside the source).
Config: `~/.config/voiceover-studio/settings.json` (Linux) / `%APPDATA%\VoiceoverStudio\settings.json`
(Windows); `.env` overrides at runtime (dev only). ffmpeg resolution: `VOS_FFMPEG`/`VOS_FFPROBE` env →
bundled `third_party/ffmpeg/` → PATH.

## Packaging

Linux (onedir):
```
bash packaging/fetch_ffmpeg.sh                                        # once per checkout
.venv-app/bin/pyinstaller packaging/voiceover_studio.spec --noconfirm
dist/VoiceoverStudio/voiceover-studio        # GUI; voiceover-studio-cli beside it
```

Windows: build on the Windows side (PyInstaller doesn't cross-compile) in a git clone of this repo,
driveable from WSL via interop. Windows Python 3.12 + venv with the same deps; then:
```
powershell -ExecutionPolicy Bypass -File packaging\fetch_ffmpeg.ps1   # ffmpeg/ffprobe/ffplay .exe
.venv-win\Scripts\pyinstaller.exe packaging\voiceover_studio.spec --noconfirm
dist\VoiceoverStudio\VoiceoverStudio.exe     # + VoiceoverStudio-cli.exe
```

Spec notes: resources land in `_internal/` (PyInstaller 6.x) — `ffbin.app_dir()` uses `sys._MEIPASS`
when frozen; `PIL._tkinter_finder` stays in hiddenimports; ffplay is bundled when the fetch script
provides it (voice Preview — BtbN ships it on both platforms).
Build Linux releases on the oldest available runner/distro (glibc floor).

**ffmpeg licensing (hard rule):** bundle ONLY **LGPL** builds (BtbN `*-lgpl` assets; fetch scripts
verify no `--enable-gpl` and fail otherwise). App is MIT; a GPL ffmpeg would drag the whole bundle
under GPL. LGPL covers the entire pipeline: video is stream-copied, ac3/pcm encoders and all used
audio filters are core LGPL avfilter/avcodec.

**License stance (settled by owner):** source = MIT; prebuilt release bundles are labeled as
distributed under **GPL-3.0 terms** because GPL-3.0 edge-tts is frozen into the executables —
deliberate choice, no subprocess isolation. README/THIRD_PARTY state this; repeat it in release
notes when publishing binaries.

## Releases

GitHub Actions (`.github/workflows/release.yml`): push tag `v*` → parallel Linux (ubuntu-22.04) +
Windows builds (version-equals-tag check, LGPL ffmpeg fetch, PyInstaller, GUI+CLI smoke) → `release`
job attaches both archives to the GitHub Release with the license note. `workflow_dispatch` runs
builds only (artifacts, no release) — use it to validate CI changes. Verified green 2026-08-27.
