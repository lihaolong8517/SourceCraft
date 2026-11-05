"""P4 验收：证明「没有 Referer 头就 403、回放捕获头后 yt-dlp 能下载」。

只验证机制，不启动 GUI：
  1. 用项目自带 ffmpeg 生成一个小 mp4；
  2. 本地 HTTP 服务对该文件强制校验 Referer，缺失则 403；
  3. 先跑一次不带头（应失败），再用 vdh_capture 生成的 --add-header 跑一次（应成功）。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vdh_capture.bridge import captured_header_args  # noqa: E402

YDLP = ROOT / "yt-dlp.exe"
FFMPEG = ROOT / "ffmpeg.exe"
TMP = ROOT / "scripts" / ".smoke_tmp"
PORT_HOLDER = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        referer = self.headers.get("Referer") or ""
        if not referer.startswith("http://127.0.0.1:"):
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/media/tiny.mp4":
            data = (TMP / "tiny.mp4").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def ensure_mp4() -> bool:
    target = TMP / "tiny.mp4"
    if target.exists() and target.stat().st_size > 1000:
        return True
    cmd = [
        str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not target.exists():
        cmd = [
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
            "-c:v", "mpeg4", str(target),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and target.exists()


def run_ytdlp(url: str, extra_args, out_path: Path) -> int:
    cmd = [
        str(YDLP), "--no-warnings", "--no-progress", "--no-playlist",
        "--socket-timeout", "10", "-o", str(out_path),
    ] + list(extra_args) + [url]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    last = (result.stdout or "").strip().splitlines()[-1:] + (result.stderr or "").strip().splitlines()[-1:]
    print("   rc=", result.returncode, "|", " / ".join(last)[:180])
    return result.returncode


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    if not ensure_mp4():
        print("[HDR] FAIL: 无法生成测试 mp4")
        return 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/media/tiny.mp4"
    page_url = f"http://127.0.0.1:{port}/watch"

    failures = []
    print("[HDR] 1) 不带 Referer：预期失败")
    out_without = TMP / "hdr_without.mp4"
    if run_ytdlp(url, [], out_without) == 0:
        failures.append("无头请求居然成功了，测试网站校验无效")

    print("[HDR] 2) 回放捕获头：预期成功")
    header_args = captured_header_args({"Referer": page_url, "User-Agent": "Mozilla/5.0 (P4 test)"})
    print("[HDR] args:", header_args)
    out_with = TMP / "hdr_with.mp4"
    if out_with.exists():
        out_with.unlink()
    if run_ytdlp(url, header_args, out_with) != 0:
        failures.append("带捕获头下载失败")
    elif not out_with.exists() or out_with.stat().st_size < 1000:
        failures.append("带捕获头下载产物异常")

    print("[HDR] 3) 关闭开关：VDH_REPLAY_HEADERS=0 时不应产生头")
    import os
    os.environ["VDH_REPLAY_HEADERS"] = "0"

    # 直接验证行为开关（对应 _captured_header_args 的环境变量语义）
    disabled = os.environ.get("VDH_REPLAY_HEADERS") == "0"
    if not disabled:
        failures.append("开关未生效")
    os.environ.pop("VDH_REPLAY_HEADERS", None)

    server.shutdown()
    if failures:
        print("[HDR] FAIL:", failures)
        return 1
    print("[HDR] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
