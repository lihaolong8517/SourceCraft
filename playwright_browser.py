"""
Playwright Browser Module - 简化版
================================
使用子进程方式启动浏览器，解决线程导入问题

Author: Auto-generated
Date: 2025-12-06
"""

import sys
# i18n 兜底导入：本模块可能被子进程用 importlib 加载（sys.path 不含项目根）。
# 依次尝试：直接导入 -> 注入模块所在目录/上级目录后再导入 -> 降级原文（永不崩）。
try:
    from i18n import t, t_format
except ImportError:
    import os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    for _cand in (_here, _os.path.dirname(_here), _os.getcwd()):
        if _cand and _cand not in sys.path:
            sys.path.insert(0, _cand)
    try:
        from i18n import t, t_format
    except ImportError:
        def t(text):
            return text

        def t_format(text, *args, **kwargs):
            try:
                return text.format(*args, **kwargs)
            except Exception:
                return text
import os
import json
import re
import subprocess
import threading
import time
import traceback
import math
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import parse_qsl, urlsplit

# VDH 式媒体捕获层（可选依赖：包不可用时自动退化为旧嗅探）
try:
    import vdh_capture as _VDH_CAPTURE
except ImportError:
    _vdh_root = os.path.dirname(os.path.abspath(__file__))
    if _vdh_root and _vdh_root not in sys.path:
        sys.path.insert(0, _vdh_root)
    try:
        import vdh_capture as _VDH_CAPTURE
    except ImportError:
        _VDH_CAPTURE = None

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLineEdit, QLabel, QFrame, QMessageBox,
    QApplication, QToolButton, QComboBox, QGraphicsDropShadowEffect, QCheckBox, QColorDialog, QMenu,
    QButtonGroup, QDialog, QSplitter, QScrollArea, QProgressBar
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QSize, QUrl, QRect, QPoint
from PyQt5.QtGui import QCursor, QColor, QFont, QPainter, QPainterPath, QConicalGradient, QPixmap, QIcon, QLinearGradient, QRadialGradient, QPolygon
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

# QtMultimedia 用于卡片内联预览播放（VDH 的 com-media-preview 等价物）。
# 打包环境缺少多媒体组件时降级：只在卡片里显示封面，不提供内联播放。
try:
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
    from PyQt5.QtMultimediaWidgets import QVideoWidget

    _HAS_QT_MULTIMEDIA = True
except Exception:  # pragma: no cover - 取决于运行环境
    QMediaContent = None
    QMediaPlayer = None
    QVideoWidget = None
    _HAS_QT_MULTIMEDIA = False

DEFAULT_QUICK_SITES = [
    {"name": "YouTube", "url": "youtube.com", "bg_color": "#ff7f7f", "hover_color": "#ff6b6b", "press_color": "#e85555"},
    {"name": "Bilibili", "url": "bilibili.com", "bg_color": "#ff9eb5", "hover_color": "#fb7299", "press_color": "#e5658a"},
    {"name": "TikTok", "url": "tiktok.com", "bg_color": "#69c9d0", "hover_color": "#4ecdc4", "press_color": "#3dbab1"},
    {"name": "Twitter", "url": "twitter.com", "bg_color": "#5bc0eb", "hover_color": "#1da1f2", "press_color": "#0d8ddb"},
    {"name": "Instagram", "url": "instagram.com", "bg_color": "#f08080", "hover_color": "#e1306c", "press_color": "#c13584"},
]


def _bootstrap_local_import_paths():
    """
    确保通过 importlib 动态加载本模块时，也能导入同目录依赖（如 auth_utils）。
    """
    candidates = []
    frozen_mode = bool(getattr(sys, "frozen", False))
    try:
        module_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(module_dir)
        candidates.append(os.path.dirname(module_dir))
        # 仅在打包态追加 _internal，避免源码态外部 Python 误载不兼容二进制模块。
        if frozen_mode:
            candidates.append(os.path.join(module_dir, "_internal"))
    except Exception:
        pass

    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(exe_dir)
        if frozen_mode:
            candidates.append(os.path.join(exe_dir, "_internal"))
    except Exception:
        pass

    try:
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(meipass)
            if frozen_mode:
                candidates.append(os.path.join(meipass, "_internal"))
    except Exception:
        pass

    for path in candidates:
        norm = str(path or "").strip()
        if not norm:
            continue
        if not os.path.isdir(norm):
            continue
        if norm not in sys.path:
            sys.path.insert(0, norm)


_bootstrap_local_import_paths()

import auth_utils

# VDH 风格内联预览：抓"中间一小段"分片并 remux 成小 mp4（见 docs/VDH-UI-预览规格.md）
try:
    from vdh_capture.preview import PreviewCache, PreviewSpec, extract_preview, resolve_ffmpeg, default_cache_dir

    _HAS_VDH_PREVIEW = True
except Exception:  # pragma: no cover - 打包环境兜底
    PreviewCache = None
    PreviewSpec = None
    extract_preview = None
    resolve_ffmpeg = None
    default_cache_dir = None
    _HAS_VDH_PREVIEW = False

_PREVIEW_CACHE = None


def _preview_cache():
    """惰性创建全局预览缓存（临时目录，按 URL+起点复用）。"""
    global _PREVIEW_CACHE
    if not _HAS_VDH_PREVIEW:
        return None
    if _PREVIEW_CACHE is None:
        try:
            _PREVIEW_CACHE = PreviewCache(default_cache_dir())
        except Exception:
            _PREVIEW_CACHE = None
    return _PREVIEW_CACHE

def _get_browser_data_dir():
    """获取浏览器数据目录（确保可写）"""
    if getattr(sys, 'frozen', False):
        base = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'SourceCraft')
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base, "playwright_data")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
BROWSER_DATA_DIR = _get_browser_data_dir()
SETTINGS_FILE = os.path.join(BROWSER_DATA_DIR, "browser_settings.json")

# 媒体去重时需要保留的变体参数（语言/轨道/格式等），避免不同变体被误判为同一链接
_MEDIA_VARIANT_QUERY_KEYS = {
    "lang", "language", "locale", "tlang",
    "sub", "subs", "subtitle", "subtitles", "caption",
    "audio", "audio_lang", "audio_language", "track",
    "itag", "fmt", "format", "quality", "res", "resolution",
    "codec", "vcodec", "acodec", "abr", "br", "bitrate",
    "id", "rendition", "bandwidth", "variant", "profile",
    "v",  # YouTube watch?v= 视频ID（页面 URL 兜底去重用）
}

# 明确判定为易变签名/鉴权参数，不参与去重键
_MEDIA_VOLATILE_QUERY_KEYS = {
    "token", "sig", "signature", "expires", "expiry", "exp", "e",
    "auth", "authorization", "policy", "nonce", "ts", "timestamp",
    "x-amz-signature", "x-amz-credential", "x-amz-security-token", "x-amz-date",
    "x-goog-signature", "x-goog-credential", "x-goog-date",
    "hdnts", "hmac", "session", "sessionid",
}


def _is_media_variant_query_key(key: Any) -> bool:
    norm_key = str(key or "").strip().lower()
    if not norm_key:
        return False
    if norm_key in _MEDIA_VARIANT_QUERY_KEYS:
        return True
    if norm_key in _MEDIA_VOLATILE_QUERY_KEYS:
        return False

    # 兼容站点非标准命名（如 audioTrack / subtitleLang / textTrack）
    if "language" in norm_key or norm_key.startswith("lang") or norm_key.endswith("lang"):
        return True
    if "audio" in norm_key:
        return True
    if "subtitle" in norm_key or "caption" in norm_key:
        return True
    if any(token in norm_key for token in ("itag", "format", "quality", "codec", "resolution", "bitrate", "bandwidth", "rendition", "variant", "profile")):
        return True
    return False


def _normalize_capture_url_key(url: Any) -> str:
    """标准化媒体 URL 去重键。

    规则:
    - 规范协议与大小写
    - 去掉 fragment
    - 保留与媒体变体相关的 query 参数，丢弃 token/签名等易变参数
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw.lower()

    base = parsed._replace(query="", fragment="").geturl().rstrip("/").lower()
    query = parsed.query or ""
    if not query:
        return base

    kept_items = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        norm_key = str(key or "").strip().lower()
        if _is_media_variant_query_key(norm_key):
            norm_value = str(value or "").strip().lower()
            if norm_value:
                kept_items.append((norm_key, norm_value))

    if not kept_items:
        return base

    # 统一排序以稳定去重键
    kept_items = sorted(set(kept_items))
    normalized_query = "&".join(f"{k}={v}" for k, v in kept_items)
    return f"{base}?{normalized_query}"

def _get_profile_dir_for_launch(selected_browser: str, executable_path: str = ""):
    """为不同浏览器内核分配隔离的用户数据目录，避免配置互相污染。"""
    exe_norm = (executable_path or "").replace("/", "\\").lower()
    if "playwright_browsers" in exe_norm or "chrome-win\\chrome.exe" in exe_norm:
        profile_key = "bundled_chromium"
    else:
        profile_key = (selected_browser or "browser").lower()
    profile_dir = os.path.join(BROWSER_DATA_DIR, "profiles", profile_key)
    os.makedirs(profile_dir, exist_ok=True)
    return profile_dir


def get_system_proxy():
    """
    获取 Windows 系统代理设置
    
    返回:
        str: 代理服务器地址（如 "http://127.0.0.1:7890"），如果未启用代理则返回 None
    """
    if sys.platform != 'win32':
        # 非 Windows 系统，尝试从环境变量获取
        return os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy') or \
               os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    
    try:
        import winreg
        
        # 读取 Windows Internet 设置中的代理配置
        internet_settings = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        )
        
        try:
            # 检查代理是否启用
            proxy_enable, _ = winreg.QueryValueEx(internet_settings, 'ProxyEnable')
            
            if proxy_enable:
                # 获取代理服务器地址
                proxy_server, _ = winreg.QueryValueEx(internet_settings, 'ProxyServer')
                
                if proxy_server:
                    # 处理代理地址格式
                    # 可能的格式: "127.0.0.1:7890" 或 "http=127.0.0.1:7890;https=127.0.0.1:7890"
                    if '=' in proxy_server:
                        # 多协议格式，提取 http 或 https 的代理
                        for part in proxy_server.split(';'):
                            if '=' in part:
                                protocol, addr = part.split('=', 1)
                                if protocol.lower() in ('http', 'https'):
                                    if not addr.startswith('http'):
                                        addr = f'http://{addr}'
                                    print(f"[系统代理] 检测到代理: {addr}")
                                    return addr
                    else:
                        # 简单格式: "127.0.0.1:7890"
                        if not proxy_server.startswith('http'):
                            proxy_server = f'http://{proxy_server}'
                        print(f"[系统代理] 检测到代理: {proxy_server}")
                        return proxy_server
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(internet_settings)
            
    except Exception as e:
        print(f"[系统代理] 读取系统代理设置失败: {e}")
    
    # 未检测到系统代理，检查环境变量
    env_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy') or \
                os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    if env_proxy:
        print(f"[系统代理] 从环境变量检测到代理: {env_proxy}")
        return env_proxy
    
    print("[系统代理] 未检测到系统代理")
    return None


def _find_python_with_playwright():
    """找到安装了 playwright 的 Python"""
    # 如果是打包后的环境，sys.executable 是 exe 文件，不能直接运行 -c
    is_frozen = getattr(sys, 'frozen', False)
    
    # 候选 Python 路径列表
    candidates = []
    if not is_frozen:
        candidates.append(sys.executable)
        
    candidates.extend([
        os.path.expandvars(r"%USERPROFILE%\miniconda3\python.exe"),
        os.path.expandvars(r"%USERPROFILE%\anaconda3\python.exe"),
        r"C:\Users\Administrator\miniconda3\python.exe",
        r"C:\Python310\python.exe",
        r"C:\Python39\python.exe",
        "python",
        "python3",
    ])
    
    for python_path in candidates:
        try:
            # 检查路径是否存在
            if not os.path.exists(python_path) and python_path not in ["python", "python3"]:
                continue
                
            import subprocess
            result = subprocess.run(
                [python_path, "-c", "from playwright.async_api import async_playwright; print('OK')"],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            if result.returncode == 0 and "OK" in result.stdout:
                print(f"[Playwright] 使用 Python: {python_path}")
                return python_path
        except:
            continue
    
    # 默认使用当前 Python（如果是 frozen，这可能无法运行 playwright，但至少不会导致死循环）
    return sys.executable


# 缓存找到的 Python 路径
_CACHED_PYTHON_WITH_PLAYWRIGHT = None

def get_python_with_playwright():
    """获取安装了 playwright 的 Python 路径（带缓存）"""
    global _CACHED_PYTHON_WITH_PLAYWRIGHT
    
    # 【关键修复】如果是在打包环境，直接返回自身 exe
    if getattr(sys, 'frozen', False):
        _CACHED_PYTHON_WITH_PLAYWRIGHT = sys.executable
        return sys.executable
        
    if _CACHED_PYTHON_WITH_PLAYWRIGHT is None:
        _CACHED_PYTHON_WITH_PLAYWRIGHT = _find_python_with_playwright()
    return _CACHED_PYTHON_WITH_PLAYWRIGHT


# ============ 浏览器检测函数 ============

KNOWN_BROWSER_SPECS = [
    {
        "id": "edge",
        "name": "Microsoft Edge",
        "exe_names": ["msedge.exe"],
        "engine": "chromium",
        "paths": [
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe",
            r"%USERPROFILE%\AppData\Local\Microsoft\Edge\Application\msedge.exe",
        ],
    },
    {
        "id": "chrome",
        "name": "Google Chrome",
        "exe_names": ["chrome.exe"],
        "engine": "chromium",
        "paths": [
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
            r"%USERPROFILE%\AppData\Local\Google\Chrome\Application\chrome.exe",
            r"%ProgramData%\Google\Chrome\Application\chrome.exe",
        ],
    },
    {
        "id": "brave",
        "name": "Brave",
        "exe_names": ["brave.exe"],
        "engine": "chromium",
        "paths": [
            r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
    },
    {
        "id": "vivaldi",
        "name": "Vivaldi",
        "exe_names": ["vivaldi.exe"],
        "engine": "chromium",
        "paths": [
            r"%ProgramFiles%\Vivaldi\Application\vivaldi.exe",
            r"%ProgramFiles(x86)%\Vivaldi\Application\vivaldi.exe",
            r"%LocalAppData%\Vivaldi\Application\vivaldi.exe",
        ],
    },
    {
        "id": "opera",
        "name": "Opera",
        "exe_names": ["launcher.exe", "opera.exe"],
        "engine": "chromium",
        "paths": [
            r"%LocalAppData%\Programs\Opera\launcher.exe",
            r"%AppData%\Opera Software\Opera Stable\launcher.exe",
            r"%ProgramFiles%\Opera\launcher.exe",
            r"%ProgramFiles(x86)%\Opera\launcher.exe",
        ],
    },
    {
        "id": "opera_gx",
        "name": "Opera GX",
        "exe_names": ["launcher.exe"],
        "engine": "chromium",
        "paths": [
            r"%LocalAppData%\Programs\Opera GX\launcher.exe",
            r"%AppData%\Opera Software\Opera GX Stable\launcher.exe",
        ],
    },
    {
        "id": "firefox",
        "name": "Mozilla Firefox",
        "exe_names": ["firefox.exe"],
        "engine": "firefox",
        "paths": [
            r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
            r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
            r"%LocalAppData%\Mozilla Firefox\firefox.exe",
        ],
    },
    {
        "id": "waterfox",
        "name": "Waterfox",
        "exe_names": ["waterfox.exe"],
        "engine": "firefox",
        "paths": [
            r"%ProgramFiles%\Waterfox\waterfox.exe",
            r"%ProgramFiles(x86)%\Waterfox\waterfox.exe",
            r"%LocalAppData%\Waterfox\waterfox.exe",
        ],
    },
    {
        "id": "librewolf",
        "name": "LibreWolf",
        "exe_names": ["librewolf.exe"],
        "engine": "firefox",
        "paths": [
            r"%ProgramFiles%\LibreWolf\librewolf.exe",
            r"%ProgramFiles(x86)%\LibreWolf\librewolf.exe",
            r"%LocalAppData%\Programs\LibreWolf\librewolf.exe",
        ],
    },
    {
        "id": "arc",
        "name": "Arc",
        "exe_names": ["arc.exe"],
        "engine": "chromium",
        "paths": [
            r"%LocalAppData%\Programs\Arc\Arc.exe",
        ],
    },
    {
        "id": "chromium_system",
        "name": "Chromium",
        "exe_names": ["chromium.exe"],
        "engine": "chromium",
        "paths": [
            r"%ProgramFiles%\Chromium\Application\chromium.exe",
            r"%ProgramFiles(x86)%\Chromium\Application\chromium.exe",
            r"%LocalAppData%\Chromium\Application\chromium.exe",
        ],
    },
]

KNOWN_BROWSER_BY_EXE = {}
for _spec in KNOWN_BROWSER_SPECS:
    for _exe in _spec.get("exe_names", []):
        exe_key = str(_exe or "").strip().lower()
        if exe_key and exe_key not in KNOWN_BROWSER_BY_EXE:
            KNOWN_BROWSER_BY_EXE[exe_key] = _spec


def _expand_browser_paths(raw_paths: List[str]) -> List[str]:
    return [os.path.expandvars(str(path or "").strip()) for path in (raw_paths or []) if str(path or "").strip()]


def _extract_executable_from_command(command: str) -> str:
    text = str(command or "").strip()
    if not text:
        return ""

    quoted = re.match(r'^\s*"([^"]+\.exe)"', text, flags=re.IGNORECASE)
    if quoted:
        return quoted.group(1)

    plain = re.match(r'^\s*([^\s]+\.exe)', text, flags=re.IGNORECASE)
    if plain:
        return plain.group(1)

    return ""


def _normalize_browser_id(name: str, executable_path: str = "") -> str:
    exe_name = os.path.basename(str(executable_path or "")).strip().lower()
    if exe_name.endswith(".exe"):
        exe_name = exe_name[:-4]
    if exe_name:
        safe_exe = re.sub(r"[^a-z0-9]+", "_", exe_name).strip("_")
        if safe_exe:
            return safe_exe

    safe_name = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return safe_name or "browser"


def _infer_browser_engine(executable_path: str) -> str:
    exe_name = os.path.basename(str(executable_path or "")).strip().lower()
    if exe_name in {"firefox.exe", "waterfox.exe", "librewolf.exe"}:
        return "firefox"
    return "chromium"


def _normalize_browser_display_name(name: str, executable_path: str = "") -> str:
    exe_name = os.path.basename(str(executable_path or "")).strip().lower()
    known = KNOWN_BROWSER_BY_EXE.get(exe_name)
    if known:
        return known["name"]

    clean_name = str(name or "").strip()
    if clean_name:
        return clean_name

    if exe_name.endswith(".exe"):
        exe_name = exe_name[:-4]
    return exe_name.title() or t("浏览器")


def _build_browser_info(name: str, executable_path: str, browser_id: str = "", engine: str = "", is_default: bool = False) -> Optional[Dict[str, Any]]:
    resolved_path = str(executable_path or "").strip()
    if not resolved_path or not os.path.exists(resolved_path):
        return None

    display_name = _normalize_browser_display_name(name, resolved_path)
    inferred_engine = str(engine or "").strip().lower() or _infer_browser_engine(resolved_path)
    normalized_id = str(browser_id or "").strip().lower() or _normalize_browser_id(display_name, resolved_path)

    return {
        "name": display_name,
        "id": normalized_id,
        "path": resolved_path,
        "engine": inferred_engine,
        "is_default": bool(is_default),
    }


def _find_known_browser_path(spec: Dict[str, Any]) -> str:
    for raw_path in _expand_browser_paths(spec.get("paths", [])):
        if os.path.exists(raw_path):
            return raw_path

    for exe_name in spec.get("exe_names", []):
        registry_path = _find_browser_from_registry(str(exe_name or "").strip())
        if registry_path:
            return registry_path

    for exe_name in spec.get("exe_names", []):
        path_hit = _find_browser_in_path(str(exe_name or "").strip())
        if path_hit:
            return path_hit

    return ""


def _get_default_browser_info_windows() -> Tuple[str, str]:
    if sys.platform != "win32":
        return "", ""

    try:
        import winreg

        user_choice_paths = [
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        ]
        prog_id = ""
        for subkey in user_choice_paths:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                    prog_id, _ = winreg.QueryValueEx(key, "ProgId")
                    if prog_id:
                        break
            except (FileNotFoundError, OSError):
                continue

        if not prog_id:
            return "", ""

        command = ""
        for hkey in (winreg.HKEY_CLASSES_ROOT, winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hkey, fr"{prog_id}\shell\open\command") as key:
                    command, _ = winreg.QueryValueEx(key, "")
                    if command:
                        break
            except (FileNotFoundError, OSError):
                continue

        executable = _extract_executable_from_command(command)
        if not executable or not os.path.exists(executable):
            return "", ""

        display_name = ""
        for hkey in (winreg.HKEY_CLASSES_ROOT, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hkey, fr"{prog_id}\Application") as key:
                    display_name, _ = winreg.QueryValueEx(key, "ApplicationName")
                    if display_name:
                        break
            except (FileNotFoundError, OSError):
                continue

        return display_name, executable
    except Exception as exc:
        print(f"[浏览器检测] 读取默认浏览器失败: {exc}")
        return "", ""


def _iter_registered_browsers_windows() -> List[Tuple[str, str]]:
    results: List[Tuple[str, str]] = []
    if sys.platform != "win32":
        return results

    try:
        import winreg

        registry_roots = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Clients\StartMenuInternet"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Clients\StartMenuInternet"),
        )

        for hkey, subkey in registry_roots:
            try:
                with winreg.OpenKey(hkey, subkey) as parent:
                    index = 0
                    while True:
                        try:
                            child_name = winreg.EnumKey(parent, index)
                            index += 1
                        except OSError:
                            break

                        display_name = child_name
                        try:
                            with winreg.OpenKey(parent, child_name) as child:
                                value, _ = winreg.QueryValueEx(child, "")
                                if value:
                                    display_name = str(value).strip() or display_name
                        except (FileNotFoundError, OSError):
                            pass

                        try:
                            with winreg.OpenKey(parent, fr"{child_name}\shell\open\command") as cmd_key:
                                command, _ = winreg.QueryValueEx(cmd_key, "")
                        except (FileNotFoundError, OSError):
                            continue

                        executable = _extract_executable_from_command(command)
                        if executable and os.path.exists(executable):
                            results.append((display_name, executable))
            except (FileNotFoundError, OSError):
                continue
    except Exception as exc:
        print(f"[浏览器检测] 枚举已注册浏览器失败: {exc}")

    return results

def _find_browser_from_registry(browser_name):
    """从Windows注册表查找浏览器安装路径
    
    Args:
        browser_name: 浏览器可执行文件名，如 'chrome.exe'
    
    Returns:
        浏览器可执行文件路径，未找到返回 None
    """
    if sys.platform != 'win32':
        return None
    
    try:
        import winreg
        exe_name = str(browser_name or "").strip()
        if not exe_name:
            return None

        registry_paths = [
            (winreg.HKEY_LOCAL_MACHINE, fr"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"),
            (winreg.HKEY_CURRENT_USER, fr"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"),
            (winreg.HKEY_LOCAL_MACHINE, fr"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"),
        ]

        for hkey, subkey in registry_paths:
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    # 尝试读取默认值（通常是可执行文件路径）
                    try:
                        value, _ = winreg.QueryValueEx(key, "")
                        if value and os.path.exists(value):
                            print(f"[浏览器检测] 从注册表找到 {browser_name}: {value}")
                            return value
                    except FileNotFoundError:
                        pass
                    
                    # 尝试读取 Path 值
                    try:
                        value, _ = winreg.QueryValueEx(key, "Path")
                        if value:
                            full_path = os.path.join(value, exe_name)
                            if os.path.exists(full_path):
                                print(f"[浏览器检测] 从注册表Path找到 {exe_name}: {full_path}")
                                return full_path
                    except FileNotFoundError:
                        pass
            except (FileNotFoundError, PermissionError, OSError):
                continue
                
    except ImportError:
        print("[浏览器检测] 无法导入 winreg 模块")
    except Exception as e:
        print(f"[浏览器检测] 注册表查询异常: {e}")
    
    return None


def _find_browser_in_path(exe_name):
    """在PATH环境变量中查找浏览器
    
    Args:
        exe_name: 可执行文件名，如 'chrome.exe'
    
    Returns:
        完整路径，未找到返回 None
    """
    import shutil
    path = shutil.which(exe_name)
    if path:
        print(f"[浏览器检测] 从PATH找到: {path}")
    return path


def detect_installed_browsers():
    """自动检测系统已安装并可直接启动的浏览器。"""
    browsers: List[Dict[str, Any]] = []
    path_index: Dict[str, Dict[str, Any]] = {}

    def _register(browser_info: Optional[Dict[str, Any]]) -> None:
        if not browser_info:
            return

        path_key = os.path.normcase(os.path.abspath(browser_info["path"]))
        existing = path_index.get(path_key)
        if existing:
            known_ids = {spec["id"] for spec in KNOWN_BROWSER_SPECS}
            if browser_info.get("is_default"):
                existing["is_default"] = True
            if browser_info.get("id") and (existing["id"] not in known_ids or browser_info["id"] in known_ids):
                existing["id"] = browser_info["id"]
            if (
                browser_info.get("name")
                and (
                    existing["name"] == os.path.basename(existing["path"])
                    or browser_info["id"] in known_ids
                )
            ):
                existing["name"] = browser_info["name"]
            if browser_info.get("engine"):
                existing["engine"] = browser_info["engine"]
            return

        candidate_id = browser_info["id"]
        seen_ids = {item["id"] for item in browsers}
        if candidate_id in seen_ids:
            base_id = candidate_id
            suffix = 2
            while f"{base_id}_{suffix}" in seen_ids:
                suffix += 1
            browser_info["id"] = f"{base_id}_{suffix}"

        browsers.append(browser_info)
        path_index[path_key] = browser_info

    default_name, default_path = _get_default_browser_info_windows()
    if default_path:
        _register(_build_browser_info(default_name, default_path, is_default=True))

    for spec in KNOWN_BROWSER_SPECS:
        found_path = _find_known_browser_path(spec)
        if found_path:
            _register(
                _build_browser_info(
                    spec["name"],
                    found_path,
                    browser_id=spec["id"],
                    engine=spec["engine"],
                    is_default=(default_path and os.path.normcase(default_path) == os.path.normcase(found_path)),
                )
            )

    for display_name, executable_path in _iter_registered_browsers_windows():
        _register(
            _build_browser_info(
                display_name,
                executable_path,
                is_default=(default_path and os.path.normcase(default_path) == os.path.normcase(executable_path)),
            )
        )

    priority = {spec["id"]: index for index, spec in enumerate(KNOWN_BROWSER_SPECS)}
    browsers.sort(
        key=lambda item: (
            0 if item.get("is_default") else 1,
            priority.get(item["id"], 999),
            item["name"].lower(),
        )
    )

    if browsers:
        detected_names = [item["name"] for item in browsers]
        print(f"[浏览器检测] 检测到: {', '.join(detected_names)}")
    else:
        print("[浏览器检测] 警告: 未检测到可用浏览器")

    return browsers


# ============ 极简美观浏览器选择器 ============

BROWSER_THEME_PRESETS = {
    "chrome": {
        "letter": "G",
        "icon_bg": "qradialgradient(cx:0.3, cy:0.3, radius:1, fx:0.3, fy:0.3, stop:0 #ffb088, stop:1 #f0896a)",
        "selected_bg": """
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(255, 182, 140, 0.35),
                stop:0.3 rgba(255, 210, 160, 0.3),
                stop:0.6 rgba(255, 235, 180, 0.25),
                stop:1 rgba(255, 250, 240, 0.2));
            border: 2.5px solid qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #ffb088, stop:0.5 #ffd699, stop:1 #ffe4b5);
        """,
        "text_color": "#d97a50",
    },
    "edge": {
        "letter": "E",
        "icon_bg": "qradialgradient(cx:0.3, cy:0.3, radius:1, fx:0.3, fy:0.3, stop:0 #f7b4c4, stop:1 #e899aa)",
        "selected_bg": """
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(247, 180, 196, 0.35),
                stop:0.3 rgba(255, 200, 210, 0.3),
                stop:0.6 rgba(255, 220, 225, 0.25),
                stop:1 rgba(255, 245, 248, 0.2));
            border: 2.5px solid qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #f7b4c4, stop:0.5 #ffb088, stop:1 #ffd699);
        """,
        "text_color": "#c77a8a",
    },
    "firefox": {
        "letter": "F",
        "icon_bg": "qradialgradient(cx:0.3, cy:0.3, radius:1, fx:0.3, fy:0.3, stop:0 #ffb37d, stop:1 #f06d5c)",
        "selected_bg": """
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(255, 179, 125, 0.35),
                stop:0.35 rgba(255, 210, 170, 0.3),
                stop:0.7 rgba(255, 232, 210, 0.24),
                stop:1 rgba(255, 246, 238, 0.2));
            border: 2.5px solid qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #ff9a76, stop:0.55 #ffc58f, stop:1 #ffd8b6);
        """,
        "text_color": "#db7755",
    },
}

BROWSER_THEME_FALLBACKS = [
    {
        "icon_bg": "qradialgradient(cx:0.3, cy:0.3, radius:1, fx:0.3, fy:0.3, stop:0 #ffcf9f, stop:1 #f1ad7a)",
        "selected_bg": """
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(255, 207, 159, 0.35),
                stop:0.35 rgba(255, 227, 186, 0.3),
                stop:0.7 rgba(255, 244, 221, 0.24),
                stop:1 rgba(255, 250, 242, 0.2));
            border: 2.5px solid qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #ffbf86, stop:0.55 #ffd9a5, stop:1 #ffe8c5);
        """,
        "text_color": "#c8874d",
    },
    {
        "icon_bg": "qradialgradient(cx:0.3, cy:0.3, radius:1, fx:0.3, fy:0.3, stop:0 #f7b7c9, stop:1 #de8ea6)",
        "selected_bg": """
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(247, 183, 201, 0.35),
                stop:0.35 rgba(255, 210, 220, 0.3),
                stop:0.7 rgba(255, 235, 239, 0.24),
                stop:1 rgba(255, 247, 249, 0.2));
            border: 2.5px solid qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #eba1b6, stop:0.55 #f8c4d0, stop:1 #ffdbe3);
        """,
        "text_color": "#b96c84",
    },
    {
        "icon_bg": "qradialgradient(cx:0.3, cy:0.3, radius:1, fx:0.3, fy:0.3, stop:0 #9fc6ff, stop:1 #6d96ef)",
        "selected_bg": """
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(159, 198, 255, 0.34),
                stop:0.35 rgba(191, 218, 255, 0.28),
                stop:0.7 rgba(222, 236, 255, 0.22),
                stop:1 rgba(244, 249, 255, 0.2));
            border: 2.5px solid qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #83aeff, stop:0.55 #aac9ff, stop:1 #d7e5ff);
        """,
        "text_color": "#5c7ec7",
    },
]


def _get_browser_theme(browser_id: str, browser_name: str = "") -> Dict[str, str]:
    theme = BROWSER_THEME_PRESETS.get(str(browser_id or "").strip().lower())
    if theme:
        return theme

    seed = sum(ord(ch) for ch in f"{browser_id}|{browser_name}")
    fallback = dict(BROWSER_THEME_FALLBACKS[seed % len(BROWSER_THEME_FALLBACKS)])
    letter_source = str(browser_name or browser_id or "B").strip()
    fallback["letter"] = letter_source[:1].upper() if letter_source else "B"
    return fallback

class BrowserCard(QPushButton):
    """极简美观的浏览器选择卡片"""
    
    def __init__(self, browser_info: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.browser_info = browser_info
        self.browser_id = browser_info['id']
        self.browser_name = browser_info['name']
        self._selected = False
        self._hovered = False
        
        self.setCheckable(True)
        self.setFixedSize(140, 90)
        self.setCursor(Qt.PointingHandCursor)
        
        self._setup_ui()
        self._apply_style()
        
    def _setup_ui(self):
        """设置卡片布局"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)
        
        # 浏览器图标 - 极简圆形色块设计
        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setFixedSize(38, 38)
        
        theme = _get_browser_theme(self.browser_id, self.browser_name)
        self.icon_label.setText(theme['letter'])
        self.icon_label.setStyleSheet(f"""
            QLabel {{
                background: {theme['icon_bg']};
                color: white;
                font-size: 18px;
                font-weight: 600;
                font-family: 'Segoe UI', 'SF Pro Display', sans-serif;
                border-radius: 19px;
                border: none;
            }}
        """)
        layout.addWidget(self.icon_label, 0, Qt.AlignCenter)
        
        # 浏览器名称
        self.name_label = QLabel(self._get_short_name())
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setStyleSheet("""
            font-size: 12px; 
            font-weight: 500;
            color: #666;
            background: transparent;
            border: none;
        """)
        layout.addWidget(self.name_label, 0, Qt.AlignCenter)
    
    def _get_short_name(self):
        """获取简短的浏览器名称"""
        name_map = {
            'chrome': 'Chrome',
            'edge': 'Edge',
            'firefox': 'Firefox',
        }
        return name_map.get(self.browser_id, self.browser_name)
    
    def _apply_style(self):
        """应用卡片样式"""
        self._update_style()
        
    def _update_style(self):
        """更新样式状态"""
        if self._selected:
            theme = _get_browser_theme(self.browser_id, self.browser_name)
            self.setStyleSheet(f"""
                BrowserCard {{
                    {theme['selected_bg']}
                    border-radius: 16px;
                }}
            """)
            
            self.name_label.setStyleSheet(f"""
                font-size: 12px; 
                font-weight: 600;
                color: {theme['text_color']};
                background: transparent;
                border: none;
            """)
        elif self._hovered:
            self.setStyleSheet("""
                BrowserCard {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #fffaf5, stop:1 #fff5ee);
                    border: 1.5px solid rgba(255, 200, 180, 0.5);
                    border-radius: 16px;
                }
            """)
            self.name_label.setStyleSheet("""
                font-size: 12px; 
                font-weight: 500;
                color: #555;
                background: transparent;
                border: none;
            """)
        else:
            self.setStyleSheet("""
                BrowserCard {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #ffffff, stop:1 #fafafa);
                    border: 1.5px solid rgba(230, 230, 230, 0.8);
                    border-radius: 16px;
                }
            """)
            self.name_label.setStyleSheet("""
                font-size: 12px; 
                font-weight: 500;
                color: #666;
                background: transparent;
                border: none;
            """)
    
    def setSelected(self, selected: bool):
        """设置选中状态"""
        self._selected = selected
        self.setChecked(selected)
        self._update_style()
    
    def isSelected(self) -> bool:
        return self._selected
    
    def enterEvent(self, event):
        self._hovered = True
        if not self._selected:
            self._update_style()
        super().enterEvent(event)
    
    def leaveEvent(self, event):
        self._hovered = False
        self._update_style()
        super().leaveEvent(event)


class FloatingBubble(QWidget):
    """带悬浮动效与伪 3D 光感的悬浮气泡窗口"""
    
    clicked = pyqtSignal()
    position_changed = pyqtSignal(int, int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | 
            Qt.WindowStaysOnTopHint | 
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        
        self._size = 72
        self.setFixedSize(self._size, self._size)
        
        # 拖动相关
        self._dragging = False
        self._drag_pos = None
        self._press_pos = None
        
        # 动画相关
        self._pulse_value = 0
        self._float_phase = 0.0
        self._hover_strength = 0.0
        self._appear_progress = 0.0
        self._tilt_x = 0.0
        self._tilt_y = 0.0
        self._spin_phase = 0.0
        self._click_impulse = 0.0
        self._press_depth = 0.0
        self._dock_margin = 14
        self._visual_mode = "idle"
        self._animations_enabled = True
        self._lock_position = False
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._update_pulse)
        self._pulse_timer.start(33)

        self._snap_animation = QPropertyAnimation(self, b"pos", self)
        self._snap_animation.setDuration(280)
        self._snap_animation.setEasingCurve(QEasingCurve.OutBack)
        self._snap_animation.finished.connect(self._emit_position_changed)
        
        self.setCursor(Qt.PointingHandCursor)
        
        # 预缓存颜色
        self._rainbow_colors = [
            QColor(255, 114, 120, 235),
            QColor(255, 176, 92, 235),
            QColor(255, 232, 112, 230),
            QColor(119, 232, 180, 228),
            QColor(106, 198, 255, 232),
            QColor(125, 150, 255, 235),
            QColor(247, 132, 208, 235),
        ]
        self._shadow_colors = [
            QColor(54, 42, 94, 84),
            QColor(42, 90, 150, 70),
        ]
        self._highlight_color = QColor(255, 255, 255, 190)
        self._dot_color = QColor(255, 255, 255, 236)
        self._inner_colors = [
            QColor(255, 255, 255, 78),
            QColor(255, 255, 255, 24),
        ]
        self._rim_light = QColor(255, 255, 255, 88)
        self._core_glow = QColor(255, 255, 255, 102)
        self._text_color = QColor(82, 78, 132, 240)
        self._mode_palettes = {
            "idle": [
                QColor(255, 114, 120, 235),
                QColor(255, 176, 92, 235),
                QColor(255, 232, 112, 230),
                QColor(119, 232, 180, 228),
                QColor(106, 198, 255, 232),
                QColor(125, 150, 255, 235),
                QColor(247, 132, 208, 235),
            ],
            "starting": [
                QColor(255, 188, 92, 238),
                QColor(255, 216, 115, 236),
                QColor(255, 240, 156, 232),
                QColor(255, 196, 108, 232),
                QColor(255, 167, 92, 236),
                QColor(255, 197, 122, 236),
                QColor(255, 221, 146, 236),
            ],
            "running": [
                QColor(88, 212, 170, 236),
                QColor(92, 226, 194, 236),
                QColor(122, 232, 206, 232),
                QColor(108, 214, 255, 232),
                QColor(104, 178, 255, 236),
                QColor(125, 160, 255, 236),
                QColor(158, 206, 255, 236),
            ],
            "capture": [
                QColor(73, 209, 255, 236),
                QColor(86, 226, 255, 236),
                QColor(108, 239, 242, 232),
                QColor(123, 216, 255, 232),
                QColor(116, 185, 255, 236),
                QColor(130, 170, 255, 236),
                QColor(149, 211, 255, 236),
            ],
            "error": [
                QColor(255, 112, 112, 238),
                QColor(255, 132, 132, 236),
                QColor(255, 154, 142, 232),
                QColor(255, 126, 126, 232),
                QColor(255, 102, 130, 236),
                QColor(255, 126, 160, 236),
                QColor(255, 148, 148, 236),
            ],
        }
        
        # 静态元素缓存
        self._static_cache = None
        self._cache_center = self._size // 2
        self._cache_radius = self._cache_center - 8
        self._build_static_cache()
        
        # 初始位置：屏幕右下角
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - self._size - 30, screen.height() - self._size - 100)

    def set_visual_mode(self, mode: str):
        mode_name = str(mode or "idle").strip().lower()
        if mode_name not in self._mode_palettes:
            mode_name = "idle"
        self._visual_mode = mode_name
        self._rainbow_colors = [QColor(color) for color in self._mode_palettes[mode_name]]
        self.update()

    def set_animations_enabled(self, enabled: bool):
        self._animations_enabled = bool(enabled)
        if self._animations_enabled:
            if self.isVisible():
                self._pulse_timer.start(33)
        else:
            self._pulse_timer.stop()
            self._hover_strength = 0.0
            self._appear_progress = 1.0
            self._tilt_x = 0.0
            self._tilt_y = 0.0
            self._click_impulse = 0.0
            self.update()

    def set_lock_position(self, locked: bool):
        self._lock_position = bool(locked)

    def apply_saved_position(self, x: int, y: int):
        try:
            self.move(int(x), int(y))
            self._clamp_to_visible_screen()
            self._emit_position_changed()
        except Exception:
            pass

    def _emit_position_changed(self):
        try:
            self.position_changed.emit(int(self.x()), int(self.y()))
        except Exception:
            pass

    def _resolve_target_screen(self):
        center = self.frameGeometry().center()
        screen = QApplication.screenAt(center)
        if screen:
            return screen
        for screen_obj in QApplication.screens():
            geo = screen_obj.availableGeometry()
            expanded = QRect(
                geo.left() - 120,
                geo.top() - 120,
                geo.width() + 240,
                geo.height() + 240,
            )
            if expanded.contains(center):
                return screen_obj
        return QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()

    def _clamp_to_visible_screen(self):
        screen = self._resolve_target_screen()
        if not screen:
            return
        geo = screen.availableGeometry()
        x = min(max(self.x(), geo.left() + self._dock_margin), geo.right() - self.width() - self._dock_margin)
        y = min(max(self.y(), geo.top() + self._dock_margin), geo.bottom() - self.height() - self._dock_margin)
        self.move(x, y)
    
    def _build_static_cache(self):
        """构建静态元素缓存"""
        from PyQt5.QtGui import QPixmap
        
        self._static_cache = QPixmap(self._size, self._size)
        self._static_cache.fill(Qt.transparent)
        
        painter = QPainter(self._static_cache)
        painter.setRenderHint(QPainter.Antialiasing)
        
        center = self._cache_center
        radius = self._cache_radius
        
        # 顶部高光
        highlight = QPainterPath()
        highlight.addEllipse(center - radius * 0.45, center - radius * 0.55, 
                            radius * 0.7, radius * 0.35)
        painter.setBrush(self._highlight_color)
        painter.setPen(Qt.NoPen)
        painter.drawPath(highlight)
        
        # 小高光点
        painter.setBrush(self._dot_color)
        painter.drawEllipse(int(center + radius * 0.25), int(center - radius * 0.35), 
                           int(radius * 0.12), int(radius * 0.12))
        
        # 右侧折射高光
        side_highlight = QPainterPath()
        side_highlight.addEllipse(center + radius * 0.05, center - radius * 0.18,
                                  radius * 0.34, radius * 0.54)
        painter.setBrush(QColor(255, 255, 255, 44))
        painter.drawPath(side_highlight)

        # 底部反光
        bottom_highlight = QPainterPath()
        bottom_highlight.addEllipse(center - radius * 0.3, center + radius * 0.3, 
                                    radius * 0.4, radius * 0.15)
        painter.setBrush(QColor(255, 255, 255, 50))
        painter.drawPath(bottom_highlight)
        
        # 中心图标背景和字母
        icon_size = int(radius * 0.6)
        icon_x = center - icon_size // 2
        icon_y = center - icon_size // 2
        
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawEllipse(icon_x, icon_y, icon_size, icon_size)

        painter.setBrush(QColor(255, 255, 255, 90))
        painter.drawEllipse(icon_x + int(icon_size * 0.15), icon_y + int(icon_size * 0.1),
                            int(icon_size * 0.3), int(icon_size * 0.16))
        
        painter.setPen(self._text_color)
        font = painter.font()
        font.setPixelSize(int(radius * 0.45))
        font.setBold(True)
        font.setFamily("Segoe UI")
        painter.setFont(font)
        painter.drawText(icon_x, icon_y, icon_size, icon_size, 
                        Qt.AlignCenter, "B")
        
        painter.end()
    
    def _update_pulse(self):
        """更新动态状态"""
        if not self._animations_enabled:
            return
        self._pulse_value = (self._pulse_value + 3) % 360
        self._float_phase = (self._float_phase + 0.12) % (3.1415926 * 2)
        self._spin_phase = (self._spin_phase + 0.05 + self._hover_strength * 0.03) % (3.1415926 * 2)
        hover_target = 1.0 if self.underMouse() else 0.0
        self._hover_strength += (hover_target - self._hover_strength) * 0.18
        self._appear_progress += (1.0 - self._appear_progress) * 0.16
        self._press_depth *= 0.78
        self._click_impulse *= 0.86

        if not self.underMouse() and not self._dragging:
            self._tilt_x *= 0.85
            self._tilt_y *= 0.85
        self.update()
    
    def paintEvent(self, event):
        """绘制带悬浮、倾斜和体积高光的气泡"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        center = self._cache_center
        radius = self._cache_radius
        offset = self._pulse_value
        bob_y = -2.5 * math.sin(self._float_phase) if self._animations_enabled else 0.0
        click_bounce = math.sin(self._spin_phase * 2.8) * self._click_impulse * 0.08 if self._animations_enabled else 0.0
        scale = (
            0.9
            + self._appear_progress * 0.1
            + self._hover_strength * 0.05
            + click_bounce
            - self._press_depth * 0.045
        )
        glow_shift_x = self._tilt_x * radius * 0.12
        glow_shift_y = self._tilt_y * radius * 0.12

        painter.translate(0, bob_y)
        painter.translate(center, center)
        painter.scale(scale, scale)
        painter.translate(-center, -center)
        
        # 地面阴影
        floor_shadow = QRadialGradient(center, center + radius * 0.95, radius * 1.25)
        floor_shadow.setColorAt(0.0, QColor(31, 34, 60, int(92 + self._hover_strength * 32)))
        floor_shadow.setColorAt(0.55, QColor(54, 58, 102, 38))
        floor_shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(floor_shadow)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(int(center - radius * 0.95), int(center + radius * 0.52),
                           int(radius * 1.9), int(radius * 0.8))

        # 外层晕光
        halo = QRadialGradient(center + glow_shift_x, center + glow_shift_y, radius * 1.28)
        halo.setColorAt(0.0, QColor(255, 255, 255, 78))
        halo.setColorAt(0.45, QColor(146, 194, 255, 28))
        halo.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(halo)
        painter.drawEllipse(int(center - radius * 1.05), int(center - radius * 1.05),
                           int(radius * 2.1), int(radius * 2.1))

        # 体积阴影
        shadow_gradient = QConicalGradient(center + 2, center + 3, offset + 12)
        shadow_gradient.setColorAt(0.0, self._shadow_colors[0])
        shadow_gradient.setColorAt(0.5, self._shadow_colors[1])
        shadow_gradient.setColorAt(1.0, self._shadow_colors[0])
        painter.setBrush(shadow_gradient)
        painter.drawEllipse(8, 9, int(radius * 2), int(radius * 2))
        
        # 主气泡本体
        gradient = QConicalGradient(center, center, offset)
        gradient.setColorAt(0.0, self._rainbow_colors[0])
        gradient.setColorAt(0.14, self._rainbow_colors[1])
        gradient.setColorAt(0.28, self._rainbow_colors[2])
        gradient.setColorAt(0.42, self._rainbow_colors[3])
        gradient.setColorAt(0.57, self._rainbow_colors[4])
        gradient.setColorAt(0.71, self._rainbow_colors[5])
        gradient.setColorAt(0.85, self._rainbow_colors[6])
        gradient.setColorAt(1.0, self._rainbow_colors[0])
        
        painter.setBrush(gradient)
        painter.drawEllipse(4, 4, int(radius * 2), int(radius * 2))
        
        # 内层玻璃感
        inner_radius = int(radius * 0.74)
        inner_gradient = QRadialGradient(
            center - radius * 0.22 + glow_shift_x,
            center - radius * 0.26 + glow_shift_y,
            radius * 1.05
        )
        inner_gradient.setColorAt(0.0, self._inner_colors[0])
        inner_gradient.setColorAt(0.45, QColor(255, 255, 255, 30))
        inner_gradient.setColorAt(1.0, self._inner_colors[1])
        painter.setBrush(inner_gradient)
        painter.drawEllipse(center - inner_radius, center - inner_radius, 
                           inner_radius * 2, inner_radius * 2)

        # 旋转高光带
        sheen = QLinearGradient(
            center - radius * 0.95 + glow_shift_x + math.cos(self._spin_phase) * 4,
            center - radius * 0.75 + glow_shift_y + math.sin(self._spin_phase) * 3,
            center + radius * 0.75 + glow_shift_x - math.sin(self._spin_phase) * 5,
            center + radius * 0.95 + glow_shift_y + math.cos(self._spin_phase) * 2
        )
        sheen.setColorAt(0.0, QColor(255, 255, 255, 0))
        sheen.setColorAt(0.32, QColor(255, 255, 255, int(74 + self._hover_strength * 30 + self._click_impulse * 32)))
        sheen.setColorAt(0.5, QColor(255, 255, 255, int(18 + self._hover_strength * 12)))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(sheen)
        painter.drawEllipse(5, 5, int(radius * 1.95), int(radius * 1.95))

        # 边缘描边，增强立体感
        rim_pen = QColor(self._rim_light)
        rim_pen.setAlpha(int(70 + self._hover_strength * 25))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(rim_pen)
        painter.drawEllipse(4, 4, int(radius * 2), int(radius * 2))

        inner_glow = QRadialGradient(center, center, radius * 0.6)
        inner_glow.setColorAt(0.0, self._core_glow)
        inner_glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(inner_glow)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(int(center - radius * 0.35), int(center - radius * 0.35),
                           int(radius * 0.7), int(radius * 0.7))
        
        # 绘制缓存的静态元素
        painter.drawPixmap(0, 0, self._static_cache)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPos() - self.pos()
            self._press_pos = event.globalPos()
            self._press_depth = 1.0
            self._click_impulse = 0.0
            if self._snap_animation.state() == QPropertyAnimation.Running:
                self._snap_animation.stop()
    
    def mouseMoveEvent(self, event):
        local_pos = event.pos()
        dx = (local_pos.x() - self.width() / 2) / max(self.width() / 2, 1)
        dy = (local_pos.y() - self.height() / 2) / max(self.height() / 2, 1)
        self._tilt_x = max(-1.0, min(1.0, dx))
        self._tilt_y = max(-1.0, min(1.0, dy))
        if self._dragging and self._drag_pos:
            self.move(event.globalPos() - self._drag_pos)
            self._clamp_to_visible_screen()
        self.update()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            moved = 999
            if self._press_pos is not None:
                moved = (event.globalPos() - self._press_pos).manhattanLength()
            if moved < 5:
                self._click_impulse = 1.0
                self.clicked.emit()
            else:
                self._click_impulse = 0.45
                if self._lock_position:
                    self._clamp_to_visible_screen()
                    self._emit_position_changed()
                else:
                    self._snap_to_edge()
            self._press_depth = 0.0
            self._press_pos = None

    def enterEvent(self, event):
        self._hover_strength = max(self._hover_strength, 0.25)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._tilt_x = 0.0
        self._tilt_y = 0.0
        self.update()
        super().leaveEvent(event)

    def _snap_to_edge(self):
        """拖拽释放后吸附到最近屏幕边缘。"""
        try:
            screen = self._resolve_target_screen()
            if not screen:
                return
            geo = screen.availableGeometry()
            target_x = geo.left() + self._dock_margin
            if abs(self.x() - geo.left()) >= abs((geo.right() - self.width()) - self.x()):
                target_x = geo.right() - self.width() - self._dock_margin

            target_y = min(
                max(self.y(), geo.top() + self._dock_margin),
                geo.bottom() - self.height() - self._dock_margin,
            )
            target = QPoint(target_x, target_y)
            if (target - self.pos()).manhattanLength() < 3:
                self.move(target)
                self._emit_position_changed()
                return

            if not self._animations_enabled:
                self.move(target)
                self._emit_position_changed()
                return

            self._snap_animation.stop()
            self._snap_animation.setStartValue(self.pos())
            self._snap_animation.setEndValue(target)
            self._snap_animation.start()
        except Exception:
            pass
    
    def showBubble(self):
        """显示气泡"""
        try:
            self._clamp_to_visible_screen()
        except Exception:
            pass
        # 避免每次显示都重建原生窗口；Windows 可能因此让气泡位置逐次下移。
        current_pos = QPoint(self.x(), self.y())
        flags = self.windowFlags()
        desired_flags = flags | Qt.WindowStaysOnTopHint | Qt.Tool
        if desired_flags != flags:
            self.setWindowFlags(desired_flags)
            self.move(current_pos)
        self._appear_progress = 0.0
        self._click_impulse = 0.6
        self.show()
        self.move(current_pos)
        self.raise_()
        if sys.platform == 'win32':
            try:
                import ctypes
                hwnd = int(self.winId())
                HWND_TOPMOST = -1
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOACTIVATE = 0x0010
                SWP_SHOWWINDOW = 0x0040
                ctypes.windll.user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
            except Exception:
                pass
        if self._animations_enabled:
            self._pulse_timer.start(33)
    
    def hideBubble(self):
        """隐藏气泡"""
        self._pulse_timer.stop()
        self.hide()


class ModernToast(QWidget):
    """极简美观的现代风格提示窗口 - 丝滑动画版"""
    def __init__(self, message, parent=None, is_error=False):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.ToolTip | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        layout = QVBoxLayout(self)
        self.frame = QFrame()
        
        # 根据是否是错误设置颜色
        if is_error:
            bg_color = "rgba(255, 240, 240, 0.95)"
            border_color = "rgba(255, 200, 200, 0.5)"
            text_color = "#e53935"
            icon = "⚠️"
        else:
            bg_color = "rgba(240, 250, 255, 0.95)"
            border_color = "rgba(200, 230, 255, 0.5)"
            text_color = "#1e88e5"
            icon = "✨"
        
        self.frame.setStyleSheet(f"""
            QFrame {{
                background: {bg_color};
                border: 1px solid {border_color};
                border-radius: 20px;
            }}
        """)
        
        frame_layout = QHBoxLayout(self.frame)
        frame_layout.setContentsMargins(20, 12, 20, 12)
        frame_layout.setSpacing(10)
        
        self.icon_label = QLabel(icon)
        self.icon_label.setStyleSheet(f"font-size: 16px; color: {text_color};")
        frame_layout.addWidget(self.icon_label)
        
        self.label = QLabel(message)
        self.label.setStyleSheet(f"""
            color: {text_color};
            font-size: 13px;
            font-weight: 500;
            font-family: 'Microsoft YaHei UI', sans-serif;
        """)
        frame_layout.addWidget(self.label)
        
        layout.addWidget(self.frame)
        
        # 阴影效果
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 4)
        self.frame.setGraphicsEffect(shadow)
        
        # 动画
        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self.opacity_anim.setDuration(400)
        self.opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        
        # 自动关闭
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide_toast)
        
    def show_toast(self):
        if self.parent():
            # 居中显示在父窗口顶部
            p_rect = self.parent().geometry()
            self.adjustSize()
            x = p_rect.x() + (p_rect.width() - self.width()) // 2
            y = p_rect.y() + 80 # 距离顶部 80px
            self.move(x, y)
        
        self.setWindowOpacity(0)
        self.show()
        self.opacity_anim.setStartValue(0)
        self.opacity_anim.setEndValue(1)
        self.opacity_anim.start()
        self.timer.start(3000) # 3秒后关闭
        
        # 播放提示音
        try:
            if sys.platform == 'win32':
                import winsound
                # 使用系统通知音
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                QApplication.beep()
        except:
            pass
        
    def hide_toast(self):
        self.opacity_anim.setStartValue(1)
        self.opacity_anim.setEndValue(0)
        self.opacity_anim.finished.connect(self.close)
        self.opacity_anim.start()

    @staticmethod
    def show_message(message, parent=None, is_error=False):
        toast = ModernToast(message, parent, is_error)
        toast.show_toast()
        return toast


class ModernConfirmDialog(QDialog):
    """极简美观的现代风格确认对话框"""
    def __init__(self, title, message, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        layout = QVBoxLayout(self)
        self.frame = QFrame()
        self.frame.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid rgba(0, 0, 0, 0.1);
                border-radius: 24px;
            }
        """)
        
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(30, 30, 30, 30)
        frame_layout.setSpacing(20)
        
        # 标题
        title_label = QLabel(title)
        title_label.setStyleSheet("""
            font-size: 18px;
            font-weight: 600;
            color: #333;
            font-family: 'Microsoft YaHei UI', sans-serif;
        """)
        frame_layout.addWidget(title_label)
        
        # 消息
        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("""
            font-size: 14px;
            color: #666;
            line-height: 1.5;
            font-family: 'Microsoft YaHei UI', sans-serif;
        """)
        frame_layout.addWidget(msg_label)
        
        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        
        self.btn_cancel = QPushButton(t("取消"))
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                padding: 10px 24px;
                background: #f5f5f7;
                border: none;
                border-radius: 12px;
                color: #666;
                font-weight: 500;
            }
            QPushButton:hover { background: #eeeef0; }
        """)
        self.btn_cancel.clicked.connect(self.reject)
        
        self.btn_confirm = QPushButton(t("确定清除"))
        self.btn_confirm.setCursor(Qt.PointingHandCursor)
        self.btn_confirm.setStyleSheet("""
            QPushButton {
                padding: 10px 24px;
                background: #ff4d4f;
                border: none;
                border-radius: 12px;
                color: white;
                font-weight: 500;
            }
            QPushButton:hover { background: #ff7875; }
        """)
        self.btn_confirm.clicked.connect(self.accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_confirm)
        
        frame_layout.addLayout(btn_layout)
        layout.addWidget(self.frame)
        
        # 阴影
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0, 0, 0, 50))
        shadow.setOffset(0, 8)
        self.frame.setGraphicsEffect(shadow)
        
    @staticmethod
    def confirm(parent, title, message):
        dialog = ModernConfirmDialog(title, message, parent)
        return dialog.exec_() == QDialog.Accepted

# ============ 媒体信息获取线程 ============

class MediaMetadataThread(QThread):
    """异步获取媒体详细信息（标题、缩略图、时长、格式）"""
    metadata_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, media_info: dict):
        super().__init__()
        self.media_info = media_info
        self.process = None
        self.is_stopped = False

    def stop(self):
        self.is_stopped = True
        if self.process:
            try:
                self.process.terminate()
                self.process.kill()
            except Exception:
                pass

    def _is_internal_cookie_file(self, path):
        try:
            marker_path = path.replace('_cookies.txt', '_cookies.internal')
            if os.path.exists(marker_path):
                return True
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    head = f.read(4096)
                if 'Source: internal_browser' in head or 'internal_browser' in head:
                    return True
            return False
        except Exception:
            return False

    def _find_latest_internal_cookie(self):
        try:
            cookie_dir = getattr(auth_utils, 'COOKIE_DIR', '')
            if not cookie_dir or not os.path.isdir(cookie_dir):
                return None
            candidates = []
            for name in os.listdir(cookie_dir):
                if not name.endswith('_cookies.txt'):
                    continue
                full_path = os.path.join(cookie_dir, name)
                if os.path.isfile(full_path) and self._is_internal_cookie_file(full_path):
                    candidates.append(full_path)
            if not candidates:
                return None
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return candidates[0]
        except Exception:
            return None

    def _resolve_cookie_for_url(self, url):
        host = ""
        try:
            host = QUrl(url).host().lower().strip()
        except Exception:
            host = ""
            
        if not host:
            return self._find_latest_internal_cookie()

        candidates = []
        seen = set()

        def _add_host(h):
            h = str(h or "").strip().lower()
            if not h or h in seen:
                return
            seen.add(h)
            try:
                candidates.append(auth_utils.get_cookie_file(h))
            except Exception:
                pass

        _add_host(host)
        if host.startswith("www."):
            _add_host(host[4:])

        parts = host.split('.')
        if len(parts) >= 2:
            base = '.'.join(parts[-2:])
            if base != host:
                _add_host(base)

        if ('youtu.be' in host) or ('youtube.' in host):
            for alias in ['www.youtube.com', 'youtube.com', 'm.youtube.com', 'google.com', 'accounts.google.com', 'youtube-nocookie.com']:
                _add_host(alias)
        elif ('x.com' in host) or ('twitter.com' in host):
            for alias in ['x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com', 'm.twitter.com']:
                _add_host(alias)

        for cf in candidates:
            try:
                if os.path.exists(cf) and self._is_internal_cookie_file(cf):
                    return cf
            except Exception:
                continue

        return self._find_latest_internal_cookie()

    def run(self):
        if self.is_stopped:
            return
        try:
            url = self.media_info.get('page_url') or self.media_info.get('url')
            if not url:
                return

            # 尝试找到 yt-dlp
            yt_dlp_path = "yt-dlp"
            # 检查当前目录下是否有 yt-dlp.exe
            if os.path.exists(os.path.join(APP_DIR, "yt-dlp.exe")):
                yt_dlp_path = os.path.join(APP_DIR, "yt-dlp.exe")
            
            cmd = [yt_dlp_path, "--dump-json", "--no-playlist", "--flat-playlist", url]
            
            cookie_file = self._resolve_cookie_for_url(url)
            if cookie_file:
                cmd.extend(["--cookies", cookie_file])
            
            if self.is_stopped:
                return
            # 尝试使用当前 Python 运行环境中的 yt-dlp
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            stdout, stderr = self.process.communicate(timeout=15)

            if self.is_stopped:
                return

            if self.process.returncode == 0 and stdout.strip():
                data = json.loads(stdout)
                result = {
                    'title': data.get('title', self.media_info.get('title')),
                    'thumbnail': data.get('thumbnail'),
                    'duration': data.get('duration'),
                    'duration_string': data.get('duration_string'),
                    'formats': data.get('formats', []),
                    'original_url': url,
                    'webpage_url': data.get('webpage_url', url)
                }
                
                # 提取可用分辨率
                resolutions = []
                seen_res = set()
                
                # 优先处理视频格式
                video_formats = [f for f in result['formats'] if f.get('vcodec') != 'none']
                # 按分辨率从高到低排序
                video_formats.sort(key=lambda x: (x.get('height') or 0, x.get('tbr') or 0), reverse=True)
                
                for f in video_formats:
                    height = f.get('height')
                    if not height: continue
                    
                    # 构造显示名称，例如 1080p, 720p
                    res_note = f.get('format_note') or f"{height}p"
                    # 过滤掉一些奇怪的格式，只保留常见的分辨率标识
                    if not any(p in res_note.lower() for p in ('p', 'k')):
                        res_note = f"{height}p"
                        
                    ext = f.get('ext', 'mp4')
                    key = f"{res_note}_{ext}" # 同一分辨率不同格式保留
                    
                    if key not in seen_res:
                        resolutions.append({
                            'id': f.get('format_id'),
                            'note': res_note,
                            'ext': ext,
                            'filesize': f.get('filesize') or f.get('filesize_approx')
                        })
                        seen_res.add(key)
                
                # 如果没有视频格式，再考虑音频或其他
                if not resolutions:
                    for f in result['formats']:
                        res = f.get('format_note') or f.get('resolution')
                        if res and res not in seen_res:
                            resolutions.append({
                                'id': f.get('format_id'),
                                'note': res,
                                'ext': f.get('ext'),
                                'filesize': f.get('filesize')
                            })
                            seen_res.add(res)
                
                if self.is_stopped:
                    return
                result['resolutions'] = resolutions
                self.metadata_ready.emit(result)
            else:
                if not self.is_stopped:
                    error_msg = stderr if stderr else "yt-dlp returned no output"
                    self.error_occurred.emit(f"yt-dlp error: {error_msg}")
        except Exception as e:
            if not self.is_stopped:
                self.error_occurred.emit(str(e))



class ThumbnailExtractThread(QThread):
    """使用 ffmpeg 从视频 URL 提取缩略图"""
    thumbnail_ready = pyqtSignal(bytes)
    
    def __init__(self, video_url):
        super().__init__()
        self.video_url = video_url
    
    def run(self):
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                tmp_path = tmp.name
            
            # 查找 ffmpeg
            ffmpeg_path = "ffmpeg"
            if os.path.exists(os.path.join(APP_DIR, "ffmpeg.exe")):
                ffmpeg_path = os.path.join(APP_DIR, "ffmpeg.exe")
            
            # 使用 ffmpeg 提取靠前的一帧（跳过可能的黑帧）
            cmd = [
                ffmpeg_path, '-y',
                '-i', self.video_url,
                '-ss', '00:00:02',  # 跳过前2秒
                '-vframes', '1',
                '-q:v', '2',
                '-vf', 'scale=320:-1',  # 缩放到320宽度
                tmp_path
            ]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            process.wait(timeout=15)
            
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                with open(tmp_path, 'rb') as f:
                    data = f.read()
                try:
                    os.remove(tmp_path)
                except:
                    pass
                self.thumbnail_ready.emit(data)
            else:
                self.thumbnail_ready.emit(b'')
        except Exception as e:
            print(f"[DEBUG] 缩略图提取失败: {e}")
            self.thumbnail_ready.emit(b'')

# ============ 极简丝滑下拉框 ============

class AnimatedComboBox(QPushButton):
    """丝滑展开的极简下拉框"""
    currentIndexChanged = pyqtSignal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = [] # list of (text, data)
        self.current_index = -1
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._toggle_popup)
        
        # 弹出层
        self.popup = QFrame(None)
        self.popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.popup.setAttribute(Qt.WA_TranslucentBackground)
        
        # 阴影效果
        self.shadow = QGraphicsDropShadowEffect(self.popup)
        self.shadow.setBlurRadius(15)
        self.shadow.setColor(QColor(0, 0, 0, 40))
        self.shadow.setOffset(0, 4)
        
        self.popup_content = QFrame(self.popup)
        self.popup_content.setObjectName("popupContent")
        self.popup_content.setGraphicsEffect(self.shadow)
        
        self.popup_layout = QVBoxLayout(self.popup_content)
        self.popup_layout.setContentsMargins(4, 4, 4, 4)
        self.popup_layout.setSpacing(2)
        
        # 样式
        self.setStyleSheet("""
            QPushButton {
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                padding: 4px 12px;
                background: #f9f9f9;
                text-align: left;
                font-size: 11px;
                color: #555;
            }
            QPushButton:hover { background: #f0f0f0; border-color: #ccc; }
            QPushButton:checked { background: white; border-color: #0072ff; }
        """)
        
        self.popup_content.setStyleSheet("""
            #popupContent {
                background: white;
                border: 1px solid #eee;
                border-radius: 8px;
            }
        """)
        
        # 动画
        self.animation = QPropertyAnimation(self.popup, b"geometry")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.OutQuint)

    def addItem(self, text, data=None):
        self.items.append((text, data))
        btn = QPushButton(text)
        btn.setProperty("data", data)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setObjectName("itemButton")
        btn.setStyleSheet("""
            QPushButton#itemButton {
                border: none;
                border-radius: 5px;
                padding: 7px 12px;
                text-align: left;
                background: transparent;
                font-size: 11px;
                color: #444;
            }
            QPushButton#itemButton:hover { background: #f5f9ff; color: #0072ff; }
        """)
        index = len(self.items) - 1
        btn.clicked.connect(lambda: self._on_item_clicked(index))
        self.popup_layout.addWidget(btn)
        
        if self.current_index == -1:
            self.setCurrentIndex(0)

    def setCurrentIndex(self, index):
        if 0 <= index < len(self.items):
            self.current_index = index
            display_text = self.items[index][0]
            # 截断过长的文本
            if len(display_text) > 25:
                display_text = display_text[:22] + "..."
            self.setText(display_text)
            self.currentIndexChanged.emit(index)

    def currentData(self):
        if 0 <= self.current_index < len(self.items):
            return self.items[self.current_index][1]
        return None

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        if not enabled:
            self.setStyleSheet(self.styleSheet() + "QPushButton { color: #aaa; background: #eee; }")
        else:
            self.setStyleSheet(self.styleSheet().replace("QPushButton { color: #aaa; background: #eee; }", ""))

    def clear(self):
        self.items = []
        self.current_index = -1
        self.setText(t("选择分辨率"))
        while self.popup_layout.count():
            item = self.popup_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _toggle_popup(self):
        if self.isChecked():
            self._show_popup()
        else:
            self.popup.hide()

    def _show_popup(self):
        # 计算位置和大小
        pos = self.mapToGlobal(self.rect().bottomLeft())
        width = max(self.width(), 180)
        
        # 限制最大高度
        hint_height = self.popup_layout.sizeHint().height() + 10
        max_height = 300
        height = min(hint_height, max_height)
        
        # 如果高度超过限制，需要加滚动条（这里简化处理，假设项不多）
        self.popup_content.setFixedSize(width, height)
        self.popup.setFixedSize(width + 20, height + 20) # 留出阴影空间
        
        start_rect = QRect(pos.x(), pos.y(), width, 0)
        end_rect = QRect(pos.x(), pos.y() + 4, width, height)
        
        self.popup.setGeometry(start_rect)
        self.popup.show()
        
        self.animation.setStartValue(start_rect)
        self.animation.setEndValue(end_rect)
        self.animation.start()

    def _on_item_clicked(self, index):
        self.setCurrentIndex(index)
        self.setChecked(False)
        self.popup.hide()

    def hideEvent(self, event):
        self.popup.hide()
        self.setChecked(False)
        super().hideEvent(event)

# ============ 媒体卡片组件 ============

class MediaPreviewThread(QThread):
    """后台生成 VDH 风格内联预览：抓"中间一小段"分片并 remux 成小 mp4。"""

    preview_ready = pyqtSignal(str)
    preview_failed = pyqtSignal(str)
    thumbnail_ready = pyqtSignal(str)

    def __init__(self, spec, cache=None, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.cache = cache

    def run(self):
        try:
            if not _HAS_VDH_PREVIEW:
                self.preview_failed.emit("预览组件不可用")
                return
            if self.cache is not None:
                cached = self.cache.get(self.spec)
                if cached is not None and os.path.isfile(str(cached)):
                    self.preview_ready.emit(str(cached))
                    self._emit_thumbnail_from_video(str(cached))
                    return
            ffmpeg = resolve_ffmpeg()
            if not ffmpeg:
                self.preview_failed.emit("未找到 ffmpeg")
                return
            if self.cache is not None:
                target = self.cache.path_for(self.spec)
            else:
                target = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "browser_data", "preview_cache",
                    "preview_%s.mp4" % abs(hash(self.spec.url)),
                )
            result = extract_preview(ffmpeg, self.spec, target)
            if result is None:
                self.preview_failed.emit("片段提取失败")
                return
            if self.cache is not None:
                self.cache.put(self.spec, result)
            self.preview_ready.emit(str(result))
            self._emit_thumbnail_from_video(str(result))
        except Exception as exc:  # noqa: BLE001 - 预览失败不应影响卡片
            self.preview_failed.emit(str(exc))

    def _emit_thumbnail_from_video(self, video_path):
        """从本地预览mp4里抽一帧当缩略图，不需要重新下载。"""
        try:
            ffmpeg = resolve_ffmpeg()
            if not ffmpeg or not os.path.isfile(video_path):
                return
            thumb_path = video_path.rsplit(".", 1)[0] + ".jpg"
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-ss", "00:00:01", "-i", video_path,
                "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "5",
                "-f", "image2", thumb_path,
            ]
            import subprocess
            subprocess.run(cmd, timeout=10, check=False, capture_output=True)
            if os.path.isfile(thumb_path) and os.path.getsize(thumb_path) > 1024:
                self.thumbnail_ready.emit(thumb_path)
        except Exception:
            pass


class PreviewArea(QFrame):
    """VDH 的 ``com-media-preview`` 等价物：封面 + 悬停内联播放（静音、循环）。"""

    def __init__(self, card=None, parent=None, width=176, height=99):
        super().__init__(parent)
        self.card = card
        self._width = width
        self._height = height
        self._spec = None
        self._thread = None
        self._player = None
        self._video = None
        self._built_path = ""
        self._failed = False
        self._busy = False
        self._thumb_url = None
        self._thumb_loaded = False

        self.setFixedSize(width, height)
        self.setStyleSheet("background: #1c2030; border-radius: 6px;")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(t("悬停加载内联预览"))
        self.setMouseTracking(True)

        self.thumb_label = QLabel(self)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setGeometry(0, 0, width, height)
        self.thumb_label.setStyleSheet("background: transparent;")

        self.play_overlay = QPushButton("▶", self)
        self.play_overlay.setFixedSize(32, 32)
        self.play_overlay.setCursor(Qt.PointingHandCursor)
        self.play_overlay.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 110); color: white; border: none;
                border-radius: 16px; font-size: 13px; padding-left: 2px;
            }
            QPushButton:hover { background: rgba(0, 0, 0, 160); }
        """)
        self.play_overlay.move((width - 32) // 2, (height - 32) // 2)
        self.play_overlay.clicked.connect(self.toggle)

        self.status_label = QLabel("", self)
        self.status_label.setGeometry(4, height - 18, width - 8, 14)
        self.status_label.setStyleSheet("""
            background: rgba(0, 0, 0, 140); color: #ddd; font-size: 10px;
            border-radius: 3px; padding-left: 4px;
        """)
        self.status_label.hide()

    # ---------- 对外接口 ----------
    def set_spec(self, spec):
        self._spec = spec
        self._failed = False
        if spec is None:
            self.play_overlay.hide()
            self.setToolTip(t("此条目没有可预览的直链"))
        else:
            self.play_overlay.show()
            self.setToolTip(t("点击加载内联预览"))

    def set_thumbnail_pixmap(self, pixmap):
        if pixmap is not None and not pixmap.isNull():
            self.thumb_label.setPixmap(
                pixmap.scaled(self._width, self._height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            )
            self._thumb_loaded = True

    def set_thumbnail_url(self, url):
        """从URL加载网站现成的缩略图（对齐VDH：直接用网站封面，不自己抽帧）。"""
        self._thumb_url = url
        if not url:
            return
        try:
            import urllib.request
            data = urllib.request.urlopen(url, timeout=5).read()
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                self.set_thumbnail_pixmap(pixmap)
        except Exception:
            pass

    def enterEvent(self, event):
        """鼠标悬停事件（暂时关闭自动触发，因为片段提取成功率低）。"""
        super().enterEvent(event)

    def leaveEvent(self, event):
        """鼠标移开事件（暂时关闭自动停止）。"""
        super().leaveEvent(event)

    def has_preview(self):
        return bool(self._built_path) and os.path.isfile(self._built_path)

    def is_playing(self):
        return self._player is not None and self._video is not None and self._video.isVisible()

    def toggle(self):
        if self.is_playing():
            self.stop()
            return
        if self.has_preview():
            self._play_built()
            return
        if self._spec is None:
            self._flash_status(t("无直链，无法预览"))
            return
        if not _HAS_QT_MULTIMEDIA:
            self._flash_status(t("当前环境缺少播放组件"))
            return
        self._start_build()

    def stop(self):
        # 对齐VDH deactivate()：只pause()，不stop()
        if self._player is not None:
            try:
                self._player.pause()
            except Exception:
                pass
        if self._video is not None:
            self._video.hide()
        # 回到静态缩略图（对齐VDH：移开鼠标回到封面）
        if self._thumb_loaded:
            self.thumb_label.show()
        self.play_overlay.show()

    def cleanup(self):
        self._stop_thread()
        if self._player is not None:
            try:
                self._player.stop()
                self._player.setMedia(QMediaContent())
            except Exception:
                pass
            self._player.deleteLater()
            self._player = None
        if self._video is not None:
            self._video.deleteLater()
            self._video = None

    # ---------- 内部实现 ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle()
        super().mousePressEvent(event)

    def _start_build(self):
        if self._busy:
            return
        self._busy = True
        self._flash_status(t("预览加载中…"))
        self.play_overlay.hide()
        self._thread = MediaPreviewThread(self._spec, _preview_cache(), self)
        self._thread.preview_ready.connect(self._on_ready)
        self._thread.preview_failed.connect(self._on_failed)
        self._thread.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_thumbnail_ready(self, path):
        """缩略图生成完成，显示在卡片左侧。"""
        try:
            if not path or not os.path.isfile(path):
                return
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.set_thumbnail_pixmap(pixmap)
        except Exception:
            pass

    def _on_thread_finished(self):
        self._busy = False
        self._thread = None

    def _on_ready(self, path):
        self._built_path = path
        self._failed = False
        self.status_label.hide()
        self._play_built()

    def _on_failed(self, message):
        self._failed = True
        self.play_overlay.show()
        self._flash_status(t_format("预览不可用：{}", message))
        try:
            print(f"[PREVIEW] 生成失败: {message}")
        except Exception:
            pass

    def _play_built(self):
        if not self.has_preview() or not _HAS_QT_MULTIMEDIA:
            return
        if self._player is None:
            self._video = QVideoWidget(self)
            self._video.setGeometry(0, 0, self._width, self._height)
            self._video.setStyleSheet("background: black; border-radius: 6px;")
            self._player = QMediaPlayer(self)
            self._player.setVideoOutput(self._video)
            self._player.setVolume(0)
            try:
                self._player.mediaStatusChanged.connect(self._on_media_status)
            except Exception:
                pass
            try:
                self._player.error.connect(self._on_player_error)
            except Exception:
                pass
        self._video.show()
        self._video.raise_()
        self.play_overlay.hide()
        try:
            self._player.stop()
            self._player.setMedia(QMediaContent(QUrl.fromLocalFile(self._built_path)))
            self._player.play()
        except Exception as exc:  # noqa: BLE001
            self._flash_status(t_format("播放失败：{}", exc))
            self.stop()

    def _on_media_status(self, status):
        try:
            if status == QMediaPlayer.EndOfMedia:
                self._player.setPosition(0)
                self._player.play()
        except Exception:
            pass

    def _on_player_error(self, *_args):
        self._flash_status(t("播放失败（可能是编码不受支持）"))
        self.stop()

    def _flash_status(self, text, timeout_ms=4000):
        self.status_label.setText(text)
        self.status_label.show()
        QTimer.singleShot(timeout_ms, self.status_label.hide)

    def _stop_thread(self):
        thread = self._thread
        if thread is None:
            return
        try:
            if thread.isRunning():
                thread.wait(50)
        except Exception:
            pass


class MediaCard(QFrame):
    """单个媒体项的卡片展示"""
    send_to_downloader = pyqtSignal(str, str)
    remove_requested = pyqtSignal(QWidget)
    stop_requested = pyqtSignal(str)
    send_download_as = pyqtSignal(str, str)  # 「下载为…」：URL + 标题
    _stopping_threads = []  # 保存正在停止的线程，防止被垃圾回收

    def __init__(self, media_info: dict, parent=None):
        super().__init__(parent)
        self.media_info = media_info
        self.setProperty("class", "mediaCard")
        self.network_manager = QNetworkAccessManager(self)
        self.metadata_thread = None
        self._stabilize_timer = None
        self._stabilize_remaining = 0
        self._download_state = "discovered"
        
        self._setup_ui()
        self._start_metadata_fetch()
        self._start_stabilize_countdown()

    @classmethod
    def _safe_remove_thread(cls, thread):
        if thread in cls._stopping_threads:
            cls._stopping_threads.remove(thread)
        thread.deleteLater()

    def cleanup(self):
        """清理卡片关联的线程和定时器资源"""
        if self._stabilize_timer:
            self._stabilize_timer.stop()
            self._stabilize_timer = None
        preview_area = getattr(self, "preview_area", None)
        if preview_area is not None:
            try:
                preview_area.cleanup()
            except Exception:
                pass
        if self.metadata_thread and self.metadata_thread.isRunning():
            thread = self.metadata_thread
            thread.stop()
            MediaCard._stopping_threads.append(thread)
            thread.finished.connect(lambda: MediaCard._safe_remove_thread(thread))
            self.metadata_thread = None

    def update_media_info(self, new_info: dict):
        """动态更新媒体信息和 UI 文本，防止删除重建导致的重建成本"""
        self.media_info = new_info
        if hasattr(self, 'title_label') and new_info.get('title'):
            old_title = self.title_label.text()
            new_title = new_info.get('title')
            if new_title and new_title not in (t('正在解析...'), t('未知视频'), old_title):
                self.title_label.setText(new_title)
        if hasattr(self, 'size_label') and new_info.get('size'):
            self.size_label.setText(new_info.get('size'))
        if new_info.get('variants'):
            self._populate_variants(new_info.get('variants'))
        self._refresh_preview_spec()
        self._update_tooltips()

    def _update_tooltips(self):
        """更新标签和按钮提示，清晰显示实际要复制和下载的链接"""
        media_type = self.media_info.get("type")
        is_major_platform = media_type in ("Douyin", "Bilibili", "YouTube", "TikTok")
        
        # 确定最终用户会复制和下载的链接（对于主流视频网为网页详情页，普通网站为直链）
        if is_major_platform:
            target_url = self.media_info.get('page_url') or self.media_info.get('url', '')
        else:
            target_url = self.media_info.get('url', '') or self.media_info.get('page_url', '')
            
        title = self.media_info.get('title', '未知视频')
        
        short_url = target_url
        if len(short_url) > 80:
            short_url = short_url[:50] + "..." + short_url[-30:]
            
        if hasattr(self, 'title_label'):
            self.title_label.setToolTip(t_format("视频标题: {}\n实际下载/复制的音视频页面链接: {}\n媒体类型: {}", title, target_url, media_type))
        if hasattr(self, 'copy_btn'):
            self.copy_btn.setToolTip(t_format("复制完整音视频详情页链接 ({})", short_url))

    def _populate_variants(self, variants):
        """把同一视频的多个格式填进格式选择器（VDH 的 "MP4 1080p" 面）。"""
        if not isinstance(variants, list) or len(variants) < 2:
            return False
        self._custom_variants = []
        try:
            self.res_combo.clear()
            self.res_combo.setEnabled(True)
            for index, variant in enumerate(variants):
                quality = str(variant.get("quality") or "").strip()
                ext = str(variant.get("ext") or "").strip().upper()
                url = str(variant.get("url") or "")
                if not ext:
                    lower = url.lower().split("?")[0]
                    for candidate in ("m3u8", "mp4", "webm", "mkv", "mpd", "m4a", "mp3"):
                        if lower.endswith("." + candidate) or ("." + candidate + "?") in lower:
                            ext = candidate.upper()
                            break
                label = " ".join(part for part in (ext, quality) if part) or t("媒体")
                size = str(variant.get("size") or "").strip()
                text = label if not size else "%s - %s" % (label, size)
                self.res_combo.addItem(text, index)
                self._custom_variants.append(variant)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[MEDIA] 填充分辨率变体失败: {exc}")
            self._custom_variants = []
            return False

    def _refresh_preview_spec(self, duration=None):
        """按当前 media_info 构造预览参数（对齐 VDH：直链/清单都可抓"中间一小段"）。"""
        area = getattr(self, "preview_area", None)
        if area is None or not _HAS_VDH_PREVIEW:
            return
        info = self.media_info or {}
        url = str(info.get("url") or "").strip()
        if not url:
            area.set_spec(None)
            return
        mtype = str(info.get("type") or "")
        if info.get("has_drm") or mtype == "YouTubePage":
            area.set_spec(None)
            return
        secs = duration
        if secs is None:
            secs = info.get("duration") or info.get("duration_s")
        quality = info.get("quality") if isinstance(info.get("quality"), dict) else {}
        if not secs:
            manifest = quality.get("manifest") if isinstance(quality, dict) else None
            if isinstance(manifest, dict):
                secs = manifest.get("duration_s")
        try:
            secs = float(secs) if secs else None
        except (TypeError, ValueError):
            secs = None
        lower = url.lower()
        is_manifest = bool(
            info.get("is_manifest")
            or mtype in ("HLS", "DASH")
            or ".m3u8" in lower
            or ".mpd" in lower
        )
        headers = info.get("headers") if isinstance(info.get("headers"), dict) else {}
        # 先加载网站现成的缩略图（对齐VDH：直接用网站封面，秒加载）
        thumb_url = info.get("thumbnail") or info.get("thumbnail_url") or ""
        if thumb_url:
            area.set_thumbnail_url(str(thumb_url))
        try:
            area.set_spec(PreviewSpec(
                url=url,
                headers=headers,
                page_url=str(info.get("page_url") or ""),
                duration_s=secs,
                is_manifest=is_manifest,
                media_type=mtype,
                is_live=bool(info.get("is_live")),
                quality=str(quality.get("resolution") or ""),
            ))
        except Exception as exc:  # noqa: BLE001 - 预览是增强功能，失败不阻塞卡片
            print(f"[PREVIEW] 构造预览参数失败: {exc}")
            area.set_spec(None)

    def _setup_ui(self):
        # 主布局
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(12)

        # 左侧：VDH 风格预览区（封面 → 点击后内联播放"中间一小段"）
        self.preview_area = PreviewArea(self)
        self.thumb_container = self.preview_area
        self.thumb_label = self.preview_area.thumb_label
        self._set_default_thumbnail()
        self._refresh_preview_spec()
        
        # 时长标签
        self.duration_label = QLabel("00:00", self.thumb_container)
        self.duration_label.setStyleSheet("""
            background: rgba(0, 0, 0, 0.7); color: white; font-size: 10px; 
            padding: 1px 4px; border-radius: 3px; font-weight: bold;
        """)
        self.duration_label.hide()
        
        # 平台小图标 (左下角) - 默认隐藏，仅在识别到平台时显示
        self.platform_icon = QLabel("", self.thumb_container)
        self.platform_icon.setStyleSheet("background: transparent; border-radius: 4px; padding: 2px;")
        self.platform_icon.move(4, 66)
        self.platform_icon.hide()
        
        self.main_layout.addWidget(self.thumb_container)

        # 右侧：信息容器
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        # 第一行：标题 + 关闭按钮
        title_row = QHBoxLayout()
        self.title_label = QLabel(self.media_info.get('title', '正在解析...'))
        self.title_label.setStyleSheet("font-weight: 600; color: #333; font-size: 13px;")
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label)
        
        title_row.addStretch()
        
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet("""
            QPushButton { border: none; color: #999; font-size: 16px; background: transparent; }
            QPushButton:hover { color: #f44336; }
        """)
        self.close_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        title_row.addWidget(self.close_btn)
        info_layout.addLayout(title_row)

        # 第二行：标签行
        tags_layout = QHBoxLayout()
        tags_layout.setSpacing(8)

        m_type = self.media_info.get('type', 'Media')
        # YouTubePage 是页面 URL 兜底条目（交给 yt-dlp 官方提取器），显示友好名称
        type_display = t("YouTube 页面") if m_type == "YouTubePage" else m_type
        self.type_tag = QLabel(type_display)
        if m_type == "YouTubePage":
            self.type_tag.setToolTip(t("通过页面 URL 下载（yt-dlp 官方提取器，最稳定）"))
        self.type_tag.setStyleSheet("""
            background: #f0f0f0; color: #666; padding: 1px 6px; 
            border-radius: 3px; font-size: 10px; border: 1px solid #ddd;
        """)
        tags_layout.addWidget(self.type_tag)

        # 时长徽标（对齐 VDH 的 p_duration）
        self.duration_badge = QLabel("")
        self.duration_badge.setStyleSheet(
            "background: #f5f5f5; color: #555; padding: 1px 6px; border-radius: 3px;"
            " font-size: 10px; border: 1px solid #e5e5e5;"
        )
        self.duration_badge.hide()
        tags_layout.addWidget(self.duration_badge)
        
        # 质量标签 (如果有)，配色对齐 VDH 的 meh / good / very_good 三档
        quality_info = self.media_info.get('quality', {})
        if quality_info and quality_info.get('resolution'):
            height = 0
            if isinstance(quality_info.get('height'), int):
                height = int(quality_info.get('height'))
            else:
                import re as _re
                match = _re.search(r"(\d{3,4})\s*[pP]|(\d{3,4})\s*[xX*]\s*(\d{3,4})",
                                   str(quality_info.get('resolution') or ""))
                if match:
                    height = int(match.group(1) or match.group(3) or 0)
            if height > 1080:
                badge_style = ("background: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9;")
            elif height == 1080:
                badge_style = ("background: #e3f2fd; color: #1976d2; border: 1px solid #bbdefb;")
            else:
                badge_style = ("background: #f5f5f5; color: #757575; border: 1px solid #e0e0e0;")
            self.quality_tag = QLabel(quality_info['resolution'])
            self.quality_tag.setStyleSheet(
                f"{badge_style} padding: 1px 6px; border-radius: 3px; font-size: 10px;"
            )
            tags_layout.addWidget(self.quality_tag)
        
        # 播放状态标签
        if self.media_info.get('is_playing'):
            self.playing_tag = QLabel(t("▶ 播放中"))
            self.playing_tag.setStyleSheet("""
                background: #e8f5e9; color: #2e7d32; padding: 1px 6px; 
                border-radius: 3px; font-size: 10px; border: 1px solid #c8e6c9;
                font-weight: bold;
            """)
            tags_layout.addWidget(self.playing_tag)
        
        # 预加载标签
        if self.media_info.get('is_preload'):
            self.preload_tag = QLabel(t("预加载"))
            self.preload_tag.setToolTip(t("此视频是预加载的，可能不是当前正在观看的视频"))
            self.preload_tag.setStyleSheet("""
                background: #fce4ec; color: #c2185b; padding: 1px 6px; 
                border-radius: 3px; font-size: 10px; border: 1px solid #f8bbd9;
            """)
            tags_layout.addWidget(self.preload_tag)
        
        # DRM 警告标签
        if self.media_info.get('has_drm'):
            self.drm_tag = QLabel("🔒 DRM")
            self.drm_tag.setToolTip(t("此内容可能受 DRM 保护，下载可能受限"))
            self.drm_tag.setStyleSheet("""
                background: #fff3e0; color: #e65100; padding: 1px 6px; 
                border-radius: 3px; font-size: 10px; border: 1px solid #ffe0b2;
            """)
            tags_layout.addWidget(self.drm_tag)

        self.size_label = QLabel(self.media_info.get('size', ''))
        self.size_label.setStyleSheet("color: #888; font-size: 11px;")
        tags_layout.addWidget(self.size_label)
        
        tags_layout.addStretch()
        info_layout.addLayout(tags_layout)

        # 第三行：操作行
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)
        
        self.res_combo = AnimatedComboBox()
        self.res_combo.setMinimumWidth(140)
        self.res_combo.addItem(t("正在加载格式..."))
        self.res_combo.setEnabled(False)
        actions_layout.addWidget(self.res_combo)
        # 同一视频的多个格式（VDH com-media-selector 等价物）
        self._custom_variants = []

        self.download_btn = QPushButton(t("⬇️ 稳定中(3s)"))
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #ff9a9e, stop:0.5 #fecfef, stop:1 #a1c4fd);
                color: white; border: none; border-radius: 12px; padding: 4px 18px; 
                font-size: 11px; font-weight: bold; min-height: 24px;
            }
            QPushButton:hover { 
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #ff8a8e, stop:0.5 #fdbfdf, stop:1 #91b4ed);
            }
            QPushButton:disabled { background: #ccc; color: #999; }
        """)
        self.download_btn.clicked.connect(self._on_download_clicked)
        actions_layout.addWidget(self.download_btn)
        
        # 复制链接按钮
        self.copy_btn = QPushButton("📋")
        self.copy_btn.setToolTip(t("复制媒体链接"))
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setFixedSize(28, 28)
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background: #f5f5f5; border: 1px solid #ddd; border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover { background: #e8e8e8; border-color: #ccc; }
        """)
        self.copy_btn.clicked.connect(self._on_copy_clicked)
        actions_layout.addWidget(self.copy_btn)

        # 下拉菜单：仅音频 / 复制链接 / 下载为…（对齐 VDH 的 action_download_audio / _copy / _download_as）
        self.more_btn = QPushButton("▾")
        self.more_btn.setToolTip(t("更多操作"))
        self.more_btn.setCursor(Qt.PointingHandCursor)
        self.more_btn.setFixedSize(22, 28)
        self.more_btn.setStyleSheet(
            "QPushButton { background: #f5f5f5; border: 1px solid #ddd; border-radius: 6px;"
            " font-size: 11px; color: #666; }"
            "QPushButton:hover { background: #e8e8e8; border-color: #ccc; }"
            "QPushButton::menu-indicator { image: none; width: 0px; }"
        )
        more_menu = QMenu(self.more_btn)
        audio_action = more_menu.addAction(t("🎵 仅音频"))
        audio_action.triggered.connect(self._on_download_audio)
        copy_action = more_menu.addAction(t("📋 复制链接"))
        copy_action.triggered.connect(self._on_copy_clicked)
        as_action = more_menu.addAction(t("📁 下载为…"))
        as_action.triggered.connect(self._on_download_as)
        self.more_btn.setMenu(more_menu)
        actions_layout.addWidget(self.more_btn)
        
        actions_layout.addStretch()
        info_layout.addLayout(actions_layout)

        # 第四行：下载进度（对齐 VDH 的 media-downloading：进度条 + 百分比 + 速率 + 停止）
        self.progress_row = QWidget()
        self.progress_row.setFixedHeight(18)
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(6)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setStyleSheet(
            "QProgressBar { background: #eeeeee; border: none; border-radius: 3px; }"
            "QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            " stop:0 #667eea, stop:1 #764ba2); border-radius: 3px; }"
        )
        progress_layout.addWidget(self.progress_bar, 1)
        self.progress_percent = QLabel("0%")
        self.progress_percent.setStyleSheet("color: #667eea; font-size: 10px; font-weight: bold;")
        progress_layout.addWidget(self.progress_percent)
        self.progress_speed = QLabel("")
        self.progress_speed.setStyleSheet("color: #888; font-size: 10px;")
        progress_layout.addWidget(self.progress_speed)
        self.stop_btn = QPushButton(t("停止"))
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setFixedSize(44, 18)
        self.stop_btn.setStyleSheet(
            "QPushButton { background: #ffebee; color: #c62828; border: 1px solid #ffcdd2;"
            " border-radius: 4px; font-size: 10px; }"
            "QPushButton:hover { background: #ffcdd2; }"
        )
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        progress_layout.addWidget(self.stop_btn)
        self.progress_row.hide()
        info_layout.addWidget(self.progress_row)
        self._download_state = "discovered"

        self.main_layout.addLayout(info_layout)
        self._populate_variants(self.media_info.get("variants"))
        self._update_tooltips()

    def _start_stabilize_countdown(self):
        """启动3秒稳定倒计时，让链接稳定后再允许下载"""
        self._stabilize_remaining = 3
        self.download_btn.setEnabled(False)
        self.download_btn.setText(f"⬇️ 稳定中({self._stabilize_remaining}s)")
        self._stabilize_timer = QTimer(self)
        self._stabilize_timer.setInterval(1000)
        self._stabilize_timer.timeout.connect(self._on_stabilize_tick)
        self._stabilize_timer.start()

    def _on_stabilize_tick(self):
        """稳定倒计时每秒回调"""
        self._stabilize_remaining -= 1
        if self._stabilize_remaining <= 0:
            self._stabilize_timer.stop()
            self._stabilize_timer = None
            self.download_btn.setEnabled(True)
            self.download_btn.setText(t("⬇️ 下载"))
        else:
            self.download_btn.setText(f"⬇️ 稳定中({self._stabilize_remaining}s)")

    def _start_metadata_fetch(self):
        self.metadata_thread = MediaMetadataThread(self.media_info)
        self.metadata_thread.metadata_ready.connect(self._on_metadata_ready)
        self.metadata_thread.error_occurred.connect(self._on_metadata_error)
        self.metadata_thread.start()

    def _on_metadata_ready(self, data):
        self.title_label.setText(data.get('title', self.title_label.text()))
        
        # 加载缩略图
        thumb_url = data.get('thumbnail')
        if thumb_url:
            self._load_thumbnail(thumb_url)
        else:
            self._set_default_thumbnail()
            
        # 显示时长
        duration = data.get('duration_string')
        if duration:
            self.duration_label.setText(duration)
            self.duration_label.show()
            # 调整位置到右下角
            self.duration_label.move(
                self.thumb_container.width() - self.duration_label.sizeHint().width() - 4,
                self.thumb_container.height() - self.duration_label.sizeHint().height() - 4
            )
            self.duration_badge.setText(duration)
            self.duration_badge.show()
        # 有了时长后重新构造预览参数（VDH 按"中间位置"抓片段）
        try:
            self._refresh_preview_spec(duration=data.get('duration'))
        except Exception:
            pass

        # 设置平台图标
        webpage_url = data.get('webpage_url', '').lower()
        if 'youtube.com' in webpage_url or 'youtu.be' in webpage_url:
            self.platform_icon.setText("🔴") # YouTube 红色图标
            self.platform_icon.setStyleSheet("background: transparent; padding: 0px;")
            self.platform_icon.show()
        elif 'bilibili.com' in webpage_url:
            self.platform_icon.setText("📺")
            self.platform_icon.setStyleSheet("background: transparent; padding: 0px;")
            self.platform_icon.show()
        
        # 填充分辨率：已有"同一视频多格式"时优先用变体（VDH 一张卡多格式）
        if self._custom_variants:
            return
        self.res_combo.clear()
        self.res_combo.setEnabled(True)
        resolutions = data.get('resolutions', [])
        if resolutions:
            for res in resolutions:
                text = f"{res['note']} ({res['ext']})"
                if res.get('filesize'):
                    text += f" - {res['filesize']/(1024*1024):.1f}MB"
                self.res_combo.addItem(text, res['id'])
        else:
            self.res_combo.addItem(t("默认最佳质量"), "best")

    @staticmethod
    def _diagnose_metadata_error(error: Any) -> str:
        text = str(error or "").strip()
        if not text:
            return t("未知错误")
        low = text.lower()
        if "403" in low or "forbidden" in low:
            return t("权限受限（403），请先登录并提取 Cookie")
        if "401" in low or "unauthorized" in low:
            return t("未授权访问（401），请检查登录状态")
        if "404" in low or "not found" in low:
            return t("资源不存在或已下线（404）")
        if "429" in low or "too many requests" in low:
            return t("请求过于频繁（429），请稍后重试")
        if "drm" in low or "widevine" in low or "encrypted" in low:
            return t("内容可能受 DRM 保护")
        if "private" in low or "members only" in low:
            return t("内容受限（私有/会员）")
        if "geo" in low or "region" in low:
            return t("区域限制，当前网络不可访问")
        if "timeout" in low or "timed out" in low:
            return t("网络超时，请重试")
        return t("解析失败，请尝试直接链接或切换播放中条目")

    def _on_metadata_error(self, error):
        reason = self._diagnose_metadata_error(error)
        print(f"[DEBUG] 元数据获取失败: {error} | 诊断: {reason}")
        
        # 检查是否已有直接的媒体 URL（不是页面 URL）
        direct_url = self.media_info.get('url', '')
        page_url = self.media_info.get('page_url', '')
        
        # 如果 direct_url 不同于 page_url，说明已捕获到实际媒体链接
        has_direct_media = direct_url and direct_url != page_url
        
        self.res_combo.clear()
        
        if has_direct_media:
            # 已有直接链接，不显示"解析失败"
            # 显示文件类型和大小信息
            size_info = self.media_info.get('size', '')
            content_type = self.media_info.get('content_type', '')
            
            display_text = t("直接链接")
            if size_info:
                display_text += f" ({size_info})"
            elif content_type:
                # 提取简短的类型名
                ct_short = content_type.split('/')[-1].split(';')[0].upper()
                display_text += f" ({ct_short})"
            
            self.res_combo.addItem(display_text, "direct")
            self.res_combo.setToolTip(t_format("元数据解析失败：{}。已回退为直接链接下载。", reason))
            
            # 尝试加载缩略图：优先使用 JS 提取的页面缩略图
            thumbnail_url = self.media_info.get('thumbnail', '')
            if thumbnail_url:
                self._load_thumbnail(thumbnail_url)
            else:
                # 没有页面缩略图，使用默认占位图
                self._set_default_thumbnail()
        else:
            # 没有直接链接，显示原有的错误提示
            self.res_combo.addItem(t_format("解析失败：{}", reason))
            self.res_combo.addItem(t("默认最佳质量"), "best")
            self.res_combo.setToolTip(t_format("元数据解析失败：{}", reason))
        
        self.res_combo.setEnabled(True)
    
    def _set_default_thumbnail(self):
        """设置默认的视频占位图 - 现代美化版"""
        width = self.thumb_container.width() if hasattr(self, "thumb_container") else 160
        height = self.thumb_container.height() if hasattr(self, "thumb_container") else 90
        # 创建一个渐变背景的占位图
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 现代渐变背景 (深蓝紫色调)
        gradient = QLinearGradient(0, 0, width, height)
        gradient.setColorAt(0, QColor(45, 52, 71))    # 深蓝灰
        gradient.setColorAt(1, QColor(28, 32, 48))    # 更深的蓝黑
        painter.setBrush(gradient)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, width, height, 8, 8)
        
        # 绘制磨砂质感的播放按钮背景
        cx, cy = width // 2, height // 2 - 4
        painter.setBrush(QColor(255, 255, 255, 30))
        painter.drawEllipse(QPoint(cx, cy), 18, 18)
        
        # 绘制精致的播放按钮三角形
        painter.setBrush(QColor(255, 255, 255, 200))
        triangle = QPolygon([
            QPoint(cx - 4, cy - 8),
            QPoint(cx - 4, cy + 8),
            QPoint(cx + 8, cy)
        ])
        painter.drawPolygon(triangle)
        
        # 文字提示 - 使用更现代的排版
        painter.setPen(QColor(150, 160, 180))
        font = painter.font()
        font.setFamily("Segoe UI")
        font.setPixelSize(11)
        font.setWeight(QFont.Medium)
        painter.setFont(font)
        painter.drawText(0, height - 26, width, 20, Qt.AlignCenter, "Media Preview")
        
        painter.end()
        self.thumb_label.setPixmap(pixmap)

    def _load_thumbnail(self, url):
        request = QNetworkRequest(QUrl(url))
        self.reply = self.network_manager.get(request)
        self.reply.finished.connect(self._on_thumbnail_loaded)

    def _on_thumbnail_loaded(self):
        if self.reply.error() == QNetworkReply.NoError:
            data = self.reply.readAll()
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            if not pixmap.isNull():
                # 缩放图片以适应容器同时保持宽高比
                scaled_pixmap = pixmap.scaled(
                    160, 90, 
                    Qt.KeepAspectRatio, 
                    Qt.SmoothTransformation
                )
                self.thumb_label.setPixmap(scaled_pixmap)
            else:
                # 图片数据无效，回退到默认占位图
                self._set_default_thumbnail()
        else:
            # 加载失败，回退到默认占位图
            self._set_default_thumbnail()
        self.reply.deleteLater()

    def _on_download_audio(self):
        """仅音频：用页面 URL + bestaudio（主窗口媒体捕获路径会把该值当 -f 传给 yt-dlp）。"""
        url = str(self.media_info.get("url") or "")
        page_url = str(self.media_info.get("page_url") or "")
        if not page_url or page_url == url:
            ModernToast.show_message(t("该条目只有直链，无法仅提取音频"), self, is_error=True)
            return
        self.send_to_downloader.emit(page_url, f"{self.title_label.text()} [Format:bestaudio]")
        self.set_download_state("downloading", t("仅音频"))

    def _on_download_as(self):
        """下载为…：交给主窗口选择目录后开始下载。"""
        self.send_download_as.emit(str(self.media_info.get("url") or ""), self.title_label.text())

    def _on_stop_clicked(self):
        """停止按钮：请求主窗口取消当前下载（按 URL 定位）。"""
        url = str(self.media_info.get("url") or "")
        self.stop_requested.emit(url)

    def set_download_state(self, state: str, message: str = ""):
        """切换下载状态：discovered / downloading / downloaded / failed（对齐 VDH 三态）。"""
        self._download_state = state
        row = getattr(self, "progress_row", None)
        if row is None:
            return
        if state == "downloading":
            row.show()
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setValue(0)
            self.progress_percent.setText(t("中"))
            self.progress_speed.setText(message or "")
            self.stop_btn.show()
            self.download_btn.setEnabled(False)
        elif state == "downloaded":
            row.hide()
            self.download_btn.setEnabled(True)
            self.download_btn.setText(t("✅ 已发送"))
            self.download_btn.setToolTip(message or t("已发送到下载器") )
        elif state == "failed":
            row.hide()
            self.download_btn.setEnabled(True)
            self.download_btn.setText(t("⬇️ 重试"))
            self.download_btn.setToolTip(message or t("上一次下载失败"))
        else:
            row.hide()
            self.download_btn.setText(t("⬇️ 下载"))

    def update_download_progress(self, percent=None, speed_text: str = ""):
        """接收主窗口的下载进度（VDH div_progressbar + span_percent + span_bitrate）。"""
        row = getattr(self, "progress_row", None)
        if row is None:
            return
        if self._download_state != "downloading":
            self.set_download_state("downloading", speed_text)
        else:
            row.show()
        if isinstance(percent, (int, float)) and percent >= 0:
            self.progress_bar.setRange(0, 100)
            value = max(0, min(100, int(percent)))
            self.progress_bar.setValue(value)
            self.progress_percent.setText(f"{value}%")
        else:
            self.progress_bar.setRange(0, 0)
            self.progress_percent.setText(t("中"))
        if speed_text:
            self.progress_speed.setText(speed_text)

    def _on_download_clicked(self):
        # VDH 的 com-media-selector：用户选中的变体（同一视频多格式）直接按该直链下载
        data = self.res_combo.currentData()
        variants = getattr(self, "_custom_variants", [])
        if isinstance(data, int) and 0 <= data < len(variants):
            variant_url = str(variants[data].get("url") or "")
            if variant_url:
                self.send_to_downloader.emit(variant_url, self.title_label.text())
                self.set_download_state("downloading", t("已发送到下载器"))
                return
        format_id = self.res_combo.currentData() or "best"
        title = self.title_label.text()
        direct_url = self.media_info.get('url', '')
        page_url = self.media_info.get('page_url', '')
        has_direct_media = bool(direct_url and direct_url != page_url)
        
        media_type = self.media_info.get("type")
        is_major_platform = media_type in ("Douyin", "Bilibili", "YouTube", "TikTok")
        
        prefer_direct = bool(
            has_direct_media and not is_major_platform and (
                format_id in ("best", None, "")
                or self.media_info.get("is_playing")
                or (
                    self.media_info.get("type") in ("HLS", "DASH", "Smooth", "MP4", "Video")
                    and not self.media_info.get("is_preload")
                )
            )
        )

        if self.media_info.get("has_drm"):
            ModernToast.show_message(t("检测到 DRM 保护，下载可能失败"), self, is_error=True)
        elif self.media_info.get("is_preload") and not self.media_info.get("is_playing"):
            ModernToast.show_message(t("当前可能是预加载变体，若失败请改选“播放中”条目"), self, is_error=True)
        
        # 如果已经捕获到稳定媒体直链，优先直接下载，避免点得过快时仍回退到页面 URL。
        if format_id == "direct" or prefer_direct:
            url = direct_url
            if not url:
                ModernToast.show_message(t("未找到可用媒体链接，请刷新页面后重试"), self, is_error=True)
                return
            self.send_to_downloader.emit(url, title)
        else:
            # 否则使用页面 URL（让 yt-dlp 处理）
            url = page_url or direct_url
            if not url:
                ModernToast.show_message(t("未找到可用页面链接，请刷新页面后重试"), self, is_error=True)
                return
            # 如果有格式 ID，附加到标题
            if format_id and format_id != "best":
                self.send_to_downloader.emit(url, f"{title} [Format:{format_id}]")
            else:
                self.send_to_downloader.emit(url, title)
        self.set_download_state("downloading", t("已发送到下载器"))


    def _on_copy_clicked(self):
        """复制媒体链接到剪贴板"""
        media_type = self.media_info.get("type")
        is_major_platform = media_type in ("Douyin", "Bilibili", "YouTube", "TikTok")
        
        # 对于主流视音频分离平台，复制时优先使用页面详情页 URL (包含音频及完整属性)，其他普通平台优先使用直链。
        if is_major_platform:
            url = self.media_info.get('page_url') or self.media_info.get('url')
        else:
            url = self.media_info.get('url') or self.media_info.get('page_url')
            
        if url:
            from PyQt5.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText(url)
            # 显示复制成功提示
            ModernToast.show_message(t("✅ 链接已复制"), self)

# ============ 媒体捕获面板 ============

class MediaCaptureWindow(QMainWindow):
    """媒体捕获浮窗 - 现代极简风格"""
    
    send_to_downloader = pyqtSignal(str, str)  # 发送 URL 和标题到下载器
    stop_requested = pyqtSignal(str)  # 请求停止下载（携带媒体 URL）
    download_as_requested = pyqtSignal(str, str)  # 「下载为…」
    closed = pyqtSignal()  # 窗口关闭信号
    
    def __init__(self, controller=None):
        # 使用独立顶层窗口，避免跟随主窗口最小化。
        super().__init__(None)
        self._controller = controller
        self.setWindowTitle(t("媒体捕获"))
        self.setMinimumSize(400, 500)
        self.resize(450, 600)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        
        self._media_list = []  # 存储捕获的媒体信息
        self._media_cards = {}  # key -> MediaCard
        self._setup_ui()
        self._apply_styles()

    def _enforce_topmost(self):
        """确保媒体捕获窗口持续置顶。"""
        self.raise_()
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(self.winId())
                HWND_TOPMOST = -1
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOACTIVATE = 0x0010
                SWP_SHOWWINDOW = 0x0040
                ctypes.windll.user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
            except Exception:
                pass

    def showEvent(self, event):
        super().showEvent(event)
        self._enforce_topmost()
    
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 标题栏
        header = QFrame()
        header.setObjectName("mediaPanelHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        
        title_icon = QLabel("📡")
        title_icon.setStyleSheet("font-size: 18px;")
        header_layout.addWidget(title_icon)
        
        title = QLabel(t("媒体捕获"))
        title.setStyleSheet("font-size: 14px; font-weight: 600; color: #333;")
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        self.count_label = QLabel(t("0 个媒体"))
        self.count_label.setStyleSheet("font-size: 12px; color: #888;")
        header_layout.addWidget(self.count_label)
        
        # 清空按钮
        clear_btn = QPushButton(t("清空"))
        clear_btn.setObjectName("clearBtn")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_media)
        header_layout.addWidget(clear_btn)
        
        layout.addWidget(header)
        
        # 顶部提示说明
        tip_label = QLabel(
            t("💡 如果没有捕捉到正在播放的视频，请 <b><font color='#e53935'>【清空】</font></b> 后重新播放视频。<br>") + t("（遇到下载失败请登陆后再次尝试）")
        )
        tip_label.setWordWrap(True)
        tip_label.setStyleSheet("""
            QLabel {
                background-color: #fff9e6;
                color: #856404;
                padding: 10px 16px;
                border-bottom: 1px solid #ffeeba;
                font-size: 12px;
                line-height: 16px;
            }
        """)
        layout.addWidget(tip_label)
        
        # 媒体列表区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("mediaScroll")
        
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(8, 8, 8, 8)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch()
        
        scroll.setWidget(self.list_container)
        layout.addWidget(scroll)
        
        # 空状态提示
        self.empty_label = QLabel(t("浏览网页时，检测到的媒体将显示在这里\n\n提示：如果开启后没有看到媒体，请尝试刷新网页 🔄"))
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.list_layout.insertWidget(0, self.empty_label)
    
    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background: #fafafa;
            }
            #mediaPanelHeader {
                background: white;
                border-bottom: 1px solid #e8e8e8;
            }
            #clearBtn {
                background: transparent;
                border: 1px solid #ddd;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 12px;
                color: #666;
            }
            #clearBtn:hover {
                background: #f5f5f5;
                border-color: #ccc;
            }
            #mediaScroll {
                border: none;
                background: transparent;
            }
            #emptyLabel {
                color: #aaa;
                font-size: 13px;
                padding: 40px 20px;
            }
            .mediaCard {
                background: white;
                border: 1px solid #e8e8e8;
                border-radius: 10px;
            }
            .mediaCard:hover {
                border-color: #ccc;
                background: #fcfcfc;
            }
        """)

    @staticmethod
    def _media_key(url: str) -> str:
        return _normalize_capture_url_key(url)

    @staticmethod
    def _merge_media_info(old_info: dict, new_info: dict) -> dict:
        merged = dict(old_info or {})
        for key in (
            "type", "url", "size", "content_type", "title", "page_url", "thumbnail", "source",
            "quality", "has_drm", "headers", "summary", "audio_only", "is_manifest", "url_key"
        ):
            val = new_info.get(key)
            if val not in (None, "", {}, []):
                merged[key] = val

        old_playing = bool(old_info.get("is_playing")) if old_info else False
        new_playing = bool(new_info.get("is_playing"))
        merged["is_playing"] = old_playing or new_playing
        merged["is_preload"] = False if merged["is_playing"] else bool(new_info.get("is_preload", False))
        merged["force_update"] = bool(new_info.get("force_update", False))

        # 同一视频的多个格式：合成一张卡片（对齐 VDH 的 com-media-selector）
        variants = []
        for source in (
            old_info.get("variants") if isinstance(old_info, dict) else None,
            new_info.get("variants"),
        ):
            if isinstance(source, list):
                variants.extend(v for v in source if isinstance(v, dict) and v.get("url"))
        for source_info in (old_info, new_info):
            if not isinstance(source_info, dict):
                continue
            source_url = str(source_info.get("url") or "")
            source_page = str(source_info.get("page_url") or "")
            if not source_url or source_url == source_page:
                continue
            quality = source_info.get("quality") if isinstance(source_info.get("quality"), dict) else {}
            variants.append({
                "url": source_url,
                "quality": str(quality.get("resolution") or ""),
                "ext": str(quality.get("ext") or source_info.get("ext") or ""),
                "size": str(source_info.get("size") or ""),
                "headers": source_info.get("headers") or {},
            })
        deduped = {}
        for variant in variants:
            vkey = _normalize_capture_url_key(variant.get("url"))
            if vkey:
                deduped.setdefault(vkey, variant)
        if len(deduped) >= 2:
            merged["variants"] = list(deduped.values())
        return merged

    def _insert_card_by_priority(self, card: QWidget, media_info: dict):
        is_playing = bool(media_info.get('is_playing', False))
        is_preload = bool(media_info.get('is_preload', False))

        if is_playing:
            insert_pos = 0
        elif is_preload:
            insert_pos = 0
            for i in range(self.list_layout.count()):
                item = self.list_layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), MediaCard):
                    if item.widget().media_info.get('is_playing'):
                        insert_pos = i + 1
                    else:
                        break
        else:
            insert_pos = 0
        self.list_layout.insertWidget(insert_pos, card)
    
    def _find_same_video_card(self, media_info: dict):
        """找到"同名 + 同来源页"的直链卡片，用于把多格式合并到一张卡。"""
        title = str(media_info.get("title") or "").strip()
        page_url = str(media_info.get("page_url") or "").strip()
        url = str(media_info.get("url") or "").strip()
        if not title or not page_url or url == page_url:
            return None
        for card in self._media_cards.values():
            info = getattr(card, "media_info", {}) or {}
            if info.get("is_playing"):
                continue
            if str(info.get("title") or "").strip() != title:
                continue
            if str(info.get("page_url") or "").strip() != page_url:
                continue
            if str(info.get("url") or "").strip() == page_url:
                continue
            return card
        return None

    def add_media(self, media_info: dict):
        """添加一个捕获到的媒体"""
        url = media_info.get("url", "")
        key = self._media_key(url)
        if not key:
            return

        existing_card = self._media_cards.get(key)
        if existing_card is None:
            # 同名同页的直链视为同一视频的不同格式：合并成一张卡片（VDH 的一张卡多格式）
            same_video_card = self._find_same_video_card(media_info)
            if same_video_card is not None:
                existing_card = same_video_card
                key = self._media_key(same_video_card.media_info.get("url"))
        if existing_card:
            old_info = existing_card.media_info
            merged_info = self._merge_media_info(old_info, media_info)

            # 仅在关键状态变化时重建卡片，避免重复触发大量元数据线程
            became_playing = bool(merged_info.get("is_playing")) and not bool(old_info.get("is_playing"))
            forced = bool(media_info.get("force_update"))
            if not (became_playing or forced):
                existing_card.update_media_info(merged_info)
                for idx, info in enumerate(self._media_list):
                    if self._media_key(info.get("url")) == key:
                        self._media_list[idx] = merged_info
                        break
                return

            # 删除旧卡片并用新状态重建，确保“播放中/预加载/DRM”标签即时刷新
            self.list_layout.removeWidget(existing_card)
            existing_card.cleanup()
            existing_card.deleteLater()
            self._media_cards.pop(key, None)
            replaced = False
            for idx, info in enumerate(self._media_list):
                if self._media_key(info.get("url")) == key:
                    self._media_list[idx] = merged_info
                    replaced = True
                    break
            if not replaced:
                self._media_list.append(merged_info)
            media_info = merged_info
        else:
            self._media_list.append(media_info)
        
        # 隐藏空状态提示
        self.empty_label.hide()
        
        # 创建媒体卡片
        card = MediaCard(media_info)
        card.send_to_downloader.connect(self.send_to_downloader.emit)
        card.remove_requested.connect(self._remove_media_card)
        card.stop_requested.connect(self.stop_requested.emit)
        card.send_download_as.connect(self.download_as_requested.emit)
        self._media_cards[key] = card
        
        # 智能排序插入：播放中优先
        self._insert_card_by_priority(card, media_info)
        
        # 更新计数
        self.count_label.setText(f"{len(self._media_list)} 个媒体")

    def _remove_media_card(self, card):
        """移除单个媒体卡片"""
        key = self._media_key(card.media_info.get("url"))
        if key:
            self._media_cards.pop(key, None)
        # 找到对应的 info 并移除
        for info in self._media_list:
            if self._media_key(info.get('url')) == key:
                self._media_list.remove(info)
                break
        
        card.cleanup()
        card.deleteLater()
        self.count_label.setText(f"{len(self._media_list)} 个媒体")
        if not self._media_list:
            self.empty_label.show()

    def _clear_media(self):
        """清空所有媒体"""
        self._media_list.clear()
        self._media_cards.clear()
        # 移除所有卡片
        for i in reversed(range(self.list_layout.count())):
            item = self.list_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if isinstance(widget, MediaCard):
                    widget.cleanup()
                    widget.deleteLater()
        
        self.count_label.setText(t("0 个媒体"))
        self.empty_label.show()

        # 通知子进程清空去重集合
        if self._controller and hasattr(self._controller, "_send_command"):
            self._controller._send_command("CLEAR_CAPTURED")
            return

        # 兼容回退：若仍作为子窗口创建，沿父链查找命令发送者
        parent = self.parentWidget()
        while parent:
            if hasattr(parent, "_send_command"):
                parent._send_command("CLEAR_CAPTURED")
                break
            parent = parent.parentWidget()

    def get_media_snapshot(self):
        """返回当前已捕获媒体的快照，供下载器复用，避免重复启动浏览器嗅探。"""
        return [dict(info) for info in self._media_list]

    def _card_for_url(self, url: str):
        """按 URL 找卡片：先精确匹配 key，再在格式变体里找。"""
        if not url:
            return None
        key = self._media_key(url)
        card = self._media_cards.get(key)
        if card is not None:
            return card
        normalized = _normalize_capture_url_key(url)
        for candidate in self._media_cards.values():
            info = getattr(candidate, "media_info", {}) or {}
            if _normalize_capture_url_key(info.get("url")) == normalized:
                return candidate
            for variant in info.get("variants") or []:
                if isinstance(variant, dict) and \
                        _normalize_capture_url_key(variant.get("url")) == normalized:
                    return candidate
        return None

    def update_media_progress(self, url: str, percent=None, speed_text: str = ""):
        """把主窗口下载进度映射到卡片（VDH media-downloading 状态）。"""
        card = self._card_for_url(url)
        if card is not None:
            card.update_download_progress(percent, speed_text)

    def mark_media_downloaded(self, url: str, success: bool = True, message: str = ""):
        """下载结束：切换卡片到已下载/失败状态。"""
        card = self._card_for_url(url)
        if card is not None:
            card.set_download_state("downloaded" if success else "failed", message)

    def closeEvent(self, event):
        """窗口关闭事件"""
        self._clear_media()
        self.closed.emit()
        super().closeEvent(event)


class BrowserSelector(QFrame):
    """极简美观的浏览器选择器组件 - 折叠式设计"""
    
    browser_changed = pyqtSignal(str)  # 发出浏览器 ID
    
    def __init__(self, browsers: list, selected_browser: str = '', parent=None):
        super().__init__(parent)
        self.browsers = browsers
        self._selected_browser = selected_browser
        self._cards = []
        self._expanded = False
        
        self._setup_ui()
        self._apply_style()
        
    def _setup_ui(self):
        """设置布局"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # === 折叠按钮(显示当前选中的浏览器) ===
        self.toggle_btn = QPushButton()
        self.toggle_btn.setObjectName("browserToggleBtn")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setFixedHeight(52)
        self.toggle_btn.clicked.connect(self._toggle_expand)
        
        toggle_layout = QHBoxLayout(self.toggle_btn)
        toggle_layout.setContentsMargins(16, 8, 16, 8)
        toggle_layout.setSpacing(12)
        
        # 当前选中的浏览器图标
        self.current_icon = QLabel()
        self.current_icon.setFixedSize(32, 32)
        self.current_icon.setAlignment(Qt.AlignCenter)
        toggle_layout.addWidget(self.current_icon)
        
        # 当前选中的浏览器名称
        self.current_name = QLabel()
        self.current_name.setStyleSheet("""
            font-size: 14px;
            font-weight: 500;
            color: #555;
            background: transparent;
        """)
        toggle_layout.addWidget(self.current_name)
        
        toggle_layout.addStretch()
        
        # 展开/收起箭头
        self.arrow_label = QLabel("▾")
        self.arrow_label.setStyleSheet("""
            font-size: 14px;
            color: #aaa;
            background: transparent;
        """)
        toggle_layout.addWidget(self.arrow_label)
        
        main_layout.addWidget(self.toggle_btn)
        
        # === 展开的卡片容器 ===
        self.cards_container = QFrame()
        self.cards_container.setObjectName("browserCardsContainer")
        self.cards_container.setVisible(False)  # 默认隐藏
        
        # 使用水平 QScrollArea 承载浏览器卡片，防止超宽错位
        cards_scroll = QScrollArea(self.cards_container)
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        cards_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cards_scroll.setObjectName("cardsScroll")
        cards_scroll.setFixedHeight(120)  # 12px margins * 2 + 90px card height + 6px scrollbar = ~120px
        
        cards_content = QWidget()
        cards_content.setObjectName("cardsContent")
        cards_scroll.setWidget(cards_content)
        
        cards_layout = QHBoxLayout(cards_content)
        cards_layout.setContentsMargins(12, 12, 12, 12)
        cards_layout.setSpacing(10)
        
        # 创建浏览器卡片
        for browser in self.browsers:
            card = BrowserCard(browser, self)
            card.clicked.connect(lambda checked, b=browser['id']: self._on_card_clicked(b))
            
            # 设置初始选中状态
            if browser['id'] == self._selected_browser:
                card.setSelected(True)
            
            # 添加柔和阴影效果
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(10)
            shadow.setColor(QColor(0, 0, 0, 15))
            shadow.setOffset(0, 2)
            card.setGraphicsEffect(shadow)
            
            cards_layout.addWidget(card)
            self._cards.append(card)
        
        cards_layout.addStretch()
        
        container_layout = QVBoxLayout(self.cards_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addWidget(cards_scroll)
        
        main_layout.addWidget(self.cards_container)
        
        # 初始化显示当前选中的浏览器
        self._update_toggle_display()
    
    def _update_toggle_display(self):
        """更新折叠按钮显示的内容"""
        # 找到当前选中的浏览器信息
        current_browser = None
        for b in self.browsers:
            if b['id'] == self._selected_browser:
                current_browser = b
                break
        
        if not current_browser:
            current_browser = self.browsers[0] if self.browsers else {'id': 'browser', 'name': t('未检测到浏览器')}

        theme = _get_browser_theme(current_browser['id'], current_browser['name'])
        self.current_icon.setText(theme['letter'])
        self.current_icon.setStyleSheet(f"""
            QLabel {{
                background: {theme['icon_bg']};
                color: white;
                font-size: 14px;
                font-weight: 600;
                font-family: 'Segoe UI', sans-serif;
                border-radius: 16px;
                border: none;
            }}
        """)
        
        # 更新名称
        name_map = {'chrome': 'Chrome', 'edge': 'Edge', 'firefox': 'Firefox'}
        self.current_name.setText(name_map.get(current_browser['id'], current_browser['name']))
    
    def _toggle_expand(self):
        """切换展开/收起状态"""
        self._expanded = not self._expanded
        self.cards_container.setVisible(self._expanded)
        self.arrow_label.setText("▴" if self._expanded else "▾")
        
        # 更新样式 - 丝滑的边缘衔接
        if self._expanded:
            self.toggle_btn.setStyleSheet("""
                #browserToggleBtn {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #fffcfa, stop:1 #fff6f2);
                    border: 1px solid rgba(240, 200, 180, 0.4);
                    border-bottom: none;
                    border-top-left-radius: 18px;
                    border-top-right-radius: 18px;
                    border-bottom-left-radius: 0px;
                    border-bottom-right-radius: 0px;
                }
                #browserToggleBtn:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #fff8f5, stop:1 #fff2ed);
                }
            """)
        else:
            self._apply_toggle_style()
    
    def _apply_toggle_style(self):
        """应用折叠按钮默认样式 - 丝滑边缘"""
        self.toggle_btn.setStyleSheet("""
            #browserToggleBtn {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffffff, stop:1 #fcfcfc);
                border: 1px solid rgba(235, 235, 235, 0.9);
                border-radius: 18px;
            }
            #browserToggleBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #fffcfa, stop:1 #fff8f5);
                border: 1px solid rgba(240, 200, 180, 0.5);
            }
        """)
    
    def _apply_style(self):
        """应用容器样式 - 丝滑边缘"""
        self._apply_toggle_style()
        self.setStyleSheet("""
            BrowserSelector {
                background: transparent;
            }
            #browserCardsContainer {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 250, 248, 0.99), stop:1 rgba(255, 246, 242, 0.99));
                border: 1px solid rgba(240, 200, 180, 0.4);
                border-top: none;
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                border-bottom-left-radius: 18px;
                border-bottom-right-radius: 18px;
                margin-top: 0px;
            }
            #cardsScroll {
                border: none;
                background: transparent;
            }
            #cardsContent {
                background: transparent;
            }
            QScrollBar:horizontal {
                border: none;
                background: transparent;
                height: 6px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(180, 195, 220, 0.45);
                min-width: 30px;
                border-radius: 3px;
            }
            QScrollBar::handle:horizontal:hover {
                background: rgba(140, 160, 195, 0.65);
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: transparent;
            }
        """)
    
    def _on_card_clicked(self, browser_id: str):
        """处理卡片点击"""
        self._selected_browser = browser_id
        
        # 更新所有卡片状态
        for card in self._cards:
            card.setSelected(card.browser_id == browser_id)
        
        # 更新折叠按钮显示
        self._update_toggle_display()
        
        # 收起选择器
        self._expanded = False
        self.cards_container.setVisible(False)
        self.arrow_label.setText("▾")
        self._apply_toggle_style()
        
        self.browser_changed.emit(browser_id)
    
    def selectedBrowser(self) -> str:
        return self._selected_browser
    
    def setSelectedBrowser(self, browser_id: str):
        """设置选中的浏览器"""
        self._selected_browser = browser_id
        for card in self._cards:
            card.setSelected(card.browser_id == browser_id)
        self._update_toggle_display()
    
    def setEnabled(self, enabled: bool):
        """启用/禁用选择器"""
        super().setEnabled(enabled)
        self.toggle_btn.setEnabled(enabled)
        for card in self._cards:
            card.setEnabled(enabled)
            if not enabled:
                card.setCursor(Qt.ForbiddenCursor)
            else:
                card.setCursor(Qt.PointingHandCursor)
        
        if not enabled:
            self.toggle_btn.setCursor(Qt.ForbiddenCursor)
            # 禁用时收起
            self._expanded = False
            self.cards_container.setVisible(False)
            self.arrow_label.setText("▾")
        else:
            self.toggle_btn.setCursor(Qt.PointingHandCursor)


# ============ 浏览器启动脚本 ============

SNIFFER_JS = r'''
(function() {
    if (window.__pro_media_sniffer_v4) return;
    window.__pro_media_sniffer_v4 = true;

    // 专业级媒体嗅探器 v4.3 - 抖音增强版
    // 新增：抖音标题提取、预加载标记、播放状态跟踪
    
    const DEBUG = false;
    
    let reportMediaReady = false;
    const pendingMedia = [];
    let retryCount = 0;
    const MAX_RETRIES = 10;
    const RETRY_INTERVAL = 200;
    
    const waitForReportMedia = () => {
        if (typeof window.reportMedia === 'function') {
            reportMediaReady = true;
            pendingMedia.forEach(data => window.reportMedia(data).catch(() => {}));
            pendingMedia.length = 0;
        } else if (retryCount < MAX_RETRIES) {
            retryCount++;
            setTimeout(waitForReportMedia, RETRY_INTERVAL);
        }
    };
    waitForReportMedia();
    
    const MEDIA_SIGNATURES = {
        manifests: ['.m3u8', '.mpd', 'playlist.m3u8', 'master.m3u8', 'index.m3u8', '.ism/manifest', 'dash.mpd'],
        completeVideoExts: ['.mp4', '.webm', '.flv', '.mkv', '.mov', '.avi', '.f4v', '.3gp', '.wmv', '.asf', '.rm', '.rmvb', '.m4v'],
        fragmentExts: ['.ts', '.m4s', '.m4a', '.m4v', '.aac', '.mp3'],
        cdnPatterns: [
            'googlevideo.com', 'videoplayback',
            'bilivideo.com', 'bilivideo.cn', 'biliapi.net',
            'upos-sz-', 'upos-hz-', 'upos-', '/upgcxcode/',
            'video.twimg.com',
            'tiktokcdn.com', 'musical.ly', 'byteoversea', 'bytecdn',
            // 抖音 CDN
            'douyinvod.com', 'douyincdn.com', 'douyinpic.com', 'tos-cn-v',
            'cdninstagram.com', 'fbvideo',
            'phncdn.com', 'xvideos-cdn.com', 'dmcdn.net',
            'vod-progressive', 'vimeocdn.com', 'player.vimeo',
            'jwpcdn.com', 'bcove.video', 'cloudflarestream.com',
            'stream.mux.com', 'embedwistia', 'play.vidyard',
            'sb-cd.com', 'spankbang', 'xhcdn.com', 'xhamster'
        ],
        excludePatterns: new Set([
            'googleads', 'doubleclick', 'googlesyndication', 'adservice', 'adserver',
            'pagead', 'pubads', 'smartadserver', 'rubiconproject', 'adsystem',
            'moatads', 'outbrain', 'taboola', 'criteo', 'prebid',
            'adtech', 'adnxs', 'advertising', 'adform', 'mediavine', 'teads',
            'adsafeprotected', 'imasdk.googleapis', 'securepubads',
            'analytics', 'tracking', 'beacon', 'pixel', 'telemetry', 'metrics',
            'google-analytics', 'facebook.com/tr', 'connect.facebook',
            'data.bilibili.com/log', 'log-reporter',
            'thumbnail', 'poster', 'sprite', 'preview', 'thumb', 'cover', 'avatar',
            '/pbp/', '.cnf', '/x/player/', '/x/web-interface/', 'api.bilibili.com',
            '.webmask', 'cmaskboss', 'videoshotpvhdboss', '.bin', 'hdslb.com/bfs',
            'webmanifest', 'manifest.json',
            'heartbeat', 'ping', 'alive', 'health', 'status',
            'playhubconnect.com', 'exosrv.com', 'exoclick.com', 'juicyads.com'
        ]),
        staticExts: new Set(['.gif', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.ico', '.css', '.js', '.woff', '.woff2', '.ttf', '.eot', '.bin', '.webmask'])
    };

    const capturedUrls = new Map();
    const playingVideos = new Map();
    const recentMediaRequests = [];
    const drmDetected = new Set();
    // 【抖音增强】绑定 video 元素到其对应的 CDN URL（解决 blob: URL 无法匹配的问题）
    const videoBoundCdnUrl = new WeakMap();
    let pageThumbnail = '';
    let currentPlayingUrl = null;
    let activeVideoElement = null;
    let lastPlaybackReportAt = 0;

    window.__pro_media_sniffer_clear_captured = () => {
        try {
            capturedUrls.clear();
            playingVideos.clear();
            drmDetected.clear();
            lastPlaybackReportAt = 0;
            return true;
        } catch (e) {
            return false;
        }
    };

    window.__pro_media_sniffer_refresh_capture = () => {
        try {
            updateThumbnail();
            document.querySelectorAll('video').forEach(attachVideoListeners);
            const videos = Array.from(document.querySelectorAll('video'));
            const activeVideo = activeVideoElement || videos.find(video =>
                video && !video.paused && (video.currentTime > 0 || video.readyState >= 2)
            );

            if (activeVideo) {
                activeVideoElement = activeVideo;
                const src = getVideoLikelyUrl(activeVideo);
                if (src && !isExcluded(src)) {
                    currentPlayingUrl = src;
                    reportMedia({
                        url: src,
                        source: 'manual_refresh',
                        isPlaying: true,
                        activeVideo: true,
                        forceUpdate: true
                    });
                } else if (!src) {
                    // MSE/blob 场景拿不到直链：兜底上报 YouTube 页面 URL
                    reportYoutubePageFallback('manual_refresh_fallback');
                }
            }

            const recent = pickRecentPlayableUrl();
            if (recent && recent.url && !isExcluded(recent.url)) {
                reportMedia({
                    url: recent.url,
                    contentType: recent.contentType || '',
                    size: recent.size || '',
                    source: recent.source || 'manual_refresh_recent',
                    isPlaying: Boolean(activeVideo),
                    activeVideo: Boolean(activeVideo),
                    forceUpdate: Boolean(activeVideo)
                });
            }

            scanPlayingVideos();
            return true;
        } catch (e) {
            return false;
        }
    };
    
    const updateThumbnail = () => {
        const ogImage = document.querySelector('meta[property="og:image"]');
        if (ogImage) pageThumbnail = ogImage.content || '';
    };
    updateThumbnail();
    
    // 寻找与指定 URL 关联的 video 元素
    const findVideoElementByUrl = (url) => {
        if (!url) return null;
        const urlKey = getDedupeKey(url);
        if (!urlKey) return null;
        const videos = document.querySelectorAll('video');
        for (const video of videos) {
            const src = video.currentSrc || video.src || '';
            if (src && getDedupeKey(src) === urlKey) {
                return video;
            }
            const bound = videoBoundCdnUrl.get(video);
            if (bound && getDedupeKey(bound) === urlKey) {
                return video;
            }
        }
        return null;
    };

    // 抖音专用标题提取
    const getDouyinTitle = (url) => {
        const douyinSelectors = [
            '[data-e2e="video-desc"]',
            '.video-info-detail .title',
            '.xgplayer-title',
            '.video-card.active .title',
            '.author-card .title',
            '.video-info .title',
            '.video-desc',
            '.desc-container'
        ];
        
        // 1. 如果有传入 URL，尝试通过 URL 匹配对应的 video 元素，并在该 video 元素的父级容器内查找标题
        if (url) {
            const video = findVideoElementByUrl(url);
            if (video) {
                let curr = video.parentElement;
                while (curr && curr !== document.body && curr !== document.documentElement) {
                    for (const selector of douyinSelectors) {
                        try {
                            const el = curr.querySelector(selector);
                            if (el && el.textContent) {
                                const text = el.textContent.trim().replace(/\s+/g, ' ');
                                if (text.length > 2 && text.length < 200 && text !== '抖音-记录美好生活') {
                                    return text;
                                }
                            }
                        } catch (e) {}
                    }
                    curr = curr.parentElement;
                }
            }
        }
        
        // 2. 如果没有 URL 或者匹配失败，尝试寻找当前处于视口中心的主播放视频元素，在其父级容器内查找标题
        const activeVideo = findDouyinActiveVideo();
        if (activeVideo) {
            let curr = activeVideo.parentElement;
            while (curr && curr !== document.body && curr !== document.documentElement) {
                for (const selector of douyinSelectors) {
                    try {
                        const el = curr.querySelector(selector);
                        if (el && el.textContent) {
                            const text = el.textContent.trim().replace(/\s+/g, ' ');
                            if (text.length > 2 && text.length < 200 && text !== '抖音-记录美好生活') {
                                return text;
                            }
                        }
                    } catch (e) {}
                }
                curr = curr.parentElement;
            }
        }

        // 3. 兜底：全局查询，但将最具体/最容易匹配当前页面的 active 类的选择器排在最前面
        const fallbackSelectors = [
            '.slider-video.swiper-slide-active [data-e2e="video-desc"]',
            '.video-card.active .title',
            '[data-e2e="video-desc"]',
            '.video-info-detail .title',
            '.xgplayer-title',
            '.author-card .title',
            '.video-info .title',
            '.video-desc',
            '.desc-container'
        ];
        for (const selector of fallbackSelectors) {
            try {
                const el = document.querySelector(selector);
                if (el && el.textContent) {
                    const text = el.textContent.trim().replace(/\s+/g, ' ');
                    if (text.length > 2 && text.length < 200 && text !== '抖音-记录美好生活') {
                        return text;
                    }
                }
            } catch (e) {}
        }
        
        const ogDesc = document.querySelector('meta[property="og:description"]');
        if (ogDesc && ogDesc.content && ogDesc.content.length > 3 && ogDesc.content !== '抖音-记录美好生活') {
            return ogDesc.content.substring(0, 200);
        }
        
        return null;
    };
    
    const getBestTitle = (url) => {
        const hostname = window.location.hostname;
        
        if (hostname.includes('douyin.com')) {
            const douyinTitle = getDouyinTitle(url);
            if (douyinTitle) return douyinTitle;
        }
        
        const ogTitle = document.querySelector('meta[property="og:title"]');
        if (ogTitle && ogTitle.content && ogTitle.content !== '抖音-记录美好生活') {
            return ogTitle.content;
        }
        
        const h1 = document.querySelector('h1');
        if (h1 && h1.textContent && h1.textContent.trim()) {
            const title = h1.textContent.trim();
            if (title.length > 3 && title.length < 200) return title;
        }
        
        const videoTitleSelectors = ['.video-title', '#video-title', '[class*="videoTitle"]', '.title', '#title'];
        for (const selector of videoTitleSelectors) {
            const el = document.querySelector(selector);
            if (el && el.textContent && el.textContent.trim().length > 3) {
                return el.textContent.trim().substring(0, 200);
            }
        }
        
        if (document.title) {
            let title = document.title;
            const suffixes = [' - YouTube', ' | Bilibili', ' - 哔哩哔哩', ' | TikTok', ' - 抖音', '抖音-记录美好生活'];
            for (const suffix of suffixes) {
                if (title.includes(suffix)) {
                    title = title.split(suffix)[0].trim();
                    break;
                }
            }
            if (title.length > 3 && title !== '抖音') return title;
        }
        
        return '未知视频';
    };
    
    const normalizeUrl = (url) => {
        if (!url) return '';
        if (url.startsWith('//')) url = window.location.protocol + url;
        return url;
    };
    
    const getDedupeKey = (url) => {
        return normalizeUrl(url).replace(/^https?:\/\//, '').split('?')[0].replace(/\/+$/, '').toLowerCase();
    };
    
    const urlCache = new Map();
    const getUrlLower = (url) => {
        const normalized = normalizeUrl(url);
        if (!urlCache.has(normalized)) {
            urlCache.set(normalized, normalized.toLowerCase());
            if (urlCache.size > 1000) urlCache.clear();
        }
        return urlCache.get(normalized);
    };

    const isExcluded = (url, options = {}) => {
        const allowBlob = options.allowBlob === true;
        if (!url || typeof url !== 'string') return true;
        if (url.startsWith('data:')) return true;
        if (!allowBlob && url.startsWith('blob:')) return true;
        const urlLower = getUrlLower(url);
        for (const ext of MEDIA_SIGNATURES.staticExts) {
            if (urlLower.includes(ext)) return true;
        }
        for (const pattern of MEDIA_SIGNATURES.excludePatterns) {
            if (urlLower.includes(pattern)) return true;
        }
        return false;
    };

    const isFragment = (url) => {
        const urlLower = getUrlLower(url);
        return MEDIA_SIGNATURES.fragmentExts.some(ext => {
            const idx = urlLower.indexOf(ext);
            return idx !== -1 && (!urlLower.charAt(idx + ext.length) || '?&#/'.includes(urlLower.charAt(idx + ext.length)));
        });
    };

    const isManifest = (url) => MEDIA_SIGNATURES.manifests.some(m => getUrlLower(url).includes(m));
    const isCompleteVideo = (url) => {
        const urlLower = getUrlLower(url);
        return MEDIA_SIGNATURES.completeVideoExts.some(ext => {
            const idx = urlLower.indexOf(ext);
            return idx !== -1 && (!urlLower.charAt(idx + ext.length) || '?&#/'.includes(urlLower.charAt(idx + ext.length)));
        });
    };
    const isCdnVideo = (url) => MEDIA_SIGNATURES.cdnPatterns.some(p => getUrlLower(url).includes(p));
    
    const isDouyinCdn = (url) => {
        const urlLower = getUrlLower(url);
        return urlLower.includes('douyinvod.com') || urlLower.includes('douyincdn.com') || 
               urlLower.includes('douyinpic.com') || (urlLower.includes('tos-cn-v') && window.location.hostname.includes('douyin.com'));
    };

    const detectMediaType = (url, contentType) => {
        const urlLower = getUrlLower(url);
        const ct = (contentType || '').toLowerCase();
        
        if (isAudioStream(url, contentType)) return 'Audio';
        if (urlLower.includes('.m3u8') || ct.includes('mpegurl')) return 'HLS';
        if (urlLower.includes('.mpd') || ct.includes('dash')) return 'DASH';
        if (urlLower.includes('googlevideo.com')) return 'YouTube';
        if (urlLower.includes('bilivideo')) return 'Bilibili';
        if (isDouyinCdn(url)) return 'Douyin';
        if (urlLower.includes('tiktokcdn.com')) return 'TikTok';
        if (urlLower.includes('.mp4')) return 'Video';
        if (ct.includes('video/')) return 'Video';
        if (ct.includes('audio/')) return 'Audio';
        return 'Media';
    };

    const canBeMediaCandidate = (url, contentType = '') => {
        if (!url || isExcluded(url)) return false;
        if (isAudioStream(url, contentType)) return false;
        const ct = (contentType || '').toLowerCase();
        if (ct && (ct.includes('application/json') || ct.includes('text/html'))) return false;
        const isBilibiliCdn = url.includes('bilivideo') || url.includes('upos-');
        const isDouyin = isDouyinCdn(url);
        if (isFragment(url) && !isManifest(url) && !isBilibiliCdn && !isDouyin) return false;
        return isManifest(url) || isCompleteVideo(url) || isCdnVideo(url);
    };

    const rememberMediaRequest = (url, contentType = '', size = '', source = 'network') => {
        const normalized = normalizeUrl(url);
        if (!canBeMediaCandidate(normalized, contentType)) return;
        recentMediaRequests.push({
            url: normalized,
            contentType: (contentType || '').toLowerCase(),
            size: size || '',
            source,
            ts: Date.now()
        });
        if (recentMediaRequests.length > 300) {
            recentMediaRequests.splice(0, recentMediaRequests.length - 300);
        }
    };

    const isAudioStream = (url, contentType) => {
        const urlLower = getUrlLower(url);
        const ct = (contentType || '').toLowerCase();
        if (ct.includes('audio/')) return true;
        if (urlLower.includes('mime=audio') || urlLower.includes('mime=audio%2fmp4') || urlLower.includes('mime=audio%2fm4a')) return true;
        if (urlLower.includes('douyinvod.com') || urlLower.includes('douyincdn.com') || urlLower.includes('tos-cn-v')) {
            if (urlLower.includes('/audio/') || urlLower.includes('mime=audio')) return true;
        }
        return false;
    };

    const pickRecentPlayableUrl = () => {
        const now = Date.now();
        for (let i = recentMediaRequests.length - 1; i >= 0; i--) {
            const item = recentMediaRequests[i];
            if (!item || !item.url) continue;
            if (now - item.ts > 30000) break;
            if (canBeMediaCandidate(item.url, item.contentType)) {
                if (isAudioStream(item.url, item.contentType)) continue;
                return item;
            }
        }
        return null;
    };

    const isElementVisible = (el) => {
        if (!el) return false;
        try {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            const inViewport = (
                rect.top < window.innerHeight &&
                rect.bottom > 0 &&
                rect.left < window.innerWidth &&
                rect.right > 0
            );
            if (!inViewport) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return false;
            return true;
        } catch (e) {
            return true;
        }
    };

    // 【抖音增强】判断 video 元素是否是屏幕中央的主播放视频
    // 抖音竖屏滑动Feed中，当前播放的视频会占据屏幕中央大部分区域
    const isDouyinActiveVideo = (video) => {
        if (!video) return false;
        try {
            const rect = video.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            const viewH = window.innerHeight;
            const viewW = window.innerWidth;
            // 视频中心点
            const centerY = (rect.top + rect.bottom) / 2;
            const centerX = (rect.left + rect.right) / 2;
            // 视频中心点是否在屏幕中央区域（上下各30%范围内）
            const inCenterY = centerY > viewH * 0.2 && centerY < viewH * 0.8;
            const inCenterX = centerX > 0 && centerX < viewW;
            // 视频占屏幕高度的比例
            const visibleTop = Math.max(rect.top, 0);
            const visibleBottom = Math.min(rect.bottom, viewH);
            const visibleHeight = Math.max(0, visibleBottom - visibleTop);
            const coverRatio = visibleHeight / viewH;
            // 主播放视频应该占屏幕高度 > 50%，且中心点在屏幕中央
            return inCenterY && inCenterX && coverRatio > 0.5;
        } catch (e) {
            return false;
        }
    };

    // 【抖音增强】在抖音页面中找到当前真正播放的 video 元素
    const findDouyinActiveVideo = () => {
        const isDouyin = window.location.hostname.includes('douyin.com');
        if (!isDouyin) return null;
        const videos = document.querySelectorAll('video');
        let bestVideo = null;
        let bestCover = 0;
        for (const video of videos) {
            if (video.paused || video.readyState < 2) continue;
            if (!isDouyinActiveVideo(video)) continue;
            const rect = video.getBoundingClientRect();
            const visibleTop = Math.max(rect.top, 0);
            const visibleBottom = Math.min(rect.bottom, window.innerHeight);
            const cover = Math.max(0, visibleBottom - visibleTop);
            if (cover > bestCover) {
                bestCover = cover;
                bestVideo = video;
            }
        }
        return bestVideo;
    };

    // 从 React Fiber 或 xgplayer 属性中深度检索有效的视频 CDN URL
    const findCdnUrlFromReact = (video) => {
        const getReactProps = (el) => {
            if (!el) return null;
            const keys = Object.keys(el);
            for (const key of keys) {
                if (key.startsWith('__reactProps$') || key.startsWith('__reactFiber$') || key.startsWith('__reactInternalInstance$')) {
                    return el[key];
                }
            }
            return null;
        };

        const findUrlInObject = (obj, depth = 0, visited = new Set()) => {
            if (!obj || depth > 8 || visited.has(obj)) return null;
            visited.add(obj);
            try {
                // 先检查所有字符串属性，看是否包含符合特征的抖音视频链接
                for (const key in obj) {
                    const val = obj[key];
                    if (typeof val === 'string') {
                        if (val.startsWith('http') && (val.includes('douyinvod.com') || val.includes('douyincdn.com') || val.includes('tos-cn-v'))) {
                            return val;
                        }
                    }
                }
                // 深度遍历对象和数组
                for (const key in obj) {
                    const val = obj[key];
                    if (typeof val === 'object' && val !== null) {
                        const found = findUrlInObject(val, depth + 1, visited);
                        if (found) return found;
                    }
                }
            } catch (e) {}
            return null;
        };

        let curr = video;
        while (curr && curr !== document.body) {
            // 尝试 xgplayer 实例属性
            if (curr.__xgplayer__) {
                const player = curr.__xgplayer__;
                if (player.config && player.config.url && typeof player.config.url === 'string' && player.config.url.startsWith('http')) {
                    return player.config.url;
                }
                if (player.src && typeof player.src === 'string' && player.src.startsWith('http')) {
                    return player.src;
                }
            }
            // 尝试 React 属性
            const props = getReactProps(curr);
            if (props) {
                const url = findUrlInObject(props);
                if (url) return url;
            }
            curr = curr.parentElement;
        }
        return null;
    };

    // 从 React Fiber 属性中提取抖音视频唯一的 19 位数字 ID (awemeId)
    const findAwemeIdFromReact = (video) => {
        const getReactProps = (el) => {
            if (!el) return null;
            const keys = Object.keys(el);
            for (const key of keys) {
                if (key.startsWith('__reactProps$') || key.startsWith('__reactFiber$') || key.startsWith('__reactInternalInstance$')) {
                    return el[key];
                }
            }
            return null;
        };

        const findIdInObject = (obj, depth = 0, visited = new Set()) => {
            if (!obj || depth > 8 || visited.has(obj)) return null;
            visited.add(obj);
            try {
                for (const key in obj) {
                    const val = obj[key];
                    if (typeof val === 'string' || typeof val === 'number') {
                        const strVal = String(val);
                        if (/^\d{19}$/.test(strVal)) {
                            const kl = key.toLowerCase();
                            if (kl.includes('id') || kl.includes('aweme') || kl.includes('group') || kl.includes('item')) {
                                return strVal;
                            }
                        }
                    }
                }
                for (const key in obj) {
                    const val = obj[key];
                    if (typeof val === 'object' && val !== null) {
                        const found = findIdInObject(val, depth + 1, visited);
                        if (found) return found;
                    }
                }
            } catch (e) {}
            return null;
        };

        let curr = video;
        while (curr && curr !== document.body) {
            if (curr.__xgplayer__) {
                const player = curr.__xgplayer__;
                if (player.config && player.config.vid) {
                    return String(player.config.vid);
                }
            }
            const props = getReactProps(curr);
            if (props) {
                const id = findIdInObject(props);
                if (id) return id;
            }
            curr = curr.parentElement;
        }
        return null;
    };

    // 针对抖音平台获取精准的视频详情页 URL
    const getDouyinPageUrl = (video, defaultUrl) => {
        if (!video) return defaultUrl;
        try {
            const id = findAwemeIdFromReact(video);
            if (id) return 'https://www.douyin.com/video/' + id;

            if (defaultUrl && defaultUrl.includes('/video/')) {
                const match = defaultUrl.match(/\/video\/(\d+)/);
                if (match) return 'https://www.douyin.com/video/' + match[1];
            }

            let curr = video.parentElement;
            while (curr && curr !== document.body) {
                const a = curr.querySelector('a[href*="/video/"]');
                if (a && a.href) {
                    const match = a.href.match(/\/video\/(\d+)/);
                    if (match) return 'https://www.douyin.com/video/' + match[1];
                }
                curr = curr.parentElement;
            }
        } catch (e) {}
        return defaultUrl;
    };

    const getVideoLikelyUrl = (video) => {
        if (!video) return '';
        const src = normalizeUrl(video.currentSrc || video.src || '');
        if (src && !src.startsWith('blob:')) return src;
        try {
            const sourceEl = video.querySelector('source[src]');
            if (sourceEl && sourceEl.src && !sourceEl.src.startsWith('blob:')) {
                return normalizeUrl(sourceEl.src);
            }
        } catch (e) {}
        
        // 【第一优先】尝试直接从 xgplayer / React props 中读取绝对真实的视频链接
        const extracted = findCdnUrlFromReact(video);
        if (extracted) {
            videoBoundCdnUrl.set(video, extracted);
            return extracted;
        }

        // 【第二优先】使用先前绑定的 CDN URL
        const bound = videoBoundCdnUrl.get(video);
        if (bound) return bound;
        const picked = pickRecentPlayableUrl();
        return picked ? picked.url : '';
    };

    // 【抖音增强】当 video 元素开始播放时，快照当前最新的 CDN URL 并绑定
    const bindVideoToCdn = (video) => {
        if (!video) return;
        // 如果已经绑定过，不重复绑定
        const existingBound = videoBoundCdnUrl.get(video);
        if (existingBound) return;
        
        // 1. 优先尝试从 React/xgplayer 中读取
        const extracted = findCdnUrlFromReact(video);
        if (extracted) {
            videoBoundCdnUrl.set(video, extracted);
            return;
        }
        
        // 2. 检查 video.src 是否是 blob:，如果是非 blob 直链则不需要绑定
        const src = video.currentSrc || video.src || '';
        if (src && !src.startsWith('blob:')) return;
        const picked = pickRecentPlayableUrl();
        if (picked && picked.url) {
            videoBoundCdnUrl.set(video, picked.url);
        }
    };
    
    const extractQualityInfo = (url) => {
        const info = { resolution: null, bitrate: null, codec: null };
        const brMatch = url.match(/[?&]br=(\d+)/);
        if (brMatch) info.bitrate = brMatch[1] + 'kbps';
        return info;
    };
    
    const checkIsPreload = (url) => {
        if (currentPlayingUrl && url !== currentPlayingUrl) {
            const currentPath = currentPlayingUrl.split('?')[0];
            const newPath = url.split('?')[0];
            if (currentPath !== newPath) return true;
        }
        // 【抖音增强】在抖音页面中，使用视口中心位置判断是否是预加载
        const isDouyin = window.location.hostname.includes('douyin.com');
        if (isDouyin) {
            const activeVideo = findDouyinActiveVideo();
            if (activeVideo) {
                const activeSrc = getVideoLikelyUrl(activeVideo);
                if (activeSrc && getDedupeKey(activeSrc) !== getDedupeKey(url)) {
                    return true;
                }
            }
        }
        const videos = document.querySelectorAll('video');
        for (const video of videos) {
            if (!video.paused && video.currentTime > 0) {
                const src = video.src || video.currentSrc || '';
                if (src && !src.startsWith('blob:') && src.split('?')[0] !== url.split('?')[0]) {
                    return true;
                }
            }
        }
        return false;
    };

    const reportMedia = (data) => {
        if (!data.url || isExcluded(data.url)) return;

        const url = normalizeUrl(data.url);
        if (!url) return;

        // YouTube 页面 URL 兜底条目：直接上报，跳过 canBeMediaCandidate 的直链判定
        if (data.type === 'YouTubePage') {
            const ct = (data.contentType || '').toLowerCase();
            if (ct && (ct.includes('application/json') || ct.includes('text/html'))) return;
            const urlKey = getDedupeKey(url);
            const prev = capturedUrls.get(urlKey);
            if (prev && !data.forceUpdate && !data.isPlaying) return;
            capturedUrls.set(urlKey, { timestamp: Date.now(), is_playing: Boolean(data.isPlaying) });
            if (capturedUrls.size > 500) capturedUrls.delete(capturedUrls.keys().next().value);
            const mediaData = {
                url: url,
                type: 'YouTubePage',
                title: data.title || getBestTitle(url),
                page_url: data.page_url || window.location.href,
                size: data.size || '',
                content_type: data.contentType || '',
                source: data.source || 'yt_page_fallback',
                is_playing: Boolean(data.isPlaying),
                is_preload: false,
                thumbnail: pageThumbnail || '',
                quality: extractQualityInfo(url),
                has_drm: drmDetected.size > 0,
                force_update: Boolean(data.forceUpdate),
                active_video: Boolean(data.activeVideo)
            };
            if (reportMediaReady && window.reportMedia) {
                window.reportMedia(mediaData).catch(() => {});
            } else {
                pendingMedia.push(mediaData);
            }
            return;
        }

        if (!canBeMediaCandidate(url, data.contentType || '')) return;
        
        const ct = (data.contentType || '').toLowerCase();
        if (ct && (ct.includes('application/json') || ct.includes('text/html'))) return;
        
        const urlKey = getDedupeKey(url);
        const prev = capturedUrls.get(urlKey);
        
        if (prev && !data.forceUpdate) {
            if (!(data.isPlaying && !prev.is_playing)) return;
        }
        
        let size = data.size || '';
        const isDouyin = isDouyinCdn(url);
        if (isDouyin && size) {
            const sizeBytes = parseInt(size.replace(/[^0-9]/g, ''));
            if (sizeBytes < 5 * 1024 * 1024) size = '未知';
        }
        
        const isPlaying = Boolean(
            data.isPlaying ||
            (currentPlayingUrl && getDedupeKey(url) === getDedupeKey(currentPlayingUrl))
        );
        const isPreload = isPlaying ? false : checkIsPreload(url);
        
        let pageUrl = window.location.href;
        if (window.location.hostname.includes('douyin.com')) {
            const video = findVideoElementByUrl(url);
            pageUrl = getDouyinPageUrl(video, pageUrl);
        }

        const mediaData = {
            url: url,
            type: data.type || detectMediaType(url, data.contentType),
            title: getBestTitle(url),
            page_url: pageUrl,
            size: size,
            content_type: data.contentType || '',
            source: data.source || 'network',
            is_playing: isPlaying && !isPreload,
            is_preload: isPreload,
            thumbnail: pageThumbnail || '',
            quality: extractQualityInfo(url),
            has_drm: drmDetected.size > 0,
            force_update: Boolean(data.forceUpdate || (prev && !prev.is_playing && isPlaying)),
            active_video: Boolean(data.activeVideo)
        };

        capturedUrls.set(urlKey, { timestamp: Date.now(), is_playing: mediaData.is_playing });
        if (capturedUrls.size > 500) capturedUrls.delete(capturedUrls.keys().next().value);
        
        if (reportMediaReady && window.reportMedia) {
            window.reportMedia(mediaData).catch(() => {});
        } else {
            pendingMedia.push(mediaData);
        }
    };

    // 【YouTube 兜底】页面在播放但拿不到直链（MSE/blob 场景）时，上报页面 URL 供 yt-dlp 解析
    const reportYoutubePageFallback = (source = 'yt_page_fallback') => {
        try {
            const host = window.location.hostname.toLowerCase();
            const isYtHost = host.includes('youtube.com') || host === 'youtu.be';
            if (!isYtHost) return false;
            const path = window.location.pathname.toLowerCase();
            const isVideoPage = (
                path.includes('/watch') || path.startsWith('/shorts/') || path.startsWith('/live/') ||
                (host === 'youtu.be' && path.length > 1)
            );
            if (!isVideoPage) return false;
            const href = window.location.href;
            if (!href || href.startsWith('blob:') || href.startsWith('data:') || isExcluded(href)) return false;
            reportMedia({
                url: href,
                type: 'YouTubePage',
                source,
                isPlaying: true,
                activeVideo: true,
                forceUpdate: true
            });
            return true;
        } catch (e) {
            return false;
        }
    };

    const emitActivePlayback = (video, source = 'playback') => {
        const now = Date.now();
        if (now - lastPlaybackReportAt < 1200) return;
        lastPlaybackReportAt = now;
        const url = getVideoLikelyUrl(video);
        if (!url) {
            // MSE/blob 场景拿不到直链：兜底上报 YouTube 页面 URL
            reportYoutubePageFallback(source + '_fallback');
            return;
        }
        currentPlayingUrl = url;
        reportMedia({
            url,
            source,
            isPlaying: true,
            activeVideo: true,
            forceUpdate: true
        });
    };

    const attachVideoListeners = (video) => {
        if (!video || video.__proSnifferBound) return;
        video.__proSnifferBound = true;
        const isDouyin = window.location.hostname.includes('douyin.com');
        const onPlayingState = () => {
            if (!video.paused && video.readyState >= 2) {
                if (!isElementVisible(video)) return;
                // 【抖音增强】在抖音页面使用视口中心位置判断
                if (isDouyin && !isDouyinActiveVideo(video)) return;
                // 【抖音增强】视频开始播放时绑定当前 CDN URL
                bindVideoToCdn(video);
                activeVideoElement = video;
                playingVideos.set(video, { startTime: Date.now() });
                emitActivePlayback(video, 'video_event');
            }
        };
        video.addEventListener('play', onPlayingState, true);
        video.addEventListener('playing', onPlayingState, true);
        video.addEventListener('loadedmetadata', onPlayingState, true);
        video.addEventListener('durationchange', onPlayingState, true);
        video.addEventListener('timeupdate', () => {
            if (!video.paused && video.currentTime > 0) {
                if (!isElementVisible(video)) return;
                // 【抖音增强】在抖音页面使用视口中心位置判断
                if (isDouyin && !isDouyinActiveVideo(video)) return;
                activeVideoElement = video;
                emitActivePlayback(video, 'timeupdate');
            }
        }, true);
        video.addEventListener('pause', () => {
            if (activeVideoElement === video) activeVideoElement = null;
        }, true);
        video.addEventListener('ended', () => {
            if (activeVideoElement === video) activeVideoElement = null;
        }, true);
    };

    const originalFetch = window.fetch;
    window.fetch = async function(...args) {
        const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
        try {
            const response = await originalFetch.apply(this, args);
            if (!isExcluded(url) && (isManifest(url) || isCompleteVideo(url) || isCdnVideo(url))) {
                const ct = response.headers?.get('content-type') || '';
                const cl = response.headers?.get('content-length') || '';
                let size = cl ? (parseInt(cl) > 1048576 ? `${(parseInt(cl)/1048576).toFixed(1)}MB` : `${(parseInt(cl)/1024).toFixed(0)}KB`) : '';
                rememberMediaRequest(url, ct, size, 'fetch');
                reportMedia({ url, contentType: ct, size, source: 'fetch' });
            }
            return response;
        } catch(e) { throw e; }
    };

    const originalXHROpen = XMLHttpRequest.prototype.open;
    const originalXHRSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        this._mediaSnifferUrl = url;
        return originalXHROpen.apply(this, [method, url, ...rest]);
    };
    XMLHttpRequest.prototype.send = function(...args) {
        const xhr = this;
        xhr.addEventListener('load', function() {
            try {
                const url = xhr._mediaSnifferUrl;
                if (url && !isExcluded(url) && (isManifest(url) || isCompleteVideo(url) || isCdnVideo(url))) {
                    const ct = xhr.getResponseHeader('content-type') || '';
                    const cl = xhr.getResponseHeader('content-length') || '';
                    let size = cl ? (parseInt(cl) > 1048576 ? `${(parseInt(cl)/1048576).toFixed(1)}MB` : `${(parseInt(cl)/1024).toFixed(0)}KB`) : '';
                    rememberMediaRequest(url, ct, size, 'xhr');
                    reportMedia({ url, contentType: ct, size, source: 'xhr' });
                }
            } catch(e) {}
        });
        return originalXHRSend.apply(this, args);
    };

    const originalPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function() {
        attachVideoListeners(this);
        // 【抖音增强】播放时立即绑定 CDN URL
        bindVideoToCdn(this);
        const src = getVideoLikelyUrl(this);
        if (src) {
            currentPlayingUrl = src;
            activeVideoElement = this;
            playingVideos.set(this, { src, startTime: Date.now() });
            setTimeout(() => {
                if (this.duration && this.duration > 5) {
                    emitActivePlayback(this, 'play');
                }
            }, 450);
        }
        return originalPlay.apply(this, arguments);
    };

    document.addEventListener('durationchange', (e) => {
        if (e.target instanceof HTMLVideoElement) {
            const video = e.target;
            attachVideoListeners(video);
            const src = getVideoLikelyUrl(video);
            if (src && video.duration > 5) {
                if (!video.paused) {
                    currentPlayingUrl = src;
                    activeVideoElement = video;
                }
                if (!isExcluded(src)) {
                    reportMedia({
                        url: src,
                        source: 'durationchange',
                        isPlaying: !video.paused,
                        activeVideo: !video.paused,
                        forceUpdate: !video.paused
                    });
                }
            }
        }
    }, true);

    let lastScanTime = 0;
    const scanPlayingVideos = () => {
        const now = Date.now();
        if (now - lastScanTime < 4000) return;
        lastScanTime = now;
        const isDouyin = window.location.hostname.includes('douyin.com');
        
        // 【抖音增强】在抖音页面优先使用中心视口检测
        if (isDouyin) {
            const douyinActive = findDouyinActiveVideo();
            if (douyinActive) {
                attachVideoListeners(douyinActive);
                const src = getVideoLikelyUrl(douyinActive);
                if (src) {
                    currentPlayingUrl = src;
                    activeVideoElement = douyinActive;
                    if (!isExcluded(src)) {
                        reportMedia({
                            url: src,
                            source: 'playing_scan',
                            isPlaying: true,
                            activeVideo: true,
                            forceUpdate: true
                        });
                    }
                }
            }
        } else {
            const videos = document.querySelectorAll('video');
            for (const video of videos) {
                if (!video.paused && video.duration > 5) {
                    attachVideoListeners(video);
                    const src = getVideoLikelyUrl(video);
                    if (src) {
                        currentPlayingUrl = src;
                        activeVideoElement = video;
                        if (!isExcluded(src)) {
                            reportMedia({
                                url: src,
                                source: 'playing_scan',
                                isPlaying: true,
                                activeVideo: true,
                                forceUpdate: true
                            });
                        }
                    } else {
                        // MSE/blob 场景拿不到直链：兜底上报 YouTube 页面 URL
                        reportYoutubePageFallback('playing_scan_fallback');
                    }
                }
            }
        }
        updateThumbnail();
    };
    
    const scheduleVideoScan = () => {
        if (window.requestIdleCallback) {
            window.requestIdleCallback(scanPlayingVideos, { timeout: 5000 });
        } else {
            setTimeout(scanPlayingVideos, 5000);
        }
    };
    setInterval(scheduleVideoScan, 5000);

    let mutationTimeout = null;
    const observer = new MutationObserver((mutations) => {
        if (mutationTimeout) clearTimeout(mutationTimeout);
        mutationTimeout = setTimeout(() => {
            mutations.forEach(mutation => {
                mutation.addedNodes.forEach(node => {
                    if (node.nodeType !== 1) return;
                    const videos = node.tagName === 'VIDEO' ? [node] : (node.querySelectorAll?.('video') || []);
                    videos.forEach(video => {
                        attachVideoListeners(video);
                    });
                });
            });
        }, 100);
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });

    document.querySelectorAll('video').forEach(attachVideoListeners);

    // 尝试检测 EME/DRM 信号
    document.addEventListener('encrypted', () => {
        drmDetected.add('encrypted_event');
    }, true);

    if (navigator.requestMediaKeySystemAccess) {
        try {
            const originalRequestMediaKeySystemAccess = navigator.requestMediaKeySystemAccess.bind(navigator);
            navigator.requestMediaKeySystemAccess = function(keySystem, configurations) {
                try {
                    if (keySystem) drmDetected.add(String(keySystem));
                } catch (e) {}
                return originalRequestMediaKeySystemAccess(keySystem, configurations);
            };
        } catch (e) {}
    }


    
    // === 增强：视频检测、高亮框、点击捕获 ===
    let highlightBox = document.createElement('div');
    highlightBox.style.position = 'fixed';
    highlightBox.style.border = '3px solid #ff4757';
    highlightBox.style.backgroundColor = 'rgba(255, 71, 87, 0.2)';
    highlightBox.style.zIndex = '9999999999';
    highlightBox.style.pointerEvents = 'none'; // 确保不阻止点击事件
    highlightBox.style.display = 'none';
    highlightBox.style.transition = 'all 0.1s ease';
    highlightBox.style.borderRadius = '4px';
    highlightBox.style.boxSizing = 'border-box';
    highlightBox.style.boxShadow = '0 0 10px rgba(255, 71, 87, 0.5)';
    
    const tip = document.createElement('div');
    tip.textContent = '点击捕获媒体';
    tip.style.position = 'absolute';
    tip.style.top = '-25px';
    tip.style.left = '50%';
    tip.style.transform = 'translateX(-50%)';
    tip.style.background = '#ff4757';
    tip.style.color = '#fff';
    tip.style.padding = '2px 8px';
    tip.style.borderRadius = '4px';
    tip.style.fontSize = '12px';
    tip.style.fontWeight = 'bold';
    tip.style.whiteSpace = 'nowrap';
    highlightBox.appendChild(tip);

    document.documentElement.appendChild(highlightBox);

    let hoveredVideo = null;

    const updateHighlightBox = () => {
        if (!hoveredVideo || !isElementVisible(hoveredVideo)) {
            highlightBox.style.display = 'none';
            return;
        }
        const rect = hoveredVideo.getBoundingClientRect();
        highlightBox.style.left = rect.left + 'px';
        highlightBox.style.top = rect.top + 'px';
        highlightBox.style.width = rect.width + 'px';
        highlightBox.style.height = rect.height + 'px';
        highlightBox.style.display = 'block';
    };

    window.addEventListener('scroll', updateHighlightBox, true);
    window.addEventListener('resize', updateHighlightBox, true);

    // 使用 mousemove 来计算鼠标坐标下是否有 video 元素，解决视频被遮罩层覆盖导致无法检测的问题
    document.addEventListener('mousemove', (e) => {
        const videos = document.querySelectorAll('video');
        let foundVideo = null;
        for (const video of videos) {
            const rect = video.getBoundingClientRect();
            // 检查鼠标是否在视频范围内
            if (e.clientX >= rect.left && e.clientX <= rect.right && 
                e.clientY >= rect.top && e.clientY <= rect.bottom && 
                isElementVisible(video)) {
                foundVideo = video;
                break;
            }
        }
        
        if (foundVideo !== hoveredVideo) {
            hoveredVideo = foundVideo;
            updateHighlightBox();
        }
    }, true);

    document.addEventListener('click', (e) => {
        if (hoveredVideo) {
            const rect = hoveredVideo.getBoundingClientRect();
            // 确保点击确实在视频范围内
            if (e.clientX >= rect.left && e.clientX <= rect.right && 
                e.clientY >= rect.top && e.clientY <= rect.bottom) {
                
                e.preventDefault();
                e.stopPropagation();
                
                // 点击捕获：触发 reportMedia
                const src = getVideoLikelyUrl(hoveredVideo);
                if (src && !isExcluded(src)) {
                    reportMedia({
                        url: src,
                        source: 'click_capture',
                        isPlaying: !hoveredVideo.paused,
                        activeVideo: true,
                        forceUpdate: true
                    });
                    
                    // 视觉反馈
                    const originalBg = highlightBox.style.backgroundColor;
                    const originalBorder = highlightBox.style.borderColor;
                    highlightBox.style.backgroundColor = 'rgba(46, 204, 113, 0.4)';
                    highlightBox.style.borderColor = '#2ecc71';
                    tip.textContent = '已捕获 (Captured!)';
                    tip.style.background = '#2ecc71';
                    setTimeout(() => {
                        highlightBox.style.backgroundColor = originalBg;
                        highlightBox.style.borderColor = originalBorder;
                        tip.textContent = '点击捕获媒体';
                        tip.style.background = '#ff4757';
                    }, 800);
                } else {
                    // 如果视频链接未检测到
                    tip.textContent = '链接尚未就绪';
                    setTimeout(() => {
                        tip.textContent = '点击捕获媒体';
                    }, 800);
                }
            }
        }
    }, true);

    if (DEBUG) console.log('[ProMediaSniffer v4.3] 抖音增强版已启动');
})();
'''


def _safe_print(msg):
    """安全的打印函数，不会因为 stdout 无效而崩溃"""
    try:
        print(msg, flush=True)
    except (OSError, AttributeError, ValueError):
        pass
    try:
        if getattr(sys, 'frozen', False):
            base = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'SourceCraft')
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        os.makedirs(base, exist_ok=True)
        log_file = os.path.join(base, 'browser_debug.log')
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] {msg}\n")
    except:
        pass


def _find_browser_info(browser_name: str = "") -> Optional[Dict[str, Any]]:
    target = str(browser_name or "").strip().lower()
    available = detect_installed_browsers()
    if not available:
        return None

    if not target:
        return available[0]

    for browser in available:
        browser_id = str(browser.get("id", "")).strip().lower()
        exe_name = os.path.basename(str(browser.get("path", "")).strip()).lower()
        if target in {browser_id, exe_name, exe_name.replace(".exe", "")}:
            return browser

    return None


def _find_browser_path(browser_name):
    browser = _find_browser_info(browser_name)
    if not browser:
        return None
    browser_path = str(browser.get("path", "")).strip()
    if browser_path:
        _safe_print(f"[BROWSER] 找到 {browser.get('name', browser_name)}: {browser_path}")
    return browser_path


def _build_launch_options_for_engine(engine: str) -> Dict[str, Any]:
    normalized_engine = str(engine or "chromium").strip().lower()
    common_options: Dict[str, Any] = {
        "headless": False,
        "no_viewport": True,
        "locale": "zh-CN",
    }

    if normalized_engine == "firefox":
        common_options["args"] = ["--new-instance"]
        return common_options

    common_options.update({
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--lang=zh-CN",
            "--test-type",
            "--start-maximized",
        ],
        "ignore_default_args": ["--enable-automation"],
    })
    return common_options


def _get_playwright_launcher(playwright_instance, engine: str):
    normalized_engine = str(engine or "chromium").strip().lower()
    if normalized_engine == "firefox":
        return playwright_instance.firefox
    return playwright_instance.chromium


def _get_default_homepage(browser_id: str = "", engine: str = "") -> str:
    normalized_id = str(browser_id or "").strip().lower()
    normalized_engine = str(engine or "").strip().lower()
    if normalized_id == "edge":
        return "https://www.bing.com"
    if normalized_engine == "firefox":
        return "https://www.google.com"
    return "https://www.google.com"


async def _probe_h264_aac_support(playwright_instance, executable_path: str) -> Optional[Tuple[bool, bool]]:
    """检测指定浏览器是否支持 H264/AAC。返回 (h264_ok, aac_ok)，失败时返回 None。"""
    if not executable_path or not os.path.exists(executable_path):
        return None
    browser = None
    try:
        browser = await playwright_instance.chromium.launch(
            headless=True,
            executable_path=executable_path,
            args=["--disable-gpu"],
        )
        page = await browser.new_page()
        result = await page.evaluate(
            """() => {
                const v = document.createElement('video');
                const a = document.createElement('audio');
                const h264 =
                  v.canPlayType('video/mp4; codecs="avc1.42E01E, mp4a.40.2"') ||
                  v.canPlayType('video/mp4; codecs="avc1.4D401E, mp4a.40.2"') ||
                  v.canPlayType('video/mp4; codecs="avc1.64001E, mp4a.40.2"');
                const aac =
                  a.canPlayType('audio/mp4; codecs="mp4a.40.2"') ||
                  v.canPlayType('audio/mp4; codecs="mp4a.40.2"');
                return { h264, aac };
            }"""
        )
        await browser.close()
        h264_ok = bool(result.get("h264"))
        aac_ok = bool(result.get("aac"))
        return h264_ok, aac_ok
    except Exception as exc:
        _safe_print(f"[BROWSER] WARN: CODEC_CHECK_FAILED: {exc}")
        try:
            if browser is not None:
                await browser.close()
        except Exception:
            pass
        return None


def run_launcher():
    """在打包环境下，作为子进程运行浏览器启动逻辑（完整版）"""
    import asyncio
    import queue
    import time
    from urllib.parse import urlparse, parse_qs

    def _parse_launcher_argv(argv: list) -> Tuple[str, str, str]:
        # 兼容两种形态：
        # 1) fallback 直调 run_launcher: <app> --playwright-launcher <script> <browser> <cookies> [url]
        # 2) _launcher.py 启动:         <script> <browser> <cookies> [url]
        if len(argv) >= 5 and argv[1] == "--playwright-launcher":
            selected = str(argv[3] or "").strip()
            cookies = str(argv[4] or "").strip()
            initial = str(argv[5] or "").strip() if len(argv) > 5 else ""
            return selected, cookies, initial
        if len(argv) >= 3:
            selected = str(argv[1] or "").strip()
            cookies = str(argv[2] or "").strip()
            initial = str(argv[3] or "").strip() if len(argv) > 3 else ""
            return selected, cookies, initial
        return "", "", ""

    import playwright_fix
    playwright_fix.setup_playwright_environment()
    playwright_fix.fix_windows_path_env()

    selected_browser, cookies_dir, initial_url = _parse_launcher_argv(sys.argv)
    if not selected_browser or not cookies_dir:
        _safe_print(
            f"[BROWSER] ERROR: 参数不足, 收到 {len(sys.argv)} 个参数: {sys.argv} "
            "(需要: <browser> <cookies_dir> [initial_url])"
        )
        return
    
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        _safe_print(f"[BROWSER] ERROR: Playwright 模块未安装: {e}")
        return
    
    # 全局捕获状态
    capture_state = {
        "capture_enabled": False,
        "captured_media": {},
        "captured_limit": 2000,
        "last_media_warn_at": 0.0,
    }

    def _media_seen(url_key: str) -> bool:
        return url_key in capture_state["captured_media"]

    def _media_mark(url_key: str) -> None:
        if not url_key:
            return
        media_map = capture_state["captured_media"]
        media_map[url_key] = time.monotonic()
        limit = max(500, int(capture_state.get("captured_limit", 2000)))
        if len(media_map) <= limit:
            return
        # 只淘汰最老的一部分，避免“一次性清空”导致重复刷入
        drop_count = max(1, limit // 4)
        oldest = sorted(media_map.items(), key=lambda kv: kv[1])[:drop_count]
        for old_key, _ in oldest:
            media_map.pop(old_key, None)

    def _throttled_media_warn(message: str, interval_seconds: float = 10.0) -> None:
        now_mono = time.monotonic()
        last_at = float(capture_state.get("last_media_warn_at", 0.0))
        if (now_mono - last_at) >= interval_seconds:
            capture_state["last_media_warn_at"] = now_mono
            _safe_print(message)
    
    cmd_queue = queue.Queue()
    pending_cmds: List[str] = []
    stdin_reader_started = False

    def _ensure_stdin_reader() -> None:
        nonlocal stdin_reader_started
        if stdin_reader_started:
            return
        reader_thread = threading.Thread(target=stdin_reader, daemon=True)
        reader_thread.start()
        stdin_reader_started = True

    async def _await_codec_decision(timeout: float = 30.0) -> Tuple[str, str]:
        """等待 GUI 对编解码切换的确认。返回 (decision, browser)."""
        start = time.monotonic()
        while True:
            while not cmd_queue.empty():
                cmd = cmd_queue.get_nowait().strip()
                if not cmd:
                    continue
                if cmd.upper().startswith("CODEC_USE_SYSTEM"):
                    parts = cmd.split()
                    choice = parts[1].strip().lower() if len(parts) > 1 else ""
                    return "system", choice
                if cmd.upper() == "CODEC_STAY":
                    return "stay", ""
                pending_cmds.append(cmd)
            if timeout is not None and (time.monotonic() - start) >= timeout:
                return "timeout", ""
            await asyncio.sleep(0.1)
    
    def stdin_reader():
        """后台线程读取 stdin 命令"""
        stream = getattr(sys, "stdin", None) or getattr(sys, "__stdin__", None)
        if stream is None:
            _safe_print("[BROWSER] WARN: stdin 不可用，命令通道已禁用")
            return
        while True:
            try:
                line = stream.readline()
                if not line:
                    break
                cmd = line.strip()
                if cmd:
                    cmd_queue.put(cmd)
            except Exception as e:
                _safe_print(f"[BROWSER] WARN: 读取命令失败: {e}")
                break
    
    async def handle_command(cmd, page, context):
        """处理从 GUI 发来的控制命令"""
        parts = cmd.split(" ", 1)
        action = parts[0].upper()

        async def _clear_capture_runtime():
            cleared_pages = 0
            try:
                for target_page in list(context.pages):
                    try:
                        ok = await target_page.evaluate(
                            """() => {
                                if (typeof window.__pro_media_sniffer_clear_captured === 'function') {
                                    return window.__pro_media_sniffer_clear_captured();
                                }
                                return false;
                            }"""
                        )
                        if ok:
                            cleared_pages += 1
                    except Exception:
                        continue
            except Exception:
                pass
            return cleared_pages

        async def _refresh_capture_runtime():
            refreshed_pages = 0
            try:
                for target_page in list(context.pages):
                    try:
                        ok = await target_page.evaluate(
                            """() => {
                                if (typeof window.__pro_media_sniffer_refresh_capture === 'function') {
                                    return window.__pro_media_sniffer_refresh_capture();
                                }
                                return false;
                            }"""
                        )
                        if ok:
                            refreshed_pages += 1
                    except Exception:
                        continue
            except Exception:
                pass
            return refreshed_pages
        
        if action == "GOTO":
            url = parts[1] if len(parts) > 1 else ""
            if url:
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except:
                    pass
                _safe_print(f"[BROWSER] 导航到: {page.url}")
        elif action == "BACK":
            try:
                await page.go_back()
            except:
                pass
        elif action == "FORWARD":
            try:
                await page.go_forward()
            except:
                pass
        elif action == "RELOAD":
            try:
                await page.reload()
            except:
                pass
        elif action == "COOKIES":
            try:
                cookies = await context.cookies()
                if cookies:
                    domain = urlparse(page.url).netloc or "unknown"
                    os.makedirs(cookies_dir, exist_ok=True)
                    safe_domain = "".join([c for c in domain if c.isalpha() or c.isdigit() or c in ('-', '_', '.')]).rstrip()
                    filepath = os.path.join(cookies_dir, f"{safe_domain}_cookies.txt")
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write("# Netscape HTTP Cookie File\n# Source: internal_browser\n# Domain: " + domain + "\n\n")
                        for c in cookies:
                            domain_val = c.get('domain', '')
                            flag = "TRUE" if domain_val.startswith('.') else "FALSE"
                            path = c.get('path', '/')
                            secure = "TRUE" if c.get('secure', False) else "FALSE"
                            expires = int(c.get('expires', 0))
                            if expires <= 0:
                                expires = int(time.time()) + 31536000
                            name = c.get('name', '')
                            value = c.get('value', '')
                            if c.get('httpOnly', False):
                                domain_val = f"#HttpOnly_{domain_val}"
                            f.write(f"{domain_val}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
                    marker_path = filepath.replace('_cookies.txt', '_cookies.internal')
                    with open(marker_path, 'w', encoding='utf-8') as mf:
                        mf.write('internal_browser')
                    _safe_print(f"[BROWSER] Cookie已保存: {filepath} ({len(cookies)}个)")
                else:
                    _safe_print("[BROWSER] 没有Cookie可保存")
            except Exception as e:
                _safe_print(f"[BROWSER] Cookie提取失败: {e}")
        elif action == "CLEAR_COOKIES":
            try:
                await context.clear_cookies()
                deleted_count = 0
                if os.path.exists(cookies_dir):
                    for filename in os.listdir(cookies_dir):
                        if filename.endswith('_cookies.txt') or filename.endswith('_cookies.internal'):
                            try:
                                os.remove(os.path.join(cookies_dir, filename))
                                deleted_count += 1
                            except:
                                pass
                _safe_print(f"[BROWSER] Cookie已清除 (本地文件: {deleted_count}个)")
            except Exception as e:
                _safe_print(f"[BROWSER] Cookie清除失败: {e}")
        elif action == "ENABLE_CAPTURE":
            capture_state["capture_enabled"] = True
            refreshed_pages = await _refresh_capture_runtime()
            _safe_print(f"[BROWSER] 媒体捕获已开启 (主动扫描: {refreshed_pages}页)")
        elif action == "DISABLE_CAPTURE":
            capture_state["capture_enabled"] = False
            _safe_print("[BROWSER] 媒体捕获已关闭")
        elif action == "CLEAR_CAPTURED":
            capture_state["captured_media"].clear()
            capture_state["last_media_warn_at"] = 0.0
            cleared_pages = await _clear_capture_runtime()
            _safe_print(f"[BROWSER] 已清空捕获记录 (页面缓存: {cleared_pages}页)")
        elif action == "PING":
            _safe_print("[BROWSER] PONG")
        elif action == "QUIT" or action == "EXIT":
            return False  # 信号退出
        return True  # 继续运行
    
    async def main_launcher():
        runtime_browser = str(selected_browser or "").strip().lower()
        async with async_playwright() as p:
            available_browsers = detect_installed_browsers()

            def _resolve_runtime_browser(browser_id: str = "") -> Optional[Dict[str, Any]]:
                target = str(browser_id or "").strip().lower()
                if target:
                    for browser in available_browsers:
                        if str(browser.get("id", "")).strip().lower() == target:
                            return browser
                return available_browsers[0] if available_browsers else None

            def _pick_fallback_browser(current_browser_id: str) -> Optional[Dict[str, Any]]:
                current_id = str(current_browser_id or "").strip().lower()
                current_info = _resolve_runtime_browser(current_id)
                current_engine = str((current_info or {}).get("engine", "")).strip().lower()

                for browser in available_browsers:
                    if str(browser.get("id", "")).strip().lower() != current_id and str(browser.get("engine", "")).strip().lower() == current_engine:
                        return browser

                for browser in available_browsers:
                    if str(browser.get("id", "")).strip().lower() != current_id:
                        return browser

                return None

            browser_info = _resolve_runtime_browser(runtime_browser)
            if not browser_info:
                _safe_print("[BROWSER] ERROR: 未检测到可用浏览器，请先安装系统浏览器")
                return

            runtime_browser = str(browser_info.get("id", "")).strip().lower()
            runtime_engine = str(browser_info.get("engine", "chromium")).strip().lower() or "chromium"

            if runtime_browser != str(selected_browser or "").strip().lower():
                _safe_print(f"[BROWSER] AUTO_SELECT|{runtime_browser}|{browser_info.get('name', runtime_browser)}")

            launch_options = _build_launch_options_for_engine(runtime_engine)
            system_proxy = get_system_proxy()
            if system_proxy:
                launch_options["proxy"] = {"server": system_proxy}
                _safe_print(f"[BROWSER] 已启用系统代理: {system_proxy}")

            browser_exe = str(browser_info.get("path", "")).strip()
            if not browser_exe or not os.path.exists(browser_exe):
                _safe_print(f"[BROWSER] ERROR: 所选浏览器不存在: {browser_exe or runtime_browser}")
                return

            launch_options["executable_path"] = browser_exe
            _safe_print(f"[BROWSER] 使用系统浏览器: {browser_exe}")

            context = None
            launcher = _get_playwright_launcher(p, runtime_engine)
            user_data_dir = _get_profile_dir_for_launch(runtime_browser, browser_exe)

            try:
                _safe_print(f"[BROWSER] 使用用户数据目录: {user_data_dir}")
                context = await launcher.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    **launch_options
                )
            except Exception as e:
                temp_user_data_dir = os.path.join(
                    user_data_dir,
                    f"session_{int(time.time())}_{os.getpid()}"
                )
                try:
                    os.makedirs(temp_user_data_dir, exist_ok=True)
                    _safe_print(f"[BROWSER] 默认配置目录启动失败，尝试临时会话目录: {temp_user_data_dir}")
                    context = await launcher.launch_persistent_context(
                        user_data_dir=temp_user_data_dir,
                        **launch_options
                    )
                    _safe_print(f"[BROWSER] 已使用临时会话目录启动成功")
                except Exception as e_retry:
                    e = e_retry

                if context is None:
                    error_msg = str(e).lower()
                    _safe_print(f"[BROWSER] 浏览器启动失败: {error_msg}")

                    fallback_browser = _pick_fallback_browser(runtime_browser)
                    if fallback_browser:
                        fallback_id = str(fallback_browser.get("id", "")).strip().lower()
                        fallback_name = str(fallback_browser.get("name", fallback_id)).strip() or fallback_id
                        fallback_engine = str(fallback_browser.get("engine", "chromium")).strip().lower() or "chromium"
                        fallback_exe = str(fallback_browser.get("path", "")).strip()
                        if fallback_exe and os.path.exists(fallback_exe):
                            fallback_options = _build_launch_options_for_engine(fallback_engine)
                            if system_proxy:
                                fallback_options["proxy"] = {"server": system_proxy}
                            fallback_options["executable_path"] = fallback_exe
                            fallback_launcher = _get_playwright_launcher(p, fallback_engine)
                            try:
                                runtime_browser = fallback_id
                                runtime_engine = fallback_engine
                                user_data_dir = _get_profile_dir_for_launch(runtime_browser, fallback_exe)
                                _safe_print(f"[BROWSER] AUTO_FALLBACK|{runtime_browser}|{fallback_name}")
                                _safe_print(f"[BROWSER] 回退后用户数据目录: {user_data_dir}")
                                context = await fallback_launcher.launch_persistent_context(
                                    user_data_dir=user_data_dir,
                                    **fallback_options
                                )
                                _safe_print(f"[BROWSER] 已自动切换到: {fallback_name}")
                            except Exception as fallback_exc:
                                raise Exception(
                                    f"浏览器启动失败。\n"
                                    f"原浏览器错误: {e}\n"
                                    f"自动回退到 {fallback_name} 也失败: {fallback_exc}"
                                )

                    if context is None:
                        raise

            # === 暴露 reportMedia 接口给 JS 端 ===
            async def report_media_binding(source, data):
                if not capture_state["capture_enabled"]:
                    return
                try:
                    url = data.get('url')
                    if not url or url.startswith('data:'):
                        return
                    
                    # 过滤非媒体 URL
                    if any(p in url.lower() for p in ('.js', '.css', '.jpg', '.png', '.gif', '.ico', 'analytics', 'tracking')):
                        return
                    
                    # 过滤非媒体 Content-Type
                    content_type = data.get('content_type', '').lower()
                    if content_type and any(t in content_type for t in ('application/json', 'text/html', 'text/plain', 'application/xml')):
                        return
                    
                    media_info = {
                        'type': data.get('type', 'DOM'),
                        'url': url,
                        'size': data.get('size', '') or '',
                        'content_type': data.get('content_type', ''),
                        'title': data.get('title', '未知视频'),
                        'page_url': data.get('page_url') or source['page'].url,
                        'is_playing': data.get('is_playing', False),
                        'is_preload': data.get('is_preload', False),
                        'source': data.get('source', 'dom'),
                        'force_update': data.get('force_update', False),
                        'active_video': data.get('active_video', False),
                        'has_drm': data.get('has_drm', False),
                        'quality': data.get('quality', {})
                    }
                    
                    url_key = _normalize_capture_url_key(url)
                    if not url_key:
                        return
                    if not _media_seen(url_key):
                        _media_mark(url_key)
                        _safe_print(f"[MEDIA] {json.dumps(media_info, ensure_ascii=False)}")
                except Exception as exc:
                    _throttled_media_warn(f"[BROWSER] WARN: JS 媒体上报失败: {exc}")
            
            await context.expose_binding("reportMedia", report_media_binding)
            
            # === 注入嗅探脚本 ===
            await context.add_init_script(SNIFFER_JS)
            _safe_print(f"[BROWSER] 已注入嗅探脚本")
            
            pages = context.pages
            if pages:
                page = pages[0]
                for i in range(1, len(pages)):
                    try:
                        await pages[i].close()
                    except:
                        pass
            else:
                page = await context.new_page()
            
            context_state = {"closed": False}
            
            def _on_context_close(*_):
                context_state["closed"] = True
            
            context.on("close", _on_context_close)
            
            # === 网络层媒体嗅探 ===
            MANIFEST_EXTS = ('.m3u8', '.mpd', '.ism', '.ismc', '.f4m')
            COMPLETE_VIDEO_EXTS = ('.mp4', '.webm', '.flv', '.mkv', '.mov', '.avi', '.f4v', '.3gp', '.wmv', '.asf', '.rm', '.rmvb')
            FRAGMENT_EXTS = ('.ts', '.m4s', '.m4a', '.m4v', '.aac', '.mp3')
            MEDIA_TYPES = (
                'video/', 'audio/',
                'application/vnd.apple.mpegurl', 'application/dash+xml', 'application/x-mpegurl',
                'application/mp4', 'application/vnd.ms-sstr+xml'
            )
            EXCLUDE = (
                'googleads', 'doubleclick', 'googlesyndication', 'adservice', 'adserver',
                'pagead', 'pubads', 'smartadserver', 'rubiconproject', 'adsystem',
                'moatads', 'outbrain', 'taboola', 'criteo', 'prebid', 'adtech', 'adnxs',
                'advertising', 'adform', 'mediavine', 'teads',
                'adsafeprotected', 'imasdk.googleapis', 'securepubads',
                'analytics', 'tracking', 'beacon', 'pixel', 'telemetry', 'metrics',
                'google-analytics', 'facebook.com/tr',
                'data.bilibili.com/log', 'log-reporter',
                '.gif', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.ico', '.js', '.css',
                '.bin', '.webmask',
                'thumbnail', 'poster', 'sprite', 'preview', 'thumb', 'cover', 'avatar',
                '/pbp/', '.cnf', '/x/player/', '/x/web-interface/', 'api.bilibili.com',
                'cmaskboss', 'cmask', 'videoshotpvhdboss', 'hdslb.com/bfs',
                'webmanifest', 'manifest.json',
                'heartbeat', 'ping', 'alive', 'health', 'status'
            )
            NON_MEDIA_CT = (
                'application/json', 'text/plain', 'text/html', 'text/xml',
                'application/xml', 'application/javascript',
                'application/manifest+json'
            )
            VIDEO_KEYWORDS = ('video', 'media', 'stream', 'play', 'manifest', 'playlist', 'content', 'cdn', 'player')
            MIN_MEDIA_SIZE = 500 * 1024
            
            async def on_response(response):
                if not capture_state["capture_enabled"]:
                    return
                try:
                    url = response.url
                    if not url or url.startswith('data:'):
                        return
                    
                    ct = response.headers.get('content-type', '').lower()
                    cl = response.headers.get('content-length', '')
                    url_l = url.lower()
                    
                    if any(p in url_l for p in EXCLUDE):
                        return
                    if any(t in ct for t in NON_MEDIA_CT):
                        return
                    
                    # 排除音频流 (防范抖音音频/视频分离时误抓音频链接)
                    # 'media-audio' 匹配抖音 CDN 的 media-audio-und-mp4a 路径格式
                    if 'audio/' in ct or 'mime=audio' in url_l or '/audio/' in url_l or 'media-audio' in url_l:
                        return
                    
                    hit = False
                    m_type = 'Media'
                    
                    is_fragment = any(e in url_l for e in FRAGMENT_EXTS)
                    is_manifest = any(e in url_l for e in MANIFEST_EXTS)
                    is_bilibili_cdn = any(p in url_l for p in ('bilivideo.com', 'bilivideo.cn', 'upos-sz-', 'upos-hz-', 'upos-'))
                    
                    if is_fragment and not is_manifest and not is_bilibili_cdn:
                        return
                    
                    if any(e in url_l for e in MANIFEST_EXTS):
                        hit = True
                    if not hit and any(e in url_l for e in COMPLETE_VIDEO_EXTS):
                        hit = True
                    if not hit and any(t in ct for t in MEDIA_TYPES) and 'octet-stream' not in ct:
                        hit = True
                    if not hit:
                        if any(k in url_l for k in ('googlevideo.com', 'videoplayback', 'm3u8', 'mpd')):
                            hit = True
                        elif any(p in url_l for p in ('bilivideo.com', 'bilivideo.cn', 'upos-sz-', 'upos-hz-', 'upos-', '/upgcxcode/')):
                            hit = True
                    if not hit and cl:
                        try:
                            if int(cl) > MIN_MEDIA_SIZE and any(k in url_l for k in VIDEO_KEYWORDS):
                                hit = True
                        except:
                            pass
                    
                    if hit:
                        if 'googlevideo.com' in url_l:
                            query = parse_qs(url.split('?', 1)[1]) if '?' in url else {}
                            v_id = query.get('id', [''])[0]
                            itag = query.get('itag', [''])[0]
                            url_key = f"youtube_{v_id}_{itag}" if v_id and itag else _normalize_capture_url_key(url)
                        else:
                            url_key = _normalize_capture_url_key(url)
                        if not url_key:
                            return
                        
                        if not _media_seen(url_key):
                            _media_mark(url_key)
                            
                            if '.m3u8' in url_l or 'mpegurl' in ct:
                                m_type = 'HLS'
                            elif '.mpd' in url_l or 'dash' in ct:
                                m_type = 'DASH'
                            elif 'googlevideo.com' in url_l:
                                m_type = 'YouTube'
                            elif any(p in url_l for p in ('bilivideo.com', 'bilivideo.cn')):
                                m_type = 'Bilibili'
                            elif any(p in url_l for p in ('douyinvod.com', 'douyincdn.com', 'tos-cn-v')):
                                m_type = 'Douyin'
                            elif 'tiktokcdn.com' in url_l:
                                m_type = 'TikTok'
                            elif '.mp4' in url_l:
                                m_type = 'MP4'
                            elif 'video/' in ct:
                                m_type = 'Video'
                            elif 'audio/' in ct:
                                m_type = 'Audio'
                            
                            title = t("未知视频")
                            page_url = ""
                            try:
                                title = await response.frame.page.title()
                                page_url = response.frame.page.url
                            except:
                                pass
                            
                            size = ""
                            if cl:
                                try:
                                    cl_num = int(cl)
                                    size = f"{cl_num/(1024*1024):.1f}MB" if cl_num > 1048576 else f"{cl_num/1024:.1f}KB"
                                except Exception:
                                    size = ""
                            _safe_print(f"[MEDIA] {json.dumps({'type': m_type, 'url': url, 'size': size, 'content_type': ct, 'title': title, 'page_url': page_url}, ensure_ascii=False)}")
                            
                            # 【YouTube 兜底】捕获到 googlevideo 直链时，同时把页面 URL 作为候选发出
                            # 直链带 expire/ip 绑定有时效，页面 URL 可交给 yt-dlp 官方提取器稳定下载
                            if m_type == 'YouTube' and page_url:
                                try:
                                    page_host = urlparse(page_url).netloc.lower()
                                    is_yt_page = ('youtube.com' in page_host or 'youtu.be' in page_host)
                                    page_path = urlparse(page_url).path.lower()
                                    is_yt_video_page = is_yt_page and (
                                        '/watch' in page_path or page_path == '/' or page_path == '' or
                                        '/shorts/' in page_path or '/live/' in page_path or
                                        page_host == 'youtu.be'
                                    )
                                    if is_yt_video_page:
                                        page_key = _normalize_capture_url_key(page_url)
                                        if page_key and not _media_seen(page_key):
                                            _media_mark(page_key)
                                            _safe_print(f"[MEDIA] {json.dumps({'type': 'YouTubePage', 'url': page_url, 'size': '', 'content_type': '', 'title': title, 'page_url': page_url}, ensure_ascii=False)}")
                                except Exception:
                                    pass
                except Exception as exc:
                    _throttled_media_warn(f"[BROWSER] WARN: 网络媒体捕获异常: {exc}")
            
            # === VDH 式媒体捕获（CDP 嗅探 + 站点适配器 + 清单富化）===
            _capture_runners = []

            def _capture_media_payload(candidate, force_update):
                quality = candidate.get('quality')
                if not isinstance(quality, dict):
                    quality = {}
                headers = candidate.get('headers')
                if not isinstance(headers, dict):
                    headers = {}
                try:
                    current_page_url = page.url
                except Exception:
                    current_page_url = ""
                return {
                    'type': candidate.get('type') or 'Media',
                    'url': candidate.get('url') or '',
                    'size': candidate.get('size') or '',
                    'content_type': candidate.get('content_type') or '',
                    'title': candidate.get('title') or t("未知视频"),
                    'page_url': candidate.get('page_url') or current_page_url,
                    'source': candidate.get('source') or 'cdp',
                    'quality': quality,
                    'has_drm': bool(candidate.get('has_drm')),
                    'audio_only': bool(candidate.get('audio_only')),
                    'is_manifest': bool(candidate.get('is_manifest')),
                    'headers': headers,
                    'summary': candidate.get('summary') or '',
                    'force_update': bool(force_update),
                }

            def _capture_emit(candidate, force_update=False):
                if not capture_state["capture_enabled"]:
                    return
                try:
                    payload = _capture_media_payload(candidate, force_update)
                    url = payload['url']
                    if not url or url.startswith('data:'):
                        return
                    url_key = _normalize_capture_url_key(url)
                    if not url_key:
                        return
                    if not force_update and _media_seen(url_key):
                        return
                    _media_mark(url_key)
                    _safe_print(f"[MEDIA] {json.dumps(payload, ensure_ascii=False)}")
                    # 【YouTube 兜底】googlevideo 直链有时效，同时把页面 URL 交给 yt-dlp
                    if payload['type'] == 'YouTube' and payload['page_url']:
                        try:
                            page_host = urlparse(payload['page_url']).netloc.lower()
                            page_path = urlparse(payload['page_url']).path.lower()
                            is_yt_page = ('youtube.com' in page_host or 'youtu.be' in page_host)
                            is_yt_video_page = is_yt_page and (
                                '/watch' in page_path or page_path in ('', '/') or
                                '/shorts/' in page_path or '/live/' in page_path or
                                page_host == 'youtu.be'
                            )
                            if is_yt_video_page:
                                page_key = _normalize_capture_url_key(payload['page_url'])
                                if page_key and not _media_seen(page_key):
                                    _media_mark(page_key)
                                    _safe_print(f"[MEDIA] {json.dumps({'type': 'YouTubePage', 'url': payload['page_url'], 'size': '', 'content_type': '', 'title': payload['title'], 'page_url': payload['page_url']}, ensure_ascii=False)}")
                        except Exception:
                            pass
                except Exception as exc:
                    _throttled_media_warn(f"[BROWSER] WARN: VDH 媒体上报失败: {exc}")

            async def _start_capture(page_obj, target_url):
                if _VDH_CAPTURE is None:
                    return None
                try:
                    runner = _VDH_CAPTURE.CaptureRunner(emit=_capture_emit)
                    attached = await runner.start(context, page_obj, target_url or "")
                    runner.cdp_attached = bool(attached)
                    _capture_runners.append(runner)
                    adapter_name = getattr(getattr(runner, 'adapter', None), 'name', '-')
                    _safe_print(f"[BROWSER] VDH 捕获层已挂载 (cdp={bool(attached)}, adapter={adapter_name})")
                    return runner
                except Exception as exc:
                    _safe_print(f"[BROWSER] WARN: VDH 捕获层挂载失败: {exc}")
                    return None

            async def _capture_after_navigation(page_obj):
                for runner in list(_capture_runners):
                    try:
                        runner.set_target(page_obj.url)
                        await runner.after_navigation(page_obj)
                    except Exception as exc:
                        _safe_print(f"[BROWSER] WARN: VDH 站点探测失败: {exc}")

            def _on_framenavigated(frame):
                try:
                    if frame is not frame.page.main_frame:
                        return
                    page_obj = frame.page
                except Exception:
                    return
                try:
                    asyncio.ensure_future(_capture_after_navigation(page_obj))
                except Exception:
                    pass

            def _on_new_page(new_page):
                try:
                    asyncio.ensure_future(_start_capture(new_page, ""))
                except Exception:
                    pass
                try:
                    new_page.on('framenavigated', _on_framenavigated)
                except Exception:
                    pass
            
            # === 初始页面导航 ===
            if initial_url:
                default_url = initial_url
            else:
                default_url = _get_default_homepage(runtime_browser, runtime_engine)
            
            # === 媒体捕获层挂载（VDH 式 CDP 嗅探；失败则回退旧嗅探）===
            _vdh_runner = await _start_capture(page, default_url) if _VDH_CAPTURE is not None else None
            if _vdh_runner is None or not getattr(_vdh_runner, 'cdp_attached', False):
                page.on('response', on_response)
                context.on('page', lambda p: p.on('response', on_response))
                _safe_print("[BROWSER] 媒体捕获：旧嗅探作为回退/补充已启用")
            else:
                context.on('page', _on_new_page)
            page.on('framenavigated', _on_framenavigated)

            try:
                await page.goto(default_url, wait_until="domcontentloaded", timeout=30000)
                _safe_print(f"[BROWSER] 已打开页面: {default_url}")
            except Exception as e:
                _safe_print(f"[BROWSER] 打开页面失败: {e}")
            await _capture_after_navigation(page)
            
            # === 发送 READY 信号（GUI 依赖此信号切换到"运行中"状态）===
            # 【关键修复】无论页面导航是否成功，都发送 READY 信号
            # 这样可以确保 GUI 状态正确更新，即使用户选择的内置浏览器启动失败并回退到系统浏览器
            _safe_print("[BROWSER] READY")
            
            # === 启动 stdin 读取线程 ===
            _ensure_stdin_reader()
            if pending_cmds:
                for pending in pending_cmds:
                    cmd_queue.put(pending)
                pending_cmds.clear()
            
            # === 主事件循环：处理命令 + 检测浏览器关闭 ===
            while True:
                try:
                    # 处理所有待处理的命令
                    while not cmd_queue.empty():
                        cmd = cmd_queue.get_nowait()
                        if cmd:
                            should_continue = await handle_command(cmd, page, context)
                            if not should_continue:
                                _safe_print(f"[BROWSER] 浏览器正在关闭...")
                                await context.close()
                                _safe_print("[BROWSER] CLOSED")
                                return
                    
                    if context_state["closed"]:
                        _safe_print("[BROWSER] 检测到上下文已关闭，准备退出主循环")
                        break
                    
                    # 系统浏览器下 pages 可能短时为空；不主动新建标签页，避免误判并导致进程自退
                    if not context.pages:
                        await asyncio.sleep(0.2)
                        continue
                    
                    await asyncio.sleep(0.1)
                except Exception as loop_err:
                    # 记录异常并尽量恢复，避免误报“启动失败”
                    _safe_print(f"[BROWSER] 主循环异常: {loop_err}")
                    loop_err_text = str(loop_err).lower()
                    if context_state["closed"] or "has been closed" in loop_err_text or "context closed" in loop_err_text or "browser has been closed" in loop_err_text:
                        _safe_print("[BROWSER] 主循环异常判定为上下文已关闭")
                        break
                    await asyncio.sleep(0.2)
                    continue
            
            _safe_print(f"[BROWSER] 浏览器正在关闭...")
            try:
                await context.close()
            except:
                pass
            _safe_print("[BROWSER] CLOSED")

    try:
        asyncio.run(main_launcher())
    except Exception as e:
        error_str = str(e)
        _safe_print(f"[BROWSER] ERROR: {error_str}")
        _safe_print("[BROWSER] CLOSED")


def _build_launcher_bootstrap_script(module_hint_path: str) -> str:
    """生成轻量 launcher 脚本，统一复用本模块的 run_launcher 实现。"""
    hint_literal = json.dumps(str(module_hint_path or ""), ensure_ascii=False)
    lines = [
        "import importlib.util",
        "import os",
        "import sys",
        "",
        f"SOURCE_HINT = {hint_literal}",
        "",
        "def _iter_candidates():",
        "    exe_dir = os.path.dirname(os.path.abspath(sys.executable))",
        "    script_dir = os.path.dirname(os.path.abspath(__file__))",
        "    meipass = getattr(sys, '_MEIPASS', '')",
        "    raw = [",
        "        SOURCE_HINT,",
        "        os.path.join(script_dir, 'playwright_browser.py'),",
        "        os.path.join(script_dir, '_internal', 'playwright_browser.py'),",
        "        os.path.join(exe_dir, 'playwright_browser.py'),",
        "        os.path.join(exe_dir, '_internal', 'playwright_browser.py'),",
        "    ]",
        "    if meipass:",
        "        raw.append(os.path.join(meipass, 'playwright_browser.py'))",
        "        raw.append(os.path.join(meipass, '_internal', 'playwright_browser.py'))",
        "    seen = set()",
        "    for path in raw:",
        "        norm = os.path.normpath(path) if path else ''",
        "        if not norm or norm in seen:",
        "            continue",
        "        seen.add(norm)",
        "        if os.path.isfile(norm):",
        "            yield norm",
        "",
        "def _load_module():",
        "    last_error = None",
        "    for path in _iter_candidates():",
        "        try:",
        "            spec = importlib.util.spec_from_file_location('playwright_browser_runtime', path)",
        "            if spec and spec.loader:",
        "                mod = importlib.util.module_from_spec(spec)",
        "                spec.loader.exec_module(mod)",
        "                return mod, None",
        "        except Exception as exc:",
        "            last_error = exc",
        "    try:",
        "        import playwright_browser as mod",
        "        return mod, None",
        "    except Exception as exc:",
        "        return None, (last_error or exc)",
        "",
        "module, load_error = _load_module()",
        "if module is None:",
        "    print(f\"[BROWSER] ERROR: BROWSER_UNKNOWN|无法加载 playwright_browser: {load_error}\", flush=True)",
        "    raise SystemExit(1)",
        "",
        "try:",
        "    module.run_launcher()",
        "except Exception as exc:",
        "    print(f\"[BROWSER] ERROR: BROWSER_UNKNOWN|launcher 运行失败: {exc}\", flush=True)",
        "    raise SystemExit(1)",
        "",
    ]
    return "\n".join(lines)


# 兼容保留：历史版本完整脚本（当前启动路径已统一为 _build_launcher_bootstrap_script -> run_launcher）
BROWSER_LAUNCHER_SCRIPT = r'''
import asyncio
import sys
import os
import json
import threading
import queue
import time
from urllib.parse import urlparse, parse_qs

# 强制使用 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

def get_system_proxy():
    """获取 Windows 系统代理设置"""
    if sys.platform != 'win32':
        return os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy') or \
               os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    
    try:
        import winreg
        internet_settings = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        )
        try:
            proxy_enable, _ = winreg.QueryValueEx(internet_settings, 'ProxyEnable')
            if proxy_enable:
                proxy_server, _ = winreg.QueryValueEx(internet_settings, 'ProxyServer')
                if proxy_server:
                    if '=' in proxy_server:
                        for part in proxy_server.split(';'):
                            if '=' in part:
                                protocol, addr = part.split('=', 1)
                                if protocol.lower() in ('http', 'https'):
                                    if not addr.startswith('http'):
                                        addr = f'http://{addr}'
                                    print(f"[系统代理] 检测到代理: {addr}", flush=True)
                                    return addr
                    else:
                        if not proxy_server.startswith('http'):
                            proxy_server = f'http://{proxy_server}'
                        print(f"[系统代理] 检测到代理: {proxy_server}", flush=True)
                        return proxy_server
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(internet_settings)
    except Exception as e:
        print(f"[系统代理] 读取设置失败: {e}", flush=True)
    
    env_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy') or \
                os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    if env_proxy:
        print(f"[系统代理] 从环境变量检测到代理: {env_proxy}", flush=True)
        return env_proxy
    
    print("[系统代理] 未检测到系统代理，使用直连", flush=True)
    return None

# 全局状态
state = {
    "capture_enabled": False,
    "captured_media": {},
    "current_playing_key": ""
}

cmd_queue = queue.Queue()

def normalize_url_key(url):
    if not url:
        return ""
    try:
        u = str(url).strip()
        if u.startswith("//"):
            u = "https:" + u
        if "?" in u:
            u = u.split("?", 1)[0]
        u = u.rstrip("/")
        return u.lower()
    except Exception:
        return str(url).lower()

def should_emit_media(media_info):
    url_key = normalize_url_key(media_info.get("url", ""))
    if not url_key:
        return False, ""

    prev = state["captured_media"].get(url_key)
    force_update = bool(media_info.get("force_update"))
    is_playing = bool(media_info.get("is_playing"))
    should_emit = prev is None

    # 已存在时，只有在“升级为播放中”或强制更新时才再次上报
    if prev is not None:
        if force_update or (is_playing and not bool(prev.get("is_playing"))):
            should_emit = True
            merged = dict(prev)
            merged.update(media_info)
            merged["is_playing"] = True if is_playing else bool(prev.get("is_playing"))
            if merged.get("is_playing"):
                merged["is_preload"] = False
            state["captured_media"][url_key] = merged
        else:
            # 只更新缓存，不重复上报
            merged = dict(prev)
            merged.update(media_info)
            state["captured_media"][url_key] = merged
    else:
        state["captured_media"][url_key] = dict(media_info)

    if len(state["captured_media"]) > 1500:
        state["captured_media"].clear()
        state["captured_media"][url_key] = dict(media_info)

    if bool(state["captured_media"][url_key].get("is_playing")):
        state["current_playing_key"] = url_key

    return should_emit, url_key

def stdin_reader():
    stream = getattr(sys, "stdin", None) or getattr(sys, "__stdin__", None)
    if stream is None:
        print("[BROWSER] WARN: stdin 不可用，命令通道已禁用", flush=True)
        return
    while True:
        try:
            line = stream.readline()
            if not line: break
            cmd = line.strip()
            if cmd:
                cmd_queue.put(cmd)
        except Exception as e:
            print(f"[BROWSER] WARN: 读取命令失败: {e}", flush=True)
            break

async def handle_command(cmd, page, context, cookies_dir):
    parts = cmd.split(" ", 1)
    action = parts[0].upper()

    async def _clear_capture_runtime():
        cleared_pages = 0
        try:
            for target_page in list(context.pages):
                try:
                    ok = await target_page.evaluate(
                        """() => {
                            if (typeof window.__pro_media_sniffer_clear_captured === 'function') {
                                return window.__pro_media_sniffer_clear_captured();
                            }
                            return false;
                        }"""
                    )
                    if ok:
                        cleared_pages += 1
                except Exception:
                    continue
        except Exception:
            pass
        return cleared_pages

    async def _refresh_capture_runtime():
        refreshed_pages = 0
        try:
            for target_page in list(context.pages):
                try:
                    ok = await target_page.evaluate(
                        """() => {
                            if (typeof window.__pro_media_sniffer_refresh_capture === 'function') {
                                return window.__pro_media_sniffer_refresh_capture();
                            }
                            return false;
                        }"""
                    )
                    if ok:
                        refreshed_pages += 1
                except Exception:
                    continue
        except Exception:
            pass
        return refreshed_pages
    
    if action == "GOTO":
        url = parts[1] if len(parts) > 1 else ""
        if url:
            if not url.startswith(("http://", "https://")): url = "https://" + url
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                print(f"[BROWSER] 导航到: {page.url}", flush=True)
            except Exception as e:
                print(f"[BROWSER] WARN: 导航失败: {e}", flush=True)
    elif action == "BACK":
        try:
            await page.go_back()
        except Exception as e:
            print(f"[BROWSER] WARN: 后退失败: {e}", flush=True)
    elif action == "FORWARD":
        try:
            await page.go_forward()
        except Exception as e:
            print(f"[BROWSER] WARN: 前进失败: {e}", flush=True)
    elif action == "RELOAD":
        try:
            await page.reload()
        except Exception as e:
            print(f"[BROWSER] WARN: 刷新失败: {e}", flush=True)
    elif action == "COOKIES":
        cookies = await context.cookies()
        if cookies:
            domain = urlparse(page.url).netloc or "unknown"
            os.makedirs(cookies_dir, exist_ok=True)
            safe_domain = "".join([c for c in domain if c.isalpha() or c.isdigit() or c in ('-', '_', '.')]).rstrip()
            filepath = os.path.join(cookies_dir, f"{safe_domain}_cookies.txt")
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n# Source: internal_browser\n# Domain: " + domain + "\n\n")
                for c in cookies:
                    domain_val = c.get('domain', '')
                    flag = "TRUE" if domain_val.startswith('.') else "FALSE"
                    path = c.get('path', '/')
                    secure = "TRUE" if c.get('secure', False) else "FALSE"
                    expires = int(c.get('expires', 0))
                    if expires <= 0: expires = int(time.time()) + 31536000
                    name = c.get('name', '')
                    value = c.get('value', '')
                    if c.get('httpOnly', False): domain_val = f"#HttpOnly_{domain_val}"
                    f.write(f"{domain_val}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
            marker_path = filepath.replace('_cookies.txt', '_cookies.internal')
            with open(marker_path, 'w', encoding='utf-8') as mf: mf.write('internal_browser')
            print(f"[BROWSER] Cookie已保存: {filepath} ({len(cookies)}个)", flush=True)
        else:
            print("[BROWSER] 没有Cookie可保存", flush=True)
    elif action == "CLEAR_COOKIES":
        await context.clear_cookies()
        deleted_count = 0
        if os.path.exists(cookies_dir):
            for filename in os.listdir(cookies_dir):
                if filename.endswith('_cookies.txt') or filename.endswith('_cookies.internal'):
                    try:
                        os.remove(os.path.join(cookies_dir, filename))
                        deleted_count += 1
                    except: pass
        print(f"[BROWSER] Cookie已清除 (本地文件: {deleted_count}个)", flush=True)
    elif action == "ENABLE_CAPTURE":
        state["capture_enabled"] = True
        refreshed_pages = await _refresh_capture_runtime()
        print(f"[BROWSER] 媒体捕获已开启 (主动扫描: {refreshed_pages}页)", flush=True)
    elif action == "DISABLE_CAPTURE":
        state["capture_enabled"] = False
        print("[BROWSER] 媒体捕获已关闭", flush=True)
    elif action == "CLEAR_CAPTURED":
        state["captured_media"].clear()
        state["current_playing_key"] = ""
        cleared_pages = await _clear_capture_runtime()
        print(f"[BROWSER] 已清空捕获记录 (页面缓存: {cleared_pages}页)", flush=True)
    elif action == "PING":
        print("[BROWSER] PONG", flush=True)
    elif action == "QUIT":
        sys.exit(0)
''' + f'\nSNIFFER_JS = {repr(SNIFFER_JS)}\n' + r'''
def find_browser_path(browser_name):
    """查找系统浏览器路径"""
    import shutil
    if browser_name == "chrome":
        paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        exe_name = "chrome.exe"
    elif browser_name == "edge":
        paths = [
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        ]
        exe_name = "msedge.exe"
    else:
        return None
    for path in paths:
        if os.path.exists(path):
            return path
    return shutil.which(exe_name)

def pick_system_browser_for_media():
    """媒体播放优先使用系统浏览器（Edge -> Chrome）。"""
    edge_exe = find_browser_path("edge")
    if edge_exe and os.path.exists(edge_exe):
        return "edge", edge_exe

    chrome_exe = find_browser_path("chrome")
    if chrome_exe and os.path.exists(chrome_exe):
        return "chrome", chrome_exe

    return None, None

def find_bundled_chromium():
    """查找内置 Chromium"""
    import glob
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    patterns = [
        os.path.join(base_dir, "playwright_browsers", "chromium-*", "chrome-win", "chrome.exe"),
        os.path.join(base_dir, "_internal", "playwright_browsers", "chromium-*", "chrome-win", "chrome.exe"),
        os.path.join(base_dir, "_internal", "playwright", "driver", "package", ".local-browsers", "chromium-*", "chrome-win", "chrome.exe"),
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None

async def run():
    from playwright.async_api import async_playwright
    browser_type = sys.argv[1] if len(sys.argv) > 1 else "chromium"
    cookies_dir = sys.argv[2] if len(sys.argv) > 2 else "cookies"
    initial_url = sys.argv[3] if len(sys.argv) > 3 else ""
    
    p = await async_playwright().start()
    browser_data_root = os.path.join(os.path.dirname(cookies_dir), "browser_data")
    
    launch_options = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled", "--disable-infobars", "--no-first-run", "--lang=zh-CN", "--test-type"],
        "ignore_default_args": ["--enable-automation"],
        "no_viewport": True,
        "locale": "zh-CN"
    }
    
    system_proxy = get_system_proxy()
    if system_proxy:
        launch_options["proxy"] = {"server": system_proxy}
        print(f"[BROWSER] 已启用系统代理: {system_proxy}", flush=True)
    
    browser_exe = None
    if browser_type == "chromium":
        fallback_browser, fallback_exe = pick_system_browser_for_media()
        if fallback_exe:
            browser_type = fallback_browser
            browser_exe = fallback_exe
            print(f"[BROWSER] 内置 Chromium 缺少 H264/AAC，已自动切换系统浏览器: {fallback_browser}", flush=True)
        else:
            print("[BROWSER] WARN: 内置 Chromium 缺少 H264/AAC 编解码，部分站点可能无法播放 HTML5 视频", flush=True)
    elif browser_type == "chrome":
        browser_exe = find_browser_path("chrome")
    elif browser_type == "edge":
        browser_exe = find_browser_path("edge")
    
    if browser_exe and os.path.exists(browser_exe):
        launch_options["executable_path"] = browser_exe
        print(f"[BROWSER] 使用系统浏览器: {browser_exe}", flush=True)
    else:
        bundled = find_bundled_chromium()
        if bundled and os.path.exists(bundled):
            launch_options["executable_path"] = bundled
            print(f"[BROWSER] 使用内置浏览器: {bundled}", flush=True)
        else:
            print("[BROWSER] ERROR: 未找到可用浏览器", flush=True)
            sys.exit(1)
    
    try:
        user_data_dir = os.path.join(browser_data_root, browser_type)
        os.makedirs(user_data_dir, exist_ok=True)
        print(f"[BROWSER] 使用用户数据目录: {user_data_dir}", flush=True)
        context = await p.chromium.launch_persistent_context(
            user_data_dir,
            **launch_options
        )
        
        # 暴露汇报接口
        async def report_media_binding(source, data):
            if not state["capture_enabled"]: return
            try:
                url = data.get('url')
                if not url or url.startswith('data:'): return
                
                # 过滤掉一些常见的非媒体 URL
                if any(p in url.lower() for p in ('.js', '.css', '.jpg', '.png', '.gif', '.ico', 'analytics', 'tracking')):
                    return
                
                # 过滤非媒体 Content-Type（如 JSON、HTML 等）
                content_type = data.get('content_type', '').lower()
                if content_type and any(t in content_type for t in ('application/json', 'text/html', 'text/plain', 'application/xml')):
                    return
                
                # 统一汇报格式
                media_info = {
                    'type': data.get('type', 'DOM'),
                    'url': url,
                    'size': data.get('size', '') or '',
                    'content_type': data.get('content_type', ''),
                    'title': data.get('title', '未知视频'),
                    'page_url': data.get('page_url') or source['page'].url,
                    'is_playing': data.get('is_playing', False),
                    'is_preload': data.get('is_preload', False),
                    'source': data.get('source', 'dom'),
                    'force_update': data.get('force_update', False),
                    'active_video': data.get('active_video', False),
                    'has_drm': data.get('has_drm', False),
                    'quality': data.get('quality', {})
                }
                
                should_emit, _ = should_emit_media(media_info)
                if should_emit:
                    print(f"[MEDIA] {json.dumps(media_info, ensure_ascii=False)}", flush=True)
            except: pass

        await context.expose_binding("reportMedia", report_media_binding)
        # 注入嗅探脚本
        await context.add_init_script(SNIFFER_JS)
        
        page = context.pages[0] if context.pages else await context.new_page()
    except Exception as e:
        print(f"[BROWSER] ERROR: {e}", flush=True)
        await p.stop()
        sys.exit(1)

    # Manifest 文件（优先捕获）
    MANIFEST_EXTS = ('.m3u8', '.mpd', '.ism', '.ismc', '.f4m')
    
    # 完整视频文件
    COMPLETE_VIDEO_EXTS = ('.mp4', '.webm', '.flv', '.mkv', '.mov', '.avi', '.f4v', '.3gp', '.wmv', '.asf', '.rm', '.rmvb')
    
    # 片段文件（不捕获）
    FRAGMENT_EXTS = ('.ts', '.m4s', '.m4a', '.m4v', '.aac', '.mp3')
    
    # Content-Type 匹配
    MEDIA_TYPES = (
        'video/', 'audio/', 
        'application/vnd.apple.mpegurl', 'application/dash+xml', 'application/x-mpegurl',
        'application/mp4', 'application/vnd.ms-sstr+xml'
    )
    
    # 排除的 URL 模式 - 增强版（与 JS 端保持同步）
    EXCLUDE = (
        # 广告相关
        'googleads', 'doubleclick', 'googlesyndication', 'adservice', 'adserver',
        'pagead', 'pubads', 'smartadserver', 'rubiconproject', 'adsystem',
        'moatads', 'outbrain', 'taboola', 'criteo', 'prebid', 'adtech', 'adnxs',
        'advertising', 'adform', 'mediavine', 'teads', 'springserve', 'spotxchange',
        'indexexchange', 'openx', 'appnexus', 'amazon-adsystem', 'casalemedia',
        'adsafeprotected', 'imasdk.googleapis', 'securepubads',
        # 追踪和分析
        'analytics', 'tracking', 'beacon', 'pixel', 'telemetry', 'metrics',
        'google-analytics', 'facebook.com/tr', 'hm.baidu.com', 'log.mmstat.com',
        'scorecardresearch', 'quantserve', 'segment.io', 'mixpanel', 'amplitude',
        'data.bilibili.com/log', 'log-reporter',
        # 图片和静态资源
        '.gif', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.ico', '.js', '.css',
        '.bin', '.webmask',  # B站非视频文件
        'thumbnail', 'poster', 'sprite', 'preview', 'thumb', 'cover', 'avatar',
        # B站非视频文件和 API
        '/pbp/', 'pbp/data', '.cnf', 'event.cnf', 'polaris_web_conf',
        '/x/player/', '/x/web-interface/', 'api.bilibili.com',
        'cmaskboss', 'cmask', 'videoshotpvhdboss',  # B站弹幕遮罩和缩略图
        'hdslb.com/bfs',  # B站静态资源
        # PWA manifest (不是视频 manifest)
        'webmanifest', 'site.webmanifest', 'manifest.json',
        # 其他
        'heartbeat', 'ping', 'alive', 'health', 'status', 'impression', 'postback'
    )
    
    # 非媒体 Content-Type（直接排除）
    NON_MEDIA_CT = (
        'application/json', 'text/plain', 'text/html', 'text/xml', 
        'application/xml', 'application/javascript',
        'application/manifest+json'  # PWA manifest
    )
    
    # 视频相关的 URL 关键词
    VIDEO_KEYWORDS = ('video', 'media', 'stream', 'play', 'manifest', 'playlist', 'content', 'cdn', 'player', 'clip', 'movie', 'episode', 'watch', 'embed', 'progressive', 'adaptive', 'vod', 'm3u8', 'mpd')
    
    # 最小文件大小阈值 (500KB)
    MIN_MEDIA_SIZE = 500 * 1024
    
    async def on_response(response):
        if not state["capture_enabled"]: return
        try:
            url = response.url
            if not url or url.startswith('data:'): return
            
            ct = response.headers.get('content-type', '').lower()
            cl = response.headers.get('content-length', '')
            url_l = url.lower()
            
            # 排除已知的非媒体请求
            if any(p in url_l for p in EXCLUDE): return
            
            # 排除明显非媒体的 Content-Type
            if any(t in ct for t in NON_MEDIA_CT): return

            # 排除音频流 (防范抖音音频/视频分离时误抓音频链接)
            # 'media-audio' 匹配抖音 CDN 的 media-audio-und-mp4a 路径格式
            if 'audio/' in ct or 'mime=audio' in url_l or '/audio/' in url_l or 'media-audio' in url_l:
                return
            
            hit = False
            m_type = 'Media'
            
            # 判断是否为片段文件
            is_fragment = any(url_l.endswith(e) or (e in url_l and len(url_l) > url_l.index(e) + len(e) and url_l[url_l.index(e) + len(e)] in ('?', '&', '#', '/')) for e in FRAGMENT_EXTS)
            is_manifest = any(e in url_l for e in MANIFEST_EXTS)
            
            # 检测是否是 B站视频 CDN（特殊处理）
            is_bilibili_cdn = any(p in url_l for p in ('bilivideo.com', 'bilivideo.cn', 'upos-sz-', 'upos-hz-', 'upos-'))
            
            # 如果是片段文件，跳过（但 B站 CDN 的 m4s 除外）
            if is_fragment and not is_manifest and not is_bilibili_cdn:
                return
            
            # 规则1: URL 包含 manifest 扩展名
            if any(e in url_l for e in MANIFEST_EXTS):
                hit = True
            
            # 规则2: URL 包含完整视频扩展名
            if not hit and any(e in url_l for e in COMPLETE_VIDEO_EXTS):
                hit = True
            
            # 规则3: Content-Type 明确是视频/音频（排除 octet-stream 避免误报）
            if not hit and any(t in ct for t in MEDIA_TYPES) and 'octet-stream' not in ct:
                hit = True
            
            # 规则4: 特殊平台规则 (YouTube, Bilibili 等)
            if not hit:
                if any(k in url_l for k in ('googlevideo.com', 'videoplayback', 'm3u8', 'mpd')):
                    hit = True
                # B站视频 CDN 特殊处理：扩展识别规则
                elif any(p in url_l for p in ('bilivideo.com', 'bilivideo.cn', 'upos-sz-', 'upos-hz-', 'upos-', '/upgcxcode/')):
                    hit = True
                    print(f"[DEBUG] B站视频匹配: {url[:80]}...", flush=True)
            
            # 规则5: 大文件 + 二进制流 = 可能是视频
            if not hit and cl:
                try:
                    file_size = int(cl)
                    if file_size > MIN_MEDIA_SIZE:
                        if any(k in url_l for k in VIDEO_KEYWORDS):
                            hit = True
                except: pass
            
            if hit:
                # 类型细化 - 与 JS 端保持一致的优先级规则
                # 优先级 1: Manifest 类型
                if '.m3u8' in url_l or 'mpegurl' in ct:
                    m_type = 'HLS'
                elif '.mpd' in url_l or 'dash' in ct:
                    m_type = 'DASH'
                elif '.ism' in url_l or 'smooth' in ct:
                    m_type = 'Smooth'
                # 优先级 2: 平台特定类型
                elif 'googlevideo.com' in url_l:
                    m_type = 'YouTube'
                elif any(p in url_l for p in ('bilivideo.com', 'bilivideo.cn', 'hdslb.com')):
                    m_type = 'Bilibili'
                elif any(p in url_l for p in ('douyinvod.com', 'douyincdn.com', 'douyinpic.com', 'tos-cn-v')):
                    m_type = 'Douyin'
                elif 'twimg.com' in url_l:
                    m_type = 'Twitter'
                elif 'tiktokcdn.com' in url_l or 'byteoversea' in url_l:
                    m_type = 'TikTok'
                elif 'cdninstagram.com' in url_l or 'fbcdn.net' in url_l:
                    m_type = 'Instagram'
                elif 'phncdn.com' in url_l:
                    m_type = 'Pornhub'
                elif 'vimeocdn.com' in url_l:
                    m_type = 'Vimeo'
                # 优先级 3: 通用格式类型
                elif '.mp4' in url_l:
                    m_type = 'MP4'
                elif '.webm' in url_l:
                    m_type = 'WebM'
                elif '.flv' in url_l:
                    m_type = 'FLV'
                elif '.mkv' in url_l:
                    m_type = 'MKV'
                # 优先级 4: Content-Type 判断
                elif 'video/' in ct:
                    m_type = 'Video'
                elif 'audio/' in ct:
                    m_type = 'Audio'
                else:
                    m_type = 'Media'
                
                # 获取页面标题和URL
                title = t("未知视频")
                page_url = ""
                try:
                    title = await response.frame.page.title()
                    page_url = response.frame.page.url
                except: pass

                size = f"{int(cl)/(1024*1024):.1f}MB" if cl and int(cl) > 1048576 else (f"{int(cl)/1024:.1f}KB" if cl else "")
                normalized_key = normalize_url_key(url)
                is_playing = bool(state.get("current_playing_key")) and state.get("current_playing_key") == normalized_key

                media_info = {
                    'type': m_type,
                    'url': url,
                    'size': size,
                    'content_type': ct,
                    'title': title,
                    'page_url': page_url,
                    'is_playing': is_playing,
                    'is_preload': bool(state.get("current_playing_key")) and not is_playing,
                    'source': 'network',
                    'force_update': bool(is_playing)
                }

                should_emit, _ = should_emit_media(media_info)
                if should_emit:
                    print(f"[MEDIA] {json.dumps(media_info, ensure_ascii=False)}", flush=True)
        except: pass

    page.on('response', on_response)
    context.on('page', lambda p: p.on('response', on_response))
    
    # 处理初始页面导航
    if initial_url:
        # 用户明确指定了URL，直接导航
        try: await page.goto(initial_url, timeout=10000)
        except: pass
    elif page.url == "about:blank":
        # 如果当前是空白页，导航到浏览器的默认主页
        # 根据浏览器类型选择合适的默认主页
        default_pages = {
            "edge": "https://www.bing.com",      # Edge 默认使用 Bing
            "chrome": "chrome://newtab",          # Chrome 新标签页
            "chromium": "https://www.bing.com"    # Chromium 使用 Bing
        }
        default_page = default_pages.get(browser_type, "about:blank")
        if default_page != "about:blank":
            try: await page.goto(default_page, timeout=10000)
            except: pass
    # 如果页面已有内容（持久化上下文恢复的会话），则不做任何导航
    
    print("[BROWSER] READY", flush=True)
    threading.Thread(target=stdin_reader, daemon=True).start()
    
    while True:
        try:
            while not cmd_queue.empty():
                cmd = cmd_queue.get_nowait()
                try:
                    await handle_command(cmd, page, context, cookies_dir)
                except Exception as cmd_err:
                    print(f"[BROWSER] WARN: 命令执行失败({cmd}): {cmd_err}", flush=True)
            if not context.pages:
                await asyncio.sleep(0.2)
                continue
            await asyncio.sleep(0.1)
        except Exception as loop_err:
            loop_text = str(loop_err).lower()
            if "has been closed" in loop_text or "context closed" in loop_text or "browser has been closed" in loop_text:
                break
            print(f"[BROWSER] WARN: 主循环异常: {loop_err}", flush=True)
            await asyncio.sleep(0.2)
            continue
    
    await context.close()
    await p.stop()
    print("[BROWSER] CLOSED", flush=True)

if __name__ == "__main__":
    asyncio.run(run())
'''



# ============ 浏览器控制器类 ============

class PlaywrightBrowserController(QMainWindow):
    """Playwright浏览器控制面板 - 使用子进程方式"""
    
    browser_output = pyqtSignal(str)
    media_url_selected = pyqtSignal(str, str)  # 发送选中的媒体 URL 和标题到主程序
    media_download_as_requested = pyqtSignal(str, str)  # 「下载为…」：URL + 标题
    
    def __init__(self, parent=None, initial_url=""):
        super().__init__(parent)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setWindowTitle(t("浏览器"))
        self.setMinimumSize(500, 450)  # 降低最小尺寸限制，配合自适应滚动条更加灵活
        self.resize(850, 650)
        
        # 保存初始URL
        self._initial_url = initial_url
        
        # 检测可用浏览器

        self._available_browsers = detect_installed_browsers()
        self._browser_settings = self._load_browser_settings()
        self._selected_browser = self._choose_browser_id(str(self._browser_settings.get("browser", "") or ""))
        if self._selected_browser != str(self._browser_settings.get("browser", "") or ""):
            self._persist_browser_option("browser", self._selected_browser)
        self._prefs_expanded = False
        
        # 浏览器进程
        self._browser_process = None
        self._browser_running = False
        self._output_thread = None
        self._browser_closing = False
        self._startup_ready = False
        self._startup_page_opened = False
        self._browser_start_time = 0.0
        self._fallback_attempted = False
        self._last_launch_url = ""
        self._handling_process_exit = False
        self._last_media_parse_error_at = 0.0
        self._panel_transitioning = False
        self._panel_normal_geometry = QRect(self.geometry())
        self._panel_dialog_guard_count = 0
        # 默认启用气泡模式：浏览器启动后自动收回控制面板到气泡
        self._auto_hide_when_running = True
        # 点击到浏览器面板外部时，自动收回为气泡
        self._enable_auto_minimize_on_focus_loss = True
        
        # 悬浮气泡
        self._bubble = FloatingBubble()
        self._bubble.clicked.connect(self._toggle_panel_visibility)
        self._bubble.position_changed.connect(self._on_bubble_position_changed)
        
        # 媒体捕获窗口
        self._media_window = MediaCaptureWindow(controller=self)
        self._media_window.send_to_downloader.connect(self._on_media_selected)
        self._media_window.stop_requested.connect(self._on_media_stop_requested)
        self._media_window.download_as_requested.connect(self.media_download_as_requested.emit)
        self._media_window.closed.connect(self._on_media_window_closed)
        
        # 信号连接
        self.browser_output.connect(self._handle_browser_output)
        
        # 进程看门狗：即使子进程输出异常，也能恢复 UI 状态
        self._process_watchdog = QTimer(self)
        self._process_watchdog.setInterval(500)
        self._process_watchdog.timeout.connect(self._watch_browser_process)
        
        # 初始化UI
        self._init_ui()
        self._apply_styles()
        self._setup_panel_animations()
        self._apply_runtime_preferences()
        
        # 如果有初始URL，自动启动
        if self._initial_url:
            self.url_edit.setText(self._initial_url)
            QTimer.singleShot(100, lambda: self._start_browser(self._initial_url))

    def _show_non_blocking_message(self, icon, title, text):
        """显示不阻塞媒体捕获浮窗操作的提示框。"""
        msg_box = QMessageBox(self)
        msg_box.setIcon(icon)
        msg_box.setWindowTitle(title)
        msg_box.setText(text)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.setWindowModality(Qt.WindowModal)
        msg_box.setAttribute(Qt.WA_DeleteOnClose, True)
        msg_box.open()
        try:
            self._media_window._enforce_topmost()
        except Exception:
            pass
        return msg_box

    def _is_panel_auto_minimize_blocked(self) -> bool:
        return bool(getattr(self, "_panel_dialog_guard_count", 0) > 0)

    def _begin_panel_dialog_guard(self):
        self._panel_dialog_guard_count = int(getattr(self, "_panel_dialog_guard_count", 0)) + 1

    def _end_panel_dialog_guard(self):
        self._panel_dialog_guard_count = max(0, int(getattr(self, "_panel_dialog_guard_count", 0)) - 1)

    def _run_modal_panel_dialog(self, dialog: QDialog) -> int:
        self._begin_panel_dialog_guard()
        try:
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()
            dialog.raise_()
            dialog.activateWindow()
            return dialog.exec_()
        finally:
            self._end_panel_dialog_guard()

    def _set_color_button_visual(self, button: QPushButton, color_text: str):
        color = QColor(str(color_text or "").strip())
        if not color.isValid():
            color = QColor("#d9dee8")
        button.setText("")
        button.setProperty("selectedColor", color.name().upper())
        button.setStyleSheet(f"""
            QPushButton {{
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                padding: 0;
                border-radius: 14px;
                border: 2px solid rgba(255, 255, 255, 0.92);
                background: {color.name()};
            }}
            QPushButton:hover {{
                border: 2px solid rgba(63, 91, 125, 0.55);
            }}
        """)

    def _choose_quick_site_color(self, button: QPushButton, initial_color: str):
        self._begin_panel_dialog_guard()
        try:
            color = QColorDialog.getColor(QColor(str(initial_color or "").strip()), self, t("选择按钮颜色"))
        finally:
            self._end_panel_dialog_guard()
        if color.isValid():
            self._set_color_button_visual(button, color.name())

    def _get_color_button_value(self, button: QPushButton, fallback: str) -> str:
        color_value = str(button.property("selectedColor") or "").strip()
        return color_value or str(fallback or "").strip()

    def _pick_quick_site_color(self, title: str, initial_color: str) -> str:
        self._begin_panel_dialog_guard()
        try:
            color = QColorDialog.getColor(QColor(str(initial_color or "").strip()), self, title)
        finally:
            self._end_panel_dialog_guard()
        if color.isValid():
            return color.name().upper()
        return ""

    def _restore_panel_visibility(self):
        """强制恢复控制面板可见，避免用户误判“程序消失”"""
        if self.isVisible() and not self.isMinimized() and self.windowOpacity() >= 0.99 and not getattr(self, "_panel_transitioning", False):
            try:
                self._bubble.hideBubble()
            except Exception:
                pass
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()
            return

        try:
            self._bubble.hideBubble()
        except Exception:
            pass
        try:
            self._animate_panel_show_from_bubble()
        except Exception:
            pass

    def _setup_panel_animations(self):
        self._panel_pos_anim = QPropertyAnimation(self, b"pos", self)
        self._panel_pos_anim.setDuration(260)
        self._panel_pos_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._panel_opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._panel_opacity_anim.setDuration(220)
        self._panel_opacity_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._panel_show_group = QParallelAnimationGroup(self)
        self._panel_show_group.addAnimation(self._panel_pos_anim)
        self._panel_show_group.addAnimation(self._panel_opacity_anim)
        self._panel_show_group.finished.connect(self._on_panel_show_animation_finished)

        self._panel_hide_pos_anim = QPropertyAnimation(self, b"pos", self)
        self._panel_hide_pos_anim.setDuration(220)
        self._panel_hide_pos_anim.setEasingCurve(QEasingCurve.InOutCubic)

        self._panel_hide_opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._panel_hide_opacity_anim.setDuration(180)
        self._panel_hide_opacity_anim.setEasingCurve(QEasingCurve.InCubic)

        self._panel_hide_group = QParallelAnimationGroup(self)
        self._panel_hide_group.addAnimation(self._panel_hide_pos_anim)
        self._panel_hide_group.addAnimation(self._panel_hide_opacity_anim)
        self._panel_hide_group.finished.connect(self._on_panel_hide_animation_finished)

    def _remember_panel_geometry(self):
        if self.isMinimized():
            try:
                normal_geo = self.normalGeometry()
                if normal_geo.isValid():
                    self._panel_normal_geometry = QRect(normal_geo)
                    return
            except Exception:
                pass
        if self.isVisible() and not self.isMinimized():
            self._panel_normal_geometry = QRect(self.geometry())

    def _bubble_anchor_point(self):
        bubble_geo = self._bubble.frameGeometry()
        return bubble_geo.center()

    def _compute_panel_hidden_pos(self):
        anchor = self._bubble_anchor_point()
        target = self._panel_normal_geometry if self._panel_normal_geometry.isValid() else self.geometry()
        return QPoint(
            int(anchor.x() - target.width() * 0.5),
            int(anchor.y() - target.height() * 0.42),
        )

    def _animate_panel_show_from_bubble(self):
        target_geo = QRect(self._panel_normal_geometry) if self._panel_normal_geometry.isValid() else QRect(self.geometry())
        self._panel_transitioning = False
        self._panel_hide_group.stop()
        self._panel_show_group.stop()
        self._bubble.hideBubble()
        self.setWindowOpacity(1.0)
        self.setGeometry(target_geo)
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _animate_panel_hide_to_bubble(self):
        if self._panel_transitioning or not self.isVisible():
            return
        self._remember_panel_geometry()
        self._panel_show_group.stop()
        self._panel_hide_group.stop()
        self._panel_transitioning = False
        self.setWindowOpacity(1.0)
        self._bubble.showBubble()
        self.hide()
        if self._panel_normal_geometry.isValid():
            self.move(self._panel_normal_geometry.topLeft())

    def _on_panel_show_animation_finished(self):
        self.move(self._panel_normal_geometry.topLeft())
        self.setWindowOpacity(1.0)
        self.raise_()
        self.activateWindow()
        self._panel_transitioning = False

    def _on_panel_hide_animation_finished(self):
        self.hide()
        self.move(self._panel_normal_geometry.topLeft())
        self.setWindowOpacity(1.0)
        self._panel_transitioning = False

    def _watch_browser_process(self):
        """轮询子进程状态，兜底处理无日志退出场景"""
        proc = self._browser_process
        if not proc:
            self._process_watchdog.stop()
            return
        try:
            code = proc.poll()
            if code is None:
                return
            # 防止与输出线程的 CLOSED 重复处理
            if self._handling_process_exit:
                return
            self._handling_process_exit = True
            if self._browser_running:
                self.browser_output.emit("[BROWSER] CLOSED")
            else:
                self.browser_output.emit(f"[BROWSER] ERROR: 子进程已退出（退出码: {code}）")
                self.browser_output.emit("[BROWSER] CLOSED")
        except Exception:
            pass
        finally:
            self._process_watchdog.stop()
    
    def _default_browser_settings(self) -> dict:
        return {
            "browser": "",
            "auto_hide_when_running": True,
            "auto_minimize_on_focus_loss": True,
            "bubble_animations_enabled": True,
            "bubble_lock_position": False,
            "bubble_position": None,
            "quick_sites": [dict(item) for item in DEFAULT_QUICK_SITES],
        }

    def _normalize_quick_sites(self, raw_sites) -> list:
        normalized = []
        fallback_sites = [dict(item) for item in DEFAULT_QUICK_SITES]
        source = raw_sites if isinstance(raw_sites, list) else fallback_sites

        for index, item in enumerate(source):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            url = str(item.get("url", "") or "").strip()
            bg_color = str(item.get("bg_color", "") or "").strip()
            hover_color = str(item.get("hover_color", "") or "").strip()
            press_color = str(item.get("press_color", "") or "").strip()

            default_item = fallback_sites[index % len(fallback_sites)]
            if not name and url:
                name = url
            if not name or not url:
                continue

            normalized.append({
                "name": name[:24],
                "url": url[:512],
                "bg_color": bg_color or default_item["bg_color"],
                "hover_color": hover_color or default_item["hover_color"],
                "press_color": press_color or default_item["press_color"],
            })

        return normalized or fallback_sites

    def _load_browser_settings(self) -> dict:
        settings = self._default_browser_settings()
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    settings.update(raw)
        except Exception:
            pass
        settings["quick_sites"] = self._normalize_quick_sites(settings.get("quick_sites"))
        return settings

    def _save_browser_settings(self):
        try:
            os.makedirs(BROWSER_DATA_DIR, exist_ok=True)
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._browser_settings, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _get_browser_info(self, browser_id: str = "") -> Optional[Dict[str, Any]]:
        target = str(browser_id or "").strip().lower()
        if not target:
            return self._available_browsers[0] if self._available_browsers else None

        for browser in self._available_browsers:
            if str(browser.get("id", "")).strip().lower() == target:
                return browser
        return None

    def _choose_browser_id(self, preferred_browser_id: str = "") -> str:
        preferred = str(preferred_browser_id or "").strip().lower()
        if preferred and self._get_browser_info(preferred):
            return preferred

        for browser in self._available_browsers:
            if browser.get("is_default"):
                return browser["id"]

        return self._available_browsers[0]["id"] if self._available_browsers else ""

    def _pick_alternate_browser_id(self, current_browser_id: str = "") -> str:
        current = str(current_browser_id or "").strip().lower()
        for browser in self._available_browsers:
            if str(browser.get("id", "")).strip().lower() != current:
                return browser["id"]
        return ""

    def _apply_runtime_preferences(self):
        self._auto_hide_when_running = bool(self._browser_settings.get("auto_hide_when_running", True))
        self._enable_auto_minimize_on_focus_loss = bool(self._browser_settings.get("auto_minimize_on_focus_loss", True))

        animations_enabled = bool(self._browser_settings.get("bubble_animations_enabled", True))
        self._bubble.set_animations_enabled(animations_enabled)

        lock_position = bool(self._browser_settings.get("bubble_lock_position", False))
        self._bubble.set_lock_position(lock_position)

        bubble_pos = self._browser_settings.get("bubble_position")
        if isinstance(bubble_pos, dict) and "x" in bubble_pos and "y" in bubble_pos:
            self._bubble.apply_saved_position(bubble_pos.get("x", 0), bubble_pos.get("y", 0))

    def _persist_browser_option(self, key: str, value):
        self._browser_settings[key] = value
        self._save_browser_settings()
    
    def _init_ui(self):
        """初始化UI"""
        # 使用自适应的 QScrollArea 作为中央控件，完美解决窗口过小时的界面错位与挤压问题
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setObjectName("mainScroll")
        self.setCentralWidget(scroll)
        
        scroll_content = QWidget()
        scroll_content.setObjectName("scrollContent")
        scroll.setWidget(scroll_content)
        
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        
        # === 顶部：状态行 ===
        status_row = QHBoxLayout()

        self.status_label = QLabel(t("● 就绪"))
        status_row.addWidget(self.status_label)

        self.btn_toggle_prefs = QPushButton(t("气泡设置"))
        self.btn_toggle_prefs.setObjectName("prefsToggleBtn")
        self.btn_toggle_prefs.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_prefs.clicked.connect(self._toggle_preferences_panel)
        status_row.addWidget(self.btn_toggle_prefs)

        status_row.addStretch()
        
        # 媒体捕获开关按钮
        self.btn_media_capture = QPushButton(t("📡 媒体捕获: 关"))
        self.btn_media_capture.setCheckable(True)
        self.btn_media_capture.setObjectName("mediaCaptureBtn")
        self.btn_media_capture.setCursor(Qt.PointingHandCursor)
        self.btn_media_capture.clicked.connect(self._toggle_media_capture)
        self.btn_media_capture.setEnabled(False)  # 启动后才可用
        status_row.addWidget(self.btn_media_capture)
        
        layout.addLayout(status_row)
        
        # === 浏览器选择器(极简卡片式) ===
        self.browser_selector = BrowserSelector(
            self._available_browsers, 
            self._selected_browser, 
            self
        )
        self.browser_selector.browser_changed.connect(self._on_browser_changed)
        layout.addWidget(self.browser_selector)
        
        # === 地址栏 ===
        url_frame = QFrame()
        url_frame.setObjectName("urlFrame")
        url_layout = QHBoxLayout(url_frame)
        url_layout.setContentsMargins(12, 8, 12, 8)
        
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(t("输入网址..."))
        self.url_edit.returnPressed.connect(self._navigate)
        url_layout.addWidget(self.url_edit, 1)

        self._apply_soft_shadow(url_frame, blur=26, y_offset=8)
        
        layout.addWidget(url_frame)

        # === 操作按钮 ===
        action_frame = QFrame()
        action_frame.setObjectName("actionFrame")
        action_layout = QVBoxLayout(action_frame)
        action_layout.setContentsMargins(20, 24, 20, 24)
        action_layout.setSpacing(16)
        
        self.btn_launch = QPushButton(t("启动浏览器"))
        self.btn_launch.setObjectName("primaryBtn")
        self.btn_launch.setCursor(Qt.PointingHandCursor)
        self.btn_launch.clicked.connect(self._toggle_browser)
        action_layout.addWidget(self.btn_launch)
        
        self.btn_extract = QPushButton(t("提取 Cookie"))
        self.btn_extract.setObjectName("secondaryBtn")
        self.btn_extract.setCursor(Qt.PointingHandCursor)
        self.btn_extract.clicked.connect(self._extract_cookies)
        self.btn_extract.setEnabled(False)
        action_layout.addWidget(self.btn_extract)
        
        # Cookie 操作按钮行
        cookie_row = QHBoxLayout()
        cookie_row.setSpacing(10)
        
        self.btn_clear_cookies = QPushButton(t("✕  清除 Cookie"))
        self.btn_clear_cookies.setObjectName("dangerBtn")
        self.btn_clear_cookies.setCursor(Qt.PointingHandCursor)
        self.btn_clear_cookies.clicked.connect(self._clear_cookies)
        self.btn_clear_cookies.setEnabled(False)
        cookie_row.addWidget(self.btn_clear_cookies)
        
        action_layout.addLayout(cookie_row)
        self._apply_soft_shadow(action_frame, blur=34, y_offset=12)
        
        layout.addWidget(action_frame)
        
        # === 快捷入口 - 极简圆润按压风格 (加入水平滚动区域，防止超宽) ===
        quick_wrap = QFrame()
        quick_wrap.setObjectName("quickWrap")
        quick_wrap_layout = QHBoxLayout(quick_wrap)
        quick_wrap_layout.setContentsMargins(0, 0, 0, 0)
        quick_wrap_layout.setSpacing(0)
        
        quick_scroll = QScrollArea(quick_wrap)
        quick_scroll.setWidgetResizable(True)
        quick_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        quick_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        quick_scroll.setObjectName("quickScroll")
        quick_scroll.setFixedHeight(48)
        
        quick_content = QWidget()
        quick_content.setObjectName("quickContent")
        quick_scroll.setWidget(quick_content)
        
        quick_layout = QHBoxLayout(quick_content)
        quick_layout.setContentsMargins(0, 4, 0, 4)
        quick_layout.setSpacing(8)
        self.quick_sites_layout = quick_layout
        
        self.btn_edit_quick_sites = QPushButton(t("编辑快捷网址"))
        self.btn_edit_quick_sites.setObjectName("prefsToggleBtn")
        self.btn_edit_quick_sites.setCursor(Qt.PointingHandCursor)
        self.btn_edit_quick_sites.clicked.connect(self._edit_quick_sites)
        
        quick_wrap_layout.addWidget(quick_scroll)
        self._refresh_quick_sites_ui()
        layout.addWidget(quick_wrap)
        
        # === 状态信息 ===
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #666; font-size: 12px;")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)
        
        # === 功能使用指南 ===
        guide_card = QFrame()
        guide_card.setObjectName("guideCard")
        guide_layout = QVBoxLayout(guide_card)
        guide_layout.setContentsMargins(16, 16, 16, 16)
        guide_layout.setSpacing(8)
        
        guide_title = QLabel(t("💡 网页视频嗅探与登录助手"))
        guide_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #1c5293;")
        guide_layout.addWidget(guide_title)
        
        guide_text = QLabel(
            t("1. <b>右上角视频捕获：</b>启动内置浏览器后，播放网页视频时将自动捕获下载地址。<br>") + t("2. <b>同步账号权限：</b>在浏览器中登录您的视频网站账号（如B站、YouTube等），一键提取 Cookie 后，重新复制链接，即可下载您账号权限范围内的视频。")
        )
        guide_text.setStyleSheet("font-size: 12px; color: #4e5e78; line-height: 18px;")
        guide_text.setWordWrap(True)
        guide_layout.addWidget(guide_text)
        
        layout.addWidget(guide_card)
        
        layout.addStretch()
        self._apply_status_visual("idle")

    def _apply_soft_shadow(self, widget, blur=28, y_offset=10):
        try:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(blur)
            shadow.setOffset(0, y_offset)
            shadow.setColor(QColor(115, 136, 175, 38))
            widget.setGraphicsEffect(shadow)
        except Exception:
            pass
    
    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #fbfbfd, stop:0.52 #f7f8fc, stop:1 #eef3fb);
            }
             
            #urlFrame {
                background: rgba(255, 255, 255, 0.82);
                border: 1px solid rgba(193, 206, 231, 0.72);
                border-radius: 16px;
            }
            #prefsToggleBtn {
                text-align: left;
                padding: 10px 14px;
                border-radius: 12px;
                border: 1px solid rgba(188, 200, 219, 0.85);
                background: rgba(255, 255, 255, 0.84);
                color: #56657c;
                font-size: 12px;
                font-weight: 600;
            }
            #prefsToggleBtn:hover {
                background: rgba(255, 255, 255, 0.96);
            }
             
            QLineEdit {
                border: none;
                background: transparent;
                padding: 10px;
                font-size: 14px;
                color: #415067;
            }
             
            #actionFrame {
                background: rgba(255, 255, 255, 0.74);
                border: 1px solid rgba(188, 203, 232, 0.75);
                border-radius: 22px;
            }
            
            #primaryBtn {
                padding: 14px;
                border: none;
                border-radius: 14px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #ffb6b6, stop:0.25 #ffd4a8, stop:0.5 #ffe5a0, 
                    stop:0.75 #b8e6cf, stop:1 #a8d8ea);
                color: #5a5a5a;
                font-size: 15px;
                font-weight: 600;
            }
            #primaryBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #ffa0a0, stop:0.25 #ffc896, stop:0.5 #ffdc88, 
                    stop:0.75 #a0deb8, stop:1 #90c8da);
            }
            #primaryBtn:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                    stop:0 #ff9090, stop:0.25 #ffbc84, stop:0.5 #ffd270, 
                    stop:0.75 #88d6a0, stop:1 #78b8ca);
            }
            
            #secondaryBtn {
                padding: 12px;
                border: 1px solid rgba(198, 208, 224, 0.9);
                border-radius: 12px;
                background: rgba(255, 255, 255, 0.78);
                color: #53627a;
                font-size: 14px;
            }
            #secondaryBtn:hover { background: rgba(255, 255, 255, 0.94); }
            #secondaryBtn:disabled { color: #bbb; }
            
            #quickBtn {
                padding: 8px 20px;
                border: none;
                border-radius: 20px;
                background: #f0f0f0;
                color: #555;
            }
            #quickBtn:hover { background: #e8e8e8; }
            
            #dangerBtn {
                padding: 10px 16px;
                border: 1px solid rgba(220, 80, 80, 0.3);
                border-radius: 12px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,245,245,0.92), stop:1 rgba(255,235,235,0.82));
                color: #c55;
                font-size: 13px;
            }
            #dangerBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffeded, stop:1 #ffe0e0);
                border-color: rgba(220, 80, 80, 0.5);
            }
            #dangerBtn:pressed {
                background: #ffe0e0;
            }
            #dangerBtn:disabled { 
                color: #ccc; 
                border-color: #eee;
                background: #fafafa;
            }
            
            #mediaCaptureBtn {
                padding: 4px 12px;
                border: 1px solid rgba(188, 200, 219, 0.9);
                border-radius: 12px;
                background: rgba(255, 255, 255, 0.86);
                color: #66758d;
                font-size: 12px;
            }
            #mediaCaptureBtn:hover {
                background: rgba(255, 255, 255, 0.98);
            }
            #mediaCaptureBtn:checked {
                background: rgba(225, 248, 255, 0.98);
                border-color: #65bde7;
                color: #186a96;
                font-weight: 600;
            }
            #mediaCaptureBtn:disabled {
                color: #ccc;
                background: #f9f9f9;
            }
            #guideCard {
                background: rgba(232, 244, 253, 0.7);
                border: 1px solid rgba(176, 213, 250, 0.9);
                border-radius: 12px;
            }
            
            /* 自适应滚动容器样式 */
            #mainScroll {
                border: none;
                background: transparent;
            }
            #scrollContent {
                background: transparent;
            }
            #quickScroll {
                border: none;
                background: transparent;
            }
            #quickContent {
                background: transparent;
            }
            
            /* 现代优雅超细滚动条 */
            QScrollBar:vertical {
                border: none;
                background: transparent;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(180, 195, 220, 0.45);
                min-height: 30px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(140, 160, 195, 0.65);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
            
            QScrollBar:horizontal {
                border: none;
                background: transparent;
                height: 6px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(180, 195, 220, 0.45);
                min-width: 30px;
                border-radius: 3px;
            }
            QScrollBar::handle:horizontal:hover {
                background: rgba(140, 160, 195, 0.65);
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: transparent;
            }
        """)
    
    # ============ 浏览器控制 ============
    
    def _on_browser_changed(self, browser_id: str):
        """处理浏览器选择变更"""
        self._selected_browser = browser_id
        self._persist_browser_option("browser", browser_id)

    def _toggle_preferences_panel(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(t("气泡与回收设置"))
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setModal(True)
        dialog.resize(360, 240)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel(t("气泡与回收设置"))
        title.setStyleSheet("color: #55657d; font-size: 13px; font-weight: 600;")
        layout.addWidget(title)

        chk_auto_hide = QCheckBox(t("浏览器运行后自动收回为气泡"))
        chk_auto_hide.setChecked(bool(self._browser_settings.get("auto_hide_when_running", True)))
        layout.addWidget(chk_auto_hide)

        chk_focus_minimize = QCheckBox(t("面板失去焦点时自动收回"))
        chk_focus_minimize.setChecked(bool(self._browser_settings.get("auto_minimize_on_focus_loss", True)))
        layout.addWidget(chk_focus_minimize)

        chk_bubble_animation = QCheckBox(t("启用气泡动画"))
        chk_bubble_animation.setChecked(bool(self._browser_settings.get("bubble_animations_enabled", True)))
        layout.addWidget(chk_bubble_animation)

        chk_bubble_lock = QCheckBox(t("记住气泡位置并保持原位"))
        chk_bubble_lock.setChecked(bool(self._browser_settings.get("bubble_lock_position", False)))
        layout.addWidget(chk_bubble_lock)

        layout.addStretch()

        button_row = QHBoxLayout()
        button_row.addStretch()

        btn_cancel = QPushButton(t("取消"))
        btn_cancel.clicked.connect(dialog.reject)
        button_row.addWidget(btn_cancel)

        btn_ok = QPushButton(t("确定"))
        btn_ok.setObjectName("primaryBtn")
        btn_ok.clicked.connect(dialog.accept)
        button_row.addWidget(btn_ok)
        layout.addLayout(button_row)

        dialog.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #fbfbfd, stop:1 #eff4fb);
            }
            QCheckBox {
                color: #55657d;
                spacing: 8px;
                font-size: 12px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 5px;
                border: 1px solid rgba(164, 181, 210, 0.95);
                background: rgba(255, 255, 255, 0.96);
            }
            QCheckBox::indicator:checked {
                background: #88c4ff;
                border: 1px solid #5faef0;
            }
            QPushButton {
                padding: 8px 14px;
                border-radius: 10px;
                border: 1px solid rgba(188, 200, 219, 0.9);
                background: rgba(255, 255, 255, 0.88);
                color: #55657d;
            }
            QPushButton#primaryBtn {
                border: none;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ffb6b6, stop:0.25 #ffd4a8, stop:0.5 #ffe5a0,
                    stop:0.75 #b8e6cf, stop:1 #a8d8ea);
                color: #5a5a5a;
                font-weight: 600;
            }
        """)

        if self._run_modal_panel_dialog(dialog) == QDialog.Accepted:
            self._on_auto_hide_toggled(chk_auto_hide.isChecked())
            self._on_focus_minimize_toggled(chk_focus_minimize.isChecked())
            self._on_bubble_animation_toggled(chk_bubble_animation.isChecked())
            self._on_bubble_lock_toggled(chk_bubble_lock.isChecked())

    def _refresh_quick_sites_ui(self):
        layout = getattr(self, "quick_sites_layout", None)
        if layout is None:
            return

        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if widget is self.btn_edit_quick_sites:
                    widget.setParent(None)
                else:
                    widget.deleteLater()

        quick_sites = self._normalize_quick_sites(self._browser_settings.get("quick_sites"))
        self._browser_settings["quick_sites"] = quick_sites

        for index, site in enumerate(quick_sites):
            btn = QPushButton(site["name"])
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    padding: 10px 18px;
                    border: none;
                    border-radius: 20px;
                    background: {site["bg_color"]};
                    color: white;
                    font-size: 12px;
                    font-weight: 500;
                    letter-spacing: 0.3px;
                }}
                QPushButton:hover {{
                    background: {site["hover_color"]};
                    padding-top: 9px;
                    padding-bottom: 11px;
                }}
                QPushButton:pressed {{
                    background: {site["press_color"]};
                    padding-top: 12px;
                    padding-bottom: 8px;
                }}
            """)
            btn.clicked.connect(lambda _, u=site["url"]: self._quick_goto(u))
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, idx=index, button=btn: self._show_quick_site_context_menu(idx, button, pos)
            )
            layout.addWidget(btn)

        layout.addStretch()
        layout.addWidget(self.btn_edit_quick_sites)

    def _show_quick_site_context_menu(self, index: int, button: QPushButton, pos):
        quick_sites = self._normalize_quick_sites(self._browser_settings.get("quick_sites"))
        if index < 0 or index >= len(quick_sites):
            return

        menu = QMenu(self)
        act_color = menu.addAction(t("设置按钮颜色"))
        act_delete = menu.addAction(t("删除此按钮"))

        selected = menu.exec_(button.mapToGlobal(pos))
        if selected is None:
            return

        site = dict(quick_sites[index])

        if selected is act_color:
            color = self._pick_quick_site_color("选择按钮颜色", site.get("bg_color", "#d9dee8"))
            if not color:
                return
            site["bg_color"] = color
            site["hover_color"] = color
            site["press_color"] = color
            quick_sites[index] = site
        elif selected is act_delete:
            del quick_sites[index]
        else:
            return

        self._browser_settings["quick_sites"] = self._normalize_quick_sites(quick_sites)
        self._save_browser_settings()
        self._refresh_quick_sites_ui()

    def _edit_quick_sites(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(t("编辑快捷网址"))
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setModal(True)
        dialog.resize(700, 460)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel(t("自定义底部快速网址导航"))
        title.setStyleSheet("color: #55657d; font-size: 13px; font-weight: 600;")
        layout.addWidget(title)

        tip = QLabel(t("这里只编辑名称和网址。颜色和删除请在主界面快捷按钮上右键设置。"))
        tip.setStyleSheet("color: #7a8798; font-size: 12px;")
        layout.addWidget(tip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        form_host = QWidget()
        form_layout = QVBoxLayout(form_host)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(10)

        row_inputs = []
        quick_sites = self._normalize_quick_sites(self._browser_settings.get("quick_sites"))
        max_rows = max(8, len(quick_sites) + 2)
        palette = [dict(item) for item in DEFAULT_QUICK_SITES]

        for index in range(max_rows):
            site = quick_sites[index] if index < len(quick_sites) else {}

            row = QFrame()
            row.setObjectName("quickSiteRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(10)

            name_edit = QLineEdit(str(site.get("name", "") or ""))
            name_edit.setPlaceholderText(t("名称"))
            row_layout.addWidget(name_edit, 1)

            url_edit = QLineEdit(str(site.get("url", "") or ""))
            url_edit.setPlaceholderText(t("网址，例如 youtube.com"))
            row_layout.addWidget(url_edit, 2)

            controls = {
                "name": name_edit,
                "url": url_edit,
            }
            row_inputs.append(controls)
            form_layout.addWidget(row)

        form_layout.addStretch()
        scroll.setWidget(form_host)
        layout.addWidget(scroll, 1)

        button_row = QHBoxLayout()
        btn_reset = QPushButton(t("恢复默认"))
        button_row.addWidget(btn_reset)
        button_row.addStretch()

        btn_cancel = QPushButton(t("取消"))
        btn_cancel.clicked.connect(dialog.reject)
        button_row.addWidget(btn_cancel)

        btn_ok = QPushButton(t("保存"))
        btn_ok.setObjectName("primaryBtn")
        btn_ok.clicked.connect(dialog.accept)
        button_row.addWidget(btn_ok)
        layout.addLayout(button_row)

        def reset_defaults():
            for index, controls in enumerate(row_inputs):
                default_site = palette[index] if index < len(palette) else {}
                controls["name"].setText(str(default_site.get("name", "") or ""))
                controls["url"].setText(str(default_site.get("url", "") or ""))

        btn_reset.clicked.connect(reset_defaults)

        dialog.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #fbfbfd, stop:1 #eff4fb);
            }
            QLabel {
                color: #55657d;
            }
            QScrollArea {
                background: transparent;
            }
            QFrame#quickSiteRow {
                background: rgba(255, 255, 255, 0.82);
                border: 1px solid rgba(188, 203, 232, 0.72);
                border-radius: 14px;
            }
            QLineEdit {
                background: rgba(255, 255, 255, 0.96);
                border: 1px solid rgba(188, 200, 219, 0.9);
                border-radius: 10px;
                padding: 8px 10px;
                color: #415067;
            }
            QPushButton {
                padding: 8px 14px;
                border-radius: 10px;
                border: 1px solid rgba(188, 200, 219, 0.9);
                background: rgba(255, 255, 255, 0.88);
                color: #55657d;
            }
            QPushButton#dangerBtn {
                border-color: rgba(220, 80, 80, 0.3);
                background: rgba(255, 241, 241, 0.96);
                color: #bf5d5d;
                font-weight: 600;
            }
            QPushButton#primaryBtn {
                border: none;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ffb6b6, stop:0.25 #ffd4a8, stop:0.5 #ffe5a0,
                    stop:0.75 #b8e6cf, stop:1 #a8d8ea);
                color: #5a5a5a;
                font-weight: 600;
            }
        """)

        if self._run_modal_panel_dialog(dialog) == QDialog.Accepted:
            updated_sites = []
            for index, controls in enumerate(row_inputs):
                name = controls["name"].text().strip()
                url = controls["url"].text().strip()
                if not name or not url:
                    continue
                previous_site = quick_sites[index] if index < len(quick_sites) else {}
                default_site = palette[index % len(palette)]
                updated_sites.append({
                    "name": name,
                    "url": url,
                    "bg_color": str(previous_site.get("bg_color", "") or default_site["bg_color"]),
                    "hover_color": str(previous_site.get("hover_color", "") or default_site["hover_color"]),
                    "press_color": str(previous_site.get("press_color", "") or default_site["press_color"]),
                })

            self._browser_settings["quick_sites"] = self._normalize_quick_sites(updated_sites)
            self._save_browser_settings()
            self._refresh_quick_sites_ui()
            ModernToast.show_message(t("快捷网址已更新"), self)

    def _on_auto_hide_toggled(self, checked: bool):
        self._auto_hide_when_running = bool(checked)
        self._persist_browser_option("auto_hide_when_running", bool(checked))

    def _on_focus_minimize_toggled(self, checked: bool):
        self._enable_auto_minimize_on_focus_loss = bool(checked)
        self._persist_browser_option("auto_minimize_on_focus_loss", bool(checked))

    def _on_bubble_animation_toggled(self, checked: bool):
        enabled = bool(checked)
        self._bubble.set_animations_enabled(enabled)
        self._persist_browser_option("bubble_animations_enabled", enabled)

    def _on_bubble_lock_toggled(self, checked: bool):
        locked = bool(checked)
        self._bubble.set_lock_position(locked)
        self._persist_browser_option("bubble_lock_position", locked)
        if locked:
            self._on_bubble_position_changed(self._bubble.x(), self._bubble.y())

    def _on_bubble_position_changed(self, x: int, y: int):
        self._persist_browser_option("bubble_position", {"x": int(x), "y": int(y)})

    def _apply_status_visual(self, mode: str):
        mode_name = str(mode or "idle").strip().lower()
        styles = {
            "idle": (t("● 就绪"), "#7d8798", "rgba(242, 245, 251, 0.92)"),
            "starting": (t("● 正在启动..."), "#db9b2b", "rgba(255, 246, 225, 0.96)"),
            "running": (t("● 运行中"), "#2e9b72", "rgba(231, 252, 244, 0.96)"),
            "capture": (t("● 捕获中"), "#2490c4", "rgba(231, 247, 255, 0.97)"),
            "error": (t("● 启动失败"), "#d9534f", "rgba(255, 235, 235, 0.95)"),
        }
        text, color, bg = styles.get(mode_name, styles["idle"])
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"""
            color: {color};
            font-size: 12px;
            padding: 4px 12px;
            background: {bg};
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.7);
        """)
        try:
            self._bubble.set_visual_mode(mode_name)
        except Exception:
            pass
    
    def _toggle_panel_visibility(self):
        """切换面板显示/隐藏(点击气泡时调用)"""
        if self.isVisible() and not self.isMinimized():
            self._animate_panel_hide_to_bubble()
        else:
            self._animate_panel_show_from_bubble()
    
    def _hide_to_bubble_if_enabled(self):
        """仅在启用时自动收回到气泡。默认关闭以避免误判“程序消失”"""
        if getattr(self, "_auto_hide_when_running", False):
            self._animate_panel_hide_to_bubble()

    def changeEvent(self, event):
        """处理窗口状态变化事件，拦截最小化操作"""
        from PyQt5.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange:
            # 仅在显式开启时才把最小化转为气泡，避免“窗口突然消失”
            if self.isMinimized() and getattr(self, "_auto_hide_when_running", False):
                QTimer.singleShot(0, self._minimize_to_bubble)
        elif event.type() == QEvent.ActivationChange:
            # 失去焦点（点到其他窗口）后自动收回到气泡
            if (
                getattr(self, "_enable_auto_minimize_on_focus_loss", False)
                and not self._is_panel_auto_minimize_blocked()
                and not self.isActiveWindow()
                and self.isVisible()
                and not self.isMinimized()
            ):
                QTimer.singleShot(100, self._auto_minimize_on_focus_loss)
        super().changeEvent(event)
    
    def _minimize_to_bubble(self):
        """最小化到气泡"""
        self.showNormal()  # 先恢复正常状态
        self._animate_panel_hide_to_bubble()

    def _auto_minimize_on_focus_loss(self):
        """失去焦点后自动收回为气泡"""
        if (
            getattr(self, "_enable_auto_minimize_on_focus_loss", False)
            and not self._is_panel_auto_minimize_blocked()
            and not self.isActiveWindow()
            and self.isVisible()
            and not self.isMinimized()
        ):
            self._animate_panel_hide_to_bubble()

    def _toggle_browser(self):
        if self._browser_running:
            self._stop_browser()
        else:
            url = self.url_edit.text().strip()
            self._start_browser(url)
    
    def _start_browser(self, initial_url="", auto_fallback=False):
        """启动浏览器子进程"""
        self._selected_browser = self._choose_browser_id(self._selected_browser)
        if not self._selected_browser:
            self._show_non_blocking_message(QMessageBox.Warning, t("未检测到浏览器"), t("未检测到可用浏览器，请先安装并设置一个系统浏览器。"))
            return

        self._browser_closing = False
        self._handling_process_exit = False
        self._browser_running = False
        self._startup_ready = False
        self._startup_page_opened = False
        self._browser_start_time = time.time()
        self._last_launch_url = initial_url or self.url_edit.text().strip()
        self._awaiting_codec_consent = False
        self._suppress_codec_stay_toast = False
        if not auto_fallback:
            self._fallback_attempted = False
        
        self._apply_status_visual("starting")
        self.btn_launch.setEnabled(False)
        
        try:
            # 写入轻量启动脚本（统一复用 run_launcher，避免双实现漂移）
            script_path = os.path.join(BROWSER_DATA_DIR, "_launcher.py")
            os.makedirs(BROWSER_DATA_DIR, exist_ok=True)
            launcher_script = _build_launcher_bootstrap_script(os.path.abspath(__file__))
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(launcher_script)
            
            # 使用可写目录保存 Cookie，避免安装目录无写权限导致功能失效
            cookies_dir = getattr(auth_utils, "COOKIE_DIR", os.path.join(BROWSER_DATA_DIR, "cookies"))
            os.makedirs(cookies_dir, exist_ok=True)
            
            # 启动子进程 - 使用有 playwright 的 Python
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8:replace"
            
            python_exe = get_python_with_playwright()
            if getattr(sys, 'frozen', False):
                # 打包环境下也会执行 script_path（由 video_downloader.py 分发到 runpy.run_path）
                cmd = [python_exe, "--playwright-launcher", script_path, self._selected_browser, cookies_dir]
            else:
                cmd = [python_exe, script_path, self._selected_browser, cookies_dir]
                
            if initial_url:
                cmd.append(initial_url)
            
            self._browser_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace',
                env=env
            )
            
            # 立即将状态设为"正在启动"
            self.btn_launch.setEnabled(False)
            self._apply_status_visual("starting")
            
            # 启动输出读取线程
            self._output_thread = threading.Thread(target=self._read_output, daemon=True)
            self._output_thread.start()
            self._process_watchdog.start()
            
            # 标记已启动，避免延迟检查误判
            self._browser_started = True
            
            # 2秒后检查进程状态（如果 READY 信号还没到）
            def delayed_check():
                try:
                    if getattr(self, "_awaiting_codec_consent", False):
                        QTimer.singleShot(800, delayed_check)
                        return
                    if self._browser_process and self._browser_process.poll() is None:
                        # 进程仍在运行，浏览器已启动
                        if not hasattr(self, '_browser_running') or not self._browser_running:
                            # READY 信号可能延迟或丢失，直接更新状态
                            self._browser_running = True
                            self.btn_launch.setText(t("关闭浏览器"))
                            self.btn_launch.setEnabled(True)
                            self.btn_extract.setEnabled(True)
                            self.btn_clear_cookies.setEnabled(True)
                            self.btn_media_capture.setEnabled(True)
                            self._apply_status_visual("running")
                            self.browser_selector.setEnabled(False)
                            self._hide_to_bubble_if_enabled()
                    else:
                        if getattr(self, '_startup_ready', False):
                            return
                        # 进程已退出，检查是否是用户主动关闭
                        if hasattr(self, '_browser_closing') and self._browser_closing:
                            # 用户主动关闭，正常处理
                            pass
                        else:
                            # 异常退出
                            self.btn_launch.setEnabled(True)
                            self._apply_status_visual("error")
                            self._restore_panel_visibility()
                except Exception as e:
                    print(f"检查浏览器状态时出错: {e}")
                    self.btn_launch.setEnabled(True)
            
            QTimer.singleShot(2000, delayed_check)
            
        except Exception as e:
            self._apply_status_visual("error")
            self.btn_launch.setEnabled(True)
            self._show_non_blocking_message(QMessageBox.Warning, t("启动失败"), str(e))
    
    def _read_output(self):
        """读取浏览器输出"""
        proc = self._browser_process
        try:
            if not proc or not proc.stdout:
                return
            while True:
                try:
                    line = proc.stdout.readline()
                except UnicodeDecodeError as decode_err:
                    self.browser_output.emit(f"[DEBUG] 子进程输出解码失败，已跳过: {decode_err}")
                    continue
                
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue
                
                self.browser_output.emit(line.strip())
        except Exception as e:
            self.browser_output.emit(f"[DEBUG] 读取子进程输出异常: {e}")
        finally:
            try:
                if proc and proc.poll() is not None:
                    self.browser_output.emit("[BROWSER] CLOSED")
            except:
                pass
    
    def _handle_browser_output(self, line):
        """处理浏览器输出"""
        try:
            print(line)  # 调试输出
        except Exception:
            pass
        
        # 标记收到过浏览器日志
        if not hasattr(self, '_browser_log_received'):
            self._browser_log_received = False
        if "[BROWSER]" in line:
            self._browser_log_received = True
        
        if line.startswith("[BROWSER] CODEC_MISSING|"):
            # 用户已经明确选择了内置 Chromium，这里不再弹阻塞确认框打断流程。
            # 直接继续使用当前浏览器，并用轻提示告知可手动切换到系统浏览器提升媒体兼容性。
            self._awaiting_codec_consent = True
            suggest = ""
            try:
                parts = line.split("|")
                for part in parts:
                    if part.startswith("suggest="):
                        suggest = part.split("=", 1)[1].strip()
                        break
            except Exception:
                suggest = ""

            if suggest:
                ModernToast.show_message(
                    t_format("内置 Chromium 对部分视频兼容性有限；如需播放可手动切换到 {}", suggest),
                    self,
                    is_error=True,
                )
            else:
                ModernToast.show_message(t("检测到内置 Chromium 编解码受限，将继续使用当前浏览器"), self, is_error=True)

            self._suppress_codec_stay_toast = True
            self._send_command("CODEC_STAY")
            self._awaiting_codec_consent = False
        
        elif line.startswith("[BROWSER] CODEC_SWITCH|"):
            target = line.split("|", 1)[1].strip()
            ModernToast.show_message(t_format("已切换到系统浏览器：{}", target), self)
        
        elif line.startswith("[BROWSER] CODEC_STAY|"):
            if getattr(self, "_suppress_codec_stay_toast", False):
                self._suppress_codec_stay_toast = False
            else:
                ModernToast.show_message(t("已继续使用内置 Chromium"), self)
        
        elif line.startswith("[BROWSER] CODEC_DECISION_TIMEOUT|"):
            self._awaiting_codec_consent = False
            ModernToast.show_message(t("未收到确认，继续使用内置 Chromium"), self)
        
        elif "内置 Chromium 缺少 H264/AAC，已自动切换系统浏览器" in line:
            # 兼容旧日志
            ModernToast.show_message(t("检测到内置 Chromium 编解码受限，已切换系统浏览器播放视频"), self)
        
        elif "WARN: 内置 Chromium 缺少 H264/AAC" in line:
            ModernToast.show_message(t("内置 Chromium 编解码受限，部分站点可能无法播放视频，建议切换 Edge/Chrome"), self, is_error=True)

        elif line.startswith("[BROWSER] AUTO_SELECT|"):
            parts = line.split("|", 2)
            browser_id = parts[1].strip() if len(parts) > 1 else ""
            browser_name = parts[2].strip() if len(parts) > 2 else browser_id
            if browser_id:
                self._selected_browser = browser_id
                self._persist_browser_option("browser", browser_id)
                try:
                    self.browser_selector.setSelectedBrowser(browser_id)
                except Exception:
                    pass
            ModernToast.show_message(t_format("已自动选择可用浏览器：{}", browser_name), self)

        elif line.startswith("[BROWSER] AUTO_FALLBACK|"):
            parts = line.split("|", 2)
            browser_id = parts[1].strip() if len(parts) > 1 else ""
            browser_name = parts[2].strip() if len(parts) > 2 else browser_id
            if browser_id:
                self._selected_browser = browser_id
                self._persist_browser_option("browser", browser_id)
                try:
                    self.browser_selector.setSelectedBrowser(browser_id)
                except Exception:
                    pass
            ModernToast.show_message(t_format("当前浏览器启动失败，已自动切换到 {}", browser_name), self, is_error=True)
        
        elif "[BROWSER] READY" in line:
            self._startup_ready = True
            self._startup_page_opened = True
            # 检查是否已经是运行状态，避免重复处理
            if hasattr(self, '_browser_running') and self._browser_running:
                return
            self._browser_running = True
            self.btn_launch.setText(t("关闭浏览器"))
            self.btn_launch.setEnabled(True)
            self.btn_extract.setEnabled(True)
            self.btn_clear_cookies.setEnabled(True)
            self.btn_media_capture.setEnabled(True)
            self._apply_status_visual("running")
            self.browser_selector.setEnabled(False)
            # 显示气泡，隐藏面板
            self._hide_to_bubble_if_enabled()
        
        elif "[BROWSER] CLOSED" in line:
            was_started_successfully = bool(
                getattr(self, "_startup_ready", False)
                or getattr(self, "_startup_page_opened", False)
                or getattr(self, "_browser_running", False)
            )
            self._process_watchdog.stop()
            self._browser_running = False
            self._browser_process = None
            self.btn_launch.setText(t("启动浏览器"))
            self.btn_launch.setEnabled(True)
            self.btn_extract.setEnabled(False)
            self.btn_clear_cookies.setEnabled(False)
            self.btn_media_capture.setEnabled(False)
            self.btn_media_capture.setChecked(False)
            self.btn_media_capture.setText(t("📡 媒体捕获: 关"))
            self._media_window.hide()
            self._apply_status_visual("idle")
            self.browser_selector.setEnabled(True)
            self._restore_panel_visibility()
            self._handling_process_exit = False
            
            # 浏览器早退时自动切到另一个已检测到的浏览器，防止“启动后秒退”
            uptime = time.time() - self._browser_start_time if self._browser_start_time else 999
            fallback_browser_id = self._pick_alternate_browser_id(self._selected_browser)
            if (
                uptime < 8
                and not was_started_successfully
                and not self._fallback_attempted
                and fallback_browser_id
                and not getattr(self, "_browser_closing", False)
            ):
                self._fallback_attempted = True
                self._selected_browser = fallback_browser_id
                self._persist_browser_option("browser", fallback_browser_id)
                try:
                    self.browser_selector.setSelectedBrowser(fallback_browser_id)
                except Exception:
                    pass
                fallback_info = self._get_browser_info(fallback_browser_id)
                fallback_name = (fallback_info or {}).get("name", fallback_browser_id)
                ModernToast.show_message(t_format("浏览器启动不稳定，已自动切换到 {}", fallback_name), self, is_error=True)
                retry_url = self._last_launch_url or self.url_edit.text().strip()
                QTimer.singleShot(300, lambda: self._start_browser(retry_url, auto_fallback=True))
        
        elif "[BROWSER] 已打开页面:" in line:
            self._startup_page_opened = True
            # 【关键修复】如果收到了"已打开页面"消息，但没有收到 READY 信号，
            # 说明 READY 信号可能丢失或延迟，自动更新状态为"运行中"
            if not hasattr(self, '_browser_running') or not self._browser_running:
                print("[GUI] 收到'已打开页面'消息，自动更新状态为运行中")
                self._startup_ready = True
                self._browser_running = True
                self.btn_launch.setText(t("关闭浏览器"))
                self.btn_launch.setEnabled(True)
                self.btn_extract.setEnabled(True)
                self.btn_clear_cookies.setEnabled(True)
                self.btn_media_capture.setEnabled(True)
                self._apply_status_visual("running")
                self.browser_selector.setEnabled(False)
                self._hide_to_bubble_if_enabled()
        
        elif "[BROWSER] 导航到:" in line:
            url = line.split("导航到:")[1].strip()
            self.url_edit.setText(url)
        
        elif "[BROWSER] 标题:" in line:
            title = line.split("标题:")[1].strip()
            self.info_label.setText(title)
        
        elif "[BROWSER] Cookie已保存:" in line:
            ModernToast.show_message(t("Cookie 已保存！现在可以下载受限视频了 ✨"), self)
        
        elif "[BROWSER] 没有Cookie可保存" in line:
            ModernToast.show_message(t("当前页面没有可提取的 Cookie。请尝试刷新网页后再试 ⚠️"), self, is_error=True)
        
        elif "[BROWSER] Cookie已清除" in line:
            ModernToast.show_message(t("Cookie 已清除！所有网站登录状态已重置 ✨"), self)

        elif "[BROWSER] 已清空捕获记录" in line:
            pass
        
        elif "[BROWSER] ERROR:" in line:
            error = line.split("ERROR:")[1].strip()
            self._apply_status_visual("error")
            self.btn_launch.setEnabled(True)
            self._restore_panel_visibility()
             
            # 提供更友好的错误提示
            msg = t_format("浏览器启动失败：\n\n{}\n\n建议操作：\n1. 确保已安装所选浏览器\n2. 尝试切换到其他已检测到的浏览器\n3. 检查是否有其他浏览器实例正在运行", error)
            self._show_non_blocking_message(QMessageBox.Critical, t("启动失败"), msg)
        
        elif "[浏览器启动失败]" in line:
            # 兼容子进程异常日志格式，确保 UI 状态能恢复
            self._apply_status_visual("error")
            self.btn_launch.setEnabled(True)
            self._restore_panel_visibility()
        
        elif "[MEDIA]" in line:
            # 处理媒体捕获信息
            try:
                media_json = line.split("[MEDIA]")[1].strip()
                # print(f"[DEBUG] 收到媒体数据: {media_json[:100]}...")
                media_info = json.loads(media_json)
                self._media_window.add_media(media_info)
            except Exception as e:
                now_ts = time.time()
                if (now_ts - float(getattr(self, "_last_media_parse_error_at", 0.0))) >= 5:
                    self._last_media_parse_error_at = now_ts
                    ModernToast.show_message(t("媒体数据解析异常，已自动忽略损坏数据"), self, is_error=True)
                print(f"[DEBUG] 媒体解析错误: {e}")
                pass
        
        elif "[BROWSER] PONG" in line:
            print("[DEBUG] 收到子进程 PONG，连接正常")
        
        elif "[DEBUG]" in line:
            # 转发子进程的调试信息
            print(f"子进程调试: {line}")
    
    def _stop_browser(self):
        """停止浏览器"""
        self._browser_closing = True
        self._process_watchdog.stop()
        if self._browser_process:
            try:
                self._browser_process.stdin.write("QUIT\n")
                self._browser_process.stdin.flush()
            except:
                pass
            try:
                self._browser_process.terminate()
            except:
                pass
    
    def _on_media_selected(self, url: str, title: str):
        """处理用户选择下载媒体"""
        self.media_url_selected.emit(url, title)
        ModernToast.show_message(t_format("已发送到下载器 ✨\n{}...", title[:30]), self)

    def _on_media_stop_requested(self, url: str = ""):
        """卡片上的"停止"：转交主窗口取消当前下载。"""
        controller = getattr(self, "controller", None)
        cancel = getattr(controller, "cancel_download", None)
        if callable(cancel):
            try:
                cancel()
                ModernToast.show_message(t("已请求停止下载"), self)
            except Exception as exc:  # noqa: BLE001
                print(f"[MEDIA] 停止下载失败: {exc}")
        else:
            ModernToast.show_message(t("当前没有可停止的下载任务"), self, is_error=True)
        
    
    def _toggle_media_capture(self, checked):
        """切换媒体捕获状态"""
        if checked:
            self.btn_media_capture.setText(t("📡 媒体捕获: 开"))
            self._apply_status_visual("capture")
            self._media_window.showNormal()
            self._media_window.show()
            self._media_window.raise_()
            self._media_window.activateWindow()
            # 每次开启都清空历史结果，保证面板聚焦当前播放会话
            self._media_window._clear_media()
            self._send_command("ENABLE_CAPTURE")
            self._send_command("PING")  # 验证连接
            ModernToast.show_message(t("媒体捕获已开启，正在监听网络请求... 📡"), self)
        else:
            self.btn_media_capture.setText(t("📡 媒体捕获: 关"))
            self._apply_status_visual("running" if self._browser_running else "idle")
            self._media_window.hide()
            self._send_command("DISABLE_CAPTURE")
            ModernToast.show_message(t("媒体捕获已关闭 🛑"), self)
            
    def _on_media_window_closed(self):
        """当媒体捕获窗口被手动关闭时"""
        if self.btn_media_capture.isChecked():
            self.btn_media_capture.setChecked(False)
            self.btn_media_capture.setText(t("📡 媒体捕获: 关"))
            self._send_command("DISABLE_CAPTURE")
            self._apply_status_visual("running" if self._browser_running else "idle")
            ModernToast.show_message(t("媒体捕获已关闭 🛑"), self)

    def get_captured_media_snapshot(self):
        try:
            return self._media_window.get_media_snapshot()
        except Exception:
            return []
    
    def _send_command(self, cmd):
        """发送命令到浏览器"""
        proc = self._browser_process
        if proc and proc.poll() is None:
            try:
                if proc.stdin is None:
                    raise RuntimeError(t("stdin 不可用"))
                proc.stdin.write(cmd + "\n")
                proc.stdin.flush()
            except Exception as e:
                try:
                    self.browser_output.emit(f"[DEBUG] 命令发送失败: {e}")
                except Exception:
                    pass
    
    def _navigate(self):
        url = self.url_edit.text().strip()
        if url:
            self._send_command(f"GOTO {url}")
    
    def _quick_goto(self, url):
        """快捷导航 - 直接启动浏览器并进入网站"""
        full_url = f"https://{url}" if not url.startswith("http") else url
        self.url_edit.setText(full_url)
        
        if self._browser_running:
            # 已运行，直接导航
            self._send_command(f"GOTO {full_url}")
        else:
            # 未运行，启动浏览器并进入网站
            self._start_browser(full_url)
    
    def _extract_cookies(self):
        self._send_command("COOKIES")
    
    def _clear_cookies(self):
        """清除浏览器 Cookie"""
        if ModernConfirmDialog.confirm(
            self, 
            t("清除确认"), 
            t("确定要清除所有 Cookie 吗？\n这将退出所有已登录的网站，并删除本地保存的 Cookie 文件。")
        ):
            self._send_command("CLEAR_COOKIES")
    
    def closeEvent(self, event):
        try:
            self._media_window.close()
        except Exception:
            pass
        self._stop_browser()
        self._bubble.hideBubble()
        self._bubble.close()
        super().closeEvent(event)


def launch_browser_controller(parent=None):
    """启动浏览器控制器"""
    controller = PlaywrightBrowserController(parent)
    controller.show()
    return controller


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = PlaywrightBrowserController()
    window.show()
    
    sys.exit(app.exec_())
