"""P0 冒烟：验证内置浏览器的媒体捕获环境是否可用。

不依赖 vdh_capture 包，只验证三件事：
  1) 项目自带的打包版 Chromium 能被 Playwright 正常启动；
  2) 页面发出的 mp4 / m3u8 网络请求能被观察到（url / content-type / 大小）；
  3) 一份可直接复用的本地 HTTP 桩服务（后续阶段改为断言 vdh_capture 的上报）。

用法:
    python scripts/smoke_vdh_capture.py
退出码 0 = 通过，1 = 失败。
"""

from __future__ import annotations

import http.server
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROMIUM = ROOT / "playwright_browsers" / "chromium-1194" / "chrome-win" / "chrome.exe"

MP4_BYTES = 2 * 1024 * 1024  # > 500KB 的分类门槛

INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>VDH smoke</title></head>
<body>
<video id="v" src="/clip.mp4" muted playsinline></video>
<script>
  // 强制整段下载，避免 <video> 只发 Range 小请求被当成碎片丢弃
  fetch('/clip.mp4').then(r => r.arrayBuffer()).then(b => {
    window.__mp4Bytes = b.byteLength;
  });
  fetch('/master.m3u8').then(r => r.text()).then(t => {
    window.__m3u8Text = t;
  });
</script>
</body></html>
"""

MASTER_M3U8 = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
1080p/index.m3u8
"""

MEDIA_M3U8 = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="https://example.invalid/key.bin"
#EXTINF:6.0,
seg0.ts
#EXTINF:6.0,
seg1.ts
#EXT-X-ENDLIST
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server 约定)
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._text(INDEX_HTML, "text/html; charset=utf-8")
        elif path == "/master.m3u8":
            self._text(MASTER_M3U8, "application/vnd.apple.mpegurl")
        elif path == "/1080p/index.m3u8":
            self._text(MEDIA_M3U8, "application/vnd.apple.mpegurl")
        elif path == "/clip.mp4":
            body = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (MP4_BYTES - 12)
            self._binary(body, "video/mp4")
        else:
            self.send_error(404)

    def _text(self, text: str, ctype: str):
        self._binary(text.encode("utf-8"), ctype)

    def _binary(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "none")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):  # 静音
        pass


def _start_server() -> tuple[http.server.ThreadingHTTPServer, int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def main() -> int:
    if not CHROMIUM.exists():
        print(f"[FAIL] 找不到打包版 Chromium: {CHROMIUM}")
        return 1

    # Playwright 需要可写的 TEMP；某些环境 %TEMP% 的 DACL 异常会导致
    # "Lock file can not be created! Error code: 5"，这里用独立临时目录规避。
    tmpdir = tempfile.mkdtemp(prefix="vdh_smoke_")
    os.environ["TEMP"] = tmpdir
    os.environ["TMP"] = tmpdir

    server, port = _start_server()
    base = f"http://127.0.0.1:{port}/"
    print(f"[SMOKE] local server: {base}")

    seen: list[dict] = []
    failures: list[str] = []

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - 环境缺依赖
        print(f"[FAIL] 无法 import playwright: {exc}")
        return 1

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROMIUM), headless=True)
            context = browser.new_context()
            page = context.new_page()

            def on_response(resp):
                url = resp.url
                if not any(key in url for key in ("clip.mp4", "master.m3u8", "1080p/index.m3u8")):
                    return
                headers = dict(resp.headers)
                try:
                    body_len = int(headers.get("content-length", "0") or 0)
                except ValueError:
                    body_len = 0
                record = {
                    "url": url,
                    "status": resp.status,
                    "content_type": (headers.get("content-type") or "").split(";")[0].strip(),
                    "size_bytes": body_len,
                }
                seen.append(record)
                print(f"[SMOKE] {record['status']} {record['content_type']:<32} {body_len:>9} B  {url}")

            page.on("response", on_response)
            page.goto(base, wait_until="load", timeout=30000)
            page.wait_for_timeout(2500)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()

    mp4 = [r for r in seen if r["url"].endswith("clip.mp4")]
    m3u8 = [r for r in seen if "master.m3u8" in r["url"]]

    if not mp4:
        failures.append("未观察到 clip.mp4 请求")
    elif max(r["size_bytes"] for r in mp4) < 500 * 1024:
        failures.append("clip.mp4 观察到的大小 < 500KB")
    if not m3u8:
        failures.append("未观察到 master.m3u8 请求")
    elif m3u8[0]["content_type"] not in (
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
    ):
        failures.append(f"master.m3u8 content-type 异常: {m3u8[0]['content_type']}")

    print(f"[SMOKE] responses matched: {len(seen)}")
    if failures:
        for item in failures:
            print(f"[FAIL] {item}")
        return 1
    print("[SMOKE] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
