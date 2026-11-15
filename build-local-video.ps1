$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$iconPath = Join-Path $PSScriptRoot 'app.ico'
& '.\.local-runtime\Scripts\python.exe' -m PyInstaller --noconfirm --onedir --contents-directory 'local-video-runtime' --windowed --name '本地视频下载器' --distpath '.dist-local' --workpath '.build-local/work' --specpath '.build-local' --collect-all gallery_dl --hidden-import socks --icon $iconPath 'launch_local_video.py'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
$builtDir = Join-Path $PSScriptRoot '.dist-local/本地视频下载器'
& '.\.local-runtime\Scripts\python.exe' 'scripts/prepare-release.py' --record-build $builtDir
if ($LASTEXITCODE -ne 0) { throw 'Release provenance check failed; commit source and install locked build dependencies' }
$runtimeDir = Join-Path $PSScriptRoot 'local-video-runtime'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $builtDir '本地视频下载器.exe') -Destination $PSScriptRoot -Force
Get-ChildItem -LiteralPath (Join-Path $builtDir 'local-video-runtime') | Copy-Item -Destination $runtimeDir -Recurse -Force
