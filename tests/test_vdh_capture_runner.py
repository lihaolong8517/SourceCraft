"""CaptureRunner / 站点适配器单元测试（不启动浏览器）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vdh_capture.adapters import SITE_ADAPTERS, get_adapter_for
from vdh_capture.adapters.base import SiteAdapter
from vdh_capture.adapters.vimeo import VimeoAdapter
from vdh_capture.runner import CaptureRunner
from vdh_capture.store import MediaStore


MASTER_M3U8 = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="en",DEFAULT=YES,URI="audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2",AUDIO="aud"
v720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4800000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",AUDIO="aud"
v1080.m3u8
"""


class FakeResponse:
    def __init__(self, text):
        self._text = text
        self.disposed = False

    async def text(self):
        return self._text

    async def dispose(self):
        self.disposed = True


class FakeRequest:
    def __init__(self, text=""):
        self.text = text
        self.calls = []

    async def get(self, url, headers=None, timeout=None, fail_on_status_code=False):
        self.calls.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        return FakeResponse(self.text)


class FakeContext:
    def __init__(self, text=""):
        self.request = FakeRequest(text)


def _candidate(url="https://cdn.example.com/master.m3u8", **extra):
    data = {
        "url": url,
        "url_key": url,
        "type": "HLS",
        "is_manifest": True,
        "size_bytes": 2048,
        "headers": {"Referer": "https://example.com/watch"},
    }
    data.update(extra)
    return data


class TestAdapterRegistry:
    def test_registry_discovers_vimeo(self):
        names = [adapter.name for adapter in SITE_ADAPTERS]
        assert "vimeo" in names

    def test_get_adapter_for(self):
        assert isinstance(get_adapter_for("https://vimeo.com/12345"), VimeoAdapter)
        assert get_adapter_for("https://example.com/video") is None


class TestSiteAdapterNormalize:
    def test_normalize_accepts_strings_and_dicts(self):
        adapter = SiteAdapter()
        adapter.name = "test"
        result = adapter.normalize([
            "https://cdn.example.com/a.m3u8",
            {"url": "https://cdn.example.com/b.mp4", "quality": {"height": 1080}},
            {"url": "blob:https://example.com/x"},
            "not-a-url",
        ])
        assert len(result) == 2
        assert result[0]["type"] == "Media"
        assert result[0]["source"] == "site"
        assert result[1]["quality"]["height"] == 1080

    def test_normalize_handles_none_and_scalars(self):
        adapter = SiteAdapter()
        assert adapter.normalize(None) == []
        assert adapter.normalize(123) == []

    def test_vimeo_matches_only_vimeo(self):
        adapter = VimeoAdapter()
        assert adapter.matches("https://vimeo.com/123")
        assert not adapter.matches("https://example.com/vimeo.com")
        assert not adapter.matches("")


class TestIngest:
    def _runner(self):
        emitted = []
        runner = CaptureRunner(emit=lambda candidate, force: emitted.append((candidate, force)),
                               store=MediaStore())
        return runner, emitted

    def test_manifest_candidate_emitted_then_updated(self):
        runner, emitted = self._runner()
        entry = runner._ingest(_candidate(), initiator="cdp")
        assert entry is not None
        assert len(emitted) == 1
        assert emitted[0][1] is False
        runner._ingest(_candidate(has_drm=True), initiator="cdp")
        assert len(emitted) == 2
        assert emitted[1][0]["has_drm"] is True
        assert runner.store.get("https://cdn.example.com/master.m3u8")["has_drm"] is True

    def test_rejected_candidate_not_emitted(self):
        runner, emitted = self._runner()
        assert runner._ingest(_candidate(url="blob:https://example.com/1")) is None
        assert emitted == []

    def test_duplicate_candidate_not_emitted(self):
        runner, emitted = self._runner()
        runner.store.add(_candidate(), initiator="cdp")
        runner._ingest(_candidate(), initiator="cdp")
        assert len(emitted) == 1

    def test_reset_allows_recapture_without_page_reload(self):
        """清空捕获记录后，同一个媒体再次出现要能重新上报。"""
        runner, emitted = self._runner()
        runner._ingest(_candidate(), initiator="cdp")
        assert len(emitted) == 1
        assert len(runner.snapshot()) == 1

        runner.reset()

        assert runner.snapshot() == []
        entry = runner._ingest(_candidate(), initiator="cdp")
        assert entry is not None
        assert len(emitted) == 2

    def test_reset_cancels_pending_enrichment(self):
        runner, _ = self._runner()
        runner._ingest(_candidate(), initiator="cdp")
        assert len(runner.snapshot()) == 1
        runner.reset()
        assert runner.snapshot() == []
        assert not runner._tasks


class TestManifestEnrichment:
    def test_enrich_manifest_updates_quality(self):
        emitted = []
        runner = CaptureRunner(emit=lambda candidate, force: emitted.append((candidate, force)),
                               store=MediaStore())
        runner._context = FakeContext(MASTER_M3U8)
        entry = runner._ingest(_candidate(url="https://cdn.example.com/master.m3u8"), initiator="cdp")
        assert entry is not None
        asyncio.run(runner._enrich_manifest(entry["url"], entry["headers"], entry["url_key"]))
        assert len(emitted) == 2
        candidate, force = emitted[1]
        assert force is True
        assert candidate["manifest"]["variants"]
        assert candidate["quality"]["resolution"] == "1080p"

    def test_enrich_manifest_ignores_non_manifest_text(self):
        emitted = []
        runner = CaptureRunner(emit=lambda candidate, force: emitted.append((candidate, force)),
                               store=MediaStore())
        runner._context = FakeContext("<html>not a manifest</html>")
        entry = runner._ingest(_candidate(url="https://cdn.example.com/master.m3u8"), initiator="cdp")
        asyncio.run(runner._enrich_manifest(entry["url"], entry["headers"], entry["url_key"]))
        assert len(emitted) == 1


class TestAfterNavigation:
    class ProbeAdapter(SiteAdapter):
        name = "probe"
        hosts = ("probe.test",)
        PROBE_JS = "() => []"
        probe_delay_ms = 0

        async def probe(self, page):
            return [{"url": "https://cdn.probe.test/stream.m3u8", "type": "HLS", "is_manifest": True}]

    class FakePage:
        url = "https://probe.test/watch"

    def test_after_navigation_ingests_probe_results(self):
        emitted = []
        runner = CaptureRunner(emit=lambda candidate, force: emitted.append((candidate, force)),
                               store=MediaStore(), adapters=[self.ProbeAdapter()])
        runner.set_target(self.FakePage.url)
        result = asyncio.run(runner.after_navigation(self.FakePage()))
        assert len(result) == 1
        assert len(emitted) == 1
        assert emitted[0][0]["url"] == "https://cdn.probe.test/stream.m3u8"
        assert emitted[0][0]["source"] == "site"

    def test_after_navigation_without_adapter_is_noop(self):
        runner = CaptureRunner(emit=lambda candidate, force: None, store=MediaStore(), adapters=[])
        runner.set_target("https://unknown.test/")
        assert asyncio.run(runner.after_navigation(TestAfterNavigation.FakePage())) == []
