# YouTube Media Downloader v3.0.0

## Highlights

- Pause and resume the active download.
- Queue previews show each item's thumbnail as its metadata is retrieved.
- Download history is stored locally in SQLite and survives application restarts.
- Custom application icon support.
- Windows PyInstaller build script for a one-file executable.
- FFmpeg, FFprobe and Deno are bundled into the executable when the required binaries are present locally.

## Build

See [README.md](README.md) and run `build_exe.bat` on Windows after placing the FFmpeg and Deno executables in the documented folders.

## Notes

Downloads run sequentially. Pause/resume applies to the active item and takes effect at yt-dlp progress checkpoints.

This is a source release. A compiled Windows installer is not attached to this repository commit.
