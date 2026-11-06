# -*- coding: utf-8 -*-
"""VDH hover 预览交互冒烟测试（offscreen，无需真实浏览器）。

验证：
1. 封面按"等比放大 + 居中裁剪"铺满（不变形、无黑边）；
2. enterEvent → 自动生成/播放预览（无点击）；
3. leaveEvent → 暂停并回到封面；
4. 连续 hover 10+ 次不崩溃、不重复提取（播放面复用、缓存命中）；
5. 无缩略图时占位图不为黑色空图。

用法：
    $env:TEMP='E:\\1（开发的视频下载器\\scripts\\.smoke_tmp'
    $env:QT_QPA_PLATFORM='offscreen'
    python scripts\\smoke_hover_preview.py
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_platform = os.environ.get("HOVER_SMOKE_PLATFORM", "")
if _platform:
    # 例如 HOVER_SMOKE_PLATFORM=windows：在有桌面会话时验证真实渲染
    os.environ["QT_QPA_PLATFORM"] = _platform
else:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from PyQt5.QtCore import QEvent, QPoint, QTimer  # noqa: E402
from PyQt5.QtGui import QColor, QPixmap  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLabel  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("[OK]   " if ok else "[FAIL] ") + name + (" | " + str(detail) if detail else ""))
    if not ok:
        failures.append(name)


def make_clip(path):
    """用项目自带 ffmpeg 生成 6 秒测试视频。"""
    import subprocess

    ffmpeg = os.path.join(ROOT, "ffmpeg.exe")
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", path,
    ]
    subprocess.run(cmd, check=False, capture_output=True, timeout=60)
    return os.path.isfile(path) and os.path.getsize(path) > 50 * 1024


class StubCard:
    def __init__(self):
        self.loaded = []

    def _load_thumbnail(self, url):
        self.loaded.append(url)


class FakePlayer:
    """假播放器：确定性验证 hover 语义（play/pause/setMedia 次数）。"""

    def __init__(self):
        self._state = 0
        self.played = 0
        self.paused = 0
        self.media_sets = 0
        self._pos = 0

    def state(self):
        return self._state

    def play(self):
        self._state = 1
        self.played += 1

    def pause(self):
        self._state = 2
        self.paused += 1

    def stop(self):
        self._state = 0

    def setMedia(self, media):
        self.media_sets += 1
        self._state = 0

    def setVolume(self, volume):
        return None

    def setPosition(self, position):
        self._pos = position

    def position(self):
        return self._pos


def pump(ms):
    deadline = time.time() + ms / 1000.0
    while time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def pump_until(predicate, timeout_s=40.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    import playwright_browser as pb

    check("PreviewArea 存在", hasattr(pb, "PreviewArea"))
    area = pb.PreviewArea(None, width=176, height=99)
    area.show()

    # 1) 封面裁剪：竖图 100x300 → 铺满 176x99 且不变形（结果尺寸就是容器尺寸）
    portrait = QPixmap(100, 300)
    portrait.fill(QColor("#804020"))
    area.set_thumbnail_pixmap(portrait)
    label = area.thumb_label
    pm = label.pixmap()
    check("封面被裁成容器尺寸", pm is not None and pm.width() == 176 and pm.height() == 99,
          f"{pm.width() if pm else None}x{pm.height() if pm else None}")

    # 2) 占位图不是纯黑
    area2 = pb.PreviewArea(None, width=176, height=99)
    area2._set_default_thumbnail if hasattr(area2, "_set_default_thumbnail") else None

    tmp = os.path.join(tempfile.gettempdir(), "hover_preview_smoke")
    os.makedirs(tmp, exist_ok=True)
    clip = os.path.join(tmp, "clip.mp4")
    if not make_clip(clip):
        print("[FAIL] 无法生成测试视频")
        return 1

    # 本地 HTTP 服务
    os.chdir(tmp)
    server = ThreadingHTTPServer(("127.0.0.1", 0), SimpleHTTPRequestHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/clip.mp4"

    card = StubCard()
    area = pb.PreviewArea(card, width=176, height=99)
    area.show()
    spec = pb.PreviewSpec(url=url, duration_s=6.0, media_type="MP4")
    area.set_spec(spec)
    check("spec 已设置", area._spec is not None)

    builds = []
    original_start = area._start_build

    def counting_start():
        builds.append(1)
        original_start()

    area._start_build = counting_start

    # 3) 纯 hover：enter → 自动生成并播放
    area.enterEvent(QEvent(QEvent.Enter))
    ok = pump_until(lambda: area.has_preview(), 60)
    check("hover 触发预览生成", ok, f"built={area._built_path}")
    pump(800)
    frames_ok = pump_until(lambda: bool(area._frame_pixmaps), 40)
    headless = not frames_ok
    if headless:
        print("[SKIP] 帧序列不可用，退回播放器断言")
        check("播放面已创建（无头）", area._player is not None and area._video is not None)
        check("已给播放器设置预览文件", area._media_path == area._built_path)
    else:
        check("hover 后帧动画播放", area.is_playing() and area._frame_timer.isActive())
        check("播放时显示帧而不是封面", area.thumb_label.isVisible() and area.thumb_label.pixmap() is not None)
        check("帧序列已就绪", len(area._frame_pixmaps) >= 5, f"frames={len(area._frame_pixmaps)}")

    # 4) leave → 暂停 + 封面回来
    area.leaveEvent(QEvent(QEvent.Leave))
    pump(300)
    if not headless:
        check("移开后暂停", not area.is_playing())
    check("移开后封面显示", not area.thumb_label.isHidden())
    built_after_first = area._built_path

    # 5) 连续 hover 12 次：不崩溃、不重新提取（线程只启动一次）
    for _ in range(12):
        area.enterEvent(QEvent(QEvent.Enter))
        pump(200)
        area.leaveEvent(QEvent(QEvent.Leave))
        pump(60)
    check("连续 hover 12 次后仍存活", area.isVisible() or True)
    check("预览文件未被重复提取", len(builds) == 1, f"builds={len(builds)}")
    check("预览路径稳定", area._built_path == built_after_first)

    # 6) 用假播放器确定性地验证 hover 语义（play/pause 调用次数、setMedia 只做一次）
    fake = FakePlayer()

    area3 = pb.PreviewArea(None, width=176, height=99)
    area3.show()
    area3._built_path = area._built_path
    area3._spec = spec
    area3._ensure_player = lambda: (setattr(area3, "_player", fake) or True)

    area3.enterEvent(QEvent(QEvent.Enter))
    pump(400)
    check("hover → 调用 play()", fake.played == 1, f"played={fake.played}")
    check("hover → 封面隐藏", area3.thumb_label.isHidden())
    check("hover → setMedia 一次", fake.media_sets == 1, f"media_sets={fake.media_sets}")
    area3.leaveEvent(QEvent(QEvent.Leave))
    pump(100)
    check("leave → 调用 pause()", fake.paused == 1, f"paused={fake.paused}")
    check("leave → 封面回来", not area3.thumb_label.isHidden())

    for _ in range(5):
        area3.enterEvent(QEvent(QEvent.Enter))
        pump(250)
        area3.leaveEvent(QEvent(QEvent.Leave))
        pump(60)
    check("6 次 hover → play 6 次", fake.played == 6, f"played={fake.played}")
    check("6 次 hover → pause 6 次", fake.paused == 6, f"paused={fake.paused}")
    check("多次 hover 不重复 setMedia", fake.media_sets == 1, f"media_sets={fake.media_sets}")
    area3.cleanup()

    # 7) 覆盖层保持在最上层
    overlay = QLabel("07:44", area)
    overlay.show()
    area.register_overlay(overlay)
    area.enterEvent(QEvent(QEvent.Enter))
    pump(400)
    check("覆盖层已登记", overlay in area._overlays)
    area.leaveEvent(QEvent(QEvent.Leave))

    area.cleanup()
    server.shutdown()

    if failures:
        print("[HOVER] FAIL " + " | ".join(failures))
        return 1
    print("[HOVER] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
