@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 (
  echo Python 3.12 or newer is required for the Windows build.
  popd
  exit /b 1
)
:allocate
set "FTV_BUILD_ROOT=%TEMP%\FileTreeViewer-build-%RANDOM%-%RANDOM%"
if exist "%FTV_BUILD_ROOT%" goto allocate
mkdir "%FTV_BUILD_ROOT%"
if errorlevel 1 goto failed
set "FTV_BUILD_ENV=%FTV_BUILD_ROOT%\env"
py -3 -m venv "%FTV_BUILD_ENV%"
if errorlevel 1 goto failed
"%FTV_BUILD_ENV%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
"%FTV_BUILD_ENV%\Scripts\python.exe" -m pip install -r requirements.txt "pyinstaller>=6.14,<7"
if errorlevel 1 goto failed
"%FTV_BUILD_ENV%\Scripts\python.exe" main.py --smoke-test
if errorlevel 1 goto failed
"%FTV_BUILD_ENV%\Scripts\python.exe" scripts\verify_core.py
if errorlevel 1 goto failed
"%FTV_BUILD_ENV%\Scripts\python.exe" scripts\verify_portable.py
if errorlevel 1 goto failed
"%FTV_BUILD_ENV%\Scripts\python.exe" scripts\verify_gui.py
if errorlevel 1 goto failed
"%FTV_BUILD_ENV%\Scripts\python.exe" scripts\build_windows.py
if errorlevel 1 goto failed
"%FTV_BUILD_ENV%\Scripts\python.exe" scripts\verify_windows.py dist\FileTreeViewer
if errorlevel 1 goto failed
"%FTV_BUILD_ENV%\Scripts\python.exe" scripts\package_windows.py dist\FileTreeViewer
if errorlevel 1 goto failed
for %%F in (release\FileTreeViewer-*-windows-x64.zip) do (
  "%FTV_BUILD_ENV%\Scripts\python.exe" scripts\verify_windows.py "%%F"
  if errorlevel 1 goto failed
)
rmdir /s /q "%FTV_BUILD_ROOT%"
echo Built and verified the portable ZIP in release\ with SHA256SUMS.
popd
exit /b 0
:failed
if exist "%FTV_BUILD_ROOT%" rmdir /s /q "%FTV_BUILD_ROOT%"
echo Build or portable verification failed.
popd
exit /b 1
