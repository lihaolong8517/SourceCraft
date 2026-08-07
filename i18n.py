# -*- coding: utf-8 -*-
"""
轻量 i18n 模块 —— 中文原文即 key，查表失败自动回退中文原文，永不崩溃。

═══════════════════════════════════════════════════════════════
 使用规范（所有 UI 文案都必须遵守）
═══════════════════════════════════════════════════════════════
1. 静态文案（按钮/标签/tooltip/placeholder/分组标题等）：
       btn.setText(t("开始下载"))
   不要直接写：btn.setText("开始下载")

2. 动态文案（含变量）用 t_format + 占位符，翻译可重排语序：
       label.setText(t_format("已添加 {n} 张图片", n=count))
   不要写：label.setText(f"已添加 {count} 张图片")

3. 不可翻译的内容（yt-dlp 格式表达式、字幕语言代码、正则、
   URL、文件名等）保持原样，不要包 t()。

4. key 一律使用中文原文；翻译表 key 必须与源码中的中文逐字一致
   （含标点、空格、emoji）。

═══════════════════════════════════════════════════════════════
 添加新语言（3 步）
═══════════════════════════════════════════════════════════════
1. 新建 locales/<code>.json，如 locales/it_IT.json：{}
2. 在 SUPPORTED_LANGUAGES 中注册语言代码与显示名；
3. 运行 python scan_i18n.py 查看该语言的缺失 key 清单并补齐翻译。

翻译文件格式：{{"中文原文": "译文"}}，UTF-8 编码。
en_US.json 是完整参考；其余语言允许渐进式补全（缺的 key 回退中文）。

语言状态（2026-08）：
- en_US / zh_TW：人工精翻，完整 2100 key；
- ja_JP / ko_KR / es_ES / fr_FR / de_DE / ru_RU：Google 机翻打底（完整 2100 key），
  术语可能不完美，欢迎人工修订；修订时直接编辑对应 locales/*.json 即可。

═══════════════════════════════════════════════════════════════
 运行时切换
═══════════════════════════════════════════════════════════════
启动时调用 set_language()（app 启动已从 QSettings 读取 language）；
默认语言 DEFAULT_LANGUAGE = "zh_CN"；未翻译 key 回退中文原文。
"""
import json
import os
import sys

# (语言代码, 下拉框显示名) —— 顺序即设置界面下拉框顺序
SUPPORTED_LANGUAGES = [
    ("en_US", "English"),
    ("zh_CN", "中文（简体）"),
    ("zh_TW", "繁體中文"),
    ("ja_JP", "日本語"),
    ("ko_KR", "한국어"),
    ("es_ES", "Español"),
    ("fr_FR", "Français"),
    ("de_DE", "Deutsch"),
    ("ru_RU", "Русский"),
]
LANG_CODES = [code for code, _ in SUPPORTED_LANGUAGES]
DEFAULT_LANGUAGE = "zh_CN"

_current = os.environ.get("SOURCECRAFT_LANG", DEFAULT_LANGUAGE)
_tables = {}   # lang -> {key: translated}
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
    for cand in candidates:
        if cand and os.path.isdir(cand):
            return cand
    fallback = os.path.join(here, "locales")
    try:
        os.makedirs(fallback, exist_ok=True)
    except Exception:
        pass
    return fallback


def _load(lang):
    """加载并缓存某语言的翻译表（懒加载）。"""
    if lang in _loaded:
        return _tables.get(lang, {})
    _loaded.add(lang)
    table = {}
    path = os.path.join(locales_dir(), lang + ".json")
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            table = data
    except Exception:
        pass
    _tables[lang] = table
    return table


def set_language(lang):
    """切换当前语言；非法代码回退默认语言。"""
    global _current
    _current = lang if lang in LANG_CODES else DEFAULT_LANGUAGE


def get_language():
    return _current


def language_display_name(lang=None):
    """语言代码 -> 显示名（用于设置界面/提示文案）。"""
    lang = lang or _current
    for code, name in SUPPORTED_LANGUAGES:
        if code == lang:
            return name
    return lang


def t(text):
    """翻译：key=中文原文；查当前语言翻译表，未命中回退原文（中文），永不抛错。"""
    if not text:
        return text
    return _load(_current).get(text, text)


def t_format(text, *args, **kwargs):
    """翻译 + 占位符填充（译文可重排语序），支持位置/关键字参数：
       t_format("已添加 {} 个文件", count) 或 t_format("已添加 {n} 个文件", n=count)。"""
    try:
        return t(text).format(*args, **kwargs)
    except Exception:
        return text
