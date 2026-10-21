@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo Python 3.10 or newer is required.
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
"%FTV_BUILD_ENV%\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name FileTreeViewer ^
  --collect-submodules send2trash ^
  --distpath release ^
  --workpath "%FTV_BUILD_ROOT%\work" ^
  --specpath "%FTV_BUILD_ROOT%" ^
  main.py
if errorlevel 1 goto failed
powershell -NoProfile -Command "$p=Start-Process -FilePath '.\release\FileTreeViewer.exe' -ArgumentList '--smoke-test' -PassThru; if (-not $p.WaitForExit(60000)) { Stop-Process -Id $p.Id -Force; exit 1 }; exit $p.ExitCode"
if errorlevel 1 goto failed
rmdir /s /q "%FTV_BUILD_ROOT%"
echo Built and verified release\FileTreeViewer.exe
popd
exit /b 0

:failed
if exist "%FTV_BUILD_ROOT%" rmdir /s /q "%FTV_BUILD_ROOT%"
echo Build or startup verification failed.
popd
exit /b 1
