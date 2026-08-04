import ctypes
from PyQt5.QtCore import QSettings
from .constants import RUNTIME_APP_DIR
"""更新相关工具函数"""
import time
import sys
import os
import re
from urllib.parse import urlparse
import urllib.request
import urllib.error
import traceback
import subprocess

"""更新相关工具函数"""
def _parse_update_mirror_prefixes(raw_text: str):
    if not raw_text:
        return []
    raw_text = raw_text.replace("\n", ",")
    prefixes = []
    for item in raw_text.split(","):
        value = (item or "").strip()
        if value:
            prefixes.append(value)
    return prefixes


DEFAULT_UPDATE_MIRROR_PREFIXES = [
    "https://gh-proxy.com/",
    "https://gh.ddlc.top/",
    "https://ghfast.top/",
    "https://gh.noki.icu/",
]

_GITHUB_DIRECT_CACHE = {
    "value": None,
    "ts": 0.0,
}
_GITHUB_DIRECT_TTL = 300  # seconds


def _is_github_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        host = urlparse(url).netloc.lower()
        return host.endswith("github.com") or host.endswith("githubusercontent.com")
    except Exception:
        return False


def _probe_github_direct_available() -> bool:
    try:
        import urllib.request
        import urllib.error

        # Use a lightweight endpoint to detect reachability.
        url = "https://api.github.com/rate_limit"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return True if response else False
        except urllib.error.HTTPError:
            # HTTP error still means the network path is reachable.
            return True
    except Exception:
        return False


def _is_github_direct_available() -> bool:
    try:
        now = time.time()
        cached = _GITHUB_DIRECT_CACHE.get("value", None)
        ts = _GITHUB_DIRECT_CACHE.get("ts", 0.0)
        if cached is not None and (now - ts) < _GITHUB_DIRECT_TTL:
            return cached
        available = _probe_github_direct_available()
        _GITHUB_DIRECT_CACHE["value"] = available
        _GITHUB_DIRECT_CACHE["ts"] = now
        return available
    except Exception:
        return False


def _get_update_mirror_prefixes():
    env = os.environ.get("CHAOS_UPDATE_MIRRORS") or os.environ.get("CHAOS_UPDATE_MIRROR_PREFIXES")
    if env:
        return _parse_update_mirror_prefixes(env)
    try:
        settings = QSettings("VideoDownloader", "Settings")
        val = str(settings.value("update_mirror_prefixes", "") or "").strip()
        prefixes = _parse_update_mirror_prefixes(val)
        if prefixes:
            return prefixes
    except Exception:
        pass
    return DEFAULT_UPDATE_MIRROR_PREFIXES


def _build_url_candidates(url: str):
    if not _is_github_url(url):
        return [url]
    
    candidates = []
    direct_first = _is_github_direct_available()
    
    # 获取镜像前缀
    prefixes = _get_update_mirror_prefixes()
    
    if direct_first:
        # 如果检测到直连可用，优先尝试直连
        candidates.append(url)
    
    # 添加镜像候选项
    if prefixes:
        for prefix in prefixes:
            try:
                if "{url}" in prefix:
                    candidate = prefix.replace("{url}", url)
                else:
                    candidate = prefix.rstrip("/") + "/" + url
                if candidate not in candidates:
                    candidates.append(candidate)
            except Exception:
                continue
    
    # 如果直连不可用，或者镜像已尝试完，最后一定补充直连作为兜底
    if url not in candidates:
        candidates.append(url)
        
    return candidates


def _open_url_with_fallback(url: str, timeout: int = 15, headers: dict = None):
    import urllib.request

    last_error = None
    for candidate in _build_url_candidates(url):
        try:
            req = urllib.request.Request(candidate, headers=headers or {})
            return urllib.request.urlopen(req, timeout=timeout)
        except Exception as e:
            last_error = e
            continue
    if last_error:
        raise last_error
    raise RuntimeError("No valid update URL candidates")


DEFAULT_APP_WEBSITE_URL = "https://hundunxiazaiqi.cn/"
DEFAULT_APP_UPDATE_METADATA_URL = DEFAULT_APP_WEBSITE_URL.rstrip("/") + "/version.json"
_APP_VERSION_CACHE = None


def get_app_version():
    """获取当前软件版本号。"""
    global _APP_VERSION_CACHE
    if _APP_VERSION_CACHE:
        return _APP_VERSION_CACHE

    version_info_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version_info.txt")
    candidates = [
        version_info_path,
        os.path.join(RUNTIME_APP_DIR, "version_info.txt"),
    ]

    for candidate in candidates:
        try:
            if not os.path.isfile(candidate):
                continue
            with open(candidate, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r"ProductVersion', u'(\d+\.\d+\.\d+)(?:\.\d+)?'", content)
            if match:
                _APP_VERSION_CACHE = match.group(1)
                return _APP_VERSION_CACHE
        except Exception:
            continue

    # 打包后的 exe 往往只带版本资源，不一定携带 version_info.txt。
    exe_candidates = []
    try:
        if getattr(sys, "frozen", False):
            exe_candidates.append(sys.executable)
    except Exception:
        pass

    try:
        main_exe = os.path.join(RUNTIME_APP_DIR, "SourceCraft.exe")
        if main_exe not in exe_candidates:
            exe_candidates.append(main_exe)
    except Exception:
        pass

    for candidate in exe_candidates:
        try:
            version = _read_windows_exe_version(candidate)
            if version:
                _APP_VERSION_CACHE = version
                return _APP_VERSION_CACHE
        except Exception:
            continue

    _APP_VERSION_CACHE = "0.0.0"
    return _APP_VERSION_CACHE


def _read_windows_exe_version(exe_path):
    """从 Windows 可执行文件的版本资源中读取 X.Y.Z 版本号。"""
    if os.name != "nt" or not exe_path or not os.path.isfile(exe_path):
        return None

    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(exe_path), None)
        if not size:
            return None

        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(exe_path), 0, size, buffer):
            return None

        value_ptr = ctypes.c_void_p()
        value_len = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(buffer, "\\", ctypes.byref(value_ptr), ctypes.byref(value_len)):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", ctypes.c_uint32),
                ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32),
                ("dwFileVersionLS", ctypes.c_uint32),
                ("dwProductVersionMS", ctypes.c_uint32),
                ("dwProductVersionLS", ctypes.c_uint32),
                ("dwFileFlagsMask", ctypes.c_uint32),
                ("dwFileFlags", ctypes.c_uint32),
                ("dwFileOS", ctypes.c_uint32),
                ("dwFileType", ctypes.c_uint32),
                ("dwFileSubtype", ctypes.c_uint32),
                ("dwFileDateMS", ctypes.c_uint32),
                ("dwFileDateLS", ctypes.c_uint32),
            ]

        ffi = ctypes.cast(value_ptr.value, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        major = (ffi.dwProductVersionMS >> 16) & 0xFFFF
        minor = ffi.dwProductVersionMS & 0xFFFF
        patch = (ffi.dwProductVersionLS >> 16) & 0xFFFF

        if any((major, minor, patch)):
            return f"{major}.{minor}.{patch}"
    except Exception as e:
        print(f"读取 exe 版本资源失败: {e}")

    return None


def compare_version_text(version1, version2):
    """比较版本号，返回 -1 / 0 / 1。"""
    try:
        def clean_version(version):
            version = str(version or "").strip()
            if version.startswith("v"):
                version = version[1:]
            match = re.match(r"(\d+(?:\.\d+)+)", version)
            return match.group(1) if match else version

        v1_clean = clean_version(version1)
        v2_clean = clean_version(version2)

        if v1_clean == v2_clean:
            return 0

        v1_parts = []
        v2_parts = []

        for part in v1_clean.replace("-", ".").split("."):
            if part.isdigit():
                v1_parts.append(int(part))
            elif part:
                numbers = re.findall(r"\d+", part)
                if numbers:
                    v1_parts.append(int(numbers[0]))

        for part in v2_clean.replace("-", ".").split("."):
            if part.isdigit():
                v2_parts.append(int(part))
            elif part:
                numbers = re.findall(r"\d+", part)
                if numbers:
                    v2_parts.append(int(numbers[0]))

        if not v1_parts:
            v1_parts = [0]
        if not v2_parts:
            v2_parts = [0]

        max_len = max(len(v1_parts), len(v2_parts))
        v1_parts.extend([0] * (max_len - len(v1_parts)))
        v2_parts.extend([0] * (max_len - len(v2_parts)))

        for i in range(max_len):
            if v1_parts[i] < v2_parts[i]:
                return -1
            if v1_parts[i] > v2_parts[i]:
                return 1
        return 0
    except Exception as e:
        print(f"软件版本比较失败: {e}")
        return 0


