# Fetch static ffmpeg/ffprobe/ffplay (gyan.dev release essentials) into third_party/ffmpeg/.
# Windows counterpart of fetch_ffmpeg.sh; run from anywhere:
#   powershell -ExecutionPolicy Bypass -File packaging\fetch_ffmpeg.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$tmp = Join-Path $env:TEMP "loc_ffmpeg"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$zip = Join-Path $tmp "ff.zip"
Write-Host "downloading $url"
Invoke-WebRequest -Uri $url -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$bin = Get-ChildItem -Path $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
$dst = Join-Path $root "third_party\ffmpeg"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
foreach ($f in "ffmpeg.exe", "ffprobe.exe", "ffplay.exe") {
    Copy-Item (Join-Path $bin.DirectoryName $f) $dst -Force
}
Remove-Item -Recurse -Force $tmp
& (Join-Path $dst "ffmpeg.exe") -version | Select-Object -First 1
Write-Host "OK -> third_party\ffmpeg\"
