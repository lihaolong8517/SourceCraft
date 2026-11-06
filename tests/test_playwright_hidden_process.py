# -*- coding: utf-8 -*-
"""回归测试：GUI 内启动的外部命令必须静默（不弹黑色控制台窗口）。

打包成窗口程序后，未加 CREATE_NO_WINDOW 的 subprocess 会闪现一个黑色命令框。
鼠标悬停生成预览时调用的 ``_run_hidden``（抽缩略图）曾经漏加该参数，
这里锁住行为，避免回退。
"""

import subprocess
import sys

import pytest

pb = pytest.importorskip("playwright_browser")


def test_run_hidden_passes_creationflags(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)

    pb._run_hidden(["ffmpeg", "-version"], timeout=3)

    expected = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    kwargs = captured["kwargs"]
    assert kwargs.get("creationflags") == expected
    assert kwargs.get("capture_output") is True
    assert kwargs.get("stdin") == subprocess.DEVNULL
    assert kwargs.get("timeout") == 3
    assert captured["cmd"] == ["ffmpeg", "-version"]


def test_run_hidden_swallows_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("ffmpeg missing")

    monkeypatch.setattr(subprocess, "run", boom)

    assert pb._run_hidden(["ffmpeg"], timeout=1) is None


def test_no_bare_subprocess_run_in_thumbnail_from_preview():
    """``_emit_thumbnail_from_video`` 不能再直接调用 subprocess.run（会闪黑框）。"""
    import inspect

    source = inspect.getsource(pb.MediaPreviewThread._emit_thumbnail_from_video)
    assert "_run_hidden" in source
    assert "subprocess.run(" not in source
