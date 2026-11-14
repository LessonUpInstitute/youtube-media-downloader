# YouTube Media Downloader

A Windows desktop media downloader built with Python, PySide6 and yt-dlp.

## Features

- Batch processing of multiple YouTube URLs
- Audio and video download modes
- Audio output formats: MP3, M4A, WAV, FLAC and best available
- Video output formats: MP4 and MKV, with quality selection
- Per-file progress, speed, ETA and size information
- Queue view with thumbnail previews as metadata is resolved
- Pause and resume for the active download
- Persistent local download history using SQLite
- Custom dark desktop interface
- Build script for a single-file Windows executable with FFmpeg and Deno bundled

## Requirements

- Windows 10/11, 64-bit
- Python 3.11+
- PySide6
- yt-dlp
- FFmpeg and FFprobe
- Deno (for supported YouTube JavaScript challenge handling)

Install Python dependencies:

```powershell
py -m pip install --upgrade PySide6 "yt-dlp[default]" pyinstaller
```

## Run from source

Place the application source as `main.py` in the project root, then run:

```powershell
py main.py
```

## Build a Windows EXE

1. Put `build_exe.bat` beside `main.py`.
2. Place `ffmpeg.exe` and `ffprobe.exe` in `ffmpeg_bin\\`, or install them in `C:\\ffmpeg\\bin\\`.
3. Place `deno.exe` in `tools\\deno.exe` or `js_runtime\\deno.exe`.
4. Run `build_exe.bat`.

The executable is generated at:

`dist\\YouTubeMediaDownloader.exe`

The build script bundles FFmpeg, FFprobe, Deno, yt-dlp and its collected dependencies. Put your custom icon at `assets\\app_icon.ico` to apply it to the EXE.

## Pause / resume notes

Downloads are processed sequentially. Pause/resume applies to the currently active download. It takes effect at yt-dlp progress checkpoints, so some extractor or post-processing operations may not pause immediately.

## Download history

History is stored in the current Windows user's local application data folder:

`%LOCALAPPDATA%\\YouTubeMediaDownloader\\download_history.sqlite3`

The history database is kept outside the installation directory.

## Included files

- `main.py` — application source
- `build_exe.bat` — Windows EXE build script
- `requirements.txt` — Python dependencies
- `assets/app_icon.ico` — application icon

## Legal notice

Only download media that you own or are authorized to download. You are responsible for complying with platform terms and applicable copyright law.
