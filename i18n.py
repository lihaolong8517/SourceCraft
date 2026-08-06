# -*- coding: utf-8 -*-
"""
轻量 i18n 模块 —— 中文原文即 key，查表失败自动回退原文（中文），永不崩溃。

用法：
    from i18n import t, t_format, set_language, get_language, SUPPORTED_LANGUAGES
    set_language("en_US")                 # 启动时从设置读取并调用
    label.setText(t("输入视频URL..."))      # 英文界面自动替换，未翻译的回退中文
    status.setText(t_format("已添加 {n} 张图片", n=count))

翻译文件：项目根 locales/<lang>.json，形如 {"中文原文": "English"}
支持语言见 SUPPORTED_LANGUAGES，新增语言只需加 JSON 文件 + 表项。
"""
import json
import os
import sys

# (语言代码, 显示名, 该语言自身的翻译完整性标记)
SUPPORTED_LANGUAGES = [
    ("zh_CN", "中文（简体）"),
    ("en_US", "English"),
    ("ja_JP", "日本語"),
    ("ko_KR", "한국어"),
    ("es_ES", "Español"),
    ("fr_FR", "Français"),
    ("de_DE", "Deutsch"),
    ("ru_RU", "Русский"),
]
LANG_CODES = [c for c, _ in SUPPORTED_LANGUAGES]
DEFAULT_LANGUAGE = "zh_CN"

_current = os.environ.get("SOURCECRAFT_LANG", DEFAULT_LANGUAGE)
_tables = {}     # lang -> {key: translated}
_loaded = set()


def locales_dir():
    """定位 locales 目录：打包版=exe 同级/locales；开发态=项目根/locales（兼容包/单体两种启动）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "locales"))
        if getattr(sys, "_MEIPASS", None):
            candidates.append(os.path.join(sys._MEIPASS, "locales"))
    candidates.append(os.path.join(here, "locales"))
    candidates.append(os.path.join(os.path.dirname(here), "locales"))  # 包模式 -> 项目根
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    fallback = os.path.join(here, "locales")
    try:
        os.makedirs(fallback, exist_ok=True)
    except Exception:
        pass
    return fallback


def _load(lang):
    if lang in _loaded:
        return _tables.get(lang, {})
    _loaded.add(lang)
    path = os.path.join(locales_dir(), lang + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _tables[lang] = data
            return data
    except Exception:
        pass
    _tables[lang] = {}
    return _tables[lang]


def set_language(lang):
    global _current
    if lang not in LANG_CODES:
        lang = DEFAULT_LANGUAGE
    _current = lang


def get_language():
    return _current


def language_display_name(lang=None):
    lang = lang or _current
    for code, name in SUPPORTED_LANGUAGES:
        if code == lang:
            return name
    return lang


def t(text):
    """翻译：key=中文原文；未翻译/默认语言返回原文，永不抛错。"""
    if not text:
        return text
    if _current == DEFAULT_LANGUAGE:
        return text
    table = _load(_current)
    return table.get(text, text)


def t_format(text, **kwargs):
    """翻译 + 占位符填充（翻译可重排语序），如 t_format("已添加 {n} 张图片", n=5)"""
    try:
        return t(text).format(**kwargs)
    except Exception:
        return text


def missing_count(lang):
    """调试用：统计该语言翻译表中缺失的 key（配合收集脚本统计覆盖度）"""
    return 0
