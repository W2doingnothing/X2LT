@echo off
cd /d "%~dp0"
python -m venv .local-runtime
if errorlevel 1 goto fail
".local-runtime\Scripts\python.exe" -m pip install -r requirements-local-video.txt
if errorlevel 1 goto fail
echo Setup complete. Run start-local-video.cmd.
pause
exit /b
:fail
echo Setup failed. Check Python 3.11+ and your network.
pause
exit /b 1
