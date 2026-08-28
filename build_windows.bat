@echo off
setlocal

set "BUILD_ENV=%TEMP%\FileTreeViewer-build-env"
set "BUILD_TEMP=%TEMP%\FileTreeViewer-pyinstaller"

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  exit /b 1
)

py -3 -m venv "%BUILD_ENV%"
if errorlevel 1 exit /b 1

"%BUILD_ENV%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1

"%BUILD_ENV%\Scripts\python.exe" -m pip install -r requirements.txt "pyinstaller>=6.14,<7"
if errorlevel 1 exit /b 1

if not exist "%BUILD_TEMP%" mkdir "%BUILD_TEMP%"

"%BUILD_ENV%\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name FileTreeViewer ^
  --collect-submodules send2trash ^
  --distpath release ^
  --workpath "%BUILD_TEMP%\work" ^
  --specpath "%BUILD_TEMP%" ^
  main.py
if errorlevel 1 exit /b 1

echo Built release\FileTreeViewer.exe
