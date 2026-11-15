@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0本地视频下载器.exe" (
  start "" "%~dp0本地视频下载器.exe"
  exit /b
)
if exist "%~dp0.local-runtime\Scripts\pythonw.exe" (
  start "" "%~dp0.local-runtime\Scripts\pythonw.exe" "%~dp0launch_local_video.py"
  exit /b
)
echo Please run setup-local-video.cmd first.
pause
