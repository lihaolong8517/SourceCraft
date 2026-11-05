"""P2 集成联调：让内置浏览器的 run_launcher() 真正跑起来，验证 [MEDIA] 上报。

不依赖 GUI：直接用子进程调用 playwright_browser.run_launcher()，本地桩站提供
mp4 直链与 HLS 清单，断言 stdout 里出现 [MEDIA] 与 VDH 捕获层挂载日志。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "scripts" / ".smoke_tmp"
MP4_BYTES = 2 * 1024 * 1024

MASTER = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="zh",DEFAULT=YES,URI="audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",AUDIO="aud"
v1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2",AUDIO="aud"
v720.m3u8
"""

INDEX = """<!doctype html><html><head><title>vdh-capture-integration</title></head>
<body>
<video id="v" src="/media/clip.mp4" controls muted></video>
<script>
fetch('/media/clip.mp4').then(r => r.arrayBuffer()).then(b => console.log('mp4 bytes', b.byteLength));
fetch('/media/master.m3u8').then(r => r.text()).then(t => console.log('m3u8', t.length));
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = INDEX.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/media/clip.mp4":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(MP4_BYTES))
            self.end_headers()
            self.wfile.write(b"\x00" * MP4_BYTES)
            return
        if path == "/media/master.m3u8":
            body = MASTER.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/media/v1080.m3u8":
            body = b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


BOOTSTRAP = r"""
import sys
sys.path.insert(0, {root!r})
import playwright_browser
sys.argv = ['playwright_browser.py', 'chromium', {cookies!r}, {url!r}]
playwright_browser.run_launcher()
"""


def _parse_media_line(line: str):
    line = (line or "").strip()
    if not line.startswith("[MEDIA] "):
        return None
    try:
        payload = json.loads(line[len("[MEDIA] "):])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/"
    cookies_dir = str(TMP / "cookies")
    os.makedirs(cookies_dir, exist_ok=True)

    env = dict(os.environ)
    env["TEMP"] = str(TMP)
    env["TMP"] = str(TMP)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    code = BOOTSTRAP.format(root=str(ROOT), cookies=cookies_dir, url=url)
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    lines = []
    try:
        assert proc.stdin is not None
        # 先等 READY（stdin 读取线程在 READY 之后才启动），再开启捕获并导航
        ready_deadline = time.time() + 30
        while time.time() < ready_deadline:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip())
            if "[BROWSER] READY" in line:
                break
        proc.stdin.write("ENABLE_CAPTURE\n")
        proc.stdin.flush()
        time.sleep(0.5)
        proc.stdin.write(f"GOTO {url}\n")
        proc.stdin.flush()
        deadline = time.time() + 30
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip())
            payload = _parse_media_line(line)
            if payload and (payload.get("quality") or {}).get("resolution"):
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        server.shutdown()
        if proc.stdout:
            rest = proc.stdout.read() or ""
            lines.extend(rest.splitlines())

    output = "\n".join(lines)
    media_lines = [l for l in lines if l.startswith("[MEDIA]")]
    payloads = [p for p in (_parse_media_line(l) for l in media_lines) if p]

    failures = []
    if "[BROWSER] VDH 捕获层已挂载" not in output:
        failures.append("VDH 捕获层未挂载")
    if "cdp=True" not in output:
        failures.append("CDP 未连接")

    mp4 = [p for p in payloads if ".mp4" in (p.get("url") or "") and p.get("source") == "network"]
    if not mp4:
        failures.append("未捕获到 mp4 直链（network）")
    else:
        first = mp4[0]
        headers = first.get("headers") or {}
        if not headers:
            failures.append("mp4 未携带请求头")
        referer = (headers.get("Referer") or headers.get("referer") or "")
        if "127.0.0.1" not in referer:
            failures.append(f"Referer 未回放: {referer!r}")

    m3u8 = [p for p in payloads if "master.m3u8" in (p.get("url") or "") and p.get("source") == "network"]
    if not m3u8:
        failures.append("未捕获到 HLS 清单（network）")
    else:
        enriched = [p for p in m3u8 if (p.get("quality") or {}).get("resolution")]
        if not enriched:
            failures.append("HLS 清单未富化出分辨率")

    print("[IT] [MEDIA] 条数:", len(media_lines))
    for p in payloads:
        q = p.get("quality") or {}
        print("[IT] ", p.get("type"), (p.get("url") or "")[:110],
              "size=", p.get("size"), "res=", q.get("resolution"), "drm=", p.get("has_drm"))
    if failures:
        print("[IT] FAIL:", failures)
        print("---- browser output ----")
        print("\n".join(lines[-80:]))
        return 1
    print("[IT] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
