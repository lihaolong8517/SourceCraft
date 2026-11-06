"""媒体捕获窗口「始终置顶」开关冒烟测试（offscreen，无需真实桌面）。"""
import os
import sys
import tempfile
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtWidgets import QApplication

import playwright_browser as pb

failures = []
oks = []


def check(name, condition, detail=""):
    if condition:
        oks.append(name)
        print(f"[OK] {name}" + (f" | {detail}" if detail else ""))
    else:
        failures.append(name)
        print(f"[FAIL] {name}" + (f" | {detail}" if detail else ""))


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    # 把 QSettings 写到临时目录，避免污染用户真实设置
    settings_dir = tempfile.mkdtemp(prefix="vdh_pin_settings_")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, settings_dir)
    QSettings.setPath(QSettings.IniFormat, QSettings.SystemScope, settings_dir)

    # ---- 1) 首次启动（无设置）默认置顶 ----
    settings = QSettings("VideoDownloader", "Settings")
    settings.remove("media_capture/always_on_top")
    settings.sync()

    w1 = pb.MediaCaptureWindow(None)
    check("默认置顶：状态变量为 True", w1._always_on_top is True)
    check("默认置顶：按钮已选中", w1.pin_btn.isChecked() is True)
    check(
        "默认置顶：窗口带 WindowStaysOnTopHint",
        bool(w1.windowFlags() & Qt.WindowStaysOnTopHint),
        hex(int(w1.windowFlags())),
    )

    # ---- 2) 切换关闭：置顶标志清除并写盘 ----
    w1.pin_btn.setChecked(False)
    check("关闭后：状态变量为 False", w1._always_on_top is False)
    check(
        "关闭后：窗口不再带 WindowStaysOnTopHint",
        not bool(w1.windowFlags() & Qt.WindowStaysOnTopHint),
        hex(int(w1.windowFlags())),
    )
    value = QSettings("VideoDownloader", "Settings").value("media_capture/always_on_top")
    check("关闭后：设置已持久化为 False", _as_bool(value) is False, repr(value))

    # ---- 3) 再打开：恢复置顶 ----
    w1.pin_btn.setChecked(True)
    check("重开后：状态变量为 True", w1._always_on_top is True)
    check(
        "重开后：窗口重新带 WindowStaysOnTopHint",
        bool(w1.windowFlags() & Qt.WindowStaysOnTopHint),
        hex(int(w1.windowFlags())),
    )

    # ---- 4) 新窗口实例读取持久化结果 ----
    w2 = pb.MediaCaptureWindow(None)
    check("新实例：读取到持久化的 True", w2._always_on_top is True and w2.pin_btn.isChecked() is True)

    w2.pin_btn.setChecked(False)
    w3 = pb.MediaCaptureWindow(None)
    check(
        "新实例：读取到持久化的 False",
        w3._always_on_top is False and w3.pin_btn.isChecked() is False,
    )
    check(
        "新实例：关闭置顶后无 WindowStaysOnTopHint",
        not bool(w3.windowFlags() & Qt.WindowStaysOnTopHint),
    )

    # ---- 5) 关闭置顶时 _enforce_topmost 不做强制 ----
    calls = {"n": 0}
    real_winid = w3.winId

    def _fake_winid():
        calls["n"] += 1
        return real_winid()

    w3.winId = _fake_winid
    w3._enforce_topmost()
    check("关闭置顶时 _enforce_topmost 提前返回", calls["n"] == 0, f"winId 调用 {calls['n']} 次")

    for w in (w1, w2, w3):
        w.close()
        w.deleteLater()
    app.processEvents()

    print(f"\n[PIN] {len(oks)} 项通过, {len(failures)} 项失败")
    if failures:
        print("[PIN] FAIL")
        return 1
    print("[PIN] PASS")
    return 0


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "off", "no", "")
    return bool(value)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        print("[PIN] FAIL")
        sys.exit(1)
