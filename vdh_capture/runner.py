"""捕获编排器：CDP 嗅探 + 站点适配器 + 清单富化 + 候选表。

对应 VDH 的 ``service/main.js`` + 页面注入脚本两部分职责的本地化实现：

1. 网络层：CDP 事件聚合出“真实请求头 + 响应类型/长度”的记录，交给 bridge 分类；
2. 站点层：匹配到适配器时，导航后执行一次页面探测，直接读取页面配置里的清单/直链；
3. 候选层：统一进 MediaStore 去重/上限/覆盖淘汰，清单类候选异步拉取正文解析，
   补全清晰度、DRM、分片结构后再上报一次（force_update）。

本模块不依赖 Qt，也不改动现有运行代码；P2 阶段才由 ``playwright_browser.py`` 接入。
"""

from __future__ import annotations

import asyncio
import logging

from .bridge import (
    classify_record,
    normalize_report,
    safe_headers,
)
from .manifest import analyze_manifest_text, manifest_patch
from .sniffer import CdpNetworkSniffer
from .store import MediaStore

_LOG = logging.getLogger(__name__)

DEFAULT_MANIFEST_TIMEOUT = 15.0


class CaptureRunner:
    """一次页面会话的捕获编排器。"""

    def __init__(self, emit, store=None, adapters=None,
                 manifest_timeout=DEFAULT_MANIFEST_TIMEOUT,
                 binding_name="__vdhReport"):
        self._emit_cb = emit
        self.store = store if store is not None else MediaStore()
        self._adapters = adapters
        self._binding_name = binding_name
        self._manifest_timeout = float(manifest_timeout or DEFAULT_MANIFEST_TIMEOUT)
        self._sniffer = CdpNetworkSniffer(self._on_network_record)
        self._context = None
        self._page = None
        self._target_url = ""
        self._adapter = None
        self._tasks = set()
        self._started = False
        self.last_error = ""

    # ---------------- 生命周期 ----------------
    @property
    def adapter(self):
        return self._adapter

    @property
    def started(self):
        return self._started

    def set_target(self, url):
        """页面/目标地址变了，重新匹配站点适配器。"""
        self._target_url = str(url or "")
        self._adapter = self._select_adapter()

    async def start(self, context, page, target_url=""):
        """挂载 CDP 嗅探与站点绑定；返回 CDP 是否挂载成功。"""
        if self._started:
            return True
        self._started = True
        self._context = context
        self._page = page
        self.set_target(target_url or getattr(page, "url", "") or "")
        try:
            await context.expose_binding(self._binding_name, self._on_js_report)
        except Exception as exc:  # 同一页面重复绑定会抛错，忽略即可
            self.last_error = "expose_binding: %s" % exc
        attached = False
        try:
            attached = await self._sniffer.attach(context, page)
        except Exception as exc:
            self.last_error = "sniffer.attach: %s" % exc
        if self._adapter is not None and getattr(self._adapter, "INIT_JS", ""):
            try:
                await page.add_init_script(self._adapter.INIT_JS)
            except Exception:
                pass
        return attached

    async def after_navigation(self, page=None):
        """导航完成后调用：延迟执行站点探测，返回新增的候选。"""
        page = page if page is not None else self._page
        if page is None:
            return []
        try:
            self.set_target(getattr(page, "url", "") or self._target_url)
        except Exception:
            pass
        adapter = self._adapter
        if adapter is None:
            return []
        delay = float(getattr(adapter, "probe_delay_ms", 0) or 0) / 1000.0
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            candidates = await adapter.probe(page)
        except Exception as exc:
            self.last_error = "adapter.probe: %s" % exc
            return []
        emitted = []
        for candidate in adapter.normalize(candidates or []):
            item = dict(candidate)
            item.setdefault("page_url", getattr(page, "url", "") or "")
            entry = self._ingest(item, initiator="adapter:%s" % getattr(adapter, "name", "?"))
            if entry is not None:
                emitted.append(entry)
        return emitted

    async def stop(self):
        self._started = False
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        try:
            await self._sniffer.detach()
        except Exception:
            pass

    def snapshot(self):
        return self.store.snapshot()

    # ---------------- 内部 ----------------
    def _select_adapter(self):
        if self._adapters is not None:
            for adapter in self._adapters:
                try:
                    if adapter.matches(self._target_url):
                        return adapter
                except Exception:
                    continue
            return None
        try:
            from .adapters import get_adapter_for
        except Exception:
            return None
        try:
            return get_adapter_for(self._target_url)
        except Exception:
            return None

    def _page_url(self):
        try:
            return getattr(self._page, "url", "") or ""
        except Exception:
            return ""

    def _on_network_record(self, record):
        try:
            candidate = classify_record(record, page_url=record.get("page_url") or self._page_url())
        except Exception as exc:
            self.last_error = "classify_record: %s" % exc
            return
        if not candidate:
            return
        self._ingest(candidate, initiator=str(record.get("initiator") or ""), force=False)

    def _on_js_report(self, source, payload):
        if not isinstance(payload, dict):
            return
        try:
            candidate = normalize_report(payload, page_url=self._page_url())
        except Exception as exc:
            self.last_error = "normalize_report: %s" % exc
            return
        if not candidate:
            return
        candidate.setdefault("page_url", self._page_url())
        self._ingest(candidate, initiator="page-js", force=True)

    def _ingest(self, candidate, initiator="", force=False):
        try:
            result = self.store.add(candidate, initiator=initiator)
        except Exception as exc:
            self.last_error = "store.add: %s" % exc
            return None
        if result == "rejected":
            return None
        entry = self.store.get(str(candidate.get("url_key") or "") or str(candidate.get("url") or ""))
        if entry is None:
            return None
        if result in ("added", "updated") or force:
            self._emit(entry, force)
        if entry.get("is_manifest") and not (entry.get("manifest") or {}).get("variants"):
            self._schedule_enrich(entry)
        return entry

    def _schedule_enrich(self, entry):
        url = str(entry.get("url") or "")
        if not url:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # 没有事件循环（纯同步测试场景）时跳过富化
            return
        task = loop.create_task(
            self._enrich_manifest(url, dict(entry.get("headers") or {}), str(entry.get("url_key") or ""))
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _enrich_manifest(self, url, headers, url_key):
        if self._context is None or not url_key:
            return
        try:
            text = await asyncio.wait_for(self._fetch_text(url, headers), timeout=self._manifest_timeout)
        except Exception as exc:
            self.last_error = "manifest fetch: %s" % exc
            return
        if not text:
            return
        try:
            analysis = analyze_manifest_text(text, url)
        except Exception as exc:
            self.last_error = "manifest parse: %s" % exc
            return
        if not analysis:
            return
        try:
            patch = manifest_patch(analysis)
        except Exception as exc:
            self.last_error = "manifest patch: %s" % exc
            return
        if not patch:
            return
        entry = self.store.update(url_key, patch)
        if entry is not None:
            self._emit(entry, True)

    async def _fetch_text(self, url, headers):
        response = await self._context.request.get(
            url,
            headers=safe_headers(headers),
            timeout=self._manifest_timeout * 1000.0,
            fail_on_status_code=False,
        )
        try:
            return await response.text()
        finally:
            try:
                await response.dispose()
            except Exception:
                pass

    def _emit(self, candidate, force):
        try:
            self._emit_cb(dict(candidate), bool(force))
        except Exception as exc:
            self.last_error = "emit: %s" % exc
