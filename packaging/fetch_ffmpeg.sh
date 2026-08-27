#!/bin/bash
# Fetch static LGPL ffmpeg/ffprobe (BtbN FFmpeg-Builds) into third_party/ffmpeg/ (Linux x86_64).
# LGPL builds only: a GPL ffmpeg would put the whole distributed bundle under GPL terms.
# third_party/ is gitignored; run once per checkout before packaging.
set -e
cd "$(dirname "$0")/.."
API="https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
URLS=$(curl -s "$API" | grep -oE '"browser_download_url": *"[^"]+"' | cut -d'"' -f4)
# prefer a release-branch build (ffmpeg-nX.Y-latest-linux64-lgpl-X.Y.tar.xz), fall back to master
URL=$(echo "$URLS" | grep -E 'ffmpeg-n[0-9.]+-latest-linux64-lgpl-[0-9.]+\.tar\.xz$' | sort -V | tail -1)
[ -z "$URL" ] && URL=$(echo "$URLS" | grep -E 'master-latest-linux64-lgpl\.tar\.xz$' | head -1)
[ -z "$URL" ] && { echo "no linux64-lgpl asset found"; exit 1; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "downloading $URL"
curl -sL -o "$TMP/ff.tar.xz" "$URL"
tar xf "$TMP/ff.tar.xz" -C "$TMP"
mkdir -p third_party/ffmpeg
cp "$TMP"/ffmpeg-*/bin/ffmpeg "$TMP"/ffmpeg-*/bin/ffprobe third_party/ffmpeg/
[ -f "$TMP"/ffmpeg-*/bin/ffplay ] && cp "$TMP"/ffmpeg-*/bin/ffplay third_party/ffmpeg/ || true
chmod +x third_party/ffmpeg/ff*
third_party/ffmpeg/ffmpeg -version | head -1
if third_party/ffmpeg/ffmpeg -version | grep -q -- "--enable-gpl"; then
  echo "ERROR: fetched build is GPL, expected LGPL"; exit 1
fi
echo "OK (LGPL) -> third_party/ffmpeg/"
