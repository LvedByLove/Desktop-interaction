@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  [1/2] Nuitka standalone build (incremental, cached via clcache)
echo ============================================================
.venv\Scripts\python.exe -m nuitka ^
  --mode=standalone ^
  --enable-plugins=pyqt5 ^
  --windows-console-mode=disable ^
  --windows-icon-from-ico=assets\image\Redstone.ico ^
  --include-data-dir=models=models ^
  --include-data-dir=scripts=scripts ^
  --include-data-dir=src=src ^
  --include-data-dir=libs=libs ^
  --include-data-dir=assets=assets ^
  --include-package-data=pypinyin ^
  --include-package=openai ^
  --include-package=httpx ^
  --include-package=httpcore ^
  --include-package=h11 ^
  --include-package=anyio ^
  --include-package=sniffio ^
  --include-package=distro ^
  --include-package=jiter ^
  --include-data-files=libs\libopus\win\x64\opus.dll=libs\libopus\win\x64\opus.dll ^
  --assume-yes-for-downloads ^
  --output-dir=nuitka_build ^
  --output-filename="Desktop interaction.exe" ^
  main.py
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  [2/2] Inno Setup build
echo ============================================================
pushd installer
"C:\Users\LiSIwei\AppData\Local\Programs\Inno Setup 6\ISCC.exe" "xiao-zhi-nuitka.iss"
if errorlevel 1 goto :fail
popd

echo.
echo ============================================================
echo  Done: dist\Desktop interaction-setup-nuitka.exe
echo ============================================================
exit /b 0

:fail
popd >nul 2>&1
echo.
echo  BUILD FAILED (errorlevel=%errorlevel%)
exit /b 1