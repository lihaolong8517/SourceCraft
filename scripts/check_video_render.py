# -*- coding: utf-8 -*-
"""真实窗口下验证 QVideoWidget + QMediaPlayer 能否渲染本地 mp4（hover 预览的播放面）。

用法（需要在有桌面会话的环境）：
    python scripts/check_video_render.py
输出 [RENDER] PASS / FAIL / SKIP。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def make_clip(path: str) -> bool:
    ffmpeg = os.path.join(ROOT, "ffmpeg.exe")
    if not os.path.exists(ffmpeg):
        return False
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=4",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", path,
    ]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def main() -> int:
    from PyQt5.QtCore import QUrl, QTimer
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
    from PyQt5.QtMultimediaWidgets import QVideoWidget

    clip = os.path.join(tempfile.gettempdir(), "sourcecraft_vdh_preview", "_render_check.mp4")
    os.makedirs(os.path.dirname(clip), exist_ok=True)
    if not make_clip(clip):
        print("[RENDER] SKIP 无法生成测试片段（缺少 ffmpeg）")
        return 0

    app = QApplication(sys.argv)
    video = QVideoWidget()
    video.resize(320, 180)
    video.show()

    player = QMediaPlayer(None)
    player.setVideoOutput(video)
    player.setVolume(0)
    errors = []
    player.error.connect(lambda: errors.append(player.errorString()))

    player.setMedia(QMediaContent(QUrl.fromLocalFile(clip)))
    player.play()

    deadline = time.time() + 4.0
    playing = False
    while time.time() < deadline:
        app.processEvents()
        if player.state() == QMediaPlayer.PlayingState and not errors:
            playing = True
            break
        time.sleep(0.05)

    player.pause()
    player.setMedia(QMediaContent())
    video.close()
    time.sleep(0.1)

    if errors:
        print(f"[RENDER] FAIL 播放器报错: {errors[0]}")
        return 1
    if not playing:
        print("[RENDER] SKIP 无桌面会话/无法建立渲染面（无头环境）")
        return 0
    print("[RENDER] PASS 真实窗口下 QVideoWidget 可渲染本地 mp4")
    return 0


if __name__ == "__main__":
    sys.exit(main())

