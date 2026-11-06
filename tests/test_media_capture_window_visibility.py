# -*- coding: utf-8 -*-
"""回归测试：媒体捕获窗口不应在"打开内置浏览器"时自动弹出。

历史 bug：``_setup_ui`` 里 ``pin_btn.setChecked(True)`` 触发 ``toggled``
→ ``_set_always_on_top`` → ``_apply_always_on_top`` → ``self.show()``
（以及 Win32 SetWindowPos 的 SWP_SHOWWINDOW），于是创建
``MediaCaptureWindow``（内置浏览器窗口初始化时）就把捕获面板弹出来。
"""

import os

import pytest

pytest.importorskip("PyQt5")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pb = pytest.importorskip("playwright_browser")


class _FakeQSettings:
    """替换 playwright_browser.QSettings：内存字典，避免污染用户注册表。"""

    store = {}

    def __init__(self, *args, **kwargs):
        pass

    def value(self, key, default=None):
        return self.store.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt 命名
        _FakeQSettings.store[key] = value


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def fake_settings(monkeypatch):
    _FakeQSettings.store = {}
    monkeypatch.setattr(pb, "QSettings", _FakeQSettings)
    return _FakeQSettings


def test_constructing_window_does_not_show(qapp, fake_settings):
    w = pb.MediaCaptureWindow(None)
    try:
        assert w.isVisible() is False
    finally:
        w.close()
        w.deleteLater()


def test_default_setting_is_topmost_and_pin_checked(qapp, fake_settings):
    w = pb.MediaCaptureWindow(None)
    try:
        assert w._always_on_top is True
        assert w.pin_btn.isChecked() is True
        assert w.isVisible() is False
    finally:
        w.close()
        w.deleteLater()


def test_changing_topmost_while_hidden_does_not_show(qapp, fake_settings):
    w = pb.MediaCaptureWindow(None)
    try:
        w._set_always_on_top(False)
        assert w.isVisible() is False
        w._set_always_on_top(True)
        assert w.isVisible() is False
        assert fake_settings.store.get("media_capture/always_on_top") is True
    finally:
        w.close()
        w.deleteLater()


def test_changing_topmost_keeps_visible_window_visible(qapp, fake_settings):
    w = pb.MediaCaptureWindow(None)
    try:
        w.show()
        qapp.processEvents()
        assert w.isVisible() is True
        w._set_always_on_top(False)
        qapp.processEvents()
        assert w.isVisible() is True
    finally:
        w.close()
        w.deleteLater()


def test_setting_is_persisted_and_reloaded(qapp, fake_settings):
    w = pb.MediaCaptureWindow(None)
    try:
        w._set_always_on_top(False)
    finally:
        w.close()
        w.deleteLater()
    assert fake_settings.store.get("media_capture/always_on_top") is False

    w2 = pb.MediaCaptureWindow(None)
    try:
        assert w2._always_on_top is False
        assert w2.pin_btn.isChecked() is False
        assert w2.isVisible() is False
    finally:
        w2.close()
        w2.deleteLater()
