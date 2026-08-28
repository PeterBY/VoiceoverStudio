# Fetch static LGPL ffmpeg/ffprobe(/ffplay) (BtbN FFmpeg-Builds) into third_party\ffmpeg\.
# LGPL builds only: a GPL ffmpeg would put the whole distributed bundle under GPL terms.
#   powershell -ExecutionPolicy Bypass -File packaging\fetch_ffmpeg.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$api = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
$assets = (Invoke-RestMethod -Uri $api).assets.browser_download_url
# PINNED branch: the ffmpeg 9.x CLI nondeterministically truncates/hangs multi-input
# filter graphs at EOF (verified 2026-08 on n9.0.1). n8.1 is race-free on the same
# graphs. Override: $env:VOS_FFMPEG_BRANCH.
$pin = if ($env:VOS_FFMPEG_BRANCH) { $env:VOS_FFMPEG_BRANCH } else { "n8.1" }
$url = $assets | Where-Object { $_ -match "ffmpeg-$pin-latest-win64-lgpl-[\d.]+\.zip$" } |
    Sort-Object | Select-Object -Last 1
if (-not $url) { throw "no $pin win64-lgpl asset found (see VOS_FFMPEG_BRANCH)" }
$tmp = Join-Path $env:TEMP "vos_ffmpeg"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$zip = Join-Path $tmp "ff.zip"
Write-Host "downloading $url"
Invoke-WebRequest -Uri $url -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$bin = (Get-ChildItem -Path $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1).DirectoryName
$dst = Join-Path $root "third_party\ffmpeg"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
foreach ($f in "ffmpeg.exe", "ffprobe.exe", "ffplay.exe") {
    $src = Join-Path $bin $f
    if (Test-Path $src) { Copy-Item $src $dst -Force }
}
Remove-Item -Recurse -Force $tmp
$ver = & (Join-Path $dst "ffmpeg.exe") -version | Select-Object -First 1
Write-Host $ver
if ((& (Join-Path $dst "ffmpeg.exe") -version) -match "--enable-gpl") { throw "fetched build is GPL, expected LGPL" }
Write-Host "OK (LGPL) -> third_party\ffmpeg\"
