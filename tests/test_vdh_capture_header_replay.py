"""P4 请求头回放：参数构造的单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vdh_capture.bridge import captured_header_args  # noqa: E402


def _args_to_pairs(args):
    pairs = {}
    items = list(args)
    for index in range(0, len(items) - 1, 2):
        if items[index] == "--add-header":
            name, _, value = items[index + 1].partition(": ")
            pairs[name.lower()] = value
    return pairs


class TestCapturedHeaderArgs:
    def test_replays_referer_and_cookie(self):
        args = captured_header_args({
            "Referer": "https://example.com/watch",
            "Cookie": "sid=abc; uid=1",
            "User-Agent": "Mozilla/5.0",
        })
        pairs = _args_to_pairs(args)
        assert pairs["referer"] == "https://example.com/watch"
        assert pairs["cookie"].startswith("sid=abc")
        assert pairs["user-agent"] == "Mozilla/5.0"
        assert len(args) % 2 == 0

    def test_drops_authorization_by_default(self):
        args = captured_header_args({"Authorization": "Bearer token", "Referer": "https://a.test/"})
        assert "authorization" not in _args_to_pairs(args)
        assert _args_to_pairs(args)["referer"] == "https://a.test/"

    def test_authorization_allowed_when_enabled(self):
        args = captured_header_args({"Authorization": "Bearer token"}, allow_authorization=True)
        assert _args_to_pairs(args)["authorization"] == "Bearer token"

    def test_rejects_crlf_and_empty_values(self):
        args = captured_header_args({
            "Referer": "https://a.test/\r\nX-Evil: 1",
            "X-Empty": "   ",
            "X-Ok": "yes",
        })
        pairs = _args_to_pairs(args)
        assert "referer" not in pairs
        assert "x-empty" not in pairs
        assert pairs["x-ok"] == "yes"

    def test_drops_non_replayable_headers(self):
        args = captured_header_args({
            "Host": "example.com",
            "Range": "bytes=0-1",
            "Accept-Encoding": "gzip",
            "Referer": "https://a.test/",
        })
        pairs = _args_to_pairs(args)
        assert set(pairs) == {"referer"}

    def test_empty_inputs(self):
        assert captured_header_args(None) == []
        assert captured_header_args({}) == []
        assert captured_header_args("not-a-dict") == []

    def test_no_duplicate_flags(self):
        args = captured_header_args({"Referer": "https://a.test/", "Origin": "https://a.test"})
        assert args.count("--add-header") == 2
