import os
import sys
import time
import sqlite3
import threading
from datetime import datetime
from urllib.request import Request, urlopen
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPixmap, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QTextEdit, QLineEdit, QFileDialog, QComboBox, QProgressBar,
    QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QSizePolicy, QScrollArea
)

try:
    import yt_dlp
except ImportError:
    yt_dlp = None


APP_NAME = "YouTube Media Downloader"
APP_VERSION = "3.0.0"

C = {
    "bg": "#080F1F",
    "sidebar": "#0D1830",
    "panel": "#101D35",
    "panel2": "#142541",
    "input": "#0B172B",
    "border": "#2A3E60",
    "blue": "#0878F9",
    "blue_hover": "#0065DB",
    "blue_soft": "#123D78",
    "red": "#F13B4B",
    "red_hover": "#D92E40",
    "text": "#F5F7FC",
    "muted": "#A9B7CC",
    "green": "#35D28B",
    "yellow": "#F7C948",
}


def bundle_root():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def tool_paths():
    root = bundle_root()
    if getattr(sys, "frozen", False):
        ffmpeg_dir = root / "ffmpeg_bin"
        deno = root / "js_runtime" / "deno.exe"
    else:
        ffmpeg_exe = shutil.which("ffmpeg")
        ffmpeg_dir = Path(ffmpeg_exe).resolve().parent if ffmpeg_exe else Path(r"C:\ffmpeg\bin")
        deno = root / "tools" / "deno.exe"
    return ffmpeg_dir, deno


def app_data_dir():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "YouTubeMediaDownloader"
    path.mkdir(parents=True, exist_ok=True)
    return path


def history_db_path():
    return app_data_dir() / "download_history.sqlite3"


def human_bytes(value):
    if value is None:
        return "—"
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def human_time(seconds):
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    mins, secs = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    return f"{hrs}h {mins}m" if hrs else (f"{mins}m {secs}s" if mins else f"{secs}s")


class DownloadWorker(QThread):
    file_started = Signal(int, int, str)
    progress = Signal(dict)
    thumbnail_urls = Signal(list)
    thumbnail_data = Signal(bytes)
    queue_preview = Signal(int, str, bytes)
    item_state = Signal(int, str)
    stage = Signal(str, str)
    succeeded = Signal(int, str)
    failed = Signal(int, str, str)
    log = Signal(str)
    finished_batch = Signal(bool, int, int)

    def __init__(self, urls, output_dir, settings, parent=None):
        super().__init__(parent)
        self.urls = urls
        self.output_dir = output_dir
        self.settings = settings
        self.stop_requested = False
        self.pause_requested = False
        self.pause_condition = threading.Condition()
        self.success_count = 0
        self.failed_count = 0

    def stop(self):
        self.stop_requested = True
        with self.pause_condition:
            self.pause_condition.notify_all()

    def pause(self):
        self.pause_requested = True
        self.item_state.emit(self.current_index, "Paused")

    def resume(self):
        self.pause_requested = False
        with self.pause_condition:
            self.pause_condition.notify_all()
        self.item_state.emit(self.current_index, "Downloading")

    def wait_if_paused(self):
        with self.pause_condition:
            while self.pause_requested and not self.stop_requested:
                self.pause_condition.wait(0.25)
        if self.stop_requested:
            raise yt_dlp.utils.DownloadError("Stopped by user")

    def make_options(self, index):
        ffmpeg_dir, deno_path = tool_paths()
        opts = {
            "noplaylist": True,
            "outtmpl": os.path.join(self.output_dir, "%(title)s.%(ext)s"),
            "windowsfilenames": True,
            "ignoreerrors": False,
            "quiet": True,
            "no_warnings": True,
            "continuedl": True,
            "overwrites": False,
            "progress_hooks": [self.progress_hook(index)],
        }

        if (ffmpeg_dir / "ffmpeg.exe").is_file():
            opts["ffmpeg_location"] = str(ffmpeg_dir)
        if deno_path.is_file():
            opts["js_runtimes"] = {"deno": {"path": str(deno_path)}}
        elif shutil.which("deno"):
            opts["js_runtimes"] = {"deno": {"path": shutil.which("deno")}}

        if self.settings["mode"] == "Audio":
            opts["format"] = "bestaudio/best"
            fmt = self.settings["audio_format"]
            if fmt != "Best available":
                pp = {"key": "FFmpegExtractAudio", "preferredcodec": fmt.lower()}
                if fmt == "MP3":
                    pp["preferredquality"] = self.settings["audio_bitrate"].split()[0]
                opts["postprocessors"] = [pp]
                opts["postprocessor_args"] = ["-id3v2_version", "3"]
        else:
            quality = self.settings["video_quality"]
            if quality == "Best available":
                fmt = "bestvideo+bestaudio/best"
            else:
                height = int(quality.split("p")[0])
                fmt = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
            opts["format"] = fmt
            opts["merge_output_format"] = self.settings["video_format"].lower()
        return opts

    def progress_hook(self, index):
        def hook(data):
            self.wait_if_paused()
            status = data.get("status")
            info = data.get("info_dict") or {}
            title = info.get("title") or data.get("filename") or f"Link {index}"
            if status == "downloading":
                downloaded = data.get("downloaded_bytes") or 0
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                pct = downloaded / total * 100 if total else 0
                self.progress.emit({
                    "index": index, "title": str(title),
                    "percent": max(0, min(100, pct)),
                    "downloaded": downloaded, "total": total,
                    "speed": data.get("speed"), "eta": data.get("eta"),
                })
            elif status == "finished":
                self.stage.emit(str(title), "Download received · processing / merging / converting…")
        return hook

    @staticmethod
    def get_thumbnail_candidates(info):
        """Return distinct thumbnail URLs, preferring larger images."""
        if not isinstance(info, dict):
            return []

        candidates = []
        for item in (info.get("thumbnails") or []):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            width = item.get("width") or 0
            height = item.get("height") or 0
            candidates.append((int(width) * int(height), str(item["url"])))

        direct = info.get("thumbnail")
        if direct:
            candidates.append((10**12, str(direct)))

        # Highest-resolution candidates first, with duplicate URLs removed.
        result = []
        seen = set()
        for _, url in sorted(candidates, key=lambda item: item[0], reverse=True):
            if url not in seen:
                seen.add(url)
                result.append(url)
        return result

    def fetch_thumbnail_bytes(self, candidates):
        """Try a few thumbnail URLs using Python's HTTPS stack."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://www.youtube.com/",
        }
        for image_url in (candidates or [])[:5]:
            if self.stop_requested:
                return None
            try:
                request = Request(str(image_url), headers=headers)
                with urlopen(request, timeout=7) as response:
                    content_type = response.headers.get("Content-Type", "")
                    image_data = response.read(5 * 1024 * 1024 + 1)
                if len(image_data) > 5 * 1024 * 1024:
                    continue
                if content_type.startswith("image/") and image_data:
                    return image_data
            except Exception as exc:
                self.log.emit(
                    f"Thumbnail source skipped: {type(exc).__name__}: {exc}"
                )
        return None

    def run(self):
        total = len(self.urls)
        for index, url in enumerate(self.urls, 1):
            if self.stop_requested:
                break
            self.current_index = index
            self.item_state.emit(index, "Preparing")
            self.file_started.emit(index, total, url)
            self.log.emit(f"[{index}/{total}] Starting: {url}")
            try:
                with yt_dlp.YoutubeDL(self.make_options(index)) as ydl:
                    # Extract metadata first: the progress hook's info_dict is not
                    # guaranteed to contain thumbnail fields on every extractor.
                    info = ydl.extract_info(url, download=False)
                    if info is None:
                        raise RuntimeError("No media information was returned.")

                    thumbnail_candidates = self.get_thumbnail_candidates(info)
                    if thumbnail_candidates:
                        self.log.emit(
                            f"[{index}/{total}] Found {len(thumbnail_candidates)} thumbnail candidate(s); fetching preview."
                        )
                        try:
                            image_data = self.fetch_thumbnail_bytes(thumbnail_candidates)
                            if image_data:
                                self.thumbnail_data.emit(image_data)
                                self.queue_preview.emit(index, str(info.get("title") or f"Link {index}"), image_data)
                                self.log.emit(f"[{index}/{total}] Thumbnail preview loaded.")
                            else:
                                self.log.emit(
                                    f"[{index}/{total}] Thumbnail preview unavailable; continuing download."
                                )
                        except Exception as thumb_exc:
                            self.log.emit(
                                f"[{index}/{total}] Thumbnail preview skipped: {thumb_exc}"
                            )
                    else:
                        self.log.emit(
                            f"[{index}/{total}] No thumbnail URL was provided by the source."
                        )

                    # Process the already-extracted result, so yt-dlp does not
                    # need to resolve the URL a second time.
                    self.wait_if_paused()
                    self.item_state.emit(index, "Downloading")
                    info = ydl.process_ie_result(info, download=True)

                if info is None:
                    raise RuntimeError("The download did not return media information.")
                title = info.get("title") or f"Link {index}"
                self.item_state.emit(index, "Completed")
                self.success_count += 1
                self.succeeded.emit(index, title)
                self.log.emit(f"[{index}/{total}] Completed: {title}")
            except Exception as exc:
                if self.stop_requested:
                    self.log.emit(f"[{index}/{total}] Stopped by user.")
                    break
                self.item_state.emit(index, "Failed")
                self.failed_count += 1
                self.failed.emit(index, url, str(exc))
                self.log.emit(f"[{index}/{total}] FAILED: {exc}")
        self.finished_batch.emit(self.stop_requested, self.success_count, self.failed_count)


class StatTile(QFrame):
    def __init__(self, title, value, accent):
        super().__init__()
        self.setObjectName("StatTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 9, 13, 9)
        layout.setSpacing(2)
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"color:{accent};font-size:19px;font-weight:800;")
        caption = QLabel(title.upper())
        caption.setObjectName("Muted")
        caption.setStyleSheet("font-size:9px;font-weight:700;letter-spacing:1px;")
        layout.addWidget(self.value_label)
        layout.addWidget(caption)

    def set_value(self, value):
        self.value_label.setText(str(value))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  •  v{APP_VERSION}")
        self.resize(1440, 960)
        self.setMinimumSize(1080, 780)
        self.worker = None
        self.queue_rows = {}
        self.db = sqlite3.connect(str(history_db_path()))
        self.db.execute("""CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
            status TEXT NOT NULL, output_dir TEXT, mode TEXT
        )""")
        self.db.commit()
        self.total = self.processed = self.completed = self.failed_count = 0
        self._apply_styles()
        self._build()
        icon_path = bundle_root() / "assets" / "app_icon.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.load_history()
        self.toast_label = QLabel(self.centralWidget())
        self.toast_label.setAlignment(Qt.AlignCenter)
        self.toast_label.setWordWrap(True)
        self.toast_label.hide()

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QWidget {{ background:{C['bg']}; color:{C['text']}; font-family:'Segoe UI'; font-size:10pt; }}
            QLabel {{ background:transparent; border:0; }}
            QFrame#Sidebar {{ background:{C['sidebar']}; border-right:1px solid {C['border']}; }}
            QFrame#Panel, QFrame#StatTile {{ background:{C['panel']}; border:1px solid {C['border']}; border-radius:13px; }}
            QFrame#Hero {{ background:{C['panel']}; border:1px solid {C['border']}; border-radius:14px; }}
            QFrame#ActionFooter {{ background:{C['sidebar']}; border-top:1px solid {C['border']}; }}
            QLabel#Brand {{ font-size:19px; font-weight:800; }}
            QLabel#BrandRed {{ color:{C['red']}; font-size:20px; font-weight:800; }}
            QLabel#PageTitle {{ font-size:27px; font-weight:800; }}
            QLabel#Subtitle {{ color:{C['muted']}; font-size:11px; }}
            QLabel#SectionTitle {{ font-size:15px; font-weight:750; }}
            QLabel#Muted {{ color:{C['muted']}; font-size:10px; }}
            QLabel#PercentRed {{ color:{C['red']}; font-size:17px; font-weight:800; }}
            QLabel#PercentBlue {{ color:#4DA0FF; font-size:17px; font-weight:800; }}
            QLineEdit, QTextEdit, QComboBox {{
                background:{C['input']}; border:1px solid {C['border']}; border-radius:8px;
                padding:9px 11px; selection-background-color:{C['blue']};
            }}
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border:1px solid {C['blue']}; }}
            QComboBox::drop-down {{ border:0; width:25px; }}
            QPushButton {{ background:{C['panel2']}; border:1px solid {C['border']}; border-radius:8px; padding:10px 15px; font-weight:700; }}
            QPushButton:hover {{ background:#1B3558; }}
            QPushButton:disabled {{ color:#66758B; background:#152239; }}
            QPushButton#Primary {{ background:{C['blue']}; border:0; color:white; padding:13px 22px; font-size:12px; }}
            QPushButton#Primary:hover {{ background:{C['blue_hover']}; }}
            QPushButton#Danger {{ background:{C['red']}; border:0; color:white; padding:13px 22px; font-size:12px; }}
            QPushButton#Danger:hover {{ background:{C['red_hover']}; }}
            QPushButton#Nav {{ text-align:left; padding:13px 14px; background:transparent; border:0; color:{C['muted']}; }}
            QPushButton#Nav:checked {{ background:{C['blue_soft']}; color:white; border-left:3px solid #55A1FF; }}
            QPushButton#Choice {{ padding:13px; font-size:13px; }}
            QPushButton#Choice:checked {{ background:#0C4E9E; border:2px solid #1685FF; color:white; }}
            QProgressBar {{ background:#1C3152; border:0; border-radius:6px; height:13px; text-align:center; }}
            QProgressBar::chunk {{ background:{C['blue']}; border-radius:6px; }}
            QProgressBar#CurrentBar::chunk {{ background:{C['red']}; border-radius:6px; }}
            QTableWidget {{ background:{C['input']}; alternate-background-color:{C['panel2']}; gridline-color:{C['border']}; border:1px solid {C['border']}; border-radius:8px; }}
            QHeaderView::section {{ background:{C['panel2']}; color:{C['muted']}; padding:8px; border:0; font-weight:700; }}
            QScrollArea {{ border:0; background:transparent; }}
            QScrollBar:vertical {{ background:{C['bg']}; width:10px; }}
            QScrollBar::handle:vertical {{ background:{C['border']}; min-height:24px; border-radius:5px; }}
        """)

    def panel(self, title=None, subtitle=None):
        frame = QFrame()
        frame.setObjectName("Panel")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(17, 14, 17, 14)
        lay.setSpacing(9)
        if title:
            label = QLabel(title)
            label.setObjectName("SectionTitle")
            lay.addWidget(label)
        if subtitle:
            label = QLabel(subtitle)
            label.setObjectName("Muted")
            lay.addWidget(label)
        return frame, lay

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(245)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(16, 26, 16, 18)
        sl.setSpacing(8)

        brand_row = QHBoxLayout()
        logo = QLabel("▶")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(52, 52)
        logo.setStyleSheet(f"background:{C['red']};border-radius:12px;font-size:25px;font-weight:900;color:white;")
        brand_text = QVBoxLayout()
        b1 = QLabel("YouTube Media")
        b1.setObjectName("Brand")
        b2 = QLabel("Downloader")
        b2.setObjectName("BrandRed")
        brand_text.addWidget(b1)
        brand_text.addWidget(b2)
        brand_row.addWidget(logo)
        brand_row.addLayout(brand_text)
        brand_row.addStretch()
        sl.addLayout(brand_row)
        sl.addSpacing(35)

        self.nav_buttons = []
        for label in ("↓   Downloader", "☷   Download Queue", "◷   History", "⚙   Settings", "ⓘ   About"):
            btn = QPushButton(label)
            btn.setObjectName("Nav")
            btn.setCheckable(True)
            sl.addWidget(btn)
            self.nav_buttons.append(btn)
        self.nav_buttons[0].setChecked(True)
        self.nav_buttons[0].clicked.connect(lambda: self.pages.setCurrentIndex(0))
        self.nav_buttons[1].clicked.connect(lambda: self.pages.setCurrentIndex(1))
        self.nav_buttons[2].clicked.connect(lambda: self.pages.setCurrentIndex(2))
        self.nav_buttons[3].clicked.connect(lambda: self.pages.setCurrentIndex(3))
        self.nav_buttons[4].clicked.connect(lambda: self.pages.setCurrentIndex(4))
        for i, btn in enumerate(self.nav_buttons):
            btn.clicked.connect(lambda checked=False, idx=i: self._select_nav(idx))
        sl.addStretch(1)
        version = QLabel(f"v{APP_VERSION}")
        version.setStyleSheet(f"background:{C['blue_soft']};color:#8CC5FF;border-radius:8px;padding:7px 12px;font-weight:700;")
        version.setFixedWidth(75)
        sl.addWidget(version)
        root.addWidget(sidebar)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.pages.addWidget(self._downloader_page())
        self.pages.addWidget(self._queue_page())
        self.pages.addWidget(self._history_page())
        self.pages.addWidget(self._settings_page())
        self.pages.addWidget(self._about_page())

    def _select_nav(self, index):
        self.pages.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)

    def _downloader_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(15)

        hero = QFrame()
        hero.setObjectName("Hero")
        hl = QHBoxLayout(hero)
        hl.setContentsMargins(22, 17, 22, 17)
        title_box = QVBoxLayout()
        title = QLabel("Download YouTube Media")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Download audio or video from YouTube URLs in high quality.")
        subtitle.setObjectName("Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        hl.addLayout(title_box)
        hl.addStretch()
        self.ready_badge = QLabel("●  READY")
        self.ready_badge.setStyleSheet(f"background:#123A34;color:{C['green']};border-radius:9px;padding:9px 13px;font-weight:800;")
        hl.addWidget(self.ready_badge, alignment=Qt.AlignTop)
        layout.addWidget(hero)

        # URL entry: deliberately tall for large batches of pasted links.
        url_panel, ul = self.panel("①  Paste YouTube URLs", "One URL per line · Paste multiple links at once")
        url_row = QHBoxLayout()
        self.urls = QTextEdit()
        self.urls.setPlaceholderText(
            "Paste YouTube links here — one per line\n\n"
            "https://www.youtube.com/watch?v=...\n"
            "https://youtu.be/...\n\n"
            "You can paste 50, 100 or more links."
        )
        self.urls.setMinimumHeight(235)
        self.urls.setMaximumHeight(310)
        self.urls.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        url_row.addWidget(self.urls, 1)
        paste_btn = QPushButton("▣  Paste")
        paste_btn.setMinimumWidth(100)
        paste_btn.clicked.connect(self.paste_clipboard)
        url_row.addWidget(paste_btn, alignment=Qt.AlignTop)
        ul.addLayout(url_row)
        self.url_count = QLabel("0 URLs detected")
        self.url_count.setObjectName("Muted")
        self.urls.textChanged.connect(self.update_url_count)
        ul.addWidget(self.url_count)
        layout.addWidget(url_panel)

        # Download type and quality side by side.
        options_row = QHBoxLayout()
        type_panel, tl = self.panel("②  Select Download Type")
        choice_row = QHBoxLayout()
        self.audio_choice = QPushButton("♫   Audio\n(Music only)")
        self.audio_choice.setObjectName("Choice")
        self.audio_choice.setCheckable(True)
        self.audio_choice.setChecked(True)
        self.video_choice = QPushButton("▣   Video\n(With image)")
        self.video_choice.setObjectName("Choice")
        self.video_choice.setCheckable(True)
        self.audio_choice.clicked.connect(lambda: self.set_mode("Audio"))
        self.video_choice.clicked.connect(lambda: self.set_mode("Video"))
        choice_row.addWidget(self.audio_choice)
        choice_row.addWidget(self.video_choice)
        tl.addLayout(choice_row)
        options_row.addWidget(type_panel, 1)

        quality_panel, ql = self.panel("③  Format & Quality")
        qgrid = QGridLayout()
        qgrid.setHorizontalSpacing(14)
        qgrid.setVerticalSpacing(6)
        self.audio_format = QComboBox()
        self.audio_format.addItems(["MP3", "M4A", "WAV", "FLAC", "Best available"])
        self.audio_bitrate = QComboBox()
        self.audio_bitrate.addItems(["320 kbps (Best)", "256 kbps", "192 kbps", "128 kbps"])
        self.video_format = QComboBox()
        self.video_format.addItems(["MP4", "MKV"])
        self.video_quality = QComboBox()
        self.video_quality.addItems([
            "Best available", "2160p (4K)", "1440p (2K)", "1080p (Full HD)",
            "720p (HD)", "480p (SD)", "360p"
        ])
        # Keep separate labels so the inactive format group can be hidden completely.
        self.audio_format_label = self.field_label("Audio format")
        self.audio_bitrate_label = self.field_label("Audio bitrate")
        self.video_format_label = self.field_label("Video container")
        self.video_quality_label = self.field_label("Video resolution")

        qgrid.addWidget(self.audio_format_label, 0, 0)
        qgrid.addWidget(self.audio_format, 1, 0)
        qgrid.addWidget(self.audio_bitrate_label, 0, 1)
        qgrid.addWidget(self.audio_bitrate, 1, 1)
        qgrid.addWidget(self.video_format_label, 2, 0)
        qgrid.addWidget(self.video_format, 3, 0)
        qgrid.addWidget(self.video_quality_label, 2, 1)
        qgrid.addWidget(self.video_quality, 3, 1)
        ql.addLayout(qgrid)
        options_row.addWidget(quality_panel, 1)
        self.set_mode("Audio")
        layout.addLayout(options_row)

        # Destination row
        dest_panel, dl = self.panel()
        dest_row = QHBoxLayout()
        folder_icon = QLabel("▰")
        folder_icon.setStyleSheet("color:#48A1FF;font-size:23px;")
        dest_label = QLabel("Save Location")
        dest_label.setStyleSheet("font-size:14px;font-weight:750;")
        self.output = QLineEdit(str(Path.home() / "Downloads" / "YouTube Downloads"))
        browse = QPushButton("▱  Browse")
        browse.clicked.connect(self.browse_folder)
        dest_row.addWidget(folder_icon)
        dest_row.addWidget(dest_label)
        dest_row.addWidget(self.output, 1)
        dest_row.addWidget(browse)
        dl.addLayout(dest_row)
        layout.addWidget(dest_panel)

        # Progress panel with current file + overall batch.
        progress_panel, pl = self.panel("▥  Download Progress")
        stats_line = QHBoxLayout()
        self.total_label = QLabel("Total: 0")
        self.completed_label = QLabel("Completed: 0")
        self.completed_label.setStyleSheet(f"color:{C['green']};font-weight:700;")
        self.downloading_label = QLabel("Downloading: 0")
        self.downloading_label.setStyleSheet("color:#43A4FF;font-weight:700;")
        self.remaining_label = QLabel("Remaining: 0")
        for w in (self.total_label, self.completed_label, self.downloading_label, self.remaining_label):
            stats_line.addWidget(w)
        stats_line.addStretch()
        pl.addLayout(stats_line)

        current_row = QHBoxLayout()
        current_row.setSpacing(14)

        self.thumbnail_preview = QLabel("VIDEO\nPREVIEW")
        self.thumbnail_preview.setAlignment(Qt.AlignCenter)
        self.thumbnail_preview.setFixedSize(150, 90)
        self.thumbnail_preview.setStyleSheet(
            f"background:{C['input']}; color:{C['muted']}; "
            f"border:1px solid {C['border']}; border-radius:9px; "
            "font-size:10px; font-weight:700;"
        )
        current_row.addWidget(self.thumbnail_preview, 0, Qt.AlignTop)

        current_info_layout = QVBoxLayout()
        current_info_layout.setSpacing(6)
        current_header = QHBoxLayout()
        self.current_title = QLabel("Waiting for download")
        self.current_title.setStyleSheet("font-weight:750;font-size:13px;")
        self.current_title.setWordWrap(True)
        self.current_pct = QLabel("0%")
        self.current_pct.setObjectName("PercentRed")
        current_header.addWidget(self.current_title, 1)
        current_header.addWidget(self.current_pct)
        current_info_layout.addLayout(current_header)

        self.current_detail = QLabel("File size, speed and ETA will appear here.")
        self.current_detail.setObjectName("Muted")
        current_info_layout.addWidget(self.current_detail)

        self.current_bar = QProgressBar()
        self.current_bar.setObjectName("CurrentBar")
        self.current_bar.setValue(0)
        self.current_bar.setFormat("")
        current_info_layout.addWidget(self.current_bar)
        current_info_layout.addStretch(1)

        current_row.addLayout(current_info_layout, 1)
        pl.addLayout(current_row)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet(f"color:{C['border']};")
        pl.addWidget(separator)
        batch_header = QHBoxLayout()
        batch_label = QLabel("Overall Progress")
        batch_label.setStyleSheet("font-size:13px;font-weight:750;")
        self.batch_count = QLabel("0 / 0 (0%)")
        self.batch_count.setObjectName("PercentBlue")
        batch_header.addWidget(batch_label)
        batch_header.addStretch()
        batch_header.addWidget(self.batch_count)
        pl.addLayout(batch_header)
        self.batch_bar = QProgressBar()
        self.batch_bar.setValue(0)
        self.batch_bar.setFormat("")
        pl.addWidget(self.batch_bar)
        layout.addWidget(progress_panel)

        # Activity log
        log_panel, log_layout = self.panel("Activity Log")
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(95)
        self.log_box.setPlaceholderText("Download activity will appear here…")
        log_layout.addWidget(self.log_box)
        layout.addWidget(log_panel)

        # Keep the content scrollable, but move the main actions into a fixed footer.
        # This ensures Start / Stop remain visible even when the page content is taller
        # than the available screen height.
        layout.addStretch(1)

        scroll.setWidget(page)

        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(scroll, 1)

        footer = QFrame()
        footer.setObjectName("ActionFooter")
        footer.setStyleSheet(
            f"QFrame#ActionFooter {{ background:{C['sidebar']}; "
            f"border-top:1px solid {C['border']}; }}"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 10, 24, 12)
        footer_layout.setSpacing(10)

        self.start_btn = QPushButton("↓   Start Download")
        self.start_btn.setObjectName("Primary")
        self.start_btn.setMinimumHeight(44)
        self.start_btn.clicked.connect(self.start_download)

        self.stop_btn = QPushButton("■   Stop")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.setMinimumHeight(44)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_download)

        self.pause_btn = QPushButton("Ⅱ   Pause")
        self.pause_btn.setMinimumHeight(44)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.toggle_pause)

        self.reset_btn = QPushButton("↻   Reset")
        self.reset_btn.setMinimumHeight(44)
        self.reset_btn.clicked.connect(self.reset_all)

        footer_layout.addWidget(self.start_btn)
        footer_layout.addWidget(self.stop_btn)
        footer_layout.addWidget(self.pause_btn)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.reset_btn)
        wrapper_layout.addWidget(footer)

        return wrapper

    def field_label(self, text):
        label = QLabel(text)
        label.setObjectName("Muted")
        return label

    def set_mode(self, mode):
        audio = mode == "Audio"
        self.audio_choice.setChecked(audio)
        self.video_choice.setChecked(not audio)

        # Show only the controls relevant to the selected download type.
        for widget in (
            self.audio_format_label, self.audio_format,
            self.audio_bitrate_label, self.audio_bitrate,
        ):
            widget.setVisible(audio)

        for widget in (
            self.video_format_label, self.video_format,
            self.video_quality_label, self.video_quality,
        ):
            widget.setVisible(not audio)

    def update_url_count(self):
        count = len([x for x in self.urls.toPlainText().splitlines() if x.strip() and not x.strip().startswith("#")])
        self.url_count.setText(f"{count} URL{'s' if count != 1 else ''} detected")

    def paste_clipboard(self):
        text = QApplication.clipboard().text()
        if text:
            existing = self.urls.toPlainText().strip()
            self.urls.setPlainText((existing + "\n" + text).strip() if existing else text)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose download folder", self.output.text())
        if folder:
            self.output.setText(folder)

    def _reset_progress(self, total):
        self.total = total
        self.processed = self.completed = self.failed_count = 0
        self.current_bar.setValue(0)
        self.current_pct.setText("0%")
        self.current_title.setText("Connecting to first URL…")
        self.current_detail.setText("Waiting for source information.")
        self.batch_bar.setValue(0)
        self.batch_count.setText(f"0 / {total} (0%)")
        self.refresh_stats()

    def refresh_stats(self):
        self.total_label.setText(f"Total: {self.total}")
        self.completed_label.setText(f"Completed: {self.completed}")
        self.downloading_label.setText("Downloading: 1" if self.worker and self.worker.isRunning() else "Downloading: 0")
        self.remaining_label.setText(f"Remaining: {max(0, self.total - self.processed - (1 if self.worker and self.worker.isRunning() else 0))}")

    def start_download(self):
        if yt_dlp is None:
            QMessageBox.critical(self, "Missing yt-dlp", 'Install it with:\npython -m pip install -U "yt-dlp[default]"')
            return
        urls = [line.strip() for line in self.urls.toPlainText().splitlines() if line.strip() and not line.strip().startswith("#")]
        if not urls:
            QMessageBox.warning(self, "No URLs", "Paste at least one URL, one per line.")
            return
        output_dir = os.path.abspath(os.path.expanduser(self.output.text().strip()))
        if not output_dir:
            QMessageBox.warning(self, "Save location required", "Choose a download folder.")
            return
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Folder error", str(exc))
            return

        settings = {
            "mode": "Audio" if self.audio_choice.isChecked() else "Video",
            "audio_format": self.audio_format.currentText(),
            "audio_bitrate": self.audio_bitrate.currentText(),
            "video_format": self.video_format.currentText(),
            "video_quality": self.video_quality.currentText(),
        }
        self._reset_progress(len(urls))
        self.populate_queue(urls)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Ⅱ   Pause")
        self.ready_badge.setText("●  DOWNLOADING")
        self.ready_badge.setStyleSheet(f"background:#3B202B;color:{C['red']};border-radius:9px;padding:9px 13px;font-weight:800;")
        self.worker = DownloadWorker(urls, output_dir, settings, self)
        self.worker.file_started.connect(self.on_file_started)
        self.worker.progress.connect(self.on_progress)
        self.worker.thumbnail_data.connect(self.display_thumbnail_data)
        self.worker.queue_preview.connect(self.on_queue_preview)
        self.worker.item_state.connect(self.on_queue_state)
        self.worker.stage.connect(self.on_stage)
        self.worker.succeeded.connect(self.on_success)
        self.worker.failed.connect(self.on_failed)
        self.worker.log.connect(self.append_log)
        self.worker.finished_batch.connect(self.on_batch_finished)
        self.worker.start()
        self.append_log(f"Started {settings['mode'].lower()} batch · {len(urls)} URLs.")

    def populate_queue(self, urls):
        self.queue_table.setRowCount(0)
        self.queue_rows.clear()
        for index, url in enumerate(urls, 1):
            row = self.queue_table.rowCount()
            self.queue_table.insertRow(row)
            preview = QLabel("PREVIEW")
            preview.setAlignment(Qt.AlignCenter)
            preview.setFixedSize(104, 60)
            preview.setStyleSheet(f"background:{C['input']};color:{C['muted']};border:1px solid {C['border']};border-radius:6px;font-size:8px;")
            self.queue_table.setCellWidget(row, 0, preview)
            self.queue_table.setItem(row, 1, QTableWidgetItem(str(index)))
            self.queue_table.setItem(row, 2, QTableWidgetItem(url))
            self.queue_table.setItem(row, 3, QTableWidgetItem("Waiting"))
            self.queue_rows[index] = {"row": row, "url": url, "title": url, "status": "Waiting"}

    def on_queue_preview(self, index, title, image_data):
        row_data = self.queue_rows.get(index)
        if not row_data:
            return
        row_data["title"] = title
        label = self.queue_table.cellWidget(row_data["row"], 0)
        pix = QPixmap()
        if pix.loadFromData(image_data):
            label.setPixmap(pix.scaled(label.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            label.setText("")
        self.queue_table.item(row_data["row"], 2).setText(title)

    def on_queue_state(self, index, status):
        row_data = self.queue_rows.get(index)
        if row_data:
            row_data["status"] = status
            self.queue_table.item(row_data["row"], 3).setText(status)

    def toggle_pause(self):
        if not self.worker or not self.worker.isRunning():
            return
        if self.worker.pause_requested:
            self.worker.resume()
            self.pause_btn.setText("Ⅱ   Pause")
            self.ready_badge.setText("●  DOWNLOADING")
        else:
            self.worker.pause()
            self.pause_btn.setText("▶   Resume")
            self.ready_badge.setText("●  PAUSED")
            self.append_log("Current item paused. Resume to continue.")

    def save_history(self, url, title, status, output_dir="", mode=""):
        self.db.execute(
            "INSERT INTO downloads(created_at,url,title,status,output_dir,mode) VALUES(?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), url, title, status, output_dir, mode)
        )
        self.db.commit()
        self.load_history()

    def load_history(self):
        if not hasattr(self, "history_table"):
            return
        rows = self.db.execute(
            "SELECT created_at,url,title,status FROM downloads ORDER BY id DESC LIMIT 1000"
        ).fetchall()
        self.history_table.setRowCount(0)
        for created_at, url, title, status in rows:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            for col, value in enumerate((created_at.replace("T", " "), status, title or url)):
                cell = QTableWidgetItem(str(value))
                if col == 1:
                    cell.setForeground(QColor(C["green"] if status == "Completed" else C["red"] if status == "Failed" else C["yellow"]))
                self.history_table.setItem(row, col, cell)

    def stop_download(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.append_log("Stop requested. Current download will be interrupted.")

    def on_file_started(self, index, total, url):
        self.set_thumbnail_placeholder("FETCHING\\nPREVIEW")
        self.current_bar.setValue(0)
        self.current_pct.setText("0%")
        self.current_title.setText(f"Downloading link {index} of {total}")
        self.current_detail.setText("Connecting to YouTube…")
        self.refresh_stats()

    def set_thumbnail_placeholder(self, text="VIDEO\\nPREVIEW"):
        self.thumbnail_preview.setPixmap(QPixmap())
        self.thumbnail_preview.setText(text)

    def display_thumbnail_data(self, image_data):
        """Decode and display thumbnail bytes emitted by the download worker."""
        pixmap = QPixmap()
        if not image_data or not pixmap.loadFromData(image_data):
            self.set_thumbnail_placeholder("PREVIEW\\nUNAVAILABLE")
            self.append_log("Thumbnail data could not be decoded as an image.")
            return

        scaled = pixmap.scaled(
            self.thumbnail_preview.size(),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation
        )
        x = max(0, (scaled.width() - self.thumbnail_preview.width()) // 2)
        y = max(0, (scaled.height() - self.thumbnail_preview.height()) // 2)
        cropped = scaled.copy(
            x, y,
            self.thumbnail_preview.width(),
            self.thumbnail_preview.height()
        )
        self.thumbnail_preview.setText("")
        self.thumbnail_preview.setPixmap(cropped)

    def on_progress(self, data):
        pct = int(data.get("percent", 0))
        self.current_bar.setValue(pct)
        self.current_pct.setText(f"{pct}%")
        self.current_title.setText(data.get("title", "Downloading…"))
        self.current_detail.setText(
            f"{human_bytes(data.get('downloaded'))} / {human_bytes(data.get('total'))}"
            f"    •    {human_bytes(data.get('speed'))}/s"
            f"    •    ETA: {human_time(data.get('eta'))}"
        )

    def on_stage(self, title, stage):
        self.current_title.setText(title)
        self.current_detail.setText(stage)
        self.current_bar.setValue(100)
        self.current_pct.setText("100%")

    def _update_batch(self):
        pct = int(self.processed / max(1, self.total) * 100)
        self.batch_bar.setValue(pct)
        self.batch_count.setText(f"{self.processed} / {self.total} ({pct}%)")
        self.refresh_stats()

    def add_history_row(self, index, status, description):
        if not hasattr(self, "history_table"):
            return
        row = self.history_table.rowCount()
        self.history_table.insertRow(row)
        values = (str(index), status, description)
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if col == 1:
                item.setForeground(QColor(C["green"] if status == "Completed" else C["red"]))
            self.history_table.setItem(row, col, item)

    def on_success(self, index, title):