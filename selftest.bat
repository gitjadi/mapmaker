@echo off
rem Runs MapMaker's automatic self-test. Screenshots and a report are written to data\selftest\
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py -3
if not exist ".venv\Scripts\python.exe" (
  echo Setting up MapMaker for the first time...
  %PY% -m venv .venv
)
".venv\Scripts\python.exe" -c "import PySide6.QtSvg, PIL, numpy, scipy" >nul 2>nul
if errorlevel 1 (
  echo Installing the packages MapMaker needs - this takes a few minutes the first time...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
".venv\Scripts\python.exe" -m mapmaker --selftest > selftest_log.txt 2>&1
type selftest_log.txt
echo.
echo Report: data\selftest\result.txt
pause
