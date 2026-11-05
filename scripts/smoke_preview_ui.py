"""预览区 UI 冒烟：PreviewArea 能生成并加载预览片段，MediaCard 能正常构建。

用法：python scripts/smoke_preview_ui.py
"""

from __future__ import annotations

import functools
import http.server
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_preview import build_media, serve  # noqa: E402

from PyQt5.QtCore import QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

import playwright_browser as pb  # noqa: E402
from vdh_capture.preview import PreviewSpec, resolve_ffmpeg  # noqa: E402


def main() -> int:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        print("[UI] 找不到 ffmpeg")
        return 2
    workdir = Path(tempfile.mkdtemp(prefix="vdh_ui_"))
    mp4, master = build_media(workdir, ffmpeg)
    httpd, base = serve(workdir)
    app = QApplication([])
    failures = []

    try:
        area = pb.PreviewArea(None)
        spec = PreviewSpec(url=f"{base}/hls/master.m3u8", is_manifest=True, media_type="HLS", duration_s=20.0)
        area.set_spec(spec)
        if not area.play_overlay.isVisible() and area.play_overlay.isHidden():
            failures.append("有 spec 时播放按钮应可见")
        area.toggle()

        deadline_ms = 90_000
        elapsed = {"ms": 0}

        def poll():
            elapsed["ms"] += 200
            if area.has_preview():
                print(f"[UI] 预览已生成: {area._built_path}")
                app.quit()
            elif elapsed["ms"] >= deadline_ms:
                failures.append("90 秒内未生成预览")
                app.quit()
            else:
                QTimer.singleShot(200, poll)

        QTimer.singleShot(200, poll)
        app.exec_()
        area.cleanup()

        # MediaCard 构建（不发真实网络请求，只验证控件树）
        card = pb.MediaCard({"url": f"{base}/clip.mp4", "type": "MP4", "title": "smoke", "duration": 20.0})
        if not hasattr(card, "preview_area"):
            failures.append("MediaCard 缺少 preview_area")
        card.cleanup()
    finally:
        httpd.shutdown()

    if failures:
        print("[UI] FAIL")
        for item in failures:
            print("  -", item)
        return 1
    print("[UI] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
