"""Vimeo 适配器：直接读 window.playerConfig（VDH vimeo.js 的等价物）。

Vimeo 页面把播放配置挂在 ``window.playerConfig.request.files`` 上：
主推 HLS（``hls.cdns[default_cdn].url``），同时可能带 progressive MP4 与 DASH。
这里只读配置，不发请求，也不碰 player.js 的内部解密逻辑。
"""

from __future__ import annotations

from .base import SiteAdapter


class VimeoAdapter(SiteAdapter):
    name = "vimeo"
    hosts = ("vimeo.com",)
    probe_delay_ms = 1200

    PROBE_JS = r"""
(() => {
  const out = [];
  const cfg = window.playerConfig || {};
  const files = (cfg.request && cfg.request.files) || {};
  const push = (url, kind, extra) => {
    if (!url || typeof url !== 'string') return;
    const item = { url: url, type: kind, is_manifest: kind === 'HLS' || kind === 'DASH' };
    if (extra) Object.assign(item, extra);
    out.push(item);
  };
  const hls = files.hls || {};
  const cdns = hls.cdns || {};
  const def = hls.default_cdn || Object.keys(cdns)[0];
  if (def && cdns[def]) {
    push(cdns[def].url, 'HLS', { summary: 'vimeo HLS (' + def + ')' });
  }
  Object.keys(cdns).forEach((key) => {
    if (key !== def && cdns[key] && cdns[key].url) push(cdns[key].url, 'HLS');
  });
  (files.progressive || []).forEach((item) => {
    if (!item || !item.url) return;
    const quality = item.quality ? { resolution: item.quality } : undefined;
    push(item.url, 'MP4', quality ? { quality: quality } : undefined);
  });
  const dashCdns = (files.dash && files.dash.cdns) || {};
  Object.keys(dashCdns).forEach((key) => {
    if (dashCdns[key] && dashCdns[key].url) push(dashCdns[key].url, 'DASH');
  });
  return out;
})()
"""
