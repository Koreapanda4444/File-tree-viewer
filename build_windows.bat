@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1
python -c "import struct, sys; raise SystemExit(0 if sys.version_info >= (3, 12) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 (
  echo Python 3.12 or newer, 64-bit, must be available as python.
  popd
  exit /b 1
)
python -m pip install -r requirements.txt "pyinstaller>=6.14,<7"
if errorlevel 1 goto failed
python packaging\windows_resources.py
if errorlevel 1 goto failed
python -m PyInstaller --noconfirm --clean --onedir --windowed --name FileTreeViewer --contents-directory _internal --distpath dist --workpath build\windows\pyinstaller --specpath build\windows --icon "%CD%\assets\app-icon.ico" --version-file "%CD%\build\windows\version.txt" --add-data "%CD%\assets\app-icon.png;assets" --add-data "%CD%\ui\translations;ui/translations" --collect-submodules send2trash "%CD%\main.py"
if errorlevel 1 goto failed
echo Built dist\FileTreeViewer\FileTreeViewer.exe. Keep the entire program folder together.
popd
exit /b 0
:failed
echo Windows build failed.
popd
exit /b 1
