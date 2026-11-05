"""vdh_capture.manifest 单测：m3u8 / MPD 解析与补丁生成。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vdh_capture.manifest import (  # noqa: E402
    analyze_manifest_text,
    manifest_patch,
    parse_m3u8,
    parse_mpd,
)

MASTER = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",DEFAULT=YES,URI="audio/en.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub",NAME="English",LANGUAGE="en",URI="subs/en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2"
360p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
1080p/index.m3u8
"""

MEDIA = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-PLAYLIST-TYPE:VOD
#EXTINF:6.0,
seg-1.ts
#EXTINF:6.0,
seg-2.ts
#EXT-X-ENDLIST
"""

LIVE = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:4
#EXTINF:4.0,
seg-1.ts
"""

AES_KEY = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="https://keys.example.com/k1"
#EXTINF:6.0,
seg-1.ts
#EXT-X-ENDLIST
"""

WIDEVINE_KEY = """#EXTM3U
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://abc",KEYFORMAT="com.widevine.alpha"
#EXTINF:6.0,
seg-1.ts
#EXT-X-ENDLIST
"""

FAIRPLAY_KEY = """#EXTM3U
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://abc",KEYFORMAT="com.apple.streamingkeydelivery"
#EXTINF:6.0,
seg-1.ts
#EXT-X-ENDLIST
"""

MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT1H2M3.5S">
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
      <Representation id="v1" bandwidth="5000000" width="1920" height="1080" codecs="avc1.640028">
        <BaseURL>video/1080.mp4</BaseURL>
      </Representation>
      <Representation id="v2" bandwidth="800000" width="640" height="360" codecs="avc1.4d401e">
        <BaseURL>video/360.mp4</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet mimeType="audio/mp4">
      <Representation id="a1" bandwidth="128000" codecs="mp4a.40.2">
        <BaseURL>audio/en.mp4</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


class TestParseM3u8:
    def test_master_variants_and_quality(self):
        result = parse_m3u8(MASTER, "https://cdn.example.com/master.m3u8")
        assert result["is_master"] is True
        assert len(result["variants"]) == 2
        assert result["variants"][0]["url"] == "https://cdn.example.com/360p/index.m3u8"
        assert result["best_video"]["height"] == 1080
        assert result["quality"]["resolution"] == "1080p"
        assert result["quality"]["bandwidth"] == 5000000
        assert "1080p" in result["summary"]

    def test_master_audio_and_subtitle_tracks(self):
        result = parse_m3u8(MASTER, "https://cdn.example.com/master.m3u8")
        assert result["audio_tracks"][0]["url"] == "https://cdn.example.com/audio/en.m3u8"
        assert result["audio_tracks"][0]["default"] is True
        assert result["subtitle_tracks"][0]["language"] == "en"

    def test_media_playlist_duration_and_vod(self):
        result = parse_m3u8(MEDIA, "https://cdn.example.com/1080p/index.m3u8")
        assert result["is_master"] is False
        assert result["segments"] == 2
        assert result["duration_s"] == pytest.approx(12.0)
        assert result["ended"] is True
        assert result["is_live"] is False
        assert result["playlist_type"] == "VOD"

    def test_live_playlist_without_endlist(self):
        result = parse_m3u8(LIVE, "https://cdn.example.com/live.m3u8")
        assert result["ended"] is False
        assert result["is_live"] is True
        assert "直播" in result["summary"]

    def test_aes128_is_encrypted_but_not_drm(self):
        result = parse_m3u8(AES_KEY, "https://cdn.example.com/x.m3u8")
        assert result["has_drm"] is False
        assert result["encryption"][0]["method"] == "AES-128"
        assert result["encryption"][0]["system"] == ""
        assert "加密:AES-128" in result["summary"]

    def test_widevine_key_is_drm(self):
        result = parse_m3u8(WIDEVINE_KEY, "https://cdn.example.com/x.m3u8")
        assert result["has_drm"] is True
        assert result["drm_systems"] == ["Widevine"]

    def test_fairplay_key_is_drm(self):
        result = parse_m3u8(FAIRPLAY_KEY, "https://cdn.example.com/x.m3u8")
        assert result["drm_systems"] == ["FairPlay"]

    def test_init_map_detected(self):
        text = '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4.0,\nseg-1.m4s\n#EXT-X-ENDLIST\n'
        assert parse_m3u8(text, "https://x.example/v.m3u8")["has_init_map"] is True

    def test_garbage_returns_empty_result(self):
        result = parse_m3u8("not a playlist", "https://x.example/")
        assert result["is_master"] is False
        assert result["variants"] == []


class TestParseMpd:
    def test_representations_and_quality(self):
        result = parse_mpd(MPD, "https://cdn.example.com/manifest.mpd")
        assert len(result["representations"]) == 3
        assert result["best_video"]["height"] == 1080
        assert result["quality"]["resolution"] == "1080p"
        assert result["duration_s"] == pytest.approx(3723.5)
        assert result["is_live"] is False

    def test_content_protection_drm(self):
        result = parse_mpd(MPD, "https://cdn.example.com/manifest.mpd")
        assert result["has_drm"] is True
        assert result["drm_systems"] == ["Widevine"]
        assert "DRM:Widevine" in result["summary"]

    def test_dynamic_is_live(self):
        text = MPD.replace('type="static"', 'type="dynamic"')
        assert parse_mpd(text, "https://x.example/live.mpd")["is_live"] is True

    def test_base_url_joined(self):
        result = parse_mpd(MPD, "https://cdn.example.com/manifest.mpd")
        urls = [rep["url"] for rep in result["representations"]]
        assert "https://cdn.example.com/video/1080.mp4" in urls


class TestAnalyzeManifestText:
    def test_dispatch_m3u8(self):
        analysis = analyze_manifest_text(MASTER, "https://x.example/master.m3u8")
        assert analysis["kind"] == "hls"

    def test_dispatch_mpd(self):
        analysis = analyze_manifest_text(MPD, "https://x.example/manifest.mpd")
        assert analysis["kind"] == "dash"

    def test_empty_text(self):
        assert analyze_manifest_text("", "https://x.example/") == {}
        assert analyze_manifest_text(None) == {}

    def test_html_is_not_manifest(self):
        assert analyze_manifest_text("<html><body>hi</body></html>", "https://x.example/") == {}


class TestManifestPatch:
    def test_patch_fields(self):
        patch = manifest_patch(analyze_manifest_text(MASTER, "https://x.example/master.m3u8"))
        assert patch["is_manifest"] is True
        assert patch["has_drm"] is False
        assert patch["quality"]["resolution"] == "1080p"
        assert patch["manifest"]["kind"] == "hls"
        assert len(patch["manifest"]["variants"]) == 2

    def test_drm_patch(self):
        patch = manifest_patch(analyze_manifest_text(MPD, "https://x.example/m.mpd"))
        assert patch["has_drm"] is True
        assert patch["manifest"]["drm_systems"] == ["Widevine"]

    def test_empty_analysis(self):
        assert manifest_patch({}) == {}
        assert manifest_patch(None) == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
