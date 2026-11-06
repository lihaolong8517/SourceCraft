"""线程生命周期压力测试：卡片/窗口在预览线程仍运行时被销毁，不能崩进程。"""
import os, sys, time, pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout

app = QApplication.instance() or QApplication(sys.argv)

import playwright_browser as pb

failures = []
def check(name, ok, detail=""):
    print(("[OK]   " if ok else "[FAIL] ") + name + ((" | " + str(detail)) if detail else ""))
    if not ok:
        failures.append(name)

_orig_extract = pb.extract_preview
_orig_thumb_run = pb.ThumbnailExtractThread.run

def slow_extract(*args, **kwargs):
    time.sleep(2.0)
    return None
def slow_thumb_run(self):
    time.sleep(2.0)
    try:
        self.thumbnail_ready.emit(b"")
    except Exception:
        pass

pb.extract_preview = slow_extract
pb.ThumbnailExtractThread.run = slow_thumb_run

def pump(ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

def make_card(url):
    info = {"url": url, "type": "HLS", "title": "stress", "page_url": "http://example.com/p", "size": "1 MB"}
    card = pb.MediaCard(info)
    card.preview_area.set_spec(pb.PreviewSpec(url=url, page_url="http://example.com/p"))
    return card

for i in range(8):
    card = make_card("http://127.0.0.1:1/slow%d.m3u8" % i)
    card.preview_area._start_build()
    pump(80)
    card.cleanup()
    card.deleteLater()
    pump(60)
check("A: 运行中销毁卡片 8 次未崩溃", True)

for i in range(5):
    win = QWidget()
    lay = QVBoxLayout(win)
    card = make_card("http://127.0.0.1:1/win%d.m3u8" % i)
    lay.addWidget(card)
    win.show()
    card.preview_area._start_build()
    pump(80)
    win.deleteLater()
    card = None
    win = None
    pump(80)
check("B: 运行中销毁整窗 5 次未崩溃", True)

for i in range(8):
    card = make_card("http://127.0.0.1:1/thumb%d.m3u8" % i)
    card._ensure_thumbnail()
    pump(60)
    card.cleanup()
    card.deleteLater()
    pump(50)
check("C: 抽帧线程运行中销毁卡片 8 次未崩溃", True)

pump(3500)
left = len(pb._LIVE_THREADS)
check("D: 线程登记表最终清空", left == 0, left)

pb.extract_preview = _orig_extract
pb.ThumbnailExtractThread.run = _orig_thumb_run
print("[THREADS] " + ("PASS" if not failures else "FAIL: " + ", ".join(failures)))
sys.exit(0 if not failures else 1)
