"""分类与请求头处理：把 CDP 网络记录转换成统一的候选字典。

输出字段与内置浏览器现有的 ``[MEDIA]`` 上报协议保持一致，便于主程序
（``MediaCaptureWindow.add_media`` / ``_merge_media_info``）零改动消费。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_MIN_DIRECT_BYTES = 500 * 1024
MIN_STRAY_BYTES = 8192  # 强媒体信号（video/mp4 等）的极小文件保护

FRAGMENT_EXTS = (".ts", ".m4s", ".m2ts", ".cmfv", ".cmfa", ".m4v")
MANIFEST_EXTS = (".m3u8", ".mpd", ".ism", ".ismc", ".f4m")
VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".flv", ".avi", ".wmv")
AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav")

MANIFEST_MIMES = (
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
    "application/dash+xml",
)

# 播放/下载需要回放的请求头白名单（对齐 VDH 的 DNR 保留集合）
DOWNLOAD_HEADER_NAMES = (
    "referer",
    "origin",
    "user-agent",
    "accept",
    "accept-language",
    "authorization",
    "cookie",
    "dnt",
    "x-requested-with",
)
HEADER_CANON = {
    "referer": "Referer",
    "origin": "Origin",
    "user-agent": "User-Agent",
    "accept": "Accept",
    "accept-language": "Accept-Language",
    "authorization": "Authorization",
    "cookie": "Cookie",
    "dnt": "DNT",
    "x-requested-with": "X-Requested-With",
}

# 签名 / 时效参数：去重时忽略（同一资源每次请求都会变）
VOLATILE_PARAMS = frozenset({
    "expire", "expires", "ei", "ip", "ipbits", "sig", "signature", "token",
    "st", "hmac", "expires_at", "begin", "end", "range", "bytestart", "byteend",
    "x-expires", "x-signature", "auth_key", "policy", "key-pair-id",
})

_CRLF_RE = re.compile(r"[\r\n\x00]")


def human_size(size_bytes) -> str:
    try:
        size = int(size_bytes or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _split_ext(url: str) -> str:
    try:
        path = urlsplit(url).path
    except (TypeError, ValueError):
        return ""
    dot = path.rfind(".")
    return path[dot:].lower() if dot >= 0 else ""


def normalize_url_key(url: str) -> str:
    """生成去重用 URL 键：忽略签名/时效参数与查询串顺序。"""
    if not isinstance(url, str) or not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    query = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name.lower() not in VOLATILE_PARAMS
    ]
    query.sort()
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path,
        urlencode(query, doseq=True),
        "",
    ))


def download_headers(headers) -> dict:
    """从原始请求头里挑出回放需要的头，并规范大小写。"""
    if not isinstance(headers, dict):
        return {}
    picked = {}
    for name, value in headers.items():
        if value is None:
            continue
        low = str(name).lower()
        if low not in DOWNLOAD_HEADER_NAMES and not low.startswith("x-"):
            continue
        if _CRLF_RE.search(str(value)):
            continue
        canon = HEADER_CANON.get(low, "-".join(part.capitalize() for part in low.split("-")))
        picked[canon] = str(value)
    return picked


def safe_headers(headers) -> dict:
    """供 HTTP 请求使用的安全头（去掉 Host 等由库自动管理的字段）。"""
    return download_headers(headers)


def build_header_args(headers) -> list:
    """把请求头转换为 yt-dlp 的 --add-header 参数对。"""
    args = []
    for name, value in download_headers(headers).items():
        args.extend(["--add-header", f"{name}: {value}"])
    return args


def captured_header_args(headers, allow_authorization: bool = False) -> list:
    """下载回放专用：在 :func:`build_header_args` 之上按需过滤 Authorization。

    Authorization 常常是短时 token，直接回放容易把请求打成无效会话，
    因此默认不携带，需要显式打开（``VDH_REPLAY_AUTH=1``）。
    """
    if not isinstance(headers, dict) or not headers:
        return []
    filtered = {}
    for name, value in headers.items():
        text = "" if value is None else str(value).strip()
        if not text:
            continue
        low = str(name or "").strip().lower()
        if not low:
            continue
        if low == "authorization" and not allow_authorization:
            continue
        filtered[name] = text
    return build_header_args(filtered)


def describe_candidate(candidate: dict) -> str:
    """一句话描述，如 ``HLS · 1080p · 5000Kbps``。"""
    kind = str(candidate.get("type") or "Media")
    quality = candidate.get("quality") or {}
    parts = [kind]
    if quality.get("resolution"):
        parts.append(str(quality["resolution"]))
    if quality.get("bandwidth"):
        try:
            parts.append(f"{int(quality['bandwidth']) // 1000}Kbps")
        except (TypeError, ValueError):
            pass
    if candidate.get("has_drm"):
        parts.append("DRM")
    elif " · " not in str(candidate.get("summary") or "") and candidate.get("size"):
        parts.append(str(candidate["size"]))
    return " · ".join(parts)


def classify_record(record: dict, page_url: str = "",
                    min_direct_bytes: int = DEFAULT_MIN_DIRECT_BYTES):
    """把一条网络记录分类成候选字典；不感兴趣的返回 ``None``。"""
    if not isinstance(record, dict):
        return None
    url = str(record.get("url") or "").strip()
    if not url:
        return None
    low_url = url.lower()
    if low_url.startswith(("data:", "blob:", "filesystem:")):
        return None
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return None
    if scheme not in ("http", "https"):
        return None
    status = record.get("status")
    try:
        status_int = int(status) if status is not None else 0
    except (TypeError, ValueError):
        status_int = 0
    if status_int and not (200 <= status_int < 300):
        return None

    ctype = str(record.get("content_type") or "").split(";")[0].strip().lower()
    ext = _split_ext(url)
    is_manifest = bool(
        ext in MANIFEST_EXTS
        or ctype in MANIFEST_MIMES
        or (not ext and ("hls" in low_url or "m3u8" in low_url or ".mpd" in low_url))
    )
    if ext in FRAGMENT_EXTS and not is_manifest:
        return None
    is_audio = bool(ctype.startswith("audio/") or ext in AUDIO_EXTS)
    is_video = bool(ctype.startswith("video/") or ext in VIDEO_EXTS)
    if not (is_manifest or is_audio or is_video):
        return None

    try:
        size_bytes = int(record.get("size_bytes") or record.get("content_length") or 0)
    except (TypeError, ValueError):
        size_bytes = 0
    if not is_manifest and size_bytes:
        if size_bytes < MIN_STRAY_BYTES:
            return None
        if size_bytes < min_direct_bytes and not is_video:
            return None
    if not size_bytes and not is_manifest and not is_video:
        return None

    if is_manifest:
        kind = "DASH" if (ext == ".mpd" or ctype == "application/dash+xml") else "HLS"
    elif is_audio:
        kind = "Audio"
    elif ctype == "video/mp4" or ext == ".mp4":
        kind = "MP4"
    else:
        kind = "Video"

    headers = download_headers(record.get("request_headers"))
    candidate = {
        "type": kind,
        "url": url,
        "url_key": normalize_url_key(url),
        "content_type": ctype,
        "size_bytes": size_bytes,
        "size": human_size(size_bytes),
        "page_url": record.get("page_url") or page_url or "",
        "source": "network",
        "quality": {},
        "has_drm": False,
        "audio_only": bool(is_audio and not is_manifest),
        "is_manifest": bool(is_manifest),
        "summary": "",
        "headers": headers,
    }
    candidate["summary"] = describe_candidate(candidate)
    return candidate


def normalize_report(payload: dict, page_url: str = "") -> dict:
    """规范化站点注入脚本上报的候选（补 url_key / 默认值）。"""
    if not isinstance(payload, dict):
        return {}
    url = str(payload.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return {}
    report = {key: value for key, value in payload.items() if value not in (None, "", [])}
    report.setdefault("type", "Media")
    report.setdefault("source", "site")
    report.setdefault("quality", {})
    report.setdefault("has_drm", False)
    report.setdefault("audio_only", False)
    report.setdefault("is_manifest", False)
    report["url"] = url
    report["url_key"] = report.get("url_key") or normalize_url_key(url)
    report["page_url"] = report.get("page_url") or page_url or ""
    report["headers"] = download_headers(report.get("headers"))
    report["summary"] = describe_candidate(report)
    return report
