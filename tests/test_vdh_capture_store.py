"""vdh_capture.store 单测：去重、合并、覆盖与淘汰规则。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vdh_capture.store import MediaStore, StoreEntry  # noqa: E402


def make_mp4(url: str = "https://cdn.example.com/video.mp4", size: int = 2 * 1024 * 1024) -> dict:
    return {
        "type": "MP4",
        "url": url,
        "url_key": url,
        "size_bytes": size,
        "size": "2.0 MB",
        "content_type": "video/mp4",
        "is_manifest": False,
    }


def make_hls(url: str = "https://cdn.example.com/master.m3u8") -> dict:
    return {
        "type": "HLS",
        "url": url,
        "url_key": url,
        "content_type": "application/vnd.apple.mpegurl",
        "is_manifest": True,
        "size_bytes": 0,
    }


class TestAddRules:
    def test_add_direct_media(self):
        store = MediaStore()
        assert store.add(make_mp4()) == "added"
        assert len(store) == 1
        assert store.get("https://cdn.example.com/video.mp4")["type"] == "MP4"

    def test_fragment_rejected(self):
        store = MediaStore()
        assert store.add(make_mp4("https://cdn.example.com/seg-1.ts")) == "rejected"
        assert store.add(make_mp4("https://cdn.example.com/seg-1.m4s")) == "rejected"
        assert len(store) == 0

    def test_small_non_manifest_rejected(self):
        store = MediaStore()
        candidate = make_mp4(size=200 * 1024)
        candidate["size"] = "200.0 KB"
        assert store.add(candidate) == "rejected"

    def test_manifest_always_accepted(self):
        store = MediaStore()
        assert store.add(make_hls("https://cdn.example.com/playlist.m3u8")) == "added"

    def test_inline_urls_rejected(self):
        store = MediaStore()
        assert store.add({"url": "blob:https://example.com/uuid"}) == "rejected"
        assert store.add({"url": "data:video/mp4;base64,AAAA"}) == "rejected"

    def test_missing_url_rejected(self):
        store = MediaStore()
        assert store.add({"type": "MP4"}) == "rejected"
        assert store.add("not-a-dict") == "rejected"


class TestMergeRules:
    def test_duplicate_merges_headers_and_quality(self):
        store = MediaStore()
        first = make_mp4()
        first["headers"] = {"Referer": "https://site.example/"}
        store.add(first)
        second = make_mp4()
        second["headers"] = {"User-Agent": "UA/1.0"}
        second["quality"] = {"resolution": "1080p", "height": 1080}
        assert store.add(second) == "updated"
        entry = store.get(second["url"])
        assert entry["headers"] == {"Referer": "https://site.example/", "User-Agent": "UA/1.0"}
        assert entry["quality"]["resolution"] == "1080p"
        assert len(store) == 1

    def test_has_drm_is_monotonic(self):
        store = MediaStore()
        store.add(make_hls())
        store.update(make_hls()["url"], {"has_drm": True})
        store.update(make_hls()["url"], {"has_drm": False})
        assert store.get(make_hls()["url"])["has_drm"] is True

    def test_audio_only_is_monotonic(self):
        store = MediaStore()
        store.add(make_hls())
        store.update(make_hls()["url"], {"audio_only": True})
        assert store.get(make_hls()["url"])["audio_only"] is True

    def test_update_missing_returns_none(self):
        assert MediaStore().update("https://nope.example/x.mp4", {"has_drm": True}) is None


class TestCoverage:
    def test_variant_of_known_manifest_is_duplicate(self):
        store = MediaStore()
        manifest = make_hls("https://cdn.example.com/master.m3u8")
        manifest["manifest"] = {
            "variants": [{"url": "https://cdn.example.com/1080p/index.m3u8", "bandwidth": 5000000}]
        }
        store.add(manifest)
        variant = make_hls("https://cdn.example.com/1080p/index.m3u8")
        assert store.add(variant) == "duplicate"
        assert len(store) == 1

    def test_manifest_shadows_earlier_variant_entries(self):
        store = MediaStore()
        variant = make_hls("https://cdn.example.com/1080p/index.m3u8")
        variant["size_bytes"] = 1024
        store.add(variant)
        assert len(store) == 1
        manifest = make_hls("https://cdn.example.com/master.m3u8")
        manifest["manifest"] = {"variants": [{"url": "https://cdn.example.com/1080p/index.m3u8"}]}
        store.add(manifest)
        assert len(store) == 1
        assert store.get("https://cdn.example.com/master.m3u8") is not None


class TestLimit:
    def test_fifo_limit(self):
        store = MediaStore(max_items=3)
        for index in range(5):
            store.add(make_mp4(f"https://cdn.example.com/v{index}.mp4"))
        assert len(store) == 3
        keys = store.keys()
        assert "https://cdn.example.com/v0.mp4" not in keys
        assert "https://cdn.example.com/v4.mp4" in keys

    def test_re_add_moves_to_end(self):
        store = MediaStore(max_items=2)
        store.add(make_mp4("https://cdn.example.com/a.mp4"))
        store.add(make_mp4("https://cdn.example.com/b.mp4"))
        store.add(make_mp4("https://cdn.example.com/a.mp4"))
        store.add(make_mp4("https://cdn.example.com/c.mp4"))
        assert "https://cdn.example.com/b.mp4" not in store.keys()
        assert "https://cdn.example.com/a.mp4" in store.keys()


class TestUtilities:
    def test_snapshot_is_copy(self):
        store = MediaStore()
        store.add(make_mp4())
        snapshot = store.snapshot()
        snapshot[0]["url"] = "mutated"
        assert store.get(make_mp4()["url"])["url"] == make_mp4()["url"]

    def test_clear_and_remove(self):
        store = MediaStore()
        store.add(make_mp4())
        assert store.remove(make_mp4()["url"]) is True
        assert len(store) == 0
        store.add(make_mp4())
        store.clear()
        assert len(store) == 0

    def test_drop_where(self):
        store = MediaStore()
        store.add(make_mp4("https://cdn.example.com/a.mp4"))
        store.add(make_hls("https://cdn.example.com/b.m3u8"))
        dropped = store.drop_where(lambda entry: entry.get("is_manifest"))
        assert dropped == 1
        assert len(store) == 1

    def test_store_entry_properties(self):
        entry = StoreEntry({"url": "https://x.example/v.mp4", "is_manifest": False})
        assert entry.url == "https://x.example/v.mp4"
        assert entry.url_key == "https://x.example/v.mp4"
        assert entry.is_manifest is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
