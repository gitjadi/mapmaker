@echo off
rem Builds a Windows installer: dist\MapMaker-Setup-<version>.exe
rem   1. packages MapMaker with PyInstaller (no Python needed on the other computer)
rem   2. wraps it in an installer with Inno Setup (free; installed automatically with winget if missing)
rem Copy the Setup .exe to the other computer and run it. Your maps (.mapmaker files) can simply be copied.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat once first, so the Python environment exists.
  pause
  exit /b 1
)
set PY=".venv\Scripts\python.exe"
set VER=
%PY% -c "import mapmaker; print(mapmaker.__version__)" > "%TEMP%\mapmaker_version.txt"
set /p VER=<"%TEMP%\mapmaker_version.txt"
del "%TEMP%\mapmaker_version.txt" 2>nul
if not defined VER set VER=0.1.0
echo Building MapMaker %VER% ...
%PY% -m pip install --upgrade pyinstaller
%PY% tools\make_icon.py
%PY% -m PyInstaller --noconfirm --clean --windowed --name MapMaker --icon installer\MapMaker.ico ^
  --collect-submodules mapmaker --collect-data mapmaker ^
  --add-data "mapmaker\gui\icon.png;mapmaker\gui" ^
  --exclude-module starlette --exclude-module uvicorn MapMaker.pyw
if errorlevel 1 goto failed

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  echo.
  echo Inno Setup - free, makes the installer - is not installed. Installing it with winget...
  winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements
  if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
)
if not defined ISCC (
  echo.
  echo Could not find Inno Setup. Install it from https://jrsoftware.org/isinfo.php and run this again.
  echo Meanwhile the packaged program is in dist\MapMaker - you can also zip that folder and copy it.
  pause
  exit /b 1
)
%ISCC% /DAppVersion=%VER% installer\MapMaker.iss
if errorlevel 1 goto failed
echo.
echo Done: dist\MapMaker-Setup-%VER%.exe
echo Copy it to the other computer and run it.
pause
exit /b 0

:failed
echo.
echo Building failed - see the messages above.
pause
exit /b 1
