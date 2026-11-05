"""vdh_capture.bridge 单测：分类、URL 去重键、请求头回放。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vdh_capture.bridge import (  # noqa: E402
    build_header_args,
    classify_record,
    describe_candidate,
    download_headers,
    human_size,
    normalize_report,
    normalize_url_key,
)


def record(**overrides) -> dict:
    base = {
        "url": "https://cdn.example.com/video.mp4",
        "status": 200,
        "content_type": "video/mp4",
        "size_bytes": 2 * 1024 * 1024,
        "request_headers": {"Referer": "https://site.example/watch", "User-Agent": "UA/1.0"},
        "page_url": "https://site.example/watch",
    }
    base.update(overrides)
    return base


class TestNormalizeUrlKey:
    def test_drops_volatile_params(self):
        key = normalize_url_key(
            "https://cdn.example.com/video.mp4?expire=123&ei=abc&sig=xyz&itag=22"
        )
        assert key == "https://cdn.example.com/video.mp4?itag=22"

    def test_sorts_query(self):
        assert normalize_url_key("https://x.example/v?b=2&a=1") == normalize_url_key(
            "https://x.example/v?a=1&b=2"
        )

    def test_drops_fragment_and_lowercases_host(self):
        key = normalize_url_key("HTTPS://CDN.Example.com/Path/V.mp4#t=10")
        assert key == "https://cdn.example.com/Path/V.mp4"

    def test_invalid_schemes_return_empty(self):
        assert normalize_url_key("blob:https://example.com/uuid") == ""
        assert normalize_url_key("data:video/mp4;base64,AAAA") == ""
        assert normalize_url_key("") == ""
        assert normalize_url_key(None) == ""


class TestHeaders:
    def test_whitelist_and_canonical_names(self):
        headers = download_headers(
            {
                "referer": "https://site.example/",
                "user-agent": "UA/2.0",
                "host": "cdn.example.com",
                "range": "bytes=0-100",
                "cookie": "sid=1",
                "x-playback-session-id": "abc",
            }
        )
        assert headers == {
            "Referer": "https://site.example/",
            "User-Agent": "UA/2.0",
            "Cookie": "sid=1",
            "X-Playback-Session-Id": "abc",
        }

    def test_crlf_injection_dropped(self):
        headers = download_headers({"Referer": "https://a/\r\nX-Evil: 1", "User-Agent": "ok"})
        assert headers == {"User-Agent": "ok"}

    def test_build_header_args(self):
        args = build_header_args({"Referer": "https://site.example/", "User-Agent": "UA/2.0"})
        assert args == [
            "--add-header",
            "Referer: https://site.example/",
            "--add-header",
            "User-Agent: UA/2.0",
        ]


class TestClassify:
    def test_mp4_direct(self):
        candidate = classify_record(record())
        assert candidate is not None
        assert candidate["type"] == "MP4"
        assert candidate["is_manifest"] is False
        assert candidate["audio_only"] is False
        assert candidate["headers"]["Referer"] == "https://site.example/watch"
        assert candidate["size_bytes"] == 2 * 1024 * 1024

    def test_hls_by_extension(self):
        candidate = classify_record(record(url="https://cdn.example.com/master.m3u8",
                                           content_type="application/vnd.apple.mpegurl",
                                           size_bytes=0))
        assert candidate["type"] == "HLS"
        assert candidate["is_manifest"] is True

    def test_dash_by_content_type(self):
        candidate = classify_record(record(url="https://cdn.example.com/manifest",
                                           content_type="application/dash+xml",
                                           size_bytes=0))
        assert candidate["type"] == "DASH"
        assert candidate["is_manifest"] is True

    def test_audio_only(self):
        candidate = classify_record(record(url="https://cdn.example.com/audio.m4a",
                                           content_type="audio/mp4",
                                           size_bytes=1024 * 1024))
        assert candidate["audio_only"] is True
        assert candidate["type"] == "Audio"

    def test_tiny_audio_dropped(self):
        # 小于 8192B 的碎音频（音效、提示音）一律不收
        assert classify_record(record(url="https://cdn.example.com/beep.m4a",
                                      content_type="audio/mp4",
                                      size_bytes=1024)) is None

    def test_fragment_dropped(self):
        assert classify_record(record(url="https://cdn.example.com/seg-1.ts")) is None
        assert classify_record(record(url="https://cdn.example.com/seg-1.m4s")) is None

    def test_404_dropped(self):
        assert classify_record(record(status=404)) is None

    def test_blob_dropped(self):
        assert classify_record(record(url="blob:https://example.com/uuid")) is None

    def test_icon_dropped(self):
        assert classify_record(record(url="https://cdn.example.com/favicon.ico",
                                      content_type="image/x-icon", size_bytes=4096)) is None

    def test_tiny_non_video_dropped(self):
        assert classify_record(record(url="https://cdn.example.com/garbage",
                                      content_type="application/octet-stream",
                                      size_bytes=1024)) is None

    def test_tiny_video_kept_for_store_to_filter(self):
        candidate = classify_record(record(size_bytes=100 * 1024))
        assert candidate is not None
        assert candidate["type"] == "MP4"

    def test_page_url_fallback(self):
        candidate = classify_record(record(page_url=""), page_url="https://fallback.example/")
        assert candidate["page_url"] == "https://fallback.example/"


class TestReport:
    def test_normalize_report_fills_defaults(self):
        report = normalize_report(
            {"url": "https://vimeo.com/123", "type": "HLS", "headers": {"referer": "https://vimeo.com/"}}
        )
        assert report["url_key"] == "https://vimeo.com/123"
        assert report["source"] == "site"
        assert report["headers"] == {"Referer": "https://vimeo.com/"}
        assert report["summary"].startswith("HLS")

    def test_normalize_report_rejects_bad_url(self):
        assert normalize_report({"url": "javascript:alert(1)"}) == {}
        assert normalize_report({"url": ""}) == {}


class TestDescribe:
    def test_describe_with_quality(self):
        text = describe_candidate(
            {"type": "HLS", "quality": {"resolution": "1080p", "bandwidth": 5000000}}
        )
        assert text == "HLS · 1080p · 5000Kbps"

    def test_describe_with_drm(self):
        text = describe_candidate({"type": "DASH", "has_drm": True})
        assert text == "DASH · DRM"

    def test_human_size(self):
        assert human_size(0) == ""
        assert human_size(512) == "512 B"
        assert human_size(2 * 1024 * 1024) == "2.0 MB"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
