"""B站适配器：直接读 window.__playinfo__ 和 window.__INITIAL_STATE__。

B站页面把播放信息挂在两个全局变量上：
- ``window.__playinfo__``：当前清晰度的播放地址（dash.video / dash.audio 数组）
- ``window.__INITIAL_STATE__``：视频元信息（标题、cid、bvid）

这里只读 dash 流的 URL 和清晰度，不碰 wbi 签名。真正的防盗链请求头由 CDP 嗅探补全。
"""

from __future__ import annotations

from .base import SiteAdapter


class BilibiliAdapter(SiteAdapter):
    name = "bilibili"
    hosts = (
        "bilibili.com",
        "www.bilibili.com",
        "m.bilibili.com",
        "api.bilibili.com",
    )
    probe_delay_ms = 1200

    PROBE_JS = r"""
(() => {
  const out = [];
  const playinfo = window.__playinfo__ || {};
  const data = playinfo.data || {};
  const dash = data.dash || {};

  // DASH 视频流
  (dash.video || []).forEach((v) => {
    if (!v || !v.baseUrl && !v.base_url) return;
    const url = v.baseUrl || v.base_url;
    const quality = {};
    if (v.width && v.height) quality.resolution = v.height + "p";
    if (v.bandwidth) quality.bandwidth = v.bandwidth;
    out.push({
      url: url,
      type: "MP4",
      is_manifest: false,
      quality: quality,
    });
  });

  // DASH 音频流
  (dash.audio || []).forEach((a) => {
    if (!a || !a.baseUrl && !a.base_url) return;
    const url = a.baseUrl || a.base_url;
    const quality = {};
    if (a.bandwidth) quality.bandwidth = a.bandwidth;
    out.push({
      url: url,
      type: "Audio",
      is_manifest: false,
      audio_only: true,
      quality: quality,
    });
  });

  return out;
})()
"""
