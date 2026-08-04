"""视频下载器工具函数"""
import locale
import sys
import os
import re
import json
import subprocess
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtCore import Qt

from .constants import RUNTIME_APP_DIR, APP_ICON_CANDIDATES


"""视频下载器工具函数"""
def decode_subprocess_bytes(data):
    """按常见 yt-dlp/Windows 输出编码解码，避免标题被替换成乱码。"""
    if data is None:
        return ""
    if isinstance(data, str):
        return data

    encodings = [
        "utf-8-sig",
        "utf-8",
        locale.getpreferredencoding(False),
        "cp936",
        "gbk",
        "mbcs",
    ]
    seen = set()
    for encoding in encodings:
        if not encoding:
            continue
        encoding_key = encoding.lower()
        if encoding_key in seen:
            continue
        seen.add(encoding_key)
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")

# 简化的绕过配置模块已移除，现在使用高级选项中的限制绕过设置

# 浏览器Cookie提取功能已移除

# 全局变量存储ffmpeg命令路径
FFMPEG_CMD = "ffmpeg"
# 导入格式列表对话框
# 导入高级格式对话框
from format_dialog import AdvancedFormatDialog, FormatListDialog
from image_processor import ImageProcessThread, ImageProcessor, check_pillow
ENABLE_REALESRGAN_FEATURE = False
MODEL_CONFIGS = {}
RealEsrganProcessThread = None


def is_supported_realesrgan_media(_path):
    return False


def can_run_with_current_or_external_runtime(_options=None):
    return False
from download_sections_utils import (
    parse_time_token_to_seconds,
    split_time_range_tokens,
    validate_download_section,
)

# CookieInterceptor类已移除

# WebViewCookieGenerator类已移除 - 限制绕过功能已被删除


def _iter_app_icon_paths():
    search_roots = []
    if getattr(sys, 'frozen', False):
        search_roots.extend([
            RUNTIME_APP_DIR,
            os.path.join(RUNTIME_APP_DIR, "_internal"),
        ])
    else:
        search_roots.append(os.path.dirname(os.path.abspath(__file__)))

    seen = set()
    for root in search_roots:
        for icon_name in APP_ICON_CANDIDATES:
            icon_path = os.path.join(root, icon_name)
            normalized = os.path.normcase(os.path.abspath(icon_path))
            if normalized in seen:
                continue
            seen.add(normalized)
            yield icon_path


def _load_app_icon():
    """优先加载 ico，其次退回 png/svg，供窗口和任务栏统一使用。"""
    for icon_path in _iter_app_icon_paths():
        if not os.path.isfile(icon_path):
            continue
        icon = QIcon(icon_path)
        if not icon.isNull():
            return icon
    return QIcon()


def _dedupe_preserve_order(items):
    """按出现顺序去重并移除空值。"""
    seen = set()
    result = []
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def cleanup_download_title(title, strip_extension=True):
    """清理下载标题中的临时后缀、扩展名和时间戳。"""
    text = str(title or "").strip()
    if not text:
        return ""

    if strip_extension:
        text = os.path.splitext(os.path.basename(text))[0]

    text = re.sub(r'\.f\d+(?:-\d+)?$', '', text)
    text = re.sub(r'_单文件$', '', text)
    text = re.sub(r'_单文件_\d{10,}(?:_\d+)?$', '', text)
    text = re.sub(r'_\d{10,}(?:_\d+)?$', '', text)
    text = re.sub(r'\s+', ' ', text).strip(" ._-")
    return text


def is_generated_download_title(title):
    """判断标题是否像工具生成的占位名，而非真实视频标题。"""
    text = cleanup_download_title(title)
    if not text:
        return True

    lowered = text.lower()
    if lowered in {"未知标题", "未知视频", "下载的视频", "媒体直链", "video", "media"}:
        return True

    if re.fullmatch(r'\d{8,}(?:[_-]\d+){1,}', text):
        return True

    if re.fullmatch(r'[\d._-]{8,}', text):
        return True

    return False


def choose_better_download_title(*candidates):
    """从多个候选中挑选最像真实标题的一个。"""
    fallback = ""
    for candidate in candidates:
        cleaned = cleanup_download_title(candidate)
        if not cleaned:
            continue
        if not fallback:
            fallback = cleaned
        if not is_generated_download_title(cleaned):
            return cleaned
    return fallback


def sanitize_output_title_component(title):
    """将标题转换为适合直接写入文件名的片段。"""
    text = cleanup_download_title(title)
    if not text:
        return ""

    text = text.replace("%", "％")
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = re.sub(r'\s+', ' ', text).strip(" .")
    return text[:120].strip() if text else ""


def build_subtitle_lang_pattern(selected_lang, available_langs=None):
    """
    构建更稳健的 --sub-langs 匹配串，避免语言代码变体导致字幕下载失败。
    返回: (pattern, normalized_lang)
    """
    lang = str(selected_lang or "").strip()
    if not lang:
        return "zh-CN,zh,zh-Hans,en,en-US,en-GB", ""
    if lang == "all":
        return "all,-live_chat", "all"

    normalized = lang.replace("_", "-")
    variant_map = {
        "en": ["en", "en-US", "en-GB", "en-AU", "en-CA", "en-IN", "en-orig"],
        "en-US": ["en-US", "en", "en-GB", "en-AU", "en-CA", "en-orig"],
        "en-GB": ["en-GB", "en", "en-US", "en-AU", "en-CA", "en-orig"],
        "zh": [
            "zh", "zh-CN", "zh-Hans", "zh-Hans-CN",
            "zh-TW", "zh-Hant", "zh-Hant-TW",
            "zh-HK", "zh-Hant-HK", "yue", "yue-HK",
        ],
        "zh-CN": ["zh-CN", "zh-Hans", "zh-Hans-CN", "zh", "zh-cmn-Hans-CN"],
        "zh-TW": ["zh-TW", "zh-Hant", "zh-Hant-TW", "zh-HK", "zh-Hant-HK", "zh"],
        "zh-HK": ["zh-HK", "zh-Hant-HK", "yue-HK", "yue", "zh-Hant", "zh-TW", "zh"],
        "ja": ["ja", "ja-JP"],
        "ko": ["ko", "ko-KR"],
        "fr": ["fr", "fr-FR", "fr-CA"],
        "es": ["es", "es-ES", "es-MX", "es-AR", "es-419"],
        "pt": ["pt", "pt-BR", "pt-PT"],
        "pt-BR": ["pt-BR", "pt", "pt-PT"],
        "de": ["de", "de-DE", "de-AT", "de-CH"],
    }

    variants = list(variant_map.get(normalized, [normalized]))

    # 通用回退：区域语言代码增加基础语言（例如 en-US -> en）
    if "-" in normalized:
        base = normalized.split("-", 1)[0]
        variants.append(base)

    # 结合预览结果做动态回退：如果已获取可用字幕语言，则优先吸收真实语言代码
    available_codes = []
    if available_langs:
        available_codes = _dedupe_preserve_order(
            [str(code or "").replace("_", "-").strip() for code in available_langs]
        )
    if available_codes:
        lower_normalized = normalized.lower()
        base = normalized.split("-", 1)[0].lower()
        for code in available_codes:
            lower_code = code.lower()
            if lower_code == lower_normalized:
                variants.append(code)
                continue
            if "-" in normalized:
                if lower_code.startswith(base + "-"):
                    variants.append(code)
            else:
                if lower_code.startswith(lower_normalized + "-"):
                    variants.append(code)

    pattern = ",".join(_dedupe_preserve_order(variants))
    return pattern, normalized


def _is_x_platform_url(url):
    """
    模块级函数：检测是否为X平台（原Twitter）的URL
    支持多种域名格式：twitter.com, x.com, t.co, mobile版本, Tor镜像等
    可被主窗口和下载线程共用。
    """
    if not url:
        return False

    url_lower = url.lower()

    x_platform_patterns = [
        r'twitter\.com',
        r'x\.com',
        r't\.co',
        r'm\.twitter\.com',
        r'mobile\.twitter\.com',
        r'twitter3e4tixl4xyajtrzo62zg5vztmjuricljdp2c5kshju4avyoid\.onion',
    ]

    for pattern in x_platform_patterns:
        if re.search(pattern, url_lower):
            return True

    return False


