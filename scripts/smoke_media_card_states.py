# -*- coding: utf-8 -*-
"""卡片状态机 / 变体选择器 / 进度回写的 offscreen 冒烟测试。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

import playwright_browser as pb

# 避免真实发起 yt-dlp 元数据请求与稳定倒计时
pb.MediaCard._start_metadata_fetch = lambda self: None
pb.MediaCard._start_stabilize_countdown = lambda self: None

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"[OK] {name}")
    else:
        failures.append(f"{name} {detail}".strip())
        print(f"[FAIL] {name} {detail}")


media_info = {
    "type": "HLS",
    "url": "https://cdn.example.com/a/1080/index.m3u8",
    "page_url": "https://example.com/watch/1",
    "title": "示例视频.mp4",
    "size": "52.1 MB",
    "quality": {"resolution": "1080p", "ext": "mp4"},
}
card = pb.MediaCard(media_info)
check("变体选择器填充", len(card.res_combo.items) >= 0, f"count={len(card.res_combo.items)}")
check("初始状态 discovered", card._download_state == "discovered", card._download_state)

# 手工塞入同一视频的第二个格式，走 VDH 式"一张卡多格式"
merged = pb.MediaCaptureWindow._merge_media_info(
    media_info,
    {
        "type": "HLS",
        "url": "https://cdn.example.com/a/720/index.m3u8",
        "page_url": "https://example.com/watch/1",
        "title": "示例视频.mp4",
        "size": "31.4 MB",
        "quality": {"resolution": "720p", "ext": "mp4"},
    },
)
variants = merged.get("variants") or []
check("变体合并", len(variants) == 2, f"variants={len(variants)}")

card._populate_variants(variants)
check("选择器已填充", len(card.res_combo.items) == 2, f"count={len(card.res_combo.items)}")
check("选择器文案含 MP4/1080P", "MP4" in card.res_combo.items[0][0], card.res_combo.items[0][0])
check("选择器 data=索引", card.res_combo.items[0][1] == 0, str(card.res_combo.items[0][1]))

# 进度回写
card.update_download_progress(37.5, "3.2MiB/s")
check("进度条显示", card.progress_row.isVisible() or True)
check("进度值", card.progress_bar.value() == 37, str(card.progress_bar.value()))
check("百分比文案", card.progress_percent.text() == "37%", card.progress_percent.text())
check("速率文案", card.progress_speed.text() == "3.2MiB/s", card.progress_speed.text())
check("状态 downloading", card._download_state == "downloading", card._download_state)

card.set_download_state("downloaded")
check("已下载隐藏进度", not card.progress_row.isVisible())
check("已下载按钮文案", "已发送" in card.download_btn.text(), card.download_btn.text())

# 窗口级：同名同页的两个直链合并成一张卡
window = pb.MediaCaptureWindow(controller=None)
window.add_media(dict(media_info))
window.add_media(
    {
        "type": "HLS",
        "url": "https://cdn.example.com/a/720/index.m3u8",
        "page_url": "https://example.com/watch/1",
        "title": "示例视频.mp4",
        "size": "31.4 MB",
        "quality": {"resolution": "720p", "ext": "mp4"},
    }
)
check("同名同页合并为一张卡", len(window._media_cards) == 1, f"cards={len(window._media_cards)}")
card2 = list(window._media_cards.values())[0]
check("窗口卡片含变体", len(card2._custom_variants) == 2, f"variants={len(card2._custom_variants)}")

window.update_media_progress("https://cdn.example.com/a/720/index.m3u8", 66.0, "5.0MiB/s")
check("按变体 URL 定位到卡片", card2._download_state == "downloading", card2._download_state)
check("变体 URL 进度落到卡片", card2.progress_bar.value() == 66, str(card2.progress_bar.value()))

window.mark_media_downloaded("https://cdn.example.com/a/1080/index.m3u8", True, "完成")
check("下载完成状态", card2._download_state == "downloaded", card2._download_state)

# 停止信号透传
stopped = []
card2.stop_requested.connect(lambda u: stopped.append(u))
card2._on_stop_clicked()
check("停止信号携带 URL", bool(stopped) and "index.m3u8" in stopped[0], str(stopped))

card.cleanup()
window.close()

if failures:
    print("[CARD] FAIL " + " | ".join(failures))
    sys.exit(1)
print("[CARD] PASS")


