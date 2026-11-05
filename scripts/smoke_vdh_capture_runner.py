"""端到端冒烟：用真实 Chromium 验证 vdh_capture.CaptureRunner。

流程：起一个本地 HTTP 桩站（index.html + 2MB mp4 + master.m3u8 + 媒体清单），
用打包版 Chromium 打开页面，让 CaptureRunner 通过 CDP 嗅探 + 清单富化采集候选，
最后断言：mp4 直链、HLS 清单、清晰度、请求头、二次上报都符合预期。

运行（必须用装好 playwright 的解释器）：
    python scripts\\smoke_vdh_capture_runner.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Playwright / Chromium 需要一个干净可写的临时目录，否则偶发
# "Lock file can not be created! Error code: 5"
_SMOKE_TMP = ROOT / "scripts" / ".smoke_tmp"
_SMOKE_TMP.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TMP", str(_SMOKE_TMP))
os.environ["TMP"] = str(_SMOKE_TMP)
os.environ["TEMP"] = str(_SMOKE_TMP)

from playwright.async_api import async_playwright  # noqa: E402

from vdh_capture.runner import CaptureRunner  # noqa: E402

CHROME = ROOT / "playwright_browsers" / "chromium-1194" / "chrome-win" / "chrome.exe"

CLIP_BYTES = 2 * 1024 * 1024
MASTER_M3U8 = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="en",DEFAULT=YES,URI="audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2",AUDIO="aud"
v720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4800000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",AUDIO="aud"
v1080.m3u8
"""
MEDIA_M3U8 = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:4.0,
seg1.ts
#EXT-X-ENDLIST
"""
INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>vdh smoke</title></head>
<body>
<video id="v" src="/clip.mp4" width="320" height="180" muted></video>
<script>
  fetch('/clip.mp4').then(function (r) { return r.arrayBuffer(); });
  fetch('/master.m3u8').then(function (r) { return r.text(); });
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静默
        pass

    def _send(self, body, content_type, status=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(INDEX_HTML, "text/html; charset=utf-8")
        if path == "/clip.mp4":
            return self._send(b"\x00" * CLIP_BYTES, "video/mp4")
        if path == "/master.m3u8":
            return self._send(MASTER_M3U8, "application/vnd.apple.mpegurl")
        if path in ("/v720.m3u8", "/v1080.m3u8", "/audio.m3u8"):
            return self._send(MEDIA_M3U8, "application/vnd.apple.mpegurl")
        return self._send("not found", "text/plain", status=404)


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    base_url = "http://127.0.0.1:%d/" % port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    emitted = []

    def emit(candidate, force_update):
        emitted.append({"candidate": candidate, "force": bool(force_update)})
        print("[SMOKE] emit force=%s type=%s size=%s url=%s"
              % (force_update, candidate.get("type"), candidate.get("size"),
                 candidate.get("url")))

    async def run():
        async with async_playwright() as pw:
            launch_kwargs = {"headless": True, "args": ["--no-sandbox", "--mute-audio"]}
            if CHROME.exists():
                launch_kwargs["executable_path"] = str(CHROME)
            browser = await pw.chromium.launch(**launch_kwargs)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                runner = CaptureRunner(emit=emit)
                attached = await runner.start(context, page, base_url)
                print("[SMOKE] runner.start attached=%s" % attached)
                await page.goto(base_url, wait_until="load", timeout=30000)
                await runner.after_navigation(page)
                await asyncio.sleep(2.0)
                await runner.stop()
                print("[SMOKE] runner.last_error=%r" % runner.last_error)
            finally:
                await browser.close()

    asyncio.run(run())
    server.shutdown()
    server.server_close()

    failures = []
    urls = [item["candidate"].get("url", "") for item in emitted]
    if not any(url.endswith("/clip.mp4") for url in urls):
        failures.append("mp4 直链未被捕获")
    manifests = [item["candidate"] for item in emitted
                 if item["candidate"].get("is_manifest") and item["candidate"].get("manifest")]
    if not manifests:
        failures.append("m3u8 清单未被富化")
    else:
        best = max(manifests, key=lambda c: (c.get("quality") or {}).get("height") or 0)
        quality = best.get("quality") or {}
        print("[SMOKE] manifest variants=%d resolution=%s"
              % (len((best.get("manifest") or {}).get("variants") or []), quality.get("resolution")))
        if not (best.get("manifest") or {}).get("variants"):
            failures.append("清单没有解析出变体")
        if quality.get("resolution") != "1080p":
            failures.append("清晰度未补全（期望 1080p，实际 %r）" % quality.get("resolution"))
    if not any((c["candidate"].get("headers") or {}).get("Referer") for c in emitted):
        failures.append("没有捕获到 Referer 请求头")
    if not any(c["force"] for c in emitted):
        failures.append("没有发生清单富化后的二次上报")

    print("[SMOKE] emitted=%d candidates" % len(emitted))
    if failures:
        print("[SMOKE] FAIL: " + "; ".join(failures))
        return 1
    print("[SMOKE] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
