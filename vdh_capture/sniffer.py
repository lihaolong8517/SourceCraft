"""CDP 网络嗅探：把页面所有请求聚合成统一的记录。

与 ``page.on('response')`` 相比，CDP 的 ``Network.*`` 事件能拿到**真实请求头**
（Referer / Cookie / User-Agent），这是后续 yt-dlp 回放与防盗链站点下载的关键。

本模块不直接依赖 Playwright 类型：只要传入的对象提供
``new_cdp_session(page)`` 与 CDP ``session.send/on/detach`` 即可，便于单测。
"""

from __future__ import annotations

from collections import OrderedDict

from .bridge import download_headers

DEFAULT_MAX_PENDING = 800


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class CdpNetworkSniffer:
    """订阅一个页面目标的 Network 事件，产出精简后的记录。"""

    def __init__(self, on_record, page_url_getter=None, max_pending: int = DEFAULT_MAX_PENDING):
        self._on_record = on_record
        self._page_url_getter = page_url_getter
        self.max_pending = max(16, int(max_pending))
        self._session = None
        self._pending: OrderedDict[str, dict] = OrderedDict()
        self._attached = False

    # ------------------------------------------------------------------ 生命周期
    @property
    def attached(self) -> bool:
        return self._attached

    async def attach(self, context, page) -> bool:
        if self._attached:
            return True
        try:
            session = await context.new_cdp_session(page)
        except Exception:
            return False
        self._session = session
        session.on("Network.requestWillBeSent", self._on_request)
        session.on("Network.responseReceived", self._on_response)
        session.on("Network.loadingFinished", self._on_finished)
        session.on("Network.loadingFailed", self._on_failed)
        try:
            await session.send(
                "Network.enable",
                {"maxResourceBufferSize": 5 * 1024 * 1024,
                 "maxTotalBufferSize": 20 * 1024 * 1024},
            )
        except Exception:
            # 老版本 Chromium 不接受这些参数，退回最简形式
            try:
                await session.send("Network.enable")
            except Exception:
                return False
        self._attached = True
        return True

    async def detach(self) -> None:
        session, self._session = self._session, None
        self._attached = False
        self._pending.clear()
        if session is not None:
            try:
                await session.detach()
            except Exception:
                pass

    # ------------------------------------------------------------------ 事件
    def _page_url(self) -> str:
        if self._page_url_getter is None:
            return ""
        try:
            return self._page_url_getter() or ""
        except Exception:
            return ""

    def _on_request(self, params: dict) -> None:
        request_id = params.get("requestId")
        request = params.get("request") or {}
        url = request.get("url") or ""
        if not request_id or not url.lower().startswith(("http://", "https://")):
            return
        if len(self._pending) >= self.max_pending:
            self._pending.popitem(last=False)
        initiator = (params.get("initiator") or {}).get("url") or ""
        self._pending[request_id] = {
            "url": url,
            "method": request.get("method") or "GET",
            "request_headers": download_headers(request.get("headers") or {}),
            "resource_type": (params.get("type") or "").lower(),
            "initiator": initiator,
            "page_url": self._page_url(),
            "status": 0,
            "content_type": "",
            "content_length": 0,
        }

    def _on_response(self, params: dict) -> None:
        record = self._pending.get(params.get("requestId"))
        if record is None:
            return
        response = params.get("response") or {}
        record["status"] = _to_int(response.get("status"))
        record["content_type"] = (response.get("mimeType") or "").split(";")[0].strip()
        headers = response.get("headers") or {}
        length = 0
        for key, value in headers.items():
            if str(key).lower() == "content-length":
                length = _to_int(value)
                break
        if length:
            record["content_length"] = length
        record["from_cache"] = bool(response.get("fromDiskCache") or response.get("fromServiceWorker"))

    def _on_finished(self, params: dict) -> None:
        record = self._pending.pop(params.get("requestId"), None)
        if record is None:
            return
        size = _to_int(params.get("encodedDataLength")) or record.get("content_length") or 0
        self._emit(record, size)

    def _on_failed(self, params: dict) -> None:
        self._pending.pop(params.get("requestId"), None)

    # ------------------------------------------------------------------ 产出
    def _emit(self, record: dict, size_bytes: int) -> None:
        payload = {
            "url": record.get("url", ""),
            "request_headers": record.get("request_headers") or {},
            "status": record.get("status", 0),
            "content_type": record.get("content_type", ""),
            "content_length": record.get("content_length", 0),
            "size_bytes": size_bytes or 0,
            "resource_type": record.get("resource_type", ""),
            "method": record.get("method", "GET"),
            "initiator": record.get("initiator", ""),
            "page_url": record.get("page_url", ""),
            "from_cache": bool(record.get("from_cache")),
        }
        try:
            self._on_record(payload)
        except Exception:
            # 单条记录处理失败不应打断整个嗅探
            pass
