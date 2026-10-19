@echo off
rem MapMaker - double-click to start. The first run installs what it needs (a few minutes).
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py -3
if not exist ".venv\Scripts\python.exe" (
  echo Setting up MapMaker for the first time...
  %PY% -m venv .venv
  if errorlevel 1 goto nopython
)
".venv\Scripts\python.exe" -c "import PySide6.QtSvg, PIL, numpy, scipy" >nul 2>nul
if errorlevel 1 (
  echo Installing the packages MapMaker needs - this takes a few minutes the first time...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
start "" ".venv\Scripts\pythonw.exe" -m mapmaker %*
exit /b

:nopython
echo.
echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" in the installer), then run start.bat again.
pause
exit /b

:failed
echo.
echo Installing the Python packages failed - check the messages above.
pause
