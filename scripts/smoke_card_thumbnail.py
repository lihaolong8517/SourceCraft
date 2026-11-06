"""冒烟：卡片默认封面（未 hover 时）来自网站封面或画面抽帧，而不是占位图。

覆盖两种路径：
1) 有 thumbnail_url（协议相对 //...）→ 规范化后经 QNetworkAccessManager 加载；
2) 没有 thumbnail_url → ThumbnailExtractThread 用 ffmpeg 从媒体直链抽帧。
"""

import os
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

TEMP = ROOT / "scripts" / ".smoke_tmp"
TEMP.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(TEMP)
os.environ["TMP"] = str(TEMP)

FAILS = []


def check(name, ok, detail=""):
    line = f"[{'OK' if ok else 'FAIL'}] {name}" + (f" | {detail}" if detail else "")
    print(line)
    if not ok:
        FAILS.append(name)


def make_clip(path):
    import subprocess

    ffmpeg = str(ROOT / "ffmpeg.exe")
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=4",
        "-pix_fmt", "yuv420p", str(path),
    ]
    return subprocess.run(cmd, capture_output=True).returncode == 0 and Path(path).is_file()


def make_thumb(path):
    image = QImage(320, 180, QImage.Format_RGB888)
    image.fill(QColor("#3a6ea5"))
    return bool(image.save(str(path), "PNG"))


def pump(app, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


def pump_until(app, predicate, timeout_s):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    import playwright_browser as pb

    clip = TEMP / "thumb_clip.mp4"
    if not make_clip(clip):
        print("[FAIL] 无法生成测试视频")
        return 1
    thumb_png = TEMP / "site_thumb.png"
    make_thumb(thumb_png)

    os.chdir(TEMP)
    server = ThreadingHTTPServer(("127.0.0.1", 0), SimpleHTTPRequestHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    # 路径 1：网站提供了封面（协议相对 URL，验证规范化）
    card = pb.MediaCard({
        "url": f"{base}/thumb_clip.mp4",
        "page_url": f"{base}/watch",
        "title": "t1",
        "type": "MP4",
        "thumbnail": f"//127.0.0.1:{port}/site_thumb.png",
    })
    card.show()
    ok = pump_until(app, lambda: card._thumb_loaded, 20)
    check("网站封面已加载并铺满卡片", ok and card.preview_area._cover_pixmap is not None
          and not card.preview_area._cover_pixmap.isNull())
    if card.preview_area._cover_pixmap is not None:
        cover = card.preview_area._cover_pixmap
        check("封面尺寸等于预览区", cover.width() == card.preview_area.width()
              and cover.height() == card.preview_area.height(),
              f"{cover.width()}x{cover.height()}")

    # 路径 2：没有封面 → 从画面抽帧补一张
    card2 = pb.MediaCard({
        "url": f"{base}/thumb_clip.mp4",
        "page_url": f"{base}/watch",
        "title": "t2",
        "type": "MP4",
    })
    card2.show()
    ok2 = pump_until(app, lambda: card2._thumb_loaded, 40)
    check("无网站封面时用画面抽帧当封面", ok2, f"started={card2._thumb_extract_started}")
    if ok2 and card2.preview_area._cover_pixmap is not None:
        cover2 = card2.preview_area._cover_pixmap
        check("抽帧封面尺寸等于预览区",
              cover2.width() == card2.preview_area.width() and cover2.height() == card2.preview_area.height(),
              f"{cover2.width()}x{cover2.height()}")

    # 路径 3：_normalize_thumbnail_url
    norm = pb._normalize_thumbnail_url
    check("协议相对 // 补 https", norm("//cdn.x/a.jpg") == "https://cdn.x/a.jpg")
    check("根相对 / 补页面域名", norm("/a.jpg", "https://site.com/p") == "https://site.com/a.jpg")
    check("裸域名补 https", norm("cdn.x/a.jpg") == "https://cdn.x/a.jpg")
    check("空封面返回空", norm("", "https://site.com") == "")
    check("blob: 不当作封面", norm("blob:https://x/y") == "")

    card.cleanup()
    card2.cleanup()
    server.shutdown()

    print("[THUMB] PASS" if not FAILS else f"[THUMB] FAIL {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
