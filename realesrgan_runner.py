# -*- coding: utf-8 -*-
"""
Real-ESRGAN / GFPGAN 集成运行时

目标：
1. 为主程序提供统一的依赖检查、模型管理和推理入口
2. 兼容源码运行与 PyInstaller 打包运行
3. 尽量贴近上游 inference_realesrgan.py / inference_realesrgan_video.py 的能力
"""

import os
import json
import math
import hashlib
import importlib.util
import queue
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import traceback
import warnings
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

try:
    import cv2
    CV2_AVAILABLE = True
except Exception:
    cv2 = None
    CV2_AVAILABLE = False

try:
    import torch
    from torch.nn import functional as F
    TORCH_AVAILABLE = True
except Exception:
    torch = None
    F = None
    TORCH_AVAILABLE = False

warnings.filterwarnings(
    "ignore",
    message="The parameter 'pretrained' is deprecated since 0.13 and may be removed in the future, please use 'weights' instead.",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message="Arguments other than a weight enum or `None` for 'weights' are deprecated since 0.13 and may be removed in the future.*",
    category=UserWarning,
)


class RealesrganCancelledError(RuntimeError):
    """用户主动取消任务。"""


NCNN_STALL_TIMEOUT_SECONDS = 45.0
NCNN_PROGRESS_LOG_INTERVAL_SECONDS = 10.0
_SYSTEM_GPU_INFO_CACHE: Optional[List[Dict[str, str]]] = None
_RUNTIME_PERF_CACHE: Optional[Dict[str, Dict[str, object]]] = None
_RUNTIME_PERF_CACHE_PATH: Optional[str] = None
_ONNX_EXPORT_LOCK = threading.Lock()
_REALESRGAN_RELEASE_SETTINGS_CACHE: Optional[Dict[str, object]] = None


@dataclass(frozen=True)
class ModelConfig:
    name: str
    display_name: str
    netscale: int
    kind: str
    urls: Tuple[str, ...]


MODEL_CONFIGS: Dict[str, ModelConfig] = {
    "RealESRGAN_x4plus": ModelConfig(
        name="RealESRGAN_x4plus",
        display_name="RealESRGAN x4plus",
        netscale=4,
        kind="rrdb_x4",
        urls=("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",),
    ),
    "RealESRNet_x4plus": ModelConfig(
        name="RealESRNet_x4plus",
        display_name="RealESRNet x4plus",
        netscale=4,
        kind="rrdb_x4",
        urls=("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.1/RealESRNet_x4plus.pth",),
    ),
    "RealESRGAN_x4plus_anime_6B": ModelConfig(
        name="RealESRGAN_x4plus_anime_6B",
        display_name="RealESRGAN x4plus anime 6B",
        netscale=4,
        kind="rrdb_anime_x4",
        urls=("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",),
    ),
    "RealESRGAN_x2plus": ModelConfig(
        name="RealESRGAN_x2plus",
        display_name="RealESRGAN x2plus",
        netscale=2,
        kind="rrdb_x2",
        urls=("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",),
    ),
    "realesr-animevideov3": ModelConfig(
        name="realesr-animevideov3",
        display_name="realesr-animevideov3",
        netscale=4,
        kind="srvgg_anime_x4",
        urls=("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth",),
    ),
    "realesr-general-x4v3": ModelConfig(
        name="realesr-general-x4v3",
        display_name="realesr-general-x4v3",
        netscale=4,
        kind="srvgg_general_x4",
        urls=(
            "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
            "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-wdn-x4v3.pth",
        ),
    ),
    "realesrgan-x4plus": ModelConfig(
        name="realesrgan-x4plus",
        display_name="RealESRGAN x4plus",
        netscale=4,
        kind="rrdb_x4",
        urls=("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",),
    ),
    "realesrgan-x4plus-anime": ModelConfig(
        name="realesrgan-x4plus-anime",
        display_name="RealESRGAN x4plus anime",
        netscale=4,
        kind="rrdb_anime_x4",
        urls=("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",),
    ),
}

GFPGAN_MODEL_URL = "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.3.pth"
FACEXLIB_MODEL_URLS = (
    "https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth",
    "https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth",
)
NCNN_RUNTIME_ARCHIVE_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
    "realesrgan-ncnn-vulkan-20220424-windows.zip"
)

SUPPORTED_INPUT_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff",
    ".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm", ".m4v",
}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm", ".m4v"}
_FFMPEG_ENCODER_CACHE: Dict[str, bool] = {}
_FFMPEG_RUNTIME_ENCODER_CACHE: Dict[str, bool] = {}
_NCNN_GPU_SELECTION_CACHE: Dict[str, str] = {}
NCNN_MODEL_NAME_MAP = {
    "RealESRGAN_x4plus": "realesrgan-x4plus",
    "RealESRNet_x4plus": "realesrnet-x4plus",
    "RealESRGAN_x4plus_anime_6B": "realesrgan-x4plus-anime",
    "realesr-animevideov3": "realesr-animevideov3",
}


def get_runtime_app_dir() -> str:
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _can_prepare_runtime_dir(path: str) -> bool:
    candidate = os.path.abspath(str(path or "").strip())
    if not candidate:
        return False
    try:
        os.makedirs(candidate, exist_ok=True)
        probe_path = os.path.join(candidate, ".write_test.tmp")
        with open(probe_path, "wb") as fh:
            fh.write(b"ok")
        os.remove(probe_path)
        return True
    except Exception:
        return False


def get_realesrgan_data_root() -> str:
    override_root = os.environ.get("SOURCECRAFT_REALESRGAN_DATA_DIR", "").strip()
    if override_root:
        root = os.path.abspath(override_root)
    elif getattr(sys, "frozen", False):
        packaged_root = os.path.join(get_runtime_app_dir(), "realesrgan_runtime")
        if _can_prepare_runtime_dir(packaged_root):
            root = packaged_root
        elif sys.platform == "win32":
            base_dir = (
                os.environ.get("LOCALAPPDATA")
                or os.environ.get("APPDATA")
                or os.path.expanduser("~")
            )
            root = os.path.join(base_dir, "SourceCraft", "realesrgan_runtime")
        elif sys.platform == "darwin":
            root = os.path.expanduser("~/Library/Application Support/SourceCraft/realesrgan_runtime")
        else:
            root = os.path.expanduser("~/.cache/sourcecraft/realesrgan_runtime")
    else:
        root = os.path.join(get_runtime_app_dir(), "realesrgan_runtime")
    os.makedirs(root, exist_ok=True)
    return root


def _format_bytes(size: int) -> str:
    value = max(float(size or 0), 0.0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def _get_ca_bundle_path() -> Optional[str]:
    try:
        import certifi

        ca_path = certifi.where()
        if ca_path and os.path.isfile(ca_path):
            return ca_path
    except Exception:
        pass
    return None


def _build_ssl_context_candidates() -> List[Tuple[str, ssl.SSLContext]]:
    contexts: List[Tuple[str, ssl.SSLContext]] = []
    ca_bundle = _get_ca_bundle_path()
    if ca_bundle:
        try:
            contexts.append(("certifi CA", ssl.create_default_context(cafile=ca_bundle)))
        except Exception:
            pass
    try:
        contexts.append(("系统证书库", ssl.create_default_context()))
    except Exception:
        pass
    if not contexts:
        contexts.append(("默认 TLS 上下文", ssl.create_default_context()))
    return contexts


def _split_download_setting(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\r\n;|]+", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        items: List[str] = []
        for item in value:
            items.extend(_split_download_setting(item))
        return items
    return []


def _find_runtime_json_file(filename: str) -> str:
    candidates = [
        os.path.join(get_runtime_app_dir(), filename),
        os.path.join(get_runtime_app_dir(), "_internal", filename),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), filename),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "_internal", filename),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return ""


def _load_realesrgan_release_settings() -> Dict[str, object]:
    global _REALESRGAN_RELEASE_SETTINGS_CACHE
    if _REALESRGAN_RELEASE_SETTINGS_CACHE is not None:
        return dict(_REALESRGAN_RELEASE_SETTINGS_CACHE)

    settings_path = _find_runtime_json_file("release_settings.json")
    if not settings_path:
        _REALESRGAN_RELEASE_SETTINGS_CACHE = {}
        return {}

    try:
        with open(settings_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _REALESRGAN_RELEASE_SETTINGS_CACHE = data if isinstance(data, dict) else {}
    except Exception:
        _REALESRGAN_RELEASE_SETTINGS_CACHE = {}
    return dict(_REALESRGAN_RELEASE_SETTINGS_CACHE)


def _get_realesrgan_mirror_bases() -> List[str]:
    settings = _load_realesrgan_release_settings()
    bases: List[str] = []
    for raw_value in (
        os.environ.get("SOURCECRAFT_REALESRGAN_MIRROR_BASE_URLS"),
        settings.get("realesrgan_mirror_base_urls"),
        settings.get("realesrgan_mirror_base_url"),
    ):
        for item in _split_download_setting(raw_value):
            if item.lower().startswith(("http://", "https://")):
                bases.append(item.rstrip("/"))
    return _unique_preserve_order(bases)


def _get_realesrgan_file_specific_mirrors(filename: str) -> List[str]:
    if not filename:
        return []
    settings = _load_realesrgan_release_settings()
    mapping = settings.get("realesrgan_download_mirrors")
    if not isinstance(mapping, dict):
        return []
    values = mapping.get(filename)
    if values is None:
        return []
    return [
        item for item in _split_download_setting(values)
        if item.lower().startswith(("http://", "https://"))
    ]


def _has_configured_realesrgan_mirrors() -> bool:
    if _get_realesrgan_mirror_bases():
        return True
    settings = _load_realesrgan_release_settings()
    mapping = settings.get("realesrgan_download_mirrors")
    if not isinstance(mapping, dict):
        return False
    for value in mapping.values():
        if any(item.lower().startswith(("http://", "https://")) for item in _split_download_setting(value)):
            return True
    return False


def _build_download_candidate_urls(url: str) -> List[str]:
    origin = str(url or "").strip()
    if not origin:
        return []

    return [origin]


def _describe_download_source(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc or parsed.path or str(url or "").strip()
    if parsed.path:
        return f"{host}{parsed.path}"
    return host


def _build_proxy_openers(ssl_context):
    from urllib.request import HTTPSHandler, ProxyHandler, build_opener

    openers = [("系统网络配置", build_opener(HTTPSHandler(context=ssl_context)))]
    openers.append(("直连", build_opener(ProxyHandler({}), HTTPSHandler(context=ssl_context))))
    return openers


def _quick_connectivity_check(url: str) -> bool:
    """快速检测下载服务器是否可达（5秒超时），避免用户长时间等待后才发现失败。"""
    import socket
    from urllib.parse import urlparse

    try:
        host = urlparse(url).hostname
        if not host:
            return True
    except Exception:
        return True

    # 尝试直连 socket 检测
    try:
        socket.create_connection((host, 443), timeout=5)
        return True
    except (socket.timeout, OSError, ConnectionRefusedError):
        pass

    # 尝试通过系统代理检测
    try:
        from urllib.request import HTTPSHandler, Request, build_opener
        opener = build_opener(HTTPSHandler())
        opener.open(Request(url, method="HEAD"), timeout=5)
        return True
    except Exception:
        pass

    return False


def _verify_model_file_integrity(file_path: str) -> bool:
    """基础模型文件完整性校验。

    检查 .pth 文件是否为有效的 ZIP 归档（PyTorch 1.x+ 格式）。
    对于其他文件，检查大小是否合理。
    """
    if not os.path.isfile(file_path):
        return False
    file_size = os.path.getsize(file_path)
    if file_size < 1024:
        return False
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pth":
        try:
            import zipfile
            with zipfile.ZipFile(file_path) as zf:
                bad = zf.testzip()
                if bad:
                    return False
        except zipfile.BadZipFile:
            # Older pickle-format .pth might not be ZIP; accept it if size is plausible
            if file_size < 500000:
                return False
    return True


def _download_url_to_path(url: str, target: str, progress_cb=None) -> None:
    from urllib.error import HTTPError
    from urllib.request import Request

    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp_target = target + ".download"
    if os.path.isfile(tmp_target):
        try:
            os.remove(tmp_target)
        except OSError:
            pass

    chunk_size = 2 * 1024 * 1024
    filename = os.path.basename(target)
    candidate_urls = _build_download_candidate_urls(url)
    verify_candidates = _build_ssl_context_candidates()
    failures: List[str] = []

    for source_index, candidate_url in enumerate(candidate_urls, start=1):
        source_desc = _describe_download_source(candidate_url)
        if progress_cb and len(candidate_urls) > 1:
            progress_cb(f"🌐 尝试下载源 {source_index}/{len(candidate_urls)}: {source_desc}")

        stop_current_source = False
        for verify_label, ssl_context in verify_candidates:
            proxy_openers = _build_proxy_openers(ssl_context)
            for proxy_label, opener in proxy_openers:
                if progress_cb and len(proxy_openers) > 1:
                    progress_cb(f"   网络模式: {proxy_label}")
                request = Request(candidate_url, headers={"User-Agent": "SourceCraft/1.0"})
                downloaded = 0
                last_reported = -1
                total_size = 0
                try:
                    response_ctx = opener.open(request, timeout=60)
                    with response_ctx as response, open(tmp_target, "wb") as fh:
                        total_size = int(response.headers.get("Content-Length") or 0)
                        if progress_cb:
                            source_note = f" | {source_desc}" if len(candidate_urls) > 1 else ""
                            if total_size > 0:
                                progress_cb(f"⬇️ 开始下载: {filename} ({_format_bytes(total_size)}){source_note}")
                            else:
                                progress_cb(f"⬇️ 开始下载: {filename}{source_note}")
                        while True:
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break
                            fh.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb and total_size > 0:
                                percent = min(int(downloaded * 100 / max(total_size, 1)), 100)
                                if percent != last_reported and (percent == 100 or percent - last_reported >= 5):
                                    progress_cb(
                                        f"   下载中 {filename}: {percent}% "
                                        f"({_format_bytes(downloaded)}/{_format_bytes(total_size)})"
                                    )
                                    last_reported = percent
                            elif progress_cb and downloaded - max(last_reported, 0) >= 8 * 1024 * 1024:
                                progress_cb(f"   下载中 {filename}: {_format_bytes(downloaded)}")
                                last_reported = downloaded
                    os.replace(tmp_target, target)
                    if not _verify_model_file_integrity(target):
                        try:
                            os.remove(target)
                        except OSError:
                            pass
                        raise RuntimeError(f"下载校验失败: {filename} 文件损坏，已自动删除，请重试。")
                    return
                except Exception as exc:
                    try:
                        if os.path.isfile(tmp_target):
                            os.remove(tmp_target)
                    except OSError:
                        pass
                    failures.append(f"{source_desc} [{proxy_label}] ({verify_label}): {exc}")
                    if isinstance(exc, HTTPError):
                        stop_current_source = True
                        break
            if stop_current_source:
                break

    if failures:
        raise RuntimeError(
            f"画质修复模型 {filename} 下载失败，请开启代理（VPN）后重试。"
        )
    raise RuntimeError(f"下载失败: {filename}")


def _get_ncnn_runtime_cache_dir() -> str:
    cache_dir = os.path.join(get_realesrgan_data_root(), "downloads")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _windows_hidden_subprocess_kwargs() -> Dict[str, object]:
    if sys.platform != "win32":
        return {}

    kwargs: Dict[str, object] = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _build_external_python_env(python_exe: str) -> Dict[str, str]:
    """
    为外部 Python 运行时构造隔离环境。

    PyInstaller 启动后会把应用的 _internal 目录加入 DLL 搜索路径/环境。
    如果直接继承给 conda Python，外部环境可能误加载打包程序里的 python313.dll，
    从而触发 "Module use of python313.dll conflicts"。
    """
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONIOENCODING", "utf-8")

    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
        child_env.pop(key, None)

    python_dir = os.path.dirname(os.path.abspath(str(python_exe or "")))
    path_entries: List[str] = []
    if python_dir:
        path_entries.extend([
            python_dir,
            os.path.join(python_dir, "DLLs"),
            os.path.join(python_dir, "Library", "bin"),
            os.path.join(python_dir, "Scripts"),
        ])

    blocked_dirs = {
        os.path.abspath(get_runtime_app_dir()),
        os.path.abspath(os.path.join(get_runtime_app_dir(), "_internal")),
        os.path.abspath(os.path.dirname(__file__)),
    }
    try:
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            blocked_dirs.add(os.path.abspath(meipass))
    except Exception:
        pass

    def _same_or_child(path: str, root: str) -> bool:
        try:
            path_abs = os.path.abspath(path)
            root_abs = os.path.abspath(root)
            return path_abs == root_abs or os.path.commonpath([path_abs, root_abs]) == root_abs
        except Exception:
            return False

    for entry in str(child_env.get("PATH") or "").split(os.pathsep):
        clean_entry = entry.strip().strip('"')
        if not clean_entry:
            continue
        if any(_same_or_child(clean_entry, blocked) for blocked in blocked_dirs):
            continue
        path_entries.append(clean_entry)

    child_env["PATH"] = os.pathsep.join(_unique_preserve_order(path_entries))
    return child_env


def _unique_preserve_order(items: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _detect_gpu_vendor(name: str, vendor: str = "") -> str:
    text = f"{name or ''} {vendor or ''}".lower()
    if any(token in text for token in ("nvidia", "geforce", "quadro", "rtx", "gtx", "tesla")):
        return "nvidia"
    if any(token in text for token in ("amd", "radeon", "firepro", "ati")):
        return "amd"
    if any(token in text for token in ("intel", "arc", "iris", "uhd", "hd graphics")):
        return "intel"
    if "microsoft basic" in text:
        return "software"
    return "unknown"


def _probe_system_gpus() -> List[Dict[str, str]]:
    global _SYSTEM_GPU_INFO_CACHE
    if _SYSTEM_GPU_INFO_CACHE is not None:
        return [dict(item) for item in _SYSTEM_GPU_INFO_CACHE]

    gpus: List[Dict[str, str]] = []
    if sys.platform == "win32":
        commands = [
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | "
                "Select-Object Name,AdapterCompatibility,PNPDeviceID | ConvertTo-Json -Compress",
            ],
            [
                "wmic",
                "path",
                "win32_VideoController",
                "get",
                "Name,AdapterCompatibility,PNPDeviceID",
                "/format:csv",
            ],
        ]
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=12,
                    **_windows_hidden_subprocess_kwargs(),
                )
            except Exception:
                continue
            if result.returncode != 0:
                continue
            stdout = str(result.stdout or "").strip()
            if not stdout:
                continue
            try:
                if "ConvertTo-Json" in " ".join(cmd):
                    data = json.loads(stdout)
                    rows = data if isinstance(data, list) else [data]
                    for row in rows:
                        name = str((row or {}).get("Name") or "").strip()
                        vendor = str((row or {}).get("AdapterCompatibility") or "").strip()
                        if not name:
                            continue
                        gpu = {
                            "name": name,
                            "vendor": vendor,
                            "vendor_key": _detect_gpu_vendor(name, vendor),
                            "pnp_device_id": str((row or {}).get("PNPDeviceID") or "").strip(),
                        }
                        if gpu["vendor_key"] != "software":
                            gpus.append(gpu)
                else:
                    for line in stdout.splitlines():
                        line = line.strip()
                        if not line or line.lower().startswith("node,") or "name" in line.lower() and "adaptercompatibility" in line.lower():
                            continue
                        parts = [part.strip() for part in line.split(",")]
                        if len(parts) < 4:
                            continue
                        vendor = parts[1]
                        name = parts[2]
                        pnp_id = parts[3]
                        if not name:
                            continue
                        gpu = {
                            "name": name,
                            "vendor": vendor,
                            "vendor_key": _detect_gpu_vendor(name, vendor),
                            "pnp_device_id": pnp_id,
                        }
                        if gpu["vendor_key"] != "software":
                            gpus.append(gpu)
            except Exception:
                continue
            if gpus:
                break

        if not gpus:
            display_class_guid = "{4d36e968-e325-11ce-bfc1-08002be10318}"
            try:
                result = subprocess.run(
                    [
                        "reg",
                        "query",
                        r"HKLM\SYSTEM\CurrentControlSet\Enum\PCI",
                        "/s",
                        "/f",
                        "ClassGUID",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    **_windows_hidden_subprocess_kwargs(),
                )
                if result.returncode == 0:
                    matched_keys: List[str] = []
                    current_key = ""
                    for raw_line in (result.stdout or "").splitlines():
                        line = raw_line.strip()
                        if not line:
                            continue
                        if line.upper().startswith("HKEY_"):
                            current_key = line
                            continue
                        if "ClassGUID" not in line:
                            continue
                        if display_class_guid.lower() not in line.lower():
                            continue
                        if current_key:
                            matched_keys.append(current_key)

                    for key_path in matched_keys:
                        try:
                            detail = subprocess.run(
                                ["reg", "query", key_path],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                timeout=10,
                                **_windows_hidden_subprocess_kwargs(),
                            )
                        except Exception:
                            continue
                        if detail.returncode != 0:
                            continue

                        values: Dict[str, str] = {}
                        for detail_line in (detail.stdout or "").splitlines():
                            stripped = detail_line.strip()
                            if not stripped or stripped.upper().startswith("HKEY_"):
                                continue
                            parts = re.split(r"\s{2,}", stripped, maxsplit=2)
                            if len(parts) >= 3:
                                values[parts[0].strip()] = parts[2].strip()

                        name = str(values.get("FriendlyName") or values.get("DeviceDesc") or "").strip()
                        vendor = str(values.get("Mfg") or "").strip()
                        if not name:
                            continue
                        if ";" in name:
                            name = name.split(";")[-1].strip()
                        if ";" in vendor:
                            vendor = vendor.split(";")[-1].strip()
                        gpu = {
                            "name": name,
                            "vendor": vendor,
                            "vendor_key": _detect_gpu_vendor(name, vendor),
                            "pnp_device_id": key_path.rsplit("\\", 1)[-1],
                        }
                        if gpu["vendor_key"] != "software":
                            gpus.append(gpu)
            except Exception:
                pass

    deduped: List[Dict[str, str]] = []
    seen = set()
    for item in gpus:
        key = (
            str(item.get("name") or "").strip().lower(),
            str(item.get("vendor") or "").strip().lower(),
            str(item.get("pnp_device_id") or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    _SYSTEM_GPU_INFO_CACHE = deduped
    return [dict(item) for item in deduped]


def _terminate_process_tree(process: Optional[subprocess.Popen]) -> None:
    if process is None:
        return

    try:
        if process.poll() is not None:
            return
    except Exception:
        return

    if sys.platform == "win32":
        pid = getattr(process, "pid", None)
        if pid:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    **_windows_hidden_subprocess_kwargs(),
                )
                return
            except Exception:
                pass

    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        pass

    try:
        if process.poll() is None:
            process.kill()
    except Exception:
        pass


def _check_cancel(cancel_cb=None) -> None:
    if callable(cancel_cb) and cancel_cb():
        raise RealesrganCancelledError("任务已取消")


def _run_command(
    cmd: List[str],
    *,
    cancel_cb=None,
    progress_cb=None,
    text: bool = True,
    encoding: str = "utf-8",
    errors: str = "replace",
    **kwargs,
):
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding=encoding,
        errors=errors,
        **_windows_hidden_subprocess_kwargs(),
        **kwargs,
    )
    try:
        while True:
            _check_cancel(cancel_cb)
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            raise RuntimeError((stderr or stdout or "命令执行失败").strip())
        return stdout, stderr
    except RealesrganCancelledError:
        if progress_cb:
            progress_cb("⚠️ 已收到取消请求，正在停止当前处理...")
        _terminate_process_tree(process)
        raise


def _get_runtime_perf_cache_path() -> str:
    global _RUNTIME_PERF_CACHE_PATH
    if _RUNTIME_PERF_CACHE_PATH:
        return _RUNTIME_PERF_CACHE_PATH
    cache_root = get_realesrgan_data_root()
    os.makedirs(cache_root, exist_ok=True)
    _RUNTIME_PERF_CACHE_PATH = os.path.join(cache_root, ".perf_cache.json")
    return _RUNTIME_PERF_CACHE_PATH


def _load_runtime_perf_cache() -> Dict[str, Dict[str, object]]:
    global _RUNTIME_PERF_CACHE
    if _RUNTIME_PERF_CACHE is not None:
        return _RUNTIME_PERF_CACHE

    default_cache: Dict[str, Dict[str, object]] = {
        "ncnn_threads": {},
        "ncnn_gpu": {},
        "torch_tiles": {},
        "video_backend": {},
        "onnx_batch": {},
        "backend_perf": {},
    }
    cache_path = _get_runtime_perf_cache_path()
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for bucket_name in list(default_cache.keys()):
                bucket = data.get(bucket_name)
                if isinstance(bucket, dict):
                    default_cache[bucket_name] = bucket
    except Exception:
        pass
    _RUNTIME_PERF_CACHE = default_cache
    return _RUNTIME_PERF_CACHE


def _get_runtime_perf_bucket(bucket_name: str) -> Dict[str, object]:
    cache = _load_runtime_perf_cache()
    bucket = cache.get(bucket_name)
    if not isinstance(bucket, dict):
        bucket = {}
        cache[bucket_name] = bucket
    return bucket


def _save_runtime_perf_cache() -> None:
    cache = _load_runtime_perf_cache()
    cache_path = _get_runtime_perf_cache_path()
    temp_path = cache_path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
        os.replace(temp_path, cache_path)
    except Exception:
        try:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def _update_runtime_perf_bucket(bucket_name: str, key: str, payload: Dict[str, object]) -> None:
    key = str(key or "").strip()
    if not key or not isinstance(payload, dict):
        return
    bucket = _get_runtime_perf_bucket(bucket_name)
    bucket[key] = dict(payload)
    _save_runtime_perf_cache()


def _resolution_bucket(width: int, height: int) -> str:
    area = max(int(width), 0) * max(int(height), 0)
    if area <= 640 * 480:
        return "sd"
    if area <= 1280 * 720:
        return "hd"
    if area <= 1920 * 1080:
        return "fhd"
    if area <= 2560 * 1440:
        return "qhd"
    return "uhd"


def _build_video_perf_profile_key(options: Optional[dict], width: int, height: int) -> str:
    options = options or {}
    model_name = str(options.get("model_name") or "realesr-general-x4v3").strip()
    outscale = _safe_float(options.get("outscale", 4), 4.0)
    face_enhance = "1" if options.get("face_enhance") else "0"
    fp32 = "1" if options.get("fp32") else "0"
    gpu_vendors = _unique_preserve_order([gpu.get("vendor_key", "") for gpu in _probe_system_gpus() if gpu.get("vendor_key")])
    vendor_key = ",".join(gpu_vendors) or "cpu"
    return "|".join([
        vendor_key,
        model_name,
        f"{outscale:.2f}",
        _resolution_bucket(width, height),
        face_enhance,
        fp32,
    ])


def _build_onnx_perf_key(
    options: Optional[dict],
    width: int,
    height: int,
    provider_name: str,
    media_kind: str = "video",
) -> str:
    profile_key = _build_video_perf_profile_key(options, width, height)
    provider_name = str(provider_name or "CPUExecutionProvider").strip()
    media_kind = str(media_kind or "video").strip().lower()
    return "|".join([media_kind, provider_name, profile_key])


def _build_backend_perf_key(
    options: Optional[dict],
    width: int,
    height: int,
    media_kind: str,
    backend_name: str,
) -> str:
    media_kind = str(media_kind or "video").strip().lower() or "video"
    backend_name = str(backend_name or "cpu").strip().lower() or "cpu"
    profile_key = _build_video_perf_profile_key(options, width, height)
    return "|".join([media_kind, backend_name, profile_key])


def _record_backend_perf(
    options: Optional[dict],
    width: int,
    height: int,
    media_kind: str,
    backend_name: str,
    speed_score: float,
    extra: Optional[Dict[str, object]] = None,
) -> None:
    speed_value = max(float(speed_score or 0.0), 0.0)
    if speed_value <= 0:
        return
    payload = {
        "speed_score": round(speed_value, 4),
        "updated_at": round(time.time(), 3),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    perf_key = _build_backend_perf_key(options, width, height, media_kind, backend_name)
    _update_runtime_perf_bucket("backend_perf", perf_key, payload)


def _get_backend_perf_entry(
    options: Optional[dict],
    width: int,
    height: int,
    media_kind: str,
    backend_name: str,
) -> Dict[str, object]:
    perf_key = _build_backend_perf_key(options, width, height, media_kind, backend_name)
    cached = _get_runtime_perf_bucket("backend_perf").get(perf_key)
    return dict(cached) if isinstance(cached, dict) else {}


def _select_measured_backend(
    candidates: List[str],
    options: Optional[dict],
    width: int,
    height: int,
    media_kind: str,
) -> str:
    best_backend = ""
    best_score = 0.0
    for backend_name in candidates:
        entry = _get_backend_perf_entry(options, width, height, media_kind, backend_name)
        score = max(float(entry.get("speed_score") or 0.0), 0.0)
        if score > best_score:
            best_score = score
            best_backend = str(backend_name or "").strip().lower()
    return best_backend


def _get_runtime_search_dirs() -> List[str]:
    candidates: List[str] = []

    try:
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(os.path.abspath(meipass))
    except Exception:
        pass

    base_dir = get_runtime_app_dir()
    candidates.append(base_dir)
    candidates.append(os.path.join(base_dir, "_internal"))

    unique_dirs: List[str] = []
    seen = set()
    for path in candidates:
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_dirs.append(os.path.abspath(path))
    return unique_dirs


def get_realesrgan_root() -> str:
    for root_dir in _get_runtime_search_dirs():
        candidate = os.path.join(root_dir, "realesrgan_runtime")
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(get_runtime_app_dir(), "realesrgan_runtime")


def get_realesrgan_ncnn_root() -> str:
    data_root = os.path.join(get_realesrgan_data_root(), "ncnn")
    if os.path.isfile(os.path.join(data_root, "realesrgan-ncnn-vulkan.exe")):
        return data_root
    for root_dir in _get_runtime_search_dirs():
        candidates = [
            os.path.join(root_dir, "realesrgan_runtime", "ncnn"),
            os.path.join(root_dir, "realesrgan_runtime"),
            root_dir,
        ]
        for candidate in candidates:
            if os.path.isfile(os.path.join(candidate, "realesrgan-ncnn-vulkan.exe")):
                return candidate
    return os.path.join(get_realesrgan_root(), "ncnn")


def get_weights_dir() -> str:
    return os.path.join(get_realesrgan_data_root(), "weights")


def get_gfpgan_weights_dir() -> str:
    return os.path.join(get_realesrgan_data_root(), "gfpgan", "weights")


def get_ncnn_models_dir() -> str:
    return os.path.join(get_realesrgan_data_root(), "ncnn", "models")


def find_realesrgan_ncnn_executable() -> str:
    path_hit = shutil.which("realesrgan-ncnn-vulkan.exe") or shutil.which("realesrgan-ncnn-vulkan")
    if path_hit and os.path.isfile(path_hit):
        return path_hit
    root = get_realesrgan_ncnn_root()
    candidates = [
        os.path.join(root, "realesrgan-ncnn-vulkan.exe"),
        os.path.join(root, "realesrgan-ncnn-vulkan"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return ""


def _find_realesrgan_ncnn_models_dir(exe_path: str) -> str:
    exe_dir = os.path.dirname(os.path.abspath(exe_path or "")) if exe_path else ""
    search_dirs = [
        get_ncnn_models_dir(),
        os.path.join(exe_dir, "models"),
        exe_dir,
        os.path.join(get_realesrgan_ncnn_root(), "models"),
    ]
    for candidate in search_dirs:
        if not candidate or not os.path.isdir(candidate):
            continue
        has_param = any(name.lower().endswith(".param") for name in os.listdir(candidate))
        has_bin = any(name.lower().endswith(".bin") for name in os.listdir(candidate))
        if has_param and has_bin:
            return candidate
    return ""


def ensure_runtime_dirs() -> None:
    os.makedirs(get_weights_dir(), exist_ok=True)
    os.makedirs(get_gfpgan_weights_dir(), exist_ok=True)
    os.makedirs(get_ncnn_models_dir(), exist_ok=True)


def _ensure_face_enhance_assets(progress_cb=None) -> None:
    ensure_runtime_dirs()
    # 1. Facexlib models
    for url in FACEXLIB_MODEL_URLS:
        filename = os.path.basename(url)
        source = os.path.join(get_weights_dir(), filename)
        target = os.path.join(get_gfpgan_weights_dir(), filename)
        if os.path.isfile(target):
            continue
        if os.path.isfile(source):
            shutil.copy2(source, target)
        else:
            if progress_cb:
                progress_cb(f"🚀 facexlib 模型缺失，准备自动从 GitHub 下载: {filename}")
            _download_url_to_path(url, target, progress_cb=progress_cb)

    # 2. GFPGAN model
    gfpgan_filename = os.path.basename(GFPGAN_MODEL_URL)
    gfpgan_target = os.path.join(get_weights_dir(), gfpgan_filename)
    if not os.path.isfile(gfpgan_target):
        if progress_cb:
            progress_cb(f"🚀 GFPGAN 模型缺失，准备自动从 GitHub 下载: {gfpgan_filename}")
        _download_url_to_path(GFPGAN_MODEL_URL, gfpgan_target, progress_cb=progress_cb)

    # 3. 同步 facexlib 模型到 facexlib 库的默认路径（防止 path 不匹配导致 facexlib 重新下载）
    _sync_facexlib_models_to_default_path()


def _get_facexlib_weights_dir() -> str:
    """获取 facexlib 库默认的模型存放目录。"""
    try:
        import facexlib.utils.misc as _fx_misc
        _root = getattr(_fx_misc, "ROOT_DIR", None)
        if _root:
            return os.path.join(_root, "facexlib", "weights")
    except Exception:
        pass
    # fallback: 用户目录下的 facexlib 缓存
    return os.path.expanduser("~/.facexlib/weights")


def _sync_facexlib_models_to_default_path() -> None:
    """将我们下载的 facexlib 模型同步到 facexlib 库的默认搜索路径。"""
    facexlib_weights_dir = _get_facexlib_weights_dir()
    if not facexlib_weights_dir:
        return
    os.makedirs(facexlib_weights_dir, exist_ok=True)
    for url in FACEXLIB_MODEL_URLS:
        filename = os.path.basename(url)
        our_path = os.path.join(get_gfpgan_weights_dir(), filename)
        default_path = os.path.join(facexlib_weights_dir, filename)
        if os.path.isfile(our_path) and not os.path.isfile(default_path):
            try:
                shutil.copy2(our_path, default_path)
            except Exception:
                pass


def _load_saved_build_python() -> str:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".build_release_config.json")
    if not os.path.isfile(config_path):
        return ""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        python_path = str(data.get("build_python") or "").strip()
        return python_path if python_path and os.path.isfile(python_path) else ""
    except Exception:
        return ""


def _get_realesrgan_script_path() -> str:
    candidates = [
        os.path.abspath(__file__),
        os.path.join(get_runtime_app_dir(), "realesrgan_runner.py"),
        os.path.join(get_runtime_app_dir(), "_internal", "realesrgan_runner.py"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return os.path.abspath(__file__)


def find_realesrgan_runtime_python() -> str:
    """
    为源码运行态自动寻找可用的画质修复 Python 运行时。
    如显式配置了外部 Python，则打包态也允许优先使用外部运行时。
    """
    external_python = _find_external_realesrgan_runtime_python()
    if external_python:
        return external_python

    if getattr(sys, "frozen", False):
        return sys.executable if _has_local_full_realesrgan_runtime() else ""

    candidates = [
        r"C:\Users\Administrator\miniconda3\envs\sourcecraft_realesrgan_cuda\python.exe",
        _load_saved_build_python(),
        r"C:\Users\Administrator\miniconda3\envs\py310aiwatermark\python.exe",
        r"C:\Users\Administrator\miniconda3\envs\py312aiwatermark\python.exe",
        r"C:\Users\Administrator\miniconda3\envs\iopaint_env\python.exe",
        r"C:\Users\Administrator\miniconda3\envs\voxcpm\python.exe",
    ]
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if not candidate or not os.path.isfile(candidate):
            continue
        try:
            result = subprocess.run(
                [
                    candidate,
                    "-c",
                    (
                        "import importlib.util as u; "
                        "mods=['torch','cv2','basicsr','realesrgan']; "
                        "print('ok' if all(u.find_spec(m) for m in mods) else 'missing')"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env=_build_external_python_env(candidate),
                **_windows_hidden_subprocess_kwargs(),
            )
            if result.returncode == 0 and result.stdout.strip() == "ok":
                return candidate
        except Exception:
            continue
    return ""


def is_supported_media(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_INPUT_EXTS


def _find_external_realesrgan_runtime_python() -> str:
    forced_python = os.environ.get("SOURCECRAFT_REALESRGAN_PYTHON", "").strip()
    if forced_python and os.path.isfile(forced_python):
        return forced_python
    return ""


def is_video_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def _find_ffmpeg_binary() -> str:
    app_dir = get_runtime_app_dir()
    bundled = os.path.join(app_dir, "ffmpeg.exe")
    if os.path.isfile(bundled):
        return bundled
    return "ffmpeg"


def _find_ffprobe_binary() -> str:
    app_dir = get_runtime_app_dir()
    bundled = os.path.join(app_dir, "ffprobe.exe")
    if os.path.isfile(bundled):
        return bundled
    return "ffprobe"


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def get_dependency_status() -> Dict[str, bool]:
    _apply_torchvision_compat_shim()
    status = {
        "torch": TORCH_AVAILABLE,
        "opencv": CV2_AVAILABLE,
        "basicsr": False,
        "realesrgan": False,
        "gfpgan": False,
        "facexlib": False,
        "ncnn_runtime": bool(find_realesrgan_ncnn_executable()),
    }
    try:
        import basicsr  # noqa: F401
        status["basicsr"] = True
    except Exception:
        pass
    try:
        import realesrgan  # noqa: F401
        status["realesrgan"] = True
    except Exception:
        pass
    try:
        import gfpgan  # noqa: F401
        status["gfpgan"] = True
    except Exception:
        pass
    try:
        import facexlib  # noqa: F401
        status["facexlib"] = True
    except Exception:
        pass
    status["external_runtime"] = bool(_find_external_realesrgan_runtime_python())
    return status


def _has_local_full_realesrgan_runtime(options: Optional[dict] = None) -> bool:
    options = options or {}
    status = get_dependency_status()
    if not all(status.get(name) for name in ("torch", "opencv", "basicsr", "realesrgan")):
        return False
    if options.get("face_enhance"):
        return bool(status.get("gfpgan")) and bool(status.get("facexlib"))
    return True


def _can_use_local_ncnn_runtime_only(options: Optional[dict] = None) -> bool:
    options = options or {}
    status = get_dependency_status()
    normalized_options = _normalize_options_for_ncnn_backend(dict(options or {}))
    return (
        bool(status.get("opencv"))
        and bool(status.get("ncnn_runtime"))
        and can_use_realesrgan_ncnn(normalized_options)
    )


def _normalize_runtime_probe_result(data: Optional[Dict[str, object]], python_path: str = "") -> Dict[str, object]:
    data = dict(data or {})
    data["python"] = str(data.get("python") or python_path or "").strip()
    data["torch_available"] = bool(data.get("torch_available"))
    data["cuda_detected"] = bool(data.get("cuda_detected", data.get("cuda_available", False)))
    data["cuda_available"] = bool(data.get("cuda_available"))
    data["cuda_version"] = data.get("cuda_version")
    data["torch_version"] = data.get("torch_version")
    data["device_name"] = str(data.get("device_name") or "").strip()
    data["onnx_available"] = bool(data.get("onnx_available"))
    data["ort_available"] = bool(data.get("ort_available"))
    providers = data.get("ort_providers") or []
    if isinstance(providers, (tuple, list)):
        data["ort_providers"] = [str(item).strip() for item in providers if str(item).strip()]
    else:
        data["ort_providers"] = []
    data["ort_cuda_available"] = bool(data.get("ort_cuda_available") or "CUDAExecutionProvider" in data["ort_providers"])
    data["ort_dml_available"] = bool(data.get("ort_dml_available") or "DmlExecutionProvider" in data["ort_providers"])
    data["ort_trt_available"] = bool(data.get("ort_trt_available") or "TensorrtExecutionProvider" in data["ort_providers"])
    data["ort_cpu_available"] = bool(data.get("ort_cpu_available") or "CPUExecutionProvider" in data["ort_providers"])
    data["onnx_error"] = str(data.get("onnx_error") or "").strip()
    data["ort_error"] = str(data.get("ort_error") or "").strip()
    data["error"] = str(data.get("error") or "").strip()
    data["preferred_video_backend"] = str(data.get("preferred_video_backend") or "").strip().lower()
    return data


def _probe_current_runtime_backends(model_name: str = "realesr-general-x4v3") -> Dict[str, object]:
    result = {
        "python": sys.executable,
        "torch_available": TORCH_AVAILABLE,
        "cuda_detected": False,
        "cuda_available": False,
        "cuda_version": getattr(getattr(torch, "version", None), "cuda", None) if TORCH_AVAILABLE else None,
        "torch_version": getattr(torch, "__version__", None) if TORCH_AVAILABLE else None,
        "device_name": "",
        "onnx_available": False,
        "ort_available": False,
        "ort_providers": [],
        "ort_cuda_available": False,
        "ort_dml_available": False,
        "ort_trt_available": False,
        "ort_cpu_available": False,
        "error": "",
        "onnx_error": "",
        "ort_error": "",
    }

    try:
        import onnx  # noqa: F401
        result["onnx_available"] = True
    except Exception as exc:
        result["onnx_error"] = str(exc)

    try:
        import onnxruntime as ort  # type: ignore

        result["ort_available"] = True
        result["ort_providers"] = [str(item) for item in ort.get_available_providers()]
        result["ort_trt_available"] = "TensorrtExecutionProvider" in result["ort_providers"]
        result["ort_cuda_available"] = "CUDAExecutionProvider" in result["ort_providers"]
        result["ort_dml_available"] = "DmlExecutionProvider" in result["ort_providers"]
        result["ort_cpu_available"] = "CPUExecutionProvider" in result["ort_providers"]
    except Exception as exc:
        result["ort_error"] = str(exc)

    if not TORCH_AVAILABLE:
        return _normalize_runtime_probe_result(result, python_path=sys.executable)

    try:
        cuda_detected = bool(torch.cuda.is_available())
    except Exception as exc:
        result["error"] = str(exc)
        return _normalize_runtime_probe_result(result, python_path=sys.executable)

    result["cuda_detected"] = cuda_detected
    if not cuda_detected:
        return _normalize_runtime_probe_result(result, python_path=sys.executable)

    try:
        device = torch.device("cuda")
        result["device_name"] = str(torch.cuda.get_device_name(device))
        probe_tensor = torch.zeros((1,), device=device)
        torch.cuda.synchronize()
        del probe_tensor

        model = _build_model(model_name)
        model = model.to(device)
        torch.cuda.synchronize()
        del model
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        result["cuda_available"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return _normalize_runtime_probe_result(result, python_path=sys.executable)


def _probe_python_runtime(python_exe: str, model_name: str = "realesr-general-x4v3") -> Dict[str, object]:
    candidate = str(python_exe or "").strip()
    if not candidate or not os.path.isfile(candidate):
        return _normalize_runtime_probe_result({
            "torch_available": False,
            "cuda_detected": False,
            "cuda_available": False,
            "cuda_version": None,
            "torch_version": None,
            "device_name": "",
            "onnx_available": False,
            "ort_available": False,
            "ort_providers": [],
            "ort_trt_available": False,
            "error": "python 不存在",
        }, python_path=candidate)

    script_path = _get_realesrgan_script_path()
    cmd = [
        candidate,
        "-c",
        (
            "import importlib.util,json,sys; "
            f"spec=importlib.util.spec_from_file_location('realesrgan_runner_probe', {script_path!r}); "
            "mod=importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(mod); "
            f"result=mod._probe_current_runtime_backends({model_name!r}); "
            "print(json.dumps(result, ensure_ascii=False))"
        ),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env=_build_external_python_env(candidate),
            **_windows_hidden_subprocess_kwargs(),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "unknown error").strip())
        data = json.loads((result.stdout or "").strip() or "{}")
        return _normalize_runtime_probe_result(data, python_path=candidate)
    except Exception as exc:
        return _normalize_runtime_probe_result({
            "torch_available": False,
            "cuda_detected": False,
            "cuda_available": False,
            "cuda_version": None,
            "torch_version": None,
            "device_name": "",
            "onnx_available": False,
            "ort_available": False,
            "ort_providers": [],
            "ort_trt_available": False,
            "error": str(exc),
        }, python_path=candidate)


def _runtime_supports_onnx_backend(runtime_info: Optional[Dict[str, object]], backend_name: str, options: Optional[dict] = None) -> bool:
    runtime_info = runtime_info or {}
    options = options or {}
    if options.get("face_enhance"):
        return False
    model_name = str(options.get("model_name") or "realesr-general-x4v3").strip()
    has_export_capability = (
        bool(runtime_info.get("torch_available")) and bool(runtime_info.get("onnx_available"))
    ) or _has_cached_onnx_model(model_name, options)
    if not runtime_info.get("ort_available") or not has_export_capability:
        return False
    if backend_name == "ort_tensorrt":
        return bool(runtime_info.get("ort_trt_available"))
    if backend_name == "ort_cuda":
        return bool(runtime_info.get("ort_cuda_available"))
    if backend_name == "ort_directml":
        return bool(runtime_info.get("ort_dml_available"))
    if backend_name == "ort_cpu":
        return bool(runtime_info.get("ort_cpu_available"))
    return False


def _rank_video_backend(backend_name: str, gpu_vendors: List[str], options: Optional[dict] = None) -> int:
    options = options or {}
    backend_name = str(backend_name or "").strip().lower()
    if not backend_name:
        return -1
    if options.get("face_enhance"):
        face_scores = {
            "ort_tensorrt": 540,
            "torch_cuda": 500,
            "ncnn": 320,
            "cpu": 100,
        }
        return face_scores.get(backend_name, -1)

    if "nvidia" in gpu_vendors:
        scores = {
            "ort_tensorrt": 620,
            "ort_cuda": 560,
            "torch_cuda": 520,
            "ncnn": 420,
            "ort_directml": 180,
            "ort_cpu": 110,
            "cpu": 100,
        }
        return scores.get(backend_name, -1)
    if "amd" in gpu_vendors or "intel" in gpu_vendors:
        scores = {
            "ort_directml": 560,
            "ncnn": 460,
            "ort_tensorrt": 220,
            "ort_cuda": 200,
            "torch_cuda": 180,
            "ort_cpu": 110,
            "cpu": 100,
        }
        return scores.get(backend_name, -1)
    scores = {
        "ort_tensorrt": 460,
        "ort_cuda": 420,
        "torch_cuda": 400,
        "ncnn": 380,
        "ort_directml": 360,
        "ort_cpu": 120,
        "cpu": 100,
    }
    return scores.get(backend_name, -1)


def _get_runtime_candidate_video_backends(
    runtime_info: Optional[Dict[str, object]],
    *,
    gpu_vendors: Optional[List[str]] = None,
    options: Optional[dict] = None,
    ncnn_video_available: bool = False,
) -> List[str]:
    runtime_info = runtime_info or {}
    gpu_vendors = [str(item).strip().lower() for item in (gpu_vendors or []) if str(item).strip()]
    options = options or {}
    candidates: List[str] = []

    if "nvidia" in gpu_vendors:
        candidates.extend(["ort_tensorrt", "ort_cuda", "torch_cuda"])
    elif "amd" in gpu_vendors or "intel" in gpu_vendors:
        candidates.extend(["ort_directml", "ncnn", "torch_cuda"])
    else:
        candidates.extend(["ort_tensorrt", "ort_cuda", "torch_cuda", "ort_directml", "ncnn"])

    if not options.get("face_enhance") and runtime_info.get("ort_cpu_available"):
        candidates.append("ort_cpu")
    if ncnn_video_available:
        candidates.append("ncnn")
    if runtime_info.get("cuda_available"):
        candidates.append("torch_cuda")
    candidates.append("cpu")

    unique_candidates = _unique_preserve_order(candidates)
    supported: List[str] = []
    for backend_name in unique_candidates:
        if backend_name == "ncnn":
            if ncnn_video_available:
                supported.append(backend_name)
            continue
        if backend_name == "torch_cuda":
            if runtime_info.get("cuda_available"):
                supported.append(backend_name)
            continue
        if backend_name == "cpu":
            supported.append(backend_name)
            continue
        if backend_name.startswith("ort_") and _runtime_supports_onnx_backend(runtime_info, backend_name, options):
            supported.append(backend_name)
    supported.sort(key=lambda name: _rank_video_backend(name, gpu_vendors, options), reverse=True)
    return supported


def _get_runtime_candidate_image_backends(
    runtime_info: Optional[Dict[str, object]],
    *,
    gpu_vendors: Optional[List[str]] = None,
    options: Optional[dict] = None,
    ncnn_available: bool = False,
) -> List[str]:
    runtime_info = runtime_info or {}
    gpu_vendors = [str(item).strip().lower() for item in (gpu_vendors or []) if str(item).strip()]
    options = options or {}
    candidates: List[str] = []

    if "nvidia" in gpu_vendors:
        candidates.extend(["ort_tensorrt", "ort_cuda", "torch_cuda", "ncnn"])
    elif "amd" in gpu_vendors or "intel" in gpu_vendors:
        candidates.extend(["ort_directml", "ncnn", "ort_cpu"])
    else:
        candidates.extend(["ort_tensorrt", "ort_cuda", "torch_cuda", "ort_directml", "ncnn", "ort_cpu"])

    if runtime_info.get("cuda_available"):
        candidates.append("torch_cuda")
    if ncnn_available:
        candidates.append("ncnn")
    if runtime_info.get("ort_cpu_available"):
        candidates.append("ort_cpu")
    candidates.append("cpu")

    unique_candidates = _unique_preserve_order(candidates)
    supported: List[str] = []
    for backend_name in unique_candidates:
        if backend_name == "ncnn":
            if ncnn_available and can_use_realesrgan_ncnn(options):
                supported.append(backend_name)
            continue
        if backend_name == "torch_cuda":
            if runtime_info.get("cuda_available"):
                supported.append(backend_name)
            continue
        if backend_name == "cpu":
            supported.append(backend_name)
            continue
        if backend_name.startswith("ort_") and _runtime_supports_onnx_backend(runtime_info, backend_name, options):
            supported.append(backend_name)
    supported.sort(key=lambda name: _rank_video_backend(name, gpu_vendors, options), reverse=True)
    return supported


def get_runtime_capabilities(options: Optional[dict] = None) -> Dict[str, object]:
    options = options or {}
    model_name = str(options.get("model_name") or "realesr-general-x4v3").strip() or "realesr-general-x4v3"
    ncnn_candidate_options = _normalize_options_for_video_backend(options, "ncnn")
    current = _probe_current_runtime_backends(model_name)

    external_python = find_realesrgan_runtime_python()
    external = None
    if external_python:
        try:
            if os.path.abspath(external_python) == os.path.abspath(sys.executable):
                external = dict(current)
            else:
                external = _probe_python_runtime(external_python, model_name)
        except Exception:
            external = _probe_python_runtime(external_python, model_name)

    system_gpus = _probe_system_gpus()
    gpu_vendors = _unique_preserve_order([gpu.get("vendor_key", "") for gpu in system_gpus if gpu.get("vendor_key")])
    ncnn_available = bool(find_realesrgan_ncnn_executable())
    ncnn_video_available = can_use_realesrgan_ncnn(ncnn_candidate_options)
    gpu_detected = bool(system_gpus)

    current_backends = _get_runtime_candidate_video_backends(
        current,
        gpu_vendors=gpu_vendors,
        options=options,
        ncnn_video_available=ncnn_video_available,
    )
    current["preferred_video_backend"] = current_backends[0] if current_backends else "cpu"

    if external:
        external_backends = _get_runtime_candidate_video_backends(
            external,
            gpu_vendors=gpu_vendors,
            options=options,
            ncnn_video_available=ncnn_video_available,
        )
        external["preferred_video_backend"] = external_backends[0] if external_backends else "cpu"
    else:
        external_backends = []

    current_image_backends = _get_runtime_candidate_image_backends(
        current,
        gpu_vendors=gpu_vendors,
        options=options,
        ncnn_available=ncnn_available,
    )
    current["preferred_image_backend"] = current_image_backends[0] if current_image_backends else "cpu"

    if external:
        external_image_backends = _get_runtime_candidate_image_backends(
            external,
            gpu_vendors=gpu_vendors,
            options=options,
            ncnn_available=ncnn_available,
        )
        external["preferred_image_backend"] = external_image_backends[0] if external_image_backends else "cpu"
    else:
        external_image_backends = []

    preferred = current
    preferred_backends = current_backends
    current_score = _rank_video_backend(current["preferred_video_backend"], gpu_vendors, options)
    external_score = _rank_video_backend(
        str((external or {}).get("preferred_video_backend") or "cpu"),
        gpu_vendors,
        options,
    )
    current_missing = local_missing_dependencies(options)

    if external:
        if current_missing and not external.get("error"):
            preferred = external
            preferred_backends = external_backends
        elif external_score > current_score + 10:
            preferred = external
            preferred_backends = external_backends

    available_devices = ["cpu"]
    if gpu_detected or ncnn_available or bool(preferred_backends):
        available_devices.append("auto")
    if any(runtime.get("cuda_available") for runtime in (current, external or {}) if isinstance(runtime, dict)):
        available_devices.append("cuda")
    available_devices = _unique_preserve_order(available_devices)

    effective_backend = str(preferred.get("preferred_video_backend") or "cpu").strip().lower() or "cpu"
    return {
        "current": current,
        "external": external,
        "effective": preferred,
        "gpu_detected": gpu_detected,
        "system_gpus": system_gpus,
        "gpu_vendors": gpu_vendors,
        "cuda_detected": bool(preferred and preferred.get("cuda_detected")),
        "cuda_available": bool(preferred and preferred.get("cuda_available")),
        "available_devices": available_devices,
        "ncnn_available": ncnn_available,
        "ncnn_video_available": ncnn_video_available,
        "available_video_backends": _unique_preserve_order(current_backends + external_backends),
        "available_image_backends": _unique_preserve_order(current_image_backends + external_image_backends),
        "preferred_image_backend": str(preferred.get("preferred_image_backend") or "cpu").strip().lower() or "cpu",
        "preferred_gpu_backend": effective_backend,
    }


def local_missing_dependencies(options: Optional[dict] = None) -> List[str]:
    options = options or {}
    if _can_use_local_ncnn_runtime_only(options):
        return []
    status = get_dependency_status()
    missing = []
    for name in ("torch", "opencv", "basicsr", "realesrgan"):
        if not status.get(name):
            missing.append(name)
    if options.get("face_enhance"):
        for name in ("gfpgan", "facexlib"):
            if not status.get(name):
                missing.append(name)
    return missing


def missing_dependencies(options: Optional[dict] = None) -> List[str]:
    """
    面向 UI 的可用性判断：
    只要当前进程可运行，或者存在可接管的内置运行时，就视为可用。
    """
    options = options or {}
    missing = local_missing_dependencies(options)
    if not missing:
        return []
    if find_realesrgan_runtime_python():
        return []
    return missing


def can_run_with_current_or_external_runtime(options: Optional[dict] = None) -> bool:
    if not local_missing_dependencies(options):
        return True
    return bool(find_realesrgan_runtime_python())


def _apply_torchvision_compat_shim() -> None:
    """
    兼容新版 torchvision:
    basicsr 仍会导入 torchvision.transforms.functional_tensor，
    而新版本已迁移到 _functional_tensor。
    """
    try:
        import importlib

        if "torchvision.transforms.functional_tensor" in sys.modules:
            return

        shim = None
        for candidate in (
            "torchvision.transforms._functional_tensor",
            "torchvision.transforms.functional",
        ):
            try:
                shim = importlib.import_module(candidate)
                break
            except Exception:
                continue

        if shim is not None:
            sys.modules["torchvision.transforms.functional_tensor"] = shim
    except Exception:
        pass


def _build_model(model_name: str):
    _apply_torchvision_compat_shim()
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from realesrgan.archs.srvgg_arch import SRVGGNetCompact

    cfg = MODEL_CONFIGS[model_name]
    if cfg.kind == "rrdb_x4":
        return RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
    if cfg.kind == "rrdb_x2":
        return RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=2)
    if cfg.kind == "rrdb_anime_x4":
        return RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=6, num_grow_ch=32, scale=4)
    if cfg.kind == "srvgg_anime_x4":
        return SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=16, upscale=4, act_type="prelu")
    if cfg.kind == "srvgg_general_x4":
        return SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=32, upscale=4, act_type="prelu")
    raise RuntimeError(f"未知模型类型: {cfg.kind}")


def _resolve_model_paths(model_name: str, progress_cb=None) -> Tuple[object, Optional[List[float]]]:
    cfg = MODEL_CONFIGS[model_name]
    weight_dir = get_weights_dir()
    os.makedirs(weight_dir, exist_ok=True)
    
    primary = os.path.join(weight_dir, os.path.basename(cfg.urls[0]))
    if not os.path.isfile(primary):
        if progress_cb:
            progress_cb(f"🚀 模型文件缺失，准备自动从 GitHub 下载: {cfg.name}")
        _download_url_to_path(cfg.urls[0], primary, progress_cb=progress_cb)

    if model_name == "realesr-general-x4v3":
        secondary = os.path.join(weight_dir, os.path.basename(cfg.urls[1]))
        if not os.path.isfile(secondary):
            if progress_cb:
                progress_cb(f"🚀 DNI 模型文件缺失，准备自动从 GitHub 下载: {cfg.name} (secondary)")
            _download_url_to_path(cfg.urls[1], secondary, progress_cb=progress_cb)
        return [primary, secondary], None
    return primary, None


def _missing_torch_model_files(options: Optional[dict] = None) -> List[str]:
    options = options or {}
    model_name = options.get("model_name", "realesr-general-x4v3")
    cfg = MODEL_CONFIGS.get(model_name)
    if not cfg:
        return [model_name]
    missing = []
    for url in cfg.urls:
        candidate = os.path.join(get_weights_dir(), os.path.basename(url))
        if not os.path.isfile(candidate):
            missing.append(os.path.basename(candidate))
    if options.get("face_enhance"):
        gfpgan_file = os.path.join(get_weights_dir(), os.path.basename(GFPGAN_MODEL_URL))
        if not os.path.isfile(gfpgan_file):
            missing.append(os.path.basename(gfpgan_file))
        for url in FACEXLIB_MODEL_URLS:
            facexlib_file = os.path.join(get_gfpgan_weights_dir(), os.path.basename(url))
            if not os.path.isfile(facexlib_file):
                missing.append(os.path.basename(facexlib_file))
    return missing


def _missing_ncnn_model_files(options: Optional[dict] = None) -> List[str]:
    required_files = _get_required_ncnn_model_files(options)
    if not required_files:
        return []
    return [
        name for name in required_files
        if not os.path.isfile(os.path.join(get_ncnn_models_dir(), name))
    ]


def _needs_ncnn_download_assets(options: Optional[dict] = None, file_list: Optional[List[str]] = None) -> bool:
    options = dict(options or {})
    capability = get_runtime_capabilities(options)
    targets = [str(path).strip() for path in (file_list or []) if str(path).strip()]
    has_video = any(is_video_file(path) for path in targets)
    has_image = any(not is_video_file(path) for path in targets)

    if not targets:
        has_video = True
        has_image = True

    if has_image and _select_image_backend(options, capability) == "ncnn":
        return True
    if has_video and _select_video_backend_for_file(options, None, capability) == "ncnn":
        return True
    return False


def collect_missing_download_resources(
    options: Optional[dict] = None,
    file_list: Optional[List[str]] = None,
) -> List[str]:
    options = dict(options or {})
    missing: List[str] = []
    needs_ncnn = _needs_ncnn_download_assets(options, file_list)
    if needs_ncnn:
        ncnn_missing = _missing_ncnn_model_files(options)
        if ncnn_missing:
            # NCNN 模型缺失时自动降级检查 Torch 模型
            torch_missing = _missing_torch_model_files(options)
            if torch_missing:
                missing.extend(torch_missing)
    else:
        missing.extend(_missing_torch_model_files(options))
    return _unique_preserve_order(missing)


def missing_model_files(options: Optional[dict] = None) -> List[str]:
    return collect_missing_download_resources(options, None)


def _collect_required_model_urls(
    options: Optional[dict] = None,
    *,
    face_enhance: Optional[bool] = None,
    download_all: bool = False,
) -> List[str]:
    options = options or {}
    urls: List[str] = []

    if download_all:
        for cfg in MODEL_CONFIGS.values():
            urls.extend(cfg.urls)
        include_face = bool(face_enhance)
    else:
        model_name = str(options.get("model_name") or "realesr-general-x4v3").strip()
        cfg = MODEL_CONFIGS.get(model_name)
        if cfg:
            urls.extend(cfg.urls)
        include_face = bool(options.get("face_enhance")) if face_enhance is None else bool(face_enhance)

    if include_face:
        urls.append(GFPGAN_MODEL_URL)
        urls.extend(FACEXLIB_MODEL_URLS)
    return _unique_preserve_order(urls)


def _get_required_ncnn_model_files(options: Optional[dict] = None) -> List[str]:
    options = options or {}
    ncnn_model_name = _get_ncnn_model_name(options)
    if not ncnn_model_name:
        return []

    if ncnn_model_name == "realesr-animevideov3":
        scale = int(round(_safe_float(options.get("outscale", 4), 4.0)))
        scale = min(max(scale, 2), 4)
        return [
            f"{ncnn_model_name}-x{scale}.bin",
            f"{ncnn_model_name}-x{scale}.param",
        ]

    return [
        f"{ncnn_model_name}.bin",
        f"{ncnn_model_name}.param",
    ]


def _extract_ncnn_model_files(archive_path: str, filenames: List[str], progress_cb=None) -> List[str]:
    ensure_runtime_dirs()
    extracted: List[str] = []
    filename_set = {str(name or "").strip() for name in filenames if str(name or "").strip()}
    if not filename_set:
        return extracted

    with zipfile.ZipFile(archive_path, "r") as zf:
        entries = {os.path.basename(info.filename): info for info in zf.infolist() if not info.is_dir()}
        missing_in_archive = [name for name in filename_set if name not in entries]
        if missing_in_archive:
            raise RuntimeError("NCNN 模型压缩包缺少文件: " + ", ".join(sorted(missing_in_archive)))

        for name in filenames:
            entry = entries.get(name)
            if entry is None:
                continue
            target = os.path.join(get_ncnn_models_dir(), name)
            if os.path.isfile(target):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp_target = target + ".download"
            if os.path.isfile(tmp_target):
                try:
                    os.remove(tmp_target)
                except OSError:
                    pass
            if progress_cb:
                progress_cb(f"📦 解压 NCNN 模型: {name}")
            try:
                with zf.open(entry, "r") as src, open(tmp_target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(tmp_target, target)
            except Exception:
                try:
                    if os.path.isfile(tmp_target):
                        os.remove(tmp_target)
                except OSError:
                    pass
                raise
            extracted.append(target)
    return extracted


def ensure_ncnn_model_files(
    options: Optional[dict] = None,
    progress_cb=None,
    *,
    allow_download: Optional[bool] = None,
) -> List[str]:
    required_files = _get_required_ncnn_model_files(options)
    if not required_files:
        return []

    ensure_runtime_dirs()
    missing_files = _missing_ncnn_model_files(options)
    if not missing_files:
        return []
    
    # Real-ESRGAN v0.3.0 logic: Always try to download if missing to support model-less packaging
    if allow_download is None:
        allow_download = True # Default to True for automatic download

    archive_name = os.path.basename(NCNN_RUNTIME_ARCHIVE_URL)
    archive_path = os.path.join(_get_ncnn_runtime_cache_dir(), archive_name)
    if not os.path.isfile(archive_path):
        _download_url_to_path(NCNN_RUNTIME_ARCHIVE_URL, archive_path, progress_cb=progress_cb)
    elif progress_cb:
        progress_cb(f"📦 使用已缓存的 NCNN 模型包: {archive_name}")
    return _extract_ncnn_model_files(archive_path, missing_files, progress_cb=progress_cb)


def download_models(
    progress_cb=None,
    face_enhance: Optional[bool] = None,
    options: Optional[dict] = None,
    download_all: bool = False,
    file_list: Optional[List[str]] = None,
) -> List[str]:

    ensure_runtime_dirs()
    options = dict(options or {})
    if face_enhance is not None:
        options["face_enhance"] = bool(face_enhance)

    downloaded: List[str] = []
    if _needs_ncnn_download_assets(options, file_list):
        before_missing = set(_missing_ncnn_model_files(options))
        if before_missing:
            if progress_cb:
                progress_cb("检测到本次任务需要 NCNN/Vulkan 模型资源，准备下载模型包...")
            ensure_ncnn_model_files(options, progress_cb=progress_cb, allow_download=True)
            after_missing = set(_missing_ncnn_model_files(options))
            for name in before_missing:
                if name not in after_missing:
                    downloaded.append(os.path.join(get_ncnn_models_dir(), name))

    urls = _collect_required_model_urls(
        options=options,
        face_enhance=face_enhance,
        download_all=download_all,
    )

    # 快速检测网络连通性，如果服务器不可达则立即提示用户开启代理
    if urls and progress_cb:
        progress_cb("正在检测网络连接...")
    if urls and not _quick_connectivity_check(next(u for u in urls)):
        raise RuntimeError(
            "画质修复模型下载失败，无法连接到下载服务器。\n"
            "请开启代理（VPN）后重试。"
        )

    for url in urls:
        filename = os.path.basename(url)
        target = os.path.join(get_weights_dir(), filename)
        if os.path.isfile(target):
            continue
        _download_url_to_path(url, target, progress_cb=progress_cb)
        downloaded.append(target)
    return _unique_preserve_order(downloaded)


def _select_device(requested: str):
    if not TORCH_AVAILABLE:
        return None, False

    requested = (requested or "auto").strip().lower()
    cuda_available = bool(torch.cuda.is_available())
    if requested == "cpu":
        return "cpu", False
    if requested == "cuda":
        if not cuda_available:
            raise RuntimeError("当前环境没有可用 CUDA，但你选择了 GPU")
        return torch.device("cuda"), True
    if cuda_available:
        return torch.device("cuda"), True
    return "cpu", False


def _is_cuda_runtime_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return "cuda" in message or "cudnn" in message or "device-side assert" in message


def _is_gpu_backend_runtime_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    if _is_cuda_runtime_error(exc):
        return True
    gpu_error_markers = (
        "vulkan",
        "ncnn",
        "vk_error",
        "vk_error_device_lost",
        "device lost",
        "directml",
        "dml",
        "onnxruntime",
        "executionprovider",
        "gpu",
        "长时间无进展",
    )
    return any(marker in message for marker in gpu_error_markers)


def _build_cpu_fallback_options(options: dict) -> dict:
    fallback_options = dict(options or {})
    fallback_options["device"] = "cpu"
    fallback_options["fp32"] = True
    return fallback_options


def _create_upsampler_with_fallback(options: dict, progress_cb=None):
    requested = str(options.get("device") or "auto").strip().lower()
    try:
        return _create_upsampler(options), requested
    except Exception as exc:
        if requested == "cpu" or not _is_cuda_runtime_error(exc):
            raise
        if progress_cb:
            progress_cb(f"⚠️ CUDA 初始化失败，自动回退到 CPU: {exc}")
        fallback_options = _build_cpu_fallback_options(options)
        return _create_upsampler(fallback_options), "cpu"


def _run_one_media(
    input_path: str,
    output_path: str,
    options: dict,
    progress_cb=None,
    progress_percent_cb=None,
    stats_cb=None,
    cancel_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
):
    _check_cancel(cancel_cb)
    if is_video_file(input_path):
        enhance_video(
            input_path,
            output_path,
            options,
            progress_cb=progress_cb,
            progress_percent_cb=progress_percent_cb,
            stats_cb=stats_cb,
            cancel_cb=cancel_cb,
            base_progress=base_progress,
            progress_span=progress_span,
        )
        return
    enhance_single_image(
        input_path,
        output_path,
        options,
        progress_cb=progress_cb,
        progress_percent_cb=progress_percent_cb,
        cancel_cb=cancel_cb,
        base_progress=base_progress,
        progress_span=progress_span,
    )


def _run_one_media_with_cpu_retry(
    input_path: str,
    output_path: str,
    options: dict,
    progress_cb=None,
    progress_percent_cb=None,
    stats_cb=None,
    cancel_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
):
    active_options = dict(options or {})
    try:
        _run_one_media(
            input_path,
            output_path,
            active_options,
            progress_cb=progress_cb,
            progress_percent_cb=progress_percent_cb,
            stats_cb=stats_cb,
            cancel_cb=cancel_cb,
            base_progress=base_progress,
            progress_span=progress_span,
        )
        return active_options, False
    except Exception as exc:
        requested = str(active_options.get("device") or "auto").strip().lower()
        is_video = is_video_file(input_path)
        current_video_backend = str(active_options.get("video_backend") or "auto").strip().lower()

        if (
            is_video
            and requested != "cpu"
            and _is_gpu_backend_runtime_error(exc)
        ):
            capability = get_runtime_capabilities(active_options)
            failed_backend = _select_video_backend_for_file(active_options, None, capability)
            fallback_candidates: List[str] = []
            for backend_name in ("ort_tensorrt", "ort_cuda", "ort_directml", "ncnn", "torch_cuda", "ort_cpu"):
                if backend_name == failed_backend:
                    continue
                if backend_name == "ncnn":
                    if can_use_realesrgan_ncnn(_normalize_options_for_video_backend(active_options, "ncnn")):
                        fallback_candidates.append(backend_name)
                    continue
                if backend_name == "torch_cuda":
                    if _can_use_torch_cuda_video_backend(active_options):
                        fallback_candidates.append(backend_name)
                    continue
                if backend_name.startswith("ort_") and _can_use_onnx_video_backend(active_options, capability, backend_name):
                    fallback_candidates.append(backend_name)

            for fallback_backend in _unique_preserve_order(fallback_candidates):
                if progress_cb:
                    progress_cb(f"⚠️ 视频 GPU 后端 {failed_backend} 运行失败，当前文件切换到 {fallback_backend} 重试: {exc}")
                gpu_fallback_options = dict(active_options)
                if fallback_backend == "torch_cuda":
                    gpu_fallback_options["video_backend"] = "torch"
                    gpu_fallback_options["device"] = "cuda"
                    original_torch_model = str(gpu_fallback_options.get("_torch_fallback_model_name") or "").strip()
                    if original_torch_model in MODEL_CONFIGS:
                        gpu_fallback_options["model_name"] = original_torch_model
                elif fallback_backend == "ncnn":
                    gpu_fallback_options["video_backend"] = "ncnn"
                    gpu_fallback_options["device"] = "auto"
                elif fallback_backend == "ort_directml":
                    gpu_fallback_options["video_backend"] = "directml"
                    gpu_fallback_options["device"] = "auto"
                elif fallback_backend == "ort_tensorrt":
                    gpu_fallback_options["video_backend"] = "tensorrt"
                    gpu_fallback_options["device"] = "cuda"
                elif fallback_backend == "ort_cuda":
                    gpu_fallback_options["video_backend"] = "onnx"
                    gpu_fallback_options["device"] = "cuda"
                elif fallback_backend == "ort_cpu":
                    gpu_fallback_options["video_backend"] = "onnx"
                    gpu_fallback_options["device"] = "cpu"
                try:
                    _run_one_media(
                        input_path,
                        output_path,
                        gpu_fallback_options,
                        progress_cb=progress_cb,
                        progress_percent_cb=progress_percent_cb,
                        stats_cb=stats_cb,
                        cancel_cb=cancel_cb,
                        base_progress=base_progress,
                        progress_span=progress_span,
                    )
                    return gpu_fallback_options, False
                except Exception as fallback_exc:
                    exc = fallback_exc

        if requested == "cpu" or not _is_gpu_backend_runtime_error(exc):
            raise
        if progress_cb:
            progress_cb(f"⚠️ GPU 加速后端处理失败，当前文件改用 CPU 重试: {exc}")
        fallback_options = _build_cpu_fallback_options(active_options)
        _run_one_media(
            input_path,
            output_path,
            fallback_options,
            progress_cb=progress_cb,
            progress_percent_cb=progress_percent_cb,
            stats_cb=stats_cb,
            cancel_cb=cancel_cb,
            base_progress=base_progress,
            progress_span=progress_span,
        )
        return fallback_options, True


def _create_upsampler(options: dict):
    _apply_torchvision_compat_shim()
    from realesrgan import RealESRGANer

    model_name = options.get("model_name", "realesr-general-x4v3")
    cfg = MODEL_CONFIGS[model_name]
    model = _build_model(model_name)
    model_path, dni_weight = _resolve_model_paths(model_name, progress_cb=options.get("progress_cb"))

    if model_name == "realesr-general-x4v3":
        denoise_strength = _safe_float(options.get("denoise_strength", 0.5), 0.5)
        denoise_strength = min(1.0, max(0.0, denoise_strength))
        if denoise_strength != 1:
            dni_weight = [denoise_strength, 1 - denoise_strength]

    device, use_half = _select_device(options.get("device"))
    fp32 = bool(options.get("fp32", False))
    
    # Video/image auto mode should scale tile size with available GPU memory instead of
    # pinning all CUDA cards to the same conservative value.
    tile_size = _suggest_torch_tile_size(options)

    return RealESRGANer(
        scale=cfg.netscale,
        model_path=model_path,
        dni_weight=dni_weight,
        model=model,
        tile=tile_size,
        tile_pad=_safe_int(options.get("tile_pad", 10), 10),
        pre_pad=_safe_int(options.get("pre_pad", 0), 0),
        half=(use_half and not fp32),
        device=device,
    )


def _create_face_enhancer(options: dict, upsampler):
    if not options.get("face_enhance"):
        return None
    if "anime" in options.get("model_name", ""):
        return None
    _apply_torchvision_compat_shim()
    try:
        from gfpgan import GFPGANer
    except ImportError:
        return None

    _ensure_face_enhance_assets(progress_cb=options.get("progress_cb"))
    model_path = os.path.join(get_weights_dir(), os.path.basename(GFPGAN_MODEL_URL))
    return GFPGANer(
        model_path=model_path,
        upscale=_safe_float(options.get("outscale", 4), 4.0),
        arch="clean",
        channel_multiplier=2,
        bg_upsampler=upsampler,
    )


def _get_onnx_cache_dir() -> str:
    cache_dir = os.path.join(get_realesrgan_data_root(), "onnx")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _get_tensorrt_cache_dir(model_name: str, options: Optional[dict] = None) -> str:
    export_key = _build_onnx_export_key(model_name, options)
    width, height = _get_frame_shape_from_options(options)
    resolution_tag = f"{max(width, 0)}x{max(height, 0)}" if width > 0 and height > 0 else "dynamic"
    cache_dir = os.path.join(_get_onnx_cache_dir(), "trt_cache", export_key, resolution_tag)
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _extract_state_dict_for_export(state_obj) -> Dict[str, object]:
    if isinstance(state_obj, dict):
        for key in ("params_ema", "params", "state_dict"):
            payload = state_obj.get(key)
            if isinstance(payload, dict):
                return payload
    if isinstance(state_obj, dict):
        return state_obj
    raise RuntimeError("无法解析模型权重状态字典")


def _torch_load_export_weights(weight_path: str):
    load_kwargs = {"map_location": "cpu"}
    try:
        return torch.load(weight_path, weights_only=True, **load_kwargs)
    except TypeError:
        return torch.load(weight_path, **load_kwargs)


def _build_onnx_export_key(model_name: str, options: Optional[dict] = None) -> str:
    options = options or {}
    model_name = str(model_name or "realesr-general-x4v3").strip()
    if model_name == "realesr-general-x4v3":
        denoise_strength = _safe_float(options.get("denoise_strength", 0.5), 0.5)
        denoise_strength = min(1.0, max(0.0, denoise_strength))
        return f"{model_name}_denoise_{int(round(denoise_strength * 100)):03d}"
    return model_name


def _get_cached_onnx_model_path(model_name: str, options: Optional[dict] = None) -> str:
    export_key = _build_onnx_export_key(model_name, options)
    export_hash = hashlib.sha1(export_key.encode("utf-8")).hexdigest()[:12]
    return os.path.join(_get_onnx_cache_dir(), f"{export_key}_{export_hash}.onnx")


def _has_cached_onnx_model(model_name: str, options: Optional[dict] = None) -> bool:
    return os.path.isfile(_get_cached_onnx_model_path(model_name, options))


def _build_export_ready_model(model_name: str, options: Optional[dict] = None):
    options = options or {}
    if not TORCH_AVAILABLE:
        raise RuntimeError("当前运行时未安装 torch，无法导出 ONNX 模型")
    model = _build_model(model_name)
    model_path, _dni_weight = _resolve_model_paths(model_name)

    if isinstance(model_path, list):
        primary_path, secondary_path = model_path
        primary_state = _extract_state_dict_for_export(_torch_load_export_weights(primary_path))
        secondary_state = _extract_state_dict_for_export(_torch_load_export_weights(secondary_path))
        denoise_strength = _safe_float(options.get("denoise_strength", 0.5), 0.5)
        denoise_strength = min(1.0, max(0.0, denoise_strength))
        if denoise_strength >= 1.0 - 1e-6:
            merged_state = primary_state
        elif denoise_strength <= 1e-6:
            merged_state = secondary_state
        else:
            merged_state = {}
            for key in primary_state.keys():
                primary_tensor = primary_state[key].float()
                secondary_tensor = secondary_state[key].float()
                merged_state[key] = primary_tensor * denoise_strength + secondary_tensor * (1.0 - denoise_strength)
        model.load_state_dict(merged_state, strict=True)
    else:
        state_dict = _extract_state_dict_for_export(_torch_load_export_weights(model_path))
        model.load_state_dict(state_dict, strict=True)
    model.eval()
    return model


def _ensure_onnx_model(model_name: str, options: Optional[dict] = None, progress_cb=None) -> str:
    options = options or {}
    onnx_path = _get_cached_onnx_model_path(model_name, options)
    if os.path.isfile(onnx_path):
        return onnx_path
    with _ONNX_EXPORT_LOCK:
        if os.path.isfile(onnx_path):
            return onnx_path
        if progress_cb:
            progress_cb(f"首次使用 {model_name} 的 ONNX 推理，正在生成缓存模型...")
        model = _build_export_ready_model(model_name, options)
        dummy_input = torch.randn(1, 3, 64, 64, dtype=torch.float32)
        torch.onnx.export(
            model,
            dummy_input,
            onnx_path,
            input_names=["input"],
            output_names=["output"],
            dynamic_axes={
                "input": {0: "batch", 2: "height", 3: "width"},
                "output": {0: "batch", 2: "out_height", 3: "out_width"},
            },
            do_constant_folding=True,
            opset_version=17,
        )
    return onnx_path


def _suggest_onnx_batch_size(provider_name: str, width: int, height: int) -> int:
    provider_name = str(provider_name or "").strip()
    area = max(int(width), 1) * max(int(height), 1)
    if provider_name == "CUDAExecutionProvider":
        if area <= 1280 * 720:
            return 6
        if area <= 1920 * 1080:
            return 4
        if area <= 2560 * 1440:
            return 2
        return 1
    if provider_name == "DmlExecutionProvider":
        if area <= 640 * 480:
            return 4
        if area <= 1280 * 720:
            return 3
        if area <= 1920 * 1080:
            return 2
        return 1
    if provider_name == "TensorrtExecutionProvider":
        if area <= 1280 * 720:
            return 8
        if area <= 1920 * 1080:
            return 6
        if area <= 2560 * 1440:
            return 3
        return 1
    return 1


def _get_onnx_batch_candidates(provider_name: str, width: int, height: int) -> List[int]:
    base_batch = _suggest_onnx_batch_size(provider_name, width, height)
    if provider_name == "TensorrtExecutionProvider":
        candidates = [base_batch, max(base_batch - 2, 1), max(base_batch // 2, 1), 1]
    elif provider_name == "CUDAExecutionProvider":
        candidates = [base_batch, max(base_batch - 1, 1), max(base_batch // 2, 1), 1]
    elif provider_name == "DmlExecutionProvider":
        candidates = [base_batch, max(base_batch - 1, 1), 1]
    else:
        candidates = [1]
    normalized = _unique_preserve_order([str(max(1, int(item))) for item in candidates])
    return [max(1, _safe_int(item, 1)) for item in normalized]


def _get_cached_onnx_batch_size(
    options: Optional[dict],
    width: int,
    height: int,
    provider_name: str,
    media_kind: str = "video",
) -> int:
    cache_key = _build_onnx_perf_key(options, width, height, provider_name, media_kind)
    cached = _get_runtime_perf_bucket("onnx_batch").get(cache_key)
    if isinstance(cached, dict):
        return max(1, _safe_int(cached.get("batch_size", 0), 0))
    return 0


def _build_tensorrt_provider_options(
    options: Optional[dict],
    input_name: str = "input",
    media_kind: str = "video",
) -> Dict[str, str]:
    options = options or {}
    width, height = _get_frame_shape_from_options(options)
    provider_options = {
        "trt_engine_cache_enable": "1",
        "trt_engine_cache_path": _get_tensorrt_cache_dir(str(options.get("model_name") or "realesr-general-x4v3"), options),
        "trt_fp16_enable": "0" if options.get("fp32") else "1",
        "trt_timing_cache_enable": "1",
    }
    if width > 0 and height > 0:
        cached_batch = _get_cached_onnx_batch_size(options, width, height, "TensorrtExecutionProvider", media_kind)
        opt_batch = cached_batch if cached_batch > 0 else _suggest_onnx_batch_size("TensorrtExecutionProvider", width, height)
        max_batch = max(_get_onnx_batch_candidates("TensorrtExecutionProvider", width, height))
        shape_template = f"{input_name}:{{batch}}x3x{height}x{width}"
        provider_options["trt_profile_min_shapes"] = shape_template.format(batch=1)
        provider_options["trt_profile_opt_shapes"] = shape_template.format(batch=max(1, opt_batch))
        provider_options["trt_profile_max_shapes"] = shape_template.format(batch=max(1, max_batch))
    return provider_options


class _OnnxVideoUpsampler:
    def __init__(self, options: dict, provider_name: str, progress_cb=None):
        if not CV2_AVAILABLE:
            raise RuntimeError("ONNX 视频后端需要 OpenCV")
        try:
            import onnxruntime as ort  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"当前运行时未安装 onnxruntime: {exc}") from exc

        self.options = dict(options or {})
        self.model_name = str(self.options.get("model_name") or "realesr-general-x4v3").strip()
        self.cfg = MODEL_CONFIGS[self.model_name]
        self.provider_name = provider_name
        self.media_kind = str(self.options.get("_media_kind") or "video").strip().lower() or "video"
        self.outscale = _safe_float(self.options.get("outscale", self.cfg.netscale), float(self.cfg.netscale))
        self.onnx_path = _ensure_onnx_model(self.model_name, self.options, progress_cb=progress_cb)
        provider_candidates = [provider_name]
        provider_options = [{}]
        if provider_name == "TensorrtExecutionProvider":
            provider_options[0] = _build_tensorrt_provider_options(self.options, input_name="input", media_kind=self.media_kind)
            provider_options[0]["trt_context_memory_sharing_enable"] = "1"
            provider_candidates.append("CUDAExecutionProvider")
            provider_options.append({})
        if provider_name != "CPUExecutionProvider":
            provider_candidates.append("CPUExecutionProvider")
            provider_options.append({})
        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            self.onnx_path,
            sess_options=session_options,
            providers=provider_candidates,
            provider_options=provider_options,
        )
        self.active_provider = str((self.session.get_providers() or ["CPUExecutionProvider"])[0])
        self.input_name = self.session.get_inputs()[0].name

    def enhance_batch(self, frames: List[np.ndarray]) -> List[np.ndarray]:
        if not frames:
            return []
        batch_rgb = []
        source_height, source_width = frames[0].shape[:2]
        for frame_bgr in frames:
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            batch_rgb.append(np.transpose(rgb, (2, 0, 1)))
        inputs = np.stack(batch_rgb, axis=0).astype(np.float32, copy=False)
        outputs = self.session.run(None, {self.input_name: inputs})[0]
        restored_frames: List[np.ndarray] = []
        target_width = max(1, int(round(source_width * self.outscale)))
        target_height = max(1, int(round(source_height * self.outscale)))
        for output in outputs:
            rgb = np.transpose(output, (1, 2, 0))
            rgb = np.clip(rgb, 0.0, 1.0)
            bgr = cv2.cvtColor((rgb * 255.0).round().astype(np.uint8), cv2.COLOR_RGB2BGR)
            if abs(self.outscale - float(self.cfg.netscale)) > 1e-6:
                bgr = cv2.resize(bgr, (target_width, target_height), interpolation=cv2.INTER_LANCZOS4)
            restored_frames.append(bgr)
        return restored_frames

    def enhance_image(self, image_bgr: np.ndarray) -> np.ndarray:
        outputs = self.enhance_batch([image_bgr])
        if not outputs:
            raise RuntimeError("ONNX 图片增强失败")
        return outputs[0]


def _autotune_onnx_batch_size(
    upsampler: _OnnxVideoUpsampler,
    sample_frame: np.ndarray,
    *,
    options: Optional[dict],
    width: int,
    height: int,
    media_kind: str = "video",
    progress_cb=None,
) -> int:
    provider_name = str(upsampler.active_provider or upsampler.provider_name or "CPUExecutionProvider").strip()
    if provider_name == "CPUExecutionProvider":
        return 1

    cached_batch = _get_cached_onnx_batch_size(options, width, height, provider_name, media_kind)
    if cached_batch > 0:
        return cached_batch

    candidates = _get_onnx_batch_candidates(provider_name, width, height)
    if len(candidates) <= 1:
        return max(candidates[0], 1) if candidates else 1

    if progress_cb:
        progress_cb(f"首次使用 {provider_name}，正在自动测速以匹配更合适的批大小...")

    best_batch = candidates[0]
    best_fps = 0.0
    sample_frame = np.ascontiguousarray(sample_frame)
    for batch_size in candidates:
        frames = [sample_frame] * batch_size
        try:
            upsampler.enhance_batch(frames)
            started_at = time.monotonic()
            upsampler.enhance_batch(frames)
            elapsed = max(time.monotonic() - started_at, 1e-6)
            observed_fps = batch_size / elapsed
        except Exception:
            continue
        if observed_fps >= best_fps:
            best_fps = observed_fps
            best_batch = batch_size

    cache_key = _build_onnx_perf_key(options, width, height, provider_name, media_kind)
    _update_runtime_perf_bucket("onnx_batch", cache_key, {
        "batch_size": best_batch,
        "provider": provider_name,
        "fps": round(best_fps, 3),
        "autotuned": True,
    })
    if progress_cb:
        progress_cb(f"{provider_name} 自动测速完成，已选择批大小 {best_batch}。")
    return best_batch


def _get_onnx_provider_name(selected_backend: str) -> str:
    """将 selected_backend 名称映射为 ONNX Runtime provider 名称。"""
    backend = str(selected_backend or "").strip().lower()
    if backend == "ort_tensorrt":
        return "TensorrtExecutionProvider"
    if backend == "ort_cuda":
        return "CUDAExecutionProvider"
    if backend == "ort_directml":
        return "DmlExecutionProvider"
    return "CPUExecutionProvider"


def _enhance_single_image_with_onnx(
    image,
    options: dict,
    selected_backend: str,
    progress_cb=None,
):
    provider_name = _get_onnx_provider_name(selected_backend)
    height, width = image.shape[:2]
    onnx_options = dict(options)
    onnx_options["_frame_shape"] = (width, height)
    onnx_options["_media_kind"] = "image"
    upsampler = _OnnxVideoUpsampler(onnx_options, provider_name, progress_cb=progress_cb)
    if progress_cb:
        progress_cb(f"图片已切换到 {upsampler.active_provider} 推理后端。")
    return upsampler.enhance_image(image)


def _enhance_single_image_with_ncnn(
    image,
    output_path: str,
    options: dict,
    progress_cb=None,
    progress_percent_cb=None,
    cancel_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
):
    temp_root = os.path.join(
        os.path.dirname(os.path.abspath(output_path)),
        ".realesrgan_tmp",
        f"{os.path.splitext(os.path.basename(output_path))[0]}_image_ncnn",
    )
    frame_in_dir = os.path.join(temp_root, "input_frames")
    frame_out_dir = os.path.join(temp_root, "output_frames")
    _reset_directory(frame_in_dir)
    _reset_directory(frame_out_dir)
    try:
        input_frame = os.path.join(frame_in_dir, "frame_00000001.png")
        output_frame = os.path.join(frame_out_dir, "frame_00000001.png")
        if not _write_image_unicode_safe(input_frame, image):
            raise RuntimeError("无法写入 NCNN 图片临时输入")
        ncnn_options = dict(options)
        height, width = image.shape[:2]
        ncnn_options["_frame_shape"] = (width, height)
        _enhance_frames_with_ncnn(
            frame_in_dir,
            frame_out_dir,
            ncnn_options,
            progress_cb=progress_cb,
            progress_percent_cb=progress_percent_cb,
            base_progress=base_progress,
            progress_span=progress_span,
            cancel_cb=cancel_cb,
        )
        output = _read_image_unicode_safe(output_frame)
        if output is None:
            raise RuntimeError("NCNN 图片输出读取失败")
        return output
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _infer_output_ext(input_path: str, options: dict) -> str:
    ext_mode = str(options.get("ext", "auto")).strip().lower()
    if ext_mode in ("jpg", "png"):
        return "." + ext_mode
    return os.path.splitext(input_path)[1] or ".png"


def _read_image_unicode_safe(path: str):
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_UNCHANGED)


def _write_image_unicode_safe(path: str, image) -> bool:
    ext = os.path.splitext(path)[1] or ".png"
    ok, encoded = cv2.imencode(ext, image)
    if not ok:
        return False
    encoded.tofile(path)
    return True


def enhance_single_image(
    input_path: str,
    output_path: str,
    options: dict,
    progress_cb=None,
    progress_percent_cb=None,
    cancel_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
):
    ensure_runtime_dirs()
    _check_cancel(cancel_cb)
    if progress_percent_cb:
        progress_percent_cb(base_progress)
    started_at = time.monotonic()

    image = _read_image_unicode_safe(input_path)
    if image is None:
        raise RuntimeError("无法读取图片")
    capability = get_runtime_capabilities(options)
    selected_backend = _select_image_backend(options, capability)
    active_options = _normalize_options_for_image_backend(options, selected_backend, progress_cb=progress_cb)
    if image.ndim >= 2:
        active_options["_frame_shape"] = (int(image.shape[1]), int(image.shape[0]))
    active_options["_media_kind"] = "image"

    image_channels = 1 if image.ndim < 3 else int(image.shape[2])
    if image_channels != 3 and selected_backend in ("ort_tensorrt", "ort_cuda", "ort_directml", "ort_cpu", "ncnn"):
        if progress_cb:
            progress_cb("检测到当前图片包含透明通道或非常规通道布局，已自动回退到 PyTorch/CPU 路径以保证结果兼容。")
        selected_backend = "torch_cuda" if _can_use_torch_cuda_video_backend(active_options) else "cpu"
        active_options = dict(active_options)
        active_options["device"] = "cuda" if selected_backend == "torch_cuda" else "cpu"

    if selected_backend == "torch_cuda" and progress_cb:
        progress_cb("图片已切换到 PyTorch CUDA 推理后端。")
    elif selected_backend == "ncnn" and progress_cb:
        progress_cb("图片已切换到 NCNN/Vulkan 推理后端。")
    elif selected_backend == "cpu" and progress_cb:
        progress_cb("图片已切换到原生 PyTorch CPU 推理（画质优先）。")

    upsampler = None
    face_enhancer = None
    if selected_backend not in ("ort_cuda", "ort_directml", "ort_cpu", "ncnn"):
        upsampler, _ = _create_upsampler_with_fallback(active_options, progress_cb=progress_cb)
        face_enhancer = _create_face_enhancer(active_options, upsampler)
    if progress_percent_cb:
        progress_percent_cb(base_progress + progress_span * 0.15)

    _check_cancel(cancel_cb)
    outscale = _safe_float(active_options.get("outscale", 4), 4.0)
    if face_enhancer is not None:
        _, _, output = face_enhancer.enhance(
            image,
            has_aligned=False,
            only_center_face=False,
            paste_back=True,
        )
    elif selected_backend in ("ort_tensorrt", "ort_cuda", "ort_directml", "ort_cpu"):
        output = _enhance_single_image_with_onnx(
            image,
            active_options,
            selected_backend,
            progress_cb=progress_cb,
        )
    elif selected_backend == "ncnn":
        output = _enhance_single_image_with_ncnn(
            image,
            output_path,
            active_options,
            progress_cb=progress_cb,
            progress_percent_cb=progress_percent_cb,
            cancel_cb=cancel_cb,
            base_progress=base_progress + progress_span * 0.15,
            progress_span=progress_span * 0.75,
        )
    else:
        alpha_upsampler = str(active_options.get("alpha_upsampler", "realesrgan")).strip().lower()
        output, _ = upsampler.enhance(image, outscale=outscale, alpha_upsampler=alpha_upsampler)
    if progress_percent_cb:
        progress_percent_cb(base_progress + progress_span * 0.9)

    _check_cancel(cancel_cb)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if not _write_image_unicode_safe(output_path, output):
        raise RuntimeError("输出文件写入失败")
    elapsed_seconds = max(time.monotonic() - started_at, 1e-6)
    width, height = _get_frame_shape_from_options(active_options)
    pixel_count = max(width * height, 1)
    megapixels_per_second = (pixel_count / 1_000_000.0) / elapsed_seconds
    _record_backend_perf(
        active_options,
        width,
        height,
        "image",
        selected_backend,
        megapixels_per_second,
        extra={
            "unit": "megapixels_per_second",
            "elapsed_seconds": round(elapsed_seconds, 4),
        },
    )
    if progress_percent_cb:
        progress_percent_cb(base_progress + progress_span)


def _probe_video(video_path: str) -> Dict[str, str]:
    ffprobe_bin = _find_ffprobe_binary()
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,duration",
        "-of", "default=noprint_wrappers=1",
        video_path,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        **_windows_hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe 执行失败")

    meta = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            meta[key.strip()] = value.strip()
    return meta


def _parse_video_fps(meta: Dict[str, str], fps_override=None) -> float:
    if fps_override:
        return _safe_float(fps_override, 24.0)

    for key in ("avg_frame_rate", "r_frame_rate"):
        rate = str(meta.get(key) or "").strip()
        if not rate:
            continue
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_value = max(_safe_float(den, 1.0), 1e-6)
            return _safe_float(num, 24.0) / den_value
        parsed = _safe_float(rate, 0.0)
        if parsed > 0:
            return parsed
    return 24.0


def _estimate_video_frame_count(meta: Dict[str, str], fps: float) -> int:
    nb_frames = str(meta.get("nb_frames") or "").strip()
    if nb_frames.isdigit():
        return max(int(nb_frames), 0)

    duration = _safe_float(meta.get("duration", 0.0), 0.0)
    if duration > 0 and fps > 0:
        return max(int(round(duration * fps)), 0)
    return 0


def _read_exact_bytes(stream, size: int) -> bytes:
    chunks = []
    remaining = max(int(size), 0)
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _normalize_tile_size(tile_size: int, minimum: int = 128, maximum: int = 2048) -> int:
    tile_size = max(int(tile_size), minimum)
    tile_size = min(tile_size, maximum)
    return max(32, int(round(tile_size / 32.0)) * 32)


def _get_frame_shape_from_options(options: Optional[dict]) -> Tuple[int, int]:
    options = options or {}
    for key in ("_frame_shape", "_source_size"):
        shape = options.get(key)
        if isinstance(shape, (list, tuple)) and len(shape) >= 2:
            width = _safe_int(shape[0], 0)
            height = _safe_int(shape[1], 0)
            if width > 0 and height > 0:
                return width, height
    return 0, 0


def _suggest_torch_tile_size(options: Optional[dict] = None) -> int:
    options = options or {}
    requested_tile = _safe_int(options.get("tile", 0), 0)
    if requested_tile > 0:
        return requested_tile

    width, height = _get_frame_shape_from_options(options)
    model_name = str(options.get("model_name") or "realesr-general-x4v3").strip()
    profile_key = _build_video_perf_profile_key(options, width, height)
    cached = _get_runtime_perf_bucket("torch_tiles").get(profile_key)
    if isinstance(cached, dict):
        cached_tile = _safe_int(cached.get("tile", 0), 0)
        if cached_tile > 0:
            return cached_tile

    if not TORCH_AVAILABLE or not torch.cuda.is_available():
        return 400

    try:
        free_mem_bytes, total_mem_bytes = torch.cuda.mem_get_info()
        free_mem_gb = max(float(free_mem_bytes) / (1024 ** 3), 0.0)
        total_mem_gb = max(float(total_mem_bytes) / (1024 ** 3), free_mem_gb)
    except Exception:
        free_mem_gb = 6.0
        total_mem_gb = free_mem_gb

    area = width * height
    if area <= 0:
        area = 1280 * 720

    if total_mem_gb >= 14:
        tile_size = 1600 if area <= 1280 * 720 else 1280 if area <= 1920 * 1080 else 960
    elif total_mem_gb >= 10:
        tile_size = 1280 if area <= 1280 * 720 else 1024 if area <= 1920 * 1080 else 768
    elif total_mem_gb >= 8:
        tile_size = 1024 if area <= 1280 * 720 else 896 if area <= 1920 * 1080 else 640
    elif total_mem_gb >= 6:
        tile_size = 896 if area <= 1280 * 720 else 768 if area <= 1920 * 1080 else 576
    else:
        tile_size = 640 if area <= 1280 * 720 else 512 if area <= 1920 * 1080 else 400

    if model_name in ("realesr-general-x4v3", "realesr-animevideov3"):
        tile_size = int(tile_size * 1.15)
    if options.get("fp32"):
        tile_size = int(tile_size * 0.7)
    if free_mem_gb < 4.0:
        tile_size = int(tile_size * 0.75)
    return _normalize_tile_size(tile_size)


def _choose_ncnn_threads(width: int = 0, height: int = 0) -> str:
    cpu_count = os.cpu_count() or 8
    area = max(int(width), 0) * max(int(height), 0)
    if cpu_count >= 24:
        return "2:6:4" if area <= 1920 * 1080 else "2:5:3"
    if cpu_count >= 16:
        return "2:5:3" if area <= 1920 * 1080 else "2:4:2"
    if cpu_count >= 8:
        return "2:4:2" if area <= 1280 * 720 else "1:3:2"
    return "1:2:2"


def _choose_ncnn_thread_candidates(options: Optional[dict] = None, width: int = 0, height: int = 0) -> List[str]:
    profile_key = _build_video_perf_profile_key(options or {}, width, height)
    cached = _get_runtime_perf_bucket("ncnn_threads").get(profile_key)
    candidates: List[str] = []
    if isinstance(cached, dict):
        cached_thread = str(cached.get("thread", "") or "").strip()
        if cached_thread:
            candidates.append(cached_thread)
    candidates.extend([
        _choose_ncnn_threads(width, height),
        "2:4:2",
        "1:4:2",
        "1:3:2",
        "1:2:2",
        "1:1:1",
    ])
    return _unique_preserve_order(candidates)


def _choose_ncnn_gpu_candidates() -> List[str]:
    capability = get_runtime_capabilities({"device": "auto", "video_backend": "auto", "model_name": "realesr-animevideov3", "outscale": 2})
    gpu_count = len(capability.get("system_gpus") or [])
    # Provide generous list of indices based on wmi gpu_count, to cover discrete + integrated
    # We do not default back to "auto" easily here as it causes fallback to slow Intel GPUs
    candidates = [str(index) for index in range(max(gpu_count, 1) + 1)]
    return _unique_preserve_order(candidates)


def _probe_ncnn_gpu_id(exe_path: str, model_dir: str, model_name: str, scale: int) -> str:
    cache_key = "|".join([os.path.abspath(exe_path or ""), os.path.abspath(model_dir or ""), str(model_name), str(scale)])
    cached = _NCNN_GPU_SELECTION_CACHE.get(cache_key)
    if cached:
        return cached
    persisted = _get_runtime_perf_bucket("ncnn_gpu").get(cache_key)
    if isinstance(persisted, dict):
        persisted_gpu = str(persisted.get("gpu_id", "") or "").strip()
        if persisted_gpu:
            _NCNN_GPU_SELECTION_CACHE[cache_key] = persisted_gpu
            return persisted_gpu
    if not CV2_AVAILABLE:
        _NCNN_GPU_SELECTION_CACHE[cache_key] = "auto"
        return "auto"

    probe_root = os.path.join(get_realesrgan_data_root(), ".ncnn_gpu_probe")
    probe_in = os.path.join(probe_root, "in")
    probe_out = os.path.join(probe_root, "out")
    shutil.rmtree(probe_root, ignore_errors=True)
    os.makedirs(probe_in, exist_ok=True)
    os.makedirs(probe_out, exist_ok=True)
    try:
        # User a 128x128 image to create a measurable benchmarking difference between Intel and Nvidia GPUs
        probe_image = np.ones((128, 128, 3), dtype=np.uint8) * 127
        probe_input_path = os.path.join(probe_in, "probe.png")
        if not cv2.imwrite(probe_input_path, probe_image):
            raise RuntimeError("无法写入 NCNN GPU 探测测试图")

        best_gpu_id = "auto"
        best_time = float("inf")
        valid_gpus = []

        for gpu_id in _choose_ncnn_gpu_candidates():
            shutil.rmtree(probe_out, ignore_errors=True)
            os.makedirs(probe_out, exist_ok=True)
            cmd = [
                exe_path,
                "-i", probe_input_path,
                "-o", os.path.join(probe_out, "probe.png"),
                "-n", model_name,
                "-s", str(scale),
                "-t", "0",
                "-j", "1:1:1",
                "-f", "png",
                "-m", model_dir,
                "-g", gpu_id,
            ]
            try:
                start_time = time.monotonic()
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    **_windows_hidden_subprocess_kwargs(),
                )
                elapsed = time.monotonic() - start_time
            except Exception:
                continue
            
            if result.returncode == 0 and os.path.isfile(os.path.join(probe_out, "probe.png")):
                valid_gpus.append((gpu_id, elapsed))
                if elapsed < best_time:
                    best_time = elapsed
                    best_gpu_id = gpu_id
                    
        if valid_gpus:
            _NCNN_GPU_SELECTION_CACHE[cache_key] = best_gpu_id
            _update_runtime_perf_bucket("ncnn_gpu", cache_key, {
                "gpu_id": best_gpu_id,
                "elapsed_seconds": round(best_time, 4),
            })
            return best_gpu_id

        _NCNN_GPU_SELECTION_CACHE[cache_key] = "auto"
        return "auto"
    finally:
        shutil.rmtree(probe_root, ignore_errors=True)


def _get_ncnn_model_name(options: dict) -> str:
    model_name = str(options.get("model_name") or "").strip()
    return NCNN_MODEL_NAME_MAP.get(model_name, "")


def can_use_realesrgan_ncnn(options: Optional[dict] = None) -> bool:
    options = options or {}
    backend = str(options.get("video_backend") or "auto").strip().lower()
    if backend in ("torch", "cuda", "pytorch"):
        return False
    if not find_realesrgan_ncnn_executable():
        return False
    if not _get_ncnn_model_name(options):
        return False
    if options.get("face_enhance"):
        return False
    requested_device = str(options.get("device") or "auto").strip().lower()
    if requested_device == "cpu":
        return False
    outscale = _safe_float(options.get("outscale", 4), 4.0)
    if abs(outscale - round(outscale)) > 1e-6:
        return False
    return int(round(outscale)) in (2, 3, 4)


def _can_use_torch_cuda_video_backend(options: Optional[dict] = None) -> bool:
    options = options or {}
    requested_device = str(options.get("device") or "auto").strip().lower()
    if requested_device == "cpu":
        return False
    if local_missing_dependencies(options):
        return False
    capability = get_runtime_capabilities(options)
    return bool(capability.get("cuda_available"))


def _can_use_torch_cpu_video_backend(options: Optional[dict] = None) -> bool:
    """检查是否可以使用原生 PyTorch CPU 推理（不需要 CUDA）。"""
    options = options or {}
    if _can_use_local_ncnn_runtime_only(options):
        return False
    status = get_dependency_status()
    return all(status.get(name) for name in ("torch", "opencv", "basicsr", "realesrgan"))


def _normalize_options_for_ncnn_backend(options: dict, progress_cb=None, media_label: str = "视频") -> dict:
    effective = dict(options or {})
    if not find_realesrgan_ncnn_executable():
        return effective
    if effective.get("face_enhance"):
        return effective
    requested_device = str(effective.get("device") or "auto").strip().lower()
    if requested_device == "cpu":
        return effective
    outscale = _safe_float(effective.get("outscale", 4), 4.0)
    if abs(outscale - round(outscale)) > 1e-6 or int(round(outscale)) not in (2, 3, 4):
        return effective

    current_model = str(effective.get("model_name") or "").strip()
    if current_model == "realesr-general-x4v3":
        effective["_torch_fallback_model_name"] = current_model
        effective["model_name"] = "RealESRGAN_x4plus"
        if progress_cb:
            progress_cb(f"当前{media_label}模型不支持官方 NCNN/Vulkan，已自动切换到官方支持的 RealESRGAN_x4plus。")
    return effective


def _normalize_options_for_video_backend(options: dict, backend_name: str, progress_cb=None) -> dict:
    effective = dict(options or {})
    backend_name = str(backend_name or "auto").strip().lower()
    if backend_name != "ncnn":
        return effective
    return _normalize_options_for_ncnn_backend(effective, progress_cb=progress_cb, media_label="视频")


def _normalize_options_for_image_backend(options: dict, backend_name: str, progress_cb=None) -> dict:
    effective = dict(options or {})
    backend_name = str(backend_name or "auto").strip().lower()
    if backend_name != "ncnn":
        return effective
    model_name = str(effective.get("model_name") or "").strip()
    if model_name == "realesr-general-x4v3":
        effective["_avoid_ncnn_for_image"] = True
        if progress_cb:
            progress_cb("当前图片任务保持通用模型原样处理，不再为 NCNN/Vulkan 自动改用 RealESRGAN_x4plus。")
        return effective
    return _normalize_options_for_ncnn_backend(effective, progress_cb=progress_cb, media_label="图片")


def _can_use_onnx_video_backend(
    options: Optional[dict] = None,
    capability: Optional[Dict[str, object]] = None,
    backend_name: str = "",
) -> bool:
    options = options or {}
    capability = capability or get_runtime_capabilities(options)
    runtime_info = capability.get("effective") or capability.get("current") or {}
    return _runtime_supports_onnx_backend(runtime_info, backend_name, options)


def _select_image_backend(
    options: Optional[dict],
    capability: Optional[Dict[str, object]] = None,
) -> str:
    options = dict(options or {})
    capability = capability or get_runtime_capabilities(options)
    normalized_ncnn_options = _normalize_options_for_image_backend(options, "ncnn")
    backend = str(options.get("image_backend") or options.get("backend") or "auto").strip().lower()
    width, height = _get_frame_shape_from_options(options)

    if backend == "ncnn":
        if not normalized_ncnn_options.get("_avoid_ncnn_for_image") and can_use_realesrgan_ncnn(normalized_ncnn_options):
            return "ncnn"
    if backend in ("tensorrt", "trt", "ort_tensorrt"):
        if _can_use_onnx_video_backend(options, capability, "ort_tensorrt"):
            return "ort_tensorrt"
    if backend in ("torch", "cuda", "pytorch"):
        if _can_use_torch_cuda_video_backend(options):
            return "torch_cuda"
    if backend in ("onnx", "ort", "onnxruntime", "ort_cuda"):
        if _can_use_onnx_video_backend(options, capability, "ort_cuda"):
            return "ort_cuda"
    if backend in ("directml", "dml", "ort_directml"):
        if _can_use_onnx_video_backend(options, capability, "ort_directml"):
            return "ort_directml"
    if backend in ("ort_cpu", "onnx_cpu"):
        if _can_use_onnx_video_backend(options, capability, "ort_cpu"):
            return "ort_cpu"

    measured_backend = _select_measured_backend(
        [str(item).strip().lower() for item in (capability.get("available_image_backends") or []) if str(item).strip()],
        options,
        width,
        height,
        "image",
    )
    if measured_backend.startswith("ort_") and _can_use_onnx_video_backend(options, capability, measured_backend):
        return measured_backend
    if measured_backend == "ncnn" and not normalized_ncnn_options.get("_avoid_ncnn_for_image") and can_use_realesrgan_ncnn(normalized_ncnn_options):
        return "ncnn"
    if measured_backend == "torch_cuda" and _can_use_torch_cuda_video_backend(options):
        return "torch_cuda"
    if measured_backend == "cpu":
        return "cpu"

    preferred_backend = str(capability.get("preferred_image_backend") or "cpu").strip().lower()
    if preferred_backend.startswith("ort_") and _can_use_onnx_video_backend(options, capability, preferred_backend):
        return preferred_backend
    if preferred_backend == "ncnn" and not normalized_ncnn_options.get("_avoid_ncnn_for_image") and can_use_realesrgan_ncnn(normalized_ncnn_options):
        return "ncnn"
    if preferred_backend == "torch_cuda" and _can_use_torch_cuda_video_backend(options):
        return "torch_cuda"
    if _can_use_onnx_video_backend(options, capability, "ort_tensorrt"):
        return "ort_tensorrt"
    if _can_use_onnx_video_backend(options, capability, "ort_cuda"):
        return "ort_cuda"
    # 原生 PyTorch CPU 推理优先于 ONNX/DirectML，因为精度更准确
    if _can_use_torch_cpu_video_backend(options):
        return "cpu"
    if not normalized_ncnn_options.get("_avoid_ncnn_for_image") and can_use_realesrgan_ncnn(normalized_ncnn_options):
        return "ncnn"
    if _can_use_torch_cuda_video_backend(options):
        return "torch_cuda"
    if _can_use_onnx_video_backend(options, capability, "ort_directml"):
        return "ort_directml"
    if _can_use_onnx_video_backend(options, capability, "ort_cpu"):
        return "ort_cpu"
    return "cpu"


def _ffmpeg_supports_encoder(encoder_name: str) -> bool:
    encoder_name = str(encoder_name or "").strip().lower()
    if not encoder_name:
        return False
    if encoder_name in _FFMPEG_ENCODER_CACHE:
        return _FFMPEG_ENCODER_CACHE[encoder_name]

    ffmpeg_bin = _find_ffmpeg_binary()
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
            **_windows_hidden_subprocess_kwargs(),
        )
        supported = result.returncode == 0 and encoder_name in (result.stdout or "").lower()
    except Exception:
        supported = False
    _FFMPEG_ENCODER_CACHE[encoder_name] = supported
    return supported


def _get_encoder_specific_args(encoder_name: str) -> List[str]:
    encoder_name = str(encoder_name or "").strip().lower()
    if encoder_name == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "23"]
    if encoder_name == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "speed", "-usage", "transcoding", "-pix_fmt", "nv12"]
    if encoder_name == "h264_qsv":
        return ["-vf", "format=nv12", "-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "23"]
    if encoder_name == "h264_mf":
        return ["-vf", "format=nv12", "-c:v", "h264_mf", "-b:v", "4M"]
    return []


def _ffmpeg_encoder_runtime_usable(encoder_name: str) -> bool:
    encoder_name = str(encoder_name or "").strip().lower()
    if not encoder_name:
        return False
    if encoder_name in _FFMPEG_RUNTIME_ENCODER_CACHE:
        return _FFMPEG_RUNTIME_ENCODER_CACHE[encoder_name]
    if not _ffmpeg_supports_encoder(encoder_name):
        _FFMPEG_RUNTIME_ENCODER_CACHE[encoder_name] = False
        return False

    ffmpeg_bin = _find_ffmpeg_binary()
    try:
        result = subprocess.run(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel", "error",
                "-f", "lavfi",
                "-i", "color=c=black:s=128x128:d=1",
                "-frames:v", "1",
                "-an",
                *_get_encoder_specific_args(encoder_name),
                "-f", "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            **_windows_hidden_subprocess_kwargs(),
        )
        usable = result.returncode == 0
    except Exception:
        usable = False
    _FFMPEG_RUNTIME_ENCODER_CACHE[encoder_name] = usable
    return usable


def _cpu_video_encoder_args() -> List[str]:
    cpu_threads = max(1, min(os.cpu_count() or 8, 16))
    return [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-threads", str(cpu_threads),
    ]


def _select_video_encoder(options: Optional[dict] = None) -> Tuple[List[str], str]:
    options = options or {}
    requested_device = str(options.get("device") or "auto").strip().lower()
    if requested_device == "cpu":
        return _cpu_video_encoder_args(), "libx264"

    capability = get_runtime_capabilities(options)
    system_gpus = capability.get("system_gpus") or []
    vendors = _unique_preserve_order([str(item.get("vendor_key") or "").strip().lower() for item in system_gpus])

    encoder_candidates: List[Tuple[str, List[str]]] = []
    if "nvidia" in vendors:
        encoder_candidates.append(("h264_nvenc", _get_encoder_specific_args("h264_nvenc")))
    if "amd" in vendors:
        encoder_candidates.append(("h264_amf", _get_encoder_specific_args("h264_amf")))
        encoder_candidates.append(("h264_mf", _get_encoder_specific_args("h264_mf")))
    if "intel" in vendors:
        encoder_candidates.append(("h264_qsv", _get_encoder_specific_args("h264_qsv")))
        encoder_candidates.append(("h264_mf", _get_encoder_specific_args("h264_mf")))
    if bool(capability.get("cuda_available")) and "nvidia" not in vendors:
        encoder_candidates.insert(0, ("h264_nvenc", _get_encoder_specific_args("h264_nvenc")))

    # Append all known hardware encoders as fallbacks before libx264 in case WMI failed to detect the GPU.
    # _ffmpeg_encoder_runtime_usable gracefully verifies them in ffmpeg, so this is completely safe and robust.
    for hw_enc in ("h264_nvenc", "h264_amf", "h264_qsv", "h264_mf"):
        if not any(enc_name == hw_enc for enc_name, _ in encoder_candidates):
            encoder_candidates.append((hw_enc, _get_encoder_specific_args(hw_enc)))

    encoder_candidates.append(("libx264", _cpu_video_encoder_args()))

    for encoder_name, encoder_args in encoder_candidates:
        if encoder_name == "libx264" or _ffmpeg_encoder_runtime_usable(encoder_name):
            return encoder_args, encoder_name
    return _cpu_video_encoder_args(), "libx264"


def get_realesrgan_execution_plan(options: Optional[dict] = None) -> Dict[str, object]:
    options = dict(options or {})
    capability = get_runtime_capabilities(options)
    selected_video_backend = _select_video_backend_for_file(options, None, capability)
    normalized_options = _normalize_options_for_video_backend(options, selected_video_backend)

    _encoder_args, encoder_name = _select_video_encoder(options)
    return {
        "capability": capability,
        "normalized_options": normalized_options,
        "selected_video_backend": selected_video_backend,
        "selected_encoder": encoder_name,
        "system_gpus": capability.get("system_gpus") or [],
        "gpu_vendors": capability.get("gpu_vendors") or [],
        "preferred_gpu_backend": capability.get("preferred_gpu_backend") or "cpu",
    }


def _select_video_backend_for_file(
    options: Optional[dict],
    meta: Optional[Dict[str, str]] = None,
    capability: Optional[Dict[str, object]] = None,
) -> str:
    options = dict(options or {})
    capability = capability or get_runtime_capabilities(options)
    backend = str(options.get("video_backend") or "auto").strip().lower()
    width = _safe_int((meta or {}).get("width", 0), 0)
    height = _safe_int((meta or {}).get("height", 0), 0)
    perf_key = _build_video_perf_profile_key(options, width, height)
    cached = _get_runtime_perf_bucket("video_backend").get(perf_key)

    if backend in ("torch", "cuda", "pytorch"):
        return "torch_cuda"
    if backend == "ncnn":
        return "ncnn"
    if backend in ("tensorrt", "trt", "ort_tensorrt"):
        if _can_use_onnx_video_backend(options, capability, "ort_tensorrt"):
            return "ort_tensorrt"
    if backend in ("onnx", "ort", "onnxruntime", "ort_cuda"):
        if _can_use_onnx_video_backend(options, capability, "ort_cuda"):
            return "ort_cuda"
    if backend in ("directml", "dml", "ort_directml"):
        if _can_use_onnx_video_backend(options, capability, "ort_directml"):
            return "ort_directml"

    measured_backend = _select_measured_backend(
        [str(item).strip().lower() for item in (capability.get("available_video_backends") or []) if str(item).strip()],
        options,
        width,
        height,
        "video",
    )
    if measured_backend.startswith("ort_") and _can_use_onnx_video_backend(options, capability, measured_backend):
        return measured_backend
    if measured_backend == "ncnn" and can_use_realesrgan_ncnn(_normalize_options_for_video_backend(options, "ncnn")):
        return "ncnn"
    if measured_backend == "torch_cuda" and _can_use_torch_cuda_video_backend(options):
        return "torch_cuda"
    if measured_backend == "cpu":
        return "cpu"

    if isinstance(cached, dict):
        cached_backend = str(cached.get("backend", "") or "").strip().lower()
        if cached_backend == "ort_tensorrt" and _can_use_onnx_video_backend(options, capability, "ort_tensorrt"):
            return "ort_tensorrt"
        if cached_backend == "torch_stream" and _can_use_torch_cuda_video_backend(options):
            return "torch_cuda"
        if cached_backend == "ort_cuda" and _can_use_onnx_video_backend(options, capability, "ort_cuda"):
            return "ort_cuda"
        if cached_backend == "ort_directml" and _can_use_onnx_video_backend(options, capability, "ort_directml"):
            return "ort_directml"
        if cached_backend == "ort_cpu" and _can_use_onnx_video_backend(options, capability, "ort_cpu"):
            return "ort_cpu"
        if cached_backend == "ncnn_chunked" and can_use_realesrgan_ncnn(options):
            return "ncnn"
        if cached_backend == "ncnn_legacy" and can_use_realesrgan_ncnn(_normalize_options_for_video_backend(options, "ncnn")):
            return "ncnn"

    preferred_backend = str(capability.get("preferred_gpu_backend") or "cpu").strip().lower()
    if preferred_backend in ("ort_tensorrt", "ort_cuda", "ort_directml", "ort_cpu", "torch_cuda", "ncnn"):
        if preferred_backend.startswith("ort_") and _can_use_onnx_video_backend(options, capability, preferred_backend):
            return preferred_backend
        if preferred_backend == "torch_cuda" and _can_use_torch_cuda_video_backend(options):
            return "torch_cuda"
        if preferred_backend == "ncnn" and can_use_realesrgan_ncnn(_normalize_options_for_video_backend(options, "ncnn")):
            return "ncnn"

    if _can_use_onnx_video_backend(options, capability, "ort_tensorrt"):
        return "ort_tensorrt"
    if _can_use_onnx_video_backend(options, capability, "ort_cuda"):
        return "ort_cuda"
    if _can_use_onnx_video_backend(options, capability, "ort_directml"):
        return "ort_directml"
    if can_use_realesrgan_ncnn(_normalize_options_for_video_backend(options, "ncnn")):
        return "ncnn"
    if _can_use_torch_cuda_video_backend(options):
        return "torch_cuda"
    if _can_use_onnx_video_backend(options, capability, "ort_cpu"):
        return "ort_cpu"
    if local_missing_dependencies(options):
        return "external_runtime"
    return "cpu"


def _should_use_chunked_ncnn_backend(options: Optional[dict], meta: Optional[Dict[str, str]] = None) -> bool:
    options = options or {}
    pipeline = str(options.get("ncnn_pipeline") or "auto").strip().lower()
    if pipeline == "chunked":
        return True
    if pipeline in ("legacy", "extract", "compat"):
        return False

    width = _safe_int((meta or {}).get("width", 0), 0)
    height = _safe_int((meta or {}).get("height", 0), 0)
    area = width * height
    if area >= 2560 * 1440:
        return True

    perf_key = _build_video_perf_profile_key(options, width, height)
    cached = _get_runtime_perf_bucket("video_backend").get(perf_key)
    if isinstance(cached, dict):
        cached_backend = str(cached.get("backend", "") or "").strip().lower()
        if cached_backend == "ncnn_chunked" and area >= 1920 * 1080:
            return True
    return False


def _emit_video_stats(
    *,
    started_at: float,
    processed_frames: int,
    total_frames: int,
    stats_cb=None,
) -> Tuple[float, Optional[float]]:
    elapsed = max(time.monotonic() - started_at, 1e-6)
    fps_value = processed_frames / elapsed if processed_frames > 0 else 0.0
    eta_seconds = max(total_frames - processed_frames, 0) / fps_value if fps_value > 0 and total_frames > 0 else None
    if callable(stats_cb):
        stats_cb({
            "kind": "video",
            "speed_fps": fps_value,
            "eta_seconds": eta_seconds,
            "processed_frames": processed_frames,
            "total_frames": total_frames,
        })
    return fps_value, eta_seconds


def _start_video_encoder_process(
    ffmpeg_bin: str,
    *,
    input_path: str,
    output_path: str,
    fps: float,
    out_width: int,
    out_height: int,
    options: dict,
    progress_cb=None,
) -> Tuple[subprocess.Popen, str]:
    video_encoder_args, encoder_name = _select_video_encoder(options)
    if progress_cb:
        progress_cb(
            f"开始使用 {'GPU' if str(options.get('device') or 'auto').strip().lower() != 'cpu' else 'CPU'} 流式后端处理视频帧..."
        )
        progress_cb(f"输出视频编码器: {encoder_name}")

    encode_cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{out_width}x{out_height}",
        "-r", str(fps),
        "-i", "-",
        "-i", input_path,
        "-map", "0:v:0",
        "-map", "1:a?",
        "-pix_fmt", "yuv420p",
        *video_encoder_args,
        "-c:a", "copy",
        output_path,
    ]
    process = subprocess.Popen(
        encode_cmd,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_windows_hidden_subprocess_kwargs(),
    )
    if process.stdin is None:
        raise RuntimeError("流式编码器未生成输入管道")
    return process, encoder_name


def _reset_directory(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)


def _choose_ncnn_chunk_size(width: int, height: int, options: Optional[dict] = None) -> int:
    options = options or {}
    area = max(int(width), 0) * max(int(height), 0)
    cpu_count = os.cpu_count() or 8
    if area <= 640 * 480:
        chunk_size = 96
    elif area <= 1280 * 720:
        chunk_size = 64
    elif area <= 1920 * 1080:
        chunk_size = 32
    elif area <= 2560 * 1440:
        chunk_size = 20
    else:
        chunk_size = 12
    if cpu_count >= 16:
        chunk_size = int(chunk_size * 1.5)
    elif cpu_count <= 6:
        chunk_size = max(8, int(chunk_size * 0.75))
    if str(options.get("model_name") or "").strip() == "realesr-animevideov3":
        chunk_size = int(chunk_size * 1.25)
    return max(8, min(chunk_size, 128))


def _enhance_video_with_extracted_ncnn_backend(
    input_path: str,
    output_path: str,
    options: dict,
    meta: Dict[str, str],
    progress_cb=None,
    progress_percent_cb=None,
    cancel_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
    stats_cb=None,
) -> None:
    ffmpeg_bin = _find_ffmpeg_binary()
    started_at = time.monotonic()
    temp_parent = os.path.join(os.path.dirname(os.path.abspath(output_path)), ".realesrgan_tmp")
    os.makedirs(temp_parent, exist_ok=True)
    temp_root = os.path.join(temp_parent, f"{os.path.splitext(os.path.basename(output_path))[0]}_work")
    frame_in_dir = os.path.join(temp_root, "input_frames")
    frame_out_dir = os.path.join(temp_root, "output_frames")
    audio_path = os.path.join(temp_root, "audio.aac")
    os.makedirs(frame_in_dir, exist_ok=True)
    os.makedirs(frame_out_dir, exist_ok=True)

    if progress_cb:
        progress_cb("提取视频帧...")
    extract_cmd = [ffmpeg_bin, "-y", "-hwaccel", "auto", "-i", input_path, os.path.join(frame_in_dir, "frame_%08d.bmp")]
    _run_command(extract_cmd, cancel_cb=cancel_cb, progress_cb=progress_cb)
    if progress_percent_cb:
        progress_percent_cb(base_progress + progress_span * 0.08)

    audio_cmd = [ffmpeg_bin, "-y", "-i", input_path, "-vn", "-acodec", "copy", audio_path]
    try:
        _run_command(audio_cmd, cancel_cb=cancel_cb, progress_cb=progress_cb)
    except RuntimeError:
        pass

    if progress_cb:
        progress_cb("开始使用兼容模式 NCNN/Vulkan 后端处理视频帧...")
    enhanced = _enhance_frames_with_ncnn(
        frame_in_dir,
        frame_out_dir,
        {**options, "_frame_shape": (int(meta.get("width", 0) or 0), int(meta.get("height", 0) or 0))},
        progress_cb=progress_cb,
        progress_percent_cb=progress_percent_cb,
        base_progress=base_progress,
        progress_span=progress_span,
        cancel_cb=cancel_cb,
        stats_cb=stats_cb,
        processed_frame_offset=0,
        expected_total_frames=_estimate_video_frame_count(meta, _parse_video_fps(meta, options.get("fps"))),
    )
    fps = _parse_video_fps(meta, options.get("fps"))
    if progress_cb:
        if enhanced:
            progress_cb(f"兼容模式 NCNN/Vulkan 本段线程 {enhanced.get('thread')}，平均 {float(enhanced.get('speed_fps') or 0.0):.2f} 帧/秒。")
        progress_cb("合成输出视频...")
    _check_cancel(cancel_cb)
    video_encoder_args, encoder_name = _select_video_encoder(options)
    if progress_cb:
        progress_cb(f"输出视频编码器: {encoder_name}")
    _run_command(
        [
            ffmpeg_bin,
            "-y",
            "-framerate", str(fps),
            "-i", os.path.join(frame_out_dir, "frame_%08d.png"),
            *([] if not (audio_path and os.path.isfile(audio_path)) else ["-i", audio_path, "-c:a", "copy"]),
            "-pix_fmt", "yuv420p",
            *video_encoder_args,
            output_path,
        ],
        cancel_cb=cancel_cb,
        progress_cb=progress_cb,
    )
    perf_key = _build_video_perf_profile_key(
        {**options, "_frame_shape": (int(meta.get("width", 0) or 0), int(meta.get("height", 0) or 0))},
        int(meta.get("width", 0) or 0),
        int(meta.get("height", 0) or 0),
    )
    total_frames = _estimate_video_frame_count(meta, fps)
    observed_fps = total_frames / max(time.monotonic() - started_at, 1e-6) if total_frames > 0 else 0.0
    _update_runtime_perf_bucket("video_backend", perf_key, {
        "backend": "ncnn_legacy",
        "chunk_size": 0,
        "fps": round(observed_fps, 3),
    })
    _record_backend_perf(
        {**options, "_frame_shape": (int(meta.get("width", 0) or 0), int(meta.get("height", 0) or 0))},
        int(meta.get("width", 0) or 0),
        int(meta.get("height", 0) or 0),
        "video",
        "ncnn",
        observed_fps,
        extra={"variant": "legacy"},
    )
    if progress_percent_cb:
        progress_percent_cb(base_progress + progress_span)


def _rebuild_video(frame_dir: str, audio_path: str, output_path: str, fps: float):
    ffmpeg_bin = _find_ffmpeg_binary()
    pattern = os.path.join(frame_dir, "frame_%08d.png")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-framerate", str(fps),
        "-i", pattern,
    ]
    if audio_path and os.path.isfile(audio_path):
        cmd.extend(["-i", audio_path, "-c:a", "copy"])
    cmd.extend(["-pix_fmt", "yuv420p", *_cpu_video_encoder_args(), output_path])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        **_windows_hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "视频封装失败")


def _enhance_video_with_chunked_ncnn_backend(
    input_path: str,
    output_path: str,
    options: dict,
    meta: Dict[str, str],
    progress_cb=None,
    progress_percent_cb=None,
    cancel_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
    stats_cb=None,
) -> None:
    ffmpeg_bin = _find_ffmpeg_binary()
    width = _safe_int(meta.get("width", 0), 0)
    height = _safe_int(meta.get("height", 0), 0)
    if width <= 0 or height <= 0:
        raise RuntimeError("无法获取视频尺寸，不能启动 NCNN 分块处理")

    fps = _parse_video_fps(meta, options.get("fps"))
    total_frames = _estimate_video_frame_count(meta, fps)
    outscale = int(round(_safe_float(options.get("outscale", 4), 4.0)))
    chunk_size = _choose_ncnn_chunk_size(width, height, options)
    perf_key = _build_video_perf_profile_key(options, width, height)
    cached_backend = _get_runtime_perf_bucket("video_backend").get(perf_key)
    if isinstance(cached_backend, dict):
        cached_chunk_size = _safe_int(cached_backend.get("chunk_size", 0), 0)
        if cached_chunk_size > 0:
            chunk_size = max(8, min(cached_chunk_size, 160))

    if progress_cb:
        progress_cb(f"启用 NCNN 分块流水后端，分块大小 {chunk_size} 帧。")
    if progress_percent_cb:
        progress_percent_cb(base_progress + progress_span * 0.04)

    decode_cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-hwaccel", "auto",
        "-i", input_path,
        "-vsync", "0",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-",
    ]
    decoder_process = subprocess.Popen(
        decode_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_windows_hidden_subprocess_kwargs(),
    )
    encoder_process = None
    decoder_stderr = b""
    encoder_stderr = b""
    frame_bytes = width * height * 3
    processed_frames = 0
    started_at = time.monotonic()
    temp_root = os.path.join(
        os.path.dirname(os.path.abspath(output_path)),
        ".realesrgan_tmp",
        f"{os.path.splitext(os.path.basename(output_path))[0]}_chunked",
    )
    frame_in_dir = os.path.join(temp_root, "input_frames")
    frame_out_dir = os.path.join(temp_root, "output_frames")
    _reset_directory(frame_in_dir)
    _reset_directory(frame_out_dir)

    try:
        if decoder_process.stdout is None:
            raise RuntimeError("NCNN 分块解码器未生成输出管道")

        encoder_process, _encoder_name = _start_video_encoder_process(
            ffmpeg_bin,
            input_path=input_path,
            output_path=output_path,
            fps=fps,
            out_width=width * outscale,
            out_height=height * outscale,
            options=options,
            progress_cb=progress_cb,
        )

        while True:
            _check_cancel(cancel_cb)
            _reset_directory(frame_in_dir)
            _reset_directory(frame_out_dir)
            chunk_frame_count = 0
            while chunk_frame_count < chunk_size:
                frame_buffer = _read_exact_bytes(decoder_process.stdout, frame_bytes)
                if not frame_buffer:
                    break
                if len(frame_buffer) != frame_bytes:
                    raise RuntimeError("NCNN 分块解码过程中收到不完整视频帧")
                frame = np.frombuffer(frame_buffer, dtype=np.uint8).reshape((height, width, 3))
                frame_path = os.path.join(frame_in_dir, f"frame_{chunk_frame_count + 1:08d}.bmp")
                if not _write_image_unicode_safe(frame_path, frame):
                    raise RuntimeError(f"写入 NCNN 分块输入帧失败: {frame_path}")
                chunk_frame_count += 1
            if chunk_frame_count <= 0:
                break

            chunk_options = dict(options)
            chunk_options["_frame_shape"] = (width, height)

            def _chunk_stats(payload):
                local_processed = int((payload or {}).get("processed_frames") or 0)
                if callable(stats_cb):
                    stats_cb({
                        "kind": "video",
                        "speed_fps": float((payload or {}).get("speed_fps") or 0.0),
                        "eta_seconds": (payload or {}).get("eta_seconds"),
                        "processed_frames": local_processed,
                        "total_frames": total_frames,
                    })

            enhanced = _enhance_frames_with_ncnn(
                frame_in_dir,
                frame_out_dir,
                chunk_options,
                progress_cb=progress_cb,
                progress_percent_cb=progress_percent_cb,
                base_progress=base_progress,
                progress_span=progress_span,
                cancel_cb=cancel_cb,
                stats_cb=_chunk_stats,
                processed_frame_offset=processed_frames,
                expected_total_frames=total_frames,
            )

            for frame_index in range(1, chunk_frame_count + 1):
                output_frame_path = os.path.join(frame_out_dir, f"frame_{frame_index:08d}.png")
                output = _read_image_unicode_safe(output_frame_path)
                if output is None:
                    raise RuntimeError(f"读取 NCNN 分块输出帧失败: {output_frame_path}")
                if not output.flags["C_CONTIGUOUS"]:
                    output = np.ascontiguousarray(output)
                if encoder_process.stdin is None:
                    raise RuntimeError("流式编码器未生成输入管道")
                encoder_process.stdin.write(output.tobytes())
                processed_frames += 1

            fps_value, _eta_seconds = _emit_video_stats(
                started_at=started_at,
                processed_frames=processed_frames,
                total_frames=total_frames,
                stats_cb=stats_cb,
            )
            if progress_percent_cb and total_frames > 0:
                ratio = min(processed_frames / max(total_frames, 1), 1.0)
                progress_percent_cb(base_progress + progress_span * (0.1 + ratio * 0.8))
            if progress_cb:
                progress_cb(
                    f"NCNN 分块完成 {processed_frames}/{total_frames or processed_frames} 帧，本段线程 {enhanced.get('thread')}，当前整体 {fps_value:.2f} 帧/秒。"
                )

        if encoder_process is None:
            raise RuntimeError("未能从视频中解码出任何帧")
        if encoder_process.stdin is not None:
            encoder_process.stdin.close()

        decoder_return_code = decoder_process.wait(timeout=30)
        if decoder_process.stderr is not None:
            decoder_stderr = decoder_process.stderr.read() or b""
        if decoder_return_code != 0:
            raise RuntimeError((decoder_stderr.decode("utf-8", errors="replace") or "FFmpeg 解码失败").strip())

        encoder_return_code = encoder_process.wait(timeout=120)
        if encoder_process.stderr is not None:
            encoder_stderr = encoder_process.stderr.read() or b""
        if encoder_return_code != 0:
            raise RuntimeError((encoder_stderr.decode("utf-8", errors="replace") or "FFmpeg 编码失败").strip())

        observed_fps = processed_frames / max(time.monotonic() - started_at, 1e-6)
        _update_runtime_perf_bucket("video_backend", perf_key, {
            "backend": "ncnn_chunked",
            "chunk_size": chunk_size,
            "fps": round(observed_fps, 3),
        })
        _record_backend_perf(
            {**options, "_frame_shape": (width, height)},
            width,
            height,
            "video",
            "ncnn",
            observed_fps,
            extra={"variant": "chunked", "chunk_size": chunk_size},
        )
        if progress_percent_cb:
            progress_percent_cb(base_progress + progress_span)
    except RealesrganCancelledError:
        _terminate_process_tree(decoder_process)
        _terminate_process_tree(encoder_process)
        raise
    except Exception:
        _terminate_process_tree(decoder_process)
        _terminate_process_tree(encoder_process)
        raise
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _enhance_frames_with_ncnn(
    frame_in_dir: str,
    frame_out_dir: str,
    options: dict,
    progress_cb=None,
    progress_percent_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
    cancel_cb=None,
    stats_cb=None,
    processed_frame_offset: int = 0,
    expected_total_frames: Optional[int] = None,
) -> Dict[str, object]:
    exe_path = find_realesrgan_ncnn_executable()
    if not exe_path:
        raise RuntimeError("未找到 realesrgan-ncnn-vulkan 可执行文件")

    model_name = _get_ncnn_model_name(options)
    if not model_name:
        raise RuntimeError("当前模型暂不支持 NCNN/Vulkan 加速")

    ensure_ncnn_model_files(options, progress_cb=progress_cb)
    model_dir = _find_realesrgan_ncnn_models_dir(exe_path)
    if not model_dir:
        raise RuntimeError("未找到 NCNN 模型目录")

    scale = int(round(_safe_float(options.get("outscale", 4), 4.0)))
    tile_size = _safe_int(options.get("tile", 0), 0)
    gpu_id = _probe_ncnn_gpu_id(exe_path, model_dir, model_name, scale)
    last_exc: Optional[Exception] = None
    width, height = _get_frame_shape_from_options(options)
    profile_key = _build_video_perf_profile_key(options, width, height)
    thread_candidates = _choose_ncnn_thread_candidates(options, width, height)
    for attempt_index, thread_arg in enumerate(thread_candidates, start=1):
        if os.path.isdir(frame_out_dir):
            shutil.rmtree(frame_out_dir, ignore_errors=True)
        os.makedirs(frame_out_dir, exist_ok=True)

        cmd = [
            exe_path,
            "-i", frame_in_dir,
            "-o", frame_out_dir,
            "-n", model_name,
            "-s", str(scale),
            "-t", str(tile_size),
            "-j", thread_arg,
            "-f", "png",
            "-v",
            "-m", model_dir,
            "-g", gpu_id,
        ]
        if progress_cb:
            progress_cb(
                f"使用高速 NCNN/Vulkan 视频后端: {os.path.basename(exe_path)} | 模型 {model_name} | GPU {gpu_id} | 线程 {thread_arg}"
            )
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_windows_hidden_subprocess_kwargs(),
        )
        expected_frames = len([name for name in os.listdir(frame_in_dir) if name.lower().endswith((".png", ".bmp"))])
        started_at = time.monotonic()
        last_output_count = len([name for name in os.listdir(frame_out_dir) if name.lower().endswith(".png")])
        last_progress_at = time.monotonic()
        last_progress_log_at = 0.0
        output_queue: "queue.Queue[str]" = queue.Queue()

        def _reader():
            try:
                if process.stdout is None:
                    return
                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    output_queue.put(line.rstrip())
            finally:
                output_queue.put("__NCNN_EOF__")

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()
        collected_lines = []
        try:
            while True:
                _check_cancel(cancel_cb)
                try:
                    line = output_queue.get(timeout=0.3)
                    if line == "__NCNN_EOF__":
                        if process.poll() is not None:
                            break
                        continue
                    if not line:
                        continue
                    collected_lines.append(line)
                    last_progress_at = time.monotonic()
                    percent_line = line.strip().rstrip("%")
                    if progress_percent_cb:
                        try:
                            ratio = float(percent_line) / 100.0
                            progress_percent_cb(base_progress + progress_span * (0.1 + ratio * 0.8))
                            continue
                        except Exception:
                            pass
                    if progress_cb and ("->" in line or "done" in line.lower()):
                        progress_cb(line)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    current_output_count = len([name for name in os.listdir(frame_out_dir) if name.lower().endswith(".png")])
                    now = time.monotonic()
                    if current_output_count > last_output_count:
                        last_output_count = current_output_count
                        last_progress_at = now
                        elapsed = max(now - started_at, 1e-6)
                        fps = current_output_count / elapsed
                        processed_frames = processed_frame_offset + current_output_count
                        total_frames = expected_total_frames or expected_frames
                        eta_seconds = max(total_frames - processed_frames, 0) / fps if fps > 0 and total_frames > 0 else None
                        if callable(stats_cb):
                            stats_cb({
                                "kind": "video",
                                "speed_fps": fps,
                                "eta_seconds": eta_seconds,
                                "processed_frames": processed_frames,
                                "total_frames": total_frames,
                            })
                        if progress_percent_cb and total_frames > 0:
                            ratio = min(processed_frames / total_frames, 1.0)
                            progress_percent_cb(base_progress + progress_span * (0.1 + ratio * 0.8))
                        if progress_cb and now - last_progress_log_at >= NCNN_PROGRESS_LOG_INTERVAL_SECONDS:
                            progress_cb(f"NCNN/Vulkan 已输出 {processed_frames}/{total_frames} 帧...")
                            last_progress_log_at = now
                    elif now - last_progress_at >= NCNN_STALL_TIMEOUT_SECONDS:
                        _terminate_process_tree(process)
                        raise RuntimeError(
                            f"NCNN/Vulkan 视频处理长时间无进展，已自动中止并准备回退（已输出 {last_output_count}/{expected_frames} 帧）"
                        )
                    continue
            return_code = process.wait(timeout=5)
            stdout_text = "\n".join(collected_lines).strip()
            if return_code != 0:
                error_text = "NCNN/Vulkan 执行失败"
                if stdout_text:
                    error_text += "\n" + stdout_text
                raise RuntimeError(error_text.strip())
            elapsed_seconds = max(time.monotonic() - started_at, 1e-6)
            observed_fps = expected_frames / elapsed_seconds if expected_frames > 0 else 0.0
            _update_runtime_perf_bucket("ncnn_threads", profile_key, {
                "thread": thread_arg,
                "gpu_id": gpu_id,
                "fps": round(observed_fps, 3),
            })
            if progress_percent_cb:
                progress_percent_cb(base_progress + progress_span * 0.9)
            return {
                "thread": thread_arg,
                "gpu_id": gpu_id,
                "processed_frames": expected_frames,
                "elapsed_seconds": round(elapsed_seconds, 4),
                "speed_fps": observed_fps,
            }
        except RealesrganCancelledError:
            if progress_cb:
                progress_cb("⚠️ 正在停止 NCNN/Vulkan 视频处理...")
            _terminate_process_tree(process)
            raise
        except Exception as exc:
            _terminate_process_tree(process)
            last_exc = exc
            has_more_attempts = attempt_index < len(thread_candidates)
            if has_more_attempts and _is_gpu_backend_runtime_error(exc):
                if progress_cb:
                    progress_cb(
                        f"⚠️ NCNN/Vulkan 当前线程配置 {thread_arg} 运行失败，自动切换到更稳的配置重试: {exc}"
                    )
                continue
            raise
    if last_exc is not None:
        raise last_exc


def _enhance_video_with_streaming_backend(
    input_path: str,
    output_path: str,
    options: dict,
    meta: Dict[str, str],
    progress_cb=None,
    progress_percent_cb=None,
    cancel_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
    stats_cb=None,
) -> None:
    ffmpeg_bin = _find_ffmpeg_binary()
    width = _safe_int(meta.get("width", 0), 0)
    height = _safe_int(meta.get("height", 0), 0)
    
    decode_cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-hwaccel", "auto",
        "-i", input_path,
        "-vsync", "0",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-",
    ]
    
    _enhance_video_with_streaming_backend_impl(
        input_path=input_path,
        output_path=output_path,
        options=options,
        meta=meta,
        decode_cmd=decode_cmd,
        progress_cb=progress_cb,
        progress_percent_cb=progress_percent_cb,
        cancel_cb=cancel_cb,
        base_progress=base_progress,
        progress_span=progress_span,
        stats_cb=stats_cb,
    )

def _enhance_video_with_streaming_backend_impl(
    input_path: str,
    output_path: str,
    options: dict,
    meta: Dict[str, str],
    decode_cmd: List[str],
    progress_cb=None,
    progress_percent_cb=None,
    cancel_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
    stats_cb=None,
) -> None:
    ffmpeg_bin = _find_ffmpeg_binary()
    width = _safe_int(meta.get("width", 0), 0)
    height = _safe_int(meta.get("height", 0), 0)
    if width <= 0 or height <= 0:
        raise RuntimeError("无法获取视频尺寸，不能启动流式处理")

    fps = _parse_video_fps(meta, options.get("fps"))
    total_frames = _estimate_video_frame_count(meta, fps)

    if progress_percent_cb:
        progress_percent_cb(base_progress + progress_span * 0.05)

    stream_options = dict(options)
    stream_options["_frame_shape"] = (width, height)
    upsampler, actual_device = _create_upsampler_with_fallback(stream_options, progress_cb=progress_cb)
    face_enhancer = _create_face_enhancer(options, upsampler)
    outscale = _safe_float(options.get("outscale", 4), 4.0)
    alpha_upsampler = str(options.get("alpha_upsampler", "realesrgan")).strip().lower()

    decoder_process = subprocess.Popen(
        decode_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_windows_hidden_subprocess_kwargs(),
    )

    encoder_process = None
    frame_bytes = width * height * 3
    processed_frames = 0
    started_at = time.monotonic()
    decoder_stderr = b""
    encoder_stderr = b""
    frame_queue: "queue.Queue[object]" = queue.Queue(maxsize=32)
    write_queue: "queue.Queue[object]" = queue.Queue(maxsize=16)
    reader_state = {"error": None}
    writer_state = {"error": None}

    # ---------- batch processing support ----------
    use_batch = (face_enhancer is None)  # face_enhancer 不支持 batch
    torch_batch_size = 1
    if use_batch and TORCH_AVAILABLE:
        try:
            free_mem = torch.cuda.mem_get_info()[0] / (1024 ** 3) if torch.cuda.is_available() else 0.0
        except Exception:
            free_mem = 0.0
        area = width * height
        if free_mem >= 12:
            torch_batch_size = 4 if area <= 1280 * 720 else 2 if area <= 1920 * 1080 else 1
        elif free_mem >= 8:
            torch_batch_size = 3 if area <= 1280 * 720 else 2 if area <= 1920 * 1080 else 1
        elif free_mem >= 5:
            torch_batch_size = 2 if area <= 1280 * 720 else 1
        else:
            torch_batch_size = 1
        if torch_batch_size <= 1:
            use_batch = False

    def _process_frame(frame_bgr):
        if face_enhancer is not None:
            _, _, output = face_enhancer.enhance(
                frame_bgr,
                has_aligned=False,
                only_center_face=False,
                paste_back=True,
            )
            return output
        output, _ = upsampler.enhance(frame_bgr, outscale=outscale, alpha_upsampler=alpha_upsampler)
        return output

    def _process_batch(frames_bgr):
        """Batch-process multiple BGR frames through the model in one forward pass."""
        if not frames_bgr:
            return []
        # Prepare batch tensor: (N, C, H, W)
        batch_imgs = []
        h_input, w_input = frames_bgr[0].shape[0:2]
        for frame in frames_bgr:
            img = frame.astype(np.float32) / 255.0
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            batch_imgs.append(img)

        batch_tensor = torch.from_numpy(
            np.stack([np.transpose(img, (2, 0, 1)) for img in batch_imgs], axis=0)
        ).float()
        if upsampler.half:
            batch_tensor = batch_tensor.half()
        batch_tensor = batch_tensor.to(upsampler.device)

        # Pre-pad
        if upsampler.pre_pad != 0:
            batch_tensor = F.pad(batch_tensor, (0, upsampler.pre_pad, 0, upsampler.pre_pad), 'reflect')
        # Mod pad
        mod_pad_h, mod_pad_w = 0, 0
        mod_scale = None
        if upsampler.scale == 2:
            mod_scale = 2
        elif upsampler.scale == 1:
            mod_scale = 4
        if mod_scale is not None:
            _, _, h, w = batch_tensor.size()
            if h % mod_scale != 0:
                mod_pad_h = mod_scale - h % mod_scale
            if w % mod_scale != 0:
                mod_pad_w = mod_scale - w % mod_scale
            if mod_pad_h > 0 or mod_pad_w > 0:
                batch_tensor = F.pad(batch_tensor, (0, mod_pad_w, 0, mod_pad_h), 'reflect')

        # Model inference — use tile or full
        with torch.no_grad():
            if upsampler.tile_size > 0:
                # Tile processing: process batch with tile logic
                _n, _c, _h, _w = batch_tensor.shape
                out_h = _h * upsampler.scale
                out_w = _w * upsampler.scale
                output_tensor = batch_tensor.new_zeros((_n, _c, out_h, out_w))
                tiles_x = math.ceil(_w / upsampler.tile_size)
                tiles_y = math.ceil(_h / upsampler.tile_size)
                for ty in range(tiles_y):
                    for tx in range(tiles_x):
                        ofs_x = tx * upsampler.tile_size
                        ofs_y = ty * upsampler.tile_size
                        in_sx = max(ofs_x - upsampler.tile_pad, 0)
                        in_ex = min(ofs_x + upsampler.tile_size + upsampler.tile_pad, _w)
                        in_sy = max(ofs_y - upsampler.tile_pad, 0)
                        in_ey = min(ofs_y + upsampler.tile_size + upsampler.tile_pad, _h)
                        input_tile = batch_tensor[:, :, in_sy:in_ey, in_sx:in_ex]
                        try:
                            output_tile = upsampler.model(input_tile)
                        except RuntimeError:
                            output_tile = batch_tensor.new_zeros((
                                _n, _c,
                                (in_ey - in_sy) * upsampler.scale,
                                (in_ex - in_sx) * upsampler.scale,
                            ))
                        in_tw = min(ofs_x + upsampler.tile_size, _w) - ofs_x
                        in_th = min(ofs_y + upsampler.tile_size, _h) - ofs_y
                        out_sx = ofs_x * upsampler.scale
                        out_ex = out_sx + in_tw * upsampler.scale
                        out_sy = ofs_y * upsampler.scale
                        out_ey = out_sy + in_th * upsampler.scale
                        tile_out_sx = (ofs_x - in_sx) * upsampler.scale
                        tile_out_ex = tile_out_sx + in_tw * upsampler.scale
                        tile_out_sy = (ofs_y - in_sy) * upsampler.scale
                        tile_out_ey = tile_out_sy + in_th * upsampler.scale
                        output_tensor[:, :, out_sy:out_ey, out_sx:out_ex] = \
                            output_tile[:, :, tile_out_sy:tile_out_ey, tile_out_sx:tile_out_ex]
            else:
                output_tensor = upsampler.model(batch_tensor)

        # Post-process: remove pads
        if mod_scale is not None and (mod_pad_h > 0 or mod_pad_w > 0):
            _, _, oh, ow = output_tensor.size()
            output_tensor = output_tensor[:, :, 0:oh - mod_pad_h * upsampler.scale,
                                          0:ow - mod_pad_w * upsampler.scale]
        if upsampler.pre_pad != 0:
            _, _, oh, ow = output_tensor.size()
            output_tensor = output_tensor[:, :, 0:oh - upsampler.pre_pad * upsampler.scale,
                                          0:ow - upsampler.pre_pad * upsampler.scale]

        # Convert back to numpy BGR frames
        results = []
        output_np = output_tensor.float().cpu().clamp_(0, 1).numpy()
        for i in range(output_np.shape[0]):
            frame_out = np.transpose(output_np[i][[2, 1, 0], :, :], (1, 2, 0))
            frame_out = (frame_out * 255.0).round().astype(np.uint8)
            if outscale is not None and abs(outscale - float(upsampler.scale)) > 1e-6:
                frame_out = cv2.resize(
                    frame_out,
                    (int(w_input * outscale), int(h_input * outscale)),
                    interpolation=cv2.INTER_LANCZOS4,
                )
            results.append(frame_out)
        return results

    def _reader():
        try:
            if decoder_process.stdout is None:
                frame_queue.put(("error", RuntimeError("流式解码器未生成输出管道")))
                return
            while True:
                frame_buffer = _read_exact_bytes(decoder_process.stdout, frame_bytes)
                if not frame_buffer:
                    break
                if len(frame_buffer) != frame_bytes:
                    frame_queue.put(("error", RuntimeError("流式解码过程中收到不完整视频帧")))
                    return
                frame = np.frombuffer(frame_buffer, dtype=np.uint8).reshape((height, width, 3))
                frame_queue.put(("frame", frame))
            frame_queue.put(("eof", None))
        except Exception as exc:
            reader_state["error"] = exc
            try:
                frame_queue.put(("error", exc))
            except Exception:
                pass

    def _writer():
        try:
            while True:
                item = write_queue.get()
                if item is None:
                    break
                enc_ref, output_frame = item
                if enc_ref.stdin is None:
                    writer_state["error"] = RuntimeError("流式编码器未生成输入管道")
                    break
                if not output_frame.flags["C_CONTIGUOUS"]:
                    output_frame = np.ascontiguousarray(output_frame)
                enc_ref.stdin.write(output_frame.tobytes())
        except Exception as exc:
            writer_state["error"] = exc

    def _ensure_encoder(output):
        nonlocal encoder_process
        if encoder_process is None:
            out_height, out_width = output.shape[:2]
            encoder_process, _encoder_name = _start_video_encoder_process(
                ffmpeg_bin,
                input_path=input_path,
                output_path=output_path,
                fps=fps,
                out_width=out_width,
                out_height=out_height,
                options=options,
                progress_cb=progress_cb,
            )

    def _flush_pending(pending):
        nonlocal processed_frames
        if not pending:
            return
        if use_batch and len(pending) > 1:
            try:
                outputs = _process_batch(pending)
            except Exception:
                # Fallback to single-frame if batch fails
                outputs = [_process_frame(f) for f in pending]
        else:
            outputs = [_process_frame(f) for f in pending]
        for output in outputs:
            if output is None:
                raise RuntimeError("视频帧增强失败")
            _ensure_encoder(output)
            write_queue.put((encoder_process, output))
            processed_frames += 1
        fps_value, eta_seconds = _emit_video_stats(
            started_at=started_at,
            processed_frames=processed_frames,
            total_frames=total_frames,
            stats_cb=stats_cb,
        )
        if progress_cb and (processed_frames <= torch_batch_size or processed_frames % 30 == 0):
            if total_frames > 0:
                progress_cb(f"流式增强视频帧 {processed_frames}/{total_frames}...")
            else:
                progress_cb(f"流式增强视频帧 {processed_frames}...")
        if progress_percent_cb and total_frames > 0:
            ratio = min(processed_frames / max(total_frames, 1), 1.0)
            progress_percent_cb(base_progress + progress_span * (0.1 + ratio * 0.8))

    try:
        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()
        writer_thread = threading.Thread(target=_writer, daemon=True)
        writer_thread.start()

        if progress_cb and use_batch and torch_batch_size > 1:
            progress_cb(f"已启用 PyTorch 批量推理模式，批大小 {torch_batch_size}")

        pending_frames: List[np.ndarray] = []
        while True:
            _check_cancel(cancel_cb)
            if writer_state["error"] is not None:
                raise writer_state["error"]
            item_kind, item_value = frame_queue.get(timeout=30)
            if item_kind == "eof":
                _flush_pending(pending_frames)
                pending_frames = []
                break
            if item_kind == "error":
                raise item_value
            pending_frames.append(item_value)
            if len(pending_frames) >= torch_batch_size:
                _flush_pending(pending_frames)
                pending_frames = []

        write_queue.put(None)
        writer_thread.join(timeout=120)
        if writer_state["error"] is not None:
            raise writer_state["error"]

        decoder_process.stdout.close()
        if encoder_process is None:
            raise RuntimeError("未能从视频中解码出任何帧")

        if encoder_process.stdin is not None:
            encoder_process.stdin.close()

        decoder_return_code = decoder_process.wait(timeout=30)
        if decoder_process.stderr is not None:
            decoder_stderr = decoder_process.stderr.read() or b""
        if decoder_return_code != 0:
            raise RuntimeError((decoder_stderr.decode("utf-8", errors="replace") or "FFmpeg 解码失败").strip())

        encoder_return_code = encoder_process.wait(timeout=120)
        if encoder_process.stderr is not None:
            encoder_stderr = encoder_process.stderr.read() or b""
        if encoder_return_code != 0:
            raise RuntimeError((encoder_stderr.decode("utf-8", errors="replace") or "FFmpeg 编码失败").strip())

        perf_key = _build_video_perf_profile_key(options, width, height)
        _update_runtime_perf_bucket("video_backend", perf_key, {
            "backend": "torch_stream",
            "tile": _suggest_torch_tile_size(stream_options),
            "device": str(actual_device),
            "fps": round(processed_frames / max(time.monotonic() - started_at, 1e-6), 3),
        })
        _record_backend_perf(
            stream_options,
            width,
            height,
            "video",
            "torch_cuda",
            processed_frames / max(time.monotonic() - started_at, 1e-6),
            extra={"device": str(actual_device)},
        )
        _update_runtime_perf_bucket("torch_tiles", perf_key, {
            "tile": _suggest_torch_tile_size(stream_options),
        })
        if progress_percent_cb:
            progress_percent_cb(base_progress + progress_span)
    except RealesrganCancelledError:
        _terminate_process_tree(decoder_process)
        _terminate_process_tree(encoder_process)
        try:
            write_queue.put(None)
        except Exception:
            pass
        raise
    except Exception:
        _terminate_process_tree(decoder_process)
        _terminate_process_tree(encoder_process)
        try:
            write_queue.put(None)
        except Exception:
            pass
        raise


def _enhance_video_multi_gpu(
    input_path: str,
    output_path: str,
    gpu_ids: List[int],
    options: dict,
    meta: Dict[str, str],
    progress_cb=None,
    progress_percent_cb=None,
    cancel_cb=None,
    stats_cb=None,
) -> None:
    """Parallelize video enhancement across multiple GPUs."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed
    
    total_duration = float(meta.get("duration", 0))
    if total_duration <= 0:
        raise RuntimeError("无法获取视频时长，无法进行并行处理")
        
    num_workers = len(gpu_ids)
    segment_duration = total_duration / num_workers
    temp_dir = os.path.join(get_realesrgan_data_root(), f"tmp_parallel_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)
    
    segments = []
    for i in range(num_workers):
        start_time = i * segment_duration
        # Last segment should cover until the end
        dur = segment_duration if i < num_workers - 1 else (total_duration - start_time)
        seg_output = os.path.join(temp_dir, f"part_{i}.mp4")
        segments.append((start_time, dur, seg_output, gpu_ids[i]))
        
    if progress_cb:
        progress_cb(f"🚀 已启动多 GPU 并行处理，任务拆分为 {num_workers} 个分段...")
        
    try:
        # Use a wrapper for multiprocessing
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = []
            for start, dur, out, gpu_id in segments:
                seg_options = dict(options)
                seg_options["device"] = f"cuda:{gpu_id}"
                seg_options["_is_segment"] = True
                futures.append(executor.submit(
                    _worker_enhance_segment,
                    input_path, out, start, dur, seg_options, meta
                ))
            
            for future in as_completed(futures):
                _check_cancel(cancel_cb)
                future.result() # Raise exception if worker failed
                
        # Merge segments
        if progress_cb:
            progress_cb("📦 所有分段处理完成，正在合并输出视频...")
        _merge_video_segments(segments, output_path, input_path)
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def _worker_enhance_segment(input_path, output_path, start, dur, options, meta):
    """Entry point for worker process."""
    # We must re-import/re-init things if needed in the new process
    _enhance_video_segment(
        input_path=input_path,
        output_path=output_path,
        start_time=start,
        duration=dur,
        options=options,
        meta=meta
    )

def _enhance_video_segment(
    input_path: str,
    output_path: str,
    start_time: float,
    duration: float,
    options: dict,
    meta: Dict[str, str],
    progress_cb=None,
    progress_percent_cb=None,
    cancel_cb=None,
    stats_cb=None,
) -> None:
    ffmpeg_bin = _find_ffmpeg_binary()
    
    decode_cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{start_time:.3f}",
        "-t", f"{duration:.3f}",
        "-i", input_path,
        "-vsync", "0",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-",
    ]
    
    _enhance_video_with_streaming_backend_impl(
        input_path=input_path,
        output_path=output_path,
        options=options,
        meta=meta,
        decode_cmd=decode_cmd,
        progress_cb=progress_cb,
        progress_percent_cb=progress_percent_cb,
        cancel_cb=cancel_cb,
        stats_cb=stats_cb,
    )

def _merge_video_segments(segments: List[Tuple], output_path: str, original_input: str) -> None:
    ffmpeg_bin = _find_ffmpeg_binary()
    temp_list_path = os.path.join(os.path.dirname(output_path), "concat_list.txt")
    with open(temp_list_path, "w", encoding="utf-8") as f:
        for _, _, seg_path, _ in segments:
            # Use absolute path and forward slashes for ffmpeg concat demuxer
            f.write(f"file '{os.path.abspath(seg_path).replace(os.sep, '/')}'\n")
            
    try:
        # Merge segments and re-add audio from original input if needed
        # Note: audio sync might be tricky with segments, but this is the v0.3.0 approach.
        merge_cmd = [
            ffmpeg_bin, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", temp_list_path,
            "-i", original_input,
            "-map", "0:v",
            "-map", "1:a?",
            "-c:v", "copy",
            "-c:a", "copy",
            output_path
        ]
        subprocess.run(merge_cmd, check=True, capture_output=True, **_windows_hidden_subprocess_kwargs())
    finally:
        if os.path.exists(temp_list_path):
            os.remove(temp_list_path)


def _enhance_video_with_onnx_backend(
    input_path: str,
    output_path: str,
    options: dict,
    meta: Dict[str, str],
    *,
    selected_backend: str,
    progress_cb=None,
    progress_percent_cb=None,
    cancel_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
    stats_cb=None,
) -> None:
    ffmpeg_bin = _find_ffmpeg_binary()
    width = _safe_int(meta.get("width", 0), 0)
    height = _safe_int(meta.get("height", 0), 0)
    if width <= 0 or height <= 0:
        raise RuntimeError("无法获取视频尺寸，不能启动 ONNX 流式处理")

    fps = _parse_video_fps(meta, options.get("fps"))
    total_frames = _estimate_video_frame_count(meta, fps)
    provider_name = _get_onnx_provider_name(selected_backend)
    onnx_options = dict(options)
    onnx_options["_frame_shape"] = (width, height)
    onnx_options["_media_kind"] = "video"
    upsampler = _OnnxVideoUpsampler(onnx_options, provider_name, progress_cb=progress_cb)
    provider_perf_key = _build_onnx_perf_key(onnx_options, width, height, upsampler.active_provider, "video")
    batch_size = _get_cached_onnx_batch_size(onnx_options, width, height, upsampler.active_provider, "video")
    if batch_size <= 0:
        batch_size = _suggest_onnx_batch_size(upsampler.active_provider, width, height)

    if progress_cb:
        progress_cb(f"启用 ONNX Runtime 视频后端: {upsampler.active_provider} | 批大小 {batch_size}")
    if progress_percent_cb:
        progress_percent_cb(base_progress + progress_span * 0.05)

    decode_cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-hwaccel", "auto",
        "-i", input_path,
        "-vsync", "0",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-",
    ]
    decoder_process = subprocess.Popen(
        decode_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_windows_hidden_subprocess_kwargs(),
    )

    encoder_process = None
    frame_bytes = width * height * 3
    processed_frames = 0
    started_at = time.monotonic()
    decoder_stderr = b""
    encoder_stderr = b""
    frame_queue: "queue.Queue[object]" = queue.Queue(maxsize=max(batch_size * 4, 32))
    write_queue: "queue.Queue[object]" = queue.Queue(maxsize=max(batch_size * 4, 32))
    batch_autotuned = batch_size > 0 and _get_cached_onnx_batch_size(onnx_options, width, height, upsampler.active_provider, "video") > 0
    writer_state = {"error": None}

    def _reader():
        try:
            if decoder_process.stdout is None:
                frame_queue.put(("error", RuntimeError("ONNX 流式解码器未生成输出管道")))
                return
            while True:
                frame_buffer = _read_exact_bytes(decoder_process.stdout, frame_bytes)
                if not frame_buffer:
                    break
                if len(frame_buffer) != frame_bytes:
                    frame_queue.put(("error", RuntimeError("ONNX 流式解码过程中收到不完整视频帧")))
                    return
                frame = np.frombuffer(frame_buffer, dtype=np.uint8).reshape((height, width, 3))
                frame_queue.put(("frame", frame))
            frame_queue.put(("eof", None))
        except Exception as exc:
            try:
                frame_queue.put(("error", exc))
            except Exception:
                pass

    def _writer():
        try:
            while True:
                item = write_queue.get()
                if item is None:
                    break
                enc_ref, output_frame = item
                if enc_ref.stdin is None:
                    writer_state["error"] = RuntimeError("流式编码器未生成输入管道")
                    break
                if not output_frame.flags["C_CONTIGUOUS"]:
                    output_frame = np.ascontiguousarray(output_frame)
                enc_ref.stdin.write(output_frame.tobytes())
        except Exception as exc:
            writer_state["error"] = exc

    def _flush_batch(frames: List[np.ndarray]) -> int:
        nonlocal encoder_process, processed_frames
        if not frames:
            return 0
        outputs = upsampler.enhance_batch(frames)
        if len(outputs) != len(frames):
            raise RuntimeError("ONNX 视频后端返回的帧数与输入不一致")
        for output in outputs:
            if encoder_process is None:
                out_height, out_width = output.shape[:2]
                encoder_process, _encoder_name = _start_video_encoder_process(
                    ffmpeg_bin,
                    input_path=input_path,
                    output_path=output_path,
                    fps=fps,
                    out_width=out_width,
                    out_height=out_height,
                    options=options,
                    progress_cb=progress_cb,
                )
            write_queue.put((encoder_process, output))
            processed_frames += 1
        return len(outputs)

    pending_frames: List[np.ndarray] = []
    try:
        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()
        writer_thread = threading.Thread(target=_writer, daemon=True)
        writer_thread.start()

        while True:
            _check_cancel(cancel_cb)
            if writer_state["error"] is not None:
                raise writer_state["error"]
            item_kind, item_value = frame_queue.get(timeout=30)
            if item_kind == "eof":
                if pending_frames:
                    _flush_batch(pending_frames)
                    pending_frames = []
                break
            if item_kind == "error":
                raise item_value
            if not batch_autotuned:
                batch_size = _autotune_onnx_batch_size(
                    upsampler,
                    item_value,
                    options=onnx_options,
                    width=width,
                    height=height,
                    media_kind="video",
                    progress_cb=progress_cb,
                )
                provider_perf_key = _build_onnx_perf_key(onnx_options, width, height, upsampler.active_provider, "video")
                batch_autotuned = True
            pending_frames.append(item_value)
            if len(pending_frames) >= batch_size:
                _flush_batch(pending_frames)
                pending_frames = []
                fps_value, _eta_seconds = _emit_video_stats(
                    started_at=started_at,
                    processed_frames=processed_frames,
                    total_frames=total_frames,
                    stats_cb=stats_cb,
                )
                if progress_cb and (processed_frames == batch_size or processed_frames % max(batch_size * 4, 16) == 0):
                    progress_cb(f"ONNX 流式增强视频帧 {processed_frames}/{total_frames or processed_frames}，当前 {fps_value:.2f} 帧/秒...")
                if progress_percent_cb and total_frames > 0:
                    ratio = min(processed_frames / max(total_frames, 1), 1.0)
                    progress_percent_cb(base_progress + progress_span * (0.1 + ratio * 0.8))

        write_queue.put(None)
        writer_thread.join(timeout=120)
        if writer_state["error"] is not None:
            raise writer_state["error"]

        if decoder_process.stdout is not None:
            decoder_process.stdout.close()
        if encoder_process is None:
            raise RuntimeError("未能从视频中解码出任何帧")

        if encoder_process.stdin is not None:
            encoder_process.stdin.close()

        decoder_return_code = decoder_process.wait(timeout=30)
        if decoder_process.stderr is not None:
            decoder_stderr = decoder_process.stderr.read() or b""
        if decoder_return_code != 0:
            raise RuntimeError((decoder_stderr.decode("utf-8", errors="replace") or "FFmpeg 解码失败").strip())

        encoder_return_code = encoder_process.wait(timeout=120)
        if encoder_process.stderr is not None:
            encoder_stderr = encoder_process.stderr.read() or b""
        if encoder_return_code != 0:
            raise RuntimeError((encoder_stderr.decode("utf-8", errors="replace") or "FFmpeg 编码失败").strip())

        observed_fps = processed_frames / max(time.monotonic() - started_at, 1e-6)
        perf_key = _build_video_perf_profile_key(onnx_options, width, height)
        _update_runtime_perf_bucket("video_backend", perf_key, {
            "backend": selected_backend,
            "provider": upsampler.active_provider,
            "batch_size": batch_size,
            "fps": round(observed_fps, 3),
        })
        _record_backend_perf(
            onnx_options,
            width,
            height,
            "video",
            selected_backend,
            observed_fps,
            extra={"provider": upsampler.active_provider, "batch_size": batch_size},
        )
        _update_runtime_perf_bucket("onnx_batch", provider_perf_key, {
            "batch_size": batch_size,
            "provider": upsampler.active_provider,
            "fps": round(observed_fps, 3),
            "autotuned": True,
        })
        if progress_percent_cb:
            progress_percent_cb(base_progress + progress_span)
    except RealesrganCancelledError:
        _terminate_process_tree(decoder_process)
        _terminate_process_tree(encoder_process)
        try:
            write_queue.put(None)
        except Exception:
            pass
        raise
    except Exception:
        _terminate_process_tree(decoder_process)
        _terminate_process_tree(encoder_process)
        try:
            write_queue.put(None)
        except Exception:
            pass
        raise


def enhance_video(
    input_path: str,
    output_path: str,
    options: dict,
    progress_cb=None,
    progress_percent_cb=None,
    cancel_cb=None,
    base_progress: float = 0.0,
    progress_span: float = 100.0,
    stats_cb=None,
):
    ffmpeg_bin = _find_ffmpeg_binary()
    temp_parent = None
    temp_root = None
    
    # Check for FLV and re-mux to MP4 if needed (Real-ESRGAN v0.3.0 logic)
    if input_path.lower().endswith(".flv"):
        if progress_cb:
            progress_cb("检测到 FLV 格式视频，正在进行无损转码以确保处理兼容性...")
        temp_dir = os.path.join(get_realesrgan_data_root(), "tmp_flv_remux")
        os.makedirs(temp_dir, exist_ok=True)
        remuxed_path = os.path.join(temp_dir, os.path.basename(input_path) + ".mp4")
        remux_cmd = [ffmpeg_bin, "-y", "-i", input_path, "-codec", "copy", remuxed_path]
        try:
            subprocess.run(remux_cmd, capture_output=True, check=True, **_windows_hidden_subprocess_kwargs())
            input_path = remuxed_path
            # Cleanup temp MP4 later
            temp_parent = temp_dir 
        except Exception as exc:
            if progress_cb:
                progress_cb(f"⚠️ FLV 转码失败，将尝试直接处理: {exc}")

    try:
        _check_cancel(cancel_cb)
        if progress_percent_cb:
            progress_percent_cb(base_progress)
        meta = _probe_video(input_path)
        capability = get_runtime_capabilities(options)
        
        # Check for multi-GPU IDs (Real-ESRGAN v0.3.0 logic)
        gpu_ids = options.get("gpu_ids")
        if isinstance(gpu_ids, str):
            try:
                gpu_ids = [int(x.strip()) for x in gpu_ids.split(",") if x.strip()]
            except Exception:
                gpu_ids = None
        
        if gpu_ids and len(gpu_ids) > 1 and TORCH_AVAILABLE and torch.cuda.is_available():
            _enhance_video_multi_gpu(
                input_path,
                output_path,
                gpu_ids,
                options,
                meta,
                progress_cb=progress_cb,
                progress_percent_cb=progress_percent_cb,
                cancel_cb=cancel_cb,
                stats_cb=stats_cb,
            )
            return

        selected_backend = _select_video_backend_for_file(options, meta, capability)
        video_options = _normalize_options_for_video_backend(options, selected_backend, progress_cb=progress_cb)

        if selected_backend == "torch_cuda":
            if progress_cb:
                progress_cb("检测到 NVIDIA CUDA 视频环境，已优先使用流式 CUDA 后端，避免整段拆帧带来的额外磁盘开销。")
            _enhance_video_with_streaming_backend(
                input_path,
                output_path,
                video_options,
                meta,
                progress_cb=progress_cb,
                progress_percent_cb=progress_percent_cb,
                cancel_cb=cancel_cb,
                base_progress=base_progress,
                progress_span=progress_span,
                stats_cb=stats_cb,
            )
        elif selected_backend in ("ort_tensorrt", "ort_cuda", "ort_directml", "ort_cpu"):
            if progress_cb:
                backend_label = {
                    "ort_tensorrt": "ONNX Runtime TensorRT",
                    "ort_cuda": "ONNX Runtime CUDA",
                    "ort_directml": "ONNX Runtime DirectML",
                    "ort_cpu": "ONNX Runtime CPU",
                }.get(selected_backend, selected_backend)
                progress_cb(f"检测到更适合当前设备的视频推理路径，已切换到 {backend_label} 流式后端。")
            _enhance_video_with_onnx_backend(
                input_path,
                output_path,
                video_options,
                meta,
                selected_backend=selected_backend,
                progress_cb=progress_cb,
                progress_percent_cb=progress_percent_cb,
                cancel_cb=cancel_cb,
                base_progress=base_progress,
                progress_span=progress_span,
                stats_cb=stats_cb,
            )
        elif selected_backend == "ncnn":
            if _should_use_chunked_ncnn_backend(video_options, meta):
                try:
                    _enhance_video_with_chunked_ncnn_backend(
                        input_path,
                        output_path,
                        video_options,
                        meta,
                        progress_cb=progress_cb,
                        progress_percent_cb=progress_percent_cb,
                        cancel_cb=cancel_cb,
                        base_progress=base_progress,
                        progress_span=progress_span,
                        stats_cb=stats_cb,
                    )
                except Exception as exc:
                    if progress_cb:
                        progress_cb(f"⚠️ NCNN 分块流水后端异常，回退到兼容模式重试: {exc}")
                    _enhance_video_with_extracted_ncnn_backend(
                        input_path,
                        output_path,
                        video_options,
                        meta,
                        cancel_cb=cancel_cb,
                        progress_cb=progress_cb,
                        progress_percent_cb=progress_percent_cb,
                        base_progress=base_progress,
                        progress_span=progress_span,
                        stats_cb=stats_cb,
                    )
            else:
                _enhance_video_with_extracted_ncnn_backend(
                    input_path,
                    output_path,
                    video_options,
                    meta,
                    cancel_cb=cancel_cb,
                    progress_cb=progress_cb,
                    progress_percent_cb=progress_percent_cb,
                    base_progress=base_progress,
                    progress_span=progress_span,
                    stats_cb=stats_cb,
                )
        else:
            _enhance_video_with_streaming_backend(
                input_path,
                output_path,
                video_options,
                meta,
                progress_cb=progress_cb,
                progress_percent_cb=progress_percent_cb,
                cancel_cb=cancel_cb,
                base_progress=base_progress,
                progress_span=progress_span,
                stats_cb=stats_cb,
            )
    finally:
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)
        if temp_parent and os.path.isdir(temp_parent):
            try:
                if not os.listdir(temp_parent):
                    os.rmdir(temp_parent)
            except Exception:
                pass


class RealEsrganProcessThread(QThread):
    progress_signal = pyqtSignal(str)
    progress_percent_signal = pyqtSignal(float)
    stats_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, file_list: List[str], output_dir: str, options: dict):
        super().__init__()
        self.file_list = file_list
        self.output_dir = output_dir
        self.options = options
        self._cancelled = False
        self._subprocess = None

    def _emit_stats(self, payload: Dict[str, object]) -> None:
        try:
            self.stats_signal.emit(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    def stop(self):
        self._cancelled = True
        _terminate_process_tree(self._subprocess)
        self.progress_signal.emit("⚠️ 正在取消 Real-ESRGAN 任务...")

    def _build_output_path(self, input_path: str) -> str:
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        suffix = str(self.options.get("suffix", "_realesrgan")).strip()
        if is_video_file(input_path):
            ext = os.path.splitext(input_path)[1].lower() or ".mp4"
        else:
            ext = _infer_output_ext(input_path, self.options)
        return os.path.join(self.output_dir, f"{base_name}{suffix}{ext}")

    def _run_with_external_python(self) -> None:
        runtime_python = find_realesrgan_runtime_python()
        if not runtime_python:
            raise RuntimeError("未找到可用的画质修复运行时")

        payload = {
            "file_list": self.file_list,
            "output_dir": self.output_dir,
            "options": self.options,
        }
        payload_path = os.path.join(self.output_dir, ".realesrgan_job.json")
        os.makedirs(self.output_dir, exist_ok=True)
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        use_packaged_entry = (
            getattr(sys, "frozen", False)
            and os.path.abspath(runtime_python) == os.path.abspath(sys.executable)
        )
        if use_packaged_entry:
            cmd = [runtime_python, "--realesrgan-job", payload_path]
            self.progress_signal.emit(f"检测到内置画质修复运行时，准备接管任务: {os.path.basename(runtime_python)}")
        else:
            cmd = [runtime_python, _get_realesrgan_script_path(), "--job", payload_path]
            self.progress_signal.emit(f"检测到外部画质修复运行时，准备接管任务: {runtime_python}")
        child_env = os.environ.copy() if use_packaged_entry else _build_external_python_env(runtime_python)
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        self._subprocess = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            **_windows_hidden_subprocess_kwargs(),
        )

        success = False
        summary_message = "画质修复完成"
        try:
            if self._subprocess.stdout is not None:
                for raw_line in self._subprocess.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("PROGRESS:"):
                        try:
                            self.progress_percent_signal.emit(float(line.split(":", 1)[1].strip()))
                        except Exception:
                            pass
                    elif line.startswith("STATS:"):
                        self.stats_signal.emit(line.split(":", 1)[1].strip())
                    elif line.startswith("DONE:"):
                        success = line.split(":", 1)[1].strip() == "1"
                    else:
                        if "画质修复完成" in line:
                            summary_message = line
                        self.progress_signal.emit(line)
            return_code = self._subprocess.wait()
            if self._cancelled:
                self.finished_signal.emit(False, "任务已取消")
                return
            if return_code != 0:
                raise RuntimeError(f"外部运行时执行失败，退出码 {return_code}")
            self.progress_percent_signal.emit(100)
            self.finished_signal.emit(success, summary_message)
        finally:
            try:
                os.remove(payload_path)
            except Exception:
                pass
            self._subprocess = None

    def run(self):
        try:
            capability = get_runtime_capabilities(self.options)
            all_videos = bool(self.file_list) and all(is_video_file(path) for path in self.file_list)
            all_images = bool(self.file_list) and all(not is_video_file(path) for path in self.file_list)
            current_runtime = capability.get("current") or {}
            external_runtime = capability.get("external") or {}
            preferred_gpu_backend = str(capability.get("preferred_gpu_backend") or "cpu").strip().lower()
            preferred_image_backend = str(capability.get("preferred_image_backend") or "cpu").strip().lower()

            runtime_info = capability.get("effective") or current_runtime or {}
            if all_images and external_runtime:
                current_image_score = _rank_video_backend(
                    str(current_runtime.get("preferred_image_backend") or "cpu"),
                    capability.get("gpu_vendors") or [],
                    self.options,
                )
                external_image_score = _rank_video_backend(
                    str(external_runtime.get("preferred_image_backend") or "cpu"),
                    capability.get("gpu_vendors") or [],
                    self.options,
                )
                if external_image_score > current_image_score + 10:
                    runtime_info = external_runtime

            runtime_python = str(runtime_info.get("python") or sys.executable).strip()
            runtime_mode_key = preferred_gpu_backend if all_videos else preferred_image_backend if all_images else preferred_gpu_backend
            if runtime_mode_key == "ncnn":
                runtime_mode = "GPU(Vulkan 自动)"
            elif runtime_mode_key == "ort_tensorrt":
                runtime_mode = "GPU(TensorRT)"
            elif runtime_mode_key == "ort_directml":
                runtime_mode = "GPU(DirectML)"
            elif runtime_mode_key == "ort_cuda":
                runtime_mode = "GPU(ONNX CUDA)"
            elif runtime_mode_key == "ort_cpu":
                runtime_mode = "CPU(ONNX Runtime)"
            elif runtime_info.get("cuda_available"):
                runtime_mode = "GPU(CUDA)"
            elif capability.get("gpu_detected"):
                runtime_mode = "GPU 已检测，异常时自动回退"
            else:
                runtime_mode = "CPU"
            self.progress_signal.emit(
                f"本次画质修复运行环境: {os.path.basename(runtime_python) or runtime_python} | {runtime_mode}"
            )
            if runtime_python and os.path.abspath(runtime_python) != os.path.abspath(sys.executable):
                self.progress_signal.emit("检测到更适合当前设备的修复运行时，正在切换到专用运行环境...")
                self._run_with_external_python()
                return
            missing = local_missing_dependencies(self.options)
            if missing:
                self.progress_signal.emit("检测到当前环境未直接加载修复引擎，正在切换到内置运行时...")
                self._run_with_external_python()
                return

            missing_resources = collect_missing_download_resources(self.options, self.file_list)
            if missing_resources:
                if self.options.get("auto_download_models"):
                    self.progress_signal.emit("检测到缺少画质修复模型资源，正在从官方源下载（如果下载失败，请开启代理/VPN后重试）...")
                    try:
                        download_models(
                            progress_cb=self.progress_signal.emit,
                            options=self.options,
                            file_list=self.file_list,
                        )
                    except Exception as exc:
                        raise RuntimeError(
                            f"画质修复模型下载失败，请开启代理（VPN）后重试。"
                        )
                    missing_resources = collect_missing_download_resources(self.options, self.file_list)
                if missing_resources:
                    raise RuntimeError(
                        "缺少画质修复模型资源，请开启代理（VPN）后重启程序重试。"
                    )

            total = len(self.file_list)
            success_count = 0
            fail_count = 0
            active_options = dict(self.options)

            for index, input_path in enumerate(self.file_list, start=1):
                if self._cancelled:
                    self.finished_signal.emit(False, "任务已取消")
                    return

                self.progress_percent_signal.emit(((index - 1) / max(total, 1)) * 100)
                self.progress_signal.emit(f"🚀 开始处理 ({index}/{total}): {os.path.basename(input_path)}")

                base_name = os.path.splitext(os.path.basename(input_path))[0]
                suffix = str(self.options.get("suffix", "_realesrgan")).strip()
                if is_video_file(input_path):
                    ext = os.path.splitext(input_path)[1].lower() or ".mp4"
                else:
                    ext = _infer_output_ext(input_path, self.options)
                output_path = os.path.join(self.output_dir, f"{base_name}{suffix}{ext}")

                if os.path.exists(output_path) and not self.options.get("overwrite"):
                    self.progress_signal.emit(f"  ⏭️ 跳过（文件已存在）: {os.path.basename(output_path)}")
                    continue

                try:
                    file_base = ((index - 1) / max(total, 1)) * 100.0
                    file_span = 100.0 / max(total, 1)
                    active_options, fell_back_to_cpu = _run_one_media_with_cpu_retry(
                        input_path,
                        output_path,
                        active_options,
                        progress_cb=self.progress_signal.emit,
                        progress_percent_cb=self.progress_percent_signal.emit,
                        stats_cb=self._emit_stats,
                        cancel_cb=lambda: self._cancelled,
                        base_progress=file_base,
                        progress_span=file_span,
                    )
                    if fell_back_to_cpu:
                        self.progress_signal.emit("⚠️ 当前显卡运行不稳定，剩余任务将统一切换到 CPU。")
                    success_count += 1
                    self.progress_signal.emit(f"  ✅ 完成: {os.path.basename(output_path)}")
                except RealesrganCancelledError:
                    self.finished_signal.emit(False, "任务已取消")
                    return
                except Exception as exc:
                    fail_count += 1
                    self.progress_signal.emit(f"  ❌ 失败: {exc}")
                    self.progress_signal.emit(traceback.format_exc().strip())

            self.progress_percent_signal.emit(100)
            summary = f"Real-ESRGAN 处理完成：成功 {success_count} 个，失败 {fail_count} 个"
            self.finished_signal.emit(success_count > 0, summary)
        except Exception as exc:
            self.progress_signal.emit(traceback.format_exc().strip())
            self.finished_signal.emit(False, f"Real-ESRGAN 启动失败: {exc}")


def _safe_console_print(message: str) -> None:
    text = str(message or "")
    try:
        print(text, flush=True)
        return
    except UnicodeEncodeError:
        pass

    stream = getattr(sys, "stdout", None)
    if stream is None:
        return
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        stream.buffer.write((text + "\n").encode(encoding, errors="replace"))
        stream.flush()
    except Exception:
        try:
            stream.write(text.encode("ascii", errors="replace").decode("ascii") + "\n")
            stream.flush()
        except Exception:
            pass


def _run_cli_job(job_path: str) -> int:
    with open(job_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    file_list = payload["file_list"]
    output_dir = payload["output_dir"]
    options = payload["options"]

    missing_resources = collect_missing_download_resources(options, file_list)
    if missing_resources:
        if options.get("auto_download_models"):
            _safe_console_print("检测到缺少画质修复资源，正在按当前任务下载...")
            download_models(
                progress_cb=_safe_console_print,
                options=options,
                file_list=file_list,
            )
            missing_resources = collect_missing_download_resources(options, file_list)
        if missing_resources:
            print("缺少画质修复资源: " + ", ".join(missing_resources), flush=True)
            print("DONE:0", flush=True)
            return 1

    total = len(file_list)
    success_count = 0
    fail_count = 0
    active_options = dict(options)
    for index, input_path in enumerate(file_list, start=1):
        print(f"PROGRESS:{((index - 1) / max(total, 1)) * 100}", flush=True)
        print(f"开始处理 ({index}/{total}): {os.path.basename(input_path)}", flush=True)
        output_path = os.path.join(
            output_dir,
            f"{os.path.splitext(os.path.basename(input_path))[0]}{str(active_options.get('suffix', '_realesrgan')).strip()}"
            f"{os.path.splitext(input_path)[1].lower() if is_video_file(input_path) else _infer_output_ext(input_path, active_options)}"
        )
        try:
            file_base = ((index - 1) / max(total, 1)) * 100.0
            file_span = 100.0 / max(total, 1)
            active_options, fell_back_to_cpu = _run_one_media_with_cpu_retry(
                input_path,
                output_path,
                active_options,
                progress_cb=_safe_console_print,
                progress_percent_cb=lambda p: _safe_console_print(f"PROGRESS:{p}"),
                stats_cb=lambda payload: _safe_console_print("STATS:" + json.dumps(payload, ensure_ascii=False)),
                base_progress=file_base,
                progress_span=file_span,
            )
            if fell_back_to_cpu:
                _safe_console_print("⚠️ 当前显卡运行不稳定，剩余任务将统一切换到 CPU。")
            success_count += 1
            _safe_console_print(f"完成: {os.path.basename(output_path)}")
        except Exception as exc:
            fail_count += 1
            _safe_console_print(f"失败: {exc}")
            _safe_console_print(traceback.format_exc().strip())

    _safe_console_print("PROGRESS:100")
    _safe_console_print(f"画质修复完成：成功 {success_count} 个，失败 {fail_count} 个")
    _safe_console_print(f"DONE:{1 if success_count > 0 else 0}")
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--job":
        sys.exit(_run_cli_job(sys.argv[2]))
