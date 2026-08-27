# Third-party components

## Bundled executables (invoked as separate processes, not linked)

| Component | License | Source |
|---|---|---|
| ffmpeg, ffprobe, ffplay | **LGPL 2.1+** (LGPL builds only — no `--enable-gpl`) | binaries: [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) · source: [ffmpeg.org](https://ffmpeg.org/download.html) |

The fetch scripts (`packaging/fetch_ffmpeg.*`) download LGPL builds and fail on a GPL one.
Do not replace them with GPL builds: that would place the distributed bundle under GPL terms.

## Python libraries frozen into the release bundles

| Library | License |
|---|---|
| edge-tts | GPL-3.0 |
| numpy | BSD-3-Clause |
| httpx | BSD-3-Clause |
| ttkbootstrap | MIT |
| Pillow | MIT-CMU |
| aiohttp | Apache-2.0 |
| CPython runtime | PSF-2.0 |

Release bundles are produced with PyInstaller (GPL 2.0 with a bootloader exception that does not
affect the licensing of the produced bundle).

**Note:** edge-tts (GPL-3.0) is frozen into the release executables, so the prebuilt release
bundles as a whole are distributed under GPL-3.0 terms. The Voiceover Studio source code itself
remains MIT-licensed.
