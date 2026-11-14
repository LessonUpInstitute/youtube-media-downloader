@echo off
setlocal EnableExtensions
title Build YouTube Media Downloader EXE

REM Run this file from the root of your Python project.
REM Expected source file: main.py
REM Optional tools:
REM   tools\deno.exe  OR  js_runtime\deno.exe
REM   ffmpeg_bin\ffmpeg.exe + ffmpeg_bin\ffprobe.exe
REM   OR C:\ffmpeg\bin\ffmpeg.exe + C:\ffmpeg\bin\ffprobe.exe

cd /d "%~dp0"

echo ================================================
echo     YouTube Media Downloader - EXE Builder
echo ================================================
echo.

if not exist "main.py" (
    echo ERROR: main.py was not found in this folder.
    echo Place build_exe.bat beside main.py and run it again.
    pause
    exit /b 1
)

set "DENO="
if exist "tools\deno.exe" set "DENO=tools\deno.exe"
if not defined DENO if exist "js_runtime\deno.exe" set "DENO=js_runtime\deno.exe"
if not defined DENO if exist "deno.exe" set "DENO=deno.exe"

set "FFMPEG_DIR="
if exist "ffmpeg_bin\ffmpeg.exe" if exist "ffmpeg_bin\ffprobe.exe" set "FFMPEG_DIR=ffmpeg_bin"
if not defined FFMPEG_DIR if exist "C:\ffmpeg\bin\ffmpeg.exe" if exist "C:\ffmpeg\bin\ffprobe.exe" set "FFMPEG_DIR=C:\ffmpeg\bin"

if not defined DENO (
    echo ERROR: deno.exe was not found.
    echo Place it in tools\deno.exe or js_runtime\deno.exe.
    pause
    exit /b 1
)

if not defined FFMPEG_DIR (
    echo ERROR: ffmpeg.exe and ffprobe.exe were not found.
    echo Place both in ffmpeg_bin\ or install them in C:\ffmpeg\bin\.
    pause
    exit /b 1
)

echo Deno:   %DENO%
echo FFmpeg: %FFMPEG_DIR%
echo.

echo [1/4] Installing / updating build dependencies...
py -m pip install --upgrade pyinstaller "yt-dlp[default]" PySide6
if errorlevel 1 goto failed

echo.
echo [2/4] Preparing PyInstaller command...

if exist "assets\app_icon.ico" (
    set "ICON_ARG=--icon assets\app_icon.ico"
) else (
    set "ICON_ARG="
)

if exist "assets" (
    set "ASSET_ARG=--add-data assets;assets"
) else (
    set "ASSET_ARG="
)

echo.
echo [3/4] Building EXE...
py -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "YouTubeMediaDownloader" ^
  %ICON_ARG% ^
  %ASSET_ARG% ^
  --add-binary "%FFMPEG_DIR%\ffmpeg.exe;ffmpeg_bin" ^
  --add-binary "%FFMPEG_DIR%\ffprobe.exe;ffmpeg_bin" ^
  --add-binary "%DENO%;js_runtime" ^
  --collect-all yt_dlp ^
  --collect-all certifi ^
  --hidden-import PySide6.QtNetwork ^
  main.py

if errorlevel 1 goto failed

echo.
echo [4/4] Build completed successfully.
echo Your EXE is here:
echo %CD%\dist\YouTubeMediaDownloader.exe
echo.
pause
exit /b 0

:failed
echo.
echo BUILD FAILED. Check the error messages above.
echo.
pause
exit /b 1