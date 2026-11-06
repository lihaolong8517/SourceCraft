"""微博适配器：从页面 video 标签和 window.__INITIAL_STATE__ 读视频地址。

微博视频通常直接嵌在 ``<video>`` 标签的 src 里，或者挂在
``window.__INITIAL_STATE__.$videoData`` 上。
这里优先扫 video 标签，兜底读全局变量。
"""

from __future__ import annotations

from .base import SiteAdapter


class WeiboAdapter(SiteAdapter):
    name = "weibo"
    hosts = (
        "weibo.com",
        "www.weibo.com",
        "m.weibo.cn",
        "weibo.cn",
    )
    probe_delay_ms = 1000

    PROBE_JS = r"""
(() => {
  const out = [];
  const seen = new Set();

  // 方式1：扫页面上所有 video 标签
  document.querySelectorAll("video").forEach((v) => {
    const src = v.src || (v.querySelector("source") || {}).src;
    if (!src || !src.startsWith("http")) return;
    if (seen.has(src)) return;
    seen.add(src);
    out.push({ url: src, type: "MP4", is_manifest: false });
  });

  // 方式2：从 __INITIAL_STATE__ 读 $videoData
  try {
    const state = window.__INITIAL_STATE__ || {};
    const vd = state.$videoData || state.videoData || {};
    const urls = vd.mp4_url_hd || vd.mp4_url || vd.hd_urls || [];
    (Array.isArray(urls) ? urls : [urls]).forEach((url) => {
      if (!url || typeof url !== "string" || seen.has(url)) return;
      seen.add(url);
      out.push({ url: url, type: "MP4", is_manifest: false });
    });
  } catch (e) {}

  return out;
})()
"""
