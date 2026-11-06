"""YouTube 适配器：直接读 window.ytInitialPlayerResponse。

YouTube 页面把完整播放配置挂在 ``window.ytInitialPlayerResponse`` 上：
``streamingData.adaptiveFormats`` 是 DASH 分离流（视频/音频分开），
``streamingData.formats`` 是合并后的 360p/720p MP4 直链。

这里只读配置里的 manifest / 直链 URL，不解签名、不碰 player.js。
真正的签名解密交给 yt-dlp 兜底。
"""

from __future__ import annotations

from .base import SiteAdapter


class YoutubeAdapter(SiteAdapter):
    name = "youtube"
    hosts = (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
    )
    probe_delay_ms = 1500

    PROBE_JS = r"""
(() => {
  const out = [];
  const yt = window.ytInitialPlayerResponse || {};
  const sd = yt.streamingData || {};

  // DASH 分离流（adaptiveFormats：视频/音频分开）
  (sd.adaptiveFormats || []).forEach((fmt) => {
    if (!fmt || !fmt.url) return;
    const isVideo = !!fmt.mimeType && fmt.mimeType.startsWith("video/");
    const isAudio = !!fmt.mimeType && fmt.mimeType.startsWith("audio/");
    const kind = isVideo ? "MP4" : (isAudio ? "Audio" : "Media");
    const quality = {};
    if (fmt.width && fmt.height) quality.resolution = fmt.height + "p";
    if (fmt.bitrate) quality.bandwidth = fmt.bitrate;
    out.push({
      url: fmt.url,
      type: kind,
      is_manifest: false,
      quality: quality,
      audio_only: isAudio,
    });
  });

  // 合并流（formats：音视频合一的低清晰度 MP4）
  (sd.formats || []).forEach((fmt) => {
    if (!fmt || !fmt.url) return;
    const quality = {};
    if (fmt.width && fmt.height) quality.resolution = fmt.height + "p";
    out.push({
      url: fmt.url,
      type: "MP4",
      is_manifest: false,
      quality: quality,
    });
  });

  return out;
})()
"""
