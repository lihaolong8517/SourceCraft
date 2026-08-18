from realesrgan_runner import RealEsrganProcessThread, get_realesrgan_execution_plan
"""主应用窗口 - VideoDownloaderApp (SourceCraft 核心 GUI)"""
import sys
import os
import re
import json
import time
import ctypes
import glob
import socket
import locale
import subprocess
import threading
import urllib.request
import urllib.error
import urllib.parse
import ssl
import traceback
import ipaddress
import winsound
from datetime import datetime

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QComboBox, QProgressBar,
                             QFileDialog, QTabWidget, QGroupBox, QFormLayout, QCheckBox,
                             QSpinBox, QDoubleSpinBox, QTextEdit, QMessageBox, QSlider, QRadioButton,
                             QListWidget, QListWidgetItem, QScrollArea, QSizePolicy, QListView,
                             QToolButton, QMenu, QAction, QStyle, QStyleFactory,
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                             QDialog, QGridLayout, QButtonGroup, QFrame, QSplitter,
                             QInputDialog, QGraphicsOpacityEffect)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl, QSize, QSettings, QTimer, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QIcon, QDesktopServices, QPalette, QColor, QFont, QPixmap, QPainter
from PyQt5.QtSvg import QSvgRenderer

# Internal modules
from i18n import t, t_format, set_language, get_language, SUPPORTED_LANGUAGES, language_display_name
from .constants import RUNTIME_APP_DIR, APP_DISPLAY_NAME, APP_ORGANIZATION_NAME, WINDOWS_APP_USER_MODEL_ID, APP_ICON_CANDIDATES
from . import utils
from . import utils_update
from .utils import (
    cleanup_download_title, choose_better_download_title, is_generated_download_title,
    sanitize_output_title_component, build_subtitle_lang_pattern, decode_subprocess_bytes,
    _is_x_platform_url, _load_app_icon, is_supported_realesrgan_media,
    can_run_with_current_or_external_runtime,
)
from .utils_update import (
    DEFAULT_APP_WEBSITE_URL, DEFAULT_APP_UPDATE_METADATA_URL, get_app_version,
)
from .dialogs.x_video_selection import XVideoSelectionDialog
from .widgets.limited_height_combo import LimitedHeightComboBox
from .dialogs.playlist_range import PlaylistRangeDialog
from .dialogs.failed_videos import FailedVideosDialog
from .history_manager import DownloadHistoryManager
from .threads.subtitle_preview import SubtitlePreviewThread
from .threads.download import DownloadThread
from .threads.transcode import TranscodeThread
from .threads.app_update_check import AppUpdateCheckThread
from .threads.update import UpdateThread
from .threads.dependency_update import DependencyUpdateThread
from .widgets.transcode_drop import TranscodeDropWidget

# External modules
import auth_utils
from special_scene_utils import build_wait_for_video_arg, fragment_unavailable_flag
from format_dialog import AdvancedFormatDialog, FormatListDialog
from image_processor import ImageProcessThread, ImageProcessor, check_pillow
from download_sections_utils import (
    split_time_range_tokens, parse_time_token_to_seconds, validate_download_section
)
from realesrgan_runner import RealEsrganProcessThread




# ============ 画质修复功能已移除的兼容占位 ============
# 功能实现已从当前版本移除（main.py --realesrgan-job 直接退出），
# 但历史 UI 代码仍引用这些函数名，提供安全默认值防止崩溃。
def get_realesrgan_runtime_capabilities(*args, **kwargs):
    return {}

def ensure_realesrgan_runtime_dirs(*args, **kwargs):
    return None

def get_realesrgan_missing_dependencies(*args, **kwargs):
    return []

def get_realesrgan_missing_download_resources(*args, **kwargs):
    return []

def download_realesrgan_models(*args, **kwargs):
    return []


# Playwright browser (loaded dynamically from package)
from video_downloader import _load_playwright_browser_module
_playwright_browser_module = _load_playwright_browser_module()
PlaywrightBrowserController = _playwright_browser_module.PlaywrightBrowserController
launch_browser_controller = _playwright_browser_module.launch_browser_controller

# License (lazy import, loaded on demand)
# from license_manager import LicenseManager
# from license_dialogs import LicenseStatusWidget, ActivationDialog, check_license_on_startup


class VideoDownloaderApp(QMainWindow):
    def __init__(self, license_manager=None):
        super().__init__()
        self.setAcceptDrops(True)  # 启用从桌面拖放图片文件
        self.download_thread = None
        self.transcode_thread = None
        self.video_info = None
        self._video_info_url = None
        self.available_formats = []  # 存储可用的格式列表
        self._formats_cache_url = None  # 缓存对应的URL
        self._requested_formats_url = None  # 当前请求的URL（用于忽略过期响应）
        self._pending_download_title = ""
        self._pending_media_capture_download = False
        self._pending_media_capture_format_id = ""
        self._non_blocking_message_boxes = []
        
        # Cookie存储已移除 - 限制绕过功能已被删除
        
        # 初始化下载历史记录管理器
        self.history_manager = DownloadHistoryManager()
        
        # 初始化许可证管理器
        self.license_manager = license_manager
        if self.license_manager is None:
            try:
                from license_manager import LicenseManager
                self.license_manager = LicenseManager()
            except ImportError:
                self.license_manager = None
        
        # 加载设置
        self.settings = QSettings("VideoDownloader", "Settings")
        self.current_theme = self.settings.value("theme", "light")
        
        # 加载界面语言（QSettings 有持久化值才覆盖；否则保留环境变量/默认值）
        try:
            saved_lang = str(self.settings.value("language", "", type=str) or "").strip()
            if saved_lang:
                set_language(saved_lang)
        except Exception:
            pass
        
        # 应用主题
        self.apply_theme(self.current_theme)
        
        self.initUI()
        
        # 启用拖拽功能
        self.setAcceptDrops(True)
        
        # 加载上次保存的输出路径
        self.load_saved_path()
        
        # 初始化新功能控件的状态（默认禁用，因为高级选项默认未启用）
        self.on_advanced_options_toggled(False)
        
        # 启动时自动检查更新 (延迟3秒，避免影响启动速度)
        QTimer.singleShot(3000, self.auto_check_dependencies_on_startup)
        QTimer.singleShot(3500, self.auto_check_app_update_on_startup)
        QTimer.singleShot(0, self._update_media_panel_splitters)
        
        # 周期性授权检查（每 30 分钟检查一次）
        self.license_timer = QTimer(self)
        self.license_timer.timeout.connect(self.periodic_license_check)
        self.license_timer.start(30 * 60 * 1000)

    def _has_active_media_capture_window(self):
        try:
            browser_window = getattr(self, 'browser_window', None)
            media_window = getattr(browser_window, '_media_window', None) if browser_window else None
            return bool(media_window and media_window.isVisible())
        except Exception:
            return False

    def _raise_media_capture_window(self):
        try:
            browser_window = getattr(self, 'browser_window', None)
            media_window = getattr(browser_window, '_media_window', None) if browser_window else None
            if media_window and media_window.isVisible():
                media_window._enforce_topmost()
        except Exception:
            pass

    def _show_non_blocking_message_box(self, icon, title, text, buttons=QMessageBox.Ok, default_button=QMessageBox.NoButton, callback=None):
        browser_window = getattr(self, 'browser_window', None)
        media_window = getattr(browser_window, '_media_window', None) if browser_window else None
        parent = media_window if media_window and media_window.isVisible() else self

        msg_box = QMessageBox(parent)
        msg_box.setIcon(icon)
        msg_box.setWindowTitle(title)
        if "<br>" in text or "<font" in text:
            msg_box.setTextFormat(Qt.RichText)
        msg_box.setText(text)
        msg_box.setStandardButtons(buttons)
        if default_button != QMessageBox.NoButton:
            msg_box.setDefaultButton(default_button)
        msg_box.setWindowModality(Qt.NonModal)
        msg_box.setMinimumWidth(320)
        msg_box.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        msg_box.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        if parent is media_window:
            msg_box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        msg_box.setAttribute(Qt.WA_DeleteOnClose, True)
        self._non_blocking_message_boxes.append(msg_box)

        def _cleanup(_result=None):
            try:
                if msg_box in self._non_blocking_message_boxes:
                    self._non_blocking_message_boxes.remove(msg_box)
            except Exception:
                pass

        msg_box.finished.connect(_cleanup)
        if callback:
            msg_box.finished.connect(callback)

        msg_box.adjustSize()
        msg_box.open()
        self._raise_media_capture_window()
        return msg_box

    def _show_browser_safe_message(self, icon, title, text):
        try:
            url_lower = ""
            if hasattr(self, 'url_input'):
                url_lower = self.url_input.text().strip().lower()
            
            is_target_platform = any(domain in url_lower for domain in [
                'douyin.com', 'iesdouyin.com',
                'kuaishou.com', 'gifshow.com', 'kwai.com',
                'xiaohongshu.com', 'xhslink.com'
            ])
            
            if is_target_platform and icon in (QMessageBox.Critical, QMessageBox.Warning):
                if "<br>" not in text and "\n" in text:
                    text = text.replace("\n", "<br>")
                suffix = "<br><br><font color='#e74c3c'><b>💡 提示：抖音、快手、小红书请尝试左上角媒体捕捉功能下载</b></font>"
                if "媒体捕捉功能下载" not in text:
                    text = text + suffix
        except Exception as e:
            print(f"Error appending platform tip: {e}")

        if self._has_active_media_capture_window():
            self._show_non_blocking_message_box(icon, title, text)
            return None

        msg_box = QMessageBox(self)
        msg_box.setIcon(icon)
        msg_box.setWindowTitle(title)
        if "<br>" in text or "<font" in text:
            msg_box.setTextFormat(Qt.RichText)
        msg_box.setText(text)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        msg_box.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        return msg_box.exec_()

    def _maybe_fix_generated_download_filename(self, file_path, resolved_title):
        """若最终文件名是内部运行键/临时名，则在写历史前修正为真实标题。"""
        try:
            if not file_path or not os.path.isfile(file_path):
                return file_path

            current_name = os.path.basename(file_path)
            current_title = cleanup_download_title(current_name)
            preferred_title = choose_better_download_title(resolved_title)
            if not preferred_title or is_generated_download_title(preferred_title):
                return file_path
            if not is_generated_download_title(current_title):
                return file_path

            safe_title = sanitize_output_title_component(preferred_title)
            if not safe_title:
                return file_path

            directory = os.path.dirname(file_path)
            _, ext = os.path.splitext(current_name)
            fixed_path = os.path.join(directory, f"{safe_title}{ext}")
            if os.path.normcase(fixed_path) == os.path.normcase(file_path):
                return file_path

            if os.path.exists(fixed_path):
                if hasattr(self, 'download_thread') and self.download_thread:
                    fixed_path = self.download_thread._generate_unique_filename(fixed_path)
                else:
                    base, ext = os.path.splitext(fixed_path)
                    counter = 1
                    while os.path.exists(fixed_path):
                        fixed_path = f"{base}_{counter}{ext}"
                        counter += 1

            os.replace(file_path, fixed_path)
            print(t_format("修正最终文件名: {} -> {}", file_path, fixed_path))
            return fixed_path
        except Exception as e:
            print(t_format("修正最终文件名失败: {}", e))
            return file_path

    def _show_browser_safe_question(self, title, text, yes_callback=None, no_callback=None):
        try:
            url_lower = ""
            if hasattr(self, 'url_input'):
                url_lower = self.url_input.text().strip().lower()
            
            is_target_platform = any(domain in url_lower for domain in [
                'douyin.com', 'iesdouyin.com',
                'kuaishou.com', 'gifshow.com', 'kwai.com',
                'xiaohongshu.com', 'xhslink.com'
            ])
            
            if is_target_platform:
                if "<br>" not in text and "\n" in text:
                    text = text.replace("\n", "<br>")
                suffix = "<br><br><font color='#e74c3c'><b>💡 提示：抖音、快手、小红书请尝试左上角媒体捕捉功能下载</b></font>"
                if "媒体捕捉功能下载" not in text:
                    text = text + suffix
        except Exception as e:
            print(f"Error appending platform tip: {e}")

        if self._has_active_media_capture_window():
            def _handle_result(result):
                if result == QMessageBox.Yes:
                    if yes_callback:
                        yes_callback()
                elif no_callback:
                    no_callback()

            self._show_non_blocking_message_box(
                QMessageBox.Question,
                title,
                text,
                buttons=QMessageBox.Yes | QMessageBox.No,
                default_button=QMessageBox.Yes,
                callback=_handle_result,
            )
            return None

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Question)
        msg_box.setWindowTitle(title)
        if "<br>" in text or "<font" in text:
            msg_box.setTextFormat(Qt.RichText)
        msg_box.setText(text)
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.Yes)
        
        reply = msg_box.exec_()
        if reply == QMessageBox.Yes:
            if yes_callback:
                yes_callback()
        elif no_callback:
            no_callback()
        return reply

    def _theme_order(self):
        return [
            "light",
            "dark",
            "business_blue",
            "corporate_gray",
            "finance_green",
            "slate_dark",
            "pastel_blue",
            "pastel_green",
            "pastel_pink",
            "pastel_purple",
            "pastel_red",
            "pastel_orange",
            "pastel_teal",
        ]

    def _normalize_theme_name(self, theme):
        name = str(theme or "").strip().lower()
        aliases = {
            "default": "light",
            "classic": "dark",
            "almond": "pastel_orange",
            "business": "business_blue",
            "commercial": "business_blue",
            "enterprise": "corporate_gray",
            "bank": "finance_green",
        }
        normalized = aliases.get(name, name)
        return normalized if normalized else "light"

    def _with_theme_defaults(self, cfg):
        defaults = {
            "group_border": "#8f8f8f",
            "toolbar_btn_bg": "#ffffff",
            "toolbar_btn_border": "#dcdcdc",
            "toolbar_btn_hover_bg": "#f5f5f5",
            "toolbar_btn_hover_border": "#cccccc",
            "toolbar_btn_press_bg": "#e8e8e8",
            "toolbar_btn_press_border": "#bbbbbb",
            "toolbar_btn_icon": "#555555",
            "status_text": "#2a82da",
            "url_input_bg": "#efe9e7",
            "url_input_text": "#1f1f1f",
            "url_input_border": "rgba(0,0,0,0.08)",
            "url_input_focus_bg": "#fdfcfc",
            "url_input_focus_border": "#8bb5e8",
            "login_btn_bg": "#9C27B0",
            "login_btn_hover_bg": "#7B1FA2",
            "login_btn_text": "#ffffff",
        }
        merged = dict(defaults)
        merged.update(cfg or {})
        return merged

    def _build_common_button_style(self, cfg):
        return f"""
            QPushButton {{
                border: 1px solid {cfg["toolbar_btn_border"]};
                border-radius: 6px;
                background-color: {cfg["toolbar_btn_bg"]};
                padding: 0px;
                margin: 0px;
                outline: none;
            }}
            QPushButton:hover {{
                background-color: {cfg["toolbar_btn_hover_bg"]};
                border-color: {cfg["toolbar_btn_hover_border"]};
            }}
            QPushButton:pressed {{
                background-color: {cfg["toolbar_btn_press_bg"]};
                border-color: {cfg["toolbar_btn_press_border"]};
            }}
            QPushButton:focus {{
                border: 1px solid {cfg["toolbar_btn_border"]};
                background-color: {cfg["toolbar_btn_bg"]};
            }}
            QPushButton:focus:hover {{
                background-color: {cfg["toolbar_btn_hover_bg"]};
                border-color: {cfg["toolbar_btn_hover_border"]};
            }}
            QPushButton:focus:pressed {{
                background-color: {cfg["toolbar_btn_press_bg"]};
                border-color: {cfg["toolbar_btn_press_border"]};
            }}
            QPushButton:disabled {{
                background-color: {cfg["toolbar_btn_bg"]};
                border-color: {cfg["toolbar_btn_border"]};
            }}
            QPushButton::menu-indicator {{
                image: none;
            }}
        """

    def _active_icon_color(self):
        cfg = getattr(self, "_active_theme_cfg", {}) or {}
        return cfg.get("toolbar_btn_icon", "#555555")

    def _apply_runtime_theme_styles(self):
        cfg = getattr(self, "_active_theme_cfg", None)
        if not cfg:
            return

        self.common_btn_style = self._build_common_button_style(cfg)

        if hasattr(self, "status_label"):
            self.status_label.setStyleSheet(
                "QLabel { "
                f"color: {cfg['status_text']}; "
                "padding: 6px 12px; "
                "border: 1px solid transparent; "
                "font-weight: bold; "
                "}"
            )

        if hasattr(self, "url_input"):
            self.url_input.setStyleSheet(
                "QLineEdit { "
                "padding: 8px 16px; "
                f"border: 1px solid {cfg['url_input_border']}; "
                "border-radius: 18px; "
                f"background-color: {cfg['url_input_bg']}; "
                f"color: {cfg['url_input_text']}; "
                "font-size: 14px; "
                "} "
                "QLineEdit:focus { "
                f"background-color: {cfg['url_input_focus_bg']}; "
                f"border: 1px solid {cfg['url_input_focus_border']}; "
                "}"
            )

        if hasattr(self, "login_button"):
            self.login_button.setStyleSheet(f"""
                QPushButton {{
                    background-color: {cfg["login_btn_bg"]};
                    color: {cfg["login_btn_text"]};
                    font-weight: bold;
                    padding: 0 15px;
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: {cfg["login_btn_hover_bg"]};
                }}
            """)

        if hasattr(self, "theme_button"):
            self.theme_button.setStyleSheet(self.common_btn_style)
            if hasattr(self, "svg_palette"):
                self.theme_button.setIcon(self._create_svg_icon(self.svg_palette, self._active_icon_color()))
        if hasattr(self, "help_button"):
            self.help_button.setStyleSheet(self.common_btn_style)
            if hasattr(self, "svg_help"):
                self.help_button.setIcon(self._create_svg_icon(self.svg_help, self._active_icon_color()))
        if hasattr(self, "settings_button"):
            self.settings_button.setStyleSheet(self.common_btn_style)
            self.settings_button.setIcon(self._create_svg_icon(self.svg_settings, self._active_icon_color()))
        if hasattr(self, "update_button"):
            if getattr(self, "is_update_btn_green", False):
                self.set_update_button_green()
            else:
                self.update_button.setStyleSheet(self.common_btn_style)
                self.update_button.setIcon(self._create_svg_icon(self.svg_upgrade, self._active_icon_color()))
        
    def apply_theme(self, theme):
        """应用主题样式"""
        app = QApplication.instance()
        app.setStyle(QStyleFactory.create("Fusion"))
        theme = self._normalize_theme_name(theme)
        
        # 定义主题颜色配置
        themes = {
            "light": {
                "window": QColor(245, 245, 245),
                "window_text": QColor(33, 33, 33),
                "base": Qt.white,
                "alternate_base": QColor(242, 242, 242),
                "text": QColor(33, 33, 33),
                "button": QColor(245, 245, 245),
                "button_text": QColor(33, 33, 33),
                "highlight": QColor(42, 130, 218),
                "highlighted_text": Qt.white,
                "tooltip_bg": "#2a82da",
                "tooltip_text": "#ffffff",
                "tab_pane_border": "#c2c2c2",
                "tab_bg": "#f0f0f0",
                "tab_text": "#333333",
                "tab_selected_bg": "#e0e0e0"
            },
            "dark": {
                "window": QColor(45, 45, 45),
                "window_text": Qt.white,
                "base": QColor(18, 18, 18),
                "alternate_base": QColor(45, 45, 45),
                "text": Qt.white,
                "button": QColor(45, 45, 45),
                "button_text": Qt.white,
                "highlight": QColor(42, 130, 218),
                "highlighted_text": Qt.black,
                "tooltip_bg": "#2a82da",
                "tooltip_text": "#ffffff",
                "tab_pane_border": "#444",
                "tab_bg": "#3a3a3a",
                "tab_text": "white",
                "tab_selected_bg": "#636363"
            },
            "business_blue": {
                "window": QColor(244, 248, 253),
                "window_text": QColor(31, 45, 61),
                "base": Qt.white,
                "alternate_base": QColor(236, 243, 252),
                "text": QColor(31, 45, 61),
                "button": QColor(230, 239, 251),
                "button_text": QColor(31, 45, 61),
                "highlight": QColor(57, 106, 177),
                "highlighted_text": Qt.white,
                "tooltip_bg": "#396ab1",
                "tooltip_text": "#ffffff",
                "tab_pane_border": "#b8cbe3",
                "tab_bg": "#eaf1fb",
                "tab_text": "#1f2d3d",
                "tab_selected_bg": "#c7d8ed",
                "toolbar_btn_icon": "#2b4f81",
                "status_text": "#1f5ea8",
                "url_input_bg": "#f4f8ff",
                "url_input_text": "#1f2d3d",
                "url_input_border": "#9bb8df",
                "url_input_focus_bg": "#ffffff",
                "url_input_focus_border": "#4f7fc1",
                "login_btn_bg": "#2f5da8",
                "login_btn_hover_bg": "#274d8c",
                "login_btn_text": "#ffffff"
            },
            "corporate_gray": {
                "window": QColor(246, 247, 249),
                "window_text": QColor(44, 49, 58),
                "base": Qt.white,
                "alternate_base": QColor(238, 240, 243),
                "text": QColor(44, 49, 58),
                "button": QColor(231, 234, 239),
                "button_text": QColor(44, 49, 58),
                "highlight": QColor(89, 110, 133),
                "highlighted_text": Qt.white,
                "tooltip_bg": "#596e85",
                "tooltip_text": "#ffffff",
                "tab_pane_border": "#b8c0cb",
                "tab_bg": "#eceff4",
                "tab_text": "#2c313a",
                "tab_selected_bg": "#cfd6e0",
                "toolbar_btn_icon": "#4f5b6a",
                "status_text": "#4a5a6a",
                "url_input_bg": "#f7f9fc",
                "url_input_text": "#2c313a",
                "url_input_border": "#b7c0cb",
                "url_input_focus_bg": "#ffffff",
                "url_input_focus_border": "#7a8ea5",
                "login_btn_bg": "#5f6f86",
                "login_btn_hover_bg": "#4e5e74",
                "login_btn_text": "#ffffff"
            },
            "finance_green": {
                "window": QColor(244, 250, 246),
                "window_text": QColor(30, 56, 42),
                "base": Qt.white,
                "alternate_base": QColor(233, 245, 237),
                "text": QColor(30, 56, 42),
                "button": QColor(221, 239, 228),
                "button_text": QColor(30, 56, 42),
                "highlight": QColor(39, 122, 90),
                "highlighted_text": Qt.white,
                "tooltip_bg": "#277a5a",
                "tooltip_text": "#ffffff",
                "tab_pane_border": "#aacdbd",
                "tab_bg": "#e5f3eb",
                "tab_text": "#1e382a",
                "tab_selected_bg": "#bfdfcf",
                "toolbar_btn_icon": "#2d6f56",
                "status_text": "#2d7c5d",
                "url_input_bg": "#f3fbf6",
                "url_input_text": "#1e382a",
                "url_input_border": "#93c5ad",
                "url_input_focus_bg": "#ffffff",
                "url_input_focus_border": "#4d9f7c",
                "login_btn_bg": "#2f8c67",
                "login_btn_hover_bg": "#27795a",
                "login_btn_text": "#ffffff"
            },
            "slate_dark": {
                "window": QColor(34, 39, 46),
                "window_text": QColor(230, 236, 242),
                "base": QColor(27, 32, 38),
                "alternate_base": QColor(39, 45, 53),
                "text": QColor(230, 236, 242),
                "button": QColor(45, 52, 62),
                "button_text": QColor(230, 236, 242),
                "highlight": QColor(78, 166, 255),
                "highlighted_text": Qt.white,
                "tooltip_bg": "#2f7ecf",
                "tooltip_text": "#ffffff",
                "tab_pane_border": "#465061",
                "tab_bg": "#303744",
                "tab_text": "#dce5ee",
                "tab_selected_bg": "#3f495a",
                "toolbar_btn_bg": "#303744",
                "toolbar_btn_border": "#4b5566",
                "toolbar_btn_hover_bg": "#3a4352",
                "toolbar_btn_hover_border": "#667185",
                "toolbar_btn_press_bg": "#28303c",
                "toolbar_btn_press_border": "#5d677a",
                "toolbar_btn_icon": "#dce5ee",
                "status_text": "#88c2ff",
                "url_input_bg": "#2a313d",
                "url_input_text": "#e8eef6",
                "url_input_border": "#4f5d73",
                "url_input_focus_bg": "#323a47",
                "url_input_focus_border": "#77b7ff",
                "group_border": "#5c6678",
                "login_btn_bg": "#4b6b99",
                "login_btn_hover_bg": "#3f5b84",
                "login_btn_text": "#ffffff"
            },
            "pastel_blue": {
                "window": QColor(240, 248, 255),  # AliceBlue
                "window_text": QColor(44, 62, 80),
                "base": Qt.white,
                "alternate_base": QColor(230, 240, 250),
                "text": QColor(44, 62, 80),
                "button": QColor(220, 235, 250),
                "button_text": QColor(44, 62, 80),
                "highlight": QColor(173, 216, 230),
                "highlighted_text": Qt.black,
                "tooltip_bg": "#add8e6",
                "tooltip_text": "#2c3e50",
                "tab_pane_border": "#bcd4e6",
                "tab_bg": "#e6f2ff",
                "tab_text": "#2c3e50",
                "tab_selected_bg": "#bcd4e6"
            },
            "pastel_green": {
                "window": QColor(245, 255, 250),  # MintCream
                "window_text": QColor(46, 64, 46),
                "base": Qt.white,
                "alternate_base": QColor(230, 245, 235),
                "text": QColor(46, 64, 46),
                "button": QColor(220, 240, 225),
                "button_text": QColor(46, 64, 46),
                "highlight": QColor(152, 251, 152),
                "highlighted_text": Qt.black,
                "tooltip_bg": "#98fb98",
                "tooltip_text": "#2e402e",
                "tab_pane_border": "#c1e1c1",
                "tab_bg": "#e8f5e9",
                "tab_text": "#2e402e",
                "tab_selected_bg": "#c1e1c1"
            },
            "pastel_pink": {
                "window": QColor(255, 245, 247),  # LavenderBlush
                "window_text": QColor(80, 44, 50),
                "base": Qt.white,
                "alternate_base": QColor(250, 230, 235),
                "text": QColor(80, 44, 50),
                "button": QColor(250, 220, 230),
                "button_text": QColor(80, 44, 50),
                "highlight": QColor(255, 182, 193),
                "highlighted_text": Qt.black,
                "tooltip_bg": "#ffb6c1",
                "tooltip_text": "#502c32",
                "tab_pane_border": "#f4c2c2",
                "tab_bg": "#fff0f5",
                "tab_text": "#502c32",
                "tab_selected_bg": "#f4c2c2"
            },
            "pastel_purple": {
                "window": QColor(250, 245, 255),  # GhostWhite/Lavender
                "window_text": QColor(60, 44, 80),
                "base": Qt.white,
                "alternate_base": QColor(240, 230, 250),
                "text": QColor(60, 44, 80),
                "button": QColor(235, 220, 250),
                "button_text": QColor(60, 44, 80),
                "highlight": QColor(221, 160, 221),
                "highlighted_text": Qt.black,
                "tooltip_bg": "#dda0dd",
                "tooltip_text": "#3c2c50",
                "tab_pane_border": "#d8bfd8",
                "tab_bg": "#f3e5f5",
                "tab_text": "#3c2c50",
                "tab_selected_bg": "#d8bfd8"
            },
            "pastel_red": {
                "window": QColor(255, 245, 245),
                "window_text": QColor(80, 44, 44),
                "base": Qt.white,
                "alternate_base": QColor(255, 235, 235),
                "text": QColor(80, 44, 44),
                "button": QColor(255, 225, 225),
                "button_text": QColor(80, 44, 44),
                "highlight": QColor(255, 192, 192),
                "highlighted_text": Qt.black,
                "tooltip_bg": "#ffc0c0",
                "tooltip_text": "#502c2c",
                "tab_pane_border": "#ffb3b3",
                "tab_bg": "#ffebee",
                "tab_text": "#502c2c",
                "tab_selected_bg": "#ffb3b3"
            },
            "pastel_orange": {
                "window": QColor(255, 250, 240),
                "window_text": QColor(80, 60, 44),
                "base": Qt.white,
                "alternate_base": QColor(255, 240, 225),
                "text": QColor(80, 60, 44),
                "button": QColor(255, 235, 210),
                "button_text": QColor(80, 60, 44),
                "highlight": QColor(255, 218, 185),
                "highlighted_text": Qt.black,
                "tooltip_bg": "#ffdab9",
                "tooltip_text": "#503c2c",
                "tab_pane_border": "#ffcc99",
                "tab_bg": "#fff3e0",
                "tab_text": "#503c2c",
                "tab_selected_bg": "#ffcc99"
            },
            "pastel_teal": {
                "window": QColor(240, 255, 255),
                "window_text": QColor(44, 80, 80),
                "base": Qt.white,
                "alternate_base": QColor(225, 250, 250),
                "text": QColor(44, 80, 80),
                "button": QColor(210, 245, 245),
                "button_text": QColor(44, 80, 80),
                "highlight": QColor(175, 238, 238),
                "highlighted_text": Qt.black,
                "tooltip_bg": "#afeeee",
                "tooltip_text": "#2c5050",
                "tab_pane_border": "#80cbc4",
                "tab_bg": "#e0f2f1",
                "tab_text": "#2c5050",
                "tab_selected_bg": "#80cbc4"
            }
        }

        if theme not in themes:
            theme = "light"

        if theme in themes:
            cfg = self._with_theme_defaults(themes[theme])
            self._active_theme_cfg = cfg
            palette = QPalette()
            disabled_color = QColor(127, 127, 127)
            disabled_rgba = f"rgba({disabled_color.red()}, {disabled_color.green()}, {disabled_color.blue()}, 170)"
            tooltip_bg = "#fff8dc"
            tooltip_text = "#2c2c2c"
            tooltip_border = "#c7c2a8"
            if theme in ("dark", "slate_dark"):
                tooltip_bg = "#2f3440"
                tooltip_text = "#f1f4f8"
                tooltip_border = "#5e6778"
            
            palette.setColor(QPalette.Window, cfg["window"])
            palette.setColor(QPalette.WindowText, cfg["window_text"])
            palette.setColor(QPalette.Base, cfg["base"])
            palette.setColor(QPalette.AlternateBase, cfg["alternate_base"])
            palette.setColor(QPalette.ToolTipBase, Qt.white)
            palette.setColor(QPalette.ToolTipText, cfg["window_text"])
            palette.setColor(QPalette.Text, cfg["text"])
            palette.setColor(QPalette.Disabled, QPalette.WindowText, disabled_color)
            palette.setColor(QPalette.Disabled, QPalette.Text, disabled_color)
            palette.setColor(QPalette.Button, cfg["button"])
            palette.setColor(QPalette.ButtonText, cfg["button_text"])
            palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled_color)
            palette.setColor(QPalette.BrightText, Qt.red)
            palette.setColor(QPalette.Link, QColor(42, 130, 218))
            palette.setColor(QPalette.Highlight, cfg["highlight"])
            palette.setColor(QPalette.HighlightedText, cfg["highlighted_text"])
            palette.setColor(QPalette.Disabled, QPalette.HighlightedText, disabled_color)
            
            app.setPalette(palette)
            
            app.setStyleSheet(f"""
                QToolTip {{ color: {tooltip_text}; background-color: {tooltip_bg}; border: 1px solid {tooltip_border}; }}
                QGroupBox {{ border: 1px solid {cfg["group_border"]}; border-radius: 5px; margin-top: 1ex; }}
                QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top center; padding: 0 3px; }}
                QGroupBox:disabled {{ color: {disabled_rgba}; }}
                QGroupBox::title:disabled {{ color: {disabled_rgba}; }}
                QTabWidget::pane {{ border: 1px solid {cfg["tab_pane_border"]}; }}
                QTabBar::tab {{ background: {cfg["tab_bg"]}; color: {cfg["tab_text"]}; padding: 5px; }}
                QTabBar::tab:selected {{ background: {cfg["tab_selected_bg"]}; }}
                QTabBar::tab:disabled {{ color: {disabled_rgba}; }}
                QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled {{ color: {disabled_rgba}; }}
            """)
        else:
            # 恢复默认亮色主题
            app.setPalette(app.style().standardPalette())
            self._active_theme_cfg = self._with_theme_defaults({})
            app.setStyleSheet("""
                QToolTip { color: #2c2c2c; background-color: #fff8dc; border: 1px solid #c7c2a8; }
                QGroupBox { border: 1px solid gray; border-radius: 5px; margin-top: 1ex; }
                QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 3px; }
                QTabWidget::pane { border: 1px solid #c2c2c2; }
                QTabBar::tab { background: #f0f0f0; padding: 5px; }
                QTabBar::tab:selected { background: #e0e0e0; }
            """)
        
        # 保存当前主题设置
        self.current_theme = theme
        self.settings.setValue("theme", theme)
        self._apply_runtime_theme_styles()
    
    def toggle_theme(self):
        """循环切换主题 (保留作为快捷键或备用)"""
        themes = self._theme_order()
        current_theme = self._normalize_theme_name(self.current_theme)
        try:
            current_index = themes.index(current_theme)
        except ValueError:
            current_index = 0
            
        next_index = (current_index + 1) % len(themes)
        self.apply_theme(themes[next_index])

    def _make_color_icon(self, color_obj, size=16):
        """创建一个纯色图标用于菜单预览"""
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(color_obj))
        p.setPen(QColor(0, 0, 0, 50))  # 细微的边框
        p.drawRoundedRect(2, 2, size-4, size-4, 3, 3)
        p.end()
        return QIcon(pm)

    def _make_text_icon(self, text, size=16, color="#5f6368"):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setPen(QColor(color))
        painter.setFont(QFont("Segoe UI Emoji", size - 4))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, text)
        painter.end()
        return QIcon(pixmap)

    def _create_svg_icon(self, svg_content, color="#555555", size=24):
        """将 SVG 字符串转换为 QIcon，支持着色"""
        # 替换颜色
        svg_content = svg_content.replace('currentColor', color)
        
        renderer = QSvgRenderer(svg_content.encode('utf-8'))
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        
        icon = QIcon(pixmap)
        # 强制为禁用状态添加相同的图像，防止系统自动变淡产生“差别感”
        icon.addPixmap(pixmap, QIcon.Disabled, QIcon.Off)
        icon.addPixmap(pixmap, QIcon.Disabled, QIcon.On)
        icon.addPixmap(pixmap, QIcon.Active, QIcon.Off)
        icon.addPixmap(pixmap, QIcon.Active, QIcon.On)
        
        return icon
    
    def initUI(self):
        # 设置窗口属性
        self.setWindowTitle(APP_DISPLAY_NAME)
        app_icon = _load_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.setFont(QFont('Segoe UI', 13))
        self.setGeometry(100, 100, 900, 700)
        # 设置最小窗口尺寸，允许用户自由调整宽度和高度
        self.setMinimumSize(800, 600)
        
        # 创建中央部件和主布局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        
        # 创建状态标签 - 预设固定高度和内边距，防止布局跳动
        self.status_label = QLabel(t("就绪"))
        self.status_label.setMinimumHeight(32) # 允许根据内容高度自动扩充
        self.status_label.setWordWrap(True)    # 允许自动换行逻辑
        self.status_label.setStyleSheet(
            "QLabel { "
            "color: #2a82da; "
            "padding: 6px 12px; "
            "border: 1px solid transparent; "
            "font-weight: bold; "
            "}"
        )
        self._last_status = t("就绪")
        
        # 创建顶部工具栏
        toolbar_layout = QHBoxLayout()
        
        # 添加登录按钮
        self.login_button = QPushButton(t("🔑 登录网站"))
        self.login_button.setToolTip(t("打开内置浏览器：播放网页视频时自动捕获下载地址；登录账号以同步您的播放与下载权限。"))
        self.login_button.setFixedHeight(34)
        self.login_button.clicked.connect(self.open_browser)
        self.login_button.setStyleSheet("""
            QPushButton {
                background-color: #9C27B0;
                color: white;
                font-weight: bold;
                padding: 0 15px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
        """)
        toolbar_layout.addWidget(self.login_button)
        
        # 定义极简风格 SVG 图标
        self.svg_palette = """
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.688-1.688h1.937c3.084 0 5.625-2.541 5.625-5.625 0-4.82-4.072-8.75-9.063-8.75z"></path>
          <circle cx="13.5" cy="6.5" r=".5" fill="currentColor"></circle>
          <circle cx="17.5" cy="10.5" r=".5" fill="currentColor"></circle>
          <circle cx="8.5" cy="7.5" r=".5" fill="currentColor"></circle>
          <circle cx="6.5" cy="12.5" r=".5" fill="currentColor"></circle>
        </svg>
        """
        self.svg_help = """
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path>
          <line x1="12" y1="17" x2="12.01" y2="17"></line>
        </svg>
        """
        self.svg_upgrade = """
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <path d="M12 17V7M8 11l4-4 4 4"></path>
        </svg>
        """
        self.svg_settings = """
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3"></circle>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
        </svg>
        """
        self.is_update_btn_green = False
        self.enable_update_notification = True  # 是否启用更新提示

        # 统一按钮样式 - 极致平滑极简风格
        self.common_btn_style = """
            QPushButton {
                border: 1px solid #dcdcdc;
                border-radius: 6px;
                background-color: #ffffff;
                padding: 0px;
                margin: 0px;
                outline: none;
            }
            QPushButton:hover {
                background-color: #f5f5f5;
                border-color: #cccccc;
            }
            QPushButton:pressed {
                background-color: #e8e8e8;
                border-color: #bbbbbb;
            }
            QPushButton:focus {
                border: 1px solid #dcdcdc;
                background-color: #ffffff;
            }
            QPushButton:focus:hover {
                background-color: #f5f5f5;
                border-color: #cccccc;
            }
            QPushButton:focus:pressed {
                background-color: #e8e8e8;
                border-color: #bbbbbb;
            }
            QPushButton:disabled {
                background-color: #ffffff;
                border-color: #dcdcdc;
            }
            QPushButton::menu-indicator {
                image: none;
            }
        """
        btn_size = QSize(34, 34)
        icon_size = QSize(20, 20)

        # 主题切换按钮
        self.theme_button = QPushButton()
        self.theme_button.setToolTip(t("选择主题颜色"))
        self.theme_button.setFixedSize(btn_size)
        self.theme_button.setIcon(self._create_svg_icon(self.svg_palette, self._active_icon_color()))
        self.theme_button.setIconSize(icon_size)
        self.theme_button.setStyleSheet(self.common_btn_style)
        
        # 创建主题选择菜单
        theme_menu = QMenu(self)
        # ... (主题菜单项保持不变)
        action_light = theme_menu.addAction(t("默认明亮"), lambda: self.apply_theme("light"))
        action_light.setIcon(self._make_color_icon("#f0f0f0"))
        action_dark = theme_menu.addAction(t("经典暗黑"), lambda: self.apply_theme("dark"))
        action_dark.setIcon(self._make_color_icon("#2d2d2d"))
        theme_menu.addSeparator()
        action_business_blue = theme_menu.addAction(t("商务蓝"), lambda: self.apply_theme("business_blue"))
        action_business_blue.setIcon(self._make_color_icon("#396ab1"))
        action_corporate_gray = theme_menu.addAction(t("企业灰"), lambda: self.apply_theme("corporate_gray"))
        action_corporate_gray.setIcon(self._make_color_icon("#8a95a5"))
        action_finance_green = theme_menu.addAction(t("金融绿"), lambda: self.apply_theme("finance_green"))
        action_finance_green.setIcon(self._make_color_icon("#277a5a"))
        action_slate_dark = theme_menu.addAction(t("石墨深色"), lambda: self.apply_theme("slate_dark"))
        action_slate_dark.setIcon(self._make_color_icon("#323a47"))
        theme_menu.addSeparator()
        action_blue = theme_menu.addAction(t("淡雅蓝"), lambda: self.apply_theme("pastel_blue"))
        action_blue.setIcon(self._make_color_icon("#add8e6"))
        action_green = theme_menu.addAction(t("淡雅绿"), lambda: self.apply_theme("pastel_green"))
        action_green.setIcon(self._make_color_icon("#98fb98"))
        action_pink = theme_menu.addAction(t("淡雅粉"), lambda: self.apply_theme("pastel_pink"))
        action_pink.setIcon(self._make_color_icon("#ffb6c1"))
        action_purple = theme_menu.addAction(t("淡雅紫"), lambda: self.apply_theme("pastel_purple"))
        action_purple.setIcon(self._make_color_icon("#dda0dd"))
        action_red = theme_menu.addAction(t("淡雅红"), lambda: self.apply_theme("pastel_red"))
        action_red.setIcon(self._make_color_icon("#ffc0c0"))
        action_orange = theme_menu.addAction(t("淡雅橙"), lambda: self.apply_theme("pastel_orange"))
        action_orange.setIcon(self._make_color_icon("#ffdab9"))
        action_teal = theme_menu.addAction(t("淡雅青"), lambda: self.apply_theme("pastel_teal"))
        action_teal.setIcon(self._make_color_icon("#afeeee"))
        
        self.theme_button.setMenu(theme_menu)
        toolbar_layout.addWidget(self.theme_button)
        
        # 添加帮助按钮
        self.help_button = QPushButton()
        self.help_button.setToolTip(t("联系开发者"))
        self.help_button.setFixedSize(btn_size)
        self.help_button.setIcon(self._create_svg_icon(self.svg_help, self._active_icon_color()))
        self.help_button.setIconSize(icon_size)
        self.help_button.setStyleSheet(self.common_btn_style)
        self.help_button.clicked.connect(lambda: QMessageBox.information(
            self, 
            t("联系开发者"), 
            t("如有问题或建议，请联系开发者：\n\n") + t("📧 邮箱：haolongli84@gmail.com\n\n") + t("💬 QQ：378952277\n\n") + t("感谢您的使用与支持！")
        ))
        toolbar_layout.addWidget(self.help_button)
        
        # 添加软件更新按钮（检查应用程序自身更新）
        self.update_button = QPushButton()
        self.update_button.setToolTip(t("检查软件更新"))
        self.update_button.setFixedSize(btn_size)
        self.update_button.setIcon(self._create_svg_icon(self.svg_upgrade, self._active_icon_color()))
        self.update_button.setIconSize(icon_size)
        self.update_button.setStyleSheet(self.common_btn_style)
        self.update_button.clicked.connect(self.check_app_update_from_toolbar)
        toolbar_layout.addWidget(self.update_button)
        
        # 添加设置按钮
        self.settings_button = QPushButton()
        self.settings_button.setToolTip(t("应用设置"))
        self.settings_button.setFixedSize(btn_size)
        self.settings_button.setIcon(self._create_svg_icon(self.svg_settings, self._active_icon_color()))
        self.settings_button.setIconSize(icon_size)
        self.settings_button.setStyleSheet(self.common_btn_style)
        self.settings_button.clicked.connect(self.show_settings_dialog)
        toolbar_layout.addWidget(self.settings_button)
        
        # 添加许可证状态显示控件
        if self.license_manager:
            try:
                from license_dialogs import LicenseStatusWidget, ActivationDialog
                self.license_status_widget = LicenseStatusWidget(self.license_manager)
                self.license_status_widget.clicked.connect(self._show_license_dialog)
                toolbar_layout.addWidget(self.license_status_widget)
            except ImportError:
                pass
        
        toolbar_layout.addStretch()
        self.main_layout.addLayout(toolbar_layout)
        
        # 创建选项卡
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)
        
        # 下载选项卡
        download_tab = QWidget()
        self.tabs.addTab(download_tab, t("下载"))
        download_layout = QVBoxLayout(download_tab)
        
        # URL输入区域
        url_group = QGroupBox(t("视频URL"))
        url_layout = QHBoxLayout()
        url_group.setLayout(url_layout)
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(t("输入视频URL..."))
        self.url_input.setMinimumHeight(36)
        self.url_input.setFont(QFont('Segoe UI', 13))
        self.url_input.setStyleSheet("QLineEdit { padding: 8px 16px; border: 1px solid rgba(0,0,0,0.08); border-radius: 18px; background-color: #efe9e7; color: #1f1f1f; font-size: 14px; } QLineEdit:focus { background-color: #fdfcfc; border: 1px solid #8bb5e8; }")
        
        self.url_input.textChanged.connect(self._on_url_text_changed)
        url_layout.addWidget(self.url_input)
        
        paste_button = QPushButton(t("粘贴"))
        paste_button.clicked.connect(self.paste_url)
        url_layout.addWidget(paste_button)
        
        info_button = QPushButton(t("获取信息"))
        info_button.clicked.connect(self.get_video_info)
        url_layout.addWidget(info_button)
        
        download_layout.addWidget(url_group)
        
        # 下载选项区域
        options_group = QGroupBox(t("下载选项"))
        options_layout = QFormLayout()
        options_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        options_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        options_group.setLayout(options_layout)
        
        # 格式选择
        format_layout = QHBoxLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItems([
            t("最佳质量"), 
            "4K",
            "2K",
            "1080p",
            "720p", 
            "480p", 
            t("仅音频 (mp3)"), 
            t("仅音频 (m4a)"),
            t("仅缩略图"),
            t("自定义"),
            t("指定格式ID")
        ])
        # 添加工具提示，说明格式选择
        self.format_combo.setToolTip(t("选择下载格式。'最佳质量'选项会自动选择最佳视频和音频质量。'仅缩略图'选项只下载视频封面图片，不下载视频文件。"))
        self.format_combo.currentIndexChanged.connect(self.update_format_options)
        format_layout.addWidget(self.format_combo)
        
        # 添加高级格式选择按钮（集成了获取格式列表功能）
        self.advanced_format_button = QPushButton(t("🛠️ 高级格式选择"))
        self.advanced_format_button.setToolTip(t("自动获取可用格式并支持复杂过滤和排序"))
        self.advanced_format_button.clicked.connect(self.open_advanced_format_dialog)
        format_layout.addWidget(self.advanced_format_button)
        
        options_layout.addRow(t("格式:"), format_layout)
        
        # 自定义格式
        self.custom_format = QLineEdit()
        self.custom_format.setPlaceholderText(t("例如: bestvideo[height<=1080]+bestaudio[acodec!=opus]/best[acodec!=opus]"))
        self.custom_format.setEnabled(False)
        options_layout.addRow(t("自定义格式:"), self.custom_format)
        
        # 格式ID选择
        self.format_id_combo = QComboBox()
        self.format_id_combo.setEnabled(False)
        self.format_id_combo.setPlaceholderText(t("选择格式ID"))
        # 设置最大可见项数，超过此数量时会显示滚动条
        self.format_id_combo.setMaxVisibleItems(15)
        # 设置下拉列表的样式，确保有足够的宽度显示完整内容
        self.format_id_combo.setStyleSheet("""
            QComboBox {
                min-width: 400px;
            }
            QComboBox QAbstractItemView {
                min-width: 400px;
                selection-background-color: #3daee9;
            }
        """)
        options_layout.addRow(t("格式ID:"), self.format_id_combo)
        
        # 音频语言选择
        audio_lang_layout = QHBoxLayout()
        self.audio_lang_combo = QComboBox()
        self.audio_lang_combo.addItems([
            t("自动选择 (推荐)"),
            t("英语 (en)"), 
            t("中文 (zh)"),
            t("日语 (ja)"),
            t("韩语 (ko)"),
            t("法语 (fr)"),
            t("德语 (de)"),
            t("西班牙语 (es)"),
            t("俄语 (ru)"),
            t("意大利语 (it)"),
            t("葡萄牙语 (pt)"),
            t("阿拉伯语 (ar)"),
            t("印地语 (hi)"),
            t("泰语 (th)"),
            t("越南语 (vi)"),
            t("自定义语言代码")
        ])
        self.audio_lang_combo.setToolTip(t("选择音频语言。'自动选择'会优先选择英语或视频的默认语言。"))
        audio_lang_layout.addWidget(self.audio_lang_combo)
        
        # 自定义语言代码输入框
        self.custom_audio_lang = QLineEdit()
        self.custom_audio_lang.setPlaceholderText(t("例如: en-US, zh-CN"))
        self.custom_audio_lang.setEnabled(False)
        self.custom_audio_lang.setMaximumWidth(120)
        audio_lang_layout.addWidget(self.custom_audio_lang)
        
        # 连接信号
        self.audio_lang_combo.currentIndexChanged.connect(self.update_audio_lang_options)
        
        options_layout.addRow(t("音频语言:"), audio_lang_layout)
        
        # 输出路径
        self.output_path = QLineEdit()
        # 默认路径设置在load_saved_path方法中，不在这里设置
        browse_button = QPushButton(t("浏览..."))
        browse_button.clicked.connect(self.browse_output_path)
        output_layout = QHBoxLayout()
        output_layout.addWidget(self.output_path)
        output_layout.addWidget(browse_button)
        options_layout.addRow(t("保存位置:"), output_layout)
        
        # 创建高级选项的可折叠区域
        self.advanced_group = QGroupBox(t("高级选项"))
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(False)
        
        # 连接高级选项状态变化信号，用于控制新功能的启用/禁用
        self.advanced_group.toggled.connect(self.on_advanced_options_toggled)
        
        # 设置高级选项组的大小策略和高度
        self.advanced_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # 移除固定最小高度，允许动态调整
        # 最大高度会在窗口大小改变时动态调整
        
        advanced_layout = QVBoxLayout()
        self.advanced_group.setLayout(advanced_layout)
        
        # 创建选项卡用于不同类别的高级选项
        advanced_tabs = QTabWidget()
        # 设置选项卡的大小策略，允许更好的响应式调整
        advanced_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        advanced_layout.addWidget(advanced_tabs)
        
        # 字幕选项卡
        subtitle_tab = QWidget()
        advanced_tabs.addTab(subtitle_tab, t("字幕"))
        
        # 创建滚动区域以容纳字幕选项
        subtitle_scroll = QScrollArea(subtitle_tab)
        subtitle_scroll.setWidgetResizable(True)
        subtitle_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        subtitle_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 创建滚动内容容器
        subtitle_content = QWidget()
        subtitle_layout = QVBoxLayout(subtitle_content)
        
        # 设置选项卡的主布局
        subtitle_tab_layout = QVBoxLayout(subtitle_tab)
        subtitle_tab_layout.addWidget(subtitle_scroll)
        subtitle_scroll.setWidget(subtitle_content)
        
        # 添加字幕功能使用说明
        subtitle_usage_label = QLabel(
            t("<b>🎬 字幕功能使用说明：</b><br>") + t("• <b>基础字幕下载：</b>勾选'下载手动字幕'获取视频原始字幕文件<br>") + t("• <b>自动字幕：</b>勾选'下载自动生成字幕'获取平台AI生成的字幕<br>") + t("• <b>语言选择：</b>选择需要的字幕语言，支持中文、英文等多种语言<br>") + t("• <b>格式转换：</b>推荐使用SRT格式，兼容性最好<br>") + t("• <b>编码转换：</b>勾选'转换字幕为SRT+UTF-8'确保中文字符正常显示")
        )
        subtitle_usage_label.setWordWrap(True)
        subtitle_usage_label.setStyleSheet(
            "QLabel { "
            "background-color: #e8f4fd; "
            "border: 1px solid #bee5eb; "
            "border-radius: 5px; "
            "padding: 10px; "
            "margin: 5px; "
            "}"
        )
        subtitle_layout.addWidget(subtitle_usage_label)
        
        # 字幕下载和配置选项组 - 合并为一个蓝色边框组
        subtitle_download_config_group = QGroupBox(t("字幕下载与配置"))
        # 设置组框样式为蓝色边框
        subtitle_download_config_group.setStyleSheet(
            "QGroupBox { "
            "font-weight: bold; "
            "font-size: 13px; "
            "margin-top: 15px; "
            "padding-top: 15px; "
            "border: 2px solid #4a90e2; "  # 蓝色边框
            "border-radius: 5px; "
            "} "
            "QGroupBox::title { "
            "subcontrol-origin: margin; "
            "subcontrol-position: top center; "
            "padding: 0 10px; "
            "background-color: white; "
            "color: #4a90e2; "  # 蓝色标题
            "}"
        )
        subtitle_download_config_layout = QVBoxLayout(subtitle_download_config_group)
        
        # 字幕列表预览按钮
        subtitle_preview_layout = QHBoxLayout()
        self.list_subs_button = QPushButton(t("🔍 预览可用字幕"))
        self.list_subs_button.setToolTip(t("预览视频的所有可用字幕语言和格式（不下载视频）"))
        self.list_subs_button.setStyleSheet(
            "QPushButton { "
            "background-color: #17a2b8; "
            "color: white; "
            "border: none; "
            "padding: 8px 16px; "
            "border-radius: 4px; "
            "font-weight: bold; "
            "} "
            "QPushButton:hover { "
            "background-color: #138496; "
            "} "
            "QPushButton:pressed { "
            "background-color: #0f6674; "
            "}"
        )
        self.list_subs_button.clicked.connect(self.preview_subtitles)
        subtitle_preview_layout.addWidget(self.list_subs_button)
        subtitle_preview_layout.addStretch()
        subtitle_download_config_layout.addLayout(subtitle_preview_layout)
        
        # 基础字幕选项
        self.subtitle_check = QCheckBox(t("下载手动字幕"))
        self.subtitle_check.setToolTip(t("下载视频的手动字幕文件"))
        subtitle_download_config_layout.addWidget(self.subtitle_check)
        
        # 自动字幕选项
        self.auto_subtitle_check = QCheckBox(t("下载自动生成字幕"))
        self.auto_subtitle_check.setToolTip(t("下载平台自动生成的字幕（如YouTube自动字幕、Bilibili AI字幕等）"))
        subtitle_download_config_layout.addWidget(self.auto_subtitle_check)
        
        # 字幕语言选择
        subtitle_lang_layout = QHBoxLayout()
        lang_label = QLabel(t("字幕语言:"))
        lang_label.setMinimumWidth(80)
        lang_label.setStyleSheet("QLabel { font-weight: bold; }")
        subtitle_lang_layout.addWidget(lang_label)
        self.subtitle_lang = LimitedHeightComboBox(max_visible_items=12)
        self.subtitle_lang.addItem(t("全部语言"), "all")
        # 东亚语言
        self.subtitle_lang.addItem(t("中文简体 (zh-CN)"), "zh-CN")
        self.subtitle_lang.addItem(t("中文繁体 (zh-TW)"), "zh-TW")
        self.subtitle_lang.addItem(t("中文香港 (zh-HK)"), "zh-HK")
        self.subtitle_lang.addItem(t("日语 (ja)"), "ja")
        self.subtitle_lang.addItem(t("韩语 (ko)"), "ko")
        # 英语变体
        self.subtitle_lang.addItem(t("英语 (en)"), "en")
        self.subtitle_lang.addItem(t("英语美式 (en-US)"), "en-US")
        self.subtitle_lang.addItem(t("英语英式 (en-GB)"), "en-GB")
        # 东南亚语言
        self.subtitle_lang.addItem(t("印度尼西亚语 (id)"), "id")
        self.subtitle_lang.addItem(t("马来语 (ms)"), "ms")
        self.subtitle_lang.addItem(t("泰语 (th)"), "th")
        self.subtitle_lang.addItem(t("越南语 (vi)"), "vi")
        self.subtitle_lang.addItem(t("菲律宾语 (fil)"), "fil")
        self.subtitle_lang.addItem(t("缅甸语 (my)"), "my")
        # 南亚语言
        self.subtitle_lang.addItem(t("印地语 (hi)"), "hi")
        self.subtitle_lang.addItem(t("孟加拉语 (bn)"), "bn")
        self.subtitle_lang.addItem(t("泰米尔语 (ta)"), "ta")
        self.subtitle_lang.addItem(t("泰卢固语 (te)"), "te")
        self.subtitle_lang.addItem(t("乌尔都语 (ur)"), "ur")
        # 西欧语言
        self.subtitle_lang.addItem(t("法语 (fr)"), "fr")
        self.subtitle_lang.addItem(t("德语 (de)"), "de")
        self.subtitle_lang.addItem(t("西班牙语 (es)"), "es")
        self.subtitle_lang.addItem(t("葡萄牙语 (pt)"), "pt")
        self.subtitle_lang.addItem(t("葡萄牙语巴西 (pt-BR)"), "pt-BR")
        self.subtitle_lang.addItem(t("意大利语 (it)"), "it")
        self.subtitle_lang.addItem(t("荷兰语 (nl)"), "nl")
        # 北欧语言
        self.subtitle_lang.addItem(t("瑞典语 (sv)"), "sv")
        self.subtitle_lang.addItem(t("挪威语 (no)"), "no")
        self.subtitle_lang.addItem(t("丹麦语 (da)"), "da")
        self.subtitle_lang.addItem(t("芬兰语 (fi)"), "fi")
        # 东欧语言
        self.subtitle_lang.addItem(t("俄语 (ru)"), "ru")
        self.subtitle_lang.addItem(t("波兰语 (pl)"), "pl")
        self.subtitle_lang.addItem(t("乌克兰语 (uk)"), "uk")
        self.subtitle_lang.addItem(t("捷克语 (cs)"), "cs")
        self.subtitle_lang.addItem(t("匈牙利语 (hu)"), "hu")
        self.subtitle_lang.addItem(t("罗马尼亚语 (ro)"), "ro")
        # 其他欧洲语言
        self.subtitle_lang.addItem(t("土耳其语 (tr)"), "tr")
        self.subtitle_lang.addItem(t("希腊语 (el)"), "el")
        # 中东语言
        self.subtitle_lang.addItem(t("阿拉伯语 (ar)"), "ar")
        self.subtitle_lang.addItem(t("希伯来语 (he)"), "he")
        self.subtitle_lang.addItem(t("波斯语 (fa)"), "fa")
        # 非洲语言
        self.subtitle_lang.addItem(t("斯瓦希里语 (sw)"), "sw")
        self.subtitle_lang.addItem(t("阿姆哈拉语 (am)"), "am")
        self.subtitle_lang.setEnabled(False)
        self.subtitle_lang.setToolTip(t("选择要下载的字幕语言，'全部语言'将下载所有可用语言的字幕"))
        subtitle_lang_layout.addWidget(self.subtitle_lang)
        subtitle_lang_layout.addStretch()
        subtitle_download_config_layout.addLayout(subtitle_lang_layout)
        
        # 字幕格式选择
        subtitle_format_layout = QHBoxLayout()
        format_label = QLabel(t("字幕格式:"))
        format_label.setMinimumWidth(80)
        format_label.setStyleSheet("QLabel { font-weight: bold; }")
        subtitle_format_layout.addWidget(format_label)
        self.subtitle_format = QComboBox()
        self.subtitle_format.addItem(t("SRT (推荐)"), "srt")
        self.subtitle_format.addItem(t("ASS (高级字幕)"), "ass")
        self.subtitle_format.addItem("VTT (WebVTT)", "vtt")
        self.subtitle_format.addItem("TTML", "ttml")
        self.subtitle_format.addItem(t("最佳格式"), "best")
        self.subtitle_format.addItem(t("格式优先级 (srt/ass/vtt/best)"), "srt/ass/vtt/best")
        self.subtitle_format.addItem(t("格式优先级 (ass/srt/best)"), "ass/srt/best")
        self.subtitle_format.setEnabled(False)
        self.subtitle_format.setToolTip(t("选择字幕文件格式。支持格式优先级，如'srt/ass/vtt/best'会按优先级选择可用格式"))
        subtitle_format_layout.addWidget(self.subtitle_format)
        subtitle_format_layout.addStretch()
        subtitle_download_config_layout.addLayout(subtitle_format_layout)
        
        subtitle_layout.addWidget(subtitle_download_config_group)
        
        # 字幕处理选项组
        subtitle_process_group = QGroupBox(t("字幕处理"))
        # 设置组框样式，确保标题完整显示
        subtitle_process_group.setStyleSheet(
            "QGroupBox { "
            "font-weight: bold; "
            "font-size: 13px; "
            "margin-top: 15px; "
            "padding-top: 15px; "
            "border: 2px solid #bee5eb; "
            "border-radius: 5px; "
            "} "
            "QGroupBox::title { "
            "subcontrol-origin: margin; "
            "subcontrol-position: top center; "
            "padding: 0 10px; "
            "background-color: white; "
            "color: black; "
            "}"
        )
        subtitle_process_layout = QVBoxLayout(subtitle_process_group)
        
        # 字幕嵌入选项 - 使用水平布局将复选框和提示放在同一行
        embed_subtitle_layout = QHBoxLayout()
        self.embed_subtitle_check = QCheckBox(t("嵌入字幕到视频文件"))
        self.embed_subtitle_check.setToolTip(t("将字幕嵌入到视频文件中（需要 ffmpeg）。注意：MP4 对字幕嵌入支持有限，MKV 更稳；建议在高级格式中选择 MKV 或启用重封装为 MKV 提高兼容性。若视频仅有自动字幕，请同时勾选'下载自动生成字幕'。"))
        self.embed_subtitle_check.setEnabled(True)
        embed_subtitle_layout.addWidget(self.embed_subtitle_check)
        
        # 在复选框右侧添加提示标签（单行显示，不换行）
        self.embed_subtitle_notice = QLabel(t("⚠ MP4 对字幕嵌入支持有限，MKV 更稳.建议在'高级格式'功能中选择 MKV"))
        self.embed_subtitle_notice.setWordWrap(False)  # 禁用自动换行
        self.embed_subtitle_notice.setStyleSheet("QLabel { color: #6c757d; font-size: 10px; margin-left: 15px; white-space: nowrap; }")
        embed_subtitle_layout.addWidget(self.embed_subtitle_notice, 0)  # 0 表示不拉伸
        embed_subtitle_layout.addStretch()  # 添加弹性空间，让提示不会占满整行
        
        subtitle_process_layout.addLayout(embed_subtitle_layout)
        
        # 字幕转换选项
        self.convert_subtitle_check = QCheckBox(t("转换字幕为SRT+UTF-8"))
        self.convert_subtitle_check.setToolTip(t("将字幕转换为 SRT 格式并使用 UTF-8 编码，确保中文字符正常显示，推荐开启"))
        self.convert_subtitle_check.setEnabled(False)
        subtitle_process_layout.addWidget(self.convert_subtitle_check)
        
        # 字幕下载延迟设置
        subtitle_delay_layout = QHBoxLayout()
        delay_label = QLabel(t("字幕下载延迟:"))
        delay_label.setMinimumWidth(100)
        delay_label.setStyleSheet("QLabel { font-weight: bold; }")
        subtitle_delay_layout.addWidget(delay_label)
        self.subtitle_delay_spin = QSpinBox()
        self.subtitle_delay_spin.setRange(0, 60)
        self.subtitle_delay_spin.setValue(0)
        self.subtitle_delay_spin.setSuffix(t(" 秒"))
        self.subtitle_delay_spin.setToolTip(t("设置字幕下载之间的延迟时间，避免频繁请求被限制"))
        self.subtitle_delay_spin.setEnabled(False)
        subtitle_delay_layout.addWidget(self.subtitle_delay_spin)
        subtitle_delay_layout.addStretch()
        subtitle_process_layout.addLayout(subtitle_delay_layout)
        
        subtitle_layout.addWidget(subtitle_process_group)
        
        # 设置字幕选项的联动逻辑
        def update_subtitle_options():
            """统一管理字幕相关选项的启用/禁用状态"""
            # 检查是否启用了任何字幕下载功能
            subtitle_enabled = self.subtitle_check.isChecked()
            auto_subtitle_enabled = self.auto_subtitle_check.isChecked()
            any_subtitle_enabled = subtitle_enabled or auto_subtitle_enabled
            
            # 字幕语言和格式选择：只有启用字幕下载时才可用
            self.subtitle_lang.setEnabled(any_subtitle_enabled)
            self.subtitle_format.setEnabled(any_subtitle_enabled)
            
            # 字幕转换：只有启用字幕下载且高级选项启用时才可用
            advanced_enabled = hasattr(self, 'advanced_group') and self.advanced_group.isChecked()
            self.convert_subtitle_check.setEnabled(any_subtitle_enabled and advanced_enabled)
            if not any_subtitle_enabled:
                self.convert_subtitle_check.setChecked(False)
            
            # 字幕嵌入：可以独立使用（yt-dlp会自动下载字幕用于嵌入）
            # 但建议同时启用字幕下载以保留外部文件
            self.embed_subtitle_check.setEnabled(True)
            
            # 字幕下载延迟：当启用任何字幕功能（包括嵌入）时可用
            embed_enabled = self.embed_subtitle_check.isChecked()
            self.subtitle_delay_spin.setEnabled(any_subtitle_enabled or embed_enabled)
            
            # 更新UI提示（单行显示）
            if embed_enabled and not any_subtitle_enabled:
                # 仅启用嵌入时的提示
                self.embed_subtitle_notice.setText(t("ℹ️ 仅嵌入模式：yt-dlp 会自动下载字幕并嵌入后删除外部文件"))
                self.embed_subtitle_notice.setStyleSheet("QLabel { color: #0066cc; font-size: 10px; margin-left: 15px; white-space: nowrap; }")
            elif embed_enabled and any_subtitle_enabled:
                # 同时启用下载和嵌入时的提示
                self.embed_subtitle_notice.setText(t("ℹ️ 下载+嵌入模式：外部字幕文件将被保留。MP4 对字幕嵌入支持有限，MKV 更稳"))
                self.embed_subtitle_notice.setStyleSheet("QLabel { color: #6c757d; font-size: 10px; margin-left: 15px; white-space: nowrap; }")
            else:
                # 默认提示
                self.embed_subtitle_notice.setText(t("⚠ MP4 对字幕嵌入支持有限，MKV 更稳.建议在'高级格式'功能中选择 MKV"))
                self.embed_subtitle_notice.setStyleSheet("QLabel { color: #6c757d; font-size: 10px; margin-left: 15px; white-space: nowrap; }")
        
        def on_subtitle_toggled(checked):
            """处理'下载字幕'复选框的切换"""
            # 控制自动字幕选项
            self.auto_subtitle_check.setEnabled(True)  # 自动字幕可以独立启用
            
            # 如果取消"下载字幕"，也取消自动字幕
            if not checked and self.auto_subtitle_check.isChecked():
                    self.auto_subtitle_check.setChecked(False)
            
            # 更新所有字幕选项的状态
            update_subtitle_options()
            
            # 更新字幕转换复选框状态（考虑高级选项状态）
            advanced_enabled = hasattr(self, 'advanced_group') and self.advanced_group.isChecked()
            subtitle_enabled = self.subtitle_check.isChecked() or self.auto_subtitle_check.isChecked()
            self.convert_subtitle_check.setEnabled(advanced_enabled and subtitle_enabled)
            
            # 调试信息
            if checked:
                print("[INFO] 已启用下载字幕；其他字幕选项现已可用")
            else:
                print("[INFO] 已禁用下载字幕；相关选项已禁用")
        
        def on_auto_subtitle_toggled(checked):
            """处理'下载自动生成字幕'复选框的切换"""
            # 更新所有字幕选项的状态
            update_subtitle_options()
            
            # 更新字幕转换复选框状态（考虑高级选项状态）
            advanced_enabled = hasattr(self, 'advanced_group') and self.advanced_group.isChecked()
            subtitle_enabled = self.subtitle_check.isChecked() or self.auto_subtitle_check.isChecked()
            self.convert_subtitle_check.setEnabled(advanced_enabled and subtitle_enabled)
            
            # 调试信息
            if checked:
                print("[INFO] 已启用自动生成字幕下载")
            else:
                print("[INFO] 已禁用自动生成字幕下载")
        
        def on_embed_subtitle_toggled(checked):
            """处理'嵌入字幕'复选框的切换"""
            # 更新所有字幕选项的状态
            update_subtitle_options()
            
            # 调试信息
            if checked:
                if not (self.subtitle_check.isChecked() or self.auto_subtitle_check.isChecked()):
                    print("[INFO] 已启用仅嵌入字幕模式（yt-dlp将自动下载并嵌入字幕）")
                else:
                    print("[INFO] 已启用字幕嵌入（外部文件将被保留）")
            else:
                print("[INFO] 已禁用字幕嵌入")
        
        # 连接信号
        self.subtitle_check.toggled.connect(on_subtitle_toggled)
        self.auto_subtitle_check.toggled.connect(on_auto_subtitle_toggled)
        self.embed_subtitle_check.toggled.connect(on_embed_subtitle_toggled)
        
        # 初始化选项状态
        update_subtitle_options()
        
        # 添加弹性空间
        subtitle_layout.addStretch()
        
        # 基本选项选项卡（移除字幕选项后）
        basic_tab = QWidget()
        advanced_tabs.addTab(basic_tab, t("基本选项"))
        
        # 创建滚动区域以容纳基本选项
        basic_scroll = QScrollArea(basic_tab)
        basic_scroll.setWidgetResizable(True)
        basic_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        basic_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 创建滚动内容容器
        basic_content = QWidget()
        basic_layout = QVBoxLayout(basic_content)
        
        # 设置选项卡的主布局
        basic_tab_layout = QVBoxLayout(basic_tab)
        basic_tab_layout.addWidget(basic_scroll)
        basic_scroll.setWidget(basic_content)
        
        # 设置内容布局的间距，增加组件之间的空间
        basic_layout.setSpacing(12)
        basic_layout.setContentsMargins(15, 15, 15, 15)
        
        # 缩略图选项
        self.thumbnail_check = QCheckBox(t("下载缩略图"))
        basic_layout.addWidget(self.thumbnail_check)
        
        # 限速选项
        speed_layout = QHBoxLayout()
        self.limit_speed_check = QCheckBox(t("限制下载速度"))
        self.speed_limit = QSpinBox()
        self.speed_limit.setRange(50, 102400)  # 最大值提高到 100 MB/s，适应现代高速网络
        self.speed_limit.setValue(5000)  # 默认值设为 5 MB/s，更适合大多数场景
        self.speed_limit.setSuffix(" KB/s")
        self.speed_limit.setEnabled(False)
        self.limit_speed_check.toggled.connect(self.speed_limit.setEnabled)
        speed_layout.addWidget(self.limit_speed_check)
        speed_layout.addWidget(self.speed_limit)
        basic_layout.addLayout(speed_layout)
        
        
        # SponsorBlock 支持组 (新功能)
        sponsorblock_group = QGroupBox(t("🚫 SponsorBlock 支持"))
        basic_layout.addWidget(sponsorblock_group)
        sponsorblock_layout = QVBoxLayout(sponsorblock_group)
        sponsorblock_layout.setSpacing(8)
        sponsorblock_layout.setContentsMargins(15, 15, 15, 15)
        
        self.sponsorblock_check = QCheckBox(t("启用 SponsorBlock 自动跳过广告"))
        self.sponsorblock_check.setToolTip(t("自动检测并移除 YouTube 视频中的广告、赞助商内容等片段"))
        self.sponsorblock_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 5px; "
            "spacing: 8px; "
            "color: #e74c3c; "
            "font-weight: bold; "
            "} "
            "QCheckBox:disabled { "
            "color: rgba(127, 127, 127, 170); "
            "} "
            "QCheckBox::indicator { "
            "width: 16px; "
            "height: 16px; "
            "}"
        )
        sponsorblock_layout.addWidget(self.sponsorblock_check)
        
        # SponsorBlock 类型选择
        sponsorblock_types_layout = QHBoxLayout()
        sponsorblock_types_label = QLabel(t("移除类型:"))
        sponsorblock_types_layout.addWidget(sponsorblock_types_label)
        
        # SponsorBlock 类型复选框
        self.sponsorblock_sponsor_check = QCheckBox(t("赞助商"))
        self.sponsorblock_sponsor_check.setChecked(True)
        self.sponsorblock_sponsor_check.setToolTip(t("移除赞助商内容"))
        sponsorblock_types_layout.addWidget(self.sponsorblock_sponsor_check)
        
        self.sponsorblock_intro_check = QCheckBox(t("开场"))
        self.sponsorblock_intro_check.setToolTip(t("移除开场内容"))
        sponsorblock_types_layout.addWidget(self.sponsorblock_intro_check)
        
        self.sponsorblock_outro_check = QCheckBox(t("结尾"))
        self.sponsorblock_outro_check.setToolTip(t("移除结尾内容"))
        sponsorblock_types_layout.addWidget(self.sponsorblock_outro_check)
        
        self.sponsorblock_selfpromo_check = QCheckBox(t("自推广"))
        self.sponsorblock_selfpromo_check.setToolTip(t("移除自我推广内容"))
        sponsorblock_types_layout.addWidget(self.sponsorblock_selfpromo_check)
        
        sponsorblock_types_layout.addStretch()
        sponsorblock_layout.addLayout(sponsorblock_types_layout)
        
        # 设置SponsorBlock选项的联动逻辑
        def update_sponsorblock_options():
            """统一管理SponsorBlock相关选项的启用/禁用状态"""
            # 检查是否启用了高级选项
            advanced_enabled = hasattr(self, 'advanced_group') and self.advanced_group.isChecked()
            
            # 检查是否启用了主SponsorBlock选项
            sponsorblock_enabled = self.sponsorblock_check.isChecked()
            
            # SponsorBlock子选项：只有启用主SponsorBlock选项且高级选项启用时才可用
            if hasattr(self, 'sponsorblock_sponsor_check'):
                self.sponsorblock_sponsor_check.setEnabled(advanced_enabled and sponsorblock_enabled)
            if hasattr(self, 'sponsorblock_intro_check'):
                self.sponsorblock_intro_check.setEnabled(advanced_enabled and sponsorblock_enabled)
            if hasattr(self, 'sponsorblock_outro_check'):
                self.sponsorblock_outro_check.setEnabled(advanced_enabled and sponsorblock_enabled)
            if hasattr(self, 'sponsorblock_selfpromo_check'):
                self.sponsorblock_selfpromo_check.setEnabled(advanced_enabled and sponsorblock_enabled)
            
            # 如果禁用主SponsorBlock选项，也取消勾选所有子选项
            if not sponsorblock_enabled:
                if hasattr(self, 'sponsorblock_sponsor_check'):
                    self.sponsorblock_sponsor_check.setChecked(False)
                if hasattr(self, 'sponsorblock_intro_check'):
                    self.sponsorblock_intro_check.setChecked(False)
                if hasattr(self, 'sponsorblock_outro_check'):
                    self.sponsorblock_outro_check.setChecked(False)
                if hasattr(self, 'sponsorblock_selfpromo_check'):
                    self.sponsorblock_selfpromo_check.setChecked(False)
        
        # 保存为类方法以便在其他地方调用
        self.update_sponsorblock_options = update_sponsorblock_options
        
        def on_sponsorblock_toggled(checked):
            """处理'启用SponsorBlock'复选框的切换"""
            # 更新所有SponsorBlock选项的状态
            update_sponsorblock_options()
            
            # 调试信息
            if checked:
                print("[INFO] 已启用SponsorBlock；子选项现已可用")
            else:
                print("[INFO] 已禁用SponsorBlock；相关子选项已禁用")
        
        # 连接信号
        self.sponsorblock_check.toggled.connect(on_sponsorblock_toggled)
        
        # 章节支持组 (优化版) - 已移除
        # chapters_group = QGroupBox(t("📖 章节支持"))
        # basic_layout.addWidget(chapters_group)
        # chapters_layout = QVBoxLayout(chapters_group)
        # chapters_layout.setSpacing(10)
        # chapters_layout.setContentsMargins(15, 15, 15, 15)
        
        # 以下所有与 chapters_layout 相关的代码均已失效，需一并移除或注释
        # self.custom_time_widget.setVisible(False)
        # chapters_layout.addWidget(self.custom_time_widget)
        
        # 以下章节支持相关控件已移除，仅保留时间段功能的核心变量与信号
        # 如需彻底清理，可继续删除后续引用；此处先注释掉 UI 构建部分
        
        # 由于 chapters_layout 已不存在，以下所有引用它的代码均失效，需全部注释或删除
        # 为简化，直接跳到下一个功能组（高级下载选项组）
        
        # 高级下载选项组 (精简版 - 仅保留常用选项)
        advanced_download_group = QGroupBox(t("🎯 特殊场景下载"))
        basic_layout.addWidget(advanced_download_group)
        advanced_download_layout = QVBoxLayout(advanced_download_group)
        advanced_download_layout.setSpacing(10)
        advanced_download_layout.setContentsMargins(15, 15, 15, 15)
        
        # 添加总管控勾选框
        self.special_scene_master_check = QCheckBox(t("启用特殊场景下载功能"))
        self.special_scene_master_check.setToolTip(t("启用此功能后，才能使用下方的特殊场景下载选项"))
        self.special_scene_master_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 14px; "
            "font-weight: bold; "
            "padding: 8px; "
            "spacing: 10px; "
            "color: #1976d2; "
            "} "
            "QCheckBox:disabled { "
            "color: rgba(127, 127, 127, 170); "
            "} "
            "QCheckBox::indicator { "
            "width: 18px; "
            "height: 18px; "
            "}"
        )
        advanced_download_layout.addWidget(self.special_scene_master_check)
        
        # 添加功能组说明
        scenario_info_label = QLabel(
            t("<b>💡 使用场景说明：</b><br>") + t("• <b>直播录制：</b>从头录制正在直播的视频<br>") + t("• <b>预约下载：</b>自动等待预约直播或定时发布的视频<br>") + t("• <b>容错处理：</b>批量下载时遇错不停，继续下载其他视频<br>") + t("• <b>片段处理：</b>处理分段视频的下载和保留方式")
        )
        scenario_info_label.setWordWrap(True)
        scenario_info_label.setStyleSheet(
            "QLabel { "
            "background-color: #e8f5e9; "
            "border: 1px solid #81c784; "
            "border-radius: 5px; "
            "padding: 8px; "
            "margin-bottom: 10px; "
            "font-size: 12px; "
            "color: #2e7d32; "
            "}"
        )
        advanced_download_layout.addWidget(scenario_info_label)
        
        # 直播相关选项
        self.live_from_start_check = QCheckBox(t("📡 从开头下载直播"))
        self.live_from_start_check.setToolTip(t("从直播开始时间下载，而不是从当前时间开始（实验性功能）"))
        self.live_from_start_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 5px; "
            "spacing: 8px; "
            "} "
            "QCheckBox::indicator { "
            "width: 16px; "
            "height: 16px; "
            "}"
        )
        advanced_download_layout.addWidget(self.live_from_start_check)
        
        # 等待视频可用（预约直播）
        wait_video_layout = QHBoxLayout()
        self.wait_for_video_check = QCheckBox(t("⏳ 等待视频可用"))
        self.wait_for_video_check.setToolTip(t("等待预约直播或计划发布的视频变为可用状态"))
        self.wait_for_video_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 5px; "
            "spacing: 8px; "
            "} "
            "QCheckBox::indicator { "
            "width: 16px; "
            "height: 16px; "
            "}"
        )
        wait_video_layout.addWidget(self.wait_for_video_check)
        
        # 等待重试间隔输入（支持 MIN 或 MIN-MAX）
        wait_video_layout.addWidget(QLabel(t("重试间隔:")))
        self.wait_for_video_time = QSpinBox()
        self.wait_for_video_time.setRange(10, 3600)
        self.wait_for_video_time.setValue(300)
        self.wait_for_video_time.setSuffix(t(" 秒"))
        self.wait_for_video_time.setEnabled(False)
        self.wait_for_video_time.setToolTip(t("设置等待重试的最小间隔（10-3600秒）"))
        wait_video_layout.addWidget(self.wait_for_video_time)

        self.wait_for_video_use_range_check = QCheckBox(t("范围"))
        self.wait_for_video_use_range_check.setEnabled(False)
        self.wait_for_video_use_range_check.setToolTip(t("启用后使用 MIN-MAX 区间随机等待"))
        wait_video_layout.addWidget(self.wait_for_video_use_range_check)

        self.wait_for_video_max_time = QSpinBox()
        self.wait_for_video_max_time.setRange(10, 7200)
        self.wait_for_video_max_time.setValue(600)
        self.wait_for_video_max_time.setSuffix(t(" 秒"))
        self.wait_for_video_max_time.setEnabled(False)
        self.wait_for_video_max_time.setToolTip(t("设置等待重试的最大间隔（需大于等于最小间隔）"))
        wait_video_layout.addWidget(self.wait_for_video_max_time)
        self.wait_for_video_max_time.setMinimum(self.wait_for_video_time.value())

        self.wait_for_video_time.valueChanged.connect(self.wait_for_video_max_time.setMinimum)

        def _on_wait_for_video_toggle(enabled):
            self.wait_for_video_time.setEnabled(enabled)
            self.wait_for_video_use_range_check.setEnabled(enabled)
            self.wait_for_video_max_time.setEnabled(enabled and self.wait_for_video_use_range_check.isChecked())

        def _on_wait_for_video_range_toggle(enabled):
            self.wait_for_video_max_time.setEnabled(enabled and self.wait_for_video_check.isChecked())

        self.wait_for_video_check.toggled.connect(_on_wait_for_video_toggle)
        self.wait_for_video_use_range_check.toggled.connect(_on_wait_for_video_range_toggle)
        wait_video_layout.addStretch()
        advanced_download_layout.addLayout(wait_video_layout)
        
        # 容错选项
        self.ignore_errors_check = QCheckBox(t("🛡️ 忽略错误继续"))
        self.ignore_errors_check.setToolTip(t("遇到下载或后处理错误时继续执行，不中止整个任务（推荐开启）"))
        self.ignore_errors_check.setChecked(True)  # 默认启用
        self.ignore_errors_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 5px; "
            "spacing: 8px; "
            "} "
            "QCheckBox::indicator { "
            "width: 16px; "
            "height: 16px; "
            "}"
        )
        advanced_download_layout.addWidget(self.ignore_errors_check)
        
        # 片段下载选项
        self.skip_unavailable_fragments_check = QCheckBox(t("⏭️ 跳过不可用片段"))
        self.skip_unavailable_fragments_check.setToolTip(t("勾选后跳过不可用片段；不勾选时遇到缺失片段将中止下载"))
        self.skip_unavailable_fragments_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 5px; "
            "spacing: 8px; "
            "} "
            "QCheckBox::indicator { "
            "width: 16px; "
            "height: 16px; "
            "}"
        )
        advanced_download_layout.addWidget(self.skip_unavailable_fragments_check)
        
        self.keep_fragments_check = QCheckBox(t("💾 保留下载片段"))
        self.keep_fragments_check.setToolTip(t("下载完成后保留临时片段文件，用于调试（会占用额外空间）"))
        self.keep_fragments_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 5px; "
            "spacing: 8px; "
            "} "
            "QCheckBox::indicator { "
            "width: 16px; "
            "height: 16px; "
            "}"
        )
        advanced_download_layout.addWidget(self.keep_fragments_check)
        
        # 连接特殊场景下载主控选项的信号
        def on_special_scene_master_toggled(checked):
            """处理特殊场景下载主控选项的切换"""
            # 获取高级选项状态
            advanced_enabled = hasattr(self, 'advanced_group') and self.advanced_group.isChecked()
            
            # 控制所有子选项的启用状态
            if hasattr(self, 'live_from_start_check'):
                self.live_from_start_check.setEnabled(advanced_enabled and checked)
            if hasattr(self, 'wait_for_video_check'):
                self.wait_for_video_check.setEnabled(advanced_enabled and checked)
                # 同时控制等待间隔输入
                if hasattr(self, 'wait_for_video_time'):
                    self.wait_for_video_time.setEnabled(advanced_enabled and checked and 
                                                      (hasattr(self, 'wait_for_video_check') and self.wait_for_video_check.isChecked()))
                if hasattr(self, 'wait_for_video_use_range_check'):
                    self.wait_for_video_use_range_check.setEnabled(
                        advanced_enabled and checked and
                        (hasattr(self, 'wait_for_video_check') and self.wait_for_video_check.isChecked())
                    )
                if hasattr(self, 'wait_for_video_max_time'):
                    self.wait_for_video_max_time.setEnabled(
                        advanced_enabled and checked and
                        (hasattr(self, 'wait_for_video_check') and self.wait_for_video_check.isChecked()) and
                        (hasattr(self, 'wait_for_video_use_range_check') and self.wait_for_video_use_range_check.isChecked())
                    )
            if hasattr(self, 'ignore_errors_check'):
                self.ignore_errors_check.setEnabled(advanced_enabled and checked)
            if hasattr(self, 'skip_unavailable_fragments_check'):
                self.skip_unavailable_fragments_check.setEnabled(advanced_enabled and checked)
            if hasattr(self, 'keep_fragments_check'):
                self.keep_fragments_check.setEnabled(advanced_enabled and checked)
            
            # 如果取消主控选项，也取消勾选所有子选项
            if not checked:
                if hasattr(self, 'live_from_start_check'):
                    self.live_from_start_check.setChecked(False)
                if hasattr(self, 'wait_for_video_check'):
                    self.wait_for_video_check.setChecked(False)
                if hasattr(self, 'wait_for_video_use_range_check'):
                    self.wait_for_video_use_range_check.setChecked(False)
                if hasattr(self, 'ignore_errors_check'):
                    self.ignore_errors_check.setChecked(False)
                if hasattr(self, 'skip_unavailable_fragments_check'):
                    self.skip_unavailable_fragments_check.setChecked(False)
                if hasattr(self, 'keep_fragments_check'):
                    self.keep_fragments_check.setChecked(False)
            else:
                # 恢复推荐默认：若无任何子选项勾选，默认开启 ignore-errors 提升批量稳定性
                selected = False
                for attr_name in (
                    'live_from_start_check',
                    'wait_for_video_check',
                    'ignore_errors_check',
                    'skip_unavailable_fragments_check',
                    'keep_fragments_check',
                ):
                    obj = getattr(self, attr_name, None)
                    if obj is not None and hasattr(obj, 'isChecked') and obj.isChecked():
                        selected = True
                        break
                if not selected and hasattr(self, 'ignore_errors_check'):
                    self.ignore_errors_check.setChecked(True)
            
            # 调试信息
            if checked:
                print("[INFO] 已启用特殊场景下载功能；子选项现已可用")
            else:
                print("[INFO] 已禁用特殊场景下载功能；相关子选项已禁用")
        
        # 保存为类方法以便在其他地方调用
        self.update_special_scene_options = on_special_scene_master_toggled
        
        # 连接信号
        self.special_scene_master_check.toggled.connect(on_special_scene_master_toggled)
        
        # 在基本选项内容末尾添加弹性空间，确保滚动区域正常工作
        basic_layout.addStretch()
        
        # 播放列表选项
        playlist_tab = QWidget()
        advanced_tabs.addTab(playlist_tab, t("播放列表"))
        
        # 创建滚动区域
        playlist_scroll = QScrollArea(playlist_tab)
        playlist_scroll.setWidgetResizable(True)
        playlist_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        playlist_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 创建滚动内容容器
        playlist_content = QWidget()
        playlist_layout = QVBoxLayout(playlist_content)
        
        # 设置选项卡的主布局
        playlist_tab_layout = QVBoxLayout(playlist_tab)
        playlist_tab_layout.addWidget(playlist_scroll)
        playlist_scroll.setWidget(playlist_content)
        
        # 添加播放列表功能使用说明
        usage_label = QLabel(
            t("<b>播放列表功能使用说明：</b><br>") + t("1. <b>单个视频：</b>直接输入视频URL，无需勾选播放列表选项<br>") + t("2. <b>播放列表/频道：</b>输入播放列表URL或博主主页URL，勾选'下载播放列表'<br>") + t("3. <b>获取信息：</b>点击'获取播放列表信息'按钮查看视频列表<br>") + t("4. <b>选择范围：</b>使用'智能选择范围'或手动输入范围（如：1-10,15,20-25）<br>") + t("5. <b>开始下载：</b>设置好范围后点击'开始下载'按钮")
        )
        usage_label.setWordWrap(True)
        usage_label.setStyleSheet(
            "QLabel { "
            "background-color: #f0f8ff; "
            "border: 1px solid #b0c4de; "
            "border-radius: 5px; "
            "padding: 8px; "
            "margin: 5px; "
            "}"
        )
        playlist_layout.addWidget(usage_label)
        
        self.playlist_check = QCheckBox(t("下载播放列表"))
        playlist_layout.addWidget(self.playlist_check)
        
        # 添加获取播放列表按钮
        get_playlist_layout = QHBoxLayout()
        self.get_playlist_button = QPushButton(t("获取播放列表信息"))
        self.get_playlist_button.setToolTip(t("点击获取当前URL的播放列表信息（适用于博主主页、频道页面等）"))
        self.get_playlist_button.clicked.connect(self.get_playlist_info)
        get_playlist_layout.addWidget(self.get_playlist_button)
        get_playlist_layout.addStretch()
        playlist_layout.addLayout(get_playlist_layout)
        
        # 播放列表范围选择
        playlist_range_layout = QHBoxLayout()
        playlist_range_layout.addWidget(QLabel(t("播放列表范围:")))
        self.playlist_range = QLineEdit()
        self.playlist_range.setPlaceholderText(t("例如: 1-10,15,20-25"))
        self.playlist_range.setEnabled(False)
        playlist_range_layout.addWidget(self.playlist_range)
        
        # 添加智能范围选择按钮
        self.playlist_range_button = QPushButton(t("智能选择范围"))
        self.playlist_range_button.setEnabled(False)
        self.playlist_range_button.clicked.connect(self.open_playlist_range_dialog)
        playlist_range_layout.addWidget(self.playlist_range_button)
        
        playlist_layout.addLayout(playlist_range_layout)
        
        # 播放列表排序选项组已移除
        
        # 下载限制选项组已移除
        
        # 文件大小过滤选项组已移除
        
        # 日期过滤选项组已移除
        
        # 在播放列表布局末尾添加弹性空间
        playlist_layout.addStretch()
        
        # 存储当前播放列表信息
        self.current_playlist_info = None
        self.playlist_check.toggled.connect(self._on_playlist_toggled)
        self._on_playlist_toggled(self.playlist_check.isChecked())
        
        # 地理限制选项卡已移除，功能合并到"限制绕过"选项卡中
        
        # 限制绕过选项卡已移除 - 限制绕过功能已被删除
        
        # 元数据选项
        metadata_tab = QWidget()
        advanced_tabs.addTab(metadata_tab, t("元数据"))
        metadata_layout = QVBoxLayout(metadata_tab)
        
        # 添加元数据功能说明
        metadata_info_label = QLabel(
            t("<b>元数据功能说明</b><br>") + t("• <b>下载选项</b>: 将元数据保存为独立文件<br>") + t("• <b>嵌入选项</b>: 将元数据直接嵌入到视频文件中<br>") + t("• <b>解析修改</b>: 自定义元数据字段的解析和替换规则<br>") + t("• <b>扩展属性</b>: 使用系统扩展属性存储元数据")
        )
        metadata_info_label.setWordWrap(True)
        metadata_info_label.setStyleSheet(
            "QLabel { "
            "background-color: #f0f8ff; "
            "border: 1px solid #b0c4de; "
            "border-radius: 5px; "
            "padding: 8px; "
            "margin: 5px 0px 15px 0px; "
            "font-size: 12px; "
            "}"
        )
        metadata_layout.addWidget(metadata_info_label)
        
        # 创建滚动区域以容纳更多选项
        metadata_scroll = QScrollArea()
        metadata_scroll.setWidgetResizable(True)
        metadata_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        metadata_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        metadata_layout.addWidget(metadata_scroll)
        
        metadata_content = QWidget()
        metadata_scroll.setWidget(metadata_content)
        metadata_content_layout = QVBoxLayout(metadata_content)
        
        # 设置内容布局的间距，增加组件之间的空间
        metadata_content_layout.setSpacing(20)
        metadata_content_layout.setContentsMargins(10, 10, 10, 10)
        
        # 元数据下载选项组
        metadata_download_group = QGroupBox(t("📁 元数据下载选项"))
        metadata_download_group.setStyleSheet(
            "QGroupBox { "
            "font-size: 14px; "
            "font-weight: bold; "
            "border: 2px solid #4CAF50; "
            "border-radius: 8px; "
            "margin-top: 10px; "
            "padding-top: 10px; "
            "} "
            "QGroupBox::title { "
            "subcontrol-origin: margin; "
            "left: 10px; "
            "padding: 0 8px 0 8px; "
            "color: #4CAF50; "
            "}"
        )
        metadata_content_layout.addWidget(metadata_download_group)
        metadata_download_layout = QVBoxLayout(metadata_download_group)
        
        # 设置下载选项组内的间距
        metadata_download_layout.setSpacing(10)
        metadata_download_layout.setContentsMargins(15, 20, 15, 15)
        
        self.write_description_check = QCheckBox(t("📄 下载视频描述"))
        self.write_description_check.setToolTip(t("下载视频的描述信息并保存为.description文件"))
        # 设置复选框样式
        self.write_description_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 6px; "
            "spacing: 10px; "
            "} "
            "QCheckBox::indicator { "
            "width: 18px; "
            "height: 18px; "
            "}"
        )
        metadata_download_layout.addWidget(self.write_description_check)
        
        self.write_info_check = QCheckBox(t("📊 下载JSON元数据"))
        self.write_info_check.setToolTip(t("下载完整的视频元数据信息并保存为.info.json文件，包含标题、作者、时长、观看次数等"))
        # 设置复选框样式
        self.write_info_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 6px; "
            "spacing: 10px; "
            "} "
            "QCheckBox::indicator { "
            "width: 18px; "
            "height: 18px; "
            "}"
        )
        metadata_download_layout.addWidget(self.write_info_check)
        
        self.write_comments_check = QCheckBox(t("💬 下载视频评论"))
        self.write_comments_check.setToolTip(t("下载视频评论并保存为JSON文件（如果网站支持）"))
        # 设置复选框样式
        self.write_comments_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 6px; "
            "spacing: 10px; "
            "} "
            "QCheckBox::indicator { "
            "width: 18px; "
            "height: 18px; "
            "}"
        )
        metadata_download_layout.addWidget(self.write_comments_check)
        
        # 元数据嵌入选项组
        metadata_embed_group = QGroupBox(t("🎬 元数据嵌入选项"))
        metadata_embed_group.setStyleSheet(
            "QGroupBox { "
            "font-size: 14px; "
            "font-weight: bold; "
            "border: 2px solid #2196F3; "
            "border-radius: 8px; "
            "margin-top: 10px; "
            "padding-top: 10px; "
            "} "
            "QGroupBox::title { "
            "subcontrol-origin: margin; "
            "left: 10px; "
            "padding: 0 8px 0 8px; "
            "color: #2196F3; "
            "}"
        )
        metadata_content_layout.addWidget(metadata_embed_group)
        metadata_embed_layout = QVBoxLayout(metadata_embed_group)
        
        # 设置嵌入选项组内的间距
        metadata_embed_layout.setSpacing(10)
        metadata_embed_layout.setContentsMargins(15, 20, 15, 15)
        
        self.embed_metadata_check = QCheckBox(t("📝 嵌入基础元数据"))
        self.embed_metadata_check.setToolTip(t("将标题、作者、描述等基础元数据信息嵌入到视频文件中"))
        # 设置复选框样式
        self.embed_metadata_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 6px; "
            "spacing: 10px; "
            "} "
            "QCheckBox::indicator { "
            "width: 18px; "
            "height: 18px; "
            "}"
        )
        metadata_embed_layout.addWidget(self.embed_metadata_check)
        
        self.embed_thumbnail_check = QCheckBox(t("🖼️ 嵌入缩略图"))
        self.embed_thumbnail_check.setToolTip(t("将视频缩略图嵌入到视频文件中作为封面"))
        # 设置复选框样式
        self.embed_thumbnail_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 6px; "
            "spacing: 10px; "
            "} "
            "QCheckBox::indicator { "
            "width: 18px; "
            "height: 18px; "
            "}"
        )
        metadata_embed_layout.addWidget(self.embed_thumbnail_check)
        
        # 新增：嵌入章节信息
        self.embed_chapters_check = QCheckBox(t("📑 嵌入章节信息"))
        self.embed_chapters_check.setToolTip(t("将视频章节信息嵌入到视频文件中（如果可用）"))
        self.embed_chapters_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 6px; "
            "spacing: 10px; "
            "} "
            "QCheckBox::indicator { "
            "width: 18px; "
            "height: 18px; "
            "}"
        )
        metadata_embed_layout.addWidget(self.embed_chapters_check)
        
        # 新增：嵌入JSON信息文件
        self.embed_info_json_check = QCheckBox(t("📋 嵌入完整JSON信息"))
        self.embed_info_json_check.setToolTip(t("将完整的JSON元数据信息嵌入到mkv/mka视频文件中"))
        self.embed_info_json_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 6px; "
            "spacing: 10px; "
            "} "
            "QCheckBox::indicator { "
            "width: 18px; "
            "height: 18px; "
            "}"
        )
        metadata_embed_layout.addWidget(self.embed_info_json_check)
        
        # 新增：写入扩展属性
        self.xattrs_check = QCheckBox(t("🏷️ 写入扩展属性(xattrs)"))
        self.xattrs_check.setToolTip(t("将元数据写入到文件的扩展属性中（使用Dublin Core和XDG标准）"))
        self.xattrs_check.setStyleSheet(
            "QCheckBox { "
            "font-size: 13px; "
            "padding: 6px; "
            "spacing: 10px; "
            "} "
            "QCheckBox::indicator { "
            "width: 18px; "
            "height: 18px; "
            "}"
        )
        metadata_embed_layout.addWidget(self.xattrs_check)
        
        # 在内容布局末尾添加伸缩空间
        metadata_content_layout.addStretch()
        
        # === 网络选项标签页（独立） ===
        network_tab = QWidget()
        advanced_tabs.addTab(network_tab, t("🌐 网络"))
        
        # 创建滚动区域
        network_scroll = QScrollArea()
        network_scroll.setWidgetResizable(True)
        network_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        network_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 创建网络选项内容容器
        network_content = QWidget()
        network_content_layout = QVBoxLayout(network_content)
        
        # 设置网络标签页的主布局
        network_tab_layout = QVBoxLayout(network_tab)
        network_tab_layout.addWidget(network_scroll)
        network_scroll.setWidget(network_content)
        
        # 添加网络功能说明
        network_info_label = QLabel(
            t("<b>🌐 网络选项说明</b><br>") + t("• <b>代理设置</b>: 配置HTTP/HTTPS/SOCKS5代理，适用于需要代理访问的环境<br>") + t("• <b>网络参数</b>: 调整超时时间、IP版本等网络连接参数<br>") + t("• <b>代理模式</b>: 启用代理后，工具会自动应用保守的网络优化策略<br>") + t("• <b>测试功能</b>: 使用'测试'按钮可验证代理连接是否正常")
        )
        network_info_label.setWordWrap(True)
        network_info_label.setStyleSheet(
            "QLabel { "
            "background-color: #e3f2fd; "
            "border: 1px solid #4a90e2; "
            "border-radius: 5px; "
            "padding: 10px; "
            "margin: 5px 0px 15px 0px; "
            "font-size: 12px; "
            "}"
        )
        network_content_layout.addWidget(network_info_label)
        
        # === 代理设置组 ===
        proxy_group = QGroupBox(t("🔐 代理设置"))
        proxy_group.setStyleSheet(
            "QGroupBox { "
            "font-weight: bold; "
            "font-size: 13px; "
            "margin-top: 10px; "
            "padding-top: 15px; "
            "border: 2px solid #4a90e2; "
            "border-radius: 5px; "
            "} "
            "QGroupBox::title { "
            "subcontrol-origin: margin; "
            "subcontrol-position: top center; "
            "padding: 0 10px; "
            "background-color: white; "
            "color: #4a90e2; "
            "}"
        )
        proxy_layout = QVBoxLayout()
        proxy_group.setLayout(proxy_layout)
        
        # 代理启用复选框
        proxy_enable_layout = QHBoxLayout()
        self.proxy_check = QCheckBox(t("启用代理"))
        self.proxy_check.setToolTip(t("启用网络代理，支持HTTP、HTTPS和SOCKS5代理"))
        self.proxy_check.setStyleSheet("QCheckBox { font-size: 13px; font-weight: bold; }")
        proxy_enable_layout.addWidget(self.proxy_check)
        proxy_enable_layout.addStretch()
        proxy_layout.addLayout(proxy_enable_layout)
        
        # 代理地址输入行
        proxy_input_layout = QHBoxLayout()
        proxy_input_layout.addWidget(QLabel(t("代理地址:")))
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText(t("例如: socks5://127.0.0.1:1080 或 http://proxy.com:8080"))
        self.proxy_input.setEnabled(False)
        self.proxy_input.setToolTip(
            t("支持的格式:\n") + t("• HTTP代理: http://127.0.0.1:7890\n") + t("• HTTPS代理: https://proxy.com:8080\n") + t("• SOCKS5代理: socks5://127.0.0.1:1080\n") + t("• 带认证: http://user:pass@proxy.com:8080")
        )
        # 设置初始蓝色边框样式
        self.proxy_input.setStyleSheet(
            "QLineEdit {"
            "border: 2px solid #4a90e2;"  # 蓝色边框
            "border-radius: 4px;"
            "padding: 5px;"
            "}"
            "QLineEdit:disabled {"
            "border: 2px solid #cccccc;"  # 禁用时灰色边框
            "background-color: #f5f5f5;"
            "}"
        )
        # 连接文本改变信号，当用户修改代理地址时重置边框颜色
        self.proxy_input.textChanged.connect(self.reset_proxy_input_border)
        proxy_input_layout.addWidget(self.proxy_input)
        
        # 代理测试按钮
        self.test_proxy_button = QPushButton(t("🔍 测试连接"))
        self.test_proxy_button.setToolTip(t("测试代理连接是否正常"))
        self.test_proxy_button.setEnabled(False)
        self.test_proxy_button.setStyleSheet(
            "QPushButton { "
            "background-color: #17a2b8; "
            "color: white; "
            "border: none; "
            "padding: 6px 15px; "
            "border-radius: 4px; "
            "font-weight: bold; "
            "font-size: 12px; "
            "} "
            "QPushButton:hover { "
            "background-color: #138496; "
            "} "
            "QPushButton:disabled { "
            "background-color: #6c757d; "
            "}"
        )
        self.test_proxy_button.clicked.connect(self.test_proxy_connection)
        proxy_input_layout.addWidget(self.test_proxy_button)
        proxy_layout.addLayout(proxy_input_layout)
        
        # 代理模板选择
        proxy_preset_layout = QHBoxLayout()
        proxy_preset_layout.addWidget(QLabel(t("常用模板:")))
        self.proxy_preset_combo = QComboBox()
        self.proxy_preset_combo.addItems([
            t("选择模板..."),
            t("本地Clash (HTTP): http://127.0.0.1:7890"),
            t("本地Clash (Socks5): socks5://127.0.0.1:7891"),
            t("本地V2Ray (HTTP): http://127.0.0.1:10809"),
            t("本地V2Ray (Socks5): socks5://127.0.0.1:10808"),
            t("本地SSR (Socks5): socks5://127.0.0.1:1080"),
            t("本地Shadowsocks: socks5://127.0.0.1:1086")
        ])
        self.proxy_preset_combo.setEnabled(False)
        self.proxy_preset_combo.setToolTip(t("快速选择常用代理配置"))
        self.proxy_preset_combo.currentIndexChanged.connect(self.apply_proxy_preset)
        proxy_preset_layout.addWidget(self.proxy_preset_combo)
        proxy_preset_layout.addStretch()
        proxy_layout.addLayout(proxy_preset_layout)
        
        # 连接代理复选框的信号
        self.proxy_check.toggled.connect(lambda checked: self.proxy_input.setEnabled(checked))
        self.proxy_check.toggled.connect(lambda checked: self.test_proxy_button.setEnabled(checked))
        self.proxy_check.toggled.connect(lambda checked: self.proxy_preset_combo.setEnabled(checked))
        # 当启用/禁用代理时，重置边框颜色
        self.proxy_check.toggled.connect(lambda checked: self.reset_proxy_input_border() if checked else None)
        
        network_content_layout.addWidget(proxy_group)
        
        # === 网络参数设置组 ===
        network_params_group = QGroupBox(t("⚙️ 网络参数"))
        network_params_group.setStyleSheet(
            "QGroupBox { "
            "font-weight: bold; "
            "font-size: 13px; "
            "margin-top: 10px; "
            "padding-top: 15px; "
            "border: 2px solid #4a90e2; "
            "border-radius: 5px; "
            "} "
            "QGroupBox::title { "
            "subcontrol-origin: margin; "
            "subcontrol-position: top center; "
            "padding: 0 10px; "
            "background-color: white; "
            "color: #4a90e2; "
            "}"
        )
        network_params_layout = QVBoxLayout()
        network_params_group.setLayout(network_params_layout)
        
        # 超时设置
        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel(t("连接超时:")))
        self.timeout_setting = QSpinBox()
        self.timeout_setting.setRange(10, 300)
        self.timeout_setting.setValue(30)
        self.timeout_setting.setSuffix(t(" 秒"))
        self.timeout_setting.setToolTip(
            t("设置网络请求的超时时间\n") + t("• 正常网络: 30秒\n") + t("• 代理网络: 建议60-120秒\n") + t("• 不稳定网络: 建议90-180秒")
        )
        self.timeout_setting.setMinimumWidth(120)
        timeout_layout.addWidget(self.timeout_setting)
        timeout_layout.addStretch()
        network_params_layout.addLayout(timeout_layout)
        
        # IP版本选择
        ip_version_layout = QHBoxLayout()
        ip_version_layout.addWidget(QLabel(t("IP版本:")))
        self.ip_version_combo = QComboBox()
        self.ip_version_combo.addItems([t("自动"), t("仅IPv4"), t("仅IPv6")])
        self.ip_version_combo.setCurrentText(t("仅IPv4"))
        self.ip_version_combo.setToolTip(
            t("选择使用的IP协议版本\n") + t("• 仅IPv4: 强制使用IPv4，避免IPv6问题（推荐）\n") + t("• 仅IPv6: 强制使用IPv6\n") + t("• 自动: 由系统自动选择")
        )
        self.ip_version_combo.setMinimumWidth(120)
        ip_version_layout.addWidget(self.ip_version_combo)
        ip_version_layout.addStretch()
        network_params_layout.addLayout(ip_version_layout)
        
        # 源地址绑定
        source_address_layout = QHBoxLayout()
        self.source_address_check = QCheckBox(t("绑定源地址"))
        self.source_address_check.setToolTip(t("绑定到特定的本地IP地址（多网卡环境使用）"))
        source_address_layout.addWidget(self.source_address_check)
        
        self.source_address_input = QLineEdit()
        self.source_address_input.setPlaceholderText(t("例如: 192.168.1.100"))
        self.source_address_input.setEnabled(False)
        self.source_address_input.setToolTip(t("输入要绑定的本地网卡IP地址"))
        source_address_layout.addWidget(self.source_address_input)
        
        # 新增：源地址测试按钮
        self.test_source_address_button = QPushButton(t("🔍 测试"))
        self.test_source_address_button.setToolTip(t("测试该IP地址是否可以在本地绑定"))
        self.test_source_address_button.setEnabled(False)
        self.test_source_address_button.setFixedWidth(80)
        self.test_source_address_button.setStyleSheet(
            "QPushButton { "
            "background-color: #17a2b8; "
            "color: white; "
            "border: none; "
            "padding: 5px; "
            "border-radius: 4px; "
            "font-weight: bold; "
            "} "
            "QPushButton:hover { "
            "background-color: #138496; "
            "} "
            "QPushButton:disabled { "
            "background-color: #6c757d; "
            "}"
        )
        self.test_source_address_button.clicked.connect(self.test_source_address_connection)
        source_address_layout.addWidget(self.test_source_address_button)
        
        self.source_address_check.toggled.connect(self.source_address_input.setEnabled)
        self.source_address_check.toggled.connect(self.test_source_address_button.setEnabled)
        network_params_layout.addLayout(source_address_layout)
        
        network_content_layout.addWidget(network_params_group)
        
        # 添加优化策略说明
        optimization_info_label = QLabel(
            t("<b>📊 优化策略说明：</b><br>") + t("• <b>正常模式</b>: 并发片段8、HTTP块10MB、aria2c 16线程，优先速度<br>") + t("• <b>代理模式</b>: 并发片段3、HTTP块5MB、重试10次、禁用aria2c，优先稳定性<br>") + t("• 启用代理后，工具会自动应用保守的网络优化策略，提高下载成功率")
        )
        optimization_info_label.setWordWrap(True)
        optimization_info_label.setStyleSheet(
            "QLabel { "
            "background-color: #fff8dc; "
            "border: 1px solid #ffa500; "
            "border-radius: 5px; "
            "padding: 8px; "
            "margin: 15px 0px 5px 0px; "
            "font-size: 11px; "
            "}"
        )
        network_content_layout.addWidget(optimization_info_label)
        
        # 在内容布局末尾添加伸缩空间
        network_content_layout.addStretch()
        
        # === 时间片段下载标签页（独立） ===
        time_section_tab = QWidget()
        advanced_tabs.addTab(time_section_tab, t("⏱️ 时间片段"))
        
        # 创建滚动区域
        time_section_scroll = QScrollArea()
        time_section_scroll.setWidgetResizable(True)
        time_section_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        time_section_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 创建时间片段选项内容容器
        time_section_content = QWidget()
        time_section_content_layout = QVBoxLayout(time_section_content)
        
        # 设置时间片段标签页的主布局
        time_section_tab_layout = QVBoxLayout(time_section_tab)
        time_section_tab_layout.addWidget(time_section_scroll)
        time_section_scroll.setWidget(time_section_content)
        
        # 添加时间片段功能说明（包含重要提示）
        time_section_info_label = QLabel(
            t("<b>⏱️ 时间片段下载</b><br>") + t("只下载视频的指定时间段，不下载完整视频。支持多个时间段。<br><br>") + t("<b>⚠️ 重要提示：</b>下载外网视频（如YouTube、Twitter/X）使用此功能时，") + t("若目标站点在当前网络环境下不可直连，") + t("<b style='color:#d32f2f;'>建议启用工具中的网络代理功能</b>；") + t("可直连时无需代理。国内视频（如B站）通常无需代理。")
        )
        time_section_info_label.setWordWrap(True)
        time_section_info_label.setStyleSheet(
            "QLabel { "
            "background-color: #fff3e0; "
            "border: 1px solid #ffb74d; "
            "border-radius: 5px; "
            "padding: 10px; "
            "margin: 5px 0px 15px 0px; "
            "font-size: 12px; "
            "}"
        )
        time_section_content_layout.addWidget(time_section_info_label)
        
        # === 时间片段设置组 ===
        time_section_group = QGroupBox(t("⏰ 时间片段设置"))
        time_section_group.setStyleSheet(
            "QGroupBox { "
            "font-weight: bold; "
            "font-size: 13px; "
            "margin-top: 10px; "
            "padding-top: 15px; "
            "border: 2px solid #ff9800; "
            "border-radius: 5px; "
            "} "
            "QGroupBox::title { "
            "subcontrol-origin: margin; "
            "subcontrol-position: top center; "
            "padding: 0 10px; "
            "background-color: white; "
            "color: #ff9800; "
            "}"
        )
        time_section_group_layout = QVBoxLayout(time_section_group)
        
        # 启用时间片段下载
        self.time_section_check = QCheckBox(t("启用时间片段下载"))
        self.time_section_check.setToolTip(t("勾选后只下载指定时间段的视频内容，需要 ffmpeg"))
        time_section_group_layout.addWidget(self.time_section_check)
        
        # 连接时间片段主控复选框的信号
        self.time_section_check.toggled.connect(self.on_time_section_master_toggled)
        
        # 快捷按钮区域
        quick_buttons_layout = QHBoxLayout()
        quick_buttons_layout.addWidget(QLabel(t("快捷选择:")))
        
        # 前1分钟按钮
        self.first_minute_btn = QPushButton(t("前1分钟"))
        self.first_minute_btn.setToolTip(t("下载视频的前60秒"))
        self.first_minute_btn.clicked.connect(lambda: self.add_time_section_quick("0", "0", "0", "0", "1", "0"))
        quick_buttons_layout.addWidget(self.first_minute_btn)
        
        # 后1分钟按钮
        self.last_minute_btn = QPushButton(t("后1分钟"))
        self.last_minute_btn.setToolTip(t("下载视频的最后60秒"))
        self.last_minute_btn.clicked.connect(lambda: self.add_time_section_quick("-1", "0", "0", "inf", "inf", "inf"))
        quick_buttons_layout.addWidget(self.last_minute_btn)
        
        # URL时间按钮
        self.url_time_btn = QPushButton(t("URL时间"))
        self.url_time_btn.setToolTip(t("使用视频URL中的时间参数（如果有）"))
        self.url_time_btn.clicked.connect(self.add_url_time_section)
        quick_buttons_layout.addWidget(self.url_time_btn)
        
        quick_buttons_layout.addStretch()
        time_section_group_layout.addLayout(quick_buttons_layout)
        
        # 时间输入区域
        time_input_widget = QWidget()
        time_input_layout = QGridLayout(time_input_widget)
        time_input_layout.setContentsMargins(0, 5, 0, 5)
        
        # 开始时间
        time_input_layout.addWidget(QLabel(t("开始时间:")), 0, 0)
        self.start_hour = QSpinBox()
        self.start_hour.setRange(0, 99)
        self.start_hour.setSuffix(t(" 时"))
        self.start_hour.setToolTip(t("开始时间-小时"))
        time_input_layout.addWidget(self.start_hour, 0, 1)
        
        self.start_minute = QSpinBox()
        self.start_minute.setRange(0, 59)
        self.start_minute.setSuffix(t(" 分"))
        self.start_minute.setToolTip(t("开始时间-分钟"))
        time_input_layout.addWidget(self.start_minute, 0, 2)
        
        self.start_second = QSpinBox()
        self.start_second.setRange(0, 59)
        self.start_second.setSuffix(t(" 秒"))
        self.start_second.setToolTip(t("开始时间-秒"))
        time_input_layout.addWidget(self.start_second, 0, 3)
        
        # 结束时间
        time_input_layout.addWidget(QLabel(t("结束时间:")), 1, 0)
        self.end_hour = QSpinBox()
        self.end_hour.setRange(0, 99)
        self.end_hour.setSuffix(t(" 时"))
        self.end_hour.setToolTip(t("结束时间-小时（填0表示到视频结束）"))
        time_input_layout.addWidget(self.end_hour, 1, 1)
        
        self.end_minute = QSpinBox()
        self.end_minute.setRange(0, 59)
        self.end_minute.setSuffix(t(" 分"))
        self.end_minute.setToolTip(t("结束时间-分钟（填0表示到视频结束）"))
        time_input_layout.addWidget(self.end_minute, 1, 2)
        
        self.end_second = QSpinBox()
        self.end_second.setRange(0, 59)
        self.end_second.setSuffix(t(" 秒"))
        self.end_second.setToolTip(t("结束时间-秒（填0表示到视频结束）"))
        time_input_layout.addWidget(self.end_second, 1, 3)
        
        # 添加和清空按钮
        buttons_layout = QHBoxLayout()
        self.add_section_btn = QPushButton(t("➕ 添加"))
        self.add_section_btn.setToolTip(t("将上面设置的时间段添加到列表"))
        self.add_section_btn.clicked.connect(self.add_custom_time_section)
        buttons_layout.addWidget(self.add_section_btn)
        
        self.clear_sections_btn = QPushButton(t("🗑️ 清空"))
        self.clear_sections_btn.setToolTip(t("清空所有已添加的时间段"))
        self.clear_sections_btn.clicked.connect(self.clear_time_sections)
        buttons_layout.addWidget(self.clear_sections_btn)
        buttons_layout.addStretch()
        
        time_input_layout.addLayout(buttons_layout, 2, 0, 1, 4)
        time_section_group_layout.addWidget(time_input_widget)

        # 手动输入时间段/章节正则（对齐 yt-dlp --download-sections 文档）
        custom_section_layout = QHBoxLayout()
        custom_section_layout.addWidget(QLabel(t("手动输入:")))
        self.custom_section_input = QLineEdit()
        self.custom_section_input.setPlaceholderText(t("例如: *10:15-inf 或 *-60-inf 或 intro|opening"))
        self.custom_section_input.setToolTip(
            t("支持 yt-dlp --download-sections 语法：\n") + t("• 时间段: *START-END（支持负时间和 inf）\n") + t("• URL时间: *from-url\n") + t("• 章节正则: intro|opening")
        )
        self.custom_section_input.returnPressed.connect(lambda: self.add_download_section(self.custom_section_input.text()))
        custom_section_layout.addWidget(self.custom_section_input)

        self.add_custom_section_btn = QPushButton(t("添加输入"))
        self.add_custom_section_btn.setToolTip(t("添加手动输入的时间段/章节规则"))
        self.add_custom_section_btn.clicked.connect(lambda: self.add_download_section(self.custom_section_input.text()))
        custom_section_layout.addWidget(self.add_custom_section_btn)
        time_section_group_layout.addLayout(custom_section_layout)
        
        # 已添加的时间段列表
        self.sections_list_widget = QListWidget()
        self.sections_list_widget.setMaximumHeight(72)
        self.sections_list_widget.setToolTip(t("已添加的时间段列表，双击可删除"))
        self.sections_list_widget.itemDoubleClicked.connect(self.remove_time_section)
        time_section_group_layout.addWidget(QLabel(t("已添加时间段（双击删除）:")))
        time_section_group_layout.addWidget(self.sections_list_widget)
        
        # 初始化时间段列表
        self.download_sections = []
        self.current_section_label = QLabel(t("当前: 无"))
        time_section_group_layout.addWidget(self.current_section_label)
        self.update_sections_display()
        
        time_section_content_layout.addWidget(time_section_group)
        
        # === 章节相关选项组 ===
        chapter_group = QGroupBox(t("📑 章节选项"))
        chapter_group.setStyleSheet(
            "QGroupBox { "
            "font-weight: bold; "
            "font-size: 13px; "
            "margin-top: 10px; "
            "padding-top: 15px; "
            "border: 2px solid #4caf50; "
            "border-radius: 5px; "
            "} "
            "QGroupBox::title { "
            "subcontrol-origin: margin; "
            "subcontrol-position: top center; "
            "padding: 0 10px; "
            "background-color: white; "
            "color: #4caf50; "
            "}"
        )
        chapter_group_layout = QVBoxLayout(chapter_group)
        
        # 按章节分割视频
        self.split_chapters_check = QCheckBox(t("按章节分割视频"))
        self.split_chapters_check.setToolTip(
            t("将视频按内置章节分割成多个文件\n") + t("适用于包含章节信息的视频（如YouTube长视频）")
        )
        self.split_chapters_check.toggled.connect(self.on_chapter_master_toggled)
        chapter_group_layout.addWidget(self.split_chapters_check)
        
        # 移除章节快捷按钮
        remove_chapter_label = QLabel(t("快捷移除章节:"))
        chapter_group_layout.addWidget(remove_chapter_label)
        
        remove_chapter_buttons = QHBoxLayout()
        
        self.remove_intro_btn = QPushButton(t("移除开场"))
        self.remove_intro_btn.setCheckable(True)
        self.remove_intro_btn.setToolTip(t("移除视频开场章节（intro、opening等）"))
        self.remove_intro_btn.clicked.connect(self.update_chapter_removal)
        remove_chapter_buttons.addWidget(self.remove_intro_btn)
        
        self.remove_outro_btn = QPushButton(t("移除结尾"))
        self.remove_outro_btn.setCheckable(True)
        self.remove_outro_btn.setToolTip(t("移除视频结尾章节（outro、ending等）"))
        self.remove_outro_btn.clicked.connect(self.update_chapter_removal)
        remove_chapter_buttons.addWidget(self.remove_outro_btn)
        
        self.remove_sponsor_btn = QPushButton(t("移除广告"))
        self.remove_sponsor_btn.setCheckable(True)
        self.remove_sponsor_btn.setToolTip(t("移除赞助商和广告章节（sponsor、ad等）"))
        self.remove_sponsor_btn.clicked.connect(self.update_chapter_removal)
        remove_chapter_buttons.addWidget(self.remove_sponsor_btn)
        
        remove_chapter_buttons.addStretch()
        chapter_group_layout.addLayout(remove_chapter_buttons)
        
        # 自定义章节名称输入
        custom_chapter_layout = QHBoxLayout()
        custom_chapter_layout.addWidget(QLabel(t("其他章节:")))
        self.custom_chapter_input = QLineEdit()
        self.custom_chapter_input.setPlaceholderText(t("输入章节名称，如: recap"))
        self.custom_chapter_input.setToolTip(t("输入要移除的章节名称"))
        custom_chapter_layout.addWidget(self.custom_chapter_input)
        
        self.add_chapter_btn = QPushButton(t("添加"))
        self.add_chapter_btn.setToolTip(t("添加自定义章节到移除列表"))
        self.add_chapter_btn.clicked.connect(self.add_custom_chapter)
        custom_chapter_layout.addWidget(self.add_chapter_btn)
        
        chapter_group_layout.addLayout(custom_chapter_layout)
        
        # 要移除的章节列表
        self.remove_chapters_list = QListWidget()
        self.remove_chapters_list.setMaximumHeight(64)
        self.remove_chapters_list.setToolTip(t("要移除的章节列表，双击可删除"))
        self.remove_chapters_list.itemDoubleClicked.connect(self.remove_chapter_item)
        chapter_group_layout.addWidget(QLabel(t("要移除的章节（双击删除）:")))
        chapter_group_layout.addWidget(self.remove_chapters_list)
        
        # 切割处强制关键帧
        self.force_keyframes_check = QCheckBox(t("切割处强制关键帧"))
        self.force_keyframes_check.setToolTip(
            t("在切割点强制生成关键帧，减少切割处的视觉伪影\n") + t("注意: 需要重新编码，处理速度会较慢")
        )
        chapter_group_layout.addWidget(self.force_keyframes_check)
        
        # 初始化章节移除列表
        self.chapters_to_remove = []
        
        time_section_content_layout.addWidget(chapter_group)
        
        # 初始化章节主控功能状态（默认禁用子选项）
        self.on_chapter_master_toggled(False)
        
        # 在内容布局末尾添加伸缩空间
        time_section_content_layout.addStretch()
        
        # 操作按钮
        buttons_layout = QHBoxLayout()
        
        self.download_button = QPushButton(t("开始下载"))
        self.download_button.clicked.connect(self.start_download)
        buttons_layout.addWidget(self.download_button)
        
        self.cancel_button = QPushButton(t("取消"))
        self.cancel_button.clicked.connect(self.cancel_download)
        self.cancel_button.setEnabled(False)
        buttons_layout.addWidget(self.cancel_button)
        
        # 创建上半部分的控件容器
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        
        # 设置控件容器的大小策略，优先使用最小尺寸
        controls_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        
        # 设置布局间距和边距
        controls_layout.setSpacing(5)
        controls_layout.setContentsMargins(5, 5, 5, 5)
        
        controls_layout.addWidget(url_group)
        controls_layout.addWidget(options_group)
        controls_layout.addWidget(self.advanced_group)
        
        # 添加伸缩空间，让按钮区域在底部
        controls_layout.addStretch()

        download_controls_scroll = QScrollArea()
        download_controls_scroll.setWidgetResizable(True)
        download_controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        download_controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        download_controls_scroll.setFrameShape(QFrame.NoFrame)
        download_controls_scroll.setWidget(controls_widget)
        
        # 进度显示
        progress_group = QGroupBox(t("下载进度"))
        progress_layout = QVBoxLayout()
        progress_group.setLayout(progress_layout)

        progress_layout.addLayout(buttons_layout)
        
        # 添加进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)  # 显示百分比文本
        self.progress_bar.setFormat("%v%")  # 设置显示格式为纯数字百分比
        progress_layout.addWidget(self.progress_bar)
        
        self.progress_text = QTextEdit()
        self.progress_text.setReadOnly(True)
        # 设置最小高度，允许用户调整大小
        self.progress_text.setMinimumHeight(110)
        progress_layout.addWidget(self.progress_text)
        
        # 使用QSplitter分隔控件区域和进度显示区域
        download_splitter = QSplitter(Qt.Vertical)
        download_splitter.addWidget(download_controls_scroll)
        download_splitter.addWidget(progress_group)
        
        # 设置分割器可以调整大小
        download_splitter.setChildrenCollapsible(False)  # 防止子部件被完全折叠
        
        # 设置伸缩策略，让进度区域优先获得额外空间
        download_splitter.setStretchFactor(0, 3)
        download_splitter.setStretchFactor(1, 2)
        
        # 设置初始分割比例，让进度区域占更多空间
        download_splitter.setSizes([560, 220])
        
        download_layout.addWidget(download_splitter)
        
        # 转码选项卡 - 使用支持拖放的自定义Widget
        transcode_tab = TranscodeDropWidget()
        self.tabs.addTab(transcode_tab, t("视频转码"))
        transcode_layout = QVBoxLayout(transcode_tab)
        
        # 连接拖放信号 - 将在后面定义处理方法
        transcode_tab.file_dropped.connect(self._on_transcode_file_dropped)
        
        # 文件选择区域
        file_group = QGroupBox(t("文件选择"))
        file_main_layout = QVBoxLayout()
        file_group.setLayout(file_main_layout)
        file_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        
        # 拖放提示标签
        transcode_drop_hint_label = QLabel(t("📁 拖放视频文件到此处，或点击下方按钮选择"))
        transcode_drop_hint_label.setAlignment(Qt.AlignCenter)
        transcode_drop_hint_label.setStyleSheet(
            "QLabel { color: #666; padding: 10px; border: 2px dashed #ccc; border-radius: 5px; margin-bottom: 5px; }"
        )
        file_main_layout.addWidget(transcode_drop_hint_label)
        
        # 文件输入行
        file_layout = QHBoxLayout()
        self.input_file = QLineEdit()
        self.input_file.setPlaceholderText(t("选择要转码的视频文件..."))
        file_layout.addWidget(self.input_file)
        
        browse_input_button = QPushButton(t("浏览..."))
        browse_input_button.clicked.connect(self.browse_input_file)
        file_layout.addWidget(browse_input_button)
        
        file_main_layout.addLayout(file_layout)  # 将文件输入行添加到主布局
        
        transcode_layout.addWidget(file_group)
        
        # 转码选项区域
        transcode_options_group = QGroupBox(t("转码选项"))
        transcode_options_layout = QFormLayout()
        transcode_options_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        transcode_options_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        transcode_options_layout.setFormAlignment(Qt.AlignTop)
        transcode_options_layout.setVerticalSpacing(6)
        transcode_options_group.setLayout(transcode_options_layout)
        transcode_options_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        
        # 输出格式
        self.output_format = QComboBox()
        self.output_format.addItems(["mp4", "mkv", "avi", "mov", "webm", "mp3", "m4a", "flac"])
        transcode_options_layout.addRow(t("输出格式:"), self.output_format)
        
        # 视频编码器
        self.video_codec = QComboBox()
        self.video_codec.addItems(["h264", "h265", "vp9", "av1", t("复制原始编码")])
        transcode_options_layout.addRow(t("视频编码器:"), self.video_codec)
        
        # 音频编码器
        self.audio_codec = QComboBox()
        self.audio_codec.addItems(["aac", "mp3", "opus", "flac", t("复制原始编码")])
        transcode_options_layout.addRow(t("音频编码器:"), self.audio_codec)
        
        # 视频质量
        quality_layout = QHBoxLayout()
        self.video_quality_slider = QSlider(Qt.Horizontal)
        self.video_quality_slider.setRange(0, 51)
        self.video_quality_slider.setValue(23)
        self.video_quality_slider.setTickPosition(QSlider.TicksBothSides)
        self.video_quality_slider.setTickInterval(1)
        self.video_quality_slider.setSingleStep(1)
        self.video_quality_slider.setPageStep(5)
        try:
            base_h = max(10, self.video_quality_slider.sizeHint().height())
            new_h = int(base_h * 1.4)
            self.video_quality_slider.setFixedHeight(new_h)
            track_h = max(4, int(new_h * 0.22))
            radius = max(2, int(track_h / 2))
            handle_h = max(track_h + 2, int(track_h * 1.2))
            handle_w = max(12, int(handle_h * 0.85))
            handle_radius = max(6, int(min(handle_w, handle_h) / 2))
            handle_margin = -int((handle_h - track_h) / 2)
            style = f"""
            QSlider {{ background: transparent; }}
            QSlider::groove:horizontal {{
                border: none;
                height: {track_h}px;
                background: #e0e0e0;
                border-radius: {radius}px;
            }}
            QSlider::handle:horizontal {{
                background: #ffffff;
                border: 1px solid #b0b0b0;
                width: {handle_w}px;
                height: {handle_h}px;
                margin: {handle_margin}px 0;
                border-radius: {handle_radius}px;
            }}
            QSlider::handle:horizontal:hover {{
                background: #f5f5f5;
                border: 1px solid #9e9e9e;
            }}
            QSlider::handle:horizontal:pressed {{
                background: #eeeeee;
                border: 1px solid #8d8d8d;
            }}
            QSlider::sub-page:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff3e3e,
                    stop:0.16 #ff9933,
                    stop:0.33 #ffea00,
                    stop:0.50 #00bf4f,
                    stop:0.66 #00cfe6,
                    stop:0.83 #3b6ce1,
                    stop:1 #8a3cea);
                border: none;
                border-radius: {radius}px;
            }}
            QSlider::add-page:horizontal {{
                background: #e0e0e0;
                border: none;
                border-radius: {radius}px;
            }}
            """
            self.video_quality_slider.setStyleSheet(style)
        except Exception:
            self.video_quality_slider.setFixedHeight(18)
            self.video_quality_slider.setStyleSheet(
                """
                QSlider { background: transparent; }
                QSlider::groove:horizontal { border: none; height: 6px; background: #e0e0e0; border-radius: 4px; }
                QSlider::handle:horizontal { background: #ffffff; border: 1px solid #b0b0b0; width: 12px; height: 12px; margin: -3px 0; border-radius: 6px; }
                QSlider::handle:horizontal:hover { background: #f5f5f5; border: 1px solid #9e9e9e; }
                QSlider::handle:horizontal:pressed { background: #eeeeee; border: 1px solid #8d8d8d; }
                QSlider::sub-page:horizontal { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff3e3e, stop:0.16 #ff9933, stop:0.33 #ffea00, stop:0.50 #00bf4f, stop:0.66 #00cfe6, stop:0.83 #3b6ce1, stop:1 #8a3cea); border: none; border-radius: 4px; }
                QSlider::add-page:horizontal { background: #e0e0e0; border: none; border-radius: 4px; }
                """
            )
        
        self.quality_label = QLabel("23")
        self.video_quality_slider.valueChanged.connect(lambda v: self.quality_label.setText(str(v)))
        
        quality_layout.addWidget(QLabel(t("高质量")))
        quality_layout.addWidget(self.video_quality_slider)
        quality_layout.addWidget(QLabel(t("低质量")))
        quality_layout.addWidget(self.quality_label)
        try:
            quality_layout.setStretch(1, 2)
        except Exception:
            pass
        
        transcode_options_layout.addRow(t("视频质量 (CRF):"), quality_layout)
        
        # 转码速度预设
        self.speed_preset = QComboBox()
        self.speed_preset.addItems([t("快速"), t("正常"), t("高质量")])
        self.speed_preset.setCurrentText(t("正常"))  # 默认正常预设
        self.speed_preset.setToolTip(t("转码速度预设：快速转码快但质量一般，正常平衡速度和质量，高质量转码慢但质量最佳"))
        transcode_options_layout.addRow(t("转码速度:"), self.speed_preset)
        
        # 线程数控制
        threads_layout = QHBoxLayout()
        self.threads_spinbox = QSpinBox()
        self.threads_spinbox.setRange(0, 32)
        self.threads_spinbox.setValue(0)  # 默认0表示自动
        self.threads_spinbox.setSpecialValueText(t("自动"))
        threads_layout.addWidget(self.threads_spinbox)
        threads_layout.addWidget(QLabel(t("(0表示自动)")))
        transcode_options_layout.addRow(t("线程数:"), threads_layout)
        
        # GPU加速选项
        gpu_layout = QHBoxLayout()
        self.gpu_checkbox = QCheckBox(t("启用GPU加速"))
        self.gpu_checkbox.setToolTip(t("使用GPU硬件加速编码，可以大幅提高转码速度"))
        gpu_layout.addWidget(self.gpu_checkbox)
        
        self.gpu_type_combo = QComboBox()
        self.gpu_type_combo.addItems([t("自动检测"), "NVIDIA (NVENC)", "AMD (AMF)"])
        self.gpu_type_combo.setCurrentIndex(0)
        self.gpu_type_combo.setEnabled(False)  # 默认禁用，只有选中复选框才启用
        self.gpu_checkbox.toggled.connect(self.gpu_type_combo.setEnabled)  # 连接信号
        gpu_layout.addWidget(self.gpu_type_combo)
        
        transcode_options_layout.addRow(t("硬件加速:"), gpu_layout)
        
        # 输出文件
        output_file_layout = QHBoxLayout()
        self.output_file = QLineEdit()
        self.output_file.setPlaceholderText(t("输出文件路径..."))
        output_file_layout.addWidget(self.output_file)
        
        browse_output_button = QPushButton(t("浏览..."))
        browse_output_button.clicked.connect(self.browse_output_file)
        output_file_layout.addWidget(browse_output_button)
        
        transcode_options_layout.addRow(t("输出文件:"), output_file_layout)
        
        transcode_layout.addWidget(transcode_options_group)
        
        # 转码操作按钮
        transcode_buttons_layout = QHBoxLayout()
        
        self.transcode_button = QPushButton(t("开始转码"))
        self.transcode_button.clicked.connect(self.start_transcode)
        transcode_buttons_layout.addWidget(self.transcode_button)
        
        self.cancel_transcode_button = QPushButton(t("取消"))
        self.cancel_transcode_button.clicked.connect(self.cancel_transcode)
        self.cancel_transcode_button.setEnabled(False)
        transcode_buttons_layout.addWidget(self.cancel_transcode_button)
        
        # 创建转码控件容器
        transcode_controls_widget = QWidget()
        transcode_controls_layout = QVBoxLayout(transcode_controls_widget)
        transcode_controls_layout.setContentsMargins(0, 0, 0, 0)
        transcode_controls_layout.setSpacing(8)
        transcode_controls_layout.addWidget(file_group)
        transcode_controls_layout.addWidget(transcode_options_group)
        transcode_controls_layout.setAlignment(Qt.AlignTop)
        transcode_controls_layout.setStretch(0, 0)
        transcode_controls_layout.setStretch(1, 0)
        transcode_controls_layout.addStretch(1)

        transcode_controls_scroll = QScrollArea()
        transcode_controls_scroll.setWidgetResizable(True)
        transcode_controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        transcode_controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        transcode_controls_scroll.setFrameShape(QFrame.NoFrame)
        transcode_controls_scroll.setAlignment(Qt.AlignTop)
        transcode_controls_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        transcode_controls_scroll.setWidget(transcode_controls_widget)
        
        # 转码进度显示
        transcode_progress_group = QGroupBox(t("转码进度"))
        transcode_progress_layout = QVBoxLayout()
        transcode_progress_layout.setObjectName("transcode_progress_layout")  # 设置布局名称以便后续查找
        transcode_progress_group.setLayout(transcode_progress_layout)
        transcode_progress_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        transcode_progress_layout.addLayout(transcode_buttons_layout)

        self.transcode_progress_bar = QProgressBar()
        self.transcode_progress_bar.setRange(0, 100)
        self.transcode_progress_bar.setValue(0)
        self.transcode_progress_bar.setFormat("0%")
        self.transcode_progress_bar.setStyleSheet("")
        transcode_progress_layout.addWidget(self.transcode_progress_bar)
        
        self.transcode_progress_text = QTextEdit()
        self.transcode_progress_text.setReadOnly(True)
        self.transcode_progress_text.setMinimumHeight(110)
        transcode_progress_layout.addWidget(self.transcode_progress_text)

        transcode_splitter = QSplitter(Qt.Vertical)
        transcode_splitter.addWidget(transcode_controls_scroll)
        transcode_splitter.addWidget(transcode_progress_group)
        transcode_splitter.setChildrenCollapsible(False)
        transcode_splitter.setStretchFactor(0, 0)
        transcode_splitter.setStretchFactor(1, 1)
        transcode_splitter.setSizes([540, 220])
        self.transcode_splitter = transcode_splitter
        self.transcode_controls_scroll = transcode_controls_scroll
        self.transcode_progress_group = transcode_progress_group
        
        transcode_layout.addWidget(transcode_splitter)
        
        # ========== 图片处理选项卡 ==========
        image_tab = QWidget()
        image_tab.setAcceptDrops(True)  # 启用整个标签页的拖放
        self.tabs.addTab(image_tab, t("图片处理"))
        image_layout = QVBoxLayout(image_tab)
        
        # 图片文件选择区域
        image_file_group = QGroupBox(t("文件选择"))
        image_file_layout = QVBoxLayout()
        image_file_layout.setContentsMargins(8, 8, 8, 8)
        image_file_layout.setSpacing(6)
        image_file_group.setLayout(image_file_layout)
        image_file_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        
        # 拖放提示标签
        drop_hint_label = QLabel(t("📁 拖放图片文件到此处，或点击下方按钮添加"))
        drop_hint_label.setAlignment(Qt.AlignCenter)
        drop_hint_label.setStyleSheet(
            "QLabel { color: #666; padding: 6px; border: 2px dashed #ccc; border-radius: 5px; margin-bottom: 2px; }"
        )
        image_file_layout.addWidget(drop_hint_label)
        
        # 文件列表工具栏
        image_toolbar = QHBoxLayout()
        
        add_image_btn = QPushButton(t("添加图片"))
        add_image_btn.setToolTip(t("选择要处理的图片文件"))
        add_image_btn.clicked.connect(self.browse_image_files)
        image_toolbar.addWidget(add_image_btn)
        
        remove_image_btn = QPushButton(t("移除选中"))
        remove_image_btn.setToolTip(t("移除选中的图片"))
        remove_image_btn.clicked.connect(self.remove_selected_images)
        image_toolbar.addWidget(remove_image_btn)
        
        clear_image_btn = QPushButton(t("清空列表"))
        clear_image_btn.setToolTip(t("清空所有图片"))
        clear_image_btn.clicked.connect(self.clear_image_list)
        image_toolbar.addWidget(clear_image_btn)
        
        image_toolbar.addStretch()
        
        # 图片数量标签
        self.image_count_label = QLabel(t("已添加 0 张图片"))
        image_toolbar.addWidget(self.image_count_label)
        
        image_file_layout.addLayout(image_toolbar)
        
        # 文件列表
        self.image_list_widget = QListWidget()
        self.image_list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.image_list_widget.setMinimumHeight(72)
        self.image_list_widget.setMaximumHeight(120)
        self.image_list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.image_list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.image_list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.image_list_widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.image_list_widget.setToolTip(t("支持拖放图片文件到此处"))
        image_file_layout.addWidget(self.image_list_widget)
        
        image_layout.addWidget(image_file_group)
        
        # 图片处理选项区域
        image_options_group = QGroupBox(t("处理选项"))
        image_options_layout = QFormLayout()
        image_options_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        image_options_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        image_options_layout.setFormAlignment(Qt.AlignTop)
        image_options_layout.setVerticalSpacing(6)
        image_options_group.setLayout(image_options_layout)
        image_options_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        
        # 输出格式
        self.image_format_combo = QComboBox()
        self.image_format_combo.addItems(["JPEG", "PNG", "WebP", "BMP", "GIF", "TIFF", "ICO"])
        self.image_format_combo.setToolTip(t("选择输出图片格式"))
        self.image_format_combo.currentTextChanged.connect(self.on_image_format_changed)
        image_options_layout.addRow(t("输出格式:"), self.image_format_combo)
        
        # 质量/压缩设置（根据格式动态显示）
        self.quality_row_widget = QWidget()
        quality_row_layout = QHBoxLayout(self.quality_row_widget)
        quality_row_layout.setContentsMargins(0, 0, 0, 0)
        
        # 质量滑块（JPEG/WebP等格式使用）
        self.image_quality_slider = QSlider(Qt.Horizontal)
        self.image_quality_slider.setRange(1, 100)
        self.image_quality_slider.setValue(85)
        self.image_quality_slider.setTickPosition(QSlider.TicksBothSides)
        self.image_quality_slider.setTickInterval(10)
        self.image_quality_slider.setSingleStep(1)
        self.image_quality_slider.setPageStep(10)
        self.image_quality_slider.setToolTip(t("图片质量 (1-100)，值越高质量越好文件越大"))
        self.image_quality_slider.setFixedHeight(18)
        self.image_quality_slider.setStyleSheet(
            """
            QSlider { background: transparent; }
            QSlider::groove:horizontal { border: none; height: 6px; background: #e0e0e0; border-radius: 3px; }
            QSlider::handle:horizontal { background: #ffffff; border: 1px solid #b0b0b0; width: 14px; height: 14px; margin: -4px 0; border-radius: 7px; }
            QSlider::handle:horizontal:hover { background: #f5f5f5; border: 1px solid #9e9e9e; }
            QSlider::handle:horizontal:pressed { background: #eeeeee; border: 1px solid #8d8d8d; }
            QSlider::sub-page:horizontal { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff3e3e, stop:0.16 #ff9933, stop:0.33 #ffea00, stop:0.50 #00bf4f, stop:0.66 #00cfe6, stop:0.83 #3b6ce1, stop:1 #8a3cea); border: none; border-radius: 3px; }
            QSlider::add-page:horizontal { background: #e0e0e0; border: none; border-radius: 3px; }
            """
        )
        self.image_quality_label = QLabel("85")
        self.image_quality_slider.valueChanged.connect(lambda v: self.image_quality_label.setText(str(v)))
        
        self.quality_low_label = QLabel(t("低"))
        self.quality_high_label = QLabel(t("高"))
        quality_row_layout.addWidget(self.quality_low_label)
        quality_row_layout.addWidget(self.image_quality_slider)
        quality_row_layout.addWidget(self.quality_high_label)
        quality_row_layout.addWidget(self.image_quality_label)
        quality_row_layout.setStretch(1, 2)
        
        # 动态标签（根据格式变化）
        self.quality_label = QLabel(t("压缩质量:"))
        image_options_layout.addRow(self.quality_label, self.quality_row_widget)
        
        # PNG压缩级别滑块（隐藏，与质量滑块共用位置）
        self.png_compress_slider = QSlider(Qt.Horizontal)
        self.png_compress_slider.setRange(0, 9)
        self.png_compress_slider.setValue(5)
        self.png_compress_slider.setTickPosition(QSlider.TicksBothSides)
        self.png_compress_slider.setTickInterval(1)
        self.png_compress_slider.setSingleStep(1)
        self.png_compress_slider.setPageStep(1)
        self.png_compress_slider.setToolTip(t("PNG压缩级别 (0-9)，值越高压缩率越高但速度越慢（无损压缩）"))
        self.png_compress_slider.setFixedHeight(18)
        self.png_compress_slider.setStyleSheet(
            """
            QSlider { background: transparent; }
            QSlider::groove:horizontal { border: none; height: 6px; background: #e0e0e0; border-radius: 3px; }
            QSlider::handle:horizontal { background: #ffffff; border: 1px solid #b0b0b0; width: 14px; height: 14px; margin: -4px 0; border-radius: 7px; }
            QSlider::handle:horizontal:hover { background: #f5f5f5; border: 1px solid #9e9e9e; }
            QSlider::handle:horizontal:pressed { background: #eeeeee; border: 1px solid #8d8d8d; }
            QSlider::sub-page:horizontal { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff3e3e, stop:0.16 #ff9933, stop:0.33 #ffea00, stop:0.50 #00bf4f, stop:0.66 #00cfe6, stop:0.83 #3b6ce1, stop:1 #8a3cea); border: none; border-radius: 3px; }
            QSlider::add-page:horizontal { background: #e0e0e0; border: none; border-radius: 3px; }
            """
        )
        self.png_compress_label = QLabel("5")
        self.png_compress_slider.valueChanged.connect(lambda v: self.png_compress_label.setText(str(v)))
        # PNG滑块默认隐藏
        self.png_compress_slider.hide()
        self.png_compress_label.hide()
        
        # 尺寸调整
        resize_layout = QHBoxLayout()
        self.resize_mode_combo = QComboBox()
        self.resize_mode_combo.addItems([t("不调整"), t("按百分比"), t("固定尺寸"), t("最大边长")])
        self.resize_mode_combo.setToolTip(t("选择尺寸调整模式"))
        self.resize_mode_combo.currentTextChanged.connect(self.on_resize_mode_changed)
        resize_layout.addWidget(self.resize_mode_combo)
        
        # 百分比输入
        self.resize_percent_widget = QWidget()
        percent_layout = QHBoxLayout(self.resize_percent_widget)
        percent_layout.setContentsMargins(0, 0, 0, 0)
        self.resize_percent_spin = QSpinBox()
        self.resize_percent_spin.setRange(1, 500)
        self.resize_percent_spin.setValue(50)
        self.resize_percent_spin.setSuffix("%")
        percent_layout.addWidget(QLabel(t("缩放:")))
        percent_layout.addWidget(self.resize_percent_spin)
        self.resize_percent_widget.hide()
        resize_layout.addWidget(self.resize_percent_widget)
        
        # 固定尺寸输入
        self.resize_fixed_widget = QWidget()
        fixed_layout = QHBoxLayout(self.resize_fixed_widget)
        fixed_layout.setContentsMargins(0, 0, 0, 0)
        self.resize_width_spin = QSpinBox()
        self.resize_width_spin.setRange(1, 10000)
        self.resize_width_spin.setValue(1920)
        self.resize_height_spin = QSpinBox()
        self.resize_height_spin.setRange(1, 10000)
        self.resize_height_spin.setValue(1080)
        self.keep_ratio_check = QCheckBox(t("保持比例"))
        self.keep_ratio_check.setChecked(True)
        fixed_layout.addWidget(QLabel(t("宽:")))
        fixed_layout.addWidget(self.resize_width_spin)
        fixed_layout.addWidget(QLabel(t("高:")))
        fixed_layout.addWidget(self.resize_height_spin)
        fixed_layout.addWidget(self.keep_ratio_check)
        self.resize_fixed_widget.hide()
        resize_layout.addWidget(self.resize_fixed_widget)
        
        # 最大边长输入
        self.resize_maxside_widget = QWidget()
        maxside_layout = QHBoxLayout(self.resize_maxside_widget)
        maxside_layout.setContentsMargins(0, 0, 0, 0)
        self.resize_maxside_spin = QSpinBox()
        self.resize_maxside_spin.setRange(1, 10000)
        self.resize_maxside_spin.setValue(1920)
        self.resize_maxside_spin.setSuffix(" px")
        maxside_layout.addWidget(QLabel(t("最大:")))
        maxside_layout.addWidget(self.resize_maxside_spin)
        self.resize_maxside_widget.hide()
        resize_layout.addWidget(self.resize_maxside_widget)
        
        image_options_layout.addRow(t("尺寸调整:"), resize_layout)
        
        # 采样方式
        self.resample_combo = QComboBox()
        self.resample_combo.addItems([t("Lanczos (最佳质量)"), t("双三次"), t("双线性"), t("最近邻 (最快)")])
        self.resample_combo.setToolTip(t("图片缩放时的采样算法"))
        image_options_layout.addRow(t("采样方式:"), self.resample_combo)
        
        # 输出目录
        output_dir_layout = QHBoxLayout()
        self.image_output_dir = QLineEdit()
        self.image_output_dir.setPlaceholderText(t("选择输出目录（留空则保存到源文件目录）..."))
        output_dir_layout.addWidget(self.image_output_dir)
        browse_output_dir_btn = QPushButton(t("浏览..."))
        browse_output_dir_btn.clicked.connect(self.browse_image_output_dir)
        output_dir_layout.addWidget(browse_output_dir_btn)
        image_options_layout.addRow(t("输出目录:"), output_dir_layout)
        
        # 文件名后缀
        self.image_suffix_input = QLineEdit()
        self.image_suffix_input.setPlaceholderText(t("例如: _compressed"))
        self.image_suffix_input.setToolTip(t("添加到输出文件名的后缀，留空则使用原文件名"))
        image_options_layout.addRow(t("文件名后缀:"), self.image_suffix_input)
        
        # 覆盖选项
        self.image_overwrite_check = QCheckBox(t("覆盖已存在的文件"))
        self.image_overwrite_check.setToolTip(t("如果输出文件已存在，是否覆盖"))
        image_options_layout.addRow("", self.image_overwrite_check)
        
        image_layout.addWidget(image_options_group)
        
        # 图片处理按钮（与转码按钮风格一致）
        image_buttons_layout = QHBoxLayout()
        
        self.start_image_process_btn = QPushButton(t("开始处理"))
        self.start_image_process_btn.clicked.connect(self.start_image_process)
        image_buttons_layout.addWidget(self.start_image_process_btn)
        
        self.cancel_image_process_btn = QPushButton(t("取消"))
        self.cancel_image_process_btn.clicked.connect(self.cancel_image_process)
        self.cancel_image_process_btn.setEnabled(False)
        image_buttons_layout.addWidget(self.cancel_image_process_btn)
        
        # 创建图片处理控件容器
        image_controls_widget = QWidget()
        image_controls_layout = QVBoxLayout(image_controls_widget)
        image_controls_layout.setContentsMargins(0, 0, 0, 0)
        image_controls_layout.setSpacing(8)
        image_controls_layout.addWidget(image_file_group)
        image_controls_layout.addWidget(image_options_group)
        image_controls_layout.setAlignment(Qt.AlignTop)
        image_controls_layout.setStretch(0, 0)
        image_controls_layout.setStretch(1, 0)
        image_controls_layout.addStretch(1)

        image_controls_scroll = QScrollArea()
        image_controls_scroll.setWidgetResizable(True)
        image_controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        image_controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        image_controls_scroll.setFrameShape(QFrame.NoFrame)
        image_controls_scroll.setAlignment(Qt.AlignTop)
        image_controls_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        image_controls_scroll.setWidget(image_controls_widget)
        
        # 图片处理进度显示
        image_progress_group = QGroupBox(t("处理进度"))
        image_progress_layout = QVBoxLayout()
        image_progress_group.setLayout(image_progress_layout)
        image_progress_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        image_progress_layout.addLayout(image_buttons_layout)
        
        # 进度条
        self.image_progress_bar = QProgressBar()
        self.image_progress_bar.setRange(0, 100)
        self.image_progress_bar.setValue(0)
        self.image_progress_bar.setTextVisible(True)
        self.image_progress_bar.setFormat("%v%")
        image_progress_layout.addWidget(self.image_progress_bar)
        
        # 日志文本
        self.image_progress_text = QTextEdit()
        self.image_progress_text.setReadOnly(True)
        self.image_progress_text.setMinimumHeight(96)
        image_progress_layout.addWidget(self.image_progress_text)

        image_splitter = QSplitter(Qt.Vertical)
        image_splitter.addWidget(image_controls_scroll)
        image_splitter.addWidget(image_progress_group)
        image_splitter.setChildrenCollapsible(False)
        image_splitter.setStretchFactor(0, 0)
        image_splitter.setStretchFactor(1, 1)
        image_splitter.setSizes([520, 220])
        self.image_splitter = image_splitter
        self.image_controls_scroll = image_controls_scroll
        self.image_progress_group = image_progress_group
        
        image_layout.addWidget(image_splitter)
        
        # 初始化图片处理线程变量
        self.image_process_thread = None
        self.image_file_list = []

        # ========== 画质修复 选项卡 ==========
        realesrgan_tab = QWidget()
        realesrgan_tab.setAcceptDrops(True)
        self.tabs.addTab(realesrgan_tab, t("画质修复"))
        realesrgan_layout = QVBoxLayout(realesrgan_tab)

        realesrgan_file_group = QGroupBox(t("文件选择"))
        realesrgan_file_group.setToolTip(t("先把要修复的图片或视频加到这里，再开始批量处理。"))
        realesrgan_file_layout = QVBoxLayout()
        realesrgan_file_layout.setContentsMargins(8, 8, 8, 8)
        realesrgan_file_layout.setSpacing(6)
        realesrgan_file_group.setLayout(realesrgan_file_layout)
        realesrgan_file_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        realesrgan_drop_hint = QLabel(t("📁 支持图片和视频批量修复。选中特定模型后将自动按需下载模型组件。"))
        realesrgan_drop_hint.setAlignment(Qt.AlignCenter)
        realesrgan_drop_hint.setToolTip(t("可直接拖入图片或视频，也可以点击下方按钮手动添加。"))
        realesrgan_drop_hint.setStyleSheet(
            "QLabel { color: #666; padding: 6px; border: 2px dashed #ccc; border-radius: 5px; margin-bottom: 2px; }"
        )
        realesrgan_file_layout.addWidget(realesrgan_drop_hint)

        realesrgan_toolbar = QHBoxLayout()
        add_realesrgan_btn = QPushButton(t("添加媒体"))
        add_realesrgan_btn.setToolTip(t("选择本地图片或视频，加入当前待修复列表。"))
        add_realesrgan_btn.clicked.connect(self.browse_realesrgan_files)
        realesrgan_toolbar.addWidget(add_realesrgan_btn)

        remove_realesrgan_btn = QPushButton(t("移除选中"))
        remove_realesrgan_btn.setToolTip(t("移除当前选中的文件，不会删除原文件。"))
        remove_realesrgan_btn.clicked.connect(self.remove_selected_realesrgan_files)
        realesrgan_toolbar.addWidget(remove_realesrgan_btn)

        clear_realesrgan_btn = QPushButton(t("清空列表"))
        clear_realesrgan_btn.setToolTip(t("清空整个待修复列表，不会删除磁盘上的原文件。"))
        clear_realesrgan_btn.clicked.connect(self.clear_realesrgan_list)
        realesrgan_toolbar.addWidget(clear_realesrgan_btn)

        realesrgan_toolbar.addStretch()

        self.realesrgan_count_label = QLabel(t("已添加 0 个文件"))
        self.realesrgan_count_label.setToolTip(t("显示当前已加入待修复队列的文件数量。"))
        realesrgan_toolbar.addWidget(self.realesrgan_count_label)
        realesrgan_file_layout.addLayout(realesrgan_toolbar)

        self.realesrgan_list_widget = QListWidget()
        self.realesrgan_list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.realesrgan_list_widget.setMinimumHeight(60)
        self.realesrgan_list_widget.setMaximumHeight(120)
        self.realesrgan_list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.realesrgan_list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.realesrgan_list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.realesrgan_list_widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.realesrgan_list_widget.setToolTip(t("这里显示待修复文件。可多选后移除，也可直接拖入新文件。"))
        realesrgan_file_layout.addWidget(self.realesrgan_list_widget)
        realesrgan_layout.addWidget(realesrgan_file_group)

        realesrgan_options_group = QGroupBox(t("修复方案"))
        realesrgan_options_group.setToolTip(t("选择修复场景和输出方式。大多数情况下只需要选场景和倍率。"))
        realesrgan_options_layout = QFormLayout()
        realesrgan_options_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        realesrgan_options_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        realesrgan_options_group.setLayout(realesrgan_options_layout)

        self.realesrgan_preset_combo = QComboBox()
        self.realesrgan_preset_combo.addItem(t("通用修复"), "general")
        self.realesrgan_preset_combo.addItem(t("人像修复"), "portrait")
        self.realesrgan_preset_combo.addItem(t("老照片修复"), "old_photo")
        self.realesrgan_preset_combo.addItem(t("动漫图片修复"), "anime_image")
        self.realesrgan_preset_combo.addItem(t("动漫视频修复"), "anime_video")
        self.realesrgan_preset_combo.addItem(t("低清图片强修复"), "strong_photo")
        self._realesrgan_preset_map = {
            "general": (
                "适合普通照片、截图、商品图和大多数日常视频画面。",
                "realesr-general-x4v3",
                False,
                0.5,
                "适合手机照片、截图、商品图和大多数日常素材。",
                "如果画面本身是动漫，边缘和线条通常不如动漫专用模型稳定。",
            ),
            "portrait": (
                "适合自拍、证件照、人物特写等以人脸为主体的画面。",
                "realesr-general-x4v3",
                True,
                0.4,
                "适合自拍、证件照、人物特写，以及脸部占画面较大的照片。",
                "如果画面里没有清晰人脸，额外的人脸增强收益通常不明显。",
            ),
            "old_photo": (
                "适合老照片、模糊人像和退化较重的旧图。",
                "RealESRGAN_x4plus",
                True,
                1.0,
                "适合老照片、模糊人像、噪点较多或退化较重的旧图。",
                "修复力度更强，部分图片可能出现偏“过修复”的感觉。",
            ),
            "anime_image": (
                "适合漫画、立绘、二次元插画等动漫图片。",
                "RealESRGAN_x4plus_anime_6B",
                False,
                1.0,
                "适合漫画、立绘、二次元插画、游戏 CG 等动漫图片。",
                "如果拿它修真人照片，细节风格往往会不自然。",
            ),
            "anime_video": (
                "适合番剧、动画短片、二次元剪辑和动漫游戏录像。",
                "realesr-animevideov3",
                False,
                1.0,
                "适合番剧、动画短片、二次元剪辑、动漫游戏录像。",
                "如果是实拍视频，通常应优先选通用修复而不是这个方案。",
            ),
            "strong_photo": (
                "适合尺寸很小、压缩很重、希望尽量拉清晰的图片。",
                "RealESRGAN_x4plus",
                False,
                1.0,
                "适合尺寸很小、压缩很重、想尽量拉清晰的图片。",
                "处理更重、更慢；普通照片若原图质量还可以，优先试通用修复。",
            ),
        }
        for preset_index in range(self.realesrgan_preset_combo.count()):
            preset_key = self.realesrgan_preset_combo.itemData(preset_index)
            preset_desc = self._realesrgan_preset_map.get(preset_key, self._realesrgan_preset_map["general"])[0]
            self.realesrgan_preset_combo.setItemData(preset_index, preset_desc, Qt.ToolTipRole)
        self.realesrgan_preset_combo.setToolTip(t("按内容类型选择修复方案。真人和实拍优先选通用修复，动漫内容选动漫方案。"))
        self.realesrgan_preset_combo.currentIndexChanged.connect(self.on_realesrgan_preset_changed)
        realesrgan_options_layout.addRow(t("修复场景:"), self.realesrgan_preset_combo)

        self.realesrgan_scale_combo = QComboBox()
        self.realesrgan_scale_combo.addItem(t("2 倍（更快，适合日常）"), 2.0)
        self.realesrgan_scale_combo.addItem(t("4 倍（更清晰，更慢）"), 4.0)
        self.realesrgan_scale_combo.setCurrentIndex(1)
        self.realesrgan_scale_combo.setToolTip(t("决定输出放大倍率。2 倍更快更稳，4 倍更清晰但更耗时。"))
        realesrgan_options_layout.addRow(t("放大倍率:"), self.realesrgan_scale_combo)

        self.realesrgan_runtime_hint = QLabel("")
        self.realesrgan_runtime_hint.setWordWrap(True)
        self.realesrgan_runtime_hint.setStyleSheet("color: #666;")
        self.realesrgan_runtime_hint.setToolTip(t("显示当前修复引擎、模型和可用状态。"))
        realesrgan_options_layout.addRow(t("状态:"), self.realesrgan_runtime_hint)

        self.realesrgan_proxy_notice = QLabel(t("💡 第一次使用需下载所需模型 必须开启代理"))
        self.realesrgan_proxy_notice.setWordWrap(True)
        self.realesrgan_proxy_notice.setStyleSheet(
            "color: #a07d4d; border: none; background: transparent; "
            "padding: 0px; font-weight: bold;"
        )
        self.realesrgan_proxy_notice.setToolTip(t("画质修复由于包体积精简，首次使用特定模型时会自动从云端高速补全。"))
        realesrgan_options_layout.addRow(t("提示:"), self.realesrgan_proxy_notice)

        self.realesrgan_speed_hint = QLabel(t("速度信息会在处理开始后显示"))
        self.realesrgan_speed_hint.setWordWrap(True)
        self.realesrgan_speed_hint.setStyleSheet("color: #666;")
        self.realesrgan_speed_hint.setToolTip(t("处理过程中会显示实时速度和预计剩余时间。"))
        realesrgan_options_layout.addRow(t("速度:"), self.realesrgan_speed_hint)

        self.realesrgan_advanced_group = QGroupBox(t("高级设置（通常不需要改）"))
        self.realesrgan_advanced_group.setCheckable(True)
        self.realesrgan_advanced_group.setChecked(False)
        self.realesrgan_advanced_group.setToolTip(t("只有在你明确知道要调什么时再展开。默认设置已经适合大多数用户。"))
        self.realesrgan_advanced_group.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid #cdbba3;
                border-radius: 8px;
                margin-top: 14px;
                padding: 12px 10px 10px 10px;
                background-color: #fcf8f1;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 2px 10px;
                color: #8a5a00;
                background-color: #f7ead7;
                border: 1px solid #d7bf9b;
                border-radius: 10px;
                font-weight: bold;
            }
            """
        )
        self.realesrgan_advanced_group.toggled.connect(self._on_realesrgan_advanced_group_toggled)
        realesrgan_advanced_layout = QFormLayout()
        realesrgan_advanced_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        realesrgan_advanced_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.realesrgan_advanced_group.setLayout(realesrgan_advanced_layout)

        self.realesrgan_device_combo = QComboBox()
        self.realesrgan_device_combo.addItem(t("自动检测硬件（推荐）"), "auto")
        self.realesrgan_device_combo.addItem(t("仅用 CPU"), "cpu")
        self.realesrgan_device_combo.setToolTip(t("默认自动检测即可。程序会按当前机器和模型自动选择更快的视频后端，并缓存上次测得的更优路径；异常时会自动切换到更稳的后备方案。"))
        self.realesrgan_device_combo.currentIndexChanged.connect(lambda _=0: self.check_realesrgan_runtime(silent=True))
        realesrgan_advanced_layout.addRow(t("运算设备:"), self.realesrgan_device_combo)

        self.realesrgan_force_model_check = QCheckBox(t("手动指定底层模型"))
        self.realesrgan_force_model_check.setToolTip(t("默认会跟随修复场景自动选模型。只有需要精细控制时再手动指定。"))
        self.realesrgan_model_combo = QComboBox()
        self.realesrgan_model_combo.addItem(t("通用小模型（realesr-general-x4v3）"), "realesr-general-x4v3")
        self.realesrgan_model_combo.addItem(t("通用强修复（RealESRGAN_x4plus）"), "RealESRGAN_x4plus")
        self.realesrgan_model_combo.addItem(t("通用 2 倍模型（RealESRGAN_x2plus）"), "RealESRGAN_x2plus")
        self.realesrgan_model_combo.addItem(t("通用平滑模型（RealESRNet_x4plus）"), "RealESRNet_x4plus")
        self.realesrgan_model_combo.addItem(t("动漫图片模型（RealESRGAN_x4plus_anime_6B）"), "RealESRGAN_x4plus_anime_6B")
        self.realesrgan_model_combo.addItem(t("动漫视频模型（realesr-animevideov3）"), "realesr-animevideov3")
        self._realesrgan_model_tips = {
            "realesr-general-x4v3": "官方通用小模型，占用更低、速度更快，适合大多数日常图像场景。",
            "RealESRGAN_x4plus": "官方通用 X4 模型，修复更强，适合低清、压缩重或退化明显的图片。",
            "RealESRGAN_x2plus": "官方通用 X2 模型，适合只想放大 2 倍、速度和体积更平衡的场景。",
            "RealESRNet_x4plus": "官方通用 X4 模型的偏平滑版本，细节更稳，但更容易偏柔和。",
            "RealESRGAN_x4plus_anime_6B": "官方动漫图片模型，适合漫画、立绘、插画等二次元图像。",
            "realesr-animevideov3": "官方动漫视频模型，适合番剧、动画短片和二次元视频。",
        }
        for model_index in range(self.realesrgan_model_combo.count()):
            model_key = self.realesrgan_model_combo.itemData(model_index)
            self.realesrgan_model_combo.setItemData(
                model_index,
                self._realesrgan_model_tips.get(model_key, ""),
                Qt.ToolTipRole,
            )
        self.realesrgan_model_combo.setEnabled(False)
        self.realesrgan_force_model_check.toggled.connect(self._on_realesrgan_force_model_toggled)
        self.realesrgan_model_combo.currentIndexChanged.connect(self._on_realesrgan_model_selection_changed)
        self.realesrgan_model_row = QWidget()
        self.realesrgan_model_row_layout = QHBoxLayout(self.realesrgan_model_row)
        self.realesrgan_model_row_layout.setContentsMargins(0, 0, 0, 0)
        self.realesrgan_model_row_layout.setSpacing(8)
        self.realesrgan_model_row_layout.addWidget(self.realesrgan_force_model_check)
        self.realesrgan_model_row_layout.addWidget(self.realesrgan_model_combo, 1)
        realesrgan_advanced_layout.addRow(t("底层模型:"), self.realesrgan_model_row)

        self.realesrgan_custom_scale_check = QCheckBox(t("使用自定义倍率"))
        self.realesrgan_custom_scale_check.setToolTip(t("默认提供 2 倍和 4 倍。只有你需要更细的倍率时再开启。"))
        self.realesrgan_custom_scale_spin = QDoubleSpinBox()
        self.realesrgan_custom_scale_spin.setRange(1.0, 4.0)
        self.realesrgan_custom_scale_spin.setSingleStep(0.1)
        self.realesrgan_custom_scale_spin.setDecimals(1)
        self.realesrgan_custom_scale_spin.setValue(4.0)
        self.realesrgan_custom_scale_spin.setEnabled(False)
        self.realesrgan_custom_scale_spin.setToolTip(t("自定义输出倍率。大多数情况保留默认即可。"))
        self.realesrgan_custom_scale_check.toggled.connect(self._refresh_realesrgan_model_state)
        self.realesrgan_scale_override_row = QWidget()
        self.realesrgan_scale_override_layout = QHBoxLayout(self.realesrgan_scale_override_row)
        self.realesrgan_scale_override_layout.setContentsMargins(0, 0, 0, 0)
        self.realesrgan_scale_override_layout.setSpacing(8)
        self.realesrgan_scale_override_layout.addWidget(self.realesrgan_custom_scale_check)
        self.realesrgan_scale_override_layout.addWidget(self.realesrgan_custom_scale_spin, 1)
        realesrgan_advanced_layout.addRow(t("自定义倍率:"), self.realesrgan_scale_override_row)

        self.realesrgan_denoise_spin = QDoubleSpinBox()
        self.realesrgan_denoise_spin.setRange(0.0, 1.0)
        self.realesrgan_denoise_spin.setSingleStep(0.1)
        self.realesrgan_denoise_spin.setDecimals(1)
        self.realesrgan_denoise_spin.setValue(0.5)
        self.realesrgan_denoise_spin.setToolTip(t("仅对通用小模型 realesr-general-x4v3 有效，用于平衡降噪和细节保留。"))
        realesrgan_advanced_layout.addRow(t("降噪强度:"), self.realesrgan_denoise_spin)

        self.realesrgan_denoise_hint = QLabel("")
        self.realesrgan_denoise_hint.setWordWrap(True)
        self.realesrgan_denoise_hint.setStyleSheet("color: #666;")
        realesrgan_advanced_layout.addRow("", self.realesrgan_denoise_hint)

        self.realesrgan_output_format_combo = QComboBox()
        self.realesrgan_output_format_combo.addItem(t("自动保持原格式"), "auto")
        self.realesrgan_output_format_combo.addItem(t("PNG（更稳，体积更大）"), "png")
        self.realesrgan_output_format_combo.addItem(t("JPG（体积更小）"), "jpg")
        self.realesrgan_output_format_combo.setToolTip(t("默认保持原格式。只有明确需要统一输出格式时再修改。"))
        realesrgan_advanced_layout.addRow(t("输出格式:"), self.realesrgan_output_format_combo)

        self.realesrgan_memory_safe_check = QCheckBox(t("稳定模式（大图、长视频或报错时更稳，但更慢）"))
        self.realesrgan_memory_safe_check.setToolTip(t("遇到大图、长视频、报错或速度不稳定时可开启，处理会更稳但更慢。"))
        realesrgan_advanced_layout.addRow("", self.realesrgan_memory_safe_check)

        self.realesrgan_keep_face_check = QCheckBox(t("优先修复人脸细节"))
        self.realesrgan_keep_face_check.setToolTip(t("适合自拍、证件照、人物特写。动漫方案不会使用这个选项。"))
        realesrgan_advanced_layout.addRow("", self.realesrgan_keep_face_check)

        realesrgan_output_dir_layout = QHBoxLayout()
        self.realesrgan_output_dir = QLineEdit()
        self.realesrgan_output_dir.setPlaceholderText(t("留空则保存到原文件所在目录"))
        self.realesrgan_output_dir.setToolTip(t("可指定修复结果保存位置。留空时，输出到原文件所在目录。"))
        realesrgan_output_dir_layout.addWidget(self.realesrgan_output_dir)
        self.browse_realesrgan_output_btn = QPushButton(t("浏览..."))
        self.browse_realesrgan_output_btn.setToolTip(t("选择修复结果的保存目录。"))
        self.browse_realesrgan_output_btn.clicked.connect(self.browse_realesrgan_output_dir)
        realesrgan_output_dir_layout.addWidget(self.browse_realesrgan_output_btn)
        realesrgan_advanced_layout.addRow(t("输出目录:"), realesrgan_output_dir_layout)

        self.realesrgan_runtime_python = QLineEdit()
        self.realesrgan_runtime_python.hide()

        self.realesrgan_suffix_input = QLineEdit(t("_画质修复"))
        self.realesrgan_suffix_input.setToolTip(t("会追加到输出文件名后面，用来区分原文件和修复结果。"))
        realesrgan_advanced_layout.addRow(t("文件名后缀:"), self.realesrgan_suffix_input)

        self.realesrgan_overwrite_check = QCheckBox(t("覆盖同名输出文件"))
        self.realesrgan_overwrite_check.setToolTip(t("勾选后，如果目标文件已存在，会直接覆盖旧结果。"))
        realesrgan_advanced_layout.addRow("", self.realesrgan_overwrite_check)

        realesrgan_options_layout.addRow(self.realesrgan_advanced_group)

        realesrgan_layout.addWidget(realesrgan_options_group)

        realesrgan_buttons_layout = QHBoxLayout()
        self.start_realesrgan_btn = QPushButton(t("开始画质修复"))
        self.start_realesrgan_btn.setToolTip(t("按当前方案批量开始修复列表中的文件。"))
        self.start_realesrgan_btn.clicked.connect(self.start_realesrgan_process)
        realesrgan_buttons_layout.addWidget(self.start_realesrgan_btn)

        self.cancel_realesrgan_btn = QPushButton(t("取消"))
        self.cancel_realesrgan_btn.setEnabled(False)
        self.cancel_realesrgan_btn.setToolTip(t("停止当前正在进行的画质修复任务。"))
        self.cancel_realesrgan_btn.clicked.connect(self.cancel_realesrgan_process)
        realesrgan_buttons_layout.addWidget(self.cancel_realesrgan_btn)

        realesrgan_controls_widget = QWidget()
        realesrgan_controls_layout = QVBoxLayout(realesrgan_controls_widget)
        realesrgan_controls_layout.setContentsMargins(0, 0, 0, 0)
        realesrgan_controls_layout.addWidget(realesrgan_file_group)
        realesrgan_controls_layout.addWidget(realesrgan_options_group)
        realesrgan_controls_layout.addStretch()

        realesrgan_controls_scroll = QScrollArea()
        realesrgan_controls_scroll.setWidgetResizable(True)
        realesrgan_controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        realesrgan_controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        realesrgan_controls_scroll.setFrameShape(QFrame.NoFrame)
        realesrgan_controls_scroll.setWidget(realesrgan_controls_widget)

        realesrgan_progress_group = QGroupBox(t("运行日志"))
        realesrgan_progress_group.setToolTip(t("这里会显示处理进度、每个文件的结果和错误信息。"))
        realesrgan_progress_layout = QVBoxLayout()
        realesrgan_progress_group.setLayout(realesrgan_progress_layout)

        realesrgan_progress_layout.addLayout(realesrgan_buttons_layout)

        self.realesrgan_progress_bar = QProgressBar()
        self.realesrgan_progress_bar.setRange(0, 100)
        self.realesrgan_progress_bar.setValue(0)
        self.realesrgan_progress_bar.setToolTip(t("显示当前批量任务的整体处理进度。"))
        realesrgan_progress_layout.addWidget(self.realesrgan_progress_bar)

        self.realesrgan_progress_text = QTextEdit()
        self.realesrgan_progress_text.setReadOnly(True)
        self.realesrgan_progress_text.setMinimumHeight(100)
        self.realesrgan_progress_text.setToolTip(t("查看每个文件的开始、完成、失败和详细处理日志。"))
        realesrgan_progress_layout.addWidget(self.realesrgan_progress_text)

        realesrgan_splitter = QSplitter(Qt.Vertical)
        realesrgan_splitter.addWidget(realesrgan_controls_scroll)
        realesrgan_splitter.addWidget(realesrgan_progress_group)
        realesrgan_splitter.setChildrenCollapsible(False)
        realesrgan_splitter.setStretchFactor(0, 3)
        realesrgan_splitter.setStretchFactor(1, 2)
        realesrgan_splitter.setSizes([540, 220])
        realesrgan_layout.addWidget(realesrgan_splitter)

        self.realesrgan_process_thread = None
        self.realesrgan_file_list = []
        for tab_index in range(self.tabs.count()):
            if self.tabs.tabText(tab_index) == t("画质修复"):
                self.tabs.removeTab(tab_index)
                break
        
        # 历史记录选项卡
        history_tab = QWidget()
        self.tabs.addTab(history_tab, t("历史记录"))
        history_layout = QVBoxLayout(history_tab)
        
        # 历史记录工具栏
        history_toolbar = QHBoxLayout()
        
        refresh_history_button = QPushButton(t("🔄 刷新历史"))
        refresh_history_button.setToolTip(t("刷新历史记录列表"))
        refresh_history_button.setStyleSheet(
            "QPushButton { "
            "background-color: #007bff; "
            "color: white; "
            "border: none; "
            "padding: 6px 12px; "
            "border-radius: 4px; "
            "font-weight: bold; "
            "} "
            "QPushButton:hover { "
            "background-color: #0056b3; "
            "} "
            "QPushButton:pressed { "
            "background-color: #004085; "
            "}"
        )
        refresh_history_button.clicked.connect(self.refresh_history)
        history_toolbar.addWidget(refresh_history_button)
        
        clear_history_button = QPushButton(t("🗑️ 清空所有历史记录"))
        clear_history_button.setToolTip(t("一键清空所有下载历史记录（不可恢复）"))
        clear_history_button.setStyleSheet(
            "QPushButton { "
            "background-color: #dc3545; "
            "color: white; "
            "border: none; "
            "padding: 6px 12px; "
            "border-radius: 4px; "
            "font-weight: bold; "
            "} "
            "QPushButton:hover { "
            "background-color: #c82333; "
            "} "
            "QPushButton:pressed { "
            "background-color: #bd2130; "
            "}"
        )
        clear_history_button.clicked.connect(self.clear_history)
        history_toolbar.addWidget(clear_history_button)
        
        history_toolbar.addStretch()
        
        # 搜索框
        search_label = QLabel(t("搜索:"))
        history_toolbar.addWidget(search_label)
        
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText(t("搜索标题或URL..."))
        self.history_search.textChanged.connect(self.filter_history)
        history_toolbar.addWidget(self.history_search)
        
        history_layout.addLayout(history_toolbar)
        
        # 历史记录表格
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(6)
        self.history_table.setHorizontalHeaderLabels([t("类型"), t("标题"), t("时间"), t("文件大小"), t("格式/选项"), t("操作")])
        
        # 设置表格属性
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSortingEnabled(True)
        
        # 设置列宽
        header = self.history_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 类型列
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # 标题列自适应
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 时间列
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 大小列
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 格式/选项列
        header.setSectionResizeMode(5, QHeaderView.Fixed)  # 操作列
        self.history_table.setColumnWidth(5, 200)
        
        history_layout.addWidget(self.history_table)
        
        # 加载历史记录
        self.load_history_table()
        
        # 关于选项卡 - 极简解压风格
        about_tab = QWidget()
        self.tabs.addTab(about_tab, t("关于"))
        about_main_layout = QVBoxLayout(about_tab)
        about_main_layout.setContentsMargins(0, 0, 0, 0)
        
        # 使用滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background-color: #fcfcfc; }")
        about_main_layout.addWidget(scroll)
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #fcfcfc;")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(30, 30, 30, 30)
        scroll_layout.setSpacing(25)
        scroll.setWidget(scroll_content)
        
        # 顶部标题
        header_layout = QVBoxLayout()
        title_label = QLabel("SourceCraft")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 32px; font-weight: bold; color: #1a73e8; margin-bottom: 25px; border: none;")
        header_layout.addWidget(title_label)
        scroll_layout.addLayout(header_layout)
        
        # 免责声明区域
        disclaimer_frame = QFrame()
        disclaimer_frame.setStyleSheet("""
            QFrame {
                background-color: #fff9f0;
                border: 1px solid #ffe8cc;
                border-radius: 12px;
                padding: 20px;
            }
        """)
        disclaimer_layout = QVBoxLayout(disclaimer_frame)
        disclaimer_layout.setSpacing(12)
        
        disclaimer_header = QLabel(t("⚖️ 工具免责声明与版权声明"))
        disclaimer_header.setAlignment(Qt.AlignCenter)
        disclaimer_header.setStyleSheet("font-size: 18px; font-weight: 800; color: #d35400; border: none; margin-bottom: 5px;")
        disclaimer_layout.addWidget(disclaimer_header)
        
        disclaimer_items = [
            (t("服务宗旨"), t("本工具提供视频URL解析技术演示，仅用于用户已获得合法授权的内容获取辅助，不得用于规避平台限制或侵犯第三方版权的行为。")),
            (t("版权声明"), t("本工具不存储、不缓存、不分发任何视频内容，所有内容版权归原作者所有。本工具仅提供技术解析服务，不参与内容传播。")),
            (t("法律责任"), t("用户使用本工具必须遵守《著作权法》及平台服务条款，严禁用于下载受版权保护的内容。因非法使用导致的法律后果由用户自行承担，开发者不承担连带责任。")),
            (t("第三方声明"), t("本工具与YouTube、B站等平台无任何官方关联，未获平台授权。使用可能违反平台条款，风险由用户自行承担。")),
            (t("免责范围"), t("开发者不对工具的稳定性、兼容性做任何担保，不对因网络问题、平台规则变更导致的下载失败负责。"))
        ]
        
        for title, content in disclaimer_items:
            item_label = QLabel(f"<b>• {title}：</b>{content}")
            item_label.setWordWrap(True)
            item_label.setStyleSheet("font-size: 14px; color: #444; line-height: 1.6; border: none;")
            disclaimer_layout.addWidget(item_label)
            
        scroll_layout.addWidget(disclaimer_frame)
        
        # 功能卡片网格
        grid_layout = QGridLayout()
        grid_layout.setSpacing(20)
        
        def create_feature_card(icon, title, points, color_bg, color_text):
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background-color: white;
                    border: 1px solid #eee;
                    border-radius: 12px;
                    padding: 15px;
                }}
            """)
            card_layout = QVBoxLayout(card)
            
            # 图标和标题行
            top_layout = QHBoxLayout()
            icon_label = QLabel(icon)
            icon_label.setFixedSize(42, 42)
            icon_label.setAlignment(Qt.AlignCenter)
            icon_label.setStyleSheet(f"""
                background-color: {color_bg};
                color: {color_text};
                border-radius: 21px;
                font-size: 22px;
                border: none;
            """)
            top_layout.addWidget(icon_label)
            
            title_label = QLabel(title)
            title_label.setStyleSheet(f"font-size: 17px; font-weight: bold; color: #2c3e50; border: none; margin-left: 5px;")
            top_layout.addWidget(title_label)
            top_layout.addStretch()
            card_layout.addLayout(top_layout)
            
            # 功能点
            points_text = "\n".join([f"• {p}" for p in points])
            points_label = QLabel(points_text)
            points_label.setStyleSheet("font-size: 13px; color: #555; line-height: 1.5; border: none; margin-top: 10px;")
            points_label.setWordWrap(True)
            card_layout.addWidget(points_label)
            
            return card

        # 添加卡片
        features = [
            ("📥", t("视频下载"), [t("支持 1000+ 网站采集"), t("批量任务与播放列表"), t("最高支持 8K 超清画质")], "#e8f0fe", "#1a73e8"),
            ("🔄", t("视频转码"), [t("多格式容器无损转换"), t("GPU 硬件加速编码"), t("CRF 精准质量控制")], "#e6fffa", "#27ae60"),
            ("🛠️", t("高级功能"), [t("时间片段与章节分割"), t("SponsorBlock 跳过广告"), t("容错处理，遇错不停")], "#fffaf3", "#e67e22"),
            ("🖼️", t("图片处理"), [t("批量转换与尺寸调整"), t("智能压缩，保留画质"), t("支持 ICO 图标制作")], "#f3f0ff", "#9b59b6"),
            ("🌐", t("内置浏览器"), [t("一键提取 Cookie 绕过限制"), t("媒体捕捉与资源嗅探"), t("持久化会话与书签管理")], "#fffdeb", "#f1c40f"),
            ("🛡️", t("网络与系统"), [t("完善的代理及连接测试"), t("历史记录与诊断导出"), t("精致主题，自由切换")], "#f8f9fa", "#34495e")
        ]
        
        for i, (icon, title, points, bg, text) in enumerate(features):
            card = create_feature_card(icon, title, points, bg, text)
            grid_layout.addWidget(card, i // 2, i % 2)
            
        scroll_layout.addLayout(grid_layout)
        
        # 底部建议
        tip_frame = QFrame()
        tip_frame.setStyleSheet("background-color: #f8f9fa; border-radius: 8px; border: 1px solid #eee; padding: 12px;")
        tip_layout = QHBoxLayout(tip_frame)
        tip_label = QLabel(t('💡 <b>使用建议：</b>推荐使用 <a href="https://www.videolan.org/vlc/" style="color:#1a73e8; text-decoration:none;">VLC 播放器</a> 获得最佳体验'))
        tip_label.setOpenExternalLinks(True)
        tip_label.setAlignment(Qt.AlignCenter)
        tip_label.setStyleSheet("color: #666; font-size: 13px; border: none;")
        tip_layout.addWidget(tip_label)
        scroll_layout.addWidget(tip_frame)
        
        # 页脚
        footer_layout = QHBoxLayout()
        footer_copy = QLabel("© 2024-2025 All Rights Reserved.")
        footer_copy.setStyleSheet("color: #bbb; font-size: 12px; border: none;")
        footer_layout.addWidget(footer_copy)
        
        footer_layout.addStretch()
        
        version_label = QLabel(f"v{get_app_version()}")
        version_label.setStyleSheet("font-weight: bold; color: #1a73e8; background: #e8f0fe; padding: 3px 10px; border-radius: 5px; font-size: 12px; border: none;")
        footer_layout.addWidget(version_label)
        scroll_layout.addLayout(footer_layout)
        
        # 添加状态标签到主窗口底部
        self.main_layout.addWidget(self.status_label)
        
        # 设置高级选项的初始状态 - 确保新功能在启动时被正确禁用
        self.on_advanced_options_toggled(self.advanced_group.isChecked())
        # 所有控件创建完成后，按当前主题统一刷新关键样式
        self._apply_runtime_theme_styles()

    def update_format_options(self):
        # 根据选择的格式更新自定义格式输入框和格式ID选择框的状态
        format_text = self.format_combo.currentText()
        
        is_custom = format_text == t("自定义")
        is_id = format_text == "指定格式ID"
        
        self.custom_format.setEnabled(is_custom)
        self.format_id_combo.setEnabled(is_id)
        
        # 当不处于对应模式时，清空/重置输入
        if not is_custom:
            self.custom_format.clear()
        if not is_id:
            self.format_id_combo.setCurrentIndex(-1)
        
        # 如果选择了"指定格式ID"且当前没有可用格式列表，提示用户获取
        if format_text == "指定格式ID":
            # 如果是从媒体捕捉触发的，不弹出提示
            if hasattr(self, '_is_media_capture_download') and self._is_media_capture_download:
                return
                
            if not hasattr(self, 'available_formats') or not self.available_formats:
                url_text = self.url_input.text().strip()
                if not url_text:
                    # 如果没有URL，不提示获取格式
                    return
                
                reply = QMessageBox.question(self, t("获取格式列表"), 
                                          t("使用'指定格式ID'需要先获取可用格式列表。\n\n是否现在获取？"),
                                          QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    self.list_available_formats()
    
    def update_audio_lang_options(self):
        """更新音频语言选择选项"""
        selected_text = self.audio_lang_combo.currentText()
        # 只有选择"自定义语言代码"时才启用自定义输入框
        is_custom_lang = selected_text == "自定义语言代码"
        self.custom_audio_lang.setEnabled(is_custom_lang)
        
        # 当不处于自定义模式时，清空输入
        if not is_custom_lang:
            self.custom_audio_lang.clear()
    
    def get_audio_language_code(self):
        """获取选择的音频语言代码"""
        selected_text = self.audio_lang_combo.currentText()
        
        if "自动选择" in selected_text:
            # 自动选择：优先视频原始语言，然后是英语
            return "orig"
        elif "自定义语言代码" in selected_text:
            custom_lang = self.custom_audio_lang.text().strip()
            return custom_lang if custom_lang else "en"
        else:
            # 从选项文本中提取语言代码
            lang_map = {
                "英语 (en)": "en",
                "中文 (zh)": "zh", 
                "日语 (ja)": "ja",
                "韩语 (ko)": "ko",
                "法语 (fr)": "fr",
                "德语 (de)": "de",
                "西班牙语 (es)": "es",
                "俄语 (ru)": "ru",
                "意大利语 (it)": "it",
                "葡萄牙语 (pt)": "pt",
                "阿拉伯语 (ar)": "ar",
                "印地语 (hi)": "hi",
                "泰语 (th)": "th",
                "越南语 (vi)": "vi"
            }
            return lang_map.get(selected_text, "en")
    
    def _get_audio_language_filter(self):
        """生成音频语言过滤器字符串"""
        audio_lang = self.get_audio_language_code()
        
        if not audio_lang or audio_lang == "orig":
            # 自动选择（原始语言）：不使用语言过滤器
            # 让yt-dlp自动选择最佳音频流（通常是原始语言或最高质量）
            # 在格式选择器中使用排序来优先选择原始语言
            return ""
        
        # 统一规范化语言代码（支持逗号分隔和语言变体）
        raw_codes = [c.strip().lower() for c in re.split(r"[,\s]+", str(audio_lang or "")) if c.strip()]
        norm_codes = []
        for code in raw_codes:
            code = code.replace("_", "-")
            # 仅保留常见语言代码字符，避免非法表达式破坏 -f 过滤器
            safe = re.sub(r"[^a-z0-9-]", "", code)
            if safe:
                norm_codes.append(safe)
        if not norm_codes:
            return ""

        # 使用正则匹配语言变体:
        # en -> en / en-US / en-GB
        # zh -> zh / zh-CN / zh-Hans
        # en-us -> en-us / en-us-x
        # 同时支持下划线变体: en_US / zh_CN
        token_patterns = [f"{re.escape(code)}(?:[-_].*)?$" for code in norm_codes]
        if len(token_patterns) == 1:
            regex = f"(?i)^{token_patterns[0]}"
        else:
            regex = f"(?i)^(?:{'|'.join(token_patterns)})"
        # 注意: ~= 的正则值必须加引号，否则包含 ?|() 等字符时会触发格式解析失败
        return f"[language~='{regex}']"

    
    def paste_url(self):
        # 从剪贴板粘贴URL，并自动提取URL部分
        clipboard = QApplication.clipboard()
        clipboard_text = clipboard.text().strip()
        
        # 如果剪贴板内容为空，直接返回
        if not clipboard_text:
            return
            
        # 从剪贴板文本中提取URL
        extracted_url = self.extract_url_from_text(clipboard_text)
        
        # 设置到输入框
        self.url_input.setText(extracted_url)
        
        # 如果原始文本与提取的URL不同，显示提示信息
        if extracted_url != clipboard_text:
            self.progress_text.append(t_format("已自动从粘贴内容中提取URL: {}", extracted_url))
            # 自动滚动到底部
            self.progress_text.verticalScrollBar().setValue(self.progress_text.verticalScrollBar().maximum())
    
    def reset_proxy_input_border(self):
        """重置代理输入框边框为蓝色（用户修改地址时调用）"""
        try:
            if hasattr(self, 'proxy_input') and self.proxy_input.isEnabled():
                self.proxy_input.setStyleSheet(
                    "QLineEdit {"
                    "border: 2px solid #4a90e2;"  # 蓝色边框
                    "border-radius: 4px;"
                    "padding: 5px;"
                    "}"
                    "QLineEdit:disabled {"
                    "border: 2px solid #cccccc;"
                    "background-color: #f5f5f5;"
                    "}"
                )
        except Exception as e:
            print(t_format("重置代理边框颜色时出错: {}", e))
    
    def set_proxy_connected_style(self):
        """设置代理连接成功的绿色边框样式"""
        try:
            if hasattr(self, 'proxy_input'):
                self.proxy_input.setStyleSheet(
                    "QLineEdit {"
                    "border: 2px solid #28a745;"  # 绿色边框 - 连接成功
                    "border-radius: 4px;"
                    "padding: 5px;"
                    "background-color: #f0fff0;"  # 淡绿色背景
                    "}"
                    "QLineEdit:disabled {"
                    "border: 2px solid #cccccc;"
                    "background-color: #f5f5f5;"
                    "}"
                )
        except Exception as e:
            print(t_format("设置代理连接成功样式时出错: {}", e))
    
    def set_proxy_disconnected_style(self):
        """设置代理连接失败的红色边框样式"""
        try:
            if hasattr(self, 'proxy_input'):
                self.proxy_input.setStyleSheet(
                    "QLineEdit {"
                    "border: 2px solid #dc3545;"  # 红色边框 - 连接失败
                    "border-radius: 4px;"
                    "padding: 5px;"
                    "background-color: #fff5f5;"  # 淡红色背景
                    "}"
                    "QLineEdit:disabled {"
                    "border: 2px solid #cccccc;"
                    "background-color: #f5f5f5;"
                    "}"
                )
        except Exception as e:
            print(t_format("设置代理连接失败样式时出错: {}", e))
    
    def apply_proxy_preset(self, index):
        """应用代理模板"""
        if index > 0:
            preset_text = self.proxy_preset_combo.itemText(index)
            # 提取代理地址（冒号后的部分）
            if ": " in preset_text:
                proxy_addr = preset_text.split(": ", 1)[1]
                self.proxy_input.setText(proxy_addr)
            # 重置为"选择模板..."
            self.proxy_preset_combo.setCurrentIndex(0)
    
    def test_proxy_connection(self):
        """测试代理连接 - 完全避免subprocess，只使用Python标准库"""
        try:
            if not hasattr(self, 'proxy_input'):
                return
                
            proxy_url = self.proxy_input.text().strip()
            if not proxy_url:
                QMessageBox.warning(self, t("提示"), t("请先输入代理地址"))
                return
            
            # 验证代理地址格式
            if not any(proxy_url.startswith(prefix) for prefix in ['http://', 'https://', 'socks4://', 'socks5://']):
                QMessageBox.warning(self, t("提示"), 
                    t("代理地址格式不正确！\n\n") + t("正确格式示例：\n") + t("• http://127.0.0.1:7890\n") + t("• socks5://user:pass@127.0.0.1:1080\n") + t("• https://proxy.example.com:8080"))
                return
            
            # 禁用测试按钮避免重复点击
            self.test_proxy_button.setEnabled(False)
            self.test_proxy_button.setText(t("测试中..."))
            QApplication.processEvents()
            
            # 简单的进度提示
            self.progress_text.append(t_format("[{}] 开始测试代理: {}", datetime.now().strftime('%H:%M:%S'), proxy_url))
            self.progress_text.verticalScrollBar().setValue(self.progress_text.verticalScrollBar().maximum())
            QApplication.processEvents()
            
        except Exception as e:
            print(t_format("初始化代理测试时出错: {}", e))
            try:
                self.test_proxy_button.setEnabled(True)
                self.test_proxy_button.setText(t("🔍 测试连接"))
            except:
                pass
            return
        
        class ProxyTestThread(QThread):
            """纯Python实现的代理测试线程 - 不使用任何外部进程"""
            test_result = pyqtSignal(bool, str)
            
            def __init__(self, proxy_url):
                super().__init__()
                self.proxy_url = proxy_url
                self.stopped = False
            
            def run(self):
                """使用纯Python标准库测试代理"""
                try:
                    import urllib.request
                    import urllib.error
                    import socket
                    import ssl
                    
                    # 测试目标列表
                    test_urls = [
                        ('http://www.google.com', 'Google'),
                        ('https://www.baidu.com', '百度'),
                        ('http://www.bing.com', 'Bing')
                    ]
                    
                    success_count = 0
                    last_error = None
                    
                    for test_url, site_name in test_urls:
                        if self.stopped:
                            break
                            
                        try:
                            # 为HTTP和HTTPS设置代理
                            proxy_handler = urllib.request.ProxyHandler({
                                'http': self.proxy_url,
                                'https': self.proxy_url
                            })
                            
                            # 创建opener
                            opener = urllib.request.build_opener(proxy_handler)
                            
                            # 添加标准请求头
                            opener.addheaders = [
                                ('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'),
                                ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'),
                                ('Accept-Language', 'zh-CN,zh;q=0.9,en;q=0.8')
                            ]
                            
                            # 注意：不设置全局socket超时，而是在请求中使用局部超时参数
                            # 这样不会影响应用程序的其他网络操作
                            
                            # 发送请求，使用局部超时参数
                            response = opener.open(test_url, timeout=10)
                            
                            # 检查响应
                            if response.status == 200 or response.code == 200:
                                success_count += 1
                                # 一次成功就够了
                                self.test_result.emit(True, 
                                    t("✅ 代理连接测试成功！\n\n") + t_format("🌐 成功通过代理访问 {}\n", site_name) + t_format("🔐 代理地址: {}\n\n", self.proxy_url) + t("✓ 代理服务器工作正常，可以使用\n") + t("✓ 下载时将自动通过此代理进行\n") + t("✓ 可以点击'开始下载'按钮开始下载"))
                                return
                            
                        except urllib.error.HTTPError as e:
                            # HTTP错误，但连接成功了
                            if e.code in [301, 302, 307, 308, 403, 404]:
                                # 重定向或禁止访问，说明代理连接成功
                                success_count += 1
                                self.test_result.emit(True, 
                                    t("✅ 代理连接测试成功！\n\n") + t_format("🌐 代理服务器响应正常 (HTTP {})\n", e.code) + t_format("🔐 代理地址: {}\n\n", self.proxy_url) + t("✓ 代理连接已验证可用\n") + t("✓ 下载时将自动通过此代理进行\n") + t("✓ 可以点击'开始下载'按钮开始下载"))
                                return
                            last_error = str(e)
                            
                        except urllib.error.URLError as e:
                            error_str = str(e.reason) if hasattr(e, 'reason') else str(e)
                            last_error = error_str
                            
                            # 分析具体错误
                            if 'refused' in error_str.lower():
                                # 连接被拒绝，立即返回
                                self.test_result.emit(False, 
                                    t("🚫 代理连接被拒绝\n\n") + t("可能的原因：\n") + t("• 代理服务未启动\n") + t("• 代理端口错误\n") + t("• 防火墙阻止连接\n\n") + t_format("代理地址: {}", self.proxy_url))
                                return
                                
                            elif 'timed out' in error_str.lower() or 'timeout' in error_str.lower():
                                # 超时，尝试下一个
                                continue
                                
                        except socket.timeout:
                            last_error = "连接超时"
                            continue
                            
                        except socket.gaierror as e:
                            last_error = t_format("DNS解析失败: {}", str(e))
                            continue
                            
                        except Exception as e:
                            last_error = f"{type(e).__name__}: {str(e)}"
                            continue
                    
                    # 所有测试都失败了
                    if success_count == 0:
                        error_msg = "❌ 代理连接失败\n\n"
                        
                        if last_error:
                            if 'refused' in last_error.lower():
                                error_msg += "连接被拒绝，请检查：\n• 代理服务是否已启动\n• 端口是否正确"
                            elif 'timeout' in last_error.lower() or 'timed out' in last_error.lower():
                                error_msg += "连接超时，请检查：\n• 代理服务器是否正常\n• 网络连接是否畅通"
                            elif 'authentication' in last_error.lower() or 'auth' in last_error.lower():
                                error_msg += "认证失败，请检查：\n• 用户名密码是否正确"
                            elif 'dns' in last_error.lower() or 'gaierror' in last_error.lower():
                                error_msg += "DNS解析失败，请检查：\n• 网络连接\n• 代理设置"
                            else:
                                error_msg += t_format("错误信息：{}", last_error[:150])
                        else:
                            error_msg += "无法连接到任何测试网站\n请检查代理设置和网络连接"
                        
                        error_msg += t_format("\n\n代理地址: {}", self.proxy_url)
                        self.test_result.emit(False, error_msg)
                    
                except ImportError as e:
                    self.test_result.emit(False, 
                        t_format("❌ 缺少必要的Python模块\n\n{}", str(e)))
                    
                except Exception as e:
                    import traceback
                    error_trace = traceback.format_exc()
                    print(t_format("代理测试异常: {}", error_trace))
                    
                    self.test_result.emit(False, 
                        t("❌ 测试过程出错\n\n") + t_format("错误类型: {}\n", type(e).__name__) + t_format("错误信息: {}\n\n", str(e)) + t("详细错误已输出到控制台"))
            
            def stop(self):
                """停止测试"""
                self.stopped = True
        
        def on_test_result(success, message):
            """处理测试结果 - 增强异常保护"""
            try:
                # 恢复测试按钮
                if hasattr(self, 'test_proxy_button'):
                    try:
                        self.test_proxy_button.setEnabled(True)
                        self.test_proxy_button.setText(t("🔍 测试连接"))
                    except:
                        pass
                
                # 显示结果
                if success:
                    # 设置绿色边框表示连接成功
                    self.set_proxy_connected_style()
                    
                    QMessageBox.information(self, t("代理测试成功"), message)
                    if hasattr(self, 'progress_text'):
                        self.progress_text.append("=" * 60)
                        self.progress_text.append(t_format("[{}] ✅ 代理连接测试成功！", datetime.now().strftime('%H:%M:%S')))
                        # 从消息中提取代理地址
                        if hasattr(self, 'proxy_input'):
                            proxy_addr = self.proxy_input.text().strip()
                            if proxy_addr:
                                self.progress_text.append(t_format("代理地址: {}", proxy_addr))
                        self.progress_text.append(t("代理服务器工作正常，可以用于下载"))
                        self.progress_text.append(t("✅ 边框已变为绿色，表示连接成功"))
                        self.progress_text.append("=" * 60)
                else:
                    # 设置红色边框表示连接失败
                    self.set_proxy_disconnected_style()
                    
                    QMessageBox.warning(self, t("代理测试失败"), message)
                    if hasattr(self, 'progress_text'):
                        self.progress_text.append("=" * 60)
                        self.progress_text.append(t_format("[{}] ❌ 代理连接测试失败", datetime.now().strftime('%H:%M:%S')))
                        if hasattr(self, 'proxy_input'):
                            proxy_addr = self.proxy_input.text().strip()
                            if proxy_addr:
                                self.progress_text.append(t_format("代理地址: {}", proxy_addr))
                        self.progress_text.append(t("请检查代理设置并重试"))
                        self.progress_text.append(t("❌ 边框已变为红色，表示连接失败"))
                        self.progress_text.append("=" * 60)
                
                # 自动滚动
                if hasattr(self, 'progress_text'):
                    try:
                        self.progress_text.verticalScrollBar().setValue(
                            self.progress_text.verticalScrollBar().maximum())
                    except:
                        pass
                        
            except Exception as e:
                print(t_format("处理测试结果时出错: {}", e))
                import traceback
                traceback.print_exc()
        
        def on_test_finished():
            """测试完成清理 - 增强异常保护"""
            try:
                if hasattr(self, 'test_proxy_button'):
                    self.test_proxy_button.setEnabled(True)
                    self.test_proxy_button.setText(t("🔍 测试连接"))
                if hasattr(self, 'proxy_test_thread'):
                    self.proxy_test_thread = None
            except Exception as e:
                print(t_format("测试清理时出错: {}", e))
        
        # 创建并启动测试线程
        try:
            self.proxy_test_thread = ProxyTestThread(proxy_url)
            self.proxy_test_thread.test_result.connect(on_test_result)
            self.proxy_test_thread.finished.connect(on_test_finished)
            self.proxy_test_thread.start()
            
        except Exception as e:
            print(t_format("启动测试线程失败: {}", e))
            import traceback
            traceback.print_exc()
            
            try:
                QMessageBox.critical(self, t("错误"), 
                    t("启动测试失败\n\n") + t_format("错误信息: {}\n\n", str(e)) + t("请查看控制台获取详细信息"))
                self.test_proxy_button.setEnabled(True)
                self.test_proxy_button.setText(t("🔍 测试连接"))
            except:
                pass
    
    def test_source_address_connection(self):
        """测试源地址绑定是否有效"""
        import socket
        import ipaddress
        
        source_ip = self.source_address_input.text().strip()
        if not source_ip:
            QMessageBox.warning(self, t("提示"), t("请先输入要绑定的本地IP地址"))
            return
            
        try:
            # 1. 验证IP格式
            ipaddress.ip_address(source_ip)
            
            # 2. 尝试绑定到该IP
            # 创建一个临时socket测试绑定
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # 绑定到指定IP和随机端口(0)
                test_sock.bind((source_ip, 0))
                QMessageBox.information(self, t("成功"), t_format("✅ 绑定成功！\n\n本地IP地址 {} 可用于网络请求。", source_ip))
            except socket.error as e:
                QMessageBox.critical(self, t("失败"), t_format("❌ 绑定失败！\n\n无法绑定到地址 {}。\n错误信息: {}\n\n提示: 请确保该IP是本机已分配的有效内网/外网IP。", source_ip, str(e)))
            finally:
                test_sock.close()
                
        except ValueError:
            QMessageBox.warning(self, t("错误"), t_format("无效的IP地址格式: {}", source_ip))
        except Exception as e:
            QMessageBox.critical(self, t("错误"), t_format("测试过程中出现异常: {}", str(e)))
    
    def load_saved_path(self):
        """从设置中加载上次保存的输出路径"""
        # 加载视频下载输出路径
        saved_path = str(self.settings.value("output_path") or "").strip()
        if saved_path and os.path.isdir(saved_path):
            self.output_path.setText(saved_path)
        else:
            # 如果没有保存的路径或路径不存在，使用默认路径并回写设置，避免各入口读到旧值
            default_path = os.path.abspath(os.path.expanduser("~/Downloads"))
            self.output_path.setText(default_path)
            self.settings.setValue("output_path", default_path)
        
        # 加载图片处理输出目录
        saved_image_dir = str(self.settings.value("image_output_dir") or "").strip()
        if saved_image_dir and os.path.isdir(saved_image_dir):
            self.image_output_dir.setText(saved_image_dir)

        os.environ.pop("SOURCECRAFT_REALESRGAN_PYTHON", None)

        # 加载更新提示设置
        self.enable_update_notification = self.settings.value("enable_update_notification", True, type=bool)

    def _get_dialog_start_dir(self) -> str:
        """获取文件对话框的安全起始目录（不干扰统一保存路径设置）。"""
        candidates = [
            str(self.settings.value("last_dialog_dir") or "").strip(),
            str(self.settings.value("output_path") or "").strip(),
            os.path.abspath(os.path.expanduser("~/Downloads")),
            os.path.abspath(os.path.expanduser("~")),
        ]
        for path in candidates:
            if path and os.path.isdir(path):
                return path
        return os.path.abspath(os.path.expanduser("~"))

    def _remember_dialog_dir(self, path: str) -> None:
        """记住最近一次文件对话框所在目录，不覆盖统一保存路径。"""
        try:
            p = str(path or "").strip()
            if not p:
                return
            directory = p if os.path.isdir(p) else os.path.dirname(p)
            directory = os.path.abspath(directory) if directory else ""
            if directory and os.path.isdir(directory):
                self.settings.setValue("last_dialog_dir", directory)
        except Exception:
            pass
    
    def browse_output_path(self):
        # 选择输出目录
        try:
            current_path = self.output_path.text() or os.path.expanduser("~/Downloads")
            directory = QFileDialog.getExistingDirectory(self, t("选择保存位置"), current_path)
            if directory:
                # 确保路径使用正确的编码
                directory = os.path.abspath(directory)
                self.output_path.setText(directory)
                # 保存用户选择的路径
                self.settings.setValue("output_path", directory)
                self._remember_dialog_dir(directory)
                self.transcode_progress_text.append(t_format("已选择输出目录: {}", directory))
        except Exception as e:
            self.transcode_progress_text.append(t_format("选择目录时出错: {}", str(e)))
            QMessageBox.warning(self, t("警告"), t_format("选择目录时出错: {}", str(e)))
            import traceback
            self.transcode_progress_text.append(traceback.format_exc())
    
    def browse_input_file(self):
        # 选择输入文件
        try:
            # 优先使用当前输入文件的目录，其次使用保存的路径，最后使用用户主目录
            if self.input_file.text():
                current_dir = os.path.dirname(self.input_file.text())
            else:
                # 使用最近文件对话框目录，不覆盖统一保存路径
                current_dir = self._get_dialog_start_dir()
                
            file, _ = QFileDialog.getOpenFileName(self, t("选择视频文件"), current_dir, t("视频文件 (*.mp4 *.mkv *.avi *.mov *.webm *.flv);;所有文件 (*)"))
            if file:
                # 确保路径使用正确的编码
                file = os.path.normpath(os.path.abspath(file))
                self.input_file.setText(file)
                self._remember_dialog_dir(file)
                
                # 自动设置输出文件名
                self.update_output_filename(file)
                return  # 成功设置文件后直接返回，避免重复设置
        except Exception as e:
            self.transcode_progress_text.append(t_format("选择文件时出错: {}", str(e)))
            QMessageBox.warning(self, t("警告"), t_format("选择文件时出错: {}", str(e)))
            import traceback
            self.transcode_progress_text.append(traceback.format_exc())
    
    def _on_transcode_file_dropped(self, file_path):
        """处理转码Tab拖放的视频文件"""
        try:
            # 确保路径使用正确的编码
            file_path = os.path.normpath(os.path.abspath(file_path))
            self.input_file.setText(file_path)
            self._remember_dialog_dir(file_path)
            
            # 自动设置输出文件名
            self.update_output_filename(file_path)
            
            # 显示成功消息
            self.transcode_progress_text.append(t_format("📁 已通过拖拽设置输入文件: {}", os.path.basename(file_path)))
        except Exception as e:
            self.transcode_progress_text.append(t_format("处理拖放文件时出错: {}", str(e)))
            import traceback
            self.transcode_progress_text.append(traceback.format_exc())
    
    def update_output_filename(self, input_file):
        """根据输入文件更新输出文件名"""
        try:
            base_name = os.path.splitext(input_file)[0]
            output_ext = self.output_format.currentText()
            self.output_file.setText(f"{base_name}_转码.{output_ext}")
        except Exception as e:
            self.transcode_progress_text.append(t_format("更新输出文件名时出错: {}", str(e)))
    
    def browse_output_file(self):
        # 选择输出文件
        # 使用保存的路径作为初始目录
        saved_path = self._get_dialog_start_dir()
        file, _ = QFileDialog.getSaveFileName(self, t("保存文件"), saved_path, t("视频文件 (*.mp4 *.mkv *.avi *.mov *.webm);;音频文件 (*.mp3 *.m4a *.flac);;所有文件 (*)"))
        if file:
            self.output_file.setText(file)
            self._remember_dialog_dir(file)
    
    def browse_cookie_file(self):
        # 选择Cookie文件
        # 使用最近文件对话框目录作为初始目录
        saved_path = self._get_dialog_start_dir()
        file, _ = QFileDialog.getOpenFileName(self, t("选择Cookie文件"), saved_path, t("Cookie文件 (*.txt);;所有文件 (*)"))
        if file:
            self.cookie_file.setText(file)
            self._remember_dialog_dir(file)
            
    def browse_client_cert_file(self):
        """选择客户端证书文件"""
        # 使用最近文件对话框目录作为初始目录
        saved_path = self._get_dialog_start_dir()
        file, _ = QFileDialog.getOpenFileName(self, t("选择客户端证书文件"), saved_path, t("证书文件 (*.pem *.crt *.p12 *.pfx);;所有文件 (*)"))
        if file:
            self.client_cert_file.setText(file)
            self._remember_dialog_dir(file)
            
    def browse_client_cert_key_file(self):
        """选择客户端证书密钥文件"""
        # 使用最近文件对话框目录作为初始目录
        saved_path = self._get_dialog_start_dir()
        file, _ = QFileDialog.getOpenFileName(self, t("选择客户端证书密钥文件"), saved_path, t("密钥文件 (*.key *.pem);;所有文件 (*)"))
        if file:
            self.client_cert_key.setText(file)
            self._remember_dialog_dir(file)
            
    def extract_browser_cookies(self):
        """从浏览器提取Cookie功能已移除"""
        QMessageBox.information(self, t("功能提示"), 
            t("浏览器Cookie提取功能已移除。\n\n") + t("请使用简化的限制绕过功能。"))
        self.progress_text.append(t("Cookie提取功能已移除，请使用简化绕过功能"))
    
    def get_format_option(self):
        """获取格式选项 - 优化兼容性，明确指定H.264+AAC编码器"""
        format_text = self.format_combo.currentText()
        
        # 获取音频语言过滤器
        audio_lang_filter = self._get_audio_language_filter()
        
        if format_text == t("自定义"):
            return self.custom_format.text()
        elif format_text == "指定格式ID":
            # 获取选择的格式ID
            if self.format_id_combo.currentData():
                format_id = self.format_id_combo.currentData()
                # 如果选择的是预设选项
                if format_id == "best":
                    return "best"
                elif format_id == "bestvideo+bestaudio":
                     # 使用最优质量组合，明确指定H.264+AAC，排除AV1/VP9
                     if audio_lang_filter:
                         return f"bestvideo[vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]{audio_lang_filter}/bestvideo[vcodec*=h264][ext=mp4]+bestaudio[acodec*=aac]{audio_lang_filter}/bestvideo[ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]{audio_lang_filter}/bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}/bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}"
                     else:
                         return "bestvideo[vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]/bestvideo[vcodec*=h264][ext=mp4]+bestaudio[ext=m4a]/bestvideo[ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
                else:
                    return format_id
            else:
                return "best"
        elif format_text == "最佳质量":
            # 最佳质量：优先选择最高分辨率，不限制分辨率
            if audio_lang_filter:
                return f"bestvideo[vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]{audio_lang_filter}/bestvideo[vcodec*=h264][ext=mp4]+bestaudio[acodec*=aac]{audio_lang_filter}/bestvideo[ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]{audio_lang_filter}/bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}/bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}"
            else:
                return "bestvideo+bestaudio/best/bestvideo[vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]/bestvideo[vcodec*=h264][ext=mp4]+bestaudio[ext=m4a]"
        elif format_text == "4K":
            # 4K分辨率：优先选择4K，如果没有则选择最佳可用分辨率
            if audio_lang_filter:
                return f"bestvideo[height>=2160]+bestaudio{audio_lang_filter}/bestvideo[height=2160]+bestaudio{audio_lang_filter}/bestvideo[height>=1440]+bestaudio{audio_lang_filter}/bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}/bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}"
            else:
                return "bestvideo[height>=2160]+bestaudio/bestvideo[height=2160]+bestaudio/bestvideo[height>=1440]+bestaudio/bestvideo+bestaudio/best"
        elif format_text == "2K":
            # 2K分辨率：优先选择2K，如果没有则选择最佳可用分辨率
            if audio_lang_filter:
                return f"bestvideo[height>=1440][height<2160]+bestaudio{audio_lang_filter}/bestvideo[height=1440]+bestaudio{audio_lang_filter}/bestvideo[height>=1080]+bestaudio{audio_lang_filter}/bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}/bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}"
            else:
                return "bestvideo[height>=1440][height<2160]+bestaudio/bestvideo[height=1440]+bestaudio/bestvideo[height>=1080]+bestaudio/bestvideo+bestaudio/best"
        elif format_text == "1080p":
            # 1080p分辨率：明确指定H.264+AAC，限制到1080p
            if audio_lang_filter:
                return f"bestvideo[height<=1080][vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]{audio_lang_filter}/bestvideo[height<=1080][vcodec*=h264][ext=mp4]+bestaudio[acodec*=aac]{audio_lang_filter}/bestvideo[height<=1080][ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]{audio_lang_filter}/bestvideo[height<=1080]+bestaudio{audio_lang_filter}/best[height<=1080]{audio_lang_filter}[acodec!=opus]/bestvideo[height<=1080]+bestaudio{audio_lang_filter}/best[height<=1080]{audio_lang_filter}"
            else:
                return "bestvideo[height<=1080][vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]/bestvideo[height<=1080][vcodec*=h264][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080][ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]"
        elif format_text == "720p":
            # 720p分辨率：明确指定H.264+AAC，限制到720p
            if audio_lang_filter:
                return f"bestvideo[height<=720][vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]{audio_lang_filter}/bestvideo[height<=720][vcodec*=h264][ext=mp4]+bestaudio[acodec*=aac]{audio_lang_filter}/bestvideo[height<=720][ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]{audio_lang_filter}/bestvideo[height<=720]+bestaudio{audio_lang_filter}/best[height<=720]{audio_lang_filter}[acodec!=opus]/bestvideo[height<=720]+bestaudio{audio_lang_filter}/best[height<=720]{audio_lang_filter}"
            else:
                return "bestvideo[height<=720][vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]/bestvideo[height<=720][vcodec*=h264][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720][ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]"
        elif format_text == "480p":
            # 480p分辨率：明确指定H.264+AAC，限制到480p
            if audio_lang_filter:
                return f"bestvideo[height<=480][vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]{audio_lang_filter}/bestvideo[height<=480][vcodec*=h264][ext=mp4]+bestaudio[acodec*=aac]{audio_lang_filter}/bestvideo[height<=480][ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]{audio_lang_filter}/bestvideo[height<=480]+bestaudio{audio_lang_filter}/best[height<=480]{audio_lang_filter}[acodec!=opus]/bestvideo[height<=480]+bestaudio{audio_lang_filter}/best[height<=480]{audio_lang_filter}"
            else:
                return "bestvideo[height<=480][vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]/bestvideo[height<=480][vcodec*=h264][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480][ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]"
        elif format_text == "仅音频 (mp3)":
            # 音频格式：优先AAC编码的m4a，然后提取为mp3
            return f"bestaudio{audio_lang_filter}[acodec^=mp4a][ext=m4a]/bestaudio{audio_lang_filter}[ext=m4a]/bestaudio{audio_lang_filter}/best -x --audio-format mp3"
        elif format_text == "仅音频 (m4a)":
            # 音频格式：优先AAC编码的m4a容器
            return f"bestaudio{audio_lang_filter}[acodec^=mp4a][ext=m4a]/bestaudio{audio_lang_filter}[ext=m4a]/bestaudio{audio_lang_filter}/best -x --audio-format m4a"
        elif format_text == "仅缩略图":
            # 仅下载缩略图，不下载视频文件
            return "--skip-download --write-thumbnail"

        # 默认返回最佳质量，明确指定H.264+AAC，排除AV1/VP9
        if audio_lang_filter:
            return f"bestvideo[vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]{audio_lang_filter}/bestvideo[vcodec*=h264][ext=mp4]+bestaudio[acodec*=aac]{audio_lang_filter}/bestvideo[ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]{audio_lang_filter}/bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}/bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}"
        else:
            return "bestvideo[vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]/bestvideo[vcodec*=h264][ext=mp4]+bestaudio[ext=m4a]/bestvideo[ext=mp4][vcodec!=vp9][vcodec!=av01]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"

    # on_geo_mode_changed方法已移除，因为地理限制选项卡已合并到限制绕过选项卡中


    

    
    def start_update_button_animation(self):
        """启动更新按钮的闪烁动画"""
        if not hasattr(self, 'update_animation'):
            from PyQt5.QtCore import QPropertyAnimation, QEasingCurve
            from PyQt5.QtWidgets import QGraphicsOpacityEffect
            
            # 创建透明度效果
            self.update_opacity_effect = QGraphicsOpacityEffect()
            self.update_button.setGraphicsEffect(self.update_opacity_effect)
            
            # 创建动画
            self.update_animation = QPropertyAnimation(self.update_opacity_effect, b"opacity")
            self.update_animation.setDuration(1000)  # 1秒
            self.update_animation.setStartValue(1.0)
            self.update_animation.setEndValue(0.6)
            self.update_animation.setEasingCurve(QEasingCurve.InOutQuad)
            
            # 设置循环
            self.update_animation.setLoopCount(-1)  # 无限循环
            
            # 连接动画完成信号，实现来回闪烁
            self.update_animation.finished.connect(self.reverse_update_animation)
        
        self.update_animation.start()
    
    def reverse_update_animation(self):
        """反转更新按钮动画方向"""
        if hasattr(self, 'update_animation'):
            start_value = self.update_animation.startValue()
            end_value = self.update_animation.endValue()
            self.update_animation.setStartValue(end_value)
            self.update_animation.setEndValue(start_value)
            self.update_animation.start()
    
    def stop_update_button_animation(self):
        """停止更新按钮的闪烁动画"""
        if hasattr(self, 'update_animation'):
            self.update_animation.stop()
            self.update_button.setGraphicsEffect(None)  # 移除透明度效果
    
    def _is_any_proxy_enabled(self):
        """检测是否启用了任何形式的代理（UI或系统环境变量）"""
        ui_proxy = hasattr(self, 'proxy_input') and self.proxy_input.text().strip()
        env_proxy = os.environ.get('http_proxy') or os.environ.get('https_proxy') or os.environ.get('all_proxy')
        return bool(ui_proxy or env_proxy)

    def check_dependency_updates(self):
        """检查所有依赖组件更新（yt-dlp, ffmpeg, ffprobe, deno）"""
        # 检查是否已在检查中
        if hasattr(self, 'dep_update_thread') and self.dep_update_thread.isRunning():
            return
        
        # 设置按钮为检查中状态
        if hasattr(self, 'update_button'):
            self.update_button.setToolTip(t("正在检查依赖更新..."))
        
        proxy_enabled = self._is_any_proxy_enabled()
        self.update_status(t("🔄 正在检查所有依赖组件版本..."), "info")
        
        # 创建并启动检查线程
        self.dep_update_thread = DependencyUpdateThread(perform_update=False, proxy_enabled=proxy_enabled)
        self.dep_update_thread.progress_signal.connect(self.on_dep_update_progress)
        self.dep_update_thread.component_signal.connect(self.on_dep_component_status)
        self.dep_update_thread.finished_signal.connect(self.on_dep_check_finished)
        self.dep_update_thread.start()
    
    def on_dep_update_progress(self, message):
        """依赖更新进度回调"""
        self.update_status(message, "info")
    
    def on_dep_component_status(self, comp_name, current_ver, latest_ver, status):
        """组件状态更新回调"""
        if status == "update_available":
            self.update_status(t_format("📦 {}: {} → {} (可更新)", comp_name, current_ver, latest_ver), "info")
        elif status == "up_to_date":
            self.update_status(t_format("✅ {}: {} (已是最新)", comp_name, current_ver), "success")
        elif status == "updated":
            self.update_status(t_format("✅ {}: 更新完成 → {}", comp_name, latest_ver), "success")
        elif status == "update_failed":
            self.update_status(t_format("❌ {}: 更新失败", comp_name), "error")
        elif status == "error":
            self.update_status(t_format("⚠️ {}: 检查失败", comp_name), "warning")
    
    def on_dep_check_finished(self, success, message):
        """依赖检查完成回调 - 静默自动更新，无弹窗"""
        # 重置按钮状态
        if hasattr(self, 'update_button'):
            self.update_button.setToolTip(t("检查依赖更新"))
        
        if not success:
            self.update_status(f"❌ {message}", "error")
            return
        
        # 如果发现可更新组件，直接静默更新，无需弹窗确认
        if "可更新" in message or "个组件可更新" in message:
            self.update_status(t_format("🔄 {}，正在自动更新...", message), "info")
            self.perform_dependency_updates()
        else:
            self.update_status(f"✅ {message}", "success")
    
    def perform_dependency_updates(self):
        """执行依赖更新"""
        if hasattr(self, 'dep_update_thread') and self.dep_update_thread.isRunning():
            return
        
        self.update_status(t("⬇️ 正在下载并更新所有组件..."), "info")
        
        if hasattr(self, 'update_button'):
            self.update_button.setEnabled(False)
            self.update_button.setToolTip(t("正在更新依赖..."))
        
        # 启动更新线程（执行实际更新）
        self.dep_update_thread = DependencyUpdateThread(perform_update=True, proxy_enabled=self._is_any_proxy_enabled())
        self.dep_update_thread.progress_signal.connect(self.on_dep_update_progress)
        self.dep_update_thread.component_signal.connect(self.on_dep_component_status)
        self.dep_update_thread.finished_signal.connect(self.on_dep_update_finished)
        self.dep_update_thread.start()
    
    def on_dep_update_finished(self, success, message):
        """依赖更新完成回调 - 静默完成，无弹窗"""
        # 重置按钮状态
        if hasattr(self, 'update_button'):
            self.update_button.setEnabled(True)
            self.update_button.setToolTip(t("检查依赖更新"))
        
        if success:
            self.update_status(message, "success")
        else:
            self.update_status(f"❌ {message}", "error")
    
    def check_and_update_yt_dlp(self):
        """检查yt-dlp更新，如果有新版本则直接开始更新"""
        # 检查是否已在检查中
        if hasattr(self, 'update_thread') and self.update_thread.isRunning():
            return
        
        # 检查按钮是否为绿色（表示已知有新版本）
        if getattr(self, 'is_update_btn_green', False):
            # 按钮是绿色的，说明已经检查过有新版本，直接开始更新
            self.start_update_process()
            return
        
        # 标记为手动检查
        self.manual_check = True
        
        # 只有在按钮是绿色的（表示已知有新版本）时才重置样式，避免产生点击时的“差别感”
        if getattr(self, 'is_update_btn_green', False):
            self.reset_update_button_icon()
        
        # 设置按钮为检查中状态（保持只有图标）
        if hasattr(self, 'update_button'):
            # 不再禁用按钮，防止产生视觉“差别感”
            # 保持只有图标的状态
            self.update_button.setToolTip(t("正在检查yt-dlp更新..."))
        
        # 启动更新检查线程
        self.update_thread = UpdateThread()
        self.update_thread.progress_signal.connect(self.on_update_progress)
        self.update_thread.finished_signal.connect(self.on_update_finished)
        self.update_thread.start()
        
        # 更新状态
        self.update_status(t("🔄 正在连接GitHub官网检查yt-dlp版本..."), "info")
    
    def start_update_process(self):
        """开始更新过程"""
        reply = QMessageBox.question(
            self, 
            t("确认更新"), 
            t("确定要更新yt-dlp到最新版本吗？\n\n更新过程中请不要关闭程序。"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            # 设置按钮为更新中状态
            if hasattr(self, 'update_button'):
                self.update_button.setEnabled(False)
                self.update_button.setToolTip(t("正在更新yt-dlp..."))
                # 重置为默认样式，显示更新中状态
                self.reset_update_button_icon()
                self.stop_update_button_animation()
            
            # 启动实际的更新线程
            self.actual_update_thread = UpdateThread(perform_update=True)
            self.actual_update_thread.progress_signal.connect(self.on_update_progress)
            self.actual_update_thread.finished_signal.connect(self.on_actual_update_finished)
            self.actual_update_thread.start()
            
            self.update_status(t("🚀 正在更新yt-dlp..."), "info")
        else:
            # 用户点击“否”，回到初始状态
            self.reset_update_button_icon()
            self.stop_update_button_animation()
            self.update_status(t("已取消更新"), "normal")
    
    def periodic_license_check(self):
        """周期性检查授权状态"""
        if not self.license_manager:
            return
            
        # 刷新状态
        self.license_manager._refresh_status()
        can_use, message = self.license_manager.can_use_app()
        
        if not can_use:
            QMessageBox.warning(self, t("授权已失效"), t_format("您的授权状态已发生变化：\n{}\n\n程序即将退出。", message))
            self.close()
            sys.exit(0)
            
    def auto_check_version_on_startup(self):
        """应用启动时自动检查yt-dlp版本更新（静默检查，不弹窗）"""
        try:
            # 检查是否已在检查中
            if hasattr(self, 'update_thread') and self.update_thread.isRunning():
                return
            
            # 标记为自动检查（非手动检查）
            self.manual_check = False
            
            # 启动静默的版本检查线程
            self.update_thread = UpdateThread(perform_update=False)
            # 连接进度信号（但不在状态栏显示，避免干扰用户）
            # self.update_thread.progress_signal.connect(self.on_update_progress)
            self.update_thread.finished_signal.connect(self.on_startup_update_check_finished)
            self.update_thread.start()
            
        except Exception as e:
            # 静默失败，不影响应用启动
            print(t_format("启动时检查更新失败: {}", str(e)))

    def auto_check_dependencies_on_startup(self):
        """应用启动时自动检查并静默更新所有依赖组件（不受设置开关管控，始终执行）"""
        try:
            # 注意：工具组件自动更新不受 auto_check_update_on_startup 设置控制，始终执行
            
            # 检查是否已在检查中
            if hasattr(self, 'dep_update_thread') and self.dep_update_thread.isRunning():
                return
            
            # yt-dlp: 每次启动都自动更新
            auto_update_list = ['yt-dlp']
            
            # deno 和 ffmpeg: 每月（30天）自动更新一次
            import time as _time
            now = _time.time()
            one_month = 30 * 24 * 3600  # 30天（秒）
            
            last_deno_update = float(self.settings.value("last_auto_update_time_deno", 0) or 0)
            if now - last_deno_update >= one_month:
                auto_update_list.append('deno')
            
            last_ffmpeg_update = float(self.settings.value("last_auto_update_time_ffmpeg", 0) or 0)
            if now - last_ffmpeg_update >= one_month:
                auto_update_list.append('ffmpeg')
            
            # 启动静默的依赖检查+自动更新线程
            self.dep_update_thread = DependencyUpdateThread(
                perform_update=False,
                auto_update_components=auto_update_list
            )
            # 不连接进度信号，避免干扰用户
            self.dep_update_thread.component_signal.connect(self._on_auto_update_component_status)
            self.dep_update_thread.finished_signal.connect(self._on_auto_update_finished)
            self.dep_update_thread.start()
            
        except Exception as e:
            # 静默失败，不影响应用启动
            print(t_format("启动时自动更新依赖失败: {}", str(e)))
    
    def _on_auto_update_component_status(self, comp_name, current_ver, latest_ver, status):
        """自动更新组件状态回调（静默，仅日志）"""
        if status == "auto_updated":
            print(t_format("[AUTO-UPDATE] {}: {} → {} 更新成功", comp_name, current_ver, latest_ver))
            # 记录更新时间，用于月度更新周期判断
            import time as _time
            self.settings.setValue(f"last_auto_update_time_{comp_name}", _time.time())
        elif status == "update_available":
            print(t_format("[AUTO-UPDATE] {}: {} → {} 可更新（未在本次自动更新范围内）", comp_name, current_ver, latest_ver))
        elif status == "up_to_date":
            print(t_format("[AUTO-UPDATE] {}: {} 已是最新", comp_name, current_ver))
    
    def _on_auto_update_finished(self, success, message):
        """自动更新完成回调（静默，无弹窗）"""
        if success:
            print(t_format("[AUTO-UPDATE] 完成: {}", message))
        else:
            print(t_format("[AUTO-UPDATE] 出错: {}", message))
    
    def check_app_update_from_toolbar(self):
        """从工具栏点击软件更新按钮"""
        # 如果按钮已经是绿色（表示已知有新版本），直接显示下载提示
        if getattr(self, 'is_update_btn_green', False):
            payload = getattr(self, '_last_app_update_payload', {})
            download_url = payload.get("download_url") or payload.get("website_url") or DEFAULT_APP_WEBSITE_URL
            latest_version = payload.get("version", t("未知"))
            current_version = get_app_version()
            
            prompt = (
                t_format("发现新版本！\n\n当前版本: v{}\n最新版本: v{}\n\n是否现在打开下载页面？", current_version, latest_version)

            )
            reply = QMessageBox.question(
                self,
                t("软件更新"),
                prompt,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl(download_url))
            else:
                self.reset_update_button_icon()
            return

        # 否则，手动触发网络检查
        if hasattr(self, 'update_button'):
            self.update_button.setToolTip(t("正在检查软件更新..."))
            # 保持原有的提示，不禁用按钮，让用户有反馈
        self.check_app_update_from_settings(manual=True)

    def check_app_update_from_settings(self, manual=False):
        """检查网站上的软件版本更新。"""
        if hasattr(self, 'app_update_thread') and self.app_update_thread.isRunning():
            return

        self.manual_app_update_check = manual

        if manual:
            self.update_status(t("🔄 正在检查网站上的软件版本..."), "info")
        self._set_settings_app_update_card_state(
            state="checking",
            message=t("正在连接官网检查版本信息..."),
        )

        self.app_update_thread = AppUpdateCheckThread(DEFAULT_APP_UPDATE_METADATA_URL)
        self.app_update_thread.finished_signal.connect(self.on_app_update_check_finished)
        self.app_update_thread.start()

    def auto_check_app_update_on_startup(self):
        """启动时静默检查软件更新。"""
        try:
            if not self.settings.value("auto_check_update_on_startup", True, type=bool):
                return
            self.check_app_update_from_settings(manual=False)
        except Exception as e:
            print(t_format("启动时检查软件更新失败: {}", str(e)))

    def on_app_update_check_finished(self, success, message, payload):
        """处理软件更新检查结果。"""
        current_version = str((payload or {}).get("current_version") or get_app_version()).strip()
        latest_version = str((payload or {}).get("version") or current_version).strip()
        download_url = str((payload or {}).get("download_url") or "").strip()
        website_url = str((payload or {}).get("website_url") or DEFAULT_APP_WEBSITE_URL).strip()
        checked_at = datetime.now().strftime("%Y-%m-%d %H:%M")

        self._last_app_update_status = message
        self._last_app_update_payload = dict(payload or {})
        self._last_app_update_checked_at = checked_at

        if self.settings is not None:
            try:
                self.settings.setValue("last_app_update_check_at", checked_at)
                self.settings.setValue("last_app_update_version", latest_version)
                self.settings.setValue("last_app_update_download_url", download_url or website_url)
                self.settings.setValue("last_app_update_has_update", bool(success and message == "update_available"))
            except Exception:
                pass

        try:
            if success and message == "update_available":
                self._set_settings_app_update_card_state(
                    state="update_available",
                    message=t_format("发现新版本 v{}，可从官网下载最新安装包。", latest_version),
                    current_version=current_version,
                    latest_version=latest_version,
                    download_url=download_url or website_url,
                    website_url=website_url,
                    checked_at=checked_at,
                )
            elif success:
                self._set_settings_app_update_card_state(
                    state="current",
                    message=t_format("当前已是最新版本 v{}", current_version),
                    current_version=current_version,
                    latest_version=latest_version,
                    download_url=download_url or website_url,
                    website_url=website_url,
                    checked_at=checked_at,
                )
            else:
                self._set_settings_app_update_card_state(
                    state="error",
                    message=message,
                    current_version=current_version,
                    latest_version=latest_version,
                    download_url=download_url or website_url,
                    website_url=website_url,
                    checked_at=checked_at,
                )
        except Exception:
            pass

        if not success:
            self.update_status(f"❌ {message}", "error")
            if hasattr(self, 'update_button'):
                self.update_button.setToolTip(t("检查软件更新"))
            if getattr(self, "manual_app_update_check", False):
                QMessageBox.warning(self, t("软件更新检查失败"), message)
                self.manual_app_update_check = False
            return

        if message == "update_available":
            self.update_status(t_format("🎆 发现软件新版本: {}", latest_version), "success")
            self.set_update_button_green()
            self.play_update_notification_sound()
            if getattr(self, 'enable_update_notification', True):
                prompt = (
                    t_format("发现新版本！\n\n当前版本: v{}\n最新版本: v{}\n", current_version, latest_version)

                )
                if download_url:
                    prompt += "\n是否现在打开下载页面？"
                    reply = QMessageBox.question(
                        self,
                        t("软件更新"),
                        prompt,
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.Yes
                    )
                    if reply == QMessageBox.Yes:
                        QDesktopServices.openUrl(QUrl(download_url))
                    else:
                        self.reset_update_button_icon()
                elif getattr(self, "manual_app_update_check", False):
                    QMessageBox.information(self, t("软件更新"), prompt)
            else:
                self.update_status(t_format("✨ 发现新版本 {}，已关闭弹窗提醒，可手动点击更新按钮升级", latest_version), "info")
        else:
            self.update_status(t_format("✅ 软件已是最新版本 v{}", current_version), "success")
            if hasattr(self, 'update_button'):
                self.update_button.setToolTip(t("检查软件更新"))
            if getattr(self, "manual_app_update_check", False):
                QMessageBox.information(
                    self,
                    t("软件更新"),
                    t_format("当前已是最新版本。\n\n当前版本: v{}", current_version)
                )

        self.manual_app_update_check = False

    def _open_app_update_url(self, prefer_download=False):
        """打开软件更新相关链接。"""
        payload = getattr(self, "_last_app_update_payload", {}) or {}
        download_url = str(payload.get("download_url") or "").strip()
        website_url = str(payload.get("website_url") or DEFAULT_APP_WEBSITE_URL).strip()
        target = download_url if prefer_download and download_url else website_url
        if target:
            QDesktopServices.openUrl(QUrl(target))

    def _set_settings_app_update_card_state(
        self,
        state="idle",
        message="",
        current_version="",
        latest_version="",
        download_url="",
        website_url="",
        checked_at="",
    ):
        """同步设置页软件更新卡片状态。"""
        current_version = str(current_version or get_app_version()).strip()
        latest_version = str(latest_version or t("未检查")).strip()
        website_url = str(website_url or DEFAULT_APP_WEBSITE_URL).strip()
        download_url = str(download_url or "").strip()
        checked_at = str(
            checked_at
            or getattr(self, "_last_app_update_checked_at", "")
            or self.settings.value("last_app_update_check_at", "", type=str)
            or ""
        ).strip()

        palette = {
            "idle": {
                "badge_bg": "#eef4ff",
                "badge_fg": "#285ea8",
                "panel_bg": "#f8f9fb",
                "panel_border": "#e3e7ed",
                "status_fg": "#5f6b7a",
            },
            "checking": {
                "badge_bg": "#fff7e8",
                "badge_fg": "#9a6700",
                "panel_bg": "#fffaf0",
                "panel_border": "#f2d6a2",
                "status_fg": "#8c5a00",
            },
            "update_available": {
                "badge_bg": "#e9f8ef",
                "badge_fg": "#1c7c45",
                "panel_bg": "#f3fbf6",
                "panel_border": "#b6e1c5",
                "status_fg": "#1f6f43",
            },
            "current": {
                "badge_bg": "#eef7ff",
                "badge_fg": "#2463a6",
                "panel_bg": "#f6fbff",
                "panel_border": "#cfe0f2",
                "status_fg": "#355d86",
            },
            "error": {
                "badge_bg": "#ffefef",
                "badge_fg": "#b13a3a",
                "panel_bg": "#fff7f7",
                "panel_border": "#efc5c5",
                "status_fg": "#b13a3a",
            },
        }.get(state, {
            "badge_bg": "#eef4ff",
            "badge_fg": "#285ea8",
            "panel_bg": "#f8f9fb",
            "panel_border": "#e3e7ed",
            "status_fg": "#5f6b7a",
        })

        if hasattr(self, "settings_app_update_card") and self.settings_app_update_card is not None:
            self.settings_app_update_card.setStyleSheet(
                f"background-color: transparent; border: none; color: #111827;"
            )

        if hasattr(self, "settings_app_update_badge") and self.settings_app_update_badge is not None:
            badge_text = {
                "idle": t("官网更新"),
                "checking": t("检查中"),
                "update_available": t("发现新版本"),
                "current": t("已是最新"),
                "error": t("检查失败"),
            }.get(state, t("官网更新"))
            self.settings_app_update_badge.setText(badge_text)
            self.settings_app_update_badge.setStyleSheet(
                f"background-color: transparent; border: none; color: {palette['badge_fg']}; font-size: 12px; font-weight: 700;"
            )

        if hasattr(self, "settings_app_update_status") and self.settings_app_update_status is not None:
            self.settings_app_update_status.setText(message or "点击下方按钮检查官网新版本")
            self.settings_app_update_status.setStyleSheet(
                f"color: {palette['status_fg']}; font-size: 12px; line-height: 1.5;"
            )

        if hasattr(self, "settings_app_update_current_value") and self.settings_app_update_current_value is not None:
            self.settings_app_update_current_value.setText(f"v{current_version}")

        if hasattr(self, "settings_app_update_latest_value") and self.settings_app_update_latest_value is not None:
            latest_text = f"v{latest_version}" if latest_version and latest_version != "未检查" else "未检查"
            self.settings_app_update_latest_value.setText(latest_text)
            if state == "update_available":
                self.settings_app_update_latest_value.setStyleSheet("color: #1c7c45; font-size: 13px; font-weight: 700;")
            elif state == "error":
                self.settings_app_update_latest_value.setStyleSheet("color: #b13a3a; font-size: 13px; font-weight: 700;")
            else:
                self.settings_app_update_latest_value.setStyleSheet("color: #1f2937; font-size: 13px; font-weight: 700;")

        if hasattr(self, "settings_app_update_last_check_value") and self.settings_app_update_last_check_value is not None:
            self.settings_app_update_last_check_value.setText(checked_at or "未检查")

        if hasattr(self, "settings_check_app_update_btn") and self.settings_check_app_update_btn is not None:
            self.settings_check_app_update_btn.setEnabled(state != "checking")
            self.settings_check_app_update_btn.setText(t("检查中...") if state == "checking" else t("检查软件更新"))

        if hasattr(self, "settings_open_website_btn") and self.settings_open_website_btn is not None:
            self.settings_open_website_btn.setEnabled(True)
            self.settings_open_website_btn.setToolTip(website_url)

    def on_startup_dep_check_finished(self, success, message):
        """处理启动时依赖检查完成（静默处理，只在发现新版本时才设置按钮颜色）"""
        try:
            if success:
                # 检查是否有可更新的组件
                if "可更新" in message or "个组件可更新" in message:
                    # 发现新版本，设置按钮为绿色
                    self.set_update_button_green()
                    # 播放悦耳的提示音
                    self.play_update_notification_sound()
                    # 在状态栏显示简短提示（不弹窗）
                    self.update_status(t_format("✨ {}，点击更新按钮即可升级", message), "success")
        except Exception as e:
            # 静默处理错误
            print(t_format("处理启动依赖检查结果时出错: {}", str(e)))
    
    def on_startup_update_check_finished(self, success, message, changelog):
        """处理启动时更新检查完成（静默处理，只在发现新版本时才提示）"""
        try:
            if success:
                # 解析消息
                parts = message.split('|')
                if len(parts) >= 3:
                    status = parts[0]
                    current_version = parts[1]
                    latest_version = parts[2]
                    
                    if status == "update_available":
                        # 发现新版本，设置按钮为绿色
                        self.set_update_button_green()
                        # 更新工具提示，告知用户有新版本
                        if hasattr(self, 'update_button'):
                            self.update_button.setToolTip(
                                t_format("🎆 发现新版本！\n\n当前版本: {}\n最新版本: {}\n\n点击此按钮立即更新", current_version, latest_version)

                            )
                        # 在状态栏显示简短提示（不弹窗）
                        self.update_status(t_format("✨ 发现 yt-dlp 新版本 {}，点击更新按钮即可升级", latest_version), "success")
                    elif status == "current":
                        # 已是最新版本，保持默认样式，不做任何提示
                        if hasattr(self, 'update_button'):
                            self.update_button.setToolTip(
                                t_format("✅ 当前版本已是最新\n\n版本: {}\n\n点击可手动检查更新", current_version)

                            )
            # 检查失败时也静默处理，不影响用户体验
        except Exception as e:
            # 静默处理错误，不影响应用使用
            print(t_format("处理启动更新检查结果时出错: {}", str(e)))
    
    def on_actual_update_finished(self, success, message, changelog):
        """处理实际更新完成"""
        # 重置按钮状态
        if hasattr(self, 'update_button'):
            self.update_button.setEnabled(True)
            self.reset_update_button_icon()
        
        if success:
            # 解析消息，检查是否为版本更新成功消息
            if message.startswith("success|"):
                parts = message.split('|')
                if len(parts) >= 3:
                    old_version = parts[1]
                    new_version = parts[2]
                    self.update_status(t_format("✅ yt-dlp更新成功！{} → {}", old_version, new_version), "success")
                    QMessageBox.information(
                        self, 
                        t("更新成功"), 
                        t_format("yt-dlp已成功更新！\n\n原版本: {}\n新版本: {}\n\n建议重启程序以确保更新生效。", old_version, new_version)

                    )
                else:
                    self.update_status(t("✅ yt-dlp更新完成！"), "success")
                    QMessageBox.information(
                        self, 
                        t("更新完成"), 
                        t("yt-dlp已成功更新到最新版本！\n\n建议重启程序以确保更新生效。")
                    )
            else:
                # 其他成功消息（如已是最新版本）
                self.update_status(f"✅ {message}", "success")
                QMessageBox.information(self, t("提示"), message)
        else:
            # 更新失败
            self.update_status(t_format("❌ 更新失败: {}", message), "error")
            QMessageBox.warning(self, t("更新失败"), t_format("更新过程中发生错误：\n\n{}\n\n请尝试：\n1. 以管理员身份运行程序\n2. 手动从 GitHub 下载最新版本\n3. 使用 pip install --upgrade yt-dlp 命令更新", message))
    
    def show_settings_dialog(self):
        """显示应用设置对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle(t("应用设置"))
        # 移除右上角的"?"帮助按钮
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setFixedWidth(560)
        dialog.setModal(True)

        dialog.setStyleSheet("""
            QDialog {
                background: #f6f7f9;
            }
            QLabel {
                color: #1f2937;
            }
            QLineEdit {
                background: #ffffff;
                border: 1px solid #dde3ea;
                border-radius: 8px;
                padding: 0 12px;
                color: #18212f;
                selection-background-color: #dce9ff;
            }
            QLineEdit:focus {
                border: 1px solid #8aaef2;
            }
            QCheckBox {
                color: #374151;
                spacing: 8px;
                font-size: 12px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #cfd6df;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #2f7cf6;
                border: 1px solid #2f7cf6;
            }
            QPushButton {
                min-height: 34px;
                border-radius: 8px;
                padding: 0 16px;
                font-weight: 600;
            }
            QFrame[card="true"] {
                background: #ffffff;
                border: 1px solid #e4e7ec;
                border-radius: 12px;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        title_label = QLabel(t("设置"))
        title_label.setStyleSheet("font-size: 22px; font-weight: 700; color: #111827;")
        layout.addWidget(title_label)

        subtitle_label = QLabel(t("保存目录与软件更新"))
        subtitle_label.setStyleSheet("font-size: 12px; color: #6b7280;")
        layout.addWidget(subtitle_label)

        def make_section_card():
            card = QFrame()
            card.setProperty("card", True)
            return card

        # ==================== 文件保存位置设置 ====================
        save_group = make_section_card()
        save_layout = QVBoxLayout(save_group)
        save_layout.setContentsMargins(16, 16, 16, 16)
        save_layout.setSpacing(10)

        save_title = QLabel(t("统一保存位置"))
        save_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        save_layout.addWidget(save_title)

        save_hint = QLabel(t("视频下载、转码输出、图片处理共用同一个目录。"))
        save_hint.setWordWrap(True)
        save_hint.setStyleSheet("font-size: 12px; color: #6b7280;")
        save_layout.addWidget(save_hint)

        path_layout = QHBoxLayout()
        path_layout.setSpacing(10)
        self.settings_save_path = QLineEdit()
        self.settings_save_path.setPlaceholderText(t("选择统一保存目录..."))
        self.settings_save_path.setMinimumHeight(42)
        # 加载当前保存的路径
        current_path = self.settings.value("output_path", os.path.expanduser("~/Downloads"))
        self.settings_save_path.setText(current_path)
        path_layout.addWidget(self.settings_save_path)

        browse_btn = QPushButton(t("浏览目录"))
        browse_btn.setFixedWidth(104)
        browse_btn.setStyleSheet("""
            QPushButton {
                background: #f3f6fb;
                color: #315b9a;
                border: 1px solid #dde5ef;
            }
            QPushButton:hover {
                background: #ebf0f7;
            }
            QPushButton:pressed {
                background: #e4e9f0;
            }
        """)
        browse_btn.clicked.connect(lambda: self._browse_settings_path())
        path_layout.addWidget(browse_btn)
        save_layout.addLayout(path_layout)

        layout.addWidget(save_group)

        # ==================== 界面语言设置 ====================
        lang_group = make_section_card()
        lang_layout = QVBoxLayout(lang_group)
        lang_layout.setContentsMargins(16, 16, 16, 16)
        lang_layout.setSpacing(10)

        lang_title = QLabel(t("界面语言"))
        lang_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        lang_layout.addWidget(lang_title)

        lang_hint = QLabel(t("选择软件界面显示的语言，切换后重启软件生效。"))
        lang_hint.setWordWrap(True)
        lang_hint.setStyleSheet("font-size: 12px; color: #6b7280;")
        lang_layout.addWidget(lang_hint)

        self.settings_lang_combo = QComboBox()
        for _lang_code, _lang_name in SUPPORTED_LANGUAGES:
            self.settings_lang_combo.addItem(_lang_name, _lang_code)
        _cur_lang = get_language()
        _cur_idx = self.settings_lang_combo.findData(_cur_lang)
        self.settings_lang_combo.setCurrentIndex(_cur_idx if _cur_idx >= 0 else 0)
        self.settings_lang_combo.setMinimumHeight(40)
        lang_layout.addWidget(self.settings_lang_combo)
        layout.addWidget(lang_group)

        # ==================== 软件更新设置 ====================
        update_group = make_section_card()
        update_layout = QVBoxLayout(update_group)
        update_layout.setContentsMargins(16, 16, 16, 16)
        update_layout.setSpacing(10)

        update_header_row = QHBoxLayout()
        update_header_row.setSpacing(10)

        update_title = QLabel(t("软件更新"))
        update_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        update_header_row.addWidget(update_title)
        update_layout.addLayout(update_header_row)

        switch_layout = QVBoxLayout()
        switch_layout.setSpacing(8)

        self.settings_update_notification = QCheckBox(t("启用更新提示音和弹窗"))
        # 加载当前设置
        enable_notification = self.settings.value("enable_update_notification", True, type=bool)
        self.enable_update_notification = enable_notification
        self.settings_update_notification.setChecked(enable_notification)
        switch_layout.addWidget(self.settings_update_notification)

        self.settings_auto_check_update = QCheckBox(t("启动时自动检查软件与依赖更新"))
        auto_check = self.settings.value("auto_check_update_on_startup", True, type=bool)
        self.settings_auto_check_update.setChecked(auto_check)
        switch_layout.addWidget(self.settings_auto_check_update)

        switch_container = QWidget()
        switch_container.setLayout(switch_layout)
        switch_container.setStyleSheet("""
            QWidget {
                background: transparent;
                border: none;
            }
        """)
        update_layout.addWidget(switch_container)

        app_update_card = QWidget()
        app_update_card.setStyleSheet("""
            QWidget {
                background-color: transparent;
                border: none;
            }
        """)
        app_update_card_layout = QVBoxLayout(app_update_card)
        app_update_card_layout.setContentsMargins(0, 0, 0, 0)
        app_update_card_layout.setSpacing(8)

        app_update_header_row = QHBoxLayout()
        app_update_header_row.setSpacing(10)

        app_update_title = QLabel(t("官网版本状态"))
        app_update_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #111827;")
        app_update_title.setFrameShape(QFrame.NoFrame)
        app_update_header_row.addWidget(app_update_title)

        app_update_header_row.addStretch()

        self.settings_app_update_badge = QLabel(t("官网更新"))
        self.settings_app_update_badge.setAlignment(Qt.AlignCenter)
        self.settings_app_update_badge.setFrameShape(QFrame.NoFrame)
        app_update_header_row.addWidget(self.settings_app_update_badge)
        app_update_card_layout.addLayout(app_update_header_row)

        self.settings_app_update_status = QLabel(t("点击下方按钮检查官网新版本"))
        self.settings_app_update_status.setWordWrap(True)
        self.settings_app_update_status.setStyleSheet("color: #6b7280; font-size: 12px;")
        self.settings_app_update_status.setFrameShape(QFrame.NoFrame)
        app_update_card_layout.addWidget(self.settings_app_update_status)

        info_layout = QGridLayout()
        info_layout.setContentsMargins(0, 2, 0, 2)
        info_layout.setHorizontalSpacing(14)
        info_layout.setVerticalSpacing(6)

        current_label = QLabel(t("当前版本"))
        current_label.setStyleSheet("font-size: 12px; color: #6b7280; background: transparent; border: none;")
        current_label.setFrameShape(QFrame.NoFrame)
        self.settings_app_update_current_value = QLabel(f"v{get_app_version()}")
        self.settings_app_update_current_value.setStyleSheet("font-size: 12px; color: #111827; font-weight: 700; background: transparent; border: none;")
        self.settings_app_update_current_value.setFrameShape(QFrame.NoFrame)
        info_layout.addWidget(current_label, 0, 0)
        info_layout.addWidget(self.settings_app_update_current_value, 0, 1)

        latest_label = QLabel(t("官网版本"))
        latest_label.setStyleSheet("font-size: 12px; color: #6b7280; background: transparent; border: none;")
        latest_label.setFrameShape(QFrame.NoFrame)
        self.settings_app_update_latest_value = QLabel(t("未检查"))
        self.settings_app_update_latest_value.setStyleSheet("font-size: 12px; color: #111827; font-weight: 700; background: transparent; border: none;")
        self.settings_app_update_latest_value.setFrameShape(QFrame.NoFrame)
        info_layout.addWidget(latest_label, 1, 0)
        info_layout.addWidget(self.settings_app_update_latest_value, 1, 1)

        last_check_label = QLabel(t("上次检查"))
        last_check_label.setStyleSheet("font-size: 12px; color: #6b7280; background: transparent; border: none;")
        last_check_label.setFrameShape(QFrame.NoFrame)
        self.settings_app_update_last_check_value = QLabel(t("未检查"))
        self.settings_app_update_last_check_value.setStyleSheet("font-size: 12px; color: #111827; font-weight: 700; background: transparent; border: none;")
        self.settings_app_update_last_check_value.setFrameShape(QFrame.NoFrame)
        info_layout.addWidget(last_check_label, 2, 0)
        info_layout.addWidget(self.settings_app_update_last_check_value, 2, 1)

        app_update_card_layout.addLayout(info_layout)

        app_update_btn_row = QHBoxLayout()
        app_update_btn_row.setSpacing(10)
        self.settings_check_app_update_btn = QPushButton(t("检查软件更新"))
        self.settings_check_app_update_btn.setFixedSize(126, 36)
        self.settings_check_app_update_btn.setStyleSheet("""
            QPushButton {
                background-color: #2f7cf6;
                color: white;
                border: none;
                padding: 0 18px;
            }
            QPushButton:hover {
                background-color: #276fe1;
            }
            QPushButton:pressed {
                background-color: #235fc2;
            }
            QPushButton:disabled {
                background-color: #9cbcf7;
            }
        """)
        self.settings_check_app_update_btn.clicked.connect(lambda: self.check_app_update_from_settings(manual=True))
        app_update_btn_row.addWidget(self.settings_check_app_update_btn)

        self.settings_open_website_btn = QPushButton(t("访问官网"))
        self.settings_open_website_btn.setFixedSize(126, 36)
        self.settings_open_website_btn.setStyleSheet("""
            QPushButton {
                background-color: #2f7cf6;
                color: white;
                border: none;
                padding: 0 18px;
            }
            QPushButton:hover {
                background-color: #276fe1;
            }
            QPushButton:pressed {
                background-color: #235fc2;
            }
        """)
        self.settings_open_website_btn.clicked.connect(lambda: self._open_app_update_url(prefer_download=False))
        app_update_btn_row.addWidget(self.settings_open_website_btn)
        app_update_btn_row.setAlignment(Qt.AlignLeft)

        app_update_card_layout.addLayout(app_update_btn_row)

        update_layout.addWidget(app_update_card)
        self.settings_app_update_card = app_update_card

        self._set_settings_app_update_card_state(
            state="update_available" if self.settings.value("last_app_update_has_update", False, type=bool) else "idle",
            message=(
                t_format("发现新版本 v{}，可从官网下载最新安装包。", getattr(self, '_last_app_update_payload', {}).get('version', ''))
                if self.settings.value("last_app_update_has_update", False, type=bool)
                and getattr(self, "_last_app_update_payload", {})
                else t("点击下方按钮检查官网新版本")
            ),
            current_version=get_app_version(),
            latest_version=(
                getattr(self, "_last_app_update_payload", {}).get("version")
                or self.settings.value("last_app_update_version", "", type=str)
            ),
            download_url=(
                getattr(self, "_last_app_update_payload", {}).get("download_url")
                or self.settings.value("last_app_update_download_url", "", type=str)
            ),
            website_url=(
                getattr(self, "_last_app_update_payload", {}).get("website_url")
                or DEFAULT_APP_WEBSITE_URL
            ),
            checked_at=(
                getattr(self, "_last_app_update_checked_at", "")
                or self.settings.value("last_app_update_check_at", "", type=str)
            ),
        )
        
        layout.addWidget(update_group)
        
        # ==================== 按钮区域 ====================
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton(t("取消"))
        cancel_btn.setMinimumWidth(92)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: #ffffff;
                color: #243041;
                border: 1px solid #dbe1e8;
            }
            QPushButton:hover {
                background: #f7f9fc;
            }
            QPushButton:pressed {
                background: #eef2f7;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton(t("保存"))
        save_btn.setMinimumWidth(98)
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #38a169;
                color: white;
                border: none;
            }
            QPushButton:hover {
                background-color: #31915f;
            }
            QPushButton:pressed {
                background-color: #2b7d52;
            }
        """)
        save_btn.clicked.connect(lambda: self._save_settings(dialog))
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)
        dialog.finished.connect(lambda _: self._clear_settings_dialog_refs())

        dialog.exec_()

    def _clear_settings_dialog_refs(self):
        """清理设置对话框中的临时控件引用。"""
        for attr_name in (
            "settings_app_update_card",
            "settings_app_update_badge",
            "settings_app_update_status",
            "settings_app_update_current_value",
            "settings_app_update_latest_value",
            "settings_app_update_last_check_value",
            "settings_check_app_update_btn",
            "settings_open_website_btn",
        ):
            try:
                setattr(self, attr_name, None)
            except Exception:
                pass
    
    def _browse_settings_path(self):
        """浏览选择保存目录"""
        current = self.settings_save_path.text() or os.path.expanduser("~/Downloads")
        directory = QFileDialog.getExistingDirectory(self, t("选择统一保存目录"), current)
        if directory:
            self.settings_save_path.setText(directory)
    
    def _save_settings(self, dialog):
        """保存设置"""
        # 保存统一保存路径
        save_path = self.settings_save_path.text().strip()
        if save_path:
            self.settings.setValue("output_path", save_path)
            # 同步更新各个输入框
            if hasattr(self, 'output_path'):
                self.output_path.setText(save_path)
            if hasattr(self, 'image_output_dir'):
                self.image_output_dir.setText(save_path)
                self.settings.setValue("image_output_dir", save_path)
        
        # 保存更新提示设置
        enable_notification = self.settings_update_notification.isChecked()
        self.settings.setValue("enable_update_notification", enable_notification)
        self.enable_update_notification = enable_notification
        
        # 保存启动时自动检查更新设置
        auto_check = self.settings_auto_check_update.isChecked()
        self.settings.setValue("auto_check_update_on_startup", auto_check)

        # 保存界面语言（重启后生效）
        try:
            new_lang = self.settings_lang_combo.currentData() or "zh_CN"
            old_lang = get_language()
            self.settings.setValue("language", new_lang)
            if new_lang != old_lang:
                set_language(new_lang)
                QMessageBox.information(
                    self, t("界面语言"),
                    t_format("语言已切换为「{lang}」。重启软件后，界面将完全显示为所选语言。", lang=language_display_name(new_lang)),
                )
        except Exception:
            pass

        # 显示成功提示
        self.update_status(t("✅ 设置已保存"), "success")
        dialog.accept()
    
    def play_update_notification_sound(self):
        """播放悦耳的更新通知提示音"""
        # 检查是否启用了更新提示
        if not getattr(self, 'enable_update_notification', True):
            return
            
        try:
            import winsound
            # 播放 Windows 系统"叮咚"提示音 (Mail beep / asterisk)
            # MB_ICONASTERISK = 0x40 - 这是一个悦耳的提示音，适合通知新版本
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception as e:
            # 如果无法播放系统声音，静默失败
            print(t_format("播放提示音失败: {}", e))

    def play_completion_notification_sound(self):
        """播放任务完成提示音"""
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception as e:
            print(t_format("播放完成提示音失败: {}", e))
    
    def reset_update_button_icon(self):
        """重置更新按钮图标颜色"""
        if hasattr(self, 'update_button'):
            self.is_update_btn_green = False
            # 重置为默认图标
            self.update_button.setIcon(self._create_svg_icon(self.svg_upgrade, self._active_icon_color()))
            self.update_button.setStyleSheet(self.common_btn_style)
            self.update_button.setToolTip(t("检查依赖更新"))
    
    def set_update_button_green(self):
        """设置更新按钮为绿色，表示有新版本可用"""
        if hasattr(self, 'update_button'):
            self.is_update_btn_green = True
            # 设置绿色背景 - 结构必须与 common_btn_style 完全一致，防止布局跳动
            self.update_button.setStyleSheet("""
                QPushButton {
                    border: 1px solid #3d8b40;
                    border-radius: 6px;
                    background-color: #4CAF50;
                    padding: 0px;
                    margin: 0px;
                    outline: none;
                    color: white;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #45a049;
                    border-color: #387a3b;
                }
                QPushButton:pressed {
                    background-color: #387a3b;
                    border-color: #2e6330;
                }
                QPushButton:focus {
                    background-color: #4CAF50;
                    border-color: #3d8b40;
                }
                QPushButton:focus:hover {
                    background-color: #45a049;
                    border-color: #387a3b;
                }
                QPushButton:focus:pressed {
                    background-color: #387a3b;
                    border-color: #2e6330;
                }
                QPushButton:disabled {
                    background-color: #4CAF50;
                    border-color: #3d8b40;
                }
            """)
            self.update_button.setIcon(self._create_svg_icon(self.svg_upgrade, "#FFFFFF"))
            self.update_button.setToolTip(t("🎆 发现新版本！点击更新"))
    
    def on_update_progress(self, message):
        """处理更新进度消息"""
        self.update_status(message, "loading")
    
    def on_update_finished(self, success, message, changelog):
        """处理更新检查完成"""
        # 重置按钮状态
        if hasattr(self, 'update_button'):
            # 恢复提示语
            self.update_button.setToolTip(t("检查依赖更新"))
            
        if success:
            # 解析消息
            parts = message.split('|')
            if len(parts) >= 3:
                status = parts[0]
                current_version = parts[1]
                latest_version = parts[2]
                
                if status == "current":
                    # 已是最新版本，重置按钮样式
                    self.reset_update_button_icon()
                    self.update_status(t_format("✅ 已是最新版本，无需更新", ), "success")
                    # 只在手动检查时显示弹窗
                    if hasattr(self, 'manual_check') and self.manual_check:
                        QMessageBox.information(
                            self, 
                            t("版本检查"), 
                            t_format("已是最新版本，无需更新\n\n当前版本: {}", current_version)
                        )
                        self.manual_check = False
                elif status == "update_available":
                    # 发现新版本，设置按钮为绿色
                    self.set_update_button_green()
                    self.update_status(t_format("🎆 发现新版本: {}", latest_version), "success")
                    
                    # 不再显示弹窗，只在状态栏显示信息
                    # 用户可以通过点击绿色的更新按钮来更新
            else:
                # 重置按钮样式（未知状态）
                self.reset_update_button_icon()
                self.update_status(message, "success")
        else:
            # 检查失败，重置按钮样式
            self.reset_update_button_icon()
            self.update_status(f"❌ {message}", "error")
            # 只在手动检查时显示错误弹窗
            if hasattr(self, 'manual_check') and self.manual_check:
                QMessageBox.warning(self, t("检查失败"), t_format("检查更新时发生错误:\n\n{}", message))
                self.manual_check = False
    
    def show_update_choice_dialog(self, current_version, latest_version, changelog):
            """显示更新选择对话框"""
            dialog = QDialog(self)
            dialog.setWindowTitle(t("发现新版本"))
            dialog.setFixedSize(600, 500)
            dialog.setModal(True)
            
            layout = QVBoxLayout(dialog)
            
            # 标题
            title_label = QLabel(t("🎆 发现 yt-dlp 新版本！"))
            title_label.setStyleSheet("""
                QLabel {
                    font-size: 18px;
                    font-weight: bold;
                    color: #2e7d32;
                    padding: 10px;
                    text-align: center;
                }
            """)
            title_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(title_label)
            
            # 版本信息
            version_info = QLabel(t_format("当前版本: {}\n最新版本: {}", current_version, latest_version))
            version_info.setStyleSheet("""
                QLabel {
                    font-size: 14px;
                    padding: 10px;
                    background-color: #f5f5f5;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                }
            """)
            version_info.setAlignment(Qt.AlignCenter)
            layout.addWidget(version_info)
            
            # 更新日志
            changelog_label = QLabel(t("新版本功能介绍:"))
            changelog_label.setStyleSheet("font-weight: bold; font-size: 14px; margin-top: 10px;")
            layout.addWidget(changelog_label)
            
            changelog_text = QTextEdit()
            changelog_text.setReadOnly(True)
            changelog_text.setMaximumHeight(200)
            
            # 格式化更新日志
            if changelog:
                formatted_changelog = self.format_changelog(changelog)
                changelog_text.setHtml(formatted_changelog)
            else:
                changelog_text.setPlainText(t("暂无详细更新信息"))
            
            layout.addWidget(changelog_text)
            
            # 按钮区域
            button_layout = QHBoxLayout()
            
            # 取消按钮
            cancel_button = QPushButton(t("稍后更新"))
            cancel_button.setStyleSheet("""
                QPushButton {
                    padding: 8px 16px;
                    font-size: 14px;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    background-color: #f8f9fa;
                }
                QPushButton:hover {
                    background-color: #e9ecef;
                }
            """)
            cancel_button.clicked.connect(dialog.reject)
            button_layout.addWidget(cancel_button)
            
            button_layout.addStretch()
            
            # 更新按钮
            update_button = QPushButton(t("🚀 立即更新"))
            update_button.setStyleSheet("""
                QPushButton {
                    padding: 8px 16px;
                    font-size: 14px;
                    font-weight: bold;
                    color: white;
                    background-color: #4CAF50;
                    border: none;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
                QPushButton:pressed {
                    background-color: #3d8b40;
                }
            """)
            update_button.clicked.connect(dialog.accept)
            update_button.setDefault(True)
            button_layout.addWidget(update_button)
            
            layout.addLayout(button_layout)
            
            # 显示对话框
            if dialog.exec_() == QDialog.Accepted:
                # 用户选择更新
                self.start_actual_update(latest_version)
            else:
                # 用户选择稍后更新，保持绿色按钮
                self.update_status(t("用户选择稍后更新"), "info")
    
    def format_changelog(self, changelog):
        """格式化更新日志为HTML"""
        if not changelog:
            return "暂无详细更新信息"
        
        # 简单的HTML格式化
        html = "<div style='font-family: Arial, sans-serif; line-height: 1.6;'>"
        
        lines = changelog.split('\n')
        for line in lines[:20]:  # 只显示前20行
            line = line.strip()
            if line:
                if line.startswith('#'):
                    # 标题
                    html += f"<h4 style='color: #2e7d32; margin: 10px 0 5px 0;'>{line.replace('#', '').strip()}</h4>"
                elif line.startswith('-') or line.startswith('*'):
                    # 列表项
                    html += f"<li style='margin: 3px 0;'>{line[1:].strip()}</li>"
                else:
                    # 普通文本
                    html += f"<p style='margin: 5px 0;'>{line}</p>"
        
        html += "</div>"
        return html
    
    def start_actual_update(self, latest_version):
        """开始实际更新yt-dlp"""
        self.update_status(t_format("🚀 开始更新到版本 {}...", latest_version), "info")
        
        # 这里可以添加实际的更新逻辑
        # 例如调用 yt-dlp -U 命令
        QMessageBox.information(self, t("更新提示"), t("更新功能将在后续版本中实现"))
            

    
    def update_progress(self, text):
        """更新进度显示"""
        self.progress_text.append(text)
        # 自动滚动到底部
        self.progress_text.verticalScrollBar().setValue(self.progress_text.verticalScrollBar().maximum())
    

    
    # 旧的绕过配置方法已移除，现在使用高级选项中的限制绕过设置
    
    def get_video_info(self):
        """获取视频信息而不下载"""
        url_text = self.url_input.text().strip()
        if not url_text:
            QMessageBox.warning(self, t("错误"), t("请输入视频URL"))
            return
            
        # 从输入文本中提取URL
        extracted_url = self.extract_url_from_text(url_text)
        
        # 如果提取的URL与原始文本不同，显示提示信息并更新输入框
        if extracted_url != url_text:
            self.progress_text.append(t_format("已自动从输入内容中提取URL: {}", extracted_url))
            self.url_input.setText(extracted_url)
            
        # 使用提取后的URL
        url = extracted_url
        self._requested_formats_url = url
        self.progress_text.append(t_format("[日志] 发起格式列表请求 URL: {}", url))
        
        # 清空进度显示
        self.progress_text.clear()
        self.progress_text.append(t("正在获取视频信息..."))
        
        # 创建并启动信息获取线程，传递播放列表设置
        playlist_enabled = self.playlist_check.isChecked()
        self.download_thread = DownloadThread(url, None, None, [], extract_info=True, playlist_enabled=playlist_enabled)
        self.download_thread.progress_signal.connect(self.update_progress)
        self.download_thread.finished_signal.connect(self.download_finished)
        self.download_thread.info_signal.connect(self.display_video_info)
        self.download_thread.failed_videos_signal.connect(self.show_failed_videos)
        self.download_thread.start()
        
        # 更新UI状态
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        
    def open_advanced_format_dialog(self):
        """打开高级格式选择对话框"""
        url_text = self.url_input.text().strip()
        if not url_text:
            QMessageBox.warning(self, t("提示"), t("请先输入视频URL，然后获取可用格式列表"))
            return
        # 如果有缓存但与当前URL不匹配，强制重新获取
        if self.available_formats and self._formats_cache_url and self._formats_cache_url != url_text:
            self.progress_text.append(t_format("[日志] 检测到缓存与当前URL不匹配 (缓存: {}, 当前: {})，将重新获取格式列表", self._formats_cache_url, url_text))
            self._pending_advanced_dialog = True
            self.list_available_formats()
            return
        
        # 如果还没有获取格式列表，先获取
        if not hasattr(self, 'available_formats') or not self.available_formats:
            reply = QMessageBox.question(self, t("获取格式列表"), 
                                      t("需要先获取可用格式列表才能使用高级选择功能。\n\n是否现在获取？"),
                                      QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                # 先获取格式列表，然后在回调中打开高级对话框
                self._pending_advanced_dialog = True
                self.list_available_formats()
            return
        
        # 打开高级格式选择对话框
        try:
            dialog = AdvancedFormatDialog(self.available_formats, self)
            dialog.format_selected.connect(self.on_advanced_format_selected)
            
            if dialog.exec_() == QDialog.Accepted:
                selected_expression = dialog.get_selected_format()
                if selected_expression:
                    # 将选择的表达式设置到自定义格式
                    self.format_combo.setCurrentText(t("自定义"))
                    self.custom_format.setText(selected_expression)
                    self.progress_text.append(t_format("✅ 已选择高级格式表达式: {}", selected_expression))
                    
                    # 显示表达式说明
                    self.show_format_expression_help(selected_expression)
        except Exception as e:
            QMessageBox.warning(self, t("错误"), t_format("打开高级格式选择器时出错: {}", str(e)))
    
    def on_advanced_format_selected(self, format_expression):
        """处理高级格式选择结果"""
        self.format_combo.setCurrentText(t("自定义"))
        self.custom_format.setText(format_expression)
        self.progress_text.append(t_format("✅ 已应用高级格式表达式: {}", format_expression))
    
    def show_format_expression_help(self, expression):
        """显示格式表达式的说明"""
        help_text = ""
        
        # 根据表达式内容提供相应的说明
        if "bv*+ba/b" in expression:
            help_text = "官方推荐默认格式：优先选择最佳视频+最佳音频，回退到最佳合并格式"
        elif "bestvideo+bestaudio" in expression:
            help_text = "最佳视频+音频：分别下载最佳视频流和音频流并合并"
        elif "height<=" in expression:
            help_text = "分辨率限制：限制视频分辨率不超过指定值"
        elif "ext=" in expression:
            help_text = "格式限制：优先选择指定的文件格式"
        elif "vcodec" in expression:
            help_text = "编码器限制：限制或优先选择特定的视频编码器"
        elif "filesize<" in expression:
            help_text = "文件大小限制：限制文件大小不超过指定值"
        else:
            help_text = "自定义格式表达式，将按照yt-dlp官方语法执行"
        
        if help_text:
            self.progress_text.append(t_format("💡 格式说明: {}", help_text))

    def list_available_formats(self):
        """获取视频可用的格式列表"""
        url_text = self.url_input.text().strip()
        if not url_text:
            QMessageBox.warning(self, t("错误"), t("请输入视频URL"))
            return
            
        # 从输入文本中提取URL
        extracted_url = self.extract_url_from_text(url_text)
        
        # 如果提取的URL与原始文本不同，显示提示信息并更新输入框
        if extracted_url != url_text:
            self.progress_text.append(t_format("已自动从输入内容中提取URL: {}", extracted_url))
            self.url_input.setText(extracted_url)
            
        # 使用提取后的URL
        url = extracted_url
        self._requested_formats_url = url
        self.progress_text.append(t_format("[日志] 发起格式列表请求 URL: {}", url))
        
        # 清空进度显示
        self.progress_text.clear()
        self.progress_text.append(t("正在获取可用格式列表..."))
        
        # 创建并启动格式列表获取线程
        self.download_thread = DownloadThread(url, None, None, [], list_formats=True)
        self.download_thread.progress_signal.connect(self.update_progress)
        self.download_thread.finished_signal.connect(self.download_finished)
        self.download_thread.formats_signal.connect(self.show_format_list_dialog)  # 修改为显示对话框
        self.download_thread.failed_videos_signal.connect(self.show_failed_videos)
        self.download_thread.start()
        
        # 更新UI状态
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        
    def show_format_list_dialog(self, formats):
        """显示格式列表对话框"""
        try:
            current_url = self.url_input.text().strip()
        except Exception:
            current_url = None
        if self._requested_formats_url and current_url and self._requested_formats_url != current_url:
            self.progress_text.append(t_format("[日志] 收到过期的格式列表响应 (响应对应: {}, 当前: {})，已忽略", self._requested_formats_url, current_url))
            return
        self.available_formats = formats
        self._formats_cache_url = self._requested_formats_url
        
        if formats:
            # 自动更新格式ID下拉菜单，使其可以下拉选择
            self.update_format_combo_with_selected("best", formats)
            # 检查是否为X平台的自动格式选择
            if (len(formats) == 1 and 
                formats[0].get('format_id') == 'best' and 
                'X平台自动选择' in formats[0].get('description', '')):
                
                # X平台自动选择，不显示对话框，直接设置最佳格式
                self.progress_text.append(t("\n<b>X平台自动格式选择:</b>"))
                self.progress_text.append(t("✅ 已自动选择最佳格式，无需手动选择"))
                self.progress_text.append(t("ℹ️ 可直接开始下载，系统将使用最优格式"))
                
                # 自动设置为最佳质量选项
                self.format_combo.setCurrentText(t("最佳质量"))
                return
            
            # 在进度文本框中显示格式信息
            self.progress_text.append(t("\n<b>可用格式列表:</b>"))
            self.progress_text.append(t("格式ID | 扩展名 | 分辨率/音频质量 | 描述"))
            self.progress_text.append("-" * 80)
            
            # 检查是否有待处理的高级对话框请求
            if hasattr(self, '_pending_advanced_dialog') and self._pending_advanced_dialog:
                self._pending_advanced_dialog = False
                # 直接打开高级格式选择对话框
                try:
                    dialog = AdvancedFormatDialog(formats, self)
                    dialog.format_selected.connect(self.on_advanced_format_selected)
                    
                    if dialog.exec_() == QDialog.Accepted:
                        selected_expression = dialog.get_selected_format()
                        if selected_expression:
                            # 将选择的表达式设置到自定义格式
                            self.format_combo.setCurrentText(t("自定义"))
                            self.custom_format.setText(selected_expression)
                            self.progress_text.append(t_format("✅ 已选择高级格式表达式: {}", selected_expression))
                            
                            # 显示表达式说明
                            self.show_format_expression_help(selected_expression)
                except Exception as e:
                    QMessageBox.warning(self, t("错误"), t_format("打开高级格式选择器时出错: {}", str(e)))
                return
            
            # 创建并显示格式列表对话框
            dialog = FormatListDialog(formats, self)
            if dialog.exec_():
                # 如果用户选择了格式，更新下拉菜单
                selected_format_id = dialog.get_selected_format_id()
                if selected_format_id:
                    self.update_format_combo_with_selected(selected_format_id, formats)
            else:
                # 用户取消了对话框，显示提示信息
                self.progress_text.append(t("已取消格式选择"))
                return
            
            # 显示提示信息
            self.progress_text.append(t("\n<b>提示:</b> 已选择格式ID，可以开始下载。"))
        else:
            self.progress_text.append(t("未找到可用格式，请尝试其他预设格式选项"))
            self.format_combo.setCurrentText(t("最佳质量"))
            QMessageBox.warning(self, t("提示"), t("未找到可用格式，已自动切换到'最佳质量'选项。"))
    
    def _on_url_text_changed(self, _text):
        try:
            self.available_formats = []
            self._formats_cache_url = None
            self._requested_formats_url = None
            self._video_info_url = None
            self.video_info = None
            self._pending_download_title = ""
            self._pending_media_capture_download = False
            self._pending_media_capture_format_id = ""
            self.progress_text.append(t("[日志] 检测到URL变更，已清空格式缓存"))
            if self.download_thread and self.download_thread.isRunning() and getattr(self.download_thread, 'list_formats', False):
                try:
                    self._safe_disconnect_download_signals()
                except Exception:
                    pass
                try:
                    self.download_thread.stop()
                except Exception:
                    pass
        except Exception:
            pass
    
    def update_format_combo_with_selected(self, format_id, formats):
        """根据选择的格式ID更新下拉菜单"""
        self.format_id_combo.clear()
        
        # 添加"自动选择最佳质量"选项
        self.format_id_combo.addItem(t("自动选择最佳质量"), "best")
        
        # 添加所有可用格式
        for fmt in formats:
            fmt_id = fmt.get('format_id', '')
            ext = fmt.get('ext', '')
            resolution = fmt.get('resolution', '')
            description = fmt.get('description', '')
            
            display_text = f"{fmt_id} | {ext} | {resolution} | {description}"
            self.format_id_combo.addItem(display_text, fmt_id)
            
            # 如果是选中的格式ID，设置为当前选中项
            if fmt_id == format_id:
                self.format_id_combo.setCurrentText(display_text)
        
        # 如果选择的是"best"，设置为第一项
        if format_id == "best":
            self.format_id_combo.setCurrentIndex(0)
        
        # 自动选择指定格式ID选项
        self.format_combo.setCurrentText(t("指定格式ID"))
        self.format_id_combo.setEnabled(True)
    
    def show_failed_videos(self, failed_videos):
        """显示播放列表中失败的视频列表"""
        # 【关键修复】无论是否有失败视频，都显示播放列表下载报告（如果启用播放列表功能）
        # 检查download_thread是否存在，以及是否启用了播放列表功能
        if not hasattr(self, 'download_thread'):
            return
        
        playlist_enabled = getattr(self.download_thread, 'playlist_enabled', False)
        if not playlist_enabled:
            return
        
        # 获取统计信息
        total_videos = 0
        successful_downloads = 0
        expected_downloads = 0
        skipped_videos = []  # 获取跳过的视频列表
        
        if hasattr(self, 'download_thread'):
            total_videos = getattr(self.download_thread, 'total_videos', 0)
            successful_downloads = getattr(self.download_thread, 'successful_downloads', 0)
            expected_downloads = getattr(self.download_thread, 'expected_download_count', 0)
            skipped_videos = getattr(self.download_thread, 'skipped_videos', [])
        
        # 【修复】不再合并跳过视频到失败列表中，只显示真正的失败视频
        # 这样报告只会显示：预计下载数、成功下载数、失败下载数、失败详情
        true_failed_videos = list(failed_videos) if failed_videos else []
        
        # 显示播放列表下载报告对话框（无论是否有失败）
        dialog = FailedVideosDialog(
            true_failed_videos,  # 只包含真正失败的视频
            total_videos=total_videos,
            successful_downloads=successful_downloads,
            expected_downloads=expected_downloads,
            skipped_videos=skipped_videos,
            parent=self
        )
        dialog.exec_()
    
    def display_video_info(self, info):
        """显示视频信息"""
        self.video_info = info
        self._video_info_url = self.url_input.text().strip()
        
        # 在进度文本框中显示视频信息
        self.progress_text.clear()
        
        # 检查是否是播放列表
        if info.get('_type') == 'playlist':
            self._display_playlist_info(info)
            return
        
        # 显示视频基本信息
        self.progress_text.append(t_format("<b>标题:</b> {}", info.get('title', '未知')))
        self.progress_text.append(t_format("<b>上传者:</b> {}", info.get('uploader', '未知')))
        
        # 显示时长信息（如果有）
        if 'duration_string' in info:
            self.progress_text.append(t_format("<b>时长:</b> {}", info.get('duration_string')))
        elif 'duration' in info:
            # 如果只有秒数，转换为时分秒格式
            duration_sec = info.get('duration')
            if isinstance(duration_sec, (int, float)):
                m, s = divmod(int(duration_sec), 60)
                h, m = divmod(m, 60)
                duration_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
                self.progress_text.append(t_format("<b>时长:</b> {}", duration_str))
        
        # 显示其他可能有用的信息
        if 'upload_date' in info:
            date_str = info.get('upload_date', '')
            if len(date_str) == 8:  # 格式通常为YYYYMMDD
                formatted_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
                self.progress_text.append(t_format("<b>上传日期:</b> {}", formatted_date))
        
        if 'view_count' in info:
            view_count = info.get('view_count')
            if view_count is not None:
                self.progress_text.append(t_format("<b>观看次数:</b> {:,}", view_count))
        
        if 'like_count' in info:
            like_count = info.get('like_count')
            if like_count is not None:
                self.progress_text.append(t_format("<b>点赞数:</b> {:,}", like_count))
        
        # 显示可用格式
        formats = info.get('formats', [])
        if formats:
            self.progress_text.append(t("\n<b>可用格式:</b>"))
            
            # 检查格式列表的结构
            if all(isinstance(fmt, dict) for fmt in formats):
                # 标准格式列表
                for fmt in formats:
                    format_id = fmt.get('format_id', t('未知'))
                    format_note = fmt.get('format_note', '')
                    resolution = fmt.get('resolution', t('未知'))
                    ext = fmt.get('ext', t('未知'))
                    
                    # 构建格式描述
                    format_desc = f"ID: {format_id}"
                    if format_note:
                        format_desc += t_format(" | 格式: {}", format_note)
                    if resolution and resolution != '未知':
                        format_desc += t_format(" | 分辨率: {}", resolution)
                    if ext and ext != '未知':
                        format_desc += t_format(" | 扩展名: {}", ext)
                    
                    # 添加更多有用信息（如果有）
                    if 'filesize' in fmt and fmt['filesize']:
                        size_mb = fmt['filesize'] / (1024 * 1024)
                        format_desc += t_format(" | 大小: {:.2f} MB", size_mb)
                    elif 'filesize_approx' in fmt and fmt['filesize_approx']:
                        size_mb = fmt['filesize_approx'] / (1024 * 1024)
                        format_desc += t_format(" | 大小(约): {:.2f} MB", size_mb)
                    
                    self.progress_text.append(format_desc)
            else:
                # 简化格式列表（来自备用方法）
                for fmt in formats:
                    if isinstance(fmt, dict) and 'description' in fmt:
                        self.progress_text.append(fmt['description'])
                    elif isinstance(fmt, str):
                        self.progress_text.append(fmt)
        else:
            self.progress_text.append(t("\n<b>注意:</b> 未找到格式信息，可能需要使用'获取可用格式'按钮获取详细格式列表。"))
        
        # 显示字幕信息并自动设置
        self._display_and_auto_set_subtitles(info)
        
        # 显示视频描述（如果有且不太长）
        if 'description' in info and info['description']:
            desc = info['description']
            # 限制描述长度，避免UI过长
            max_desc_length = 500
            if len(desc) > max_desc_length:
                desc = desc[:max_desc_length] + "... (描述已截断)"
            
            self.progress_text.append(t("\n<b>视频描述:</b>"))
            self.progress_text.append(desc)
    
    def _display_playlist_info(self, playlist_info):
        """显示播放列表信息"""
        # 存储播放列表信息
        self.current_playlist_info = playlist_info
        
        # 显示播放列表标题
        self.progress_text.append(t_format("<b>播放列表标题:</b> {}", playlist_info.get('title', '未知播放列表')))
        
        # 显示条目数量
        entries = playlist_info.get('entries', [])
        self.progress_text.append(t_format("<b>视频数量:</b> {}", len(entries)))
        
        # 显示播放列表中的视频
        if entries:
            self.progress_text.append(t("\n<b>播放列表中的视频:</b>"))
            # 限制显示的条目数量，避免UI过长
            max_entries = 20
            for i, entry in enumerate(entries[:max_entries]):
                title = entry.get('title', t_format("视频 #{}", i + 1))
                self.progress_text.append(f"{i+1}. {title}")
            
            if len(entries) > max_entries:
                self.progress_text.append(t_format("... 还有 {} 个视频未显示", len(entries) - max_entries))
        
        # 提示用户如何下载播放列表
        self.progress_text.append(t("\n<b>提示:</b> 要下载整个播放列表，请在'高级选项'中勾选'下载播放列表'选项。"))
        self.progress_text.append(t("<b>智能选择:</b> 点击'智能选择范围'按钮可以可视化选择下载范围。"))
        
        # 自动勾选播放列表选项并启用智能选择按钮
        self.playlist_check.setChecked(True)
        self.playlist_range_button.setEnabled(True)
    
    def _display_and_auto_set_subtitles(self, info):
        """显示字幕信息并自动设置字幕选项"""
        # 获取字幕信息
        subtitles = info.get('subtitles', {})
        automatic_captions = info.get('automatic_captions', {})
        
        # 合并手动字幕和自动字幕
        all_subtitles = {}
        all_subtitles.update(subtitles)
        all_subtitles.update(automatic_captions)
        self._last_preview_subtitle_langs = sorted(all_subtitles.keys())
        
        if all_subtitles:
            self.progress_text.append(t("\n<b>可用字幕:</b>"))
            
            # 显示可用的字幕语言
            subtitle_langs = list(all_subtitles.keys())
            subtitle_langs.sort()
            
            # 按类型分组显示
            manual_subs = [lang for lang in subtitle_langs if lang in subtitles]
            auto_subs = [lang for lang in subtitle_langs if lang in automatic_captions and lang not in subtitles]
            
            if manual_subs:
                self.progress_text.append(t_format("<b>手动字幕:</b> {}", ', '.join(manual_subs)))
            
            if auto_subs:
                self.progress_text.append(t_format("<b>自动字幕:</b> {}", ', '.join(auto_subs)))
            
            # 自动选择字幕语言的优先级
            preferred_langs = ['zh-CN', 'zh', 'en', 'ja', 'ko']
            selected_lang = None
            
            # 首先尝试找到首选语言的手动字幕
            for lang in preferred_langs:
                if lang in manual_subs:
                    selected_lang = lang
                    break
            
            # 如果没有找到首选的手动字幕，尝试自动字幕
            if not selected_lang:
                for lang in preferred_langs:
                    if lang in auto_subs:
                        selected_lang = lang
                        break
            
            # 如果还是没有找到，选择第一个可用的字幕
            if not selected_lang and subtitle_langs:
                selected_lang = subtitle_langs[0]
            
            # 自动设置字幕选项
            if selected_lang:
                # 自动勾选下载字幕
                self.subtitle_check.setChecked(True)
                
                # 查找并设置对应的语言选项
                for i in range(self.subtitle_lang.count()):
                    lang_code = self.subtitle_lang.itemData(i)
                    if lang_code == selected_lang:
                        self.subtitle_lang.setCurrentIndex(i)
                        break
                else:
                    # 如果下拉框中没有对应的语言，添加它
                    lang_name = self._get_language_name(selected_lang)
                    self.subtitle_lang.addItem(f"{lang_name} ({selected_lang})", selected_lang)
                    self.subtitle_lang.setCurrentIndex(self.subtitle_lang.count() - 1)
                
                # 启用字幕语言选择
                self.subtitle_lang.setEnabled(True)
                
                # 显示自动选择的结果
                lang_name = self._get_language_name(selected_lang)
                subtitle_type = "手动字幕" if selected_lang in manual_subs else "自动字幕"
                self.progress_text.append(t_format("<b>已自动选择:</b> {} ({}) - {}", lang_name, selected_lang, subtitle_type))
            
        else:
            self.progress_text.append(t("\n<b>字幕信息:</b> 此视频没有可用的字幕"))
            self._last_preview_subtitle_langs = []
            # 如果没有字幕，取消勾选字幕选项
            self.subtitle_check.setChecked(False)
            self.subtitle_lang.setEnabled(False)
    
    def _get_language_name(self, lang_code):
        """根据语言代码获取语言名称"""
        lang_names = {
            'zh-CN': '中文(简体)',
            'zh-TW': '中文(繁体)',
            'zh': '中文',
            'en': '英文',
            'ja': '日文',
            'ko': '韩文',
            'fr': '法文',
            'de': '德文',
            'es': '西班牙文',
            'ru': '俄文',
            'it': '意大利文',
            'pt': '葡萄牙文',
            'ar': '阿拉伯文',
            'hi': '印地文',
            'th': '泰文',
            'vi': '越南文',
            'id': '印尼文',
            'ms': '马来文',
            'tr': '土耳其文',
            'pl': '波兰文',
            'nl': '荷兰文',
            'sv': '瑞典文',
            'da': '丹麦文',
            'no': '挪威文',
            'fi': '芬兰文'
        }
        return lang_names.get(lang_code, lang_code.upper())
    
    # update_age_limit_bypass_status方法已移除，年龄限制绕过功能已删除

    def _on_playlist_toggled(self, checked):
        """联动播放列表相关控件；取消勾选时恢复范围框初始状态。"""
        enabled = bool(checked)
        if hasattr(self, 'playlist_range'):
            self.playlist_range.setEnabled(enabled)
            if not enabled:
                # 恢复初始状态：清空范围输入内容
                self.playlist_range.clear()
        if hasattr(self, 'playlist_range_button'):
            # 只有启用播放列表且已获取到播放列表信息时才允许智能选择
            self.playlist_range_button.setEnabled(enabled and bool(getattr(self, 'current_playlist_info', None)))
        if hasattr(self, 'get_playlist_button'):
            # 未勾选"下载播放列表"时禁用"获取播放列表信息"按钮
            self.get_playlist_button.setEnabled(enabled)
    
    def get_playlist_info(self):
        """专门获取播放列表信息"""
        url_text = self.url_input.text().strip()
        if not url_text:
            QMessageBox.warning(self, t("错误"), t("请输入URL"))
            return
            
        # 从输入文本中提取URL
        extracted_url = self.extract_url_from_text(url_text)
        
        # 如果提取的URL与原始文本不同，显示提示信息并更新输入框
        if extracted_url != url_text:
            self.progress_text.append(t_format("已自动从输入内容中提取URL: {}", extracted_url))
            self.url_input.setText(extracted_url)
            
        # 使用提取后的URL
        url = extracted_url
        
        # 清空进度显示
        self.progress_text.clear()
        self.progress_text.append(t("正在获取播放列表信息..."))
        self.progress_text.append(t("提示: 此功能专门用于获取博主主页、频道页面等的播放列表信息"))
        
        # 创建并启动播放列表信息获取线程
        self.download_thread = DownloadThread(url, None, None, [], extract_playlist=True)
        self.download_thread.progress_signal.connect(self.update_progress)
        self.download_thread.finished_signal.connect(self.download_finished)
        self.download_thread.info_signal.connect(self.display_video_info)
        self.download_thread.failed_videos_signal.connect(self.show_failed_videos)
        self.download_thread.start()
        
        # 更新UI状态
        self.get_playlist_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
    
    def open_playlist_range_dialog(self):
        """打开播放列表范围选择对话框"""
        if not self.current_playlist_info:
            QMessageBox.warning(self, t("警告"), t("没有可用的播放列表信息，请先获取播放列表信息。"))
            return
        
        dialog = PlaylistRangeDialog(self.current_playlist_info, self)
        if dialog.exec_() == QDialog.Accepted:
            selected_range = dialog.get_selected_range()
            if selected_range:
                self.playlist_range.setText(selected_range)
                QMessageBox.information(self, t("成功"), t_format("已设置播放列表范围: {}", selected_range))
    
    def get_additional_options(self):
        is_proxy_enabled = False  # 初始化（后续代理设置段会重新赋值）

        # 获取附加选项
        options = []
        preview_langs = getattr(self, "_last_preview_subtitle_langs", [])
        
        # 检查高级选项是否启用
        print(t_format("[调试] get_additional_options: advanced_group.isChecked() = {}", self.advanced_group.isChecked()))
        if not self.advanced_group.isChecked():
            # 如果高级选项未启用，返回空选项
            print(t("[调试] 高级选项未启用，返回空列表"))
            return options
        
        # 高级选项（只有在高级选项启用时才生效）
        # 播放列表基本选项
        print(t_format("[调试] playlist_check.isChecked() = {}", self.playlist_check.isChecked()))
        if self.playlist_check.isChecked():
            options.append("--yes-playlist")
            print(t_format("[调试] playlist_range.text().strip() = '{}'", self.playlist_range.text().strip()))
            if self.playlist_range.text().strip():
                options.extend(["--playlist-items", self.playlist_range.text().strip()])
                print(t_format("DEBUG: 添加播放列表范围: --playlist-items {}", self.playlist_range.text().strip()))
            options.extend(["--skip-playlist-after-errors", "3"])  # 限制播放列表错误，避免拖慢整体
        else:
            options.append("--no-playlist")
            print(t("DEBUG: 禁用播放列表下载: --no-playlist"))
        
        # 播放列表排序、下载限制、文件大小过滤、日期过滤选项已移除
        
        # 字幕选项
        if self.subtitle_check.isChecked():
            options.append("--write-subs")
            selected_lang = self.subtitle_lang.currentData()
            # 添加调试信息
            print(t_format("DEBUG: 字幕选项 - 选中的语言数据: {}", selected_lang))
            print(t_format("DEBUG: 字幕选项 - 当前文本: {}", self.subtitle_lang.currentText()))
            print(t_format("DEBUG: 字幕选项 - 当前索引: {}", self.subtitle_lang.currentIndex()))
            
            if selected_lang and selected_lang != "all":
                lang_pattern, normalized_lang = build_subtitle_lang_pattern(
                    selected_lang,
                    available_langs=preview_langs,
                )
                options.extend(["--sub-langs", lang_pattern])
                print(t_format("DEBUG: 添加字幕语言匹配选项: --sub-langs {} (选择: {})", lang_pattern, normalized_lang))
            elif selected_lang == "all":
                lang_pattern, _ = build_subtitle_lang_pattern("all", available_langs=preview_langs)
                options.extend(["--sub-langs", lang_pattern])
                print(t_format("DEBUG: 添加全部字幕选项: --sub-langs {}", lang_pattern))
            else:
                default_langs, _ = build_subtitle_lang_pattern("", available_langs=preview_langs)
                options.extend(["--sub-langs", default_langs])
                print(t_format("DEBUG: 添加默认字幕选项: --sub-langs {}", default_langs))
        
        # 处理新增的字幕选项
        if hasattr(self, 'auto_subtitle_check') and self.auto_subtitle_check.isChecked():
            options.append("--write-auto-subs")
            print(t("DEBUG: 添加自动字幕选项: --write-auto-subs"))
        
        if hasattr(self, 'subtitle_format') and (self.subtitle_check.isChecked() or (hasattr(self, 'embed_subtitle_check') and self.embed_subtitle_check.isChecked())):
            selected_format = self.subtitle_format.currentData()
            if selected_format and selected_format != "best":
                options.extend(["--sub-format", selected_format])
                print(t_format("DEBUG: 添加字幕格式选项: --sub-format {}", selected_format))
            else:
                options.extend(["--sub-format", "srt/ass/vtt/best"])
                print(t("DEBUG: 使用字幕格式偏好: --sub-format srt/ass/vtt/best"))
        

        if hasattr(self, 'convert_subtitle_check') and self.convert_subtitle_check.isChecked():
            # 使用--convert-subs 转换字幕格式（支持 ass/lrc/srt/vtt），并强制UTF-8编码
            target = "srt"
            if hasattr(self, 'convert_subtitle_format'):
                fmt = self.convert_subtitle_format.currentData()
                if fmt in ("srt", "ass", "vtt", "lrc"):
                    target = fmt
            options.extend(["--convert-subs", target])
            options.extend(["--encoding", "utf-8"])
            print(t_format("DEBUG: 添加字幕格式转换选项: --convert-subs {}", target))
            print(t("DEBUG: 添加UTF-8编码选项: --encoding utf-8"))
        
        # 字幕嵌入选项 - 根据 yt-dlp 官方最佳实践优化
        if hasattr(self, 'embed_subtitle_check') and self.embed_subtitle_check.isChecked():
            options.append("--embed-subs")
            
            # 判断字幕下载状态
            write_subs_enabled = self.subtitle_check.isChecked() or (
                hasattr(self, 'auto_subtitle_check') and self.auto_subtitle_check.isChecked()
            )
            
            if write_subs_enabled:
                # 场景1: 同时勾选"下载字幕"和"嵌入字幕"
                # → yt-dlp 默认会保留外部字幕文件（新版本行为）
                print(t("DEBUG: 字幕嵌入+外部保留模式（同时下载和嵌入字幕）"))
                # 注意：如果将来需要嵌入后删除外部文件，可添加：
                # options.extend(["--compat-options", "no-keep-subs"])
            else:
                # 场景2: 只勾选"嵌入字幕"，不勾选"下载字幕"
                # → yt-dlp 会自动下载字幕用于嵌入，然后删除外部文件
                print(t("DEBUG: 仅嵌入字幕模式（自动下载后删除外部文件）"))
                
                # 确保指定字幕语言，否则可能下载不到字幕
                if not any('--sub-langs' in str(opt) for opt in options):
                    # 如果用户没有通过UI选择语言，添加默认语言
                    default_langs, _ = build_subtitle_lang_pattern("", available_langs=preview_langs)
                    options.extend(["--sub-langs", default_langs])
                    print(t_format("DEBUG: 为仅嵌入模式添加默认字幕语言: {}", default_langs))
        
        # 确保 ffmpeg 可用：当需要嵌入/转换/嵌入缩略图时指定位置
        ffmpeg_path = os.path.join(RUNTIME_APP_DIR, "ffmpeg.exe")
        if ((hasattr(self, 'embed_subtitle_check') and self.embed_subtitle_check.isChecked()) or 
            (hasattr(self, 'convert_subtitle_check') and self.convert_subtitle_check.isChecked()) or 
            (hasattr(self, 'embed_thumbnail_check') and self.embed_thumbnail_check.isChecked())):
            if os.path.isfile(ffmpeg_path):
                options.extend(["--ffmpeg-location", ffmpeg_path])
                print(t_format("DEBUG: 指定 ffmpeg 位置: {}", ffmpeg_path))
            else:
                print("WARN: 未找到 ffmpeg.exe，字幕嵌入/转换可能失败")
        
        # 字幕下载延迟选项
        if hasattr(self, 'subtitle_delay_spin') and (self.subtitle_check.isChecked() or (hasattr(self, 'embed_subtitle_check') and self.embed_subtitle_check.isChecked())):
            delay_value = self.subtitle_delay_spin.value()
            if delay_value > 0:
                options.extend(["--sleep-subtitles", str(delay_value)])
                print(t_format("DEBUG: 添加字幕下载延迟选项: --sleep-subtitles {}", delay_value))
        
        # 缩略图选项
        if self.thumbnail_check.isChecked():
            options.append("--write-thumbnail")
        
        # 元数据选项处理
        if self.write_description_check.isChecked():
            options.append("--write-description")
            print(t("DEBUG: 添加视频描述选项: --write-description"))
        
        if self.write_info_check.isChecked():
            options.append("--write-info-json")
            print(t("DEBUG: 添加JSON元数据选项: --write-info-json"))
        
        if self.embed_metadata_check.isChecked():
            options.append("--embed-metadata")
            print(t("DEBUG: 添加嵌入元数据选项: --embed-metadata"))
        
        if self.embed_thumbnail_check.isChecked():
            options.append("--embed-thumbnail")
            print(t("DEBUG: 添加嵌入缩略图选项: --embed-thumbnail"))
        
        if self.write_comments_check.isChecked():
            options.append("--write-comments")
            print(t("DEBUG: 添加评论选项: --write-comments"))
        
        # 嵌入章节信息
        if hasattr(self, 'embed_chapters_check') and self.embed_chapters_check.isChecked():
            options.append("--embed-chapters")
            print(t("DEBUG: 添加嵌入章节选项: --embed-chapters"))
        
        # 嵌入完整JSON信息
        if hasattr(self, 'embed_info_json_check') and self.embed_info_json_check.isChecked():
            options.append("--embed-info-json")
            print(t("DEBUG: 添加嵌入JSON信息选项: --embed-info-json"))
        
        # 写入扩展属性
        if hasattr(self, 'xattrs_check') and self.xattrs_check.isChecked():
            options.append("--xattrs")
            print(t("DEBUG: 添加扩展属性选项: --xattrs"))
        
        # 限速选项
        if self.limit_speed_check.isChecked():
            options.extend(["--limit-rate", f"{self.speed_limit.value()}K"])
        
        # 高级限制绕过选项
        if hasattr(self, 'advanced_bypass_mode'):
            bypass_mode = self.advanced_bypass_mode.currentText()
            platform = self.platform_selector.currentText()
            
            if bypass_mode != "关闭":
                # 年龄限制绕过
                if self.age_bypass_check.isChecked():
                    # 为了避免与DownloadThread中的YouTube 2025绕过策略冲突
                    # 只对非YouTube平台添加年龄绕过参数
                    if platform != "YouTube" and platform != "全平台 (推荐)":
                        options.extend(["--extractor-args", "youtube:bypass_age_gate=true"])
                    # 对其他平台添加年龄绕过
                    if "Bilibili" in platform:
                        options.extend(["--extractor-args", "bilibili:bypass_age_gate=true"])
                    print(t_format("DEBUG: 年龄限制绕过 - 平台: {}", platform))
                
                # 地理限制绕过
                if self.geo_bypass_check.isChecked():
                    options.append("--geo-bypass")
                    if bypass_mode == "高级模式 (推荐)":
                        options.extend(["--xff", "default"])
                    elif bypass_mode == "强制绕过":
                        options.extend(["--xff", "default"])
                        options.extend(["--geo-bypass-country", "US"])
                
                # 登录限制绕过
                if self.login_bypass_check.isChecked():
                    # Cookie处理逻辑已移除 - 限制绕过功能已被删除
                    pass
                
                # 会员限制绕过（实验性）
                if self.premium_bypass_check.isChecked():
                    # 避免与YouTube 2025绕过策略冲突，只对特定平台添加
                    if platform != "YouTube" and platform != "全平台 (推荐)":
                        options.extend(["--extractor-args", "youtube:bypass_premium=true"])
                    if "Bilibili" in platform:
                        options.extend(["--extractor-args", "bilibili:bypass_vip=true"])
                    print(t_format("DEBUG: 会员限制绕过 - 平台: {}", platform))
                
                # 重试设置（超时设置已在网络代理配置部分统一处理）
                if not is_proxy_enabled:  # 代理模式已在上面设置过重试
                    options.extend(["--retries", str(self.retry_count.value())])
        
        # 旧的简化地理限制选项已移除，现在统一使用高级限制绕过设置
        
        # 年龄限制绕过功能已移除，认证组件已删除
        
        # === 网络代理与连接优化 ===
        is_proxy_enabled = False
        if hasattr(self, 'proxy_check') and self.proxy_check.isChecked():
            if hasattr(self, 'proxy_input'):
                proxy_url = self.proxy_input.text().strip()
                if proxy_url:
                    is_proxy_enabled = True
                    options.extend(["--proxy", proxy_url])
                    print(t_format("DEBUG: ✓ 启用代理: {}", proxy_url))
                    # 注意：这里是在主窗口类中，没有progress_signal，不能emit
                    # self.progress_signal.emit(t_format("✓ 使用代理: {}", proxy_url))
        
        # 网络超时设置（代理模式下使用更长的超时时间）
        if hasattr(self, 'timeout_setting'):
            timeout = self.timeout_setting.value()
            # 如果启用了代理但超时时间还是默认30秒，建议增加
            if is_proxy_enabled and timeout == 30:
                timeout = 60
                print(t("DEBUG: 代理模式自动调整超时时间: 30秒 -> 60秒"))
                # 注意：这里是在主窗口类中，没有progress_signal，不能emit
                # self.progress_signal.emit("ℹ️ 代理模式：已自动增加超时时间到60秒")
            options.extend(["--socket-timeout", str(timeout)])
            print(t_format("DEBUG: 网络超时: {}秒", timeout))
        
        # IP版本选择
        if hasattr(self, 'ip_version_combo'):
            ip_version = self.ip_version_combo.currentText()
            if ip_version == "仅IPv4":
                options.append("--force-ipv4")
                print(t("DEBUG: 强制使用IPv4"))
            elif ip_version == "仅IPv6":
                options.append("--force-ipv6")
                print(t("DEBUG: 强制使用IPv6"))
        
        # 源地址绑定
        if hasattr(self, 'source_address_check') and self.source_address_check.isChecked():
            if hasattr(self, 'source_address_input'):
                source_ip = self.source_address_input.text().strip()
                if source_ip:
                    options.extend(["--source-address", source_ip])
                    print(t_format("DEBUG: 绑定源地址: {}", source_ip))
                    # 注意：这里是在主窗口类中，没有progress_signal，不能emit
                    # self.progress_signal.emit(t_format("✓ 绑定源地址: {}", source_ip))
        
        # === 下载速度与稳定性优化（根据代理状态自适应）===
        # 代理模式和正常模式使用不同的优化策略
        
        if is_proxy_enabled:
            # 代理模式：保守策略，优先稳定性
            print("DEBUG: 应用代理网络优化策略")
            # 注意：这里是在主窗口类中，没有progress_signal，不能emit
            # self.progress_signal.emit("📡 代理模式：应用保守网络优化策略")
            
            # 减少并发数（代理可能限制连接数）
            options.extend(["--concurrent-fragments", "3"])
            print("DEBUG: - 并发片段: 3 (代理优化)")
            
            # 减小HTTP块大小（降低超时风险）
            options.extend(["--http-chunk-size", "5M"])
            print("DEBUG: - HTTP块大小: 5MB (代理优化)")
            
            # 增加重试次数（应对代理不稳定）
            options.extend(["--retries", "10"])
            options.extend(["--fragment-retries", "10"])
            print("DEBUG: - 重试次数: 10次 (代理优化)")
            
            # 更保守的重试策略（指数退避）
            options.extend(["--retry-sleep", "exp=1:10:2"])
            print("DEBUG: - 重试策略: 指数退避 1-10秒")
            
            # 增大缓冲区（应对网络波动）
            options.extend(["--buffer-size", "128K"])
            print("DEBUG: - 缓冲区: 128KB")
            
            # 更激进的 throttled-rate 检测（低速时重新提取）
            options.extend(["--throttled-rate", "50K"])
            print("DEBUG: - 低速检测: 50KB/s")
            
            # 添加请求间延迟（避免代理限速）
            options.extend(["--sleep-requests", "0.5"])
            print("DEBUG: - 请求延迟: 0.5秒")
            
            # 代理环境下禁用 aria2c（可能不稳定）
            # 不添加 aria2c 相关选项
            
        else:
            # 正常模式：不在这里添加优化参数，由 _add_advanced_speed_optimizations() 处理
            print("DEBUG: 正常模式，优化参数由其他方法处理")
        
        # 通用配置（代理和正常模式都需要）
        # 检查是否启用时间片段下载
        time_section_enabled = hasattr(self, 'time_section_check') and self.time_section_check.isChecked()
        
        if not time_section_enabled:
            # 未启用时间片段：显式指定 HLS/DASH 使用原生下载器，提升稳定性
            options.extend(["--downloader", "dash,m3u8:native"])
            print("DEBUG: 使用原生下载器处理 HLS/DASH")
        else:
            # 启用时间片段：不设置下载器，让 yt-dlp 自动选择 ffmpeg
            print("DEBUG: 时间片段模式，使用 ffmpeg 处理下载")
        
        # === 时间片段下载选项 ===
        if hasattr(self, 'time_section_check') and self.time_section_check.isChecked():
            sections = self._get_download_sections()
            if not sections:
                raise ValueError("已启用时间片段下载，但尚未添加任何时间段或章节规则")

            time_ranges = [s for s in sections if s.startswith("*")]
            chapter_regexes = [s for s in sections if not s.startswith("*")]

            # 启用时再次做格式兜底校验，防止历史脏数据绕过 UI 校验
            invalid_sections = []
            for section in sections:
                valid, warning_msg, _ = self.validate_section_format(section)
                if not valid:
                    invalid_sections.append(t_format("{}: {}", section, warning_msg or '格式不正确'))
            if invalid_sections:
                raise ValueError("时间片段设置无效:\n" + "\n".join(invalid_sections[:8]))

            ffmpeg_available = False
            if time_ranges:
                try:
                    result = subprocess.run(
                        ['ffmpeg', '-version'],
                        capture_output=True,
                        timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    ffmpeg_available = (result.returncode == 0)
                except Exception:
                    ffmpeg_available = False

                if not ffmpeg_available:
                    raise ValueError(
                        "时间范围下载需要 ffmpeg，但当前未检测到 ffmpeg。\n"
                        "请先安装 ffmpeg 或仅使用章节正则规则。"
                    )

                print("DEBUG: 检测到 ffmpeg，可执行时间范围下载")

                # 时间范围模式且启用代理时，通过 ffmpeg downloader args 传递代理
                if is_proxy_enabled and hasattr(self, 'proxy_input'):
                    proxy_url = self.proxy_input.text().strip()
                    if proxy_url:
                        options.extend(["--downloader-args", f"ffmpeg:-http_proxy {proxy_url}"])
                        print(t_format("DEBUG: 为 ffmpeg 添加代理参数: {}", proxy_url))

            for section in sections:
                options.extend(["--download-sections", section])

            print(t_format("DEBUG: 添加时间片段下载选项: {}", sections))
            if chapter_regexes and not time_ranges:
                print("DEBUG: 当前为章节正则模式，无需 ffmpeg")
        
        # 章节相关选项
        if hasattr(self, 'split_chapters_check') and self.split_chapters_check.isChecked():
            options.append("--split-chapters")
            print("DEBUG: 添加按章节分割选项: --split-chapters")
        
        if hasattr(self, 'chapters_to_remove') and self.chapters_to_remove:
            # 合并所有章节名称为正则表达式
            regex = "|".join(self.chapters_to_remove)
            options.extend(["--remove-chapters", regex])
            print(t_format("DEBUG: 添加移除章节选项: --remove-chapters {}", regex))
        
        if hasattr(self, 'force_keyframes_check') and self.force_keyframes_check.isChecked():
            options.append("--force-keyframes-at-cuts")
            print("DEBUG: 添加强制关键帧选项: --force-keyframes-at-cuts")
        
        # 返回选项列表
        return options
        
    def _check_aria2c_available(self):
        """检查aria2c是否可用"""
        try:
            import subprocess
            result = subprocess.run(
                ["aria2c", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            return False
    
    def _add_advanced_speed_optimizations(self, options):
        """添加高级下载速度优化选项（安全、无递归）"""
        # HLS优化：使用 mpegts 容器，提升稳定性
        options.append("--hls-use-mpegts")
        
        # 禁用懒惰播放列表处理：提高响应速度
        options.append("--no-lazy-playlist")
        
        # 启用 throttled-rate 检测：低速时触发重新提取
        options.extend(["--throttled-rate", "100K"])
        
        # 注意：移除不安全选项 --no-check-certificates
        # 注意：移除递归调用，避免无限递归
        
        print("DEBUG: 已应用高级速度优化")
        print("DEBUG: - 并发片段: 6")
        print("DEBUG: - HTTP块大小: 10MB")
        print("DEBUG: - 片段重试: 3次")
        print("DEBUG: - 文件访问重试: 2次")
        if self._check_aria2c_available():
            print("DEBUG: - 外部下载器: aria2c 仅HTTP/HTTPS (8线程)")
        print("DEBUG: - 重试策略: 指数退避")
        print("DEBUG: - 网络超时: 30秒")
        print("DEBUG: - throttled-rate检测: 100KB/s")
        print("DEBUG: - 启用HLS mpegts优化")
        
        return options
    
    def extract_url_from_text(self, text):
        """从文本中提取URL
        
{{ ... }}
        参数:
            text: 包含URL的文本
            
        返回:
            提取到的URL或原始文本（如果未提取到URL）
        """
        if not text:
            return text
        
        # 首先尝试使用更强大的正则表达式提取URL
        # 这个正则表达式可以匹配大多数URL格式，包括带有复杂查询参数的URL
        url_pattern = re.compile(r'(https?://[\w\-\.]+(?:\:\d+)?(?:/[\w\-\.~!$&\'\(\)*+,;=:/?#\[\]%@]+)?)', re.IGNORECASE)
        url_match = url_pattern.search(text)
        
        if url_match:
            # 提取匹配到的URL
            extracted_url = url_match.group(0)
            
            # 处理特殊情况：如果URL被引号或括号包围，可能会漏掉部分内容
            # 检查URL后面是否有查询参数或锚点被截断
            if extracted_url.count('?') == 0 and '?' in text[url_match.end():] and '=' in text[url_match.end():text.find(' ', url_match.end()) if ' ' in text[url_match.end():] else len(text)]:
                # 找到URL后面的第一个空格位置
                next_space = text.find(' ', url_match.end())
                if next_space == -1:  # 如果没有空格，取到字符串结尾
                    next_space = len(text)
                # 扩展URL到下一个空格
                extracted_url = text[url_match.start():next_space]
            
            # 确保URL不以标点符号结尾（可能是文本中的标点）
            extracted_url = extracted_url.rstrip(',.;:"\'')
            
            # 处理YouTube shorts和其他特殊URL格式
            # 确保查询参数完整保留
            if 'youtube.com/shorts/' in extracted_url or 'youtu.be/' in extracted_url:
                # 检查是否有查询参数被截断
                if extracted_url.count('?') == 0 and '?' in text[url_match.start():] and '=' in text[url_match.start():text.find(' ', url_match.start()) if ' ' in text[url_match.start():] else len(text)]:
                    # 找到URL后的第一个空格或字符串结尾
                    next_space = text.find(' ', url_match.start())
                    if next_space == -1:
                        next_space = len(text)
                    # 重新提取完整URL，包括所有查询参数
                    extracted_url = text[url_match.start():next_space]
            
            return extracted_url
        
        # 如果正则表达式匹配失败，尝试简单的方法
        try:
            # 使用简单的正则表达式查找URL的起始位置
            url_start_match = re.search(r'https?://', text)
            if url_start_match:
                # 从URL开始位置截取到字符串结束
                full_url = text[url_start_match.start():]
                # 如果URL后面有空格，只保留到空格之前的部分
                if ' ' in full_url:
                    full_url = full_url.split(' ')[0]
                # 去除可能的结尾标点
                return full_url.rstrip(',.;:"\'')
        except Exception as e:
            # 记录异常信息
            print(t_format("URL提取异常: {}", str(e)))
            pass
            
        # URL提取模式
        url_patterns = [
            # 通用URL模式 - 更全面的模式，支持更多特殊字符和格式
            r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[\w\d._~:/?#[\]@!$&\'()*+,;=]+',
            # B站特定模式 - 支持多种格式
            r'https?://(?:www\.)?bilibili\.com/video/[A-Za-z0-9]+(?:[/?].*)?',
            r'https?://b23\.tv/[A-Za-z0-9]+(?:[/?].*)?',
            # YouTube特定模式 - 增强支持更多YouTube URL格式
            r'https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[A-Za-z0-9_-]+(?:[&?].*)?',
            # 改进YouTube shorts和live链接的提取，确保完整捕获查询参数
            r'https?://(?:www\.)?youtube\.com/(?:shorts|live)/[A-Za-z0-9_-]+(?:[/?].*)?',
            # 优酷
            r'https?://(?:www\.)?youku\.com/video/id_[A-Za-z0-9=]+',
            # 腾讯视频
            r'https?://(?:www\.)?v\.qq\.com/x/(?:page|cover|play)/[A-Za-z0-9]+',
            # 西瓜视频
            r'https?://(?:www\.)?ixigua\.com/[A-Za-z0-9]+',
            # 抖音
            r'https?://(?:www\.)?douyin\.com/(?:video|note)/[A-Za-z0-9]+'
        ]
        
        # 尝试匹配每个模式
        for pattern in url_patterns:
            matches = re.findall(pattern, text)
            if matches:
                # 返回第一个匹配的URL
                return matches[0]
        
        # 特殊处理：尝试查找任何形式的URL
        # 这是一个更宽松的模式，作为最后的尝试
        general_url_match = re.search(r'https?://\S+', text)
        if general_url_match:
            # 获取匹配的URL
            url = general_url_match.group(0)
            # 处理URL末尾可能的标点符号或其他非URL字符
            # 移除URL末尾的标点符号和空白字符
            url = re.sub(r'[.,;:"\'\s]+$', '', url)
            return url
                
        # 如果没有匹配到URL，返回原始文本
        return text
    
    def open_browser(self):
        """打开Playwright浏览器控制台"""
        try:
            # 获取URL输入框内容
            url = self.url_input.text().strip()
            
            # 若窗口已存在，优先复用，避免重复实例导致状态错乱
            if hasattr(self, 'browser_window') and self.browser_window:
                try:
                    self.browser_window._bubble.hideBubble()
                except Exception:
                    pass
                self.browser_window.showNormal()
                self.browser_window.show()
                self.browser_window.raise_()
                self.browser_window.activateWindow()
                if url:
                    self.browser_window.url_edit.setText(url)
                return
            
            # 使用新的Playwright浏览器控制器，并传入初始URL
            self.browser_window = PlaywrightBrowserController(parent=None, initial_url=url)
            
            # 连接媒体捕获信号：当用户选择下载媒体时，自动填入URL
            self.browser_window.media_url_selected.connect(self._on_media_url_received)
            
            self.browser_window.show()
            
        except Exception as e:
            self._show_browser_safe_message(QMessageBox.Critical, t("错误"), t_format("打开浏览器失败: {}", str(e)))
            print(f"Open browser error: {e}")
    
    def _on_media_url_received(self, url: str, title: str = ""):
        """处理从浏览器接收到的媒体URL"""
        # 提取格式ID（如果有）
        format_id = None
        clean_title = title
        if "[Format:" in title:
            import re
            match = re.search(r"\[Format:(.*?)\]", title)
            if match:
                format_id = match.group(1)
                clean_title = title.replace(match.group(0), "").strip()

        # 填入URL输入框
        self.url_input.setText(url)
        self._pending_download_title = choose_better_download_title(clean_title)
        self._pending_media_capture_download = True
        self._pending_media_capture_format_id = format_id or ""
        
        # 切换到下载选项卡
        self.tabs.setCurrentIndex(0)
        
        # 显示提示
        if hasattr(self, 'progress_text'):
            display_title = clean_title if clean_title else url[:80] + "..."
            self.progress_text.append(t_format("📡 从浏览器捕获媒体: {}", display_title))
            if format_id:
                self.progress_text.append(t_format("🎯 捕获到的媒体分辨率/格式: {}", format_id))
            self.progress_text.append(t("ℹ️ 本次媒体捕获下载将忽略下载器页面当前格式设置，自动使用兼容模式"))
        
        # 将主窗口带到前台
        self.showNormal()  # 确保不是最小化
        self.raise_()
        self.activateWindow()
        
        # 自动开始下载
        QTimer.singleShot(500, self.start_download)
    
    def _navigate_browser_to_url(self, url):
        """在Playwright浏览器中导航到URL"""
        if hasattr(self, 'browser_window') and self.browser_window:
            self.browser_window.url_edit.setText(url)
            # 触发启动浏览器并导航
            if not self.browser_window._browser_running:
                self.browser_window._launch_browser()
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(2000, lambda: self.browser_window._navigate())
            else:
                self.browser_window._navigate()

    def _probe_x_multi_video(self, url):
        """
        探测X平台（Twitter）推文是否包含多个视频。
        返回 entries 列表（每个元素是一个 dict），如果只有1个或0个视频则返回空列表。
        注意：该方法在主线程同步执行，需要在 start_download 之前调用。
        """
        try:
            cmd = [
                "yt-dlp.exe",
                "--dump-json",
                "--yes-playlist",   # 关键：必须用 --yes-playlist 才能拿到多个entries
                "--no-download",
                "--ignore-config",
                "--no-warnings",
                "--no-cache-dir",
                url,
            ]

            # 尝试附加Cookie（复用 DownloadThread 的 Cookie 查找逻辑）
            try:
                cookies_dir = os.path.join(RUNTIME_APP_DIR, "cookies")
                if os.path.isdir(cookies_dir):
                    # 查找X平台对应的Cookie文件
                    x_domains = ['x.com', 'twitter.com']
                    cookie_file = None
                    for domain in x_domains:
                        candidate = os.path.join(cookies_dir, f"{domain}_cookies.txt")
                        if os.path.isfile(candidate):
                            cookie_file = candidate
                            break
                    # 如果没有找到特定域名的Cookie，尝试最新的Cookie文件
                    if not cookie_file:
                        cookie_files = sorted(
                            glob.glob(os.path.join(cookies_dir, "*_cookies.txt")),
                            key=os.path.getmtime,
                            reverse=True,
                        )
                        if cookie_files:
                            cookie_file = cookie_files[0]
                    if cookie_file:
                        cmd.extend(["--cookies", cookie_file])
            except Exception:
                pass

            # 附加代理设置（如果高级选项启用了代理）
            try:
                if hasattr(self, 'advanced_group') and self.advanced_group.isChecked():
                    if hasattr(self, 'proxy_check') and self.proxy_check.isChecked():
                        proxy_url = self.proxy_input.text().strip()
                        if proxy_url:
                            cmd.extend(["--proxy", proxy_url])
            except Exception:
                pass

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                creationflags=creationflags,
            )

            stdout_bytes, stderr_bytes = process.communicate(timeout=30)
            stdout = decode_subprocess_bytes(stdout_bytes) if stdout_bytes else ""

            if process.returncode != 0:
                return []

            # yt-dlp --dump-json --yes-playlist 对于多视频推文会输出多行JSON（每行一个视频）
            entries = []
            for line in stdout.strip().splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue

            # 只有多于1个视频时才返回entries供选择
            if len(entries) > 1:
                return entries

            return []

        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                pass
            return []
        except Exception as e:
            print(t_format("[调试] X平台多视频探测失败: {}", e))
            return []

    def start_download(self):
        # 开始下载
        url_text = self.url_input.text().strip()
        if not url_text:
            self._show_browser_safe_message(QMessageBox.Warning, t("错误"), t("请输入视频URL"))
            return
            
        # 从输入文本中提取URL
        extracted_url = self.extract_url_from_text(url_text)
        
        # 如果提取的URL与原始文本不同，显示提示信息并更新输入框
        if extracted_url != url_text:
            self.progress_text.append(t_format("已自动从输入内容中提取URL: {}", extracted_url))
            self.url_input.setText(extracted_url)
            
        # 使用提取后的URL
        url = extracted_url
        
        output_path = self.output_path.text()
        if not os.path.exists(output_path):
            try:
                os.makedirs(output_path)
            except Exception as e:
                self._show_browser_safe_message(QMessageBox.Critical, t("错误"), t_format("无法创建输出目录: {}", str(e)))
                return
        
        is_media_capture_download = bool(self._pending_media_capture_download)
        format_option = None if is_media_capture_download else self.get_format_option()
        preferred_title = choose_better_download_title(
            self._pending_download_title,
            self.video_info.get('title', '') if self.video_info and self._video_info_url == url else "",
        )
        try:
            additional_options = self.get_additional_options()
        except ValueError as e:
            self._show_browser_safe_message(QMessageBox.Warning, t("时间片段设置错误"), str(e))
            return
        except Exception as e:
            self._show_browser_safe_message(QMessageBox.Critical, t("参数构建失败"), t_format("下载参数构建失败: {}", str(e)))
            return
        
        # 【调试】打印获取到的additional_options
        print(t_format("[调试] start_download 获取到的additional_options: {}", additional_options))
        
        # ── X平台多视频推文检测与选择 ──────────────────────────
        # 仅在非媒体捕获模式 且 用户未手动启用播放列表 时触发
        x_playlist_items_override = None  # None=不干预, ""=全部下载, "1,3"=下载指定项
        if (not is_media_capture_download
                and _is_x_platform_url(url)
                and not self.playlist_check.isChecked()):
            self.progress_text.clear()
            self.progress_text.append(t("🔍 检测到X平台链接，正在探测推文中的视频数量..."))
            QApplication.processEvents()

            x_entries = self._probe_x_multi_video(url)
            if x_entries:
                # 弹出视频选择对话框
                dialog = XVideoSelectionDialog(x_entries, self)
                if dialog.exec_() != QDialog.Accepted:
                    # 用户取消了选择
                    self.progress_text.append(t("已取消下载"))
                    return
                x_playlist_items_override = dialog.get_playlist_items_str()
                self.progress_text.append(
                    t_format("✅ 已选择 {}/{} 个视频", len(dialog.selected_indices), len(x_entries))
                )
            else:
                self.progress_text.append(t("✅ 该推文只包含一个视频，直接下载"))

        # 如果用户选择了指定格式ID但没有先获取可用格式列表
        # 注意：如果格式 ID 是从媒体捕获窗口传入的，则跳过此检查
        format_id_from_capture = self.format_id_combo.currentText().strip() if self.format_combo.currentText() == t("指定格式ID") else ""
        skip_format_dialog = bool(format_id_from_capture) and format_id_from_capture not in ("", "选择格式ID")
        
        if (not is_media_capture_download) and self.format_combo.currentText() == t("指定格式ID") and not self.available_formats and not skip_format_dialog:
            handled = {"triggered": False}

            def _go_list_formats():
                handled["triggered"] = True
                self.list_available_formats()

            reply = self._show_browser_safe_question(
                t("确认"),
                t("您选择了指定格式ID但尚未获取可用格式列表。\n\n是否先获取可用格式列表？"),
                yes_callback=_go_list_formats,
            )
            if reply is None or handled["triggered"]:
                return
        
        # 清空进度显示
        self.progress_text.clear()
        self.progress_text.append(t_format("使用格式选项: {}", format_option if format_option else '自动兼容模式'))
        if is_media_capture_download:
            if self._pending_media_capture_format_id and self._pending_media_capture_format_id.lower() != "best":
                self.progress_text.append(t_format("ℹ️ 媒体捕获下载：已忽略主页面格式选择，优先使用捕获面板格式 {}", self._pending_media_capture_format_id))
            else:
                self.progress_text.append(t("ℹ️ 媒体捕获下载：已忽略主页面格式选择，使用直链兼容下载策略"))
        
        # 重置进度条并确保正确设置
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("0%")
        self.progress_bar.setAlignment(Qt.AlignCenter)  # 确保文本居中显示
        self.progress_bar.setTextVisible(True)  # 确保文本可见
        self.setWindowTitle(t("SourceCraft - 准备下载"))
        
        # 强制更新UI，确保进度条初始状态正确显示
        QApplication.processEvents()
        
        # 添加调试信息
        self.progress_text.append(t("正在初始化下载线程..."))
        
        # 新增：源地址绑定反馈
        if hasattr(self, 'source_address_check') and self.source_address_check.isChecked():
            source_ip = self.source_address_input.text().strip()
            if source_ip:
                self.progress_text.append(t_format("<b>✓ 绑定源地址:</b> {}", source_ip))
        
        # 检查是否启用了播放列表功能
        playlist_enabled = self.playlist_check.isChecked()
        
        # ── X平台多视频：注入播放列表参数 ──────────────────────
        if x_playlist_items_override is not None:
            playlist_enabled = True  # X多视频必须启用播放列表模式

            # 移除已有的 --no-playlist / --yes-playlist，以免冲突
            cleaned = []
            skip_next = False
            for opt in additional_options:
                if skip_next:
                    skip_next = False
                    continue
                if opt in ("--no-playlist", "--yes-playlist"):
                    continue
                if opt == "--playlist-items":
                    skip_next = True  # 同时跳过其参数值
                    continue
                cleaned.append(opt)
            additional_options = cleaned

            additional_options.append("--yes-playlist")
            if x_playlist_items_override:
                additional_options.extend(["--playlist-items", x_playlist_items_override])
                self.progress_text.append(t_format("🐦 X平台多视频：下载第 {} 个视频", x_playlist_items_override))
            else:
                self.progress_text.append(t("🐦 X平台多视频：下载全部视频"))

        # 创建并启动下载线程，传递playlist_enabled参数
        self.download_thread = DownloadThread(url, output_path, format_option, additional_options, 
                                             playlist_enabled=playlist_enabled, main_app=self,
                                             preferred_title=preferred_title,
                                             media_capture_download=is_media_capture_download,
                                             media_capture_format_id=self._pending_media_capture_format_id)
        self.download_thread.progress_signal.connect(self.update_progress)
        self.download_thread.progress_percent_signal.connect(self.update_progress_bar)  # 连接进度百分比信号
        self.download_thread.finished_signal.connect(self.download_finished)
        self.download_thread.failed_videos_signal.connect(self.show_failed_videos)  # 连接失败视频列表信号
        
        # 添加调试信息
        self.progress_text.append(t_format("信号连接完成，播放列表模式: {}，开始下载...", '已启用' if playlist_enabled else '未启用'))
        self.download_thread.start()
        
        # 更新UI状态
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
    
    
    def preview_subtitles(self):
        """预览可用字幕功能"""
        url_text = self.url_input.text().strip()
        if not url_text:
            QMessageBox.warning(self, t("错误"), t("请输入视频URL"))
            return
            
        # 从输入文本中提取URL
        extracted_url = self.extract_url_from_text(url_text)
        
        # 如果提取的URL与原始文本不同，显示提示信息并更新输入框
        if extracted_url != url_text:
            self.progress_text.append(t_format("已自动从输入内容中提取URL: {}", extracted_url))
            self.url_input.setText(extracted_url)
            
        # 使用提取后的URL
        url = extracted_url
        
        # 禁用预览按钮，防止重复点击
        self.list_subs_button.setEnabled(False)
        
        # 清空进度显示
        self.progress_text.clear()
        self.progress_text.append(t("正在获取字幕信息..."))
        
        # 创建字幕预览线程
        self.subtitle_thread = SubtitlePreviewThread(url)
        self.subtitle_thread.progress_signal.connect(self.progress_text.append)
        self.subtitle_thread.subtitle_info_signal.connect(self.show_subtitle_preview)
        self.subtitle_thread.error_signal.connect(self.show_subtitle_error)
        self.subtitle_thread.finished.connect(lambda: self.list_subs_button.setEnabled(True))
        self.subtitle_thread.start()
        
    def show_subtitle_preview(self, subtitle_info):
        """显示字幕预览信息"""
        if not subtitle_info:
            self.progress_text.append(t("该视频没有可用的字幕"))
            return
            
        # 创建字幕预览对话框
        dialog = QDialog(self)
        dialog.setWindowTitle(t("可用字幕预览"))
        dialog.setMinimumSize(600, 400)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        layout = QVBoxLayout(dialog)
        
        # 添加说明文本
        info_label = QLabel(t("以下是该视频的可用字幕信息："))
        layout.addWidget(info_label)
        
        # 创建表格显示字幕信息
        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels([t("语言代码"), t("语言名称"), t("格式"), t("类型")])
        
        # 设置表格样式
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        # 填充字幕数据
        subtitles = subtitle_info.get('subtitles', {})
        automatic_captions = subtitle_info.get('automatic_captions', {})
        self._last_preview_subtitle_langs = sorted(
            set(list(subtitles.keys()) + list(automatic_captions.keys()))
        )
        
        row_count = 0
        for lang_code, formats in subtitles.items():
            for fmt in formats:
                row_count += 1
        for lang_code, formats in automatic_captions.items():
            for fmt in formats:
                row_count += 1
                
        table.setRowCount(row_count)
        
        row = 0
        # 添加手动字幕
        for lang_code, formats in subtitles.items():
            for fmt in formats:
                table.setItem(row, 0, QTableWidgetItem(lang_code))
                table.setItem(row, 1, QTableWidgetItem(fmt.get('name', t('未知'))))
                table.setItem(row, 2, QTableWidgetItem(fmt.get('ext', t('未知'))))
                table.setItem(row, 3, QTableWidgetItem(t("手动字幕")))
                row += 1
                
        # 添加自动字幕
        for lang_code, formats in automatic_captions.items():
            for fmt in formats:
                table.setItem(row, 0, QTableWidgetItem(lang_code))
                table.setItem(row, 1, QTableWidgetItem(fmt.get('name', t('未知'))))
                table.setItem(row, 2, QTableWidgetItem(fmt.get('ext', t('未知'))))
                table.setItem(row, 3, QTableWidgetItem(t("自动字幕")))
                row += 1
        
        layout.addWidget(table)
        
        # 添加统计信息
        stats_label = QLabel(t_format("共找到 {} 种手动字幕语言，{} 种自动字幕语言", len(subtitles), len(automatic_captions)))
        layout.addWidget(stats_label)
        
        # 添加帮助/关闭按钮
        button_layout = QHBoxLayout()
        help_button = QPushButton(t("帮助"))
        help_button.clicked.connect(lambda: QMessageBox.information(
            dialog,
            t("字幕预览说明"),
            t("手动字幕请选择“下载手动字幕”。\n") + t("自动字幕请仅勾选“下载自动生成字幕”。\n") + t("没有预览字幕代表源视频无字幕。")
        ))
        close_button = QPushButton(t("关闭"))
        close_button.clicked.connect(dialog.accept)
        button_layout.addStretch()
        button_layout.addWidget(help_button)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
        
        # 显示对话框
        dialog.exec_()
        
    def show_subtitle_error(self, error_message):
        """显示字幕预览错误"""
        self.progress_text.append(t_format("获取字幕信息失败: {}", error_message))
        QMessageBox.warning(self, t("错误"), t_format("获取字幕信息失败:\n{}", error_message))
    
    def cancel_download(self):
        """取消下载，添加异常处理确保UI稳定性"""
        try:
            self.progress_text.append(t(" 用户点击取消按钮，开始取消下载流程..."))
            
            if self.download_thread and self.download_thread.isRunning():
                self.progress_text.append(t_format(" 检测到下载线程正在运行，线程 ID: {}", self.download_thread.currentThreadId()))
                
                # 安全地断开所有信号连接
                self._safe_disconnect_download_signals()
                
                # 停止下载线程
                self.download_thread.stop()
                
                # 等待短时间确保线程停止
                QApplication.processEvents()
                
                # 更新UI状态
                self._update_ui_after_cancel()
                
                # 显示取消成功消息（使用QTimer延迟显示，避免UI冲突）
                QTimer.singleShot(100, self._show_cancel_success_message)
            else:
                self.progress_text.append(t(" 没有检测到正在运行的下载线程"))
                self._update_ui_after_cancel()
                
                # 即使没有运行的线程也显示取消成功消息
                QTimer.singleShot(100, self._show_cancel_success_message)
                
        except Exception as e:
            # 捕获所有异常，确保UI不会崩溃
            self.progress_text.append(t_format("取消下载时发生异常: {}", str(e)))
            import traceback
            self.progress_text.append(t_format("异常详情: {}", traceback.format_exc()))
            
            # 即使发生异常也要恢复UI状态
            self._update_ui_after_cancel()
            
            # 即使发生异常，也显示取消成功消息（因为下载确实已经停止）
            QTimer.singleShot(200, self._show_cancel_success_message)
    
    def _safe_disconnect_download_signals(self):
        """安全地断开下载线程的所有信号连接"""
        if not self.download_thread:
            return
            
        try:
            # 断开finished_signal连接，避免取消时弹出错误提示
            self.download_thread.finished_signal.disconnect()
        except (TypeError, RuntimeError):
            pass
            
        try:
            # 断开progress_signal连接
            self.download_thread.progress_signal.disconnect()
        except (TypeError, RuntimeError):
            pass
            
        try:
            # 断开progress_percent_signal连接
            self.download_thread.progress_percent_signal.disconnect()
        except (TypeError, RuntimeError):
            pass
            
        try:
            # 断开cancel_signal连接
            self.download_thread.cancel_signal.disconnect()
        except (TypeError, RuntimeError):
            pass
    
    def _update_ui_after_cancel(self):
        """取消下载后更新UI状态"""
        try:
            self.download_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            
            # 重置进度条
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat(t("已取消"))
            
            # 重置窗口标题
            self.setWindowTitle("SourceCraft")
            
            # 强制更新UI
            QApplication.processEvents()
            
        except Exception as e:
            # 即使UI更新失败也不应该崩溃
            print(t_format("更新UI状态时发生错误: {}", str(e)))
    
    def _show_cancel_success_message(self):
        """显示取消成功的消息，添加异常处理"""
        try:
            # 直接显示消息框，由于QTimer已经确保在主线程中调用
            QMessageBox.information(self, t("取消成功"), t("下载已成功取消！"))
        except Exception as e:
            # 如果消息框显示失败，在进度文本中显示替代消息
            print(t_format("显示取消成功消息框时发生错误: {}", str(e)))
            try:
                self.progress_text.append(t("✓ 下载已成功取消！"))
                # 滚动到底部显示最新消息
                self.progress_text.verticalScrollBar().setValue(self.progress_text.verticalScrollBar().maximum())
            except Exception as inner_e:
                print(t_format("在进度文本中显示取消消息时也发生错误: {}", str(inner_e)))
    
    def update_progress(self, text):
        # 更新进度显示
        self.progress_text.append(text)
        # 滚动到底部
        self.progress_text.verticalScrollBar().setValue(self.progress_text.verticalScrollBar().maximum())
        
    def update_progress_bar(self, percent):
        # 更新进度条
        
        # 【修复】特殊处理：-1 表示重置进度条（用于重试下载场景）
        if percent == -1:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0%")
            self.setWindowTitle(t("SourceCraft - 重试下载"))
            self.progress_text.append(t("📊 进度已重置（重试模式）"))
            QApplication.processEvents()
            return
        
        # 添加详细的调试信息，记录接收到的原始进度值
        self.progress_text.append(t_format("收到进度信号: {:.2f}%", percent))
        
        # 确保百分比值在有效范围内(0-100)
        if percent < 0:
            percent = 0
            self.progress_text.append(t("警告: 收到负进度值，已修正为0%"))
        elif percent > 100:
            percent = 100
            self.progress_text.append(t("警告: 收到超过100%的进度值，已修正为100%"))
        
        # 保留两位小数用于内部计算，但显示整数百分比
        float_percent = round(percent, 2)  # 保留两位小数，避免浮点误差
        
        # 获取当前进度条值，确保进度只增不减
        current_value = self.progress_bar.value()
        
        # 修改进度条更新逻辑，确保小进度值也能正确显示
        # 如果是初始阶段（当前进度为0），允许显示非常小的进度值
        # 如果新进度小于当前进度，且当前进度大于1%，则保持当前进度
        if float_percent < current_value and current_value > 1:
            self.progress_text.append(t_format("忽略较小的进度值: {:.2f}%，保持当前进度: {}%", float_percent, current_value))
            return
        
        int_percent = int(float_percent)   # 转换为整数用于显示
        
        # 更新进度条值
        self.progress_bar.setValue(int_percent)
        
        # 更新进度条文本显示，确保显示整数百分比
        self.progress_bar.setFormat(f"{int_percent}%")
        
        # 更新窗口标题显示下载进度
        self.setWindowTitle(t_format("SourceCraft - 下载进度: {}%", int_percent))
        
        # 记录进度更新到日志，帮助调试
        print(t_format("进度条更新: {:.2f}% (显示为{}%)", float_percent, int_percent))
        
        # 同时在进度文本框中显示进度更新，但减少更新频率
        # 在关键节点(0%, 25%, 50%, 75%, 100%)或每10%更新一次文本框
        if int_percent in [0, 25, 50, 75, 100] or int_percent % 10 == 0:
            self.progress_text.append(t_format("下载进度: {}%", int_percent))
            
        # 强制更新UI，确保进度条立即显示
        QApplication.processEvents()
        
        # 如果进度达到100%，确保进度条显示完成状态
        if int_percent == 100:
            self.progress_text.append(t("下载进度条更新完成！"))
            # 再次强制更新UI，确保最终状态正确显示
            QApplication.processEvents()

    def _extract_precise_error_evidence(self, message, matched_keyword=""):
        """从原始错误文本提取最有诊断价值的一行。"""
        text = str(message or "")
        if not text.strip():
            return ""

        lines = [line.strip() for line in text.splitlines() if line and line.strip()]
        if not lines:
            return ""

        priority_tokens = [
            "error:",
            "error ",
            "postprocessing",
            "ffmpeg",
            "subtitle",
            "requested format",
            "requested language",
            "not available",
            "too many requests",
            "http error",
            "certificate",
            "tls",
            "ssl",
            "timed out",
            "timeout",
            "permission denied",
            "no space left",
            "unable to extract",
            "unsupported url",
            "invalid url",
            "cookie",
            "session",
            "token",
        ]
        if matched_keyword:
            priority_tokens.insert(0, str(matched_keyword).lower())

        for line in lines:
            lower = line.lower()
            if any(token in lower for token in priority_tokens):
                return line[:260]

        # 默认返回最后一行（多数情况下最接近最终失败原因）
        return lines[-1][:260]

    def _diagnose_download_error(self, message):
        """更精确地诊断下载失败原因，返回结构化结果。"""
        text = str(message or "")
        lower = text.lower()

        profiles = [
            {
                "title": "字幕语言不匹配",
                "summary": "当前选择的字幕语言没有可用条目，导致字幕阶段失败。",
                "keywords": [
                    "subtitles for the requested languages are not available",
                    "requested languages are not available",
                    "requested language",
                    "did not match any subtitles",
                ],
                "causes": [
                    "视频只有语言变体（如 en-US），但当前只匹配了基础语言或反之",
                    "仅有自动字幕，未勾选“下载自动生成字幕”",
                    "字幕格式要求过严，目标格式不存在",
                ],
                "actions": [
                    "先点“预览可用字幕”，按预览中的实际语言代码选择",
                    "勾选“下载自动生成字幕”后重试",
                    "将字幕格式改为“最佳格式”或 srt/ass/vtt/best",
                ],
            },
            {
                "title": "视频无可用字幕",
                "summary": "视频本身没有可下载的字幕或自动字幕。",
                "keywords": [
                    "no subtitles",
                    "there are no subtitles",
                    "video has no subtitles",
                    "no automatic captions",
                    "has no automatic captions",
                ],
                "causes": [
                    "上传者未提供手动字幕",
                    "平台未生成自动字幕",
                    "当前站点/解析器不提供字幕接口",
                ],
                "actions": [
                    "关闭字幕下载，仅下载视频",
                    "切换到“全部语言”再试一次",
                    "若只需文本，可尝试平台端字幕导出方式",
                ],
            },
            {
                "title": "FFmpeg缺失",
                "summary": "后处理依赖未就绪，无法进行字幕嵌入/转码/合并。",
                "keywords": [
                    "ffmpeg and ffprobe not found",
                    "ffmpeg not found",
                    "ffprobe not found",
                    "ffmpeg is not installed",
                ],
                "causes": [
                    "程序目录缺少 ffmpeg.exe / ffprobe.exe",
                    "可执行文件被杀软隔离或路径无效",
                ],
                "actions": [
                    "检查程序目录是否存在 ffmpeg.exe 与 ffprobe.exe",
                    "点击依赖更新功能重新下载依赖",
                    "先关闭字幕嵌入/后处理再下载验证",
                ],
            },
            {
                "title": "格式不可用",
                "summary": "请求的视频/音频格式当前视频不提供。",
                "keywords": [
                    "requested format is not available",
                    "format is not available",
                    "no suitable formats",
                    "no formats found",
                ],
                "causes": [
                    "所选分辨率或编码在该视频不存在",
                    "音频语言过滤过于严格",
                ],
                "actions": [
                    "先获取可用格式列表，再选存在的格式ID",
                    "切换为“最佳质量”验证",
                    "放宽音频语言与分辨率限制",
                ],
            },
            {
                "title": "需要登录或权限不足",
                "summary": "该视频需要账号登录或会员权限。",
                "keywords": [
                    "please sign in",
                    "login required",
                    "authentication required",
                    "members only",
                    "subscribers only",
                    "premium",
                ],
                "causes": [
                    "视频仅登录用户可见",
                    "视频为会员/付费内容",
                    "Cookie 已过期或账号权限不足",
                ],
                "actions": [
                    "使用内置浏览器重新登录并提取 Cookie",
                    "确认账号具有目标内容访问权限",
                    "更换有效 Cookie 后重试",
                ],
            },
            {
                "title": "地区限制",
                "summary": "视频在当前IP所属地区不可访问。",
                "keywords": [
                    "not available in your country",
                    "geo-restricted",
                    "geo blocked",
                    "blocked in your region",
                    "地区限制",
                ],
                "causes": [
                    "内容按地区版权分发",
                    "当前出口IP被限制",
                ],
                "actions": [
                    "切换代理/VPN到可访问地区",
                    "在高级选项中配置代理重试",
                ],
            },
            {
                "title": "请求频率受限",
                "summary": "触发了平台的访问频率限制（429/限流）。",
                "keywords": [
                    "too many requests",
                    "rate limit",
                    "quota exceeded",
                    "throttled",
                    "http error 429",
                ],
                "causes": [
                    "短时间请求过多",
                    "同IP访问频率过高",
                ],
                "actions": [
                    "等待数分钟后再试",
                    "增加下载延迟，减少并发",
                    "必要时更换网络出口",
                ],
            },
            {
                "title": "网络连接超时/中断",
                "summary": "下载过程中网络连接不稳定或被中断。",
                "keywords": [
                    "timed out",
                    "timeout",
                    "connection reset",
                    "connection refused",
                    "network is unreachable",
                    "name resolution",
                    "dns",
                ],
                "causes": [
                    "网络抖动或DNS解析失败",
                    "代理/防火墙拦截连接",
                ],
                "actions": [
                    "检查网络稳定性并重试",
                    "刷新DNS缓存后重试",
                    "切换网络或调整代理设置",
                ],
            },
            {
                "title": "TLS/证书连接失败",
                "summary": "HTTPS握手或证书验证失败。",
                "keywords": [
                    "ssl",
                    "tls",
                    "certificate",
                    "certificate verify failed",
                    "ssl handshake",
                    "curl: (35)",
                ],
                "causes": [
                    "系统时间异常导致证书校验失败",
                    "代理中间人或网络环境导致握手失败",
                    "站点TLS策略更新导致旧链路失败",
                ],
                "actions": [
                    "先校准系统时间",
                    "更换网络或代理线路",
                    "更新 yt-dlp 与依赖后重试",
                ],
            },
            {
                "title": "视频不可用/已删除",
                "summary": "链接指向的视频不存在、私有或已被移除。",
                "keywords": [
                    "video unavailable",
                    "private video",
                    "video has been removed",
                    "video deleted",
                    "http error 404",
                    "page not found",
                ],
                "causes": [
                    "视频被作者删除或设为私有",
                    "链接失效或输入错误",
                ],
                "actions": [
                    "在浏览器中直接打开URL确认可访问性",
                    "重新复制完整链接后重试",
                    "若私有内容需获得访问授权",
                ],
            },
            {
                "title": "磁盘空间不足",
                "summary": "写文件失败，目标磁盘可用空间不足。",
                "keywords": [
                    "no space left",
                    "disk full",
                    "not enough space",
                    "errno 28",
                ],
                "causes": [
                    "输出盘剩余空间不足",
                    "临时目录空间不足",
                ],
                "actions": [
                    "清理磁盘或切换输出目录",
                    "预留至少目标文件2倍空间后重试",
                ],
            },
            {
                "title": "文件权限错误",
                "summary": "输出目录或目标文件无写权限，或文件被占用。",
                "keywords": [
                    "permission denied",
                    "access denied",
                    "operation not permitted",
                    "read-only",
                    "file in use",
                    "locked",
                ],
                "causes": [
                    "目录权限不足",
                    "文件被其他程序占用",
                    "安全软件阻止写入",
                ],
                "actions": [
                    "切换到当前用户可写目录",
                    "关闭占用目标文件的程序",
                    "必要时以管理员权限运行",
                ],
            },
            {
                "title": "解析器失效或版本过旧",
                "summary": "站点页面结构变化，当前解析器无法稳定提取。",
                "keywords": [
                    "extractor error",
                    "unable to extract",
                    "site layout changed",
                    "youtube said",
                    "unsupported url",
                ],
                "causes": [
                    "yt-dlp版本滞后",
                    "目标站点近期改版",
                ],
                "actions": [
                    "更新 yt-dlp 到最新版本",
                    "稍后重试或先验证站点是否仍受支持",
                ],
            },
        ]

        for profile in profiles:
            for keyword in profile["keywords"]:
                if keyword in lower:
                    evidence = self._extract_precise_error_evidence(text, keyword)
                    return {
                        "title": profile["title"],
                        "summary": profile["summary"],
                        "causes": profile["causes"],
                        "actions": profile["actions"],
                        "evidence": evidence,
                        "matched_keyword": keyword,
                    }

        return None

    def _show_precise_error_dialog(self, diagnosis, raw_message):
        """展示结构化、可执行的错误提示。"""
        causes_text = "\n".join(
            f"{idx + 1}. {item}" for idx, item in enumerate(diagnosis.get("causes", []))
        )
        actions_text = "\n".join(
            f"{idx + 1}. {item}" for idx, item in enumerate(diagnosis.get("actions", []))
        )
        evidence = diagnosis.get("evidence") or "未提取到关键错误行"
        matched_keyword = diagnosis.get("matched_keyword") or "未命中关键字"
        raw_preview = str(raw_message or "").strip()[:800] or "无"

        detail = (
            t_format("{}\n\n最可能原因：\n{}\n\n建议操作：\n{}\n\n诊断依据：\n{}\n匹配规则：{}\n\n原始错误（截断）：\n{}", diagnosis.get('summary', '下载失败'), causes_text or '1. 暂无', actions_text or '1. 暂无', evidence, matched_keyword, raw_preview)

        )
        self._show_browser_safe_message(QMessageBox.Critical, diagnosis.get("title", t("下载失败")), detail)
    
    def download_finished(self, success, message):
        # 下载完成处理
        self.progress_text.append(message)
        self.download_button.setEnabled(True)
        # 【修复】按"下载播放列表"勾选状态恢复，未勾选时保持禁用（避免绕过 _on_playlist_toggled 联动）
        self.get_playlist_button.setEnabled(self.playlist_check.isChecked())
        self.cancel_button.setEnabled(False)
        is_media_capture_download = bool(
            hasattr(self, 'download_thread') and getattr(self.download_thread, 'media_capture_download', False)
        )
        
        # 如果下载成功，确保进度条显示100%
        if success:
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("100%")
            # 强制更新UI，确保进度条显示正确
            QApplication.processEvents()
            
            # 添加到下载历史记录
            history_debug_info = ""
            try:
                url = self.url_input.text().strip()
                output_path = self.output_path.text().strip()
                format_option = self.get_format_option()
                is_media_capture_download = bool(
                    hasattr(self, 'download_thread') and getattr(self.download_thread, 'media_capture_download', False)
                )
                media_capture_format_id = ""
                if hasattr(self, 'download_thread'):
                    media_capture_format_id = str(getattr(self.download_thread, 'media_capture_format_id', '') or '').strip()
                if is_media_capture_download:
                    if media_capture_format_id and media_capture_format_id.lower() != "best":
                        format_option = t_format("媒体捕捉器: {}", media_capture_format_id)
                    else:
                        format_option = "媒体捕捉器: 自动兼容模式"
                
                print(t_format("开始添加历史记录 - URL: {}, 输出路径: {}", url, output_path))
                
                # 尝试从下载线程获取视频信息
                title = ""  # 先设为空，稍后填充
                file_size = None
                format_info = format_option
                title_candidates = []
                
                # 【关键修复】优先从下载线程获取当前视频标题
                if hasattr(self, 'download_thread'):
                    if hasattr(self.download_thread, 'preferred_title') and self.download_thread.preferred_title:
                        title_candidates.append(self.download_thread.preferred_title)
                        print(t_format("从下载线程的preferred_title获取标题候选: {}", self.download_thread.preferred_title))

                    # 方法1：尝试获取current_video_title（下载过程中设置的标题）
                    if hasattr(self.download_thread, 'current_video_title') and self.download_thread.current_video_title:
                        title_candidates.append(self.download_thread.current_video_title)
                        print(t_format("从下载线程的current_video_title获取标题候选: {}", self.download_thread.current_video_title))
                    
                    # 方法2：尝试从downloaded_files集合中获取（用于播放列表下载）
                    if hasattr(self.download_thread, 'downloaded_files') and self.download_thread.downloaded_files:
                        # 获取第一个下载的文件名作为标题
                        for downloaded_title in self.download_thread.downloaded_files:
                            if downloaded_title:
                                title_candidates.append(downloaded_title)
                                print(t_format("从下载线程的downloaded_files获取标题候选: {}", downloaded_title))
                                break
                    
                    # 方法3：尝试从video_info获取（如果有的话）
                    if hasattr(self.download_thread, 'video_info') and self.download_thread.video_info:
                        video_info = self.download_thread.video_info
                        title_candidates.append(video_info.get('title', ''))
                        file_size = video_info.get('filesize') or video_info.get('filesize_approx')
                        format_info = video_info.get('format', format_option)
                        print(t_format("从下载线程的video_info获取标题候选: {}", video_info.get('title', '')))
                
                # 尝试找到实际下载的文件路径
                downloaded_file_path = None
                if output_path and os.path.exists(output_path):
                    print(t_format("搜索下载文件，路径: {}", output_path))
                    # 查找最近创建的文件（扩大时间范围到10分钟）
                    files = []
                    current_time = time.time()
                    for root, dirs, filenames in os.walk(output_path):
                        for filename in filenames:
                            file_path = os.path.join(root, filename)
                            try:
                                # 检查文件是否是最近10分钟内创建或修改的
                                ctime = os.path.getctime(file_path)
                                mtime = os.path.getmtime(file_path)
                                recent_time = max(ctime, mtime)
                                
                                if recent_time > current_time - 600:  # 10分钟
                                    file_size_bytes = os.path.getsize(file_path)
                                    files.append((file_path, recent_time, file_size_bytes))
                                    print(t_format("找到最近文件: {}, 时间: {}, 大小: {}", file_path, recent_time, file_size_bytes))
                            except:
                                continue
                    
                    if files:
                        # 智能选择主要文件：优先选择视频文件而非缩略图
                        video_files = []
                        thumbnail_files = []
                        other_files = []
                        
                        for file_path, recent_time, file_size_bytes in files:
                            filename = os.path.basename(file_path).lower()
                            file_ext = os.path.splitext(filename)[1].lower()
                            
                            # 判断是否为缩略图文件
                            if ('thumbnail' in filename or 'thumb' in filename or 
                                filename.endswith('.jpg') or filename.endswith('.jpeg') or 
                                filename.endswith('.png') or filename.endswith('.webp')):
                                thumbnail_files.append((file_path, recent_time, file_size_bytes))
                                print(t_format("识别为缩略图: {}", file_path))
                            # 判断是否为视频文件
                            elif file_ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.m4v', '.wmv', '.mpg', '.mpeg', '.3gp', '.ts', '.mts', '.m2ts']:
                                video_files.append((file_path, recent_time, file_size_bytes))
                                print(t_format("识别为视频文件: {}", file_path))
                            # 判断是否为音频文件
                            elif file_ext in ['.mp3', '.m4a', '.aac', '.flac', '.ogg', '.wav', '.opus']:
                                video_files.append((file_path, recent_time, file_size_bytes))  # 音频文件也算主要文件
                                print(t_format("识别为音频文件: {}", file_path))
                            else:
                                other_files.append((file_path, recent_time, file_size_bytes))
                                print(t_format("识别为其他文件: {}", file_path))
                        
                        # 优先选择视频/音频文件，如果没有则选择其他文件，最后才选择缩略图
                        if video_files:
                            # 按时间排序，选择最新的视频/音频文件
                            video_files.sort(key=lambda x: x[1], reverse=True)
                            downloaded_file_path = video_files[0][0]
                            file_size = video_files[0][2]
                            print(t_format("选择主要视频/音频文件: {}", downloaded_file_path))
                        elif other_files:
                            # 按时间排序，选择最新的其他文件
                            other_files.sort(key=lambda x: x[1], reverse=True)
                            downloaded_file_path = other_files[0][0]
                            file_size = other_files[0][2]
                            print(t_format("选择其他文件: {}", downloaded_file_path))
                        elif thumbnail_files:
                            # 只有缩略图时才选择缩略图
                            thumbnail_files.sort(key=lambda x: x[1], reverse=True)
                            downloaded_file_path = thumbnail_files[0][0]
                            file_size = thumbnail_files[0][2]
                            print(t_format("只找到缩略图，选择: {}", downloaded_file_path))
                        
                        print(t_format("最终选择文件: {}, 大小: {} 字节", downloaded_file_path, file_size))
                        
                        if downloaded_file_path:
                            filename = os.path.basename(downloaded_file_path)
                            title_candidates.append(filename)
                            print(t_format("从文件名提取标题候选: {}", filename))
                    else:
                        print("未找到最近下载的文件")
                else:
                    print(t_format("输出路径不存在: {}", output_path))
                
                title = choose_better_download_title(*title_candidates)

                # 【关键修复】确保标题不为空，如果所有方法都失败则使用默认值
                if not title:
                    title = "下载的视频"
                    print(t_format("使用默认标题: {}", title))

                if downloaded_file_path and title:
                    downloaded_file_path = self._maybe_fix_generated_download_filename(downloaded_file_path, title)
                    if downloaded_file_path:
                        filename = os.path.basename(downloaded_file_path)
                        title_candidates.append(filename)
                        title = choose_better_download_title(title, filename)
                
                # 添加到历史记录（即使没有找到文件路径也添加记录）
                if url:
                    if not downloaded_file_path:
                        # 如果没有找到具体文件，使用输出目录作为路径
                        downloaded_file_path = output_path if output_path else "未知路径"
                        print(t_format("使用默认路径: {}", downloaded_file_path))
                    
                    print(t_format("添加历史记录: URL={}, 标题={}, 路径={}", url, title, downloaded_file_path))
                    self.add_download_to_history(url, title, downloaded_file_path, file_size, format_info)
                    print("历史记录添加成功")
                else:
                    print("URL为空，无法添加历史记录")
                    
            except Exception as e:
                print(t_format("添加下载历史记录时出错: {}", e))
                import traceback
                traceback.print_exc()
                history_debug_info = t_format("⚠️ 历史记录异常: {}", e)
            
            # 在UI中显示历史记录诊断信息（打包后也可见）
            if history_debug_info:
                try:
                    self.progress_text.append(history_debug_info)
                except Exception:
                    pass
            else:
                try:
                    record_count = len(self.history_manager.history)
                    self.progress_text.append(t_format("📝 历史记录已保存 (共{}条, 路径: {})", record_count, self.history_manager.settings_file))
                except Exception:
                    pass
        
        if success:
            self.play_completion_notification_sound()
            # 【修复】播放列表任务：下载报告弹窗（FailedVideosDialog）已展示完整统计，
            # 跳过重复的"下载完成"小弹窗；单视频/媒体捕捉下载保留原提示
            is_playlist_job = bool(
                hasattr(self, 'download_thread') and getattr(self.download_thread, 'playlist_enabled', False)
            )
            if not is_playlist_job:
                dialog_title = "媒体捕捉下载完成" if is_media_capture_download else "下载完成"
                self._show_browser_safe_message(QMessageBox.Information, dialog_title, message)
        else:
            # 将错误消息转换为小写用于匹配
            message_lower = message.lower()

            # 优先使用精准诊断器，命中后直接给出“原因+操作+依据”。
            diagnosis = self._diagnose_download_error(message)
            if diagnosis:
                self._show_precise_error_dialog(diagnosis, message)
                return
            
            # 检查是否是网络连接问题
            if any(keyword in message_lower for keyword in [
                'connection error', 'network error', 'unable to connect', 
                'timed out', 'connection refused', 'connection reset',
                'temporary failure', 'name resolution', 'dns error',
                'no route', 'host unreachable'
            ]) and 'ssl' not in message_lower and 'tls' not in message_lower and 'certificate' not in message_lower:
                self._show_browser_safe_message(QMessageBox.Critical, t("网络连接失败"), 
                    t("无法连接到视频服务器，请检查您的网络连接。\n\n") +
                    t("💡 可能的原因：\n") +
                    t("1. 🌐 网络连接不稳定或已断开\n") +
                    t("2. 🔒 防火墙阻止了连接\n") +
                    t("3. 🌍 DNS解析失败\n") +
                    t("4. ⏱️ 服务器响应超时\n\n") +
                    t("🔧 建议解决方案：\n") +
                    t("• 检查您的网络连接是否正常\n") +
                    t("• 尝试刷新DNS缓存（运行 ipconfig /flushdns）\n") +
                    t("• 切换到其他网络或使用代理\n") +
                    t("• 检查防火墙设置\n") +
                    t("• 稍后重试，可能是服务器暂时过载\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否需要登录/账号验证
            elif any(keyword in message_lower for keyword in [
                'please sign in', 'login required', 'sign in required',
                'you must be logged in', 'authentication required',
                'only available for registered', 'members only',
                'subscribers only', 'requires authentication'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("需要登录账号"), 
                    t("此视频需要登录账号才能下载。\n\n") +
                    t("💡 解决方案：\n") +
                    t("1. 🌐 点击工具栏的内置浏览器按钮\n") +
                    t("2. 🔐 在浏览器中登录您的账号\n") +
                    t("3. 🍪 点击Cookie提取按钮获取登录状态\n") +
                    t("4. 🔄 重新开始下载\n\n") +
                    t("⚠️ 注意：如果是会员专享内容，\n") +
                    t("需要使用有会员权限的账号登录。\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是年龄限制错误
            elif any(keyword in message_lower for keyword in [
                '年龄限制', 'age-restricted', 'age restricted', 'age gate',
                'age verification', 'confirm your age', 'sign in to confirm your age',
                'inappropriate for some users', 'this video may be inappropriate',
                'content warning', 'mature content'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("年龄限制"), 
                    t("此视频设置了年龄限制，需要验证身份。\n\n") +
                    t("💡 解决方案：\n") +
                    t("1. 🌐 使用内置浏览器登录已验证年龄的账号\n") +
                    t("2. 🍪 提取Cookie后重新下载\n") +
                    t("3. 🔧 在「高级选项」中启用「限制绕过」功能\n\n") +
                    t("⚠️ 注意：您的账号需要已完成年龄验证\n") +
                    t("（通常需要满18岁）\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是地理位置限制
            elif any(keyword in message_lower for keyword in [
                'not available in your country', 'geo-blocked', 'geo-restricted',
                'geographic restriction', 'region blocked', 'location restricted',
                'country restriction', 'territory restriction', 'blocked in your region',
                'this video is not available', 'ip blocked', '地区限制'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("地区限制"), 
                    t("此视频在您所在的地区不可用。\n\n") +
                    t("💡 解决方案：\n") +
                    t("1. 🔄 更换VPN服务器线路\n") +
                    t("   - 尝试切换到美国、日本等服务器节点\n") +
                    t("2. 🌐 在「高级选项」中配置代理服务器\n") +
                    t("3. 🔧 启用「限制绕过」功能\n\n") +
                    t("💡 提示：不同视频可能在不同地区可用，\n") +
                    t("尝试切换到视频允许的地区。\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是会员/付费内容
            elif any(keyword in message_lower for keyword in [
                'premium', 'premium content', 'paid', 'purchase required',
                'membership required', 'subscription required', '会员专享',
                'vip', 'youtube premium', 'requires subscription'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("会员专享内容"), 
                    t("此视频为会员/付费专享内容。\n\n") +
                    t("💡 解决方案：\n") +
                    t("1. 💳 需要使用有会员权限的账号\n") +
                    t("2. 🌐 在内置浏览器中登录会员账号\n") +
                    t("3. 🍪 提取Cookie后重新下载\n\n") +
                    t("⚠️ 注意：请确保您的账号已购买相应的会员服务。\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是视频不存在/已删除/私有
            elif any(keyword in message_lower for keyword in [
                'video unavailable', 'private video', 'video removed', 'video deleted',
                'removed by the user', 'no longer available', 'video has been removed',
                'this video is unavailable', 'this video does not exist',
                'video not found', 'http error 404', 'page not found', '视频不可用'
            ]) and 'ffmpeg' not in message_lower and 'cookie' not in message_lower and 'playlist' not in message_lower:
                self._show_browser_safe_message(QMessageBox.Critical, t("视频不可用"), 
                    t("该视频不存在、已被删除或设为私有。\n\n") +
                    t("💡 可能的原因：\n") +
                    t("1. 📹 视频已被上传者删除\n") +
                    t("2. 🔒 视频被设置为私有\n") +
                    t("3. 🔗 视频链接已失效或不正确\n") +
                    t("4. ⚖️ 视频因违规被平台删除\n\n") +
                    t("🔧 建议：\n") +
                    t("• 确认视频链接是否正确\n") +
                    t("• 尝试在浏览器中直接访问链接\n") +
                    t("• 如果是私有视频，需要获得授权\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是服务器错误
            elif any(keyword in message_lower for keyword in [
                'server error', 'http error 500', 'http error 502', 'http error 503', 'http error 504',
                'service unavailable', 'internal server error', 'bad gateway', 'gateway timeout',
                'server is down', 'server temporarily unavailable'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("服务器错误"), 
                    t("视频平台服务器暂时出现问题。\n\n") +
                    t("💡 这通常是临时性问题：\n") +
                    t("1. ⏱️ 服务器正在维护或过载\n") +
                    t("2. 🌐 平台暂时不可用\n\n") +
                    t("🔧 建议：\n") +
                    t("• 稍等几分钟后重试\n") +
                    t("• 检查视频网站是否正常运行\n") +
                    t("• 在非高峰时段重试\n") +
                    t("• 尝试使用代理或VPN\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是请求频率限制
            elif any(keyword in message_lower for keyword in [
                'too many requests', 'rate limit', 'slow down', 'throttled',
                '429', 'quota exceeded', 'api limit', 'request limit'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("请求过于频繁"), 
                    t("触发了平台的访问频率限制。\n\n") +
                    t("💡 解决方案：\n") +
                    t("1. ⏱️ 等待几分钟后重试\n") +
                    t("2. ⚙️ 在「高级选项」中增加下载延迟\n") +
                    t("3. 📉 减少并发下载数量\n") +
                    t("4. 🔄 使用不同的IP地址或代理\n\n") +
                    t("⚠️ 避免短时间内大量下载\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是版权问题
            elif any(keyword in message_lower for keyword in [
                'copyright', 'dmca', 'content id', 'blocked due to',
                'contains content from', 'who has blocked it', 'policy violation'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("版权限制"), 
                    t("该视频因版权问题受到限制。\n\n") +
                    t("💡 可能的情况：\n") +
                    t("1. 📜 视频内容涉及版权保护\n") +
                    t("2. ⚖️ 收到版权投诉被限制\n") +
                    t("3. 🌍 特定地区的版权限制\n\n") +
                    t("🔧 建议：\n") +
                    t("• 尝试使用其他地区的代理\n") +
                    t("• 检查是否有其他来源可用\n") +
                    t("• 联系版权持有者获取授权\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是格式不可用的错误
            elif "requested format is not available" in message_lower:
                self._show_browser_safe_question(
                    t("格式不可用"),
                    t("请求的视频格式不可用。\n\n是否获取此视频的可用格式列表？"),
                    yes_callback=self.list_available_formats,
                )
            elif "format" in message_lower and "not available" in message_lower:
                self._show_browser_safe_question(
                    t("格式不可用"),
                    t("所选格式可能不可用。\n\n是否获取此视频的可用格式列表？"),
                    yes_callback=self.list_available_formats,
                )
            
            # 检查是否是FFmpeg处理问题
            elif any(keyword in message_lower for keyword in [
                'ffmpeg', 'postprocessor', 'conversion failed', 'merging',
                'muxing', 'could not write header', 'invalid data'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("视频处理失败"), 
                    t("FFmpeg处理视频时出错。\n\n") +
                    t("💡 解决方案：\n") +
                    t("1. 📁 确认ffmpeg.exe在程序目录下\n") +
                    t("2. 💾 检查磁盘空间是否充足\n") +
                    t("3. 🔄 尝试选择不同的输出格式\n") +
                    t("4. ⚙️ 关闭字幕嵌入或其他后处理功能\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是SSL/证书问题
            elif any(keyword in message_lower for keyword in [
                'ssl', 'certificate', 'tls', 'certificate verify failed', 
                'ssl handshake', 'https error'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("安全连接失败"), 
                    t("HTTPS连接安全证书验证失败。\n\n") +
                    t("💡 解决方案：\n") +
                    t("1. 🕐 检查系统时间是否正确\n") +
                    t("2. 🔄 更新系统根证书\n") +
                    t("3. 🌐 检查网络代理设置\n") +
                    t("4. 🔧 程序会自动禁用证书检查重试\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是字幕相关问题
            elif any(keyword in message_lower for keyword in [
                'subtitle', 'subtitles', 'caption', 'captions', 'srt', 'vtt',
                'no subtitles', 'subtitle download', 'embed subtitle',
                'write subtitle', 'convert subtitle'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("字幕处理失败"), 
                    t("字幕下载或处理时出错。\n\n") +
                    t("💡 可能的原因：\n") +
                    t("1. 📝 该视频没有可用的字幕\n") +
                    t("2. 🌐 字幕语言不可用\n") +
                    t("3. ⚙️ 字幕格式转换失败\n") +
                    t("4. 📁 字幕嵌入到视频失败\n\n") +
                    t("🔧 建议：\n") +
                    t("• 在「字幕选项」中选择其他语言\n") +
                    t("• 尝试下载自动生成的字幕\n") +
                    t("• 关闭字幕嵌入，单独下载字幕文件\n") +
                    t("• 确保ffmpeg正确安装\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是时间片段/裁剪问题
            elif any(keyword in message_lower for keyword in [
                'download section', 'time range', 'start time', 'end time',
                'duration', 'segment', 'clip', 'cut', 'trim', 'split',
                'invalid time', 'time format', 'chapter'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("时间片段处理失败"), 
                    t("时间片段下载或裁剪时出错。\n\n") +
                    t("💡 可能的原因：\n") +
                    t("1. ⏱️ 时间格式输入不正确\n") +
                    t("2. 📹 指定的时间超出视频长度\n") +
                    t("3. 🔧 FFmpeg裁剪处理失败\n") +
                    t("4. 📊 视频不支持分段下载\n\n") +
                    t("🔧 建议：\n") +
                    t("• 检查时间格式是否正确（如 00:01:30-00:05:00）\n") +
                    t("• 确保开始时间小于结束时间\n") +
                    t("• 确保时间不超过视频总长度\n") +
                    t("• 尝试先下载完整视频再使用转码功能裁剪\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是播放列表问题
            elif any(keyword in message_lower for keyword in [
                'playlist', 'playlist_index', 'entries', 'playlist error',
                'playlist unavailable', 'private playlist', 'empty playlist'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("播放列表处理失败"), 
                    t("播放列表下载时出错。\n\n") +
                    t("💡 可能的原因：\n") +
                    t("1. 📋 播放列表已被删除或设为私有\n") +
                    t("2. 🔒 需要登录才能访问该播放列表\n") +
                    t("3. 📊 播放列表为空或无可用视频\n") +
                    t("4. 🌐 播放列表链接无效\n\n") +
                    t("🔧 建议：\n") +
                    t("• 确认播放列表链接是否正确\n") +
                    t("• 在浏览器中检查播放列表是否可访问\n") +
                    t("• 使用内置浏览器登录账号后重试\n") +
                    t("• 尝试单独下载播放列表中的视频\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是直播视频问题
            elif any(keyword in message_lower for keyword in [
                'live stream', 'livestream', 'is live', 'live video',
                'live broadcast', 'stream ended', 'live event',
                'premiere', 'upcoming', 'scheduled'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("直播视频限制"), 
                    t("该视频为直播或正在进行中的视频。\n\n") +
                    t("💡 说明：\n") +
                    t("1. 📺 正在直播的视频无法直接下载\n") +
                    t("2. ⏰ 预告/首播视频需要等待发布\n") +
                    t("3. 🔴 直播结束后可能需要等待转录\n\n") +
                    t("🔧 建议：\n") +
                    t("• 等待直播结束并转为回放后再下载\n") +
                    t("• 对于首播视频，等待正式发布时间\n") +
                    t("• 使用直播录制功能（如果支持）\n") +
                    t("• 稍后重试，直播回放可能需要处理时间\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是磁盘空间问题
            elif any(keyword in message_lower for keyword in [
                'no space', 'disk full', 'storage', 'not enough space',
                'disk space', 'write error', 'cannot write', 'out of space',
                'quota exceeded', 'errno 28', 'no space left'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("磁盘空间不足"), 
                    t("磁盘空间不足，无法保存视频。\n\n") +
                    t("💡 解决方案：\n") +
                    t("1. 💾 清理磁盘空间，删除不需要的文件\n") +
                    t("2. 📁 更换输出目录到其他磁盘\n") +
                    t("3. 🗑️ 清理临时文件（%TEMP%）\n") +
                    t("4. 📊 选择较低分辨率减少文件大小\n\n") +
                    t("⚠️ 建议确保有足够的可用空间\n") +
                    t("（一般预留视频大小2倍的空间）\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是文件权限问题
            elif any(keyword in message_lower for keyword in [
                'permission denied', 'access denied', 'cannot create',
                'file in use', 'locked', 'read-only', 'write protected',
                'cannot open', 'cannot delete', 'operation not permitted'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("文件权限错误"), 
                    t("文件操作权限不足。\n\n") +
                    t("💡 可能的原因：\n") +
                    t("1. 🔒 输出目录没有写入权限\n") +
                    t("2. 📁 文件被其他程序占用\n") +
                    t("3. 🛡️ 杀毒软件阻止了文件写入\n") +
                    t("4. 💻 需要管理员权限\n\n") +
                    t("🔧 建议：\n") +
                    t("• 更换输出目录到用户文件夹\n") +
                    t("• 关闭可能占用文件的程序\n") +
                    t("• 以管理员身份运行程序\n") +
                    t("• 检查杀毒软件设置\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是yt-dlp版本问题
            elif any(keyword in message_lower for keyword in [
                'outdated', 'update required', 'version', 'unsupported',
                'extractor error', 'extract error', 'parser error',
                'youtube said', 'site layout changed'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("需要更新yt-dlp"), 
                    t("yt-dlp版本可能过旧或不支持该网站。\n\n") +
                    t("💡 说明：\n") +
                    t("1. 📦 网站更新后可能需要新版本yt-dlp\n") +
                    t("2. 🌐 该网站可能不被yt-dlp支持\n") +
                    t("3. 🔧 解析器需要更新\n\n") +
                    t("🔧 建议：\n") +
                    t("• 点击工具栏「更新yt-dlp」按钮\n") +
                    t("• 或运行命令: pip install -U yt-dlp\n") +
                    t("• 检查yt-dlp是否支持该网站\n") +
                    t("• 访问 yt-dlp GitHub 查看最新版本\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是Cookie问题
            elif any(keyword in message_lower for keyword in [
                'cookie', 'cookies', 'session expired', 'token expired',
                'invalid cookie', 'cookie file', 'netscape cookie'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("Cookie认证失败"), 
                    t("Cookie已过期或格式不正确。\n\n") +
                    t("💡 解决方案：\n") +
                    t("1. 🌐 打开内置浏览器重新登录\n") +
                    t("2. 🍪 点击Cookie提取按钮获取新Cookie\n") +
                    t("3. 📁 删除旧的Cookie文件重新导入\n") +
                    t("4. 🔄 清除浏览器缓存后重新登录\n\n") +
                    t("⚠️ Cookie通常在一段时间后会过期，\n") +
                    t("需要定期更新。\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是URL格式问题
            elif any(keyword in message_lower for keyword in [
                'invalid url', 'malformed url', 'url error', 'bad url',
                'unsupported url', 'no valid', 'cannot parse url',
                'url not recognized'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("URL格式错误"), 
                    t("视频链接格式不正确或不被支持。\n\n") +
                    t("💡 检查项目：\n") +
                    t("1. 🔗 确保URL完整（包含http://或https://）\n") +
                    t("2. 📋 检查是否复制了完整的链接\n") +
                    t("3. 🌐 确认该网站被yt-dlp支持\n") +
                    t("4. ❓ 避免链接中有多余的空格或字符\n\n") +
                    t("🔧 建议：\n") +
                    t("• 从浏览器地址栏复制完整URL\n") +
                    t("• 尝试使用视频分享链接\n") +
                    t("• 查看yt-dlp支持的网站列表\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是合并/混流问题
            elif any(keyword in message_lower for keyword in [
                'merge', 'mux', 'remux', 'container', 'codec',
                'incompatible', 'stream copy', 'audio sync'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("视频合并失败"), 
                    t("视频和音频合并时出错。\n\n") +
                    t("💡 可能的原因：\n") +
                    t("1. 🎬 视频和音频编码不兼容\n") +
                    t("2. 📦 输出容器格式不支持该编码\n") +
                    t("3. 🔧 FFmpeg版本过旧\n") +
                    t("4. 📁 源文件已损坏\n\n") +
                    t("🔧 建议：\n") +
                    t("• 尝试选择不同的输出格式（如mkv）\n") +
                    t("• 更新FFmpeg到最新版本\n") +
                    t("• 关闭「贪婪合并」选项\n") +
                    t("• 尝试仅下载视频或音频\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            # 检查是否是下载中断问题
            elif any(keyword in message_lower for keyword in [
                'interrupted', 'incomplete', 'partial', 'resume',
                'broken pipe', 'aborted', 'terminated', 'cancelled'
            ]):
                self._show_browser_safe_message(QMessageBox.Critical, t("下载被中断"), 
                    t("下载过程被中断。\n\n") +
                    t("💡 可能的原因：\n") +
                    t("1. 🌐 网络连接不稳定\n") +
                    t("2. 🔌 服务器断开了连接\n") +
                    t("3. ⏸️ 下载被手动取消\n") +
                    t("4. 💻 系统资源不足\n\n") +
                    t("🔧 建议：\n") +
                    t("• 检查网络连接稳定性\n") +
                    t("• 重新开始下载（支持断点续传）\n") +
                    t("• 尝试使用代理服务器\n") +
                    t("• 降低视频质量减少下载时间\n\n") +
                    t_format("详细错误信息：\n{}", message[:500]))
            
            else:
                # 显示通用错误弹窗（中文）
                self._show_browser_safe_message(QMessageBox.Critical, t("下载失败"), 
                    t("下载过程中出现错误。\n\n") +
                    t("💡 通用解决方案：\n") +
                    t("1. 检查网络连接是否正常\n") +
                    t("2. 确认视频链接是否有效\n") +
                    t("3. 尝试选择不同的视频格式\n") +
                    t("4. 查看进度窗口中的详细错误日志\n") +
                    t("5. 更新yt-dlp到最新版本\n\n") +
                    t_format("错误信息：\n{}", message))
    


    def get_transcode_options(self):
        # 获取转码选项
        options = []
        
        # 检查输入和输出文件格式
        input_file = self.input_file.text()
        output_file = self.output_file.text()
        input_ext = os.path.splitext(input_file)[1].lower() if input_file else ''
        output_ext = os.path.splitext(output_file)[1].lower() if output_file else ''
        
        # 检查是否启用GPU加速
        use_gpu = self.gpu_checkbox.isChecked()
        gpu_type = self.gpu_type_combo.currentText() if use_gpu else None
        
        # 定义格式兼容性映射
        format_compatibility = {
            '.mp4': {
                'video_codecs': ['h264', 'h265'],
                'audio_codecs': ['aac', 'mp3'],
                'default_video': 'h264',
                'default_audio': 'aac'
            },
            '.mkv': {
                'video_codecs': ['h264', 'h265', 'vp9', 'av1'],
                'audio_codecs': ['aac', 'mp3', 'opus', 'flac'],
                'default_video': 'h264',
                'default_audio': 'aac'
            },
            '.avi': {
                'video_codecs': ['h264'],
                'audio_codecs': ['aac', 'mp3'],
                'default_video': 'h264',
                'default_audio': 'aac'
            },
            '.mov': {
                'video_codecs': ['h264', 'h265'],
                'audio_codecs': ['aac'],
                'default_video': 'h264',
                'default_audio': 'aac'
            },
            '.webm': {
                'video_codecs': ['vp9', 'av1'],
                'audio_codecs': ['opus', 'aac'],
                'default_video': 'vp9',
                'default_audio': 'aac'
            },
            '.mp3': {
                'video_codecs': [],  # 纯音频格式
                'audio_codecs': ['mp3'],
                'default_video': None,
                'default_audio': 'mp3'
            },
            '.m4a': {
                'video_codecs': [],  # 纯音频格式
                'audio_codecs': ['aac'],
                'default_video': None,
                'default_audio': 'aac'
            },
            '.flac': {
                'video_codecs': [],  # 纯音频格式
                'audio_codecs': ['flac'],
                'default_video': None,
                'default_audio': 'flac'
            }
        }
        
        # 获取输出格式的兼容性信息
        output_format_info = format_compatibility.get(output_ext, {})
        
        # 视频编码器处理
        video_codec = self.video_codec.currentText()
        
        # 检查是否为纯音频格式
        is_audio_only = output_ext in ['.mp3', '.m4a', '.flac']
        
        if is_audio_only:
            # 纯音频格式，跳过视频编码
            options.extend(["-vn"])  # 禁用视频
            self.transcode_progress_text.append(t_format("输出格式 {} 为纯音频格式，将跳过视频编码", output_ext))
        elif video_codec == "复制原始编码":
            # 检查复制模式的兼容性
            if input_ext == '.webm' and output_ext != '.webm':
                # WebM转换为其他格式时，使用合适的编码器
                default_video = output_format_info.get('default_video', 'h264')
                options.extend(["-c:v", f"lib{default_video}"])
                options.extend(["-pix_fmt", "yuv420p"])
                options.extend(["-crf", str(self.video_quality_slider.value())])
                self.transcode_progress_text.append(t_format("WebM转换检测：自动使用 {} 编码器", default_video))
            elif output_ext == '.webm' and input_ext != '.webm':
                # 其他格式转换为WebM时，使用VP9编码器
                options.extend(["-c:v", "libvpx-vp9"])
                options.extend(["-pix_fmt", "yuv420p"])
                options.extend(["-b:v", "0", "-crf", str(self.video_quality_slider.value())])
                self.transcode_progress_text.append(t("WebM输出检测：自动使用 VP9 编码器"))
            else:
                options.extend(["-c:v", "copy"])
        else:
            # 检查编码器与输出格式的兼容性
            compatible_video_codecs = output_format_info.get('video_codecs', [])
            
            if video_codec not in compatible_video_codecs:
                # 编码器不兼容，使用默认编码器
                default_video = output_format_info.get('default_video', 'h264')
                self.transcode_progress_text.append(t_format("警告：{} 编码器与 {} 格式不兼容，自动切换为 {}", video_codec, output_ext, default_video))
                video_codec = default_video
            
            # 将UI显示的编码器名称转换为ffmpeg支持的编码器名称
            codec_map = {
                "h264": "libx264",
                "h265": "libx265",
                "vp9": "libvpx-vp9",
                "av1": "libaom-av1"
            }
            
            # 检查是否使用GPU加速
            if use_gpu and video_codec in ["h264", "h265"]:
                # 启用硬件加速时，仅记录目标编码类型，具体硬件编码器由线程内按自动检测结果选择
                ffmpeg_codec = codec_map.get(video_codec, video_codec)
                options.extend(["-c:v", ffmpeg_codec])
                options.extend(["-pix_fmt", "yuv420p"])
                display_gpu_type = gpu_type if gpu_type and gpu_type != "自动检测" else "自动检测GPU"
                self.transcode_progress_text.append(t_format("使用{}硬件加速编码 {}", display_gpu_type, video_codec))
            else:
                # 使用CPU编码
                ffmpeg_codec = codec_map.get(video_codec, video_codec)
                options.extend(["-c:v", ffmpeg_codec])
                
                # 设置CRF值（质量）
                if video_codec in ["h264", "h265"]:
                    options.extend(["-crf", str(self.video_quality_slider.value())])
                    # 添加像素格式参数，确保兼容性
                    options.extend(["-pix_fmt", "yuv420p"])
                
                # 为VP9和AV1设置特定的质量参数
                if video_codec == "vp9":
                    options.extend(["-b:v", "0", "-crf", str(self.video_quality_slider.value())])
                    # VP9需要特定的像素格式
                    options.extend(["-pix_fmt", "yuv420p"])
                elif video_codec == "av1":
                    options.extend(["-crf", str(self.video_quality_slider.value()), "-strict", "experimental"])
                    options.extend(["-pix_fmt", "yuv420p"])
        
        # 音频编码器处理
        audio_codec = self.audio_codec.currentText()
        
        # 检查音频编码器与输出格式的兼容性
        compatible_audio_codecs = output_format_info.get('audio_codecs', [])
        
        if audio_codec == "复制原始编码":
            # 检查复制模式的兼容性
            if input_ext == '.webm' and output_ext != '.webm':
                # WebM转换为其他格式时，使用合适的音频编码器
                default_audio = output_format_info.get('default_audio', 'aac')
                audio_codec_map = {
                    "aac": "aac",
                    "mp3": "libmp3lame",
                    "opus": "libopus",
                    "flac": "flac"
                }
                ffmpeg_audio_codec = audio_codec_map.get(default_audio, "aac")
                options.extend(["-c:a", ffmpeg_audio_codec])
                if default_audio in ["aac", "mp3"]:
                    options.extend(["-b:a", "192k"])
                elif default_audio == "opus":
                    options.extend(["-b:a", "128k"])
                self.transcode_progress_text.append(t_format("WebM音频转换检测：自动使用 {} 编码器", default_audio))
            elif output_ext == '.webm' and input_ext != '.webm':
                # 其他格式转换为WebM时，使用AAC编码器以确保兼容性
                options.extend(["-c:a", "aac", "-b:a", "192k"])
                self.transcode_progress_text.append(t("WebM输出检测：自动使用 aac 音频编码器"))
            else:
                options.extend(["-c:a", "copy"])
        else:
            # 检查音频编码器兼容性
            if audio_codec not in compatible_audio_codecs:
                # 音频编码器不兼容，使用默认编码器
                default_audio = output_format_info.get('default_audio', 'aac')
                self.transcode_progress_text.append(t_format("警告：{} 音频编码器与 {} 格式不兼容，自动切换为 {}", audio_codec, output_ext, default_audio))
                audio_codec = default_audio
            
            # 将UI显示的音频编码器名称转换为ffmpeg支持的编码器名称
            audio_codec_map = {
                "aac": "aac",
                "mp3": "libmp3lame",
                "opus": "libopus",
                "flac": "flac"
            }
            ffmpeg_audio_codec = audio_codec_map.get(audio_codec, audio_codec)
            options.extend(["-c:a", ffmpeg_audio_codec])
            
            # 为音频编码器设置适当的比特率
            if audio_codec in ["aac", "mp3"]:
                options.extend(["-b:a", "192k"])
            elif audio_codec == "opus":
                options.extend(["-b:a", "128k"])
        
        # 特殊处理MP3转码，使用更保守的参数避免内存缓冲区不足错误
        if output_ext == '.mp3':
            # 为MP3转码添加更保守的缓冲区设置
            options.extend(["-max_muxing_queue_size", "512"])  # 使用较小的队列大小
            options.extend(["-probesize", "2M", "-analyzeduration", "2M"])  # 降低分析缓冲区
            # 添加MP3特定的优化参数
            if ffmpeg_audio_codec == "libmp3lame":
                options.extend(["-q:a", "2"])  # 使用VBR质量模式而不是固定比特率
                # 移除可能导致内存问题的比特率设置，使用质量模式
                if "-b:a" in options:
                    # 找到并移除-b:a参数
                    ba_index = options.index("-b:a")
                    options.pop(ba_index)  # 移除-b:a
                    if ba_index < len(options):
                        options.pop(ba_index)  # 移除比特率值
            self.transcode_progress_text.append(t("MP3转码优化：使用保守缓冲区设置和VBR质量模式"))
        
        # 添加额外参数以确保转码成功
        # 对于webm格式的特殊处理
        if input_ext == '.webm' or output_ext == '.webm':
            # 添加更多的容错选项，但避免与MP3优化冲突
            if output_ext != '.mp3':
                options.extend(["-max_muxing_queue_size", "9999"])
            # 添加更多的线程支持
            if "-threads" not in ' '.join(options):
                options.extend(["-threads", str(max(1, os.cpu_count() - 1))])
        
        # 为所有音频转码添加基本的内存优化参数（除非已经设置）
        if is_audio_only and output_ext != '.mp3':
            # 对于非MP3的纯音频转码，使用适中的缓冲区设置
            if "-max_muxing_queue_size" not in ' '.join(options):
                options.extend(["-max_muxing_queue_size", "2048"])
            if "-probesize" not in ' '.join(options):
                options.extend(["-probesize", "10M", "-analyzeduration", "10M"])
        
        # 验证输出文件扩展名与选择的格式是否匹配
        selected_format = self.output_format.currentText()
        if output_ext != f'.{selected_format}':
            self.transcode_progress_text.append(t_format("警告：输出文件扩展名 {} 与选择的格式 {} 不匹配", output_ext, selected_format))
            # 自动修正输出文件名
            base_name = os.path.splitext(output_file)[0]
            corrected_output = f"{base_name}.{selected_format}"
            self.output_file.setText(corrected_output)
            self.transcode_progress_text.append(t_format("已自动修正输出文件名为: {}", corrected_output))
        
        # 添加覆盖选项
        options.append("-y")
        
        # 记录最终的转码配置
        self.transcode_progress_text.append(t_format("转码配置：输入格式 {} -> 输出格式 {}", input_ext, output_ext))
        if not is_audio_only:
            self.transcode_progress_text.append(t_format("视频编码器：{}", video_codec))
        self.transcode_progress_text.append(t_format("音频编码器：{}", audio_codec if audio_codec != '复制原始编码' else '复制原始编码'))
        
        return options
    
    def start_transcode(self):
        # 开始转码
        try:
            input_file = self.input_file.text()
            if not input_file or not os.path.exists(input_file):
                QMessageBox.warning(self, t("错误"), t("请选择有效的输入文件"))
                return
            
            # 先获取转码选项，这会自动修正输出文件名
            transcode_options = self.get_transcode_options()
            
            # 重新获取修正后的输出文件名
            output_file = self.output_file.text()
            if not output_file:
                QMessageBox.warning(self, t("错误"), t("请指定输出文件"))
                return
            
            # 确保输出目录存在
            output_dir = os.path.dirname(output_file)
            if output_dir and not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir)
                except Exception as e:
                    QMessageBox.critical(self, t("错误"), t_format("无法创建输出目录: {}", str(e)))
                    return
            
            # 确保路径使用正确的编码
            try:
                # 尝试规范化路径，避免编码问题
                input_file = os.path.normpath(os.path.abspath(input_file))
                output_file = os.path.normpath(os.path.abspath(output_file))
                
                # 检查文件是否存在
                if not os.path.isfile(input_file):
                    QMessageBox.warning(self, t("错误"), t_format("输入文件不存在或无法访问: {}", input_file))
                    return
                
                # 记录路径信息用于调试
                self.transcode_progress_text.append(t_format("输入文件: {}", input_file))
                self.transcode_progress_text.append(t_format("输出文件: {}", output_file))
            except Exception as e:
                self.transcode_progress_text.append(t_format("路径处理警告: {}", str(e)))
                QMessageBox.warning(self, t("警告"), t_format("路径处理可能存在问题: {}\n请确保文件路径不包含特殊字符", str(e)))
            
            # 清空进度显示
            self.transcode_progress_text.clear()
            self.transcode_progress_text.append(t("准备开始转码..."))
            self._transcode_cancel_requested = False
            
            self.transcode_progress_bar.setValue(0)
            self.transcode_progress_bar.setFormat("0%")
            self.transcode_progress_bar.setStyleSheet("")
            self.transcode_progress_bar.setVisible(True)
            
            # 获取转码速度预设和线程数
            preset = self.speed_preset.currentText()
            threads = self.threads_spinbox.value()
            
            # 获取GPU加速选项
            use_gpu = self.gpu_checkbox.isChecked()
            gpu_type = self.gpu_type_combo.currentText() if use_gpu else None
            
            # 如果启用GPU加速，在进度文本中显示
            if use_gpu:
                if gpu_type == "自动检测":
                    self.transcode_progress_text.append(t("使用硬件加速编码: 自动检测 GPU 类型"))
                else:
                    self.transcode_progress_text.append(t_format("使用{}硬件加速编码", gpu_type))
            
            # 创建并启动转码线程
            self.transcode_thread = TranscodeThread(input_file, output_file, transcode_options, preset, threads, use_gpu, gpu_type)
            self.transcode_thread.progress_signal.connect(self.update_transcode_progress)
            self.transcode_thread.progress_percent_signal.connect(self.update_transcode_progress_percent)  # 连接进度百分比信号
            self.transcode_thread.finished_signal.connect(self.transcode_finished)
            self.transcode_thread.start()
            
            # 更新UI状态
            self.transcode_button.setEnabled(False)
            self.cancel_transcode_button.setEnabled(True)
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            self.transcode_progress_text.append(t_format("启动转码时出错: {}", str(e)))
            self.transcode_progress_text.append(t_format("详细错误信息: {}", error_details))
            QMessageBox.critical(self, t("错误"), t_format("启动转码时出错: {}", str(e)))
            self.transcode_button.setEnabled(True)
            self.cancel_transcode_button.setEnabled(False)
    
    def cancel_transcode(self):
        # 取消转码
        if self.transcode_thread and self.transcode_thread.isRunning():
            self._transcode_cancel_requested = True
            # 断开finished_signal连接，避免取消时弹出错误提示
            try:
                self.transcode_thread.finished_signal.disconnect(self.transcode_finished)
            except TypeError:
                # 如果信号未连接，忽略错误
                pass
            
            # 断开进度百分比信号，避免取消后收到失败(-1)信号导致进度条变红
            try:
                self.transcode_thread.progress_percent_signal.disconnect(self.update_transcode_progress_percent)
            except TypeError:
                pass
            
            # 先断开之前可能存在的信号连接，避免重复连接
            try:
                self.transcode_thread.cancel_signal.disconnect()
            except:
                pass
            # 连接取消成功信号
            self.transcode_thread.cancel_signal.connect(self._on_transcode_canceled)
            self.transcode_thread.stop()
            self.transcode_progress_text.append(t("转码已取消"))
            self._reset_transcode_progress_bar()
            self.transcode_button.setEnabled(True)
            self.cancel_transcode_button.setEnabled(False)
    
    def _reset_transcode_progress_bar(self):
        """将转码进度条恢复为初始状态。"""
        if hasattr(self, 'transcode_progress_bar'):
            self.transcode_progress_bar.setStyleSheet("")
            self.transcode_progress_bar.setValue(0)
            self.transcode_progress_bar.setFormat("0%")
            self.transcode_progress_bar.setVisible(True)
    
    def _on_transcode_canceled(self):
        # 显示取消成功的提示消息
        self._reset_transcode_progress_bar()
        QMessageBox.information(self, t("取消成功"), t("转码已成功取消！"))
        # 断开信号连接，避免重复触发
        try:
            self.transcode_thread.cancel_signal.disconnect()
        except:
            pass
    
    def update_transcode_progress(self, text):
        # 更新转码进度显示
        self.transcode_progress_text.append(text)
        # 滚动到底部
        self.transcode_progress_text.verticalScrollBar().setValue(self.transcode_progress_text.verticalScrollBar().maximum())
        
        # 更新进度条 - 仅从文本中提取进度信息作为备用方法
        if hasattr(self, 'transcode_progress_bar'):
            # 检查是否包含进度百分比信息
            progress_match = re.search(r'进度: (\d+\.?\d*)%', text)
            if progress_match:
                try:
                    progress_value = float(progress_match.group(1))
                    # 只有当进度值大于当前进度时才更新
                    current_value = self.transcode_progress_bar.value()
                    if progress_value > current_value:
                        self.transcode_progress_bar.setValue(int(progress_value))
                        # 更新窗口标题显示转码进度
                        self.setWindowTitle(t_format("SourceCraft - 转码进度: {}%", int(progress_value)))
                except (ValueError, IndexError):
                    pass  # 忽略解析错误
    
    def update_transcode_progress_percent(self, percent):
        # 直接从信号更新进度条，这是主要的进度更新方法
        if hasattr(self, 'transcode_progress_bar'):
            # 取消转码后，保持进度条初始状态，忽略所有后续进度/错误信号
            if getattr(self, '_transcode_cancel_requested', False):
                self._reset_transcode_progress_bar()
                return
            # 添加详细的调试信息，记录接收到的原始进度值
            self.transcode_progress_text.append(t_format("收到转码进度信号: {:.2f}%", percent))
            
            # 检查是否为错误状态（负值）
            if percent < 0:
                # 负值表示转码错误
                self.transcode_progress_text.append(t("错误: 转码失败，显示错误状态"))
                # 设置进度条为错误状态（红色）
                self.transcode_progress_bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")
                # 保持当前进度值，但更改显示文本为错误状态
                self.transcode_progress_bar.setFormat(t("转码失败"))
                return
            elif percent > 100:
                percent = 100
                self.transcode_progress_text.append(t("警告: 收到超过100%的进度值，已修正为100%"))
            
            # 保留两位小数用于内部计算，但显示整数百分比
            float_percent = round(percent, 2)  # 保留两位小数，避免浮点误差
            
            # 获取当前进度条值
            current_value = self.transcode_progress_bar.value()
            
            # 移除对90%附近进度的特殊处理，直接使用实际进度值
            # 确保进度条能够平滑显示所有进度值，包括0%到100%之间的所有值
            
            # 只有当新进度明显小于当前进度时才忽略，避免进度条回退
            # 但允许小幅度回退，确保初始进度能够正常显示
            if float_percent < current_value and (current_value - float_percent) > 1:
                self.transcode_progress_text.append(t_format("忽略回退的进度值: {:.2f}%，保持当前进度: {}%", float_percent, current_value))
                return
            
            int_percent = int(float_percent)   # 转换为整数用于显示
            
            # 重置进度条样式（如果之前设置了错误状态）
            self.transcode_progress_bar.setStyleSheet("")
            
            # 更新进度条值
            self.transcode_progress_bar.setValue(int_percent)
            
            # 更新进度条文本显示，确保显示整数百分比
            self.transcode_progress_bar.setFormat(f"{int_percent}%")
            
            # 更新窗口标题显示转码进度
            self.setWindowTitle(t_format("SourceCraft - 转码进度: {}%", int_percent))
            
            # 记录进度更新到日志，帮助调试
            print(t_format("转码进度条更新: {:.2f}% (显示为{}%)", float_percent, int_percent))
            
            # 同时在进度文本框中显示进度更新，但减少更新频率
            # 在关键节点(0%, 25%, 50%, 75%, 90%, 95%, 100%)或每10%更新一次文本框
            if int_percent in [0, 25, 50, 75, 90, 95, 100] or int_percent % 10 == 0:
                self.transcode_progress_text.append(t_format("转码进度: {}%", int_percent))
                
            # 强制更新UI，确保进度条立即显示
            QApplication.processEvents()
            
            # 如果进度达到100%，确保进度条显示完成状态
            if int_percent == 100:
                self.transcode_progress_text.append(t("转码进度条更新完成！"))
                # 再次强制更新UI，确保最终状态正确显示
                QApplication.processEvents()
    
    def transcode_finished(self, success, message):
        # 转码完成处理
        self.transcode_progress_text.append(message)
        self.transcode_button.setEnabled(True)
        self.cancel_transcode_button.setEnabled(False)
        
        # 如果是用户取消流程，进度条保持初始状态，不走失败红色逻辑
        if getattr(self, '_transcode_cancel_requested', False):
            self._reset_transcode_progress_bar()
            return
        
        # 添加转码历史记录
        if hasattr(self, 'transcode_thread') and self.transcode_thread:
            input_file = self.transcode_thread.input_file
            output_file = self.transcode_thread.output_file
            transcode_options = self.transcode_thread.transcode_options
            
            # 添加转码记录到历史
            error_message = None if success else message
            self.history_manager.add_transcode_record(
                input_file=input_file,
                output_file=output_file,
                transcode_options=transcode_options,
                success=success,
                error_message=error_message
            )
            
            # 刷新历史记录表格
            self.load_history_table()
        
        # 更新进度条状态
        if hasattr(self, 'transcode_progress_bar'):
            if success:
                # 转码成功时设置进度条为100%
                self.transcode_progress_bar.setValue(100)
                self.transcode_progress_bar.setFormat("100%")
                # 确保进度条样式正常（清除可能的错误样式）
                self.transcode_progress_bar.setStyleSheet("")
            else:
                # 转码失败时设置进度条样式为红色并显示失败文本
                self.transcode_progress_bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")
                # 设置显示文本为转码失败
                self.transcode_progress_bar.setFormat(t("转码失败"))
                # 不更新进度值，保持当前进度
        
        if success:
            QMessageBox.information(self, t("转码成功"), message)
        else:
            QMessageBox.critical(self, t("转码失败"), message)
    
    def load_history_table(self):
        """加载历史记录到表格"""
        sorting_enabled = self.history_table.isSortingEnabled()
        self.history_table.setSortingEnabled(False)

        # 清理旧的按钮和事件连接
        for row in range(self.history_table.rowCount()):
            widget = self.history_table.cellWidget(row, 5)
            if widget:
                widget.deleteLater()
        
        history = self.history_manager.get_history()
        self.history_table.setRowCount(len(history))
        
        for row, record in enumerate(history):
            record_type = record.get('type', 'download')
            
            # 类型列
            if record_type == 'transcode':
                type_text = "转码"
                if not record.get('success', True):
                    type_text += "(失败)"
            elif record_type == 'image_process':
                type_text = "图片处理"
                success_count = record.get('success_count', 0)
                fail_count = record.get('fail_count', 0)
                if fail_count > 0:
                    type_text += t_format("({}成功/{}失败)", success_count, fail_count)
            elif record_type == 'quality_repair':
                type_text = "画质修复"
                success_count = record.get('success_count', 0)
                fail_count = record.get('fail_count', 0)
                if fail_count > 0:
                    type_text += t_format("({}成功/{}失败)", success_count, fail_count)
            else:
                type_text = "下载"
            type_item = QTableWidgetItem(type_text)
            self.history_table.setItem(row, 0, type_item)
            
            # 标题
            title_item = QTableWidgetItem(record.get('title', t('未知标题')))
            if record_type == 'download':
                title_item.setToolTip(record.get('url', ''))
            elif record_type in ('image_process', 'quality_repair'):
                input_files = record.get('input_files', [])
                files_str = "\n".join(os.path.basename(f) for f in input_files[:5])
                if len(input_files) > 5:
                    files_str += t_format("\n...等共{}个文件", len(input_files))
                title_item.setToolTip(t_format("处理文件:\n{}\n输出目录: {}", files_str, record.get('output_dir', '')))
            else:
                # 转码记录
                title_item.setToolTip(t_format("输入: {}\n输出: {}", record.get('input_file', ''), record.get('output_file', '')))
            self.history_table.setItem(row, 1, title_item)
            
            # 时间
            if record_type == 'transcode':
                time_key = 'transcode_time'
            elif record_type in ('image_process', 'quality_repair'):
                time_key = 'process_time'
            else:
                time_key = 'download_time'
            
            record_time = record.get(time_key, '')
            if record_time:
                try:
                    dt = datetime.fromisoformat(record_time)
                    formatted_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    formatted_time = record_time
            else:
                formatted_time = '未知时间'
            time_item = QTableWidgetItem(formatted_time)
            self.history_table.setItem(row, 2, time_item)
            
            # 文件大小/数量
            if record_type in ('image_process', 'quality_repair'):
                # 显示处理数量
                success_count = record.get('success_count', 0)
                fail_count = record.get('fail_count', 0)
                size_str = t_format("{}成功, {}失败", success_count, fail_count)
            else:
                file_size = record.get('file_size', '')
                if file_size:
                    try:
                        size_bytes = int(file_size)
                        if size_bytes >= 1024 * 1024 * 1024:
                            size_str = f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
                        elif size_bytes >= 1024 * 1024:
                            size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
                        elif size_bytes >= 1024:
                            size_str = f"{size_bytes / 1024:.1f} KB"
                        else:
                            size_str = f"{size_bytes} B"
                    except:
                        size_str = file_size
                else:
                    size_str = '未知大小'
            size_item = QTableWidgetItem(size_str)
            self.history_table.setItem(row, 3, size_item)
            
            # 格式/选项列
            if record_type == 'transcode':
                # 显示转码选项
                transcode_options = record.get('transcode_options', [])
                if transcode_options:
                    options_str = ' '.join(str(opt) for opt in transcode_options)
                    if len(options_str) > 40:
                        display_str = options_str[:37] + '...'
                    else:
                        display_str = options_str
                else:
                    display_str = '默认设置'
                format_item = QTableWidgetItem(display_str)
                format_item.setToolTip(t_format("转码选项: {}", ' '.join((str(opt) for opt in transcode_options)) if transcode_options else '默认设置'))
                
                # 如果转码失败，显示错误信息
                if not record.get('success', True):
                    error_msg = record.get('error_message', t('未知错误'))
                    format_item.setToolTip(t_format("转码选项: {}\n错误信息: {}", ' '.join((str(opt) for opt in transcode_options)) if transcode_options else '默认设置', error_msg))
            elif record_type == 'image_process':
                # 显示图片处理选项
                process_options = record.get('process_options', {})
                if process_options:
                    fmt = process_options.get('format', 'JPEG')
                    quality = process_options.get('quality', 85)
                    display_str = t_format("格式:{} 质量:{}", fmt, quality)
                else:
                    display_str = '默认设置'
                format_item = QTableWidgetItem(display_str)
                format_item.setToolTip(t_format("输出目录: {}", record.get('output_dir', '')))
            elif record_type == 'quality_repair':
                process_options = record.get('process_options', {})
                if process_options:
                    preset = process_options.get('preset', 'general')
                    outscale = int(float(process_options.get('outscale', 4) or 4))
                    preset_names = {
                        'general': '通用修复',
                        'portrait': '人像修复',
                        'old_photo': '老照片修复',
                        'anime_image': '动漫图片修复',
                        'anime_video': '动漫视频修复',
                        'strong_photo': '低清图片强修复',
                    }
                    display_str = t_format("{} {}倍", preset_names.get(preset, preset), outscale)
                else:
                    display_str = '默认设置'
                format_item = QTableWidgetItem(display_str)
                format_item.setToolTip(t_format("输出目录: {}", record.get('output_dir', '')))
            else:
                # 显示下载格式信息
                format_info = str(record.get('format_info') or '未知格式')
                if len(format_info) > 40:
                    display_str = format_info[:37] + '...'
                else:
                    display_str = format_info
                format_item = QTableWidgetItem(display_str)
                format_item.setToolTip(format_info)
            
            self.history_table.setItem(row, 4, format_item)
            
            # 操作按钮
            button_widget = QWidget()
            button_layout = QHBoxLayout(button_widget)
            button_layout.setContentsMargins(5, 2, 5, 2)
            
            # 根据记录类型设置不同的路径
            if record_type in ('image_process', 'quality_repair'):
                # 图片处理：file_path是输出目录
                folder_path = record.get('output_dir', record.get('file_path', ''))
                # 获取输出文件（如果有）
                output_files = record.get('output_files', [])
                if output_files:
                    file_path = output_files[0]  # 使用第一个输出文件
                else:
                    file_path = folder_path  # 回退到目录
            else:
                folder_path = record.get('file_path', '')
                file_path = record.get('file_path', '')
            
            # 打开文件夹按钮
            open_folder_btn = QPushButton(t("打开文件夹"))
            open_folder_btn.setMaximumWidth(80)
            if record_type in ('image_process', 'quality_repair'):
                # 图片处理：直接打开输出目录
                open_folder_btn.clicked.connect(lambda checked, path=folder_path: self.open_image_folder(path))
            else:
                open_folder_btn.clicked.connect(lambda checked, path=folder_path: self.open_folder(path))
            button_layout.addWidget(open_folder_btn)
            
            # 定位文件按钮
            locate_file_btn = QPushButton(t("定位文件"))
            locate_file_btn.setMaximumWidth(80)
            # 统一使用locate_file方法，它有更完善的窗口重用功能
            locate_file_btn.clicked.connect(lambda checked, path=file_path: self.locate_file(path))
            button_layout.addWidget(locate_file_btn)
            
            # 删除记录按钮
            delete_btn = QPushButton(t("删除"))
            delete_btn.setMaximumWidth(50)
            delete_btn.clicked.connect(lambda checked, idx=row: self.delete_history_record(idx))
            button_layout.addWidget(delete_btn)
            
            self.history_table.setCellWidget(row, 5, button_widget)

        self.history_table.setSortingEnabled(sorting_enabled)
    
    def update_status(self, message, status_type="normal"):
        """更新状态显示（静态消息在入口统一翻译；动态消息由调用点 t_format 处理）"""
        self.status_label.setText(t(message))
        
        # 根据状态类型设置不同的样式 - 保持内边距一致，防止布局跳动
        common_style = "padding: 6px 12px; border-radius: 4px; font-weight: bold; border: 1px solid "
        
        if status_type == "info":
            self.status_label.setStyleSheet(
                f"QLabel {{ background-color: #d1ecf1; {common_style} #bee5eb; color: #0c5460; }}"
            )
        elif status_type == "success":
            self.status_label.setStyleSheet(
                f"QLabel {{ background-color: #d4edda; {common_style} #c3e6cb; color: #155724; }}"
            )
        elif status_type == "error":
            self.status_label.setStyleSheet(
                f"QLabel {{ background-color: #f8d7da; {common_style} #f5c6cb; color: #721c24; }}"
            )
        else:  # normal
            self.status_label.setStyleSheet(
                "QLabel { color: #2a82da; padding: 6px 12px; border: 1px solid transparent; font-weight: bold; }"
            )
    
    def refresh_history(self):
        """刷新历史记录"""
        # 获取刷新按钮的引用
        refresh_button = None
        for widget in self.findChildren(QPushButton):
            if "刷新历史" in widget.text():
                refresh_button = widget
                break
        
        # 显示刷新开始状态
        self.update_status(t("正在刷新历史记录..."), "info")
        
        # 临时禁用刷新按钮并显示加载状态
        if refresh_button:
            refresh_button.setEnabled(False)
            original_text = refresh_button.text()
            refresh_button.setText(t("⏳ 刷新中..."))
        
        # 使用QTimer延迟执行刷新操作，让UI有时间更新
        QTimer.singleShot(100, lambda: self._do_refresh_history(refresh_button, original_text))
    
    def _do_refresh_history(self, refresh_button, original_text):
        """执行实际的历史记录刷新操作"""
        try:
            # 重新加载历史记录
            self.history_manager.history = self.history_manager.load_history()
            self.load_history_table()
            self.history_search.clear()
            
            # 显示刷新完成状态
            record_count = len(self.history_manager.get_history())
            self.update_status(t_format("历史记录刷新完成，共 {} 条记录", record_count), "success")
            
        except Exception as e:
            # 如果刷新失败，显示错误信息
            self.update_status(t_format("刷新历史记录失败: {}", str(e)), "error")
        
        finally:
            # 恢复刷新按钮状态
            if refresh_button:
                refresh_button.setEnabled(True)
                refresh_button.setText(original_text)
            
            # 3秒后恢复状态标签为默认状态
            QTimer.singleShot(3000, lambda: self.update_status(t("就绪"), "normal"))
    
    def clear_history(self):
        """清空历史记录"""
        reply = QMessageBox.question(self, t("确认清空"), t("确定要清空所有下载历史记录吗？"),
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            # 获取清空按钮的引用
            clear_button = None
            for widget in self.findChildren(QPushButton):
                if "清空所有历史记录" in widget.text():
                    clear_button = widget
                    break
            
            # 显示清空开始状态
            self.update_status(t("正在清空历史记录..."), "info")
            
            # 临时禁用清空按钮并显示加载状态
            if clear_button:
                clear_button.setEnabled(False)
                original_text = clear_button.text()
                clear_button.setText(t("🗑️ 清空中..."))
            
            # 使用QTimer延迟执行清空操作，让UI有时间更新
            QTimer.singleShot(100, lambda: self._do_clear_history(clear_button, original_text))
    
    def _do_clear_history(self, clear_button, original_text):
        """执行实际的历史记录清空操作"""
        try:
            # 清空历史记录
            self.history_manager.clear_history()
            self.load_history_table()
            self.history_search.clear()
            
            # 显示清空完成状态
            self.update_status(t("所有历史记录已成功清空"), "success")
            
        except Exception as e:
            # 如果清空失败，显示错误信息
            self.update_status(t_format("清空历史记录失败: {}", str(e)), "error")
        
        finally:
            # 恢复清空按钮状态
            if clear_button:
                clear_button.setEnabled(True)
                clear_button.setText(original_text)
            
            # 3秒后恢复状态标签为默认状态
            QTimer.singleShot(3000, lambda: self.update_status(t("就绪"), "normal"))
    
    def filter_history(self, text):
        """过滤历史记录"""
        query = text.lower().strip()
        for row in range(self.history_table.rowCount()):
            title_item = self.history_table.item(row, 1)
            if title_item:
                title = title_item.text().lower()
                tooltip = title_item.toolTip().lower()  # URL
                should_show = query in title or query in tooltip
                self.history_table.setRowHidden(row, not should_show)
    
    def delete_history_record(self, row):
        """删除历史记录"""
        reply = QMessageBox.question(self, t("确认删除"), t("确定要删除这条下载记录吗？"),
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            # 获取实际的历史记录索引（考虑过滤和排序）
            history = self.history_manager.get_history()
            if 0 <= row < len(history):
                self.history_manager.remove_record(row)
                self.load_history_table()
    

    def locate_file(self, file_path):
        """
        高性能文件定位功能：快速响应、智能窗口重用、精确定位、健壮错误处理
        
        功能特性：
        - 快速响应：优化的执行流程，最小化延迟
        - 窗口重用：Windows和macOS上智能重用现有文件浏览器窗口
        - 精确定位：所有平台都高亮选中目标文件
        - 健壮处理：完善的错误处理和用户反馈机制
        - 防抖动：防止短时间内重复执行定位操作
        """
        # 全局防抖动机制：防止任何文件定位操作在短时间内重复执行
        import time
        current_time = time.time()
        
        # 初始化防抖动属性（如果不存在）
        if not hasattr(self, '_last_locate_time'):
            self._last_locate_time = 0
            self._last_locate_path = ""
            self._locate_in_progress = False
        
        # 检查是否有定位操作正在进行
        if hasattr(self, '_locate_in_progress') and self._locate_in_progress:
            return  # 静默忽略，不显示日志
        
        # 全局防抖动：任何文件定位操作都需要间隔至少0.5秒
        if current_time - self._last_locate_time < 0.5:
            return  # 静默忽略，不显示日志
        
        # 如果是相同文件且在1秒内重复调用，则忽略
        if (current_time - self._last_locate_time < 1.0 and 
            os.path.normpath(file_path) == self._last_locate_path):
            return  # 静默忽略，不显示日志
        
        # 设置定位操作进行中标志
        self._locate_in_progress = True
        
        # 更新防抖动记录
        self._last_locate_time = current_time
        self._last_locate_path = os.path.normpath(file_path)
        
        # 输入验证
        if not self._validate_file_path(file_path):
            # 如果验证失败，重置进行中标志
            self._locate_in_progress = False
            return
            
        # 添加开始定位的日志信息
        self.progress_text.append(t_format("开始定位文件: {}", os.path.basename(file_path)))
            
        # 规范化路径
        file_path = os.path.normpath(file_path)
        
        try:
            # 平台特定的文件定位实现
            if sys.platform == "win32":
                self._locate_file_windows(file_path)
            elif sys.platform == "darwin":
                self._locate_file_macos(file_path)
            else:
                self._locate_file_linux(file_path)
                
        except Exception as e:
            self._handle_locate_error(e, file_path)
        finally:
            # 无论成功还是失败，都要重置进行中标志
            self._locate_in_progress = False
    
    def _validate_file_path(self, file_path):
        """验证文件路径的有效性"""
        if not file_path or not file_path.strip():
            QMessageBox.warning(self, t("路径错误"), t("文件路径为空，请提供有效的文件路径。"))
            return False
            
        if not os.path.exists(file_path):
            # 提供更详细的错误信息
            if os.path.isabs(file_path):
                msg = t_format("文件不存在：\n{}\n\n可能原因：\n• 文件已被删除或移动\n• 路径拼写错误\n• 没有访问权限", file_path)
            else:
                msg = t_format("相对路径文件不存在：\n{}\n\n请使用绝对路径或确认当前工作目录。", file_path)
            QMessageBox.warning(self, t("文件不存在"), msg)
            return False
            
        if not os.path.isfile(file_path):
            QMessageBox.warning(self, t("路径错误"), t_format("指定路径不是文件：\n{}\n\n请确认路径指向一个文件而不是文件夹。", file_path))
            return False
            
        return True
    
    def _locate_file_windows(self, file_path):
        """
        在Windows上定位文件。
        优化的实现：智能窗口重用，避免重复打开新窗口
        """
        try:
            file_path_norm = os.path.normpath(file_path)
            if not self._validate_file_path(file_path_norm):
                return

            # 方法1：尝试使用COM接口（推荐方式，支持窗口重用）
            if self._try_com_locate_file(file_path_norm):
                return
                
            # 方法2：使用subprocess调用explorer（备用方案）
            if self._try_subprocess_locate_file(file_path_norm):
                return
                
            # 方法3：最后的fallback - 使用ShellExecuteW
            self._fallback_shell_execute(file_path_norm)

        except Exception as e:
            self.progress_text.append(t_format("错误: 定位文件 '{}' 时发生意外错误: {}", file_path, e))
            QMessageBox.critical(self, t("错误"), t_format("定位文件时发生未知错误：\n{}\n\n错误：\n{}", file_path, e))
    
    def _try_com_locate_file(self, file_path):
        """尝试使用COM接口定位文件（支持窗口重用）"""
        try:
            import win32com.client
            import pythoncom
            import win32gui
            import win32con
            
            # 初始化COM
            pythoncom.CoInitialize()
            
            try:
                shell = win32com.client.Dispatch("Shell.Application")
                
                # 获取文件夹路径
                folder_path = os.path.dirname(file_path)
                file_name = os.path.basename(file_path)
                
                # 方法1：尝试在已打开的窗口中定位文件
                windows = shell.Windows()
                target_window = None
                
                # 遍历所有打开的Explorer窗口
                for window in windows:
                    try:
                        if hasattr(window, 'LocationURL') and window.LocationURL:
                            # 将URL转换为本地路径
                            import urllib.parse
                            window_path = urllib.parse.unquote(window.LocationURL)
                            if window_path.startswith('file:///'):
                                window_path = window_path[8:].replace('/', '\\')
                                # 规范化路径进行比较
                                if os.path.normpath(window_path).lower() == os.path.normpath(folder_path).lower():
                                    target_window = window
                                    break
                    except:
                        continue
                
                # 如果找到了已打开的窗口，在该窗口中定位文件
                if target_window:
                    try:
                        # 激活窗口并置前
                        target_window.Visible = True
                        hwnd = target_window.HWND
                        win32gui.SetForegroundWindow(hwnd)
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                        
                        # 在已打开的窗口中定位文件
                        try:
                            # 方法1：尝试使用Document.SelectItem直接选中文件
                            folder = shell.NameSpace(folder_path)
                            if folder:
                                # 首先清除所有选择
                                try:
                                    target_window.Document.SelectItem(None, 0)  # 清除所有选择
                                except:
                                    pass
                                 
                                # 查找目标文件
                                target_item = folder.ParseName(file_name)
                                if target_item:
                                    # 选中目标文件
                                    target_window.Document.SelectItem(target_item, 1 | 8 | 4)  # SVSI_SELECT | SVSI_ENSUREVISIBLE | SVSI_DESELECTOTHERS
                                    self.progress_text.append(t_format("✓ 已在现有窗口中定位文件: {}", file_name))
                                    return True
                                else:
                                    # 如果找不到文件，可能是文件名大小写问题，尝试遍历
                                    for item in folder.Items():
                                        if item.Name.lower() == file_name.lower():
                                            target_window.Document.SelectItem(item, 1 | 8 | 4)
                                            self.progress_text.append(t_format("✓ 已在现有窗口中定位文件: {}", file_name))
                                            return True
                                             
                        except Exception as e:
                            # 如果直接选中失败，尝试关闭窗口再用explorer /select
                            try:
                                target_window.Quit()
                                import time
                                time.sleep(0.1)
                            except:
                                pass
                            

                    except Exception as e:
                        # 如果操作失败，回退到标准方法
                        pass
                else:
                    # 如果没有找到已打开的窗口，使用explorer.exe /select命令
                    shell.ShellExecute(
                        "explorer.exe",
                        f'/select,"{file_path}"',
                        "",
                        "open",
                        1
                    )
                    return True
                
            finally:
                pythoncom.CoUninitialize()
                
        except ImportError:
            # win32com不可用，返回False使用备用方案
            return False
        except Exception:
            return False
    
    def _try_subprocess_locate_file(self, file_path):
        """使用subprocess调用explorer（改进的命令行方式）"""
        try:
            # 使用Windows API确保explorer.exe路径正确
            explorer_path = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'explorer.exe')
            
            # 方法1：使用explorer.exe /select命令（最可靠）
            try:
                result = subprocess.run(
                    [explorer_path, '/select,', file_path],
                    capture_output=True,
                    timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                # explorer.exe总是返回0，即使失败，所以这个方法主要依赖explorer的内部逻辑
                return True
            except subprocess.TimeoutExpired:
                # 如果超时，explorer可能已经在处理请求
                return True
            except FileNotFoundError:
                # 如果explorer.exe路径不正确，尝试下一种方法
                pass
            except Exception:
                # 如果出现其他错误，尝试下一种方法
                pass
            
            # 方法2：使用cmd调用（兼容性更好）
            try:
                cmd_command = f'explorer.exe /select,"{file_path}"'
                result = subprocess.run(
                    ['cmd.exe', '/c', cmd_command],
                    capture_output=True,
                    timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                return True  # cmd总是返回0
            except Exception:
                # 如果cmd方法也失败，尝试PowerShell
                pass
                
            # 方法3：使用PowerShell（现代Windows系统）
            try:
                ps_command = f'Start-Process explorer.exe -ArgumentList "/select,`"{file_path}`""'
                result = subprocess.run(
                    ['powershell.exe', '-Command', ps_command],
                    capture_output=True,
                    timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                return True
            except Exception:
                # 所有subprocess方法都失败
                pass
                
            return False
            
        except Exception:
            return False
    
    def _fallback_shell_execute(self, file_path):
        """最后的fallback方案：使用ShellExecuteW"""
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,
                "open",
                "explorer.exe",
                f'/select,"{file_path}"',
                None,
                1  # SW_SHOWNORMAL
            )
            
            # ShellExecuteW 在成功时返回一个大于32的值
            if ret <= 32:
                # 如果ShellExecute失败，尝试直接打开文件夹
                folder_path = os.path.dirname(file_path)
                ctypes.windll.shell32.ShellExecuteW(
                    None,
                    "open",
                    folder_path,
                    None,
                    None,
                    1
                )
                
        except Exception as e:
            QMessageBox.warning(self, t("定位失败"), t_format("无法通过 Shell API 定位文件: {}", str(e)))
    
    def _locate_file_macos(self, file_path):
        """macOS平台文件定位：使用Finder的Reveal功能"""
        # macOS的open -R命令天然支持窗口重用和文件选中
        subprocess.run(['open', '-R', file_path])
    
    def _locate_file_linux(self, file_path):
        """Linux平台文件定位：多文件管理器支持，优先DBus"""
        file_uri = f"file://{file_path}"
        
        # 方法1：使用DBus与文件管理器通信（推荐，支持GNOME、KDE等）
        if self._try_dbus_show_file(file_uri):
            return
            
        # 方法2：尝试特定文件管理器的命令
        if self._try_file_manager_commands(file_path):
            return
            
        # 方法3：最后的fallback - 打开文件夹
        self._open_folder_fallback(file_path)
    
    def _try_dbus_show_file(self, file_uri):
        """尝试使用DBus显示文件"""
        try:
            subprocess.run([
                'dbus-send',
                '--session',
                '--dest=org.freedesktop.FileManager1',
                '--type=method_call',
                '/org/freedesktop/FileManager1',
                'org.freedesktop.FileManager1.ShowItems',
                f'array:string:{file_uri}',
                'string:""'
            ], check=True, capture_output=True, timeout=2)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
    
    def _try_file_manager_commands(self, file_path):
        """尝试特定文件管理器的选中命令"""
        folder_path = os.path.dirname(file_path)
        file_name = os.path.basename(file_path)
        
        # 常见Linux文件管理器的选中命令
        commands = [
            # Nautilus (GNOME)
            ['nautilus', '--select', file_path],
            # Dolphin (KDE)
            ['dolphin', '--select', file_path],
            # Thunar (XFCE)
            ['thunar', '--select', file_path],
            # PCManFM (LXDE)
            ['pcmanfm', '--select', file_path],
            # Nemo (Cinnamon)
            ['nemo', '--select', file_path]
        ]
        
        for cmd in commands:
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=2)
                return True
            except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
                
        return False
    
    def open_folder(self, file_path):
        """打开文件所在的文件夹（不选中文件）"""
        if not self._validate_file_path(file_path):
            return
            
        folder_path = os.path.dirname(os.path.normpath(file_path))
        
        try:
            if sys.platform == "win32":
                # Windows: 使用explorer打开文件夹
                subprocess.run(['explorer', folder_path], timeout=3)
            elif sys.platform == "darwin":
                # macOS: 使用Finder打开文件夹
                subprocess.run(['open', folder_path], timeout=3)
            else:
                # Linux: 使用默认文件管理器
                subprocess.run(['xdg-open', folder_path], timeout=3)
        except Exception as e:
            QMessageBox.critical(self, t("错误"), t_format("无法打开文件夹：\n{}\n\n错误：{}", folder_path, str(e)))
    
    def open_image_folder(self, folder_path):
        """直接打开指定的文件夹（用于图片处理记录）"""
        if not folder_path:
            QMessageBox.warning(self, t("提示"), t("输出目录路径为空"))
            return
            
        folder_path = os.path.normpath(folder_path)
        
        if not os.path.exists(folder_path):
            QMessageBox.warning(self, t("提示"), t_format("目录不存在：\n{}", folder_path))
            return
        
        try:
            if sys.platform == "win32":
                subprocess.run(['explorer', folder_path], timeout=3)
            elif sys.platform == "darwin":
                subprocess.run(['open', folder_path], timeout=3)
            else:
                subprocess.run(['xdg-open', folder_path], timeout=3)
        except Exception as e:
            QMessageBox.critical(self, t("错误"), t_format("无法打开文件夹：\n{}\n\n错误：{}", folder_path, str(e)))
    
    def locate_image_file(self, path, is_dir=False):
        """定位图片处理的输出文件或目录"""
        if not path:
            QMessageBox.warning(self, t("提示"), t("路径为空"))
            return
            
        path = os.path.normpath(path)
        
        if not os.path.exists(path):
            QMessageBox.warning(self, t("提示"), t_format("文件或目录不存在：\n{}", path))
            return
        
        try:
            if sys.platform == "win32":
                if is_dir or os.path.isdir(path):
                    # 如果是目录，直接打开
                    subprocess.run(['explorer', path], timeout=3)
                else:
                    # 如果是文件，选中它
                    subprocess.run(['explorer', '/select,', path], timeout=3)
            elif sys.platform == "darwin":
                if is_dir or os.path.isdir(path):
                    subprocess.run(['open', path], timeout=3)
                else:
                    subprocess.run(['open', '-R', path], timeout=3)
            else:
                # Linux: 打开包含该文件的目录
                folder = path if os.path.isdir(path) else os.path.dirname(path)
                subprocess.run(['xdg-open', folder], timeout=3)
        except Exception as e:
            QMessageBox.critical(self, t("错误"), t_format("无法定位文件：\n{}\n\n错误：{}", path, str(e)))
    
    def _open_folder_fallback(self, file_path):
        """最后的fallback：打开文件所在文件夹"""
        folder_path = os.path.dirname(file_path)
        try:
            subprocess.run(['xdg-open', folder_path], timeout=3)
        except Exception as e:
            raise Exception(t_format("无法打开文件夹 {}: {}", folder_path, e))
    
    def _handle_locate_error(self, error, file_path):
        """统一的错误处理"""
        error_msg = t_format("定位文件时发生错误：\n\n文件路径：{}\n错误详情：{}\n\n建议：\n• 检查文件是否存在\n• 确认有足够的系统权限\n• 重启应用程序后重试", file_path, str(error))
        QMessageBox.critical(self, t("文件定位失败"), error_msg)
    
    def add_download_to_history(self, url, title, file_path, file_size=None, format_info=None):
        """添加下载记录到历史"""
        self.history_manager.add_record(url, title, file_path, file_size=file_size, format_info=format_info)
        print(t_format("历史记录文件路径: {}", self.history_manager.settings_file))
        print(t_format("当前历史记录数: {}", len(self.history_manager.history)))
        # 如果历史记录选项卡当前可见，刷新显示
        if hasattr(self, 'history_table'):
            self.load_history_table()
    
    def dragEnterEvent(self, event):
        """拖拽进入事件"""
        try:
            if event.mimeData().hasUrls():
                # 检查是否包含视频文件
                for url in event.mimeData().urls():
                    if url.isLocalFile():
                        file_path = url.toLocalFile()
                        # 检查文件扩展名
                        ext = os.path.splitext(file_path)[1].lower()
                        if ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.m4v', '.wmv', '.mpg', '.mpeg', '.3gp', '.ts', '.mts', '.m2ts']:
                            event.acceptProposedAction()
                            return
            event.ignore()
        except Exception as e:
            print(t_format("拖拽进入事件处理错误: {}", e))
            event.ignore()
    
    def dragMoveEvent(self, event):
        """拖拽移动事件"""
        try:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
            else:
                event.ignore()
        except Exception as e:
            print(t_format("拖拽移动事件处理错误: {}", e))
            event.ignore()
    
    def dropEvent(self, event):
        """拖拽放下事件"""
        try:
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if url.isLocalFile():
                        file_path = url.toLocalFile()
                        # 检查文件扩展名
                        ext = os.path.splitext(file_path)[1].lower()
                        if ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.m4v', '.wmv', '.mpg', '.mpeg', '.3gp', '.ts', '.mts', '.m2ts']:
                            # 设置到转码输入文件
                            self.input_file.setText(file_path)
                            # 自动切换到转码选项卡
                            self.tabs.setCurrentIndex(1)  # 假设转码是第二个选项卡
                            # 自动设置输出文件名
                            self.update_output_filename(file_path)
                            # 显示成功消息
                            self.transcode_progress_text.append(t_format("已通过拖拽设置输入文件: {}", os.path.basename(file_path)))
                            event.acceptProposedAction()
                            return
            event.ignore()
        except Exception as e:
            print(t_format("拖拽放下事件处理错误: {}", e))
            # 显示错误消息给用户
            try:
                self.transcode_progress_text.append(t_format("拖拽文件时发生错误: {}", str(e)))
            except:
                pass
            event.ignore()
    
    def _show_license_dialog(self):
        """显示许可证激活对话框"""
        if self.license_manager:
            try:
                from license_dialogs import ActivationDialog
                dialog = ActivationDialog(self.license_manager, self)
                if dialog.exec_():
                    # 激活成功，更新状态显示
                    if hasattr(self, 'license_status_widget'):
                        self.license_status_widget.update_status()
            except Exception as e:
                print(t_format("[VideoDownloader] 显示许可证对话框失败: {}", e))
    
    def closeEvent(self, event):
        """下载器窗口关闭时，确保内置浏览器窗口也关闭"""
        # 关闭内置浏览器窗口（如果存在）
        if hasattr(self, 'browser_window') and self.browser_window:
            try:
                self.browser_window.close()
                self.browser_window = None
            except Exception as e:
                print(t_format("[VideoDownloader] 关闭浏览器窗口失败: {}", e))
        
        # 调用父类的closeEvent
        super().closeEvent(event)
    
    # Cookie生成器相关方法已移除 - 限制绕过功能已被删除
    
    def on_remove_chapters_toggled(self, checked):
        """移除章节复选框状态改变时的处理"""
        # 新的章节移除设计不需要这个方法
        pass

    def _get_download_sections(self):
        current = getattr(self, 'download_sections', [])
        if isinstance(current, str):
            current = [current] if current.strip() else []
        if not isinstance(current, list):
            return []
        return [str(x).strip() for x in current if str(x).strip()]

    def _get_download_sections_summary(self):
        sections = self._get_download_sections()
        return ", ".join(sections)

    def _time_section_mode_active(self):
        return (
            hasattr(self, 'time_section_check')
            and self.time_section_check.isChecked()
            and bool(self._get_download_sections())
        )

    def _split_time_range_tokens(self, content):
        return split_time_range_tokens(content)

    def _parse_time_token_to_seconds(self, token):
        return parse_time_token_to_seconds(token)
    
    def add_time_section_quick(self, start_h, start_m, start_s, end_h, end_m, end_s):
        """快捷添加时间片段"""
        try:
            # 处理特殊值
            if end_h == "inf" and end_m == "inf" and end_s == "inf":
                # 后1分钟: *-60-inf (负60秒到结束)
                time_str = "*-60-inf"
            else:
                # 构建时间字符串
                start_h_int = int(start_h) if start_h != "-1" else -1
                start_m_int = int(start_m)
                start_s_int = int(start_s)
                end_h_int = int(end_h)
                end_m_int = int(end_m)
                end_s_int = int(end_s)
                
                # 开始时间
                if start_h_int < 0:
                    # 负时间：转换为秒数
                    total_seconds = abs(start_m_int * 60 + start_s_int)
                    start_time = f"-{total_seconds}"
                elif start_h_int == 0 and start_m_int == 0 and start_s_int == 0:
                    start_time = "0"  # 从0秒开始
                elif start_h_int == 0:
                    start_time = f"{start_m_int}:{start_s_int:02d}"
                else:
                    start_time = f"{start_h_int}:{start_m_int:02d}:{start_s_int:02d}"
                
                # 结束时间
                if end_h_int == 0 and end_m_int == 0 and end_s_int == 0:
                    end_time = "inf"
                elif end_h_int == 0:
                    end_time = f"{end_m_int}:{end_s_int:02d}"
                else:
                    end_time = f"{end_h_int}:{end_m_int:02d}:{end_s_int:02d}"
                
                time_str = f"*{start_time}-{end_time}"
            
            self.add_download_section(time_str)
        except Exception as e:
            print(t_format("快捷添加时间片段错误: {}", e))
    
    def add_url_time_section(self):
        """添加URL时间片段"""
        self.add_download_section("*from-url")
    
    def add_custom_time_section(self):
        """添加自定义时间片段"""
        try:
            # 获取输入值
            start_h = self.start_hour.value()
            start_m = self.start_minute.value()
            start_s = self.start_second.value()
            end_h = self.end_hour.value()
            end_m = self.end_minute.value()
            end_s = self.end_second.value()
            
            # 验证输入：只检查结束时间是否有效
            # 允许从0:0:0开始，但结束时间必须大于0或为inf
            if end_h == 0 and end_m == 0 and end_s == 0:
                # 结束时间全0表示到视频结束(inf)，这是允许的
                pass
            else:
                # 如果设置了具体结束时间，检查是否大于开始时间
                start_total = start_h * 3600 + start_m * 60 + start_s
                end_total = end_h * 3600 + end_m * 60 + end_s
                if end_total <= start_total:
                    QMessageBox.warning(self, t("警告"), t("结束时间必须大于开始时间！"))
                    return
            
            # 构建时间字符串
            # 开始时间
            if start_h == 0 and start_m == 0 and start_s == 0:
                start_time = "0"  # 从0秒开始
            elif start_h == 0:
                start_time = f"{start_m}:{start_s:02d}"
            else:
                start_time = f"{start_h}:{start_m:02d}:{start_s:02d}"
            
            # 结束时间
            if end_h == 0 and end_m == 0 and end_s == 0:
                end_time = "inf"  # 全零表示到视频结束
            elif end_h == 0:
                end_time = f"{end_m}:{end_s:02d}"
            else:
                end_time = f"{end_h}:{end_m:02d}:{end_s:02d}"
            
            time_str = f"*{start_time}-{end_time}"
            
            before_count = len(self._get_download_sections())
            self.add_download_section(time_str)
            after_count = len(self._get_download_sections())

            if after_count > before_count:
                # 清空输入
                self.start_hour.setValue(0)
                self.start_minute.setValue(0)
                self.start_second.setValue(0)
                self.end_hour.setValue(0)
                self.end_minute.setValue(0)
                self.end_second.setValue(0)
            else:
                QMessageBox.information(self, t("提示"), t("该时间段已存在或格式不正确！"))
        except Exception as e:
            QMessageBox.warning(self, t("错误"), t_format("添加时间片段失败: {}", str(e)))
    
    def remove_time_section(self, item):
        """删除时间片段"""
        try:
            time_str = item.text()
            if time_str in self.download_sections:
                self.download_sections.remove(time_str)
            
            # 从列表中移除
            row = self.sections_list_widget.row(item)
            self.sections_list_widget.takeItem(row)
            self.update_sections_display()
        except Exception as e:
            print(t_format("删除时间片段错误: {}", e))
    
    def clear_time_sections(self):
        """清空所有时间片段"""
        self.download_sections.clear()
        self.sections_list_widget.clear()
        self.update_sections_display()
    
    def on_time_section_master_toggled(self, checked):
        """处理时间片段主控复选框的切换"""
        # 获取高级选项状态
        advanced_enabled = hasattr(self, 'advanced_group') and self.advanced_group.isChecked()
        
        # 控制所有子选项的启用状态（需要主控选项和高级选项同时启用）
        if hasattr(self, 'first_minute_btn'):
            self.first_minute_btn.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'last_minute_btn'):
            self.last_minute_btn.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'url_time_btn'):
            self.url_time_btn.setEnabled(advanced_enabled and checked)
        
        # 控制时间输入控件
        if hasattr(self, 'start_hour'):
            self.start_hour.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'start_minute'):
            self.start_minute.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'start_second'):
            self.start_second.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'end_hour'):
            self.end_hour.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'end_minute'):
            self.end_minute.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'end_second'):
            self.end_second.setEnabled(advanced_enabled and checked)
        
        # 控制添加和清空按钮
        if hasattr(self, 'add_section_btn'):
            self.add_section_btn.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'clear_sections_btn'):
            self.clear_sections_btn.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'custom_section_input'):
            self.custom_section_input.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'add_custom_section_btn'):
            self.add_custom_section_btn.setEnabled(advanced_enabled and checked)
        
        # 控制时间段列表
        if hasattr(self, 'sections_list_widget'):
            self.sections_list_widget.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'current_section_label'):
            self.current_section_label.setEnabled(advanced_enabled and checked)
        
        # 如果取消主控选项，清空所有时间段并重置输入控件到初始状态
        if not checked:
            # 清空时间段列表
            self.clear_time_sections()
            
            # 重置时间输入控件到初始值
            if hasattr(self, 'start_hour'):
                self.start_hour.setValue(0)
            if hasattr(self, 'start_minute'):
                self.start_minute.setValue(0)
            if hasattr(self, 'start_second'):
                self.start_second.setValue(0)
            if hasattr(self, 'end_hour'):
                self.end_hour.setValue(0)
            if hasattr(self, 'end_minute'):
                self.end_minute.setValue(0)
            if hasattr(self, 'end_second'):
                self.end_second.setValue(0)
            if hasattr(self, 'custom_section_input'):
                self.custom_section_input.clear()
        
        # 调试信息
        if checked:
            print("[INFO] 已启用时间片段下载功能；子选项现已可用")
        else:
            print("[INFO] 已禁用时间片段下载功能；相关子选项已禁用并重置到初始状态")
    
    def on_chapter_master_toggled(self, checked):
        """章节分割主控复选框状态改变时的处理"""
        # 获取高级选项状态
        advanced_enabled = hasattr(self, 'advanced_group') and self.advanced_group.isChecked()
        
        # 控制所有子选项的启用状态（基于主控复选框状态）
        if hasattr(self, 'remove_intro_btn'):
            self.remove_intro_btn.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'remove_outro_btn'):
            self.remove_outro_btn.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'remove_sponsor_btn'):
            self.remove_sponsor_btn.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'custom_chapter_input'):
            self.custom_chapter_input.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'add_chapter_btn'):
            self.add_chapter_btn.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'remove_chapters_list'):
            self.remove_chapters_list.setEnabled(advanced_enabled and checked)
        if hasattr(self, 'force_keyframes_check'):
            self.force_keyframes_check.setEnabled(advanced_enabled and checked)
        
        # 当主控复选框取消勾选时，重置所有子选项到初始状态
        if not checked:
            # 重置按钮状态
            if hasattr(self, 'remove_intro_btn'):
                self.remove_intro_btn.setChecked(False)
            if hasattr(self, 'remove_outro_btn'):
                self.remove_outro_btn.setChecked(False)
            if hasattr(self, 'remove_sponsor_btn'):
                self.remove_sponsor_btn.setChecked(False)
            if hasattr(self, 'force_keyframes_check'):
                self.force_keyframes_check.setChecked(False)
            
            # 清空输入框和列表
            if hasattr(self, 'custom_chapter_input'):
                self.custom_chapter_input.clear()
            if hasattr(self, 'chapters_to_remove'):
                self.chapters_to_remove.clear()
            if hasattr(self, 'remove_chapters_list'):
                self.remove_chapters_list.clear()
        
        # 调试信息
        if checked:
            print("[INFO] 已启用章节分割功能；子选项现已可用")
        else:
            print("[INFO] 已禁用章节分割功能；相关子选项已禁用并重置到初始状态")
    
    def update_chapter_removal(self):
        """更新章节移除列表"""
        try:
            # 清空列表
            self.chapters_to_remove.clear()
            self.remove_chapters_list.clear()
            
            # 根据按钮状态添加
            if self.remove_intro_btn.isChecked():
                patterns = ["intro", "opening"]
                for p in patterns:
                    if p not in self.chapters_to_remove:
                        self.chapters_to_remove.append(p)
                        self.remove_chapters_list.addItem(t_format("开场: {}", p))
            
            if self.remove_outro_btn.isChecked():
                patterns = ["outro", "ending"]
                for p in patterns:
                    if p not in self.chapters_to_remove:
                        self.chapters_to_remove.append(p)
                        self.remove_chapters_list.addItem(t_format("结尾: {}", p))
            
            if self.remove_sponsor_btn.isChecked():
                patterns = ["sponsor", "ad"]
                for p in patterns:
                    if p not in self.chapters_to_remove:
                        self.chapters_to_remove.append(p)
                        self.remove_chapters_list.addItem(t_format("广告: {}", p))
        except Exception as e:
            print(t_format("更新章节移除列表错误: {}", e))
    
    def add_custom_chapter(self):
        """添加自定义章节"""
        try:
            chapter_name = self.custom_chapter_input.text().strip()
            if not chapter_name:
                return
            
            if chapter_name not in self.chapters_to_remove:
                self.chapters_to_remove.append(chapter_name)
                self.remove_chapters_list.addItem(t_format("自定义: {}", chapter_name))
                self.custom_chapter_input.clear()
            else:
                QMessageBox.information(self, t("提示"), t("该章节已存在！"))
        except Exception as e:
            print(t_format("添加自定义章节错误: {}", e))
    
    def remove_chapter_item(self, item):
        """删除章节项"""
        try:
            text = item.text()
            # 提取章节名称（去掉前缀）
            if ": " in text:
                chapter_name = text.split(": ", 1)[1]
            else:
                chapter_name = text
            
            if chapter_name in self.chapters_to_remove:
                self.chapters_to_remove.remove(chapter_name)
            
            # 从列表中移除
            row = self.remove_chapters_list.row(item)
            self.remove_chapters_list.takeItem(row)
            
            # 更新按钮状态
            if chapter_name in ["intro", "opening"]:
                self.remove_intro_btn.setChecked(False)
            elif chapter_name in ["outro", "ending"]:
                self.remove_outro_btn.setChecked(False)
            elif chapter_name in ["sponsor", "ad"]:
                self.remove_sponsor_btn.setChecked(False)
        except Exception as e:
            print(t_format("删除章节项错误: {}", e))
    
    def diagnose_download_sections(self):
        from PyQt5.QtWidgets import QMessageBox
        import subprocess
        
        diag_messages = []
        diag_messages.append("=" * 50)
        diag_messages.append(t("时间段下载功能诊断"))
        diag_messages.append("=" * 50)
        diag_messages.append("")
        
        # 1. 检查 ffmpeg
        diag_messages.append(t("1. 检查 ffmpeg（必需）"))
        diag_messages.append("-" * 40)
        
        ffmpeg_ok = False
        ffprobe_ok = False
        
        try:
            result = subprocess.run(['ffmpeg', '-version'], 
                                  capture_output=True, 
                                  text=True, 
                                  timeout=5)
            if result.returncode == 0:
                version = result.stdout.split('\n')[0]
                diag_messages.append(f"✅ ffmpeg: {version[:60]}")
                ffmpeg_ok = True
            else:
                diag_messages.append(t("❌ ffmpeg 未找到或无法运行"))
        except Exception as e:
            diag_messages.append(t_format("❌ ffmpeg 未找到: {}", str(e)[:40]))
        
        try:
            result = subprocess.run(['ffprobe', '-version'], 
                                  capture_output=True, 
                                  text=True, 
                                  timeout=5)
            if result.returncode == 0:
                diag_messages.append(t("✅ ffprobe 已安装"))
                ffprobe_ok = True
            else:
                diag_messages.append(t("❌ ffprobe 未找到"))
        except:
            diag_messages.append(t("❌ ffprobe 未找到"))
        
        diag_messages.append("")
        
        # 2. 检查当前设置（支持多段）
        diag_messages.append(t("2. 当前时间段/章节设置"))
        diag_messages.append("-" * 40)
        
        current = getattr(self, 'download_sections', [])
        # 向后兼容字符串格式
        if isinstance(current, str):
            current = [current] if current.strip() else []
        
        if current:
            diag_messages.append(t_format("✅ 已设置 {} 个段落:", len(current)))
            time_ranges = []
            chapters = []
            for i, section in enumerate(current, 1):
                if section.startswith("*"):
                    time_ranges.append(section)
                    diag_messages.append(t_format("   [{}] 时间范围: {}", i, section))
                else:
                    chapters.append(section)
                    diag_messages.append(t_format("   [{}] 章节匹配: {}", i, section))
            
            diag_messages.append("")
            diag_messages.append(t_format("   统计: {} 个时间范围, {} 个章节匹配", len(time_ranges), len(chapters)))
            
            if time_ranges and not ffmpeg_ok:
                diag_messages.append(t("   ⚠️ 警告：时间范围需要 ffmpeg！"))
            if chapters:
                diag_messages.append(t("   ✅ 章节匹配不需要 ffmpeg"))
        else:
            diag_messages.append(t("当前未设置任何时间段或章节"))
        
        diag_messages.append("")
        
        # 3. 官方文档格式说明（优化版）
        diag_messages.append(t("3. 官方格式说明（基于 yt-dlp 文档）"))
        diag_messages.append("-" * 40)
        diag_messages.append(t("【时间范围】（需要 * 前缀和 ffmpeg）"))
        diag_messages.append(t("✅ *0-30          前30秒"))
        diag_messages.append(t("✅ *0-1:00        前1分钟"))
        diag_messages.append(t("✅ *1:00-2:30     1分钟到2分30秒"))
        diag_messages.append(t("✅ *-60-inf       最后1分钟"))
        diag_messages.append(t("✅ *10:00-inf     从10分钟到结束"))
        diag_messages.append(t("✅ *from-url      使用URL时间参数"))
        diag_messages.append("")
        diag_messages.append(t("【章节匹配】（正则表达式，不需要 ffmpeg）"))
        diag_messages.append(t("✅ intro          匹配intro章节"))
        diag_messages.append(t("✅ sponsor.*      匹配sponsor开头的章节"))
        diag_messages.append(t("✅ outro|ending   匹配outro或ending章节"))
        diag_messages.append("")
        diag_messages.append(t("【多段下载】"))
        diag_messages.append(t("可以添加多个时间段/章节，每个都会生成"))
        diag_messages.append(t("独立的 --download-sections 参数"))
        diag_messages.append("")
        diag_messages.append(t("【常见错误】"))
        diag_messages.append(t("❌ 0-30           缺少 * 前缀"))
        diag_messages.append(t("❌ *-30-0         结束应该用 inf"))
        diag_messages.append("")
        
        # 4. 总结
        diag_messages.append("=" * 50)
        diag_messages.append(t("诊断总结"))
        diag_messages.append("=" * 50)
        
        if ffmpeg_ok and ffprobe_ok:
            diag_messages.append(t("✅ ffmpeg 和 ffprobe 都已安装"))
            diag_messages.append(t("✅ 时间段下载功能应该可以正常使用"))
        else:
            diag_messages.append(t("❌ 缺少必要组件！"))
            if not ffmpeg_ok:
                diag_messages.append(t("   - 需要安装 ffmpeg"))
            if not ffprobe_ok:
                diag_messages.append(t("   - 需要安装 ffprobe"))
            diag_messages.append("")
            diag_messages.append(t("请从以下网址下载 ffmpeg:"))
            diag_messages.append("https://ffmpeg.org/download.html")
            diag_messages.append("")
            diag_messages.append(t("Windows用户可以使用："))
            diag_messages.append("https://github.com/BtbN/FFmpeg-Builds/releases")
        
        # 显示诊断结果
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(t("时间段下载功能诊断"))
        msg_box.setText("\n".join(diag_messages))
        
        if ffmpeg_ok and ffprobe_ok:
            msg_box.setIcon(QMessageBox.Information)
        else:
            msg_box.setIcon(QMessageBox.Warning)
        
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.exec_()
    
    def add_download_section(self, value):
        """添加下载时间段 - 基于官方 --download-sections 文档，支持多段"""
        try:
            if not value or not value.strip():
                return
            
            # 支持用逗号或分号分隔的多个时间段
            sections_to_add = []
            for sep in [',', ';']:
                if sep in value:
                    sections_to_add = [s.strip() for s in value.split(sep) if s.strip()]
                    break
            
            if not sections_to_add:
                sections_to_add = [value.strip()]
            
            invalid_messages = []
            added_count = 0
            duplicate_count = 0

            # 验证并添加每个时间段
            for section in sections_to_add:
                is_valid, warning_msg, section_type = self.validate_section_format(section)
                if not is_valid:
                    invalid_messages.append(t_format("{}: {}", section, warning_msg or '格式不正确'))
                    continue

                if section in self.download_sections:
                    duplicate_count += 1
                    continue

                self.download_sections.append(section)
                if hasattr(self, 'sections_list_widget'):
                    self.sections_list_widget.addItem(section)
                added_count += 1
            
            # 更新显示
            self.update_sections_display()
            
            # 清空输入框
            if hasattr(self, 'custom_section_input'):
                self.custom_section_input.clear()

            if invalid_messages:
                QMessageBox.warning(
                    self,
                    t("时间段格式错误"),
                    t("以下条目未添加：\n") + "\n".join(invalid_messages[:8]),
                )
            elif added_count == 0 and duplicate_count > 0:
                QMessageBox.information(self, t("提示"), t("所有时间段已存在，无需重复添加"))
            
        except Exception as e:
            print(t_format("添加下载时间段错误: {}", e))
    
    def validate_section_format(self, section):
        """验证时间段格式（增强版：支持负数与inf）"""
        return validate_download_section(section)
    
    def update_sections_display(self):
        """更新时间段显示"""
        if not hasattr(self, 'current_section_label'):
            return

        if not self.download_sections:
            self.current_section_label.setText(t("当前: 无"))
            self.current_section_label.setStyleSheet(
                "font-size: 11px; "
                "color: #7f8c8d; "
                "padding: 5px; "
                "background-color: #ecf0f1; "
                "border-radius: 3px;"
            )
        else:
            count = len(self.download_sections)
            display_text = t_format("当前 ({}段): ", count)
            
            # 分类显示
            time_ranges = [s for s in self.download_sections if s.startswith("*")]
            chapters = [s for s in self.download_sections if not s.startswith("*")]
            
            parts = []
            if time_ranges:
                parts.append(t_format("时间: {}", ', '.join(time_ranges[:3])))
                if len(time_ranges) > 3:
                    parts[-1] += t_format("...等{}段", len(time_ranges))
            if chapters:
                parts.append(t_format("章节: {}", ', '.join(chapters[:2])))
                if len(chapters) > 2:
                    parts[-1] += t_format("...等{}个", len(chapters))
            
            display_text += " | ".join(parts)
            
            self.current_section_label.setText(display_text)
            self.current_section_label.setStyleSheet(
                "font-size: 11px; "
                "color: #2c3e50; "
                "padding: 5px; "
                "background-color: #d4edda; "
                "border-radius: 3px; "
                "font-weight: bold;"
            )
    
    def clear_download_sections(self):
        """清除所有下载时间段"""
        self.download_sections = []
        if hasattr(self, 'sections_list_widget'):
            self.sections_list_widget.clear()
        self.update_sections_display()
    
    def update_remove_chapters_regex(self):
        """更新移除章节的正则表达式"""
        try:
            patterns = []
            
            # 收集所有选中的预设模式
            for pattern, check in self.chapter_remove_checks.items():
                if check.isChecked():
                    patterns.append(pattern)
            
            # 添加自定义规则（如果有）
            custom_rule = self.custom_remove_input.text().strip()
            if custom_rule:
                patterns.append(custom_rule)
            
            # 合并所有模式
            if patterns:
                self.remove_chapters_regex = "|".join(patterns)
                # 显示简化的当前规则
                display_text = self.remove_chapters_regex
                if len(display_text) > 50:
                    display_text = display_text[:47] + "..."
                self.current_remove_label.setText(t_format("当前: {}", display_text))
                self.current_remove_label.setStyleSheet(
                    "font-size: 11px; "
                    "color: #2c3e50; "
                    "padding: 5px; "
                    "background-color: #f8d7da; "
                    "border-radius: 3px; "
                    "font-weight: bold;"
                )
            else:
                self.remove_chapters_regex = ""
                self.current_remove_label.setText(t("当前: 无"))
                self.current_remove_label.setStyleSheet(
                    "font-size: 11px; "
                    "color: #7f8c8d; "
                    "padding: 5px; "
                    "background-color: #fadbd8; "
                    "border-radius: 3px;"
                )
        except Exception as e:
            print(t_format("更新移除章节规则错误: {}", e))
    
    def clear_all_remove_rules(self):
        """清除所有章节移除规则 - 功能已移除，保留空壳避免运行时错误"""
        pass

    def _update_media_panel_splitters(self):
        """让转码/图片处理页的上半区始终贴合内容高度，避免选项框下方出现大空隙。"""
        try:
            splitter_pairs = [
                (
                    getattr(self, 'transcode_splitter', None),
                    getattr(self, 'transcode_controls_scroll', None),
                    getattr(self, 'transcode_progress_group', None),
                ),
                (
                    getattr(self, 'image_splitter', None),
                    getattr(self, 'image_controls_scroll', None),
                    getattr(self, 'image_progress_group', None),
                ),
            ]

            for splitter, controls_scroll, progress_group in splitter_pairs:
                if not splitter or not controls_scroll or not progress_group:
                    continue
                if splitter.count() < 2:
                    continue

                controls_widget = controls_scroll.widget()
                if controls_widget is None:
                    continue

                handle_height = splitter.handleWidth() or 6
                available_height = max(0, splitter.size().height() - handle_height)
                content_height = controls_widget.sizeHint().height()
                viewport_margins = controls_scroll.contentsMargins().top() + controls_scroll.contentsMargins().bottom()
                desired_top = min(max(content_height + viewport_margins + 4, 0), available_height)

                progress_hint = progress_group.minimumSizeHint().height()
                min_bottom = max(progress_hint, 180)
                max_top = max(0, available_height - min_bottom)
                desired_top = min(desired_top, max_top) if max_top > 0 else desired_top
                desired_bottom = max(min_bottom, available_height - desired_top)

                splitter.setSizes([desired_top, desired_bottom])
        except Exception as e:
            print(t_format("更新媒体页面分割布局失败: {}", e))
    
    def resizeEvent(self, event):
        """窗口大小改变事件处理"""
        super().resizeEvent(event)
        
        # 动态调整高级选项组的最大高度，但不干扰折叠行为
        if hasattr(self, 'advanced_group'):
            # 获取当前窗口高度
            window_height = self.height()
            # 设置高级选项组的最大高度为窗口高度的50%，但不超过500px
            max_height = min(int(window_height * 0.5), 500)
            # 允许更小的最小高度，但保持一个合理的下限
            min_height = max(int(window_height * 0.15), 150)
            max_height = max(max_height, min_height)
            self.advanced_group.setMaximumHeight(max_height)
            
            # 不再强制设置高度，让QGroupBox自然处理折叠行为
        self._update_media_panel_splitters()
    
    def on_advanced_options_toggled(self, checked):
        """处理高级选项启用/禁用状态变化"""
        try:
            # ========================================
            # 第一步：如果禁用高级选项，先取消勾选所有复选框（在禁用控件之前）
            # 这样确保取消勾选操作能够正常生效
            # ========================================
            if not checked:
                # 取消勾选元数据相关选项
                if hasattr(self, 'write_description_check'):
                    self.write_description_check.setChecked(False)
                if hasattr(self, 'write_info_check'):
                    self.write_info_check.setChecked(False)
                if hasattr(self, 'write_comments_check'):
                    self.write_comments_check.setChecked(False)
                if hasattr(self, 'embed_metadata_check'):
                    self.embed_metadata_check.setChecked(False)
                if hasattr(self, 'embed_thumbnail_check'):
                    self.embed_thumbnail_check.setChecked(False)
                if hasattr(self, 'embed_chapters_check'):
                    self.embed_chapters_check.setChecked(False)
                if hasattr(self, 'embed_info_json_check'):
                    self.embed_info_json_check.setChecked(False)
                if hasattr(self, 'xattrs_check'):
                    self.xattrs_check.setChecked(False)
                
                # 取消勾选SponsorBlock相关选项
                if hasattr(self, 'sponsorblock_check'):
                    self.sponsorblock_check.setChecked(False)
                if hasattr(self, 'sponsorblock_sponsor_check'):
                    self.sponsorblock_sponsor_check.setChecked(False)
                if hasattr(self, 'sponsorblock_intro_check'):
                    self.sponsorblock_intro_check.setChecked(False)
                if hasattr(self, 'sponsorblock_outro_check'):
                    self.sponsorblock_outro_check.setChecked(False)
                if hasattr(self, 'sponsorblock_selfpromo_check'):
                    self.sponsorblock_selfpromo_check.setChecked(False)
                
                # 取消勾选章节支持相关选项
                if hasattr(self, 'split_chapters_check'):
                    self.split_chapters_check.setChecked(False)
                if hasattr(self, 'force_keyframes_check'):
                    self.force_keyframes_check.setChecked(False)
                if hasattr(self, 'remove_chapters_check'):
                    self.remove_chapters_check.setChecked(False)
                if hasattr(self, 'remove_chapters_input'):
                    self.remove_chapters_input.clear()
                
                # 取消勾选高级下载选项
                if hasattr(self, 'live_from_start_check'):
                    self.live_from_start_check.setChecked(False)
                if hasattr(self, 'wait_for_video_check'):
                    self.wait_for_video_check.setChecked(False)
                if hasattr(self, 'wait_for_video_use_range_check'):
                    self.wait_for_video_use_range_check.setChecked(False)
                
                # 取消勾选特殊场景下载主控选项和所有子选项
                if hasattr(self, 'special_scene_master_check'):
                    self.special_scene_master_check.setChecked(False)
                if hasattr(self, 'ignore_errors_check'):
                    self.ignore_errors_check.setChecked(False)
                if hasattr(self, 'skip_unavailable_fragments_check'):
                    self.skip_unavailable_fragments_check.setChecked(False)
                if hasattr(self, 'keep_fragments_check'):
                    self.keep_fragments_check.setChecked(False)
                
                # 取消勾选字幕相关选项
                if hasattr(self, 'subtitle_check'):
                    self.subtitle_check.setChecked(False)
                if hasattr(self, 'auto_subtitle_check'):
                    self.auto_subtitle_check.setChecked(False)
                if hasattr(self, 'embed_subtitle_check'):
                    self.embed_subtitle_check.setChecked(False)
                if hasattr(self, 'convert_subtitle_check'):
                    self.convert_subtitle_check.setChecked(False)
                
                # 取消勾选基本选项
                if hasattr(self, 'thumbnail_check'):
                    self.thumbnail_check.setChecked(False)
                if hasattr(self, 'limit_speed_check'):
                    self.limit_speed_check.setChecked(False)
                if hasattr(self, 'playlist_check'):
                    self.playlist_check.setChecked(False)
                
                # 取消勾选网络相关选项
                if hasattr(self, 'proxy_check'):
                    self.proxy_check.setChecked(False)
                if hasattr(self, 'source_address_check'):
                    self.source_address_check.setChecked(False)
                
                # 清空输入框和重置其他控件
                if hasattr(self, 'custom_section_input'):
                    self.custom_section_input.clear()
                    self.download_sections = []
                    if hasattr(self, 'sections_list_widget'):
                        self.sections_list_widget.clear()
                    self.update_sections_display()
                # 以下章节移除相关控件已移除
                # if hasattr(self, 'custom_remove_input'):
                #     self.custom_remove_input.clear()
                #     self.remove_chapters_regex = ""
                #     self.current_remove_label.setText("当前: 无")
                
                # 取消勾选时间片段下载主控选项并重置所有子选项
                if hasattr(self, 'time_section_check'):
                    self.time_section_check.setChecked(False)
                
                if hasattr(self, 'subtitle_delay_spin'):
                    self.subtitle_delay_spin.setValue(0)
                if hasattr(self, 'ip_version_combo'):
                    self.ip_version_combo.setCurrentText(t("仅IPv4"))
            
            # ========================================
            # 第二步：控制所有控件的启用/禁用状态
            # ========================================
            
            # 首先控制高级选项组内的选项卡内容启用状态
            if hasattr(self, 'advanced_group'):
                # 查找高级选项组内的所有选项卡组件
                advanced_layout = self.advanced_group.layout()
                if advanced_layout:
                    for i in range(advanced_layout.count()):
                        widget = advanced_layout.itemAt(i).widget()
                        if widget and hasattr(widget, 'setEnabled'):
                            widget.setEnabled(checked)
                            # 如果是选项卡组件，也要启用其内部的所有选项卡
                            if hasattr(widget, 'count'):  # QTabWidget
                                for j in range(widget.count()):
                                    tab_widget = widget.widget(j)
                                    if tab_widget:
                                        tab_widget.setEnabled(checked)
            
            # 控制所有在高级选项区域内的复选框
            
            # 控制元数据相关功能的启用状态
            if hasattr(self, 'write_description_check'):
                self.write_description_check.setEnabled(checked)
            if hasattr(self, 'write_info_check'):
                self.write_info_check.setEnabled(checked)
            if hasattr(self, 'write_comments_check'):
                self.write_comments_check.setEnabled(checked)
            if hasattr(self, 'embed_metadata_check'):
                self.embed_metadata_check.setEnabled(checked)
            if hasattr(self, 'embed_thumbnail_check'):
                self.embed_thumbnail_check.setEnabled(checked)
            # 新增：控制新增元数据选项的启用状态
            if hasattr(self, 'embed_chapters_check'):
                self.embed_chapters_check.setEnabled(checked)
            if hasattr(self, 'embed_info_json_check'):
                self.embed_info_json_check.setEnabled(checked)
            if hasattr(self, 'xattrs_check'):
                self.xattrs_check.setEnabled(checked)
            
            # 控制SponsorBlock功能的启用状态
            if hasattr(self, 'sponsorblock_check'):
                self.sponsorblock_check.setEnabled(checked)
            if hasattr(self, 'sponsorblock_sponsor_check'):
                # 当高级选项启用时，SponsorBlock子选项默认禁用（需要用户手动勾选主选项后才启用）
                sponsorblock_enabled = hasattr(self, 'sponsorblock_check') and self.sponsorblock_check.isChecked()
                self.sponsorblock_sponsor_check.setEnabled(checked and sponsorblock_enabled)
            if hasattr(self, 'sponsorblock_intro_check'):
                sponsorblock_enabled = hasattr(self, 'sponsorblock_check') and self.sponsorblock_check.isChecked()
                self.sponsorblock_intro_check.setEnabled(checked and sponsorblock_enabled)
            if hasattr(self, 'sponsorblock_outro_check'):
                sponsorblock_enabled = hasattr(self, 'sponsorblock_check') and self.sponsorblock_check.isChecked()
                self.sponsorblock_outro_check.setEnabled(checked and sponsorblock_enabled)
            if hasattr(self, 'sponsorblock_selfpromo_check'):
                sponsorblock_enabled = hasattr(self, 'sponsorblock_check') and self.sponsorblock_check.isChecked()
                self.sponsorblock_selfpromo_check.setEnabled(checked and sponsorblock_enabled)
            
            # 控制章节支持功能的启用状态
            if hasattr(self, 'split_chapters_check'):
                self.split_chapters_check.setEnabled(checked)
            
            # 控制章节子选项的启用状态（基于主控复选框状态）
            chapter_master_enabled = hasattr(self, 'split_chapters_check') and self.split_chapters_check.isChecked()
            if hasattr(self, 'remove_intro_btn'):
                self.remove_intro_btn.setEnabled(checked and chapter_master_enabled)
            if hasattr(self, 'remove_outro_btn'):
                self.remove_outro_btn.setEnabled(checked and chapter_master_enabled)
            if hasattr(self, 'remove_sponsor_btn'):
                self.remove_sponsor_btn.setEnabled(checked and chapter_master_enabled)
            time_section_master_enabled = hasattr(self, 'time_section_check') and self.time_section_check.isChecked()
            if hasattr(self, 'custom_section_input'):
                self.custom_section_input.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'add_custom_section_btn'):
                self.add_custom_section_btn.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'current_section_label'):
                self.current_section_label.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'custom_chapter_input'):
                self.custom_chapter_input.setEnabled(checked and chapter_master_enabled)
            if hasattr(self, 'add_chapter_btn'):
                self.add_chapter_btn.setEnabled(checked and chapter_master_enabled)
            if hasattr(self, 'remove_chapters_list'):
                self.remove_chapters_list.setEnabled(checked and chapter_master_enabled)
            if hasattr(self, 'force_keyframes_check'):
                self.force_keyframes_check.setEnabled(checked and chapter_master_enabled)
            if hasattr(self, 'remove_chapters_check'):
                self.remove_chapters_check.setEnabled(checked)
            if hasattr(self, 'remove_chapters_input'):
                # 移除章节输入框的状态同时受高级选项和复选框控制
                input_enabled = checked and (hasattr(self, 'remove_chapters_check') and self.remove_chapters_check.isChecked())
                self.remove_chapters_input.setEnabled(input_enabled)
            
            # 控制高级下载选项的启用状态
            if hasattr(self, 'live_from_start_check'):
                self.live_from_start_check.setEnabled(checked)
            if hasattr(self, 'wait_for_video_check'):
                self.wait_for_video_check.setEnabled(checked)
            
            # 控制特殊场景下载主控选项的启用状态
            if hasattr(self, 'special_scene_master_check'):
                self.special_scene_master_check.setEnabled(checked)
            
            # 控制特殊场景下载子选项的启用状态（需要主控选项和高级选项同时启用）
            master_enabled = hasattr(self, 'special_scene_master_check') and self.special_scene_master_check.isChecked()
            wait_enabled = master_enabled and hasattr(self, 'wait_for_video_check') and self.wait_for_video_check.isChecked()
            wait_range_enabled = wait_enabled and hasattr(self, 'wait_for_video_use_range_check') and self.wait_for_video_use_range_check.isChecked()
            if hasattr(self, 'wait_for_video_time'):
                self.wait_for_video_time.setEnabled(checked and wait_enabled)
            if hasattr(self, 'wait_for_video_use_range_check'):
                self.wait_for_video_use_range_check.setEnabled(checked and wait_enabled)
            if hasattr(self, 'wait_for_video_max_time'):
                self.wait_for_video_max_time.setEnabled(checked and wait_range_enabled)
            if hasattr(self, 'ignore_errors_check'):
                self.ignore_errors_check.setEnabled(checked and master_enabled)
            if hasattr(self, 'skip_unavailable_fragments_check'):
                self.skip_unavailable_fragments_check.setEnabled(checked and master_enabled)
            if hasattr(self, 'keep_fragments_check'):
                self.keep_fragments_check.setEnabled(checked and master_enabled)
            
            # 控制字幕相关功能的启用状态
            if hasattr(self, 'subtitle_check'):
                self.subtitle_check.setEnabled(checked)
            if hasattr(self, 'auto_subtitle_check'):
                self.auto_subtitle_check.setEnabled(checked)
            if hasattr(self, 'embed_subtitle_check'):
                self.embed_subtitle_check.setEnabled(checked)
            if hasattr(self, 'convert_subtitle_check'):
                # 当高级选项启用时，字幕转换默认禁用（需要用户手动勾选字幕下载后才启用）
                self.convert_subtitle_check.setEnabled(checked and (
                    (hasattr(self, 'subtitle_check') and self.subtitle_check.isChecked()) or
                    (hasattr(self, 'auto_subtitle_check') and self.auto_subtitle_check.isChecked())
                ))
            # 控制字幕语言和格式选择器（这些通常由字幕复选框控制，但也应该受高级选项影响）
            if hasattr(self, 'subtitle_lang'):
                self.subtitle_lang.setEnabled(checked and (
                    (hasattr(self, 'subtitle_check') and self.subtitle_check.isChecked()) or
                    (hasattr(self, 'auto_subtitle_check') and self.auto_subtitle_check.isChecked())
                ))
            if hasattr(self, 'subtitle_format'):
                self.subtitle_format.setEnabled(checked and (
                    (hasattr(self, 'subtitle_check') and self.subtitle_check.isChecked()) or
                    (hasattr(self, 'auto_subtitle_check') and self.auto_subtitle_check.isChecked())
                ))
            if hasattr(self, 'subtitle_delay_spin'):
                self.subtitle_delay_spin.setEnabled(checked and (
                    (hasattr(self, 'subtitle_check') and self.subtitle_check.isChecked()) or
                    (hasattr(self, 'auto_subtitle_check') and self.auto_subtitle_check.isChecked())
                ))
            
            # 控制基本选项的启用状态
            if hasattr(self, 'thumbnail_check'):
                self.thumbnail_check.setEnabled(checked)
            if hasattr(self, 'limit_speed_check'):
                self.limit_speed_check.setEnabled(checked)
            if hasattr(self, 'playlist_check'):
                self.playlist_check.setEnabled(checked)
            
            # 控制网络相关选项的启用状态
            if hasattr(self, 'proxy_check'):
                self.proxy_check.setEnabled(checked)
            if hasattr(self, 'source_address_check'):
                self.source_address_check.setEnabled(checked)
            if hasattr(self, 'ip_version_combo'):
                self.ip_version_combo.setEnabled(checked)
            
            # 控制时间片段下载主控选项的启用状态
            if hasattr(self, 'time_section_check'):
                self.time_section_check.setEnabled(checked)
            
            # 控制时间片段子选项的启用状态（需要主控选项和高级选项同时启用）
            time_section_master_enabled = hasattr(self, 'time_section_check') and self.time_section_check.isChecked()
            if hasattr(self, 'first_minute_btn'):
                self.first_minute_btn.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'last_minute_btn'):
                self.last_minute_btn.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'url_time_btn'):
                self.url_time_btn.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'start_hour'):
                self.start_hour.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'start_minute'):
                self.start_minute.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'start_second'):
                self.start_second.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'end_hour'):
                self.end_hour.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'end_minute'):
                self.end_minute.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'end_second'):
                self.end_second.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'add_section_btn'):
                self.add_section_btn.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'clear_sections_btn'):
                self.clear_sections_btn.setEnabled(checked and time_section_master_enabled)
            if hasattr(self, 'sections_list_widget'):
                self.sections_list_widget.setEnabled(checked and time_section_master_enabled)
            
            # ========================================
            # 第三步：在禁用控件后，再次确保依赖控件也被禁用
            # ========================================
            if not checked:
                # 禁用依赖于复选框的输入控件
                if hasattr(self, 'speed_limit'):
                    self.speed_limit.setEnabled(False)
                if hasattr(self, 'proxy_input'):
                    self.proxy_input.setEnabled(False)
                if hasattr(self, 'test_proxy_button'):
                    self.test_proxy_button.setEnabled(False)
                if hasattr(self, 'proxy_preset_combo'):
                    self.proxy_preset_combo.setEnabled(False)
                if hasattr(self, 'source_address_input'):
                    self.source_address_input.setEnabled(False)
                if hasattr(self, 'subtitle_lang'):
                    self.subtitle_lang.setEnabled(False)
                if hasattr(self, 'subtitle_format'):
                    self.subtitle_format.setEnabled(False)
                if hasattr(self, 'subtitle_delay_spin'):
                    self.subtitle_delay_spin.setEnabled(False)
                if hasattr(self, 'ip_version_combo'):
                    self.ip_version_combo.setEnabled(False)
                if hasattr(self, 'wait_for_video_time'):
                    self.wait_for_video_time.setEnabled(False)
                if hasattr(self, 'wait_for_video_use_range_check'):
                    self.wait_for_video_use_range_check.setEnabled(False)
                if hasattr(self, 'wait_for_video_max_time'):
                    self.wait_for_video_max_time.setEnabled(False)
                
                # 禁用时间片段相关控件
                if hasattr(self, 'first_minute_btn'):
                    self.first_minute_btn.setEnabled(False)
                if hasattr(self, 'last_minute_btn'):
                    self.last_minute_btn.setEnabled(False)
                if hasattr(self, 'url_time_btn'):
                    self.url_time_btn.setEnabled(False)
                if hasattr(self, 'start_hour'):
                    self.start_hour.setEnabled(False)
                if hasattr(self, 'start_minute'):
                    self.start_minute.setEnabled(False)
                if hasattr(self, 'start_second'):
                    self.start_second.setEnabled(False)
                if hasattr(self, 'end_hour'):
                    self.end_hour.setEnabled(False)
                if hasattr(self, 'end_minute'):
                    self.end_minute.setEnabled(False)
                if hasattr(self, 'end_second'):
                    self.end_second.setEnabled(False)
                if hasattr(self, 'add_section_btn'):
                    self.add_section_btn.setEnabled(False)
                if hasattr(self, 'clear_sections_btn'):
                    self.clear_sections_btn.setEnabled(False)
                if hasattr(self, 'sections_list_widget'):
                    self.sections_list_widget.setEnabled(False)
            
            # 更新状态提示
            if checked:
                print("[INFO] 高级选项已启用，所有功能现已可用")
            else:
                print("[WARNING] 高级选项已禁用，所有功能选项已重置")
            
            # 初始化SponsorBlock选项状态
            if hasattr(self, 'update_sponsorblock_options'):
                self.update_sponsorblock_options()
            
            # 初始化特殊场景下载选项状态
            if hasattr(self, 'update_special_scene_options'):
                self.update_special_scene_options(self.special_scene_master_check.isChecked() if hasattr(self, 'special_scene_master_check') else False)
            
            # 初始化时间片段下载选项状态
            if hasattr(self, 'on_time_section_master_toggled'):
                self.on_time_section_master_toggled(self.time_section_check.isChecked() if hasattr(self, 'time_section_check') else False)
                
        except Exception as e:
            print(t_format("[ERROR] 处理高级选项状态变化时发生错误: {}", e))
    
    # AI去水印相关方法已移除
    
    # AI去水印相关事件处理方法和线程类已移除
    
    def check_version_async(self):
        """异步检查yt-dlp版本"""
        def check_version():
            try:
                # 获取当前版本
                current_version = self.get_current_yt_dlp_version()
                if not current_version:
                    return
                
                # 获取最新版本
                latest_version = self.get_latest_yt_dlp_version()
                if not latest_version:
                    return
                
                # 比较版本
                if self.compare_versions(current_version, latest_version) < 0:
                    # 有新版本可用
                    QTimer.singleShot(0, lambda: self.show_update_notification(current_version, latest_version))
                    
            except Exception as e:
                print(t_format("版本检查失败: {}", e))
        
        # 在后台线程中执行版本检查
        import threading
        threading.Thread(target=check_version, daemon=True).start()
    
    def get_current_yt_dlp_version(self):
        """获取当前yt-dlp版本"""
        try:
            result = subprocess.run(
                ["yt-dlp.exe", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None
    
    def get_latest_yt_dlp_version(self):
        """获取最新yt-dlp版本"""
        try:
            import urllib.request
            import json
            
            # 从GitHub API获取最新版本信息
            with urllib.request.urlopen("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest", timeout=10) as response:
                data = json.loads(response.read().decode())
                return data['tag_name']
        except Exception:
            return None
    
    def compare_versions(self, version1, version2):
        """比较版本号，返回 -1, 0, 1"""
        try:
            # 提取版本号中的数字部分
            v1_parts = [int(x) for x in version1.replace('-', '.').split('.') if x.isdigit()]
            v2_parts = [int(x) for x in version2.replace('-', '.').split('.') if x.isdigit()]
            
            # 补齐长度
            max_len = max(len(v1_parts), len(v2_parts))
            v1_parts.extend([0] * (max_len - len(v1_parts)))
            v2_parts.extend([0] * (max_len - len(v2_parts)))
            
            # 比较
            for i in range(max_len):
                if v1_parts[i] < v2_parts[i]:
                    return -1
                elif v1_parts[i] > v2_parts[i]:
                    return 1
            return 0
        except Exception:
            return 0
    
    def show_update_notification(self, current_version, latest_version):
        """显示更新通知"""
        reply = QMessageBox.question(
            self,
            t("yt-dlp 更新可用"),
            t("发现新版本！\n\n") + t_format("当前版本: {}\n", current_version) + t_format("最新版本: {}\n\n", latest_version) + t("是否立即更新？"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            self.check_and_update_yt_dlp()
    
    # ========== 图片处理相关方法 ==========
    
    def browse_image_files(self):
        """选择要处理的图片文件"""
        try:
            files, _ = QFileDialog.getOpenFileNames(
                self,
                t("选择图片文件"),
                "",
                t("图片文件 (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tiff *.tif *.ico *.heic *.heif);;所有文件 (*.*)")
            )
            if files:
                added = 0
                for file_path in files:
                    if file_path not in self.image_file_list:
                        self.image_file_list.append(file_path)
                        # 获取文件信息
                        filename = os.path.basename(file_path)
                        filesize = os.path.getsize(file_path)
                        size_str = self._format_file_size(filesize)
                        self.image_list_widget.addItem(f"{filename} ({size_str})")
                        added += 1
                if added:
                    self._reset_image_process_state()
                self._update_image_count()
        except Exception as e:
            self.image_progress_text.append(t_format("❌ 添加文件失败: {}", str(e)))
    
    def remove_selected_images(self):
        """移除选中的图片"""
        try:
            selected_items = self.image_list_widget.selectedItems()
            if not selected_items:
                return
            for item in selected_items:
                row = self.image_list_widget.row(item)
                self.image_list_widget.takeItem(row)
                if row < len(self.image_file_list):
                    self.image_file_list.pop(row)
            self._reset_image_process_state()
            self._update_image_count()
        except Exception as e:
            self.image_progress_text.append(t_format("❌ 移除文件失败: {}", str(e)))
    
    def clear_image_list(self):
        """清空图片列表"""
        self.image_list_widget.clear()
        self.image_file_list.clear()
        self._reset_image_process_state()
        self._update_image_count()
    
    def _update_image_count(self):
        """更新图片数量标签"""
        count = len(self.image_file_list)
        self.image_count_label.setText(t_format("已添加 {} 张图片", count))

    def _reset_image_process_state(self):
        """当图片列表变化时，重置处理状态显示。"""
        self.image_progress_bar.setValue(0)
        self.image_progress_text.clear()
    
    def browse_image_output_dir(self):
        """选择输出目录"""
        try:
            directory = QFileDialog.getExistingDirectory(
                self,
                t("选择输出目录"),
                self.image_output_dir.text() or ""
            )
            if directory:
                self.image_output_dir.setText(directory)
        except Exception as e:
            self.image_progress_text.append(t_format("❌ 选择目录失败: {}", str(e)))
    
    def on_image_format_changed(self, format_text):
        """输出格式改变时更新UI - 切换质量/压缩控件"""
        if format_text == "PNG":
            # PNG使用压缩级别（无损）
            self.quality_label.setText(t("压缩级别:"))
            self.quality_low_label.setText(t("快速"))
            self.quality_high_label.setText(t("高压缩"))
            self.image_quality_slider.hide()
            self.image_quality_label.hide()
            self.png_compress_slider.show()
            self.png_compress_label.show()
            # 把PNG滑块添加到布局
            layout = self.quality_row_widget.layout()
            layout.insertWidget(1, self.png_compress_slider)
            layout.insertWidget(3, self.png_compress_label)
        elif format_text in ["BMP", "GIF", "TIFF", "ICO"]:
            # 这些格式不支持质量调整
            self.quality_label.setText(t("压缩选项:"))
            self.quality_low_label.setText("")
            self.quality_high_label.setText(t("(此格式无需设置)"))
            self.image_quality_slider.hide()
            self.image_quality_label.hide()
            self.png_compress_slider.hide()
            self.png_compress_label.hide()
        else:
            # JPEG/WebP使用质量（有损）
            self.quality_label.setText(t("压缩质量:"))
            self.quality_low_label.setText(t("低"))
            self.quality_high_label.setText(t("高"))
            self.png_compress_slider.hide()
            self.png_compress_label.hide()
            self.image_quality_slider.show()
            self.image_quality_label.show()
            # 把质量滑块添加到布局
            layout = self.quality_row_widget.layout()
            layout.insertWidget(1, self.image_quality_slider)
            layout.insertWidget(3, self.image_quality_label)
    
    def on_resize_mode_changed(self, mode_text):
        """尺寸调整模式改变时更新UI"""
        self.resize_percent_widget.hide()
        self.resize_fixed_widget.hide()
        self.resize_maxside_widget.hide()
        
        if mode_text == "按百分比":
            self.resize_percent_widget.show()
        elif mode_text == "固定尺寸":
            self.resize_fixed_widget.show()
        elif mode_text == "最大边长":
            self.resize_maxside_widget.show()
    
    def start_image_process(self):
        """开始图片处理"""
        try:
            # 检查Pillow
            if not check_pillow():
                reply = QMessageBox.question(
                    self,
                    t("需要安装 Pillow"),
                    t("图片处理功能需要 Pillow 库。\n\n是否现在安装？"),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    self.image_progress_text.append(t("正在安装 Pillow..."))
                    import subprocess
                    result = subprocess.run(
                        ['pip', 'install', 'Pillow'],
                        capture_output=True,
                        text=True,
                        timeout=120
                    )
                    if result.returncode == 0:
                        self.image_progress_text.append(t("✅ Pillow 安装成功！请重新启动程序。"))
                    else:
                        self.image_progress_text.append(t_format("❌ 安装失败: {}", result.stderr))
                return
            
            # 检查文件列表
            if not self.image_file_list:
                QMessageBox.warning(self, t("提示"), t("请先添加要处理的图片文件"))
                return
            
            # 获取输出目录
            output_dir = self.image_output_dir.text().strip()
            if not output_dir:
                # 使用第一个文件的目录
                output_dir = os.path.dirname(self.image_file_list[0])
            
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            
            # 获取处理选项
            format_text = self.image_format_combo.currentText()
            resize_mode_text = self.resize_mode_combo.currentText()
            
            resize_mode_map = {
                "不调整": "none",
                "按百分比": "percent",
                "固定尺寸": "fixed",
                "最大边长": "max_side"
            }
            
            options = {
                'format': format_text,
                'quality': self.image_quality_slider.value(),
                'compress_level': self.png_compress_slider.value(),
                'resize_mode': resize_mode_map.get(resize_mode_text, 'none'),
                'resize_percent': self.resize_percent_spin.value(),
                'resize_width': self.resize_width_spin.value(),
                'resize_height': self.resize_height_spin.value(),
                'resize_max_side': self.resize_maxside_spin.value(),
                'keep_aspect_ratio': self.keep_ratio_check.isChecked(),
                'resample': self.resample_combo.currentText(),
                'suffix': self.image_suffix_input.text().strip(),
                'overwrite': self.image_overwrite_check.isChecked()
            }
            
            # 更新UI状态
            self.start_image_process_btn.setEnabled(False)
            self.cancel_image_process_btn.setEnabled(True)
            self.image_progress_bar.setValue(0)
            self.image_progress_text.clear()
            
            # 保存输出目录到设置（记忆功能）
            if output_dir:
                self.settings.setValue("image_output_dir", output_dir)
            
            # 保存处理选项用于历史记录
            self._current_image_process_options = options
            self._current_image_output_dir = output_dir
            self._current_image_files = self.image_file_list.copy()
            
            # 创建并启动处理线程
            self.image_process_thread = ImageProcessThread(
                self.image_file_list.copy(),
                output_dir,
                options
            )
            self.image_process_thread.progress_signal.connect(self._on_image_progress)
            self.image_process_thread.progress_percent_signal.connect(self._on_image_progress_percent)
            self.image_process_thread.finished_signal.connect(self._on_image_process_finished)
            self.image_process_thread.start()
            
        except Exception as e:
            self.image_progress_text.append(t_format("❌ 启动处理失败: {}", str(e)))
            self.start_image_process_btn.setEnabled(True)
            self.cancel_image_process_btn.setEnabled(False)
    
    def cancel_image_process(self):
        """取消图片处理"""
        if self.image_process_thread and self.image_process_thread.isRunning():
            self.image_process_thread.stop()
            self.image_progress_text.append(t("⚠️ 正在取消处理..."))
    
    def _on_image_progress(self, message):
        """处理进度日志回调"""
        self.image_progress_text.append(message)
        # 滚动到底部
        scrollbar = self.image_progress_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _on_image_progress_percent(self, percent):
        """处理进度百分比回调"""
        self.image_progress_bar.setValue(int(percent))
    
    def _on_image_process_finished(self, success, message):
        """处理完成回调"""
        self.start_image_process_btn.setEnabled(True)
        self.cancel_image_process_btn.setEnabled(False)
        
        if success:
            self.image_progress_text.append(f"\n✅ {message}")
        else:
            self.image_progress_text.append(f"\n⚠️ {message}")
        
        # 保存到历史记录
        try:
            # 从消息中解析成功/失败数量
            success_count = 0
            fail_count = 0
            total_saved = 0
            
            import re
            # 匹配"成功 X 个"
            match = re.search(r'成功\s*(\d+)', message)
            if match:
                success_count = int(match.group(1))
            # 匹配"失败 X 个"
            match = re.search(r'失败\s*(\d+)', message)
            if match:
                fail_count = int(match.group(1))
            
            # 计算输出文件列表
            output_files = []
            if hasattr(self, '_current_image_files') and hasattr(self, '_current_image_output_dir') and hasattr(self, '_current_image_process_options'):
                output_dir = self._current_image_output_dir
                options = self._current_image_process_options
                output_format = options.get('format', 'JPEG') if options else 'JPEG'
                suffix = options.get('suffix', '') if options else ''
                
                # 格式扩展名映射
                ext_map = {
                    'JPEG': '.jpg', 'PNG': '.png', 'WebP': '.webp',
                    'BMP': '.bmp', 'GIF': '.gif', 'TIFF': '.tiff', 'ICO': '.ico'
                }
                new_ext = ext_map.get(output_format, '.jpg')
                
                for input_file in self._current_image_files[:10]:  # 只取前10个
                    base_name = os.path.splitext(os.path.basename(input_file))[0]
                    output_name = f"{base_name}{suffix}{new_ext}"
                    output_path = os.path.join(output_dir, output_name)
                    if os.path.exists(output_path):
                        output_files.append(output_path)
            
            # 添加到历史记录
            if hasattr(self, '_current_image_files') and self._current_image_files:
                self.history_manager.add_image_process_record(
                    input_files=self._current_image_files,
                    output_dir=getattr(self, '_current_image_output_dir', ''),
                    success_count=success_count,
                    fail_count=fail_count,
                    total_saved=total_saved,
                    process_options=getattr(self, '_current_image_process_options', None),
                    output_files=output_files
                )
                # 刷新历史记录表格
                self.load_history_table()
        except Exception as e:
            print(t_format("保存图片处理历史记录失败: {}", e))

        has_skipped = "跳过" in message
        has_real_success = "成功 0 个" not in message
        has_failures = "失败 0 个" not in message

        if success and has_real_success:
            self.play_completion_notification_sound()
            QMessageBox.information(self, t("图片处理完成"), message)
        elif has_skipped and not has_failures:
            QMessageBox.warning(self, t("图片处理提示"), message)
        else:
            QMessageBox.warning(self, t("图片处理失败"), message)

    def browse_realesrgan_files(self):
        """选择画质修复输入文件"""
        try:
            files, _ = QFileDialog.getOpenFileNames(
                self,
                t("选择要修复的图片或视频"),
                "",
                t("媒体文件 (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff *.mp4 *.mov *.avi *.mkv *.flv *.webm *.m4v);;所有文件 (*.*)")
            )
            added = 0
            for file_path in files:
                if not is_supported_realesrgan_media(file_path):
                    continue
                if file_path in self.realesrgan_file_list:
                    continue
                self.realesrgan_file_list.append(file_path)
                filename = os.path.basename(file_path)
                filesize = os.path.getsize(file_path)
                size_str = self._format_file_size(filesize)
                self.realesrgan_list_widget.addItem(f"{filename} ({size_str})")
                added += 1
            if added:
                self._update_realesrgan_count()
        except Exception as e:
            self.realesrgan_progress_text.append(t_format("❌ 添加文件失败: {}", e))

    def remove_selected_realesrgan_files(self):
        """移除选中的画质修复文件"""
        try:
            selected_items = self.realesrgan_list_widget.selectedItems()
            for item in reversed(selected_items):
                row = self.realesrgan_list_widget.row(item)
                self.realesrgan_list_widget.takeItem(row)
                if row < len(self.realesrgan_file_list):
                    self.realesrgan_file_list.pop(row)
            self._update_realesrgan_count()
        except Exception as e:
            self.realesrgan_progress_text.append(t_format("❌ 移除文件失败: {}", e))

    def clear_realesrgan_list(self):
        """清空画质修复列表"""
        self.realesrgan_list_widget.clear()
        self.realesrgan_file_list.clear()
        self._update_realesrgan_count()

    def _update_realesrgan_count(self):
        count = len(self.realesrgan_file_list)
        self.realesrgan_count_label.setText(t_format("已添加 {} 个文件", count))

    def browse_realesrgan_output_dir(self):
        """选择画质修复输出目录"""
        try:
            directory = QFileDialog.getExistingDirectory(
                self,
                t("选择画质修复输出目录"),
                self.realesrgan_output_dir.text() or ""
            )
            if directory:
                self.realesrgan_output_dir.setText(directory)
                self.settings.setValue("realesrgan_output_dir", directory)
        except Exception as e:
            self.realesrgan_progress_text.append(t_format("❌ 选择输出目录失败: {}", e))

    def _on_realesrgan_runtime_python_changed(self, value):
        runtime_python = str(value or "").strip()
        if runtime_python and os.path.isfile(runtime_python):
            self.settings.setValue("realesrgan_runtime_python", runtime_python)
            os.environ["SOURCECRAFT_REALESRGAN_PYTHON"] = runtime_python
        else:
            self.settings.remove("realesrgan_runtime_python")
            os.environ.pop("SOURCECRAFT_REALESRGAN_PYTHON", None)
        self.check_realesrgan_runtime(silent=True)

    def _refresh_realesrgan_device_options(self, capability=None):
        capability = capability or get_realesrgan_runtime_capabilities(self._collect_realesrgan_options() if hasattr(self, "realesrgan_preset_combo") else {})
        available_devices = [str(item).strip().lower() for item in (capability.get("available_devices") or ["cpu"])]
        has_cuda = "cuda" in available_devices
        ncnn_available = bool(capability.get("ncnn_available"))
        ncnn_video_available = bool(capability.get("ncnn_video_available"))

        previous_value = str(self.realesrgan_device_combo.currentData() or "auto").strip().lower()
        self.realesrgan_device_combo.blockSignals(True)
        self.realesrgan_device_combo.clear()

        if has_cuda or ncnn_available:
            auto_label = "自动检测硬件（推荐）"
            if ncnn_video_available:
                auto_label = "自动检测硬件（视频将优先走 Vulkan）"
            elif ncnn_available:
                auto_label = "自动检测硬件（已支持 Vulkan 视频加速）"
            self.realesrgan_device_combo.addItem(auto_label, "auto")
        else:
            self.realesrgan_device_combo.addItem(t("自动选择（当前仅能用 CPU）"), "auto")

        if has_cuda or ncnn_available:
            self.realesrgan_device_combo.addItem(t("使用 CPU（更稳）"), "cpu")
        else:
            self.realesrgan_device_combo.addItem(t("使用 CPU"), "cpu")

        target_value = previous_value
        if target_value not in ("auto", "cpu"):
            target_value = "auto"

        index = self.realesrgan_device_combo.findData(target_value)
        if index < 0:
            index = self.realesrgan_device_combo.findData("auto")
        if index < 0:
            index = 0
        self.realesrgan_device_combo.setCurrentIndex(index)
        self.realesrgan_device_combo.setToolTip(
            t("普通情况下保持“自动检测硬件（推荐）”即可。程序会自动探测 NVIDIA / AMD / Intel 等显卡并优先走最快的可用路径；如果某条 GPU 路径不稳定，会继续自动降级重试。")
        )
        self.realesrgan_device_combo.blockSignals(False)

    def _describe_realesrgan_runtime(self, title, runtime_info):
        if not runtime_info:
            return t_format("{}: 未检测到", title)

        python_path = str(runtime_info.get("python") or "").strip()
        runtime_name = os.path.basename(python_path) if python_path else "未知"
        providers = [str(item).strip() for item in (runtime_info.get("ort_providers") or []) if str(item).strip()]
        if runtime_info.get("ort_trt_available"):
            mode = "GPU 可用（TensorRT）"
        elif runtime_info.get("ort_dml_available"):
            mode = "GPU 可用（DirectML）"
        elif runtime_info.get("ort_cuda_available"):
            mode = "GPU 可用（ONNX CUDA）"
        elif runtime_info.get("cuda_available"):
            mode = "GPU 可用（PyTorch CUDA）"
        elif runtime_info.get("cuda_detected"):
            mode = "检测到 GPU，但当前不可用"
        elif runtime_info.get("ort_cpu_available"):
            mode = "仅 CPU（ONNX Runtime）"
        else:
            mode = "仅 CPU"
        version_parts = []
        if runtime_info.get("torch_version"):
            version_parts.append(f"torch {runtime_info.get('torch_version')}")
        if runtime_info.get("cuda_version"):
            version_parts.append(f"CUDA {runtime_info.get('cuda_version')}")
        if providers:
            version_parts.append("ORT " + ", ".join(providers))
        if runtime_info.get("device_name"):
            version_parts.append(str(runtime_info.get("device_name")))

        detail = f"{title}: {mode} | {runtime_name}"
        if version_parts:
            detail += " | " + " | ".join(version_parts)
        if python_path:
            detail += t_format("\n路径: {}", python_path)
        error_text = str(runtime_info.get("error") or "").strip()
        if error_text:
            detail += t_format("\n异常: {}", error_text)
        return detail

    def browse_realesrgan_runtime_python(self):
        """选择画质修复外部运行时 Python"""
        try:
            current_value = self.realesrgan_runtime_python.text().strip()
            start_dir = current_value if (current_value and os.path.isfile(current_value)) else self._get_dialog_start_dir()
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                t("选择画质修复运行时 Python"),
                start_dir,
                "Python (python.exe);;Executable (*.exe);;All files (*.*)",
            )
            if file_path:
                self.realesrgan_runtime_python.setText(file_path)
        except Exception as e:
            self.realesrgan_progress_text.append(t_format("❌ 选择画质修复运行时失败: {}", e))

    def on_realesrgan_preset_changed(self):
        """根据场景预设更新说明和默认行为"""
        preset = self.realesrgan_preset_combo.currentData()
        preset_map = getattr(self, "_realesrgan_preset_map", {})
        desc, model_name, face_default, denoise_strength, best_for, caution = preset_map.get(
            preset,
            preset_map.get(
                "general",
                (
                    "基于官方 realesr-general-x4v3：适合通用图像场景的小模型，占用更低、速度更快；去模糊和降噪能力相对温和，适合普通照片、截图、商品图和大多数视频画面。",
                    "realesr-general-x4v3",
                    False,
                    0.5,
                    "适合手机照片、截图、商品图和大多数日常素材。",
                    "如果画面本身是动漫，边缘和线条通常不如动漫专用模型稳定。",
                ),
            )
        )
        self._realesrgan_selected_model_name = model_name
        self._realesrgan_selected_denoise_strength = denoise_strength
        self.realesrgan_keep_face_check.setChecked(face_default)
        self.realesrgan_denoise_spin.setValue(denoise_strength)
        if not self.realesrgan_force_model_check.isChecked():
            combo_index = self.realesrgan_model_combo.findData(model_name)
            if combo_index >= 0:
                self.realesrgan_model_combo.setCurrentIndex(combo_index)
        if hasattr(self, "realesrgan_scale_combo") and not (
            hasattr(self, "realesrgan_custom_scale_check") and self.realesrgan_custom_scale_check.isChecked()
        ):
            preferred_scale = 2.0 if preset in ("general", "portrait", "old_photo", "anime_image", "anime_video") else 4.0
            scale_index = self.realesrgan_scale_combo.findData(preferred_scale)
            if scale_index >= 0:
                self.realesrgan_scale_combo.setCurrentIndex(scale_index)
        self._refresh_realesrgan_model_state()

    def _get_realesrgan_effective_model_name(self):
        if hasattr(self, "realesrgan_force_model_check") and self.realesrgan_force_model_check.isChecked():
            return str(self.realesrgan_model_combo.currentData() or getattr(self, "_realesrgan_selected_model_name", "realesr-general-x4v3"))
        return getattr(self, "_realesrgan_selected_model_name", "realesr-general-x4v3")

    def _reset_realesrgan_advanced_options(self):
        if hasattr(self, "realesrgan_device_combo"):
            device_index = self.realesrgan_device_combo.findData("auto")
            if device_index >= 0:
                self.realesrgan_device_combo.setCurrentIndex(device_index)
        if hasattr(self, "realesrgan_force_model_check"):
            self.realesrgan_force_model_check.setChecked(False)
        if hasattr(self, "realesrgan_model_combo"):
            default_model = getattr(self, "_realesrgan_selected_model_name", "realesr-general-x4v3")
            model_index = self.realesrgan_model_combo.findData(default_model)
            if model_index >= 0:
                self.realesrgan_model_combo.setCurrentIndex(model_index)
        if hasattr(self, "realesrgan_custom_scale_check"):
            self.realesrgan_custom_scale_check.setChecked(False)
        if hasattr(self, "realesrgan_custom_scale_spin"):
            self.realesrgan_custom_scale_spin.setValue(4.0)
            self.realesrgan_custom_scale_spin.setEnabled(False)
        if hasattr(self, "realesrgan_denoise_spin"):
            self.realesrgan_denoise_spin.setValue(getattr(self, "_realesrgan_selected_denoise_strength", 0.5))
        if hasattr(self, "realesrgan_output_format_combo"):
            output_index = self.realesrgan_output_format_combo.findData("auto")
            if output_index >= 0:
                self.realesrgan_output_format_combo.setCurrentIndex(output_index)
        if hasattr(self, "realesrgan_memory_safe_check"):
            self.realesrgan_memory_safe_check.setChecked(False)
        if hasattr(self, "realesrgan_keep_face_check"):
            self.realesrgan_keep_face_check.setChecked(False)
        if hasattr(self, "realesrgan_output_dir"):
            self.realesrgan_output_dir.clear()
        if hasattr(self, "realesrgan_suffix_input"):
            self.realesrgan_suffix_input.setText(t("_画质修复"))
        if hasattr(self, "realesrgan_overwrite_check"):
            self.realesrgan_overwrite_check.setChecked(False)
        self.on_realesrgan_preset_changed()

    def _on_realesrgan_force_model_toggled(self, checked):
        self._refresh_realesrgan_model_state()
        self.check_realesrgan_runtime(silent=True)

    def _on_realesrgan_model_selection_changed(self):
        self._refresh_realesrgan_model_state()
        self.check_realesrgan_runtime(silent=True)

    def _on_realesrgan_advanced_group_toggled(self, checked):
        if checked:
            self._refresh_realesrgan_model_state()
        else:
            self._reset_realesrgan_advanced_options()
            self._refresh_realesrgan_model_state()

    def _refresh_realesrgan_model_state(self):
        advanced_enabled = bool(getattr(self, "realesrgan_advanced_group", None) and self.realesrgan_advanced_group.isChecked())
        manual_override = bool(getattr(self, "realesrgan_force_model_check", None) and self.realesrgan_force_model_check.isChecked())
        custom_scale_enabled = bool(getattr(self, "realesrgan_custom_scale_check", None) and self.realesrgan_custom_scale_check.isChecked())

        always_bound_widgets = [
            getattr(self, "realesrgan_device_combo", None),
            getattr(self, "realesrgan_force_model_check", None),
            getattr(self, "realesrgan_custom_scale_check", None),
            getattr(self, "realesrgan_output_format_combo", None),
            getattr(self, "realesrgan_memory_safe_check", None),
            getattr(self, "realesrgan_output_dir", None),
            getattr(self, "realesrgan_suffix_input", None),
            getattr(self, "realesrgan_overwrite_check", None),
        ]
        for widget in always_bound_widgets:
            if widget is not None:
                widget.setEnabled(advanced_enabled)
        browse_button = getattr(self, "browse_realesrgan_output_btn", None)
        if browse_button is not None:
            browse_button.setEnabled(advanced_enabled)
        if hasattr(self, "realesrgan_model_combo"):
            self.realesrgan_model_combo.setEnabled(advanced_enabled and manual_override)
        if hasattr(self, "realesrgan_custom_scale_spin"):
            self.realesrgan_custom_scale_spin.setEnabled(advanced_enabled and custom_scale_enabled)

        active_model = self._get_realesrgan_effective_model_name()
        supports_face = "anime" not in active_model
        if hasattr(self, "realesrgan_keep_face_check"):
            self.realesrgan_keep_face_check.setEnabled(advanced_enabled and supports_face)
            if not supports_face:
                self.realesrgan_keep_face_check.setChecked(False)

        if hasattr(self, "realesrgan_denoise_spin") and hasattr(self, "realesrgan_denoise_hint"):
            supports_denoise = active_model == "realesr-general-x4v3"
            self.realesrgan_denoise_spin.setEnabled(advanced_enabled and supports_denoise)
            self.realesrgan_denoise_hint.setEnabled(advanced_enabled)
            if supports_denoise:
                self.realesrgan_denoise_hint.setText(t("仅当前通用小模型支持降噪强度调节。数值越高，降噪越强；数值越低，通常保留更多原始细节。"))
                self.realesrgan_denoise_hint.setToolTip(self.realesrgan_denoise_hint.text())
            else:
                self.realesrgan_denoise_hint.setText(t("当前模型不使用降噪强度调节，因此这里会自动禁用。"))
                self.realesrgan_denoise_hint.setToolTip(self.realesrgan_denoise_hint.text())

    def _collect_realesrgan_options(self):
        preset = self.realesrgan_preset_combo.currentData()
        model_name = self._get_realesrgan_effective_model_name()
        face_enhance = self.realesrgan_keep_face_check.isChecked() and "anime" not in model_name
        if hasattr(self, "realesrgan_custom_scale_check") and self.realesrgan_custom_scale_check.isChecked():
            outscale = float(self.realesrgan_custom_scale_spin.value())
        else:
            outscale = float(self.realesrgan_scale_combo.currentData() or 4.0)
        use_safe_mode = self.realesrgan_memory_safe_check.isChecked()
        selected_device = str(self.realesrgan_device_combo.currentData() or "auto").strip().lower()
        denoise_strength = float(self.realesrgan_denoise_spin.value()) if model_name == "realesr-general-x4v3" else 1.0

        tile_size = 512 if use_safe_mode else 0
        tile_pad = 24 if use_safe_mode else 10
        pre_pad = 10 if use_safe_mode else 0

        return {
            "preset": preset,
            "model_name": model_name,
            "outscale": outscale,
            "denoise_strength": denoise_strength,
            "tile": tile_size,
            "tile_pad": tile_pad,
            "pre_pad": pre_pad,
            "alpha_upsampler": "realesrgan",
            "device": selected_device,
            "fp32": use_safe_mode,
            "face_enhance": face_enhance,
            "ext": str(self.realesrgan_output_format_combo.currentData() or "auto").strip().lower(),
            "fps": None,
            "extract_frame_first": True,
            "video_backend": "auto",
            "suffix": self.realesrgan_suffix_input.text().strip() or "_画质修复",
            "overwrite": self.realesrgan_overwrite_check.isChecked(),
        }

    def check_realesrgan_runtime(self, silent=False):
        """检查画质修复运行时状态"""
        try:
            ensure_realesrgan_runtime_dirs()
            options = self._collect_realesrgan_options() if hasattr(self, "realesrgan_preset_combo") else {}
            missing_deps = get_realesrgan_missing_dependencies(options)
            current_files = getattr(self, "realesrgan_file_list", None) or []
            missing_models = get_realesrgan_missing_download_resources(options, current_files)
            execution_plan = get_realesrgan_execution_plan(options)
            capability = execution_plan.get("capability") or get_realesrgan_runtime_capabilities(options)
            current_runtime = capability.get("current") or {}
            external_runtime = capability.get("external") or {}
            effective_runtime = capability.get("effective") or {}
            effective_cuda_detected = bool(capability.get("cuda_detected"))
            effective_cuda = bool(capability.get("cuda_available"))
            ncnn_available = bool(capability.get("ncnn_available"))
            ncnn_video_available = bool(capability.get("ncnn_video_available"))
            available_video_backends = [str(item).strip() for item in (capability.get("available_video_backends") or []) if str(item).strip()]
            available_image_backends = [str(item).strip() for item in (capability.get("available_image_backends") or []) if str(item).strip()]
            system_gpus = execution_plan.get("system_gpus") or []
            gpu_names = [str(item.get("name") or "").strip() for item in system_gpus if str(item.get("name") or "").strip()]
            selected_encoder = str(execution_plan.get("selected_encoder") or "libx264").strip()
            selected_video_backend = str(execution_plan.get("selected_video_backend") or "cpu").strip()
            normalized_options = execution_plan.get("normalized_options") or {}
            requested_model_name = str(options.get("model_name") or "").strip()
            normalized_model_name = str(normalized_options.get("model_name") or requested_model_name).strip()
            available_devices = [str(item).strip().lower() for item in (capability.get("available_devices") or [])]
            has_cuda = "cuda" in available_devices
            self._refresh_realesrgan_device_options(capability)
            selected_device = str(self.realesrgan_device_combo.currentData() or "auto").strip().lower()

            if missing_models:
                summary_message = "资源缺失"
            elif can_run_with_current_or_external_runtime(options):
                if available_video_backends or available_image_backends:
                    summary_message = "自动加速"
                elif has_cuda:
                    if selected_device == "cpu":
                        summary_message = "CPU 模式"
                    elif selected_device == "cuda":
                        summary_message = "显卡加速"
                    else:
                        summary_message = "自动优先显卡"
                elif effective_cuda_detected:
                    summary_message = "回退 CPU"
                else:
                    summary_message = "仅 CPU"

                effective_python = os.path.abspath(str(effective_runtime.get("python") or sys.executable))
                current_python = os.path.abspath(sys.executable)
                if effective_python and effective_python != current_python:
                    summary_message += " · 专用环境"
                elif selected_device == "cuda" and not effective_cuda and not ncnn_video_available:
                    if effective_cuda_detected:
                        summary_message = "显卡不可用 · CPU"
                    else:
                        summary_message = "未检测到显卡"
            else:
                summary_message = "引擎未就绪"

            backend_label_map = {
                "ort_tensorrt": "ONNX Runtime TensorRT",
                "ort_cuda": "ONNX Runtime CUDA",
                "ort_directml": "ONNX Runtime DirectML",
                "ort_cpu": "ONNX Runtime CPU",
                "torch_cuda": "PyTorch CUDA",
                "ncnn": "NCNN/Vulkan",
                "cpu": "CPU",
                "external_runtime": "外部运行时",
            }
            backend_short_label_map = {
                "ort_tensorrt": "TensorRT",
                "ort_cuda": "CUDA",
                "ort_directml": "DirectML",
                "ort_cpu": "CPU",
                "torch_cuda": "PyTorch CUDA",
                "ncnn": "NCNN",
                "cpu": "CPU",
                "external_runtime": "外部运行时",
            }
            selected_backend_label = backend_label_map.get(selected_video_backend, selected_video_backend or "CPU")
            selected_backend_short_label = backend_short_label_map.get(selected_video_backend, selected_video_backend or "CPU")
            if selected_video_backend in ("ort_cuda", "ort_directml", "ort_cpu", "torch_cuda", "ncnn"):
                summary_message += f" · {selected_backend_short_label}"
            elif ncnn_available:
                summary_message += " · 备用 NCNN"
            if requested_model_name and normalized_model_name and requested_model_name != normalized_model_name:
                summary_message += " · 模型已调整"

            effective_runtime_desc = "系统自动选择的加速环境"
            if effective_runtime and os.path.abspath(str(effective_runtime.get("python") or "")) == os.path.abspath(sys.executable):
                effective_runtime_desc = "当前程序内置环境"
            detail_parts = [
                "检测结果: " + ("视频已支持自动后端选择" if available_video_backends else ("CPU / GPU(CUDA)" if has_cuda else ("检测到 GPU，但 CUDA 当前不可用" if effective_cuda_detected else "仅 CPU"))),
                "视频高速后端: " + ("NCNN/Vulkan 已就绪" if ncnn_video_available else ("NCNN/Vulkan 已检测到，但当前参数不兼容" if ncnn_available else "未检测到 NCNN/Vulkan")),
                "可用图片后端: " + (" / ".join(available_image_backends) if available_image_backends else "无"),
                "实际识别显卡: " + (" / ".join(gpu_names) if gpu_names else "未识别到可用显卡"),
                "计划执行后端: " + selected_video_backend,
                "可用视频后端: " + (" / ".join(available_video_backends) if available_video_backends else "无"),
                "请求模型: " + (requested_model_name or "未指定"),
                "实际视频模型: " + (normalized_model_name or requested_model_name or "未指定"),
                "计划输出编码器: " + selected_encoder,
                effective_runtime_desc,
                self._describe_realesrgan_runtime(t("实际使用环境"), effective_runtime),
            ]
            detail_message = "\n".join(detail_parts)
            self.realesrgan_runtime_hint.setText(summary_message)
            self.realesrgan_runtime_hint.setToolTip(detail_message)
            if not silent:
                self.realesrgan_progress_text.append(summary_message)
                self.realesrgan_progress_text.append(detail_message)
        except Exception as e:
            if hasattr(self, "realesrgan_runtime_hint"):
                self.realesrgan_runtime_hint.setText(t_format("运行时检查失败: {}", e))
            if not silent and hasattr(self, "realesrgan_progress_text"):
                self.realesrgan_progress_text.append(t_format("❌ 运行时检查失败: {}", e))

    def download_realesrgan_models(self):
        """下载画质修复资源（内部工具保留）"""
        try:
            options = self._collect_realesrgan_options() if hasattr(self, "realesrgan_preset_combo") else {}
            self.realesrgan_progress_text.append(t("开始下载当前任务所需画质修复资源..."))
            downloaded = download_realesrgan_models(
                progress_cb=self.realesrgan_progress_text.append,
                face_enhance=self.realesrgan_keep_face_check.isChecked(),
                options=options,
                file_list=self.realesrgan_file_list.copy(),
            )
            if downloaded:
                self.realesrgan_progress_text.append(t_format("✅ 已下载 {} 个画质修复资源文件", len(downloaded)))
            else:
                self.realesrgan_progress_text.append(t("✅ 当前任务所需画质修复资源已齐全，无需重复下载"))
            self.check_realesrgan_runtime(silent=True)
        except Exception as e:
            self.realesrgan_progress_text.append(t_format("❌ 下载画质修复资源失败: {}", e))

    def start_realesrgan_process(self):
        """启动画质修复"""
        try:
            if not self.realesrgan_file_list:
                QMessageBox.warning(self, t("提示"), t("请先添加要处理的图片或视频"))
                return

            options = self._collect_realesrgan_options()
            capability = get_realesrgan_runtime_capabilities(options)
            execution_plan = get_realesrgan_execution_plan(options)
            only_video_files = bool(self.realesrgan_file_list) and all(
                os.path.splitext(path)[1].lower() in {".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm", ".m4v"}
                for path in self.realesrgan_file_list
            )
            selected_video_backend = str(execution_plan.get("selected_video_backend") or "cpu").strip().lower()
            ncnn_video_ready = bool(capability.get("ncnn_video_available")) and only_video_files and selected_video_backend == "ncnn"

            if ncnn_video_ready and str(options.get("device") or "auto").strip().lower() == "cuda":
                self.realesrgan_progress_text.append(
                    t("ℹ️ 当前任务为纯视频修复，已优先切换到自动硬件检测模式，将由官方 NCNN/Vulkan 后端自动选择可用 GPU。")
                )
                options["device"] = "auto"

            if str(options.get("device") or "auto").strip().lower() == "cuda" and not capability.get("cuda_available") and not ncnn_video_ready:
                if capability.get("cuda_detected"):
                    self.realesrgan_progress_text.append(
                        t("⚠️ 已检测到显卡，但当前模型或驱动环境下 CUDA 初始化失败，本次会自动回退到 CPU 继续处理。")
                    )
                else:
                    QMessageBox.warning(
                        self,
                        t("显卡不可用"),
                        t("你选择了“优先显卡(CUDA)”，但当前画质修复运行时没有可用 CUDA。\n\n") + t("请改为“自动选择”或“仅用 CPU”，或者更换为 CUDA 版运行时后再试。")
                    )
                    return
            if not can_run_with_current_or_external_runtime(options):
                QMessageBox.warning(
                    self,
                    t("功能异常"),
                    t("当前未找到可用的画质修复运行时。请联系开发处理运行时集成。")
                )
                return
            missing_deps = get_realesrgan_missing_dependencies(options)

            missing_models = get_realesrgan_missing_download_resources(options, self.realesrgan_file_list.copy())
            if missing_models:
                options["auto_download_models"] = True

            output_dir = self.realesrgan_output_dir.text().strip()
            if not output_dir:
                output_dir = os.path.dirname(self.realesrgan_file_list[0])
            os.makedirs(output_dir, exist_ok=True)
            self.settings.setValue("realesrgan_output_dir", output_dir)

            self.start_realesrgan_btn.setEnabled(False)
            self.cancel_realesrgan_btn.setEnabled(True)
            self.realesrgan_progress_bar.setValue(0)
            self.realesrgan_progress_bar.setFormat("0%")
            self.realesrgan_progress_text.clear()
            self._realesrgan_started_at = time.time()
            self._realesrgan_last_stats = None
            self.realesrgan_speed_hint.setText(t("准备开始处理..."))
            self.realesrgan_progress_text.append(
                t_format("已选择方案：{} | 放大倍率：{} 倍", self.realesrgan_preset_combo.currentText(), int(options['outscale']))
            )
            if options.get("auto_download_models"):
                self.realesrgan_progress_text.append(t("检测到缺少模型，程序将自动下载官方模型；如下载失败，请先开启代理后重试。"))
            normalized_options = execution_plan.get("normalized_options") or {}
            requested_model_name = str(options.get("model_name") or "").strip()
            normalized_model_name = str(normalized_options.get("model_name") or requested_model_name).strip()
            if requested_model_name and normalized_model_name and requested_model_name != normalized_model_name:
                self.realesrgan_progress_text.append(
                    t_format("ℹ️ 当前视频参数下，实际会改用 {} 作为视频后端模型。", normalized_model_name)
                )
            self._current_realesrgan_files = self.realesrgan_file_list.copy()
            self._current_realesrgan_output_dir = output_dir
            self._current_realesrgan_options = options.copy()

            self.realesrgan_process_thread = RealEsrganProcessThread(
                self.realesrgan_file_list.copy(),
                output_dir,
                options,
            )
            self.realesrgan_process_thread.progress_signal.connect(self._on_realesrgan_progress)
            self.realesrgan_process_thread.progress_percent_signal.connect(self._on_realesrgan_progress_percent)
            self.realesrgan_process_thread.stats_signal.connect(self._on_realesrgan_stats)
            self.realesrgan_process_thread.finished_signal.connect(self._on_realesrgan_process_finished)
            self.realesrgan_process_thread.start()
        except Exception as e:
            self.realesrgan_progress_text.append(t_format("❌ 启动失败: {}", e))
            self.start_realesrgan_btn.setEnabled(True)
            self.cancel_realesrgan_btn.setEnabled(False)

    def cancel_realesrgan_process(self):
        """取消 Real-ESRGAN 处理"""
        if self.realesrgan_process_thread and self.realesrgan_process_thread.isRunning():
            self.realesrgan_process_thread.stop()

    def _on_realesrgan_progress(self, message):
        self.realesrgan_progress_text.append(message)
        scrollbar = self.realesrgan_progress_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _format_eta_text(self, seconds_value):
        if seconds_value is None:
            return "预计剩余时间计算中"
        seconds_value = max(int(seconds_value), 0)
        minutes, seconds = divmod(seconds_value, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _on_realesrgan_stats(self, payload_text):
        try:
            payload = json.loads(payload_text or "{}")
        except Exception:
            return
        self._realesrgan_last_stats = payload
        if payload.get("kind") == "video":
            fps_value = float(payload.get("speed_fps") or 0.0)
            eta_seconds = payload.get("eta_seconds")
            processed_frames = int(payload.get("processed_frames") or 0)
            total_frames = int(payload.get("total_frames") or 0)
            if total_frames > 0:
                self.realesrgan_speed_hint.setText(
                    t_format("实时速度：{:.2f} 帧/秒 | 预计剩余：{} | 已处理 {}/{} 帧", fps_value, self._format_eta_text(eta_seconds), processed_frames, total_frames)
                )
            else:
                self.realesrgan_speed_hint.setText(
                    t_format("实时速度：{:.2f} 帧/秒 | 预计剩余：{}", fps_value, self._format_eta_text(eta_seconds))
                )

    def _on_realesrgan_progress_percent(self, percent):
        int_percent = max(0, min(100, int(percent)))
        current_value = self.realesrgan_progress_bar.value()
        if int_percent < current_value:
            int_percent = current_value
        self.realesrgan_progress_bar.setValue(int_percent)
        self.realesrgan_progress_bar.setFormat(f"{int_percent}%")
        started_at = getattr(self, "_realesrgan_started_at", None)
        last_stats = getattr(self, "_realesrgan_last_stats", None)
        if started_at and not last_stats:
            elapsed = max(time.time() - started_at, 1e-6)
            if int_percent > 0:
                eta_seconds = elapsed * max(100 - int_percent, 0) / int_percent
                self.realesrgan_speed_hint.setText(
                    t_format("整体进度：{}% | 预计剩余：{}", int_percent, self._format_eta_text(eta_seconds))
                )

    def _on_realesrgan_process_finished(self, success, message):
        self.start_realesrgan_btn.setEnabled(True)
        self.cancel_realesrgan_btn.setEnabled(False)
        self.realesrgan_progress_text.append(f"\n{'✅' if success else '⚠️'} {message}")
        if success:
            self.realesrgan_progress_bar.setValue(100)
            self.realesrgan_progress_bar.setFormat("100%")
        else:
            self.realesrgan_progress_bar.setFormat(t("处理失败"))
        if success:
            started_at = getattr(self, "_realesrgan_started_at", None)
            elapsed = (time.time() - started_at) if started_at else None
            if elapsed is not None:
                self.realesrgan_speed_hint.setText(t_format("处理完成，总耗时：{}", self._format_eta_text(elapsed)))
            else:
                self.realesrgan_speed_hint.setText(t("处理完成"))
        else:
            self.realesrgan_speed_hint.setText(t("处理失败"))

        try:
            success_count = 0
            fail_count = 0
            import re
            match = re.search(r'成功\s*(\d+)', message)
            if match:
                success_count = int(match.group(1))
            match = re.search(r'失败\s*(\d+)', message)
            if match:
                fail_count = int(match.group(1))

            output_files = []
            if hasattr(self, '_current_realesrgan_files') and hasattr(self, '_current_realesrgan_output_dir') and hasattr(self, '_current_realesrgan_options'):
                output_dir = self._current_realesrgan_output_dir
                options = self._current_realesrgan_options or {}
                suffix = options.get('suffix', t('_画质修复'))
                for input_file in self._current_realesrgan_files[:10]:
                    base_name = os.path.splitext(os.path.basename(input_file))[0]
                    ext = os.path.splitext(input_file)[1].lower() or '.png'
                    output_name = f"{base_name}{suffix}{ext}"
                    output_path = os.path.join(output_dir, output_name)
                    if os.path.exists(output_path):
                        output_files.append(output_path)

            if hasattr(self, '_current_realesrgan_files') and self._current_realesrgan_files:
                self.history_manager.add_quality_repair_record(
                    input_files=self._current_realesrgan_files,
                    output_dir=getattr(self, '_current_realesrgan_output_dir', ''),
                    success_count=success_count,
                    fail_count=fail_count,
                    process_options=getattr(self, '_current_realesrgan_options', None),
                    output_files=output_files,
                )
                self.load_history_table()
        except Exception as e:
            print(t_format("保存画质修复历史记录失败: {}", e))

        if success:
            self.play_completion_notification_sound()
            QMessageBox.information(self, t("画质修复完成"), message)
        else:
            QMessageBox.warning(self, t("画质修复失败"), message)
        self.check_realesrgan_runtime(silent=True)
    
    def _format_file_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def _get_dropped_local_files(self, event):
        files = []
        if not event.mimeData().hasUrls():
            return files
        for url in event.mimeData().urls():
            if url.isLocalFile():
                files.append(url.toLocalFile())
        return files

    def _route_dropped_files(self, file_paths):
        image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tiff', '.tif', '.ico', '.heic', '.heif'}

        image_files = []
        transcode_video = None

        for file_path in file_paths:
            ext = os.path.splitext(file_path)[1].lower()
            if ext in image_exts:
                image_files.append(file_path)
            elif ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.m4v', '.wmv', '.mpg', '.mpeg', '.3gp', '.ts', '.mts', '.m2ts']:
                if transcode_video is None:
                    transcode_video = file_path

        current_tab = self.tabs.tabText(self.tabs.currentIndex()) if hasattr(self, 'tabs') else ""

        if current_tab == "图片处理" and image_files:
            added = 0
            for file_path in image_files:
                if file_path in self.image_file_list:
                    continue
                self.image_file_list.append(file_path)
                filename = os.path.basename(file_path)
                size_str = self._format_file_size(os.path.getsize(file_path))
                self.image_list_widget.addItem(f"{filename} ({size_str})")
                added += 1
            if added:
                self._reset_image_process_state()
                self._update_image_count()
                self.image_progress_text.append(t_format("✅ 已通过拖放添加 {} 张图片", added))
                return True

        if image_files:
            added = 0
            for file_path in image_files:
                if file_path in self.image_file_list:
                    continue
                self.image_file_list.append(file_path)
                filename = os.path.basename(file_path)
                size_str = self._format_file_size(os.path.getsize(file_path))
                self.image_list_widget.addItem(f"{filename} ({size_str})")
                added += 1
            if added:
                self._reset_image_process_state()
                self._update_image_count()
                self.image_progress_text.append(t_format("✅ 已通过拖放添加 {} 张图片", added))
                for i in range(self.tabs.count()):
                    if self.tabs.tabText(i) == t("图片处理"):
                        self.tabs.setCurrentIndex(i)
                        break
                return True

        if transcode_video:
            self.input_file.setText(transcode_video)
            self.update_output_filename(transcode_video)
            self.transcode_progress_text.append(t_format("已通过拖拽设置输入文件: {}", os.path.basename(transcode_video)))
            self.tabs.setCurrentIndex(1)
            return True

        return False
    
    def dragEnterEvent(self, event):
        """拖拽进入事件"""
        file_paths = self._get_dropped_local_files(event)
        if any(os.path.splitext(path)[1].lower() in {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tiff', '.tif', '.ico', '.heic', '.heif'} for path in file_paths):
            event.acceptProposedAction()
            return
        event.ignore()
    
    def dragMoveEvent(self, event):
        """拖拽移动事件"""
        if self._get_dropped_local_files(event):
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def dropEvent(self, event):
        """拖拽放下事件"""
        file_paths = self._get_dropped_local_files(event)
        if file_paths and self._route_dropped_files(file_paths):
            event.acceptProposedAction()
        else:
            event.ignore()


