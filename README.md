# Voiceover Studio

Single-narrator voiceover ("lektor") dubbing for videos: pick files, pick a voice, get a
translated narrator track mixed over the quieted original.

Give it a video with embedded subtitles and it will:

1. extract the subtitle track you choose (SDH noise tags and speaker labels are cleaned automatically);
2. translate it with any **OpenAI-compatible API** — your endpoint, your key, editable prompt;
3. synthesize a narrator with **Microsoft Edge neural voices** (edge-tts, 300+ voices);
4. duck the original audio under the narrator — **center channel only** on surround sources, so
   music and effects stay untouched — and mux everything back.

The result is `<video>.<LANG>.mkv` beside the source: video stream-copied (no re-encode), the new
voiceover track as default audio, translated subtitles included, plus whichever original audio and
subtitle tracks you chose to keep.

## Features

- **Batch processing** for whole seasons: files are grouped by stream structure, mismatches flagged;
  per-file progress and cancel
- **Resume for free**: translations and synthesized lines are cached in a `.work/` folder beside each
  source — re-runs and parameter tweaks only redo the affected stages
- **Narrator volume tracking**: the voiceover follows the loudness of the original scene
  (or a fixed ±dB offset, your choice)
- Ducking presets or off; dub track as **stereo downmix** or the **original channel layout**
- **External subtitles mode**: feed an already-translated `.srt` and skip the translation API entirely
- Voice preview button; result is verified automatically (duration match, audible tail)
- GUI (Tkinter + ttkbootstrap) and a CLI twin over the same core

## Download

Prebuilt bundles for **Linux** and **Windows** are attached to
[Releases](../../releases). Unpack anywhere and run `VoiceoverStudio.exe` (Windows) or
`voiceover-studio` (Linux). No Python, no ffmpeg installation — everything is bundled.

## Run from source

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install numpy edge-tts httpx ttkbootstrap
bash packaging/fetch_ffmpeg.sh        # or: use ffmpeg/ffprobe from PATH
python run_gui.py                     # GUI
python run_cli.py probe <file>        # CLI
python run_cli.py dub <file> --audio 0 --sub 0
```

Tkinter must be available (`python3-tk` package on Debian/Ubuntu; included in the python.org
Windows installer).

## Configuration

⚙ Settings in the app: AI API host, key and model (any OpenAI-compatible server — both
`/v1/responses` and `/v1/chat/completions` are supported, auto-detected), plus the translation
prompt. Stored per-user: `~/.config/voiceover-studio/settings.json` (Linux),
`%APPDATA%\VoiceoverStudio\settings.json` (Windows).

## Notes

- **No TTS speed-up by design.** Overlong lines are absorbed by shifting and resolve at pauses; the
  translation prompt asks for compact phrasing. This keeps the narrator natural at the cost of a
  small drift in very dense scenes.
- edge-tts uses Microsoft's online voices — an internet connection is required while synthesizing.
- Windows may show a SmartScreen warning for downloaded unsigned binaries.

## License

The Voiceover Studio source code is licensed under the [MIT License](LICENSE).

Release bundles additionally contain third-party components under their own licenses — notably
**LGPL builds of ffmpeg/ffprobe/ffplay** ([BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)),
which are invoked as separate processes, never linked. GPL ffmpeg builds must not be bundled.
See [THIRD_PARTY.md](THIRD_PARTY.md) for the full list.
