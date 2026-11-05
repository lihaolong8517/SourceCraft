"""m3u8 / MPD 清单解析：清晰度、加密方式、DRM 系统、时长。

纯标准库实现（正则 + 字符串），不拉取网络、不依赖第三方库，便于单测。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

_M3U8_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)(?:,|$)')
_MPD_REP_RE = re.compile(r"<Representation\b([^>]*)>(.*?)</Representation>", re.S)
_MPD_BASEURL_RE = re.compile(r"<BaseURL>([^<]+)</BaseURL>", re.S)
_MPD_PROT_RE = re.compile(r"<ContentProtection\b([^>]*?)/?>", re.S)
_MPD_AS_RE = re.compile(r"<AdaptationSet\b([^>]*)>(.*?)</AdaptationSet>", re.S)
_DUR_RE = re.compile(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?")
_RES_RE = re.compile(r"^(\d+)[xX*](\d+)$")


def _wanted(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text or "")
    return match.group(1) if match else default


def _attrs(line: str) -> dict:
    """解析 ``#EXT-X-STREAM-INF:`` 这类行里的 KEY=VALUE 列表。"""
    text = line.split(":", 1)[1] if ":" in line else ""
    found = {}
    for match in _M3U8_ATTR_RE.finditer(text):
        value = match.group(2).strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        found[match.group(1)] = value
    return found


def _duration_seconds(iso: str) -> float:
    match = _DUR_RE.fullmatch((iso or "").strip())
    if not match:
        return 0.0
    years, months, days, hours, minutes, seconds = match.groups()
    total = 0.0
    if years:
        total += int(years) * 365 * 86400
    if months:
        total += int(months) * 30 * 86400
    if days:
        total += int(days) * 86400
    if hours:
        total += int(hours) * 3600
    if minutes:
        total += int(minutes) * 60
    if seconds:
        total += float(seconds)
    return total


def _resolution_to_quality(resolution: str) -> dict:
    match = _RES_RE.match(str(resolution or "").strip())
    if not match:
        return {"resolution": str(resolution).strip()} if resolution else {}
    width, height = int(match.group(1)), int(match.group(2))
    return {"resolution": f"{min(width, height)}p", "width": width, "height": height}


def _normalize_drm(text: str) -> str:
    low = str(text or "").lower()
    if "edef8ba9" in low or "widevine" in low:
        return "Widevine"
    if "9a04f079" in low or "playready" in low:
        return "PlayReady"
    if "fairplay" in low or "com.apple.streamingkeydelivery" in low or "skd://" in low or "94ce86fb" in low:
        return "FairPlay"
    if "clearkey" in low or "e2719d58" in low:
        return "ClearKey"
    return ""


def _best_variant(variants: list) -> dict | None:
    best = None
    for variant in variants:
        if best is None:
            best = variant
            continue
        if (variant.get("height") or 0, variant.get("bandwidth") or 0) > (
            best.get("height") or 0,
            best.get("bandwidth") or 0,
        ):
            best = variant
    return best


def _quality_of(best: dict | None) -> dict:
    if not best:
        return {}
    quality = {
        "resolution": best.get("resolution", ""),
        "width": best.get("width", 0),
        "height": best.get("height", 0),
        "bandwidth": best.get("bandwidth", 0),
    }
    return {key: value for key, value in quality.items() if value}


def parse_m3u8(text: str, base_url: str = "") -> dict:
    """解析 master / media playlist。"""
    result = {
        "kind": "hls",
        "url": base_url,
        "is_master": False,
        "variants": [],
        "audio_tracks": [],
        "subtitle_tracks": [],
        "encryption": [],
        "drm_systems": [],
        "has_drm": False,
        "duration_s": 0.0,
        "segments": 0,
        "ended": False,
        "is_live": False,
        "playlist_type": "",
        "target_duration": 0.0,
        "has_init_map": False,
        "quality": {},
        "best_video": None,
        "summary": "",
    }
    if not isinstance(text, str) or "#EXTM3U" not in text:
        return result
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    def add_encryption(attrs: dict):
        method = (attrs.get("METHOD") or "").upper()
        if not method or method == "NONE":
            return
        keyformat = attrs.get("KEYFORMAT", "") or ""
        uri = attrs.get("URI", "") or ""
        system = _normalize_drm(keyformat) or _normalize_drm(uri)
        result["encryption"].append({"method": method, "uri": uri,
                                     "keyformat": keyformat, "system": system})
        if system:
            if system not in result["drm_systems"]:
                result["drm_systems"].append(system)
            result["has_drm"] = True

    pending_stream = None
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF"):
            result["is_master"] = True
            pending_stream = _attrs(line)
            continue
        if line.startswith("#EXT-X-MEDIA"):
            attrs = _attrs(line)
            media_type = (attrs.get("TYPE") or "").upper()
            track = {
                "group_id": attrs.get("GROUP-ID", ""),
                "name": attrs.get("NAME", ""),
                "language": attrs.get("LANGUAGE", ""),
                "url": urljoin(base_url, attrs["URI"]) if attrs.get("URI") else "",
                "default": attrs.get("DEFAULT", "").upper() == "YES",
            }
            if media_type == "AUDIO":
                result["audio_tracks"].append(track)
            elif media_type == "SUBTITLES":
                result["subtitle_tracks"].append(track)
            continue
        if line.startswith("#EXT-X-KEY"):
            add_encryption(_attrs(line))
            continue
        if line.startswith("#EXT-X-MAP"):
            result["has_init_map"] = True
            continue
        if line.startswith("#EXTINF"):
            result["segments"] += 1
            try:
                result["duration_s"] += float(line.split(":", 1)[1].split(",", 1)[0])
            except (IndexError, ValueError):
                pass
            continue
        if line.startswith("#EXT-X-ENDLIST"):
            result["ended"] = True
            continue
        if line.startswith("#EXT-X-PLAYLIST-TYPE"):
            result["playlist_type"] = line.split(":", 1)[-1].strip().upper()
            continue
        if line.startswith("#EXT-X-TARGETDURATION"):
            try:
                result["target_duration"] = float(line.split(":", 1)[1].strip())
            except (IndexError, ValueError):
                pass
            continue
        if line.startswith("#"):
            continue
        if pending_stream is not None:
            variant = {
                "url": urljoin(base_url, line),
                "bandwidth": int(pending_stream.get("BANDWIDTH") or 0),
                "codecs": pending_stream.get("CODECS", ""),
                "resolution": pending_stream.get("RESOLUTION", ""),
            }
            variant.update(_resolution_to_quality(variant["resolution"]))
            result["variants"].append(variant)
            pending_stream = None

    result["is_live"] = bool(not result["ended"] and result["playlist_type"] != "VOD")
    result["best_video"] = _best_variant(result["variants"])
    result["quality"] = _quality_of(result["best_video"])

    parts = ["HLS"]
    if result["is_master"]:
        if result["quality"].get("resolution"):
            parts.append(result["quality"]["resolution"])
        if result["quality"].get("bandwidth"):
            parts.append(f"{int(result['quality']['bandwidth']) // 1000}Kbps")
        if len(result["variants"]) > 1:
            parts.append(f"{len(result['variants'])} 个清晰度")
        elif result["audio_tracks"]:
            parts.append(f"{len(result['audio_tracks'])} 条音轨")
    else:
        if result["duration_s"]:
            parts.append(f"时长 {result['duration_s']:.1f}s")
        if result["segments"]:
            parts.append(f"{result['segments']} 段")
        if result["is_live"]:
            parts.append("直播")
    if result["has_drm"]:
        parts.append("DRM:" + "/".join(result["drm_systems"]))
    elif result["encryption"]:
        parts.append("加密:" + (result["encryption"][0].get("method") or ""))
    result["summary"] = " · ".join(part for part in parts if part)
    return result


def parse_mpd(text: str, base_url: str = "") -> dict:
    """解析 DASH MPD（Representation 级别 + ContentProtection）。"""
    result = {
        "kind": "dash",
        "url": base_url,
        "is_master": True,
        "representations": [],
        "variants": [],
        "drm_systems": [],
        "has_drm": False,
        "duration_s": 0.0,
        "is_live": False,
        "quality": {},
        "best_video": None,
        "summary": "",
    }
    if not isinstance(text, str) or "<MPD" not in text:
        return result

    head = ""
    mpd_head = re.search(r"<MPD\b([^>]*)>", text)
    if mpd_head:
        head = mpd_head.group(1)
    duration = _wanted(r'mediaPresentationDuration="([^"]+)"', head)
    if duration:
        result["duration_s"] = _duration_seconds(duration)
    result["is_live"] = bool(re.search(r'type="dynamic"', head))

    for adaptation in _MPD_AS_RE.finditer(text):
        attrs, body = adaptation.group(1), adaptation.group(2)
        mime = _wanted(r'mimeType="([^"]+)"', attrs).lower()
        for prot in _MPD_PROT_RE.finditer(body):
            system = _normalize_drm(prot.group(1))
            if system and system not in result["drm_systems"]:
                result["drm_systems"].append(system)
        for rep in _MPD_REP_RE.finditer(body):
            rep_attrs, rep_body = rep.group(1), rep.group(2)
            rep_mime = mime or _wanted(r'mimeType="([^"]+)"', rep_attrs).lower()
            base = urljoin(base_url, _wanted(r"<BaseURL>([^<]+)</BaseURL>", rep_body)) if "<BaseURL>" in rep_body else base_url
            item = {
                "id": _wanted(r'id="([^"]+)"', rep_attrs),
                "mime_type": rep_mime,
                "bandwidth": int(_wanted(r'bandwidth="(\d+)"', rep_attrs, "0") or 0),
                "codecs": _wanted(r'codecs="([^"]+)"', rep_attrs),
                "width": int(_wanted(r'width="(\d+)"', rep_attrs, "0") or 0),
                "height": int(_wanted(r'height="(\d+)"', rep_attrs, "0") or 0),
                "url": base,
            }
            if item["width"] and item["height"]:
                item["resolution"] = f"{min(item['width'], item['height'])}p"
            result["representations"].append(item)

    if result["drm_systems"]:
        result["has_drm"] = True
    videos = [item for item in result["representations"] if "video" in (item.get("mime_type") or "") or item.get("height")]
    audios = [item for item in result["representations"] if "audio" in (item.get("mime_type") or "")]
    for item in videos:
        result["variants"].append({
            "url": item["url"],
            "bandwidth": item["bandwidth"],
            "resolution": item.get("resolution", ""),
            "width": item["width"],
            "height": item["height"],
        })
    result["best_video"] = _best_variant(videos)
    result["quality"] = _quality_of(result["best_video"])

    parts = ["DASH"]
    if result["quality"].get("resolution"):
        parts.append(result["quality"]["resolution"])
    if result["quality"].get("bandwidth"):
        parts.append(f"{int(result['quality']['bandwidth']) // 1000}Kbps")
    if audios:
        parts.append("含音轨")
    if result["duration_s"]:
        parts.append(f"时长 {result['duration_s']:.0f}s")
    if result["is_live"]:
        parts.append("直播")
    if result["has_drm"]:
        parts.append("DRM:" + "/".join(result["drm_systems"]))
    result["summary"] = " · ".join(part for part in parts if part)
    return result


def analyze_manifest_text(text: str, url: str = "") -> dict:
    """按内容分派到 m3u8 / MPD 解析器；不是清单的文本返回空字典。"""
    if not isinstance(text, str) or not text.strip():
        return {}
    stripped = text.lstrip()
    head = stripped[:1024]
    if "#EXTM3U" in head:
        return parse_m3u8(stripped, url)
    if "<MPD" in head:
        return parse_mpd(stripped, url)
    return {}


def manifest_patch(analysis: dict) -> dict:
    """把解析结果压缩成可直接回填到候选里的补丁。"""
    if not isinstance(analysis, dict) or not analysis:
        return {}
    patch = {
        "is_manifest": True,
        "has_drm": bool(analysis.get("has_drm")),
        "summary": analysis.get("summary") or "",
        "manifest": {
            "kind": analysis.get("kind"),
            "variants": analysis.get("variants") or [],
            "audio_tracks": analysis.get("audio_tracks") or [],
            "subtitle_tracks": analysis.get("subtitle_tracks") or [],
            "drm_systems": analysis.get("drm_systems") or [],
            "duration_s": analysis.get("duration_s") or 0,
            "is_live": bool(analysis.get("is_live")),
            "segments": analysis.get("segments") or 0,
        },
    }
    quality = analysis.get("quality") or {}
    if quality:
        patch["quality"] = quality
    return patch
