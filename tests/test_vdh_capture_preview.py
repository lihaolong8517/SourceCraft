"""vdh_capture.preview 单测：预览片段参数、缓存与失败处理。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vdh_capture.preview import (  # noqa: E402
    MIN_PREVIEW_BYTES,
    PreviewCache,
    PreviewSpec,
    build_preview_command,
    build_frames_command,
    extract_frames,
    extract_preview,
    preview_start_seconds,
)

FFMPEG = r"E:\1（开发的视频下载器\ffmpeg.exe"


class TestStartSeconds:
    def test_vod_middle(self):
        assert preview_start_seconds(600) == 297.0

    def test_short_video_starts_at_zero(self):
        assert preview_start_seconds(3) == 0.0

    def test_live_starts_at_zero(self):
        assert preview_start_seconds(3600, is_live=True) == 0.0

    def test_unknown_duration(self):
        assert preview_start_seconds(None) == 0.0
        assert preview_start_seconds("bad") == 0.0  # type: ignore[arg-type]


class TestBuildCommand:
    def spec(self, **kwargs):
        base = dict(url="https://cdn.example.com/master.m3u8", duration_s=600.0)
        base.update(kwargs)
        return PreviewSpec(**base)

    def test_core_flags(self):
        cmd = build_preview_command(FFMPEG, self.spec(), "out.mp4")
        joined = " ".join(cmd)
        assert cmd[0] == FFMPEG
        assert "-ss" in cmd and "297.000" in cmd
        assert "-t" in cmd and "6.000" in cmd
        assert joined.count("-c copy") == 1
        assert "-movflags" in cmd and "+faststart" in cmd
        assert cmd[-1] == "out.mp4"
        assert "libx264" not in joined

    def test_no_seek_for_live(self):
        cmd = build_preview_command(FFMPEG, self.spec(duration_s=3600, is_live=True), "out.mp4")
        assert "-ss" not in cmd

    def test_headers_whitelisted(self):
        headers = {
            "Referer": "https://example.com/watch",
            "Host": "cdn.example.com",
            "Range": "bytes=0-99",
            "sec-ch-ua": "x",
            "Cookie": "a=b",
        }
        cmd = build_preview_command(FFMPEG, self.spec(headers=headers), "out.mp4")
        joined = " ".join(cmd)
        assert "Referer: https://example.com/watch" in joined
        assert "Cookie: a=b" in joined
        assert "Host:" not in joined
        assert "Range:" not in joined
        assert "sec-ch-ua" not in joined.lower()

    def test_crlf_injected_header_dropped(self):
        cmd = build_preview_command(FFMPEG, self.spec(headers={"Referer": "a\r\nX: y"}), "out.mp4")
        assert not any("X: y" in c for c in cmd)

    def test_user_agent_and_referer_flags(self):
        cmd = build_preview_command(
            FFMPEG,
            self.spec(headers={"User-Agent": "Mozilla/5.0", "Referer": "https://e.com/p"}),
            "out.mp4",
        )
        assert "-user_agent" in cmd
        assert cmd[cmd.index("-user_agent") + 1] == "Mozilla/5.0"
        assert "-referer" in cmd

    def test_referer_falls_back_to_page_url(self):
        cmd = build_preview_command(FFMPEG, self.spec(page_url="https://e.com/page"), "out.mp4")
        assert cmd[cmd.index("-referer") + 1] == "https://e.com/page"


class TestExtract:
    def test_success_writes_file(self, tmp_path):
        target = tmp_path / "p.mp4"

        def runner(cmd, timeout):
            target.write_bytes(b"\0" * (MIN_PREVIEW_BYTES + 1))
            return None

        assert extract_preview(FFMPEG, PreviewSpec(url="u"), target, runner=runner) == target

    def test_small_output_treated_as_failure(self, tmp_path):
        target = tmp_path / "p.mp4"

        def runner(cmd, timeout):
            target.write_bytes(b"\0" * 16)

        assert extract_preview(FFMPEG, PreviewSpec(url="u"), target, runner=runner) is None
        assert not target.exists()

    def test_runner_exception_is_swallowed(self, tmp_path):
        def runner(cmd, timeout):
            raise TimeoutError("boom")

        assert extract_preview(FFMPEG, PreviewSpec(url="u"), tmp_path / "p.mp4", runner=runner) is None


class TestCacheKeyAndCache:
    def test_key_ignores_signature_params(self):
        a = PreviewSpec(url="https://cdn/a.m3u8?expire=111&token=x&quality=1080", duration_s=600)
        b = PreviewSpec(url="https://cdn/a.m3u8?expire=222&token=y&quality=1080", duration_s=600)
        assert a.cache_key() == b.cache_key()

    def test_key_differs_by_start(self):
        a = PreviewSpec(url="https://cdn/a.m3u8", duration_s=600)
        b = PreviewSpec(url="https://cdn/a.m3u8", duration_s=900)
        assert a.cache_key() != b.cache_key()

    def test_put_and_get(self, tmp_path):
        cache = PreviewCache(tmp_path / "cache")
        spec = PreviewSpec(url="https://cdn/a.m3u8", duration_s=600)
        target = cache.path_for(spec)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\0" * (MIN_PREVIEW_BYTES + 1))
        cache.put(spec, target)
        assert cache.get(spec) == target

    def test_missing_file_returns_none(self, tmp_path):
        cache = PreviewCache(tmp_path / "cache")
        assert cache.get(PreviewSpec(url="https://cdn/none.m3u8")) is None

    def test_eviction_keeps_max_items(self, tmp_path):
        cache = PreviewCache(tmp_path / "cache", max_items=2)
        paths = []
        for index in range(3):
            spec = PreviewSpec(url=f"https://cdn/v{index}.m3u8")
            target = cache.path_for(spec)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\0" * (MIN_PREVIEW_BYTES + 1))
            cache.put(spec, target)
            paths.append(target)
        assert len(cache._entries) == 2
        assert not paths[0].exists()


class TestFrames:
    def test_command_uses_crop_and_rawvideo(self):
        cmd = build_frames_command(FFMPEG, "clip.mp4", width=176, height=99, fps=12, max_frames=72)
        joined = " ".join(cmd)
        assert "fps=12" in joined
        assert "scale=176:99:force_original_aspect_ratio=increase" in joined
        assert "crop=176:99" in joined
        assert "-f rawvideo" in joined
        assert "-pix_fmt rgb24" in joined
        assert cmd[-1] == "-"

    def test_extract_frames_splits_raw_bytes(self, tmp_path):
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * (MIN_PREVIEW_BYTES + 10))
        frame_size = 4 * 3 * 3
        data = bytes(range(frame_size)) * 2

        class _Result:
            returncode = 0
            stdout = data
            stderr = b""

        width, height, fps, frames = extract_frames(
            FFMPEG, clip, width=4, height=3, fps=10, runner=lambda cmd, timeout: _Result()
        )
        assert (width, height, fps) == (4, 3, 10)
        assert len(frames) == 2
        assert len(frames[0]) == frame_size

    def test_extract_frames_ffmpeg_failure(self, tmp_path):
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * (MIN_PREVIEW_BYTES + 10))

        class _Result:
            returncode = 1
            stdout = b""
            stderr = b"boom"

        assert extract_frames(FFMPEG, clip, runner=lambda cmd, timeout: _Result()) is None

    def test_extract_frames_short_clip(self, tmp_path):
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 10)
        assert extract_frames(FFMPEG, clip, runner=lambda cmd, timeout: None) is None
