"""预览引擎端到端验证：本地生成 HLS/MP4，交给 ffmpeg 抓"中间一小段"。

用法：python scripts/check_preview.py
"""

from __future__ import annotations

import functools
import http.server
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vdh_capture.preview import PreviewCache, PreviewSpec, extract_preview, resolve_ffmpeg  # noqa: E402


def build_media(workdir: Path, ffmpeg: str) -> tuple[Path, Path]:
    """生成 20 秒测试视频 + HLS 分片，返回 (mp4, master.m3u8)。"""
    mp4 = workdir / "clip.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=20",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(mp4),
        ],
        check=True,
    )
    hls_dir = workdir / "hls"
    hls_dir.mkdir(exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(mp4), "-c", "copy", "-f", "hls",
            "-hls_time", "4", "-hls_list_size", "0",
            "-hls_segment_filename", str(hls_dir / "seg%03d.ts"),
            str(hls_dir / "master.m3u8"),
        ],
        check=True,
    )
    return mp4, hls_dir / "master.m3u8"


def serve(directory: Path) -> tuple[socketserver.TCPServer, str]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def main() -> int:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        print("[PREVIEW] 找不到 ffmpeg")
        return 2
    print(f"[PREVIEW] ffmpeg = {ffmpeg}")

    workdir = Path(tempfile.mkdtemp(prefix="vdh_preview_"))
    mp4, master = build_media(workdir, ffmpeg)
    httpd, base = serve(workdir)
    cache = PreviewCache(workdir / "previews")
    failures = []

    try:
        for name, url, spec_kwargs in (
            ("hls", f"{base}/hls/master.m3u8", {"is_manifest": True, "media_type": "hls", "duration_s": 20.0}),
            ("mp4", f"{base}/clip.mp4", {"media_type": "video", "duration_s": 20.0}),
        ):
            spec = PreviewSpec(url=url, **spec_kwargs)
            target = cache.path_for(spec)
            result = extract_preview(ffmpeg, spec, target, timeout=60)
            if result is None:
                failures.append(f"{name}: extract_preview 返回 None")
                continue
            size = result.stat().st_size
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(result)],
                capture_output=True, text=True,
            )
            duration = probe.stdout.strip()
            print(f"[PREVIEW] {name}: {size} B, duration={duration}s, path={result.name}")
            if size < 8192:
                failures.append(f"{name}: 文件太小")
            try:
                if float(duration or 0) <= 0:
                    failures.append(f"{name}: 时长无效")
            except ValueError:
                failures.append(f"{name}: 时长解析失败 {duration!r}")
    finally:
        httpd.shutdown()

    if failures:
        print("[PREVIEW] FAIL")
        for item in failures:
            print("  -", item)
        return 1
    print("[PREVIEW] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
