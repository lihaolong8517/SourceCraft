# -*- coding: utf-8 -*-
"""Unit tests for special_scene_utils.py (UI-agnostic, pure functions)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from special_scene_utils import (
    build_wait_for_video_arg,
    fragment_unavailable_flag,
    live_from_start_supported,
)


class TestBuildWaitForVideoArg:
    def test_min_only(self):
        assert build_wait_for_video_arg(300) == "300"

    def test_range(self):
        assert build_wait_for_video_arg(300, use_range=True, max_seconds=600) == "300-600"

    def test_max_less_than_min_clamped(self):
        assert build_wait_for_video_arg(600, use_range=True, max_seconds=300) == "600-600"

    def test_range_without_max_uses_min(self):
        assert build_wait_for_video_arg(300, use_range=True) == "300-300"

    def test_invalid_min_falls_back_to_default(self):
        assert build_wait_for_video_arg("abc", default_seconds=60) == "60"

    def test_non_positive_min_falls_back_to_default(self):
        assert build_wait_for_video_arg(0, default_seconds=60) == "60"

    def test_invalid_max_falls_back_to_min(self):
        assert build_wait_for_video_arg(300, use_range=True, max_seconds="oops") == "300-300"


class TestFragmentUnavailableFlag:
    def test_skip(self):
        assert fragment_unavailable_flag(True) == "--skip-unavailable-fragments"

    def test_abort(self):
        assert fragment_unavailable_flag(False) == "--abort-on-unavailable-fragments"


class TestLiveFromStartSupported:
    def test_youtube_com(self):
        assert live_from_start_supported("https://www.youtube.com/watch?v=abc") == "YouTube"

    def test_youtu_be(self):
        assert live_from_start_supported("https://youtu.be/abc") == "YouTube"

    def test_twitch(self):
        assert live_from_start_supported("https://www.twitch.tv/someone") == "Twitch"

    def test_tver(self):
        assert live_from_start_supported("https://tver.jp/some/episode") == "TVer"

    def test_mellow_fan(self):
        assert live_from_start_supported("https://mellow-fan.com/foo") == "mellow-fan"

    def test_unsupported_bilibili_live(self):
        assert live_from_start_supported("https://live.bilibili.com/12345") is None

    def test_unsupported_douyu(self):
        assert live_from_start_supported("https://www.douyu.com/9999") is None

    def test_case_insensitive(self):
        assert live_from_start_supported("HTTPS://WWW.YOUTUBE.COM/WATCH?V=X") == "YouTube"

    def test_empty_url(self):
        assert live_from_start_supported("") is None

    def test_none_url(self):
        assert live_from_start_supported(None) is None
