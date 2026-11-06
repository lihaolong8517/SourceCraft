"""vdh_capture：VDH（Video Download Helper）等价的媒体捕获层。

设计目标（对齐 VDH 的抓取与识别能力）：
  * CDP 网络嗅探：全站请求 + 真实请求头；
  * 站点适配器：对特定站点读取页面全局配置，拿到清单/直链；
  * 清单富化：拉取 m3u8/mpd 文本，解析清晰度、加密方式、DRM；
  * 候选表：去重、合并、上限淘汰。

本包不依赖 Qt；Playwright 只在 runner 里用到。
"""

from __future__ import annotations

VERSION = "1.0.0"
__version__ = VERSION

# 惰性导入，避免子模块互相依赖时出现循环导入
_LAZY_ATTRS = {
    "MediaStore": ("store", "MediaStore"),
    "StoreEntry": ("store", "StoreEntry"),
    "CdpNetworkSniffer": ("sniffer", "CdpNetworkSniffer"),
    "CaptureRunner": ("runner", "CaptureRunner"),
    "classify_record": ("bridge", "classify_record"),
    "normalize_report": ("bridge", "normalize_report"),
    "normalize_url_key": ("bridge", "normalize_url_key"),
    "download_headers": ("bridge", "download_headers"),
    "build_header_args": ("bridge", "build_header_args"),
    "captured_header_args": ("bridge", "captured_header_args"),
    "human_size": ("bridge", "human_size"),
    "analyze_manifest_text": ("manifest", "analyze_manifest_text"),
    "manifest_patch": ("manifest", "manifest_patch"),
    "PreviewSpec": ("preview", "PreviewSpec"),
    "PreviewCache": ("preview", "PreviewCache"),
    "build_preview_command": ("preview", "build_preview_command"),
    "extract_preview": ("preview", "extract_preview"),
    "resolve_ffmpeg": ("preview", "resolve_ffmpeg"),
    "default_cache_dir": ("preview", "default_cache_dir"),
}


def __getattr__(name):
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    value = getattr(module, attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals()) + list(_LAZY_ATTRS))


__all__ = ["VERSION", *_LAZY_ATTRS]
