# -*- coding: utf-8 -*-
"""
i18n 翻译维护工具 —— 扫描代码中 t()/t_format() 调用的中文 key，
检查各语言翻译文件的缺失情况，供维护者补齐翻译。

用法：
    python scan_i18n.py                 # 统计全部语言的覆盖率 + 缺失 key 数
    python scan_i18n.py --lang en_US    # 只看某语言
    python scan_i18n.py --list-missing  # 列出全部语言的缺失 key
    python scan_i18n.py --update-pending  # 重新生成 locales/_pending_zh_keys.txt
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
LOCALES = os.path.join(ROOT, "locales")

# 扫描范围：包目录 + 根目录单体
SCAN_FILES = []
for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "video_downloader")):
    if "__pycache__" in dirpath:
        continue
    for fn in filenames:
        if fn.endswith(".py"):
            SCAN_FILES.append(os.path.join(dirpath, fn))
SCAN_FILES.append(os.path.join(ROOT, "video_downloader.py"))

# t("...") 或 t_format("...")，key 为含中文的字符串字面量（支持 \' 等转义）
T_PAT = re.compile(r'\bt(?:_format)?\(\s*("(?:[^"\\\n]|\\.)*?")\s*[,)]')


def scan_keys():
    """扫描所有 t()/t_format() 的中文 key（求值为运行时字符串），返回有序去重列表。"""
    keys = set()
    for path in SCAN_FILES:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fp:
                content = fp.read()
        except Exception:
            continue
        for m in T_PAT.finditer(content):
            raw = m.group(1)
            if not re.search(r"[\u4e00-\u9fff]", raw):
                continue  # 非中文 key 不参与翻译
            try:
                keys.add(eval(raw))  # 求值为运行时字符串
            except Exception:
                keys.add(raw)
    return sorted(keys)


def load_table(lang):
    path = os.path.join(LOCALES, lang + ".json")
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser(description="i18n 翻译维护工具")
    ap.add_argument("--lang", default=None, help="只看某语言代码，如 en_US")
    ap.add_argument("--list-missing", action="store_true", help="列出缺失 key")
    ap.add_argument("--update-pending", action="store_true", help="重新生成 _pending_zh_keys.txt")
    args = ap.parse_args()

    keys = scan_keys()
    print(f"扫描到 {len(keys)} 个中文 key（{len(SCAN_FILES)} 个文件）\n")

    # 语言清单：以 i18n.py 的 SUPPORTED_LANGUAGES 为准
    i18n_path = os.path.join(ROOT, "i18n.py")
    lang_order = ["en_US", "zh_CN", "zh_TW", "ja_JP", "ko_KR", "es_ES", "fr_FR", "de_DE", "ru_RU"]
    with open(i18n_path, encoding="utf-8") as fp:
        i18n_src = fp.read()
    langs = [code for code in lang_order if code in i18n_src]

    if args.update_pending:
        pending = os.path.join(LOCALES, "_pending_zh_keys.txt")
        with open(pending, "w", encoding="utf-8") as fp:
            fp.write("\n".join(keys))
        print(f"已更新 {pending}")

    for lang in langs:
        table = load_table(lang)
        missing = [k for k in keys if k not in table]
        extra = [k for k in table if k not in keys]
        if args.lang and lang != args.lang:
            continue
        if lang == "zh_CN":
            # 中文原文即 key：zh_CN.json 恒为空，这是设计行为而非缺失
            print(f"[{lang}] 默认语言（无需翻译表，key=中文原文）")
            continue
        status = "完整" if not missing else f"缺 {len(missing)}"
        print(f"[{lang}] {status}  (key {len(table)}，多余 {len(extra)})")
        if args.list_missing and missing:
            for k in missing:
                print(f"    - {k}")
        if extra:
            print(f"    ! 多余 key（源码中已不存在）: {extra}")


if __name__ == "__main__":
    main()
