"""
SourceCraft / 混沌下载器 — 视频下载工具包
"""
import sys, os

# Ensure project root is importable
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import multiprocessing
import importlib.util
from collections import deque
import playwright_fix

# Runtime app directory for both source and PyInstaller builds.
RUNTIME_APP_DIR = os.path.dirname(
    os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__)
)
APP_DISPLAY_NAME = "SourceCraft"
APP_ORGANIZATION_NAME = "SourceCraft"
WINDOWS_APP_USER_MODEL_ID = "SourceCraft.SourceCraft"
APP_ICON_CANDIDATES = ("app_icon.ico", "app_icon.png", "app_icon.svg")


_PLAYWRIGHT_BROWSER_MODULE_CACHE = None


def _load_playwright_browser_module():
    """从源码文件加载 playwright_browser，确保开发态/打包态使用同一逻辑。"""
    global _PLAYWRIGHT_BROWSER_MODULE_CACHE
    if _PLAYWRIGHT_BROWSER_MODULE_CACHE is not None:
        return _PLAYWRIGHT_BROWSER_MODULE_CACHE

    candidates = []
    if getattr(sys, 'frozen', False):
        candidates.extend([
            os.path.join(RUNTIME_APP_DIR, "_internal", "playwright_browser.py"),
            os.path.join(RUNTIME_APP_DIR, "playwright_browser.py"),
        ])
    else:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "playwright_browser.py"))

    for module_path in candidates:
        if not os.path.isfile(module_path):
            continue
        spec = importlib.util.spec_from_file_location("playwright_browser_runtime", module_path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PLAYWRIGHT_BROWSER_MODULE_CACHE = module
        return module

    import playwright_browser as module  # fallback
    _PLAYWRIGHT_BROWSER_MODULE_CACHE = module
    return module


def _set_windows_app_user_model_id():
    """为 Windows 任务栏设置稳定的应用标识，避免显示为默认通用图标。"""
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except Exception:
        pass


# === 【关键修复】stdout/stderr 编码修复 ===
# 中文 Windows 默认 stdout 编码为 GBK，无法输出 \ufffd 等特殊 Unicode 字符，
# 导致 print() 抛出 UnicodeEncodeError 从而中断业务流程（如历史记录保存）。
# 强制将 stdout/stderr 设为 UTF-8 + errors='replace'，确保任何字符都不会崩溃。
try:
    import io as _io
    for _stream_name in ('stdout', 'stderr'):
        _stream = getattr(sys, _stream_name, None)
        if _stream is None:
            setattr(sys, _stream_name, open(os.devnull, 'w', encoding='utf-8', errors='replace'))
        elif hasattr(_stream, 'reconfigure'):
            try:
                _stream.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass
        elif hasattr(_stream, 'encoding') and (_stream.encoding or '').lower() not in ('utf-8', 'utf8'):
            try:
                setattr(sys, _stream_name, _io.TextIOWrapper(
                    _stream.buffer, encoding='utf-8', errors='replace', line_buffering=True
                ))
            except Exception:
                pass
except Exception:
    pass

# Ensure relative executable calls (yt-dlp.exe/ffmpeg.exe/deno.exe) work
# when users launch from shortcuts on a new machine.
if getattr(sys, 'frozen', False):
    try:
        os.chdir(RUNTIME_APP_DIR)
    except Exception:
        pass

# === 【关键】Deno 运行时配置 - yt-dlp 需要 Deno 来解决 YouTube JS 挑战 ===
# 参考: https://github.com/yt-dlp/yt-dlp/wiki/Installation#javascript-runtime
if sys.platform == 'win32':
    # 1. 检查应用程序目录（打包后）
    app_dir = RUNTIME_APP_DIR
    bundled_deno = os.path.join(app_dir, 'deno.exe')
    if os.path.exists(bundled_deno):
        if app_dir not in os.environ.get('PATH', ''):
            os.environ['PATH'] = app_dir + os.pathsep + os.environ.get('PATH', '')
    else:
        # 2. 检查用户目录下的 .deno\bin
        deno_path = os.path.join(os.environ.get('USERPROFILE', ''), '.deno', 'bin')
        if os.path.exists(deno_path) and deno_path not in os.environ.get('PATH', ''):
            os.environ['PATH'] = deno_path + os.pathsep + os.environ.get('PATH', '')


# 注意：QtWebEngine相关环境变量已移除
# 现在使用 Playwright 控制真实 Chrome/Edge 浏览器，不再需要 QtWebEngine

import subprocess
import threading
import json
import re
import time
import ctypes
import glob
import socket
import locale
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QComboBox, QProgressBar, 
                             QFileDialog, QTabWidget, QGroupBox, QFormLayout, QCheckBox,
                             QSpinBox, QDoubleSpinBox, QTextEdit, QMessageBox, QSlider, QRadioButton,
                             QListWidget, QListWidgetItem, QScrollArea, QSizePolicy, QListView,
                             QToolButton, QMenu, QAction, QStyle, QStyleFactory,
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                             QDialog, QGridLayout, QButtonGroup, QFrame, QSplitter)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl, QSize, QSettings, QTimer
from PyQt5.QtGui import QIcon, QDesktopServices, QPalette, QColor, QFont, QPixmap, QPainter
from PyQt5.QtSvg import QSvgRenderer
_playwright_browser_module = _load_playwright_browser_module()
PlaywrightBrowserController = _playwright_browser_module.PlaywrightBrowserController
launch_browser_controller = _playwright_browser_module.launch_browser_controller
import auth_utils
from special_scene_utils import build_wait_for_video_arg, fragment_unavailable_flag
# WebEngine相关导入已移除 - 限制绕过功能已被删除

# === 延迟导入（避免包初始化期间的循环依赖） ===
_LAZY_IMPORTS = {
    'APP_DISPLAY_NAME': ('.constants', 'APP_DISPLAY_NAME'),
    'APP_ORGANIZATION_NAME': ('.constants', 'APP_ORGANIZATION_NAME'),
    'WINDOWS_APP_USER_MODEL_ID': ('.constants', 'WINDOWS_APP_USER_MODEL_ID'),
    'APP_ICON_CANDIDATES': ('.constants', 'APP_ICON_CANDIDATES'),
    'XVideoSelectionDialog': ('.dialogs.x_video_selection', 'XVideoSelectionDialog'),
    'LimitedHeightComboBox': ('.widgets.limited_height_combo', 'LimitedHeightComboBox'),
    'PlaylistRangeDialog': ('.dialogs.playlist_range', 'PlaylistRangeDialog'),
    'FailedVideosDialog': ('.dialogs.failed_videos', 'FailedVideosDialog'),
    'DownloadHistoryManager': ('.history_manager', 'DownloadHistoryManager'),
    'SubtitlePreviewThread': ('.threads.subtitle_preview', 'SubtitlePreviewThread'),
    'DownloadThread': ('.threads.download', 'DownloadThread'),
    'TranscodeThread': ('.threads.transcode', 'TranscodeThread'),
    'AppUpdateCheckThread': ('.threads.app_update_check', 'AppUpdateCheckThread'),
    'UpdateThread': ('.threads.update', 'UpdateThread'),
    'DependencyUpdateThread': ('.threads.dependency_update', 'DependencyUpdateThread'),
    'TranscodeDropWidget': ('.widgets.transcode_drop', 'TranscodeDropWidget'),
    'VideoDownloaderApp': ('.app', 'VideoDownloaderApp'),
    'main': ('.main', 'main'),
    'check_dependencies': ('.main', 'check_dependencies'),
}

import importlib

def __getattr__(name):
    if name in _LAZY_IMPORTS:
        mod_path, attr = _LAZY_IMPORTS[name]
        mod = importlib.import_module(mod_path, __package__)
        value = getattr(mod, attr)
        # Cache in globals for future access
        globals()[name] = value
        return value
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

__all__ = list(_LAZY_IMPORTS.keys())
