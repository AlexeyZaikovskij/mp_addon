@echo off
rem MultiProbe Toolkit: double-click to start. "run.bat --dev" restarts by itself when the code changes.
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python environment .venv ...
    python -m venv .venv || goto :failed
)
rem Quick when nothing changed; after "git pull" it brings new dependencies.
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || goto :failed
if not exist "config.env" copy "config.example.env" "config.env" >nul

".venv\Scripts\python.exe" run.py %*
echo.
echo The Toolkit has stopped.
pause
exit /b

:failed
echo.
echo Could not prepare the Python environment, see the messages above.
pause
exit /b 1
