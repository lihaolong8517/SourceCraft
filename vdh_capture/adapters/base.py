"""站点适配器基类。

对齐 VDH 的“注入脚本 + 页面配置读取”思路：

* ``INIT_JS`` 在 document_start 注入主世界，用于挂接站点自身的播放逻辑；
* ``PROBE_JS`` 是一个返回候选列表的表达式，导航完成后再执行一次，
  从页面全局变量里直接读出清单/直链（比纯嗅探更精确，能拿到没有网络请求的清单）。

适配器只做“读”，不下载、不破解；DRM 只标注不绕过。
"""

from __future__ import annotations

from urllib.parse import urlsplit


class SiteAdapter:
    """一个站点的媒体发现逻辑。"""

    name = "base"
    hosts = ()
    INIT_JS = ""
    PROBE_JS = ""
    probe_delay_ms = 800

    def matches(self, url) -> bool:
        """按主机名匹配（子域算命中），避免 URL 里带站名就误判。"""
        text = str(url or "")
        if not text:
            return False
        try:
            host = (urlsplit(text).hostname or "").lower()
        except Exception:
            return False
        if not host:
            return False
        for item in self.hosts:
            item = str(item or "").lower().strip()
            if not item:
                continue
            if host == item or host.endswith("." + item):
                return True
        return False

    async def probe(self, page) -> list:
        """执行 PROBE_JS 并把结果规范化为候选列表。"""
        if not self.PROBE_JS:
            return []
        try:
            raw = await page.evaluate(self.PROBE_JS)
        except Exception:
            return []
        return self.normalize(raw)

    def normalize(self, raw) -> list:
        if raw is None:
            return []
        if isinstance(raw, (str, dict)):
            items = [raw]
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            return []
        candidates = []
        for item in items:
            if isinstance(item, str):
                candidate = {"url": item}
            elif isinstance(item, dict):
                candidate = dict(item)
            else:
                continue
            url = str(candidate.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            candidate["url"] = url
            candidate.setdefault("type", "Media")
            candidate.setdefault("source", "site")
            candidate.setdefault("adapter", self.name)
            candidate.setdefault("page_url", "")
            candidates.append(candidate)
        return candidates
