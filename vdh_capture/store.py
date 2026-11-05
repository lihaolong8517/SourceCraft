"""媒体候选存储：去重、合并、覆盖与淘汰。

对齐 VDH（Video Download Helper）的媒体表语义：
  * 分片（.ts/.m4s/...）一律不收；
  * 非清单类资源小于 500KB 不收（图标、雪碧图、预览小片段）；
  * 同一 URL（忽略签名/时效参数）只保留一条，重复出现时合并请求头与清晰度；
  * 已被清单覆盖的重复源会被合并掉；
  * 表有上限（默认 30 条），超出后按 FIFO 淘汰最旧的一条。

本模块不依赖 Qt / Playwright，可独立单测。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from urllib.parse import urlsplit

DEFAULT_MAX_ITEMS = 30
DEFAULT_MIN_DIRECT_BYTES = 500 * 1024
DEFAULT_DUP_SIZE_THRESHOLD = 20 * 1024 * 1024

FRAGMENT_EXTS = (".ts", ".m4s", ".m2ts", ".cmfv", ".cmfa", ".m4v")
MANIFEST_EXTS = (".m3u8", ".mpd", ".ism", ".ismc", ".f4m")


def _url_ext(url):
    try:
        path = urlsplit(url).path
    except (TypeError, ValueError):
        return ""
    dot = path.rfind(".")
    return path[dot:].lower() if dot >= 0 else ""


def _merge_headers(old, new):
    merged = dict(old or {})
    for key, value in (new or {}).items():
        if value:
            merged[key] = value
    return merged


def _merge_quality(old, new):
    merged = dict(old or {})
    for key, value in (new or {}).items():
        if value not in (None, "", 0):
            merged[key] = value
    return merged


class StoreEntry(dict):
    """一条候选媒体（dict 语义 + initiator 来源标记）。"""

    def __init__(self, candidate, initiator=""):
        super().__init__(candidate)
        self.initiator = initiator or ""

    @property
    def url(self):
        return self.get("url", "")

    @property
    def url_key(self):
        return self.get("url_key", "") or self.url

    @property
    def is_manifest(self):
        return bool(self.get("is_manifest"))


class MediaStore:
    """有界、去重、可合并的媒体候选表。"""

    def __init__(self, max_items=DEFAULT_MAX_ITEMS,
                 min_direct_bytes=DEFAULT_MIN_DIRECT_BYTES,
                 dup_size_threshold=DEFAULT_DUP_SIZE_THRESHOLD):
        self.max_items = max(1, int(max_items))
        self.min_direct_bytes = max(0, int(min_direct_bytes))
        self.dup_size_threshold = max(0, int(dup_size_threshold))
        self._items = OrderedDict()
        self._lock = threading.RLock()

    # ---------------- 查询 ----------------
    def __len__(self):
        return len(self._items)

    def __contains__(self, url_key):
        return url_key in self._items

    def __iter__(self):
        return iter(self.items())

    def keys(self):
        with self._lock:
            return list(self._items.keys())

    def items(self):
        with self._lock:
            return list(self._items.values())

    def get(self, url_key):
        with self._lock:
            return self._items.get(url_key)

    def snapshot(self):
        with self._lock:
            return [dict(entry) for entry in self._items.values()]

    # ---------------- 写入 ----------------
    def add(self, candidate, initiator=""):
        """返回 added / updated / duplicate / rejected。"""
        if not isinstance(candidate, dict):
            return "rejected"
        url = str(candidate.get("url") or "").strip()
        if not url:
            return "rejected"
        url_key = str(candidate.get("url_key") or "") or url
        if self._reject_reason(candidate, url):
            return "rejected"
        with self._lock:
            existing = self._items.get(url_key)
            if existing is not None:
                self._merge_into(existing, candidate, initiator)
                self._items.move_to_end(url_key)
                return "updated"
            # 清单 variants 里已经出现过的 URL（哪怕它本身也是清单）视为重复：
            # 对齐 VDH “插入 master playlist 后删除其变体” 的语义。
            if url in self._covered_urls():
                return "duplicate"
            entry = StoreEntry(dict(candidate), initiator=initiator)
            self._items[url_key] = entry
            if entry.is_manifest:
                self._drop_shadowed(entry)
            self._enforce_limit()
            return "added"

    def update(self, url_key, patch):
        if not isinstance(patch, dict):
            return None
        with self._lock:
            entry = self._items.get(url_key)
            if entry is None:
                return None
            self._merge_into(entry, patch, entry.initiator)
            return entry

    def remove(self, url_key):
        with self._lock:
            return self._items.pop(url_key, None) is not None

    def drop_where(self, predicate):
        with self._lock:
            doomed = [key for key, entry in self._items.items() if predicate(entry)]
            for key in doomed:
                self._items.pop(key, None)
            return len(doomed)

    def clear(self):
        with self._lock:
            self._items.clear()

    # ---------------- 内部 ----------------
    def _reject_reason(self, candidate, url):
        low = url.lower()
        if low.startswith(("data:", "blob:", "filesystem:")):
            return "inline-url"
        ext = _url_ext(url)
        if ext in FRAGMENT_EXTS and not candidate.get("is_manifest"):
            return "fragment"
        if candidate.get("is_manifest"):
            return ""
        try:
            size = int(candidate.get("size_bytes") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and size < self.min_direct_bytes:
            return "too-small"
        return ""

    def _merge_into(self, entry, candidate, initiator):
        if candidate.get("headers"):
            entry["headers"] = _merge_headers(entry.get("headers"), candidate.get("headers"))
        if candidate.get("quality"):
            entry["quality"] = _merge_quality(entry.get("quality"), candidate.get("quality"))
        if candidate.get("manifest"):
            entry["manifest"] = dict(candidate["manifest"])
        if candidate.get("has_drm"):
            entry["has_drm"] = True
        if candidate.get("audio_only"):
            entry["audio_only"] = True
        for key in ("size_bytes", "content_type", "summary", "title", "page_url", "thumbnail"):
            value = candidate.get(key)
            if value not in (None, "", 0):
                entry[key] = value
        if candidate.get("size_bytes"):
            entry["size"] = candidate.get("size") or entry.get("size", "")
        if candidate.get("type") and entry.get("type") in (None, "", "Media"):
            entry["type"] = candidate["type"]
        if initiator and not entry.initiator:
            entry.initiator = initiator

    def _covered_urls(self):
        covered = set()
        for entry in self._items.values():
            manifest = entry.get("manifest") or {}
            for item in manifest.get("variants") or []:
                url = item.get("url") if isinstance(item, dict) else item
                if url:
                    covered.add(url)
        return covered

    def _drop_shadowed(self, manifest_entry):
        manifest = manifest_entry.get("manifest") or {}
        variant_urls = set()
        for item in manifest.get("variants") or []:
            url = item.get("url") if isinstance(item, dict) else item
            if url:
                variant_urls.add(url)
        if not variant_urls:
            return
        doomed = [key for key, entry in self._items.items()
                  if entry is not manifest_entry
                  and entry.get("url") in variant_urls
                  and int(entry.get("size_bytes") or 0) <= self.dup_size_threshold]
        for key in doomed:
            self._items.pop(key, None)

    def _enforce_limit(self):
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
