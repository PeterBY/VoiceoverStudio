#!/bin/bash
# Fetch the pinned static ffmpeg/ffprobe into third_party/ffmpeg/ (Linux x86_64).
# third_party/ is gitignored; run this once per checkout before packaging.
set -e
cd "$(dirname "$0")/.."
URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "downloading $URL"
curl -sL -o "$TMP/ff.tar.xz" "$URL"
tar xf "$TMP/ff.tar.xz" -C "$TMP"
mkdir -p third_party/ffmpeg
cp "$TMP"/ffmpeg-*-static/ffmpeg "$TMP"/ffmpeg-*-static/ffprobe third_party/ffmpeg/
chmod +x third_party/ffmpeg/ffmpeg third_party/ffmpeg/ffprobe
third_party/ffmpeg/ffmpeg -version | head -1
echo "OK -> third_party/ffmpeg/"
