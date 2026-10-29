@echo off
rem MultiProbe Toolkit: double-click to start. "run.bat --dev" restarts by itself when the code changes.
rem It asks for administrator rights (UAC): the Services page needs them to control every MultiProbe
rem service on this computer and to change start types. "run.bat --no-admin" starts without them.
chcp 65001 >nul
cd /d "%~dp0"

fltmc >nul 2>&1 && goto :ready
echo %* | findstr /i /c:"--no-admin" /c:"--elevated" >nul && goto :ready
echo Asking for administrator rights ...
powershell -NoProfile -Command "Start-Process -FilePath $env:ComSpec -ArgumentList '/c \"\"%~f0\" --elevated %*\"' -Verb RunAs" >nul 2>&1 && exit /b
echo Administrator rights were not given: the Services page will only show the server services of this computer.
echo.

:ready
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
