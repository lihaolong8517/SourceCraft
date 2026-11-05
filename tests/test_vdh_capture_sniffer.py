"""vdh_capture.sniffer 单测：CDP 事件聚合（用假 session/context，不启动浏览器）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vdh_capture.sniffer import CdpNetworkSniffer  # noqa: E402


class FakeSession:
    def __init__(self):
        self.handlers = {}
        self.send_calls = []
        self.detached = False

    def on(self, event, handler):
        self.handlers[event] = handler

    async def send(self, method, params=None):
        self.send_calls.append((method, params))

    async def detach(self):
        self.detached = True


class FakeContext:
    def __init__(self, session=None, fail_attach=False):
        self.session = session or FakeSession()
        self.fail_attach = fail_attach
        self.attach_calls = 0

    async def new_cdp_session(self, page):
        self.attach_calls += 1
        if self.fail_attach:
            raise RuntimeError("CDP 不可用")
        return self.session


def build_sniffer(records, page_url=""):
    sniffer = CdpNetworkSniffer(records.append, page_url_getter=lambda: page_url)
    return sniffer


def request_params(request_id="1", url="https://cdn.example.com/video.mp4", headers=None):
    return {
        "requestId": request_id,
        "request": {
            "url": url,
            "method": "GET",
            "headers": headers
            or {
                "Referer": "https://site.example/watch",
                "User-Agent": "UA/1.0",
                "Host": "cdn.example.com",
                "Range": "bytes=0-1024",
                "Cookie": "sid=abc",
            },
        },
        "type": "Media",
        "initiator": {"url": "https://site.example/watch"},
    }


def response_params(request_id="1", status=200, mime="video/mp4", length="2097152"):
    return {
        "requestId": request_id,
        "response": {
            "status": status,
            "mimeType": mime,
            "headers": {"content-length": length, "content-type": mime},
            "fromDiskCache": False,
            "fromServiceWorker": False,
        },
    }


class TestAttach:
    def test_attach_registers_handlers_and_enables_network(self):
        records = []
        session = FakeSession()
        context = FakeContext(session)
        sniffer = build_sniffer(records)

        ok = asyncio.run(sniffer.attach(context, object()))

        assert ok is True
        assert sniffer.attached is True
        assert set(session.handlers) == {
            "Network.requestWillBeSent",
            "Network.responseReceived",
            "Network.loadingFinished",
            "Network.loadingFailed",
        }
        assert session.send_calls[0][0] == "Network.enable"
        assert session.send_calls[0][1] == {
            "maxResourceBufferSize": 5 * 1024 * 1024,
            "maxTotalBufferSize": 20 * 1024 * 1024,
        }

    def test_attach_failure_returns_false(self):
        sniffer = build_sniffer([])
        assert asyncio.run(sniffer.attach(FakeContext(fail_attach=True), object())) is False
        assert sniffer.attached is False

    def test_attach_is_idempotent(self):
        context = FakeContext()
        sniffer = build_sniffer([])
        asyncio.run(sniffer.attach(context, object()))
        assert asyncio.run(sniffer.attach(context, object())) is True
        assert context.attach_calls == 1

    def test_detach_clears_state(self):
        session = FakeSession()
        sniffer = build_sniffer([])
        asyncio.run(sniffer.attach(FakeContext(session), object()))
        asyncio.run(sniffer.detach())
        assert sniffer.attached is False
        assert session.detached is True


class TestRecords:
    def test_full_response_emits_record(self):
        records = []
        session = FakeSession()
        sniffer = build_sniffer(records, page_url="https://site.example/watch")
        asyncio.run(sniffer.attach(FakeContext(session), object()))

        session.handlers["Network.requestWillBeSent"](request_params())
        session.handlers["Network.responseReceived"](response_params())
        session.handlers["Network.loadingFinished"](
            {"requestId": "1", "encodedDataLength": 2097152}
        )

        assert len(records) == 1
        record = records[0]
        assert record["url"] == "https://cdn.example.com/video.mp4"
        assert record["status"] == 200
        assert record["content_type"] == "video/mp4"
        assert record["size_bytes"] == 2097152
        assert record["page_url"] == "https://site.example/watch"
        assert record["initiator"] == "https://site.example/watch"
        assert record["resource_type"] == "media"
        # 白名单过滤：Referer/UA/Cookie 保留，Host/Range 丢弃
        assert record["request_headers"] == {
            "Referer": "https://site.example/watch",
            "User-Agent": "UA/1.0",
            "Cookie": "sid=abc",
        }

    def test_404_record_still_emitted(self):
        records = []
        session = FakeSession()
        sniffer = build_sniffer(records)
        asyncio.run(sniffer.attach(FakeContext(session), object()))
        session.handlers["Network.requestWillBeSent"](request_params())
        session.handlers["Network.responseReceived"](response_params(status=404, mime="text/html"))
        session.handlers["Network.loadingFinished"]({"requestId": "1", "encodedDataLength": 10})
        assert len(records) == 1
        assert records[0]["status"] == 404

    def test_size_falls_back_to_content_length(self):
        records = []
        session = FakeSession()
        sniffer = build_sniffer(records)
        asyncio.run(sniffer.attach(FakeContext(session), object()))
        session.handlers["Network.requestWillBeSent"](request_params())
        session.handlers["Network.responseReceived"](response_params())
        session.handlers["Network.loadingFinished"]({"requestId": "1"})
        assert records[0]["size_bytes"] == 2097152

    def test_from_cache_flag(self):
        records = []
        session = FakeSession()
        sniffer = build_sniffer(records)
        asyncio.run(sniffer.attach(FakeContext(session), object()))
        session.handlers["Network.requestWillBeSent"](request_params())
        params = response_params()
        params["response"]["fromDiskCache"] = True
        session.handlers["Network.responseReceived"](params)
        session.handlers["Network.loadingFinished"]({"requestId": "1", "encodedDataLength": 5})
        assert records[0]["from_cache"] is True

    def test_loading_failed_discards_pending(self):
        records = []
        session = FakeSession()
        sniffer = build_sniffer(records)
        asyncio.run(sniffer.attach(FakeContext(session), object()))
        session.handlers["Network.requestWillBeSent"](request_params())
        session.handlers["Network.loadingFailed"]({"requestId": "1"})
        session.handlers["Network.loadingFinished"]({"requestId": "1", "encodedDataLength": 5})
        assert records == []

    def test_non_http_request_ignored(self):
        records = []
        session = FakeSession()
        sniffer = build_sniffer(records)
        asyncio.run(sniffer.attach(FakeContext(session), object()))
        session.handlers["Network.requestWillBeSent"](request_params(url="data:video/mp4;base64,AA"))
        session.handlers["Network.responseReceived"](response_params())
        session.handlers["Network.loadingFinished"]({"requestId": "1", "encodedDataLength": 5})
        assert records == []

    def test_pending_is_bounded(self):
        records = []
        session = FakeSession()
        sniffer = CdpNetworkSniffer(records.append, max_pending=16)
        asyncio.run(sniffer.attach(FakeContext(session), object()))
        for index in range(20):
            session.handlers["Network.requestWillBeSent"](
                request_params(request_id=str(index), url=f"https://cdn.example.com/{index}.mp4")
            )
        # 最早的 4 条被挤出，finished 时不会产出
        for index in range(20):
            session.handlers["Network.loadingFinished"](
                {"requestId": str(index), "encodedDataLength": 1024}
            )
        assert len(records) == 16
        assert records[0]["url"] == "https://cdn.example.com/4.mp4"

    def test_callback_exception_does_not_propagate(self):
        def boom(_record):
            raise RuntimeError("下游失败")

        session = FakeSession()
        sniffer = CdpNetworkSniffer(boom)
        asyncio.run(sniffer.attach(FakeContext(session), object()))
        session.handlers["Network.requestWillBeSent"](request_params())
        session.handlers["Network.responseReceived"](response_params())
        session.handlers["Network.loadingFinished"]({"requestId": "1", "encodedDataLength": 5})  # 不抛


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
