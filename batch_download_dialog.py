# -*- coding: utf-8 -*-
"""批量下载对话框（方案 A）。

设计约束（不破坏现有功能）：
1. 不修改主下载器任何现有函数/方法/代码路径；
2. 不覆盖主窗口的 self.download_thread，批量任务使用独立线程引用；
3. 批量任务只连接本对话框自己的槽，不经过主窗口 download_finished，
   避免单任务 modal 弹窗阻塞队列；
4. DownloadThread 采用延迟导入，避免与 video_downloader.py 循环导入。
"""

import os
import re
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from i18n import t, t_format


_DIALOG_QSS = """
QDialog { background: #f6f7f9; }

QLabel#SectionTitle { font-size: 13px; font-weight: 600; color: #1f2329; }
QLabel#Hint { font-size: 12px; color: #8a9099; }
QLabel#Stats { font-size: 12px; color: #6b7280; }
QLabel#CurrentTask { font-size: 12px; color: #4b5563; }
QLabel#Summary { font-size: 12px; color: #4b5563; }

QFrame#Card {
    background: #ffffff;
    border: 1px solid #e6e9ed;
    border-radius: 10px;
}

QPlainTextEdit#UrlInput {
    background: #ffffff;
    border: 1px solid #e3e6ea;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
    color: #1f2329;
    selection-background-color: #cfe2fb;
}
QPlainTextEdit#UrlInput:focus { border: 1px solid #3b7dd8; }

QPlainTextEdit#LogView {
    background: #fbfbfc;
    border: 1px solid #eceef1;
    border-radius: 8px;
    padding: 6px 8px;
    color: #5b6270;
    font-size: 11px;
}

QTableWidget {
    background: #ffffff;
    border: 1px solid #e6e9ed;
    border-radius: 8px;
    gridline-color: transparent;
    outline: none;
    font-size: 12px;
}
QTableWidget::item { padding: 6px 8px; border-bottom: 1px solid #f1f3f5; }
QTableWidget::item:selected { background: #eaf2fd; color: #1f2329; }

QHeaderView::section {
    background: #f7f8fa;
    border: none;
    border-bottom: 1px solid #e6e9ed;
    padding: 9px 8px;
    font-size: 12px;
    font-weight: 600;
    color: #6b7280;
}

QProgressBar {
    border: none;
    background: #e9edf2;
    border-radius: 5px;
    height: 14px;
    text-align: center;
    font-size: 10px;
    color: #5b6270;
}
QProgressBar::chunk { background: #3b7dd8; border-radius: 5px; }

QPushButton {
    background: #ffffff;
    border: 1px solid #d8dde3;
    border-radius: 6px;
    padding: 7px 16px;
    font-size: 12px;
    color: #374151;
}
QPushButton:hover { border-color: #b9c2cc; background: #fafbfc; }
QPushButton:pressed { background: #f0f2f5; }
QPushButton:disabled { color: #b3b9c2; border-color: #e6e9ed; background: #fafbfc; }

QPushButton#PrimaryButton {
    background: #3b7dd8;
    border: none;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover { background: #4a8ce6; }
QPushButton#PrimaryButton:pressed { background: #2f68b8; }
QPushButton#PrimaryButton:disabled { background: #b9cfe9; color: #eef3fa; }

QPushButton#DangerButton { color: #c0392b; border-color: #e8c4c0; }
QPushButton#DangerButton:hover { background: #fdf3f2; border-color: #d89b95; }

QCheckBox { font-size: 12px; color: #4b5563; spacing: 6px; }
"""


class _BatchUrlInput(QPlainTextEdit):
    """粘贴链接时自动按行排列。

    覆盖两种场景：
    1. 一次粘贴多个链接（空格/换行/英文逗号/中文逗号/分号分隔）→ 每行一个；
    2. 连续粘贴单个纯链接 → 自动新起一行，不接在上一条后面。
    """

    _URL_PATTERN = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
    _SEPARATOR_BEFORE_URL = re.compile(r'(?<=[,;，。；、])(?=\s*https?://)', re.IGNORECASE)

    def insertFromMimeData(self, source):
        text = source.text() if source is not None else ""
        urls = self._split_urls(text)

        if len(urls) > 1:
            self._insert_url_lines(urls)
            return

        if len(urls) == 1 and text.strip() == urls[0]:
            cursor = self.textCursor()
            if cursor.positionInBlock() > 0 and cursor.block().text().strip():
                cursor.beginEditBlock()
                cursor.movePosition(cursor.EndOfBlock)
                cursor.insertText("\n" + urls[0])
                cursor.endEditBlock()
                self.setTextCursor(cursor)
                self.ensureCursorVisible()
                return

        super().insertFromMimeData(source)

    def _insert_url_lines(self, urls):
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.positionInBlock() > 0 and cursor.block().text().strip():
            cursor.movePosition(cursor.EndOfBlock)
            cursor.insertText("\n")
        cursor.insertText("\n".join(urls))
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    @classmethod
    def _split_urls(cls, text):
        if not text:
            return []
        # 把「分隔符 + 紧邻下一个链接」的位置统一成换行，兼容逗号/分号/中文标点分隔
        normalized = cls._SEPARATOR_BEFORE_URL.sub("\n", text)
        found = []
        for match in cls._URL_PATTERN.finditer(normalized):
            url = match.group(0).rstrip(',.;:"\'，。；、')
            if url:
                found.append(url)
        return found


STATUS_WAITING = "waiting"
STATUS_DOWNLOADING = "downloading"
STATUS_PAUSED = "paused"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_SKIPPED = "skipped"

_STATUS_COLORS = {
    STATUS_WAITING: "#7a7a7a",
    STATUS_DOWNLOADING: "#2f6fbf",
    STATUS_PAUSED: "#b07d1a",
    STATUS_SUCCESS: "#2e7d52",
    STATUS_FAILED: "#c0392b",
    STATUS_CANCELLED: "#8a8a8a",
    STATUS_SKIPPED: "#8a8a8a",
}


class _BatchTask:
    """单个批量任务的状态容器。"""

    __slots__ = ("index", "url", "status", "message", "progress", "retry_count", "before_files")

    def __init__(self, index, url):
        self.index = index
        self.url = url
        self.status = STATUS_WAITING
        self.message = ""
        self.progress = 0.0
        self.retry_count = 0
        self.before_files = set()


class BatchDownloadDialog(QDialog):
    """批量下载对话框。"""

    MAX_RETRY = 1

    def __init__(self, app, parent=None):
        super().__init__(parent if isinstance(parent, QWidget) else None)
        self.app = app
        self._tasks = []
        self._running = False
        self._current_index = -1
        self._current_thread = None
        self._last_percent_ts = 0.0
        self._last_log_ts = 0.0

        self._build_ui()
        self._prefill_from_main()
        self._refresh_stats()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        self.setWindowTitle(t("批量下载"))
        self.resize(940, 700)
        self.setMinimumSize(840, 580)
        # 移除标题栏问号（无帮助内容，避免成为摆设）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setStyleSheet(_DIALOG_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        # 输入卡片
        input_card = QFrame()
        input_card.setObjectName("Card")
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(14, 12, 14, 12)
        input_layout.setSpacing(8)

        input_header = QHBoxLayout()
        title_label = QLabel(t("视频链接"))
        title_label.setObjectName("SectionTitle")
        input_header.addWidget(title_label)
        input_header.addStretch(1)
        hint_label = QLabel(t("每行一个，支持直接粘贴"))
        hint_label.setObjectName("Hint")
        input_header.addWidget(hint_label)
        input_layout.addLayout(input_header)

        self.input_edit = _BatchUrlInput()
        self.input_edit.setObjectName("UrlInput")
        self.input_edit.setPlaceholderText(
            t("每行粘贴一个视频链接，例如：\nhttps://www.bilibili.com/video/BV...\nhttps://youtu.be/...")
        )
        self.input_edit.setFixedHeight(100)
        self.input_edit.textChanged.connect(self._on_input_changed)
        input_layout.addWidget(self.input_edit)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("Stats")
        input_layout.addWidget(self.stats_label)

        root.addWidget(input_card)

        # 任务表
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([t("#"), t("链接"), t("状态"), t("结果")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        header = self.table.horizontalHeader()
        header.setFixedHeight(36)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 42)
        self.table.setColumnWidth(2, 118)
        self.table.setColumnWidth(3, 190)
        root.addWidget(self.table, 1)

        # 当前任务 + 进度
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.current_label = QLabel(t("当前任务：无"))
        self.current_label.setObjectName("CurrentTask")
        status_row.addWidget(self.current_label, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setFixedHeight(14)
        status_row.addWidget(self.progress_bar)
        root.addLayout(status_row)

        # 日志
        self.log_edit = QPlainTextEdit()
        self.log_edit.setObjectName("LogView")
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(88)
        self.log_edit.setMaximumBlockCount(300)
        self.log_edit.setFont(QFont("Consolas", 9))
        root.addWidget(self.log_edit)

        # 底部
        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 1)
        self.overall_bar.setValue(0)
        self.overall_bar.setTextVisible(False)
        self.overall_bar.setFixedWidth(120)
        self.overall_bar.setFixedHeight(14)
        footer.addWidget(self.overall_bar)

        self.summary_label = QLabel(t("总进度 0/0"))
        self.summary_label.setObjectName("Summary")
        footer.addWidget(self.summary_label)
        footer.addStretch(1)

        self.auto_retry = QCheckBox(t("失败自动重试"))
        self.auto_retry.setChecked(True)
        footer.addWidget(self.auto_retry)

        self.start_btn = QPushButton(t("开始下载"))
        self.start_btn.setObjectName("PrimaryButton")
        self.start_btn.setMinimumWidth(96)
        self.start_btn.clicked.connect(self._start_batch)
        footer.addWidget(self.start_btn)

        self.stop_btn = QPushButton(t("停止"))
        self.stop_btn.setObjectName("DangerButton")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_batch)
        footer.addWidget(self.stop_btn)

        self.close_btn = QPushButton(t("关闭"))
        self.close_btn.clicked.connect(self.close)
        footer.addWidget(self.close_btn)

        root.addLayout(footer)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._refresh_stats)

    def _prefill_from_main(self):
        try:
            existing = (self.app.url_input.text() or "").strip()
        except Exception:
            existing = ""
        if existing:
            self.input_edit.setPlainText(existing)

    # -------------------------------------------------------------- parsing

    def _parse_urls(self):
        raw = self.input_edit.toPlainText() or ""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        extractor = getattr(self.app, "extract_url_from_text", None)
        valid = []
        seen = set()
        duplicate = 0
        invalid = 0
        for line in lines:
            url = line
            if callable(extractor):
                try:
                    url = extractor(line) or line
                except Exception:
                    url = line
            url = (url or "").strip().rstrip(",.;:\"'")
            if not url.lower().startswith(("http://", "https://")):
                invalid += 1
                continue
            if url in seen:
                duplicate += 1
                continue
            seen.add(url)
            valid.append(url)
        return valid, duplicate, invalid

    def _on_input_changed(self):
        self._debounce.stop()
        self._debounce.start(300)

    def _refresh_stats(self):
        valid, duplicate, invalid = self._parse_urls()
        self.stats_label.setText(
            t_format(
                "识别 {} 个有效链接 · 去重 {} 个 · 跳过 {} 行无法识别",
                len(valid),
                duplicate,
                invalid,
            )
        )
        self.start_btn.setEnabled(bool(valid) and not self._running)

    # ------------------------------------------------------------- queue

    def _load_thread_class(self):
        """获取主下载器的 DownloadThread 类。

        兼容两种运行形态：
        - 打包/模块化运行：video_downloader 包（threads.download）；
        - 开发期直接运行：根目录 video_downloader.py。
        项目根目录同时存在同名包与 .py，Python 优先导入包，故按优先级逐个尝试。
        """
        import sys

        # 1. 当前运行中的主模块（直接运行 .py 时 DownloadThread 在 __main__）
        for module_name in ("__main__", "video_downloader"):
            module = sys.modules.get(module_name)
            if module is None:
                continue
            thread_class = getattr(module, "__dict__", {}).get("DownloadThread")
            if thread_class is not None:
                return thread_class

        # 2. 模块化包（打包环境同样适用）
        try:
            from video_downloader.threads.download import DownloadThread

            return DownloadThread
        except Exception:
            pass

        try:
            from video_downloader import DownloadThread

            return DownloadThread
        except Exception:
            pass

        # 3. 开发期单体文件兜底（仅源码环境存在该文件）
        base = os.path.dirname(os.path.abspath(__file__))
        target = os.path.join(base, "video_downloader.py")
        if os.path.isfile(target):
            import importlib.util

            module = sys.modules.get("_sourcecraft_main_downloader")
            if module is None:
                spec = importlib.util.spec_from_file_location(
                    "_sourcecraft_main_downloader", target
                )
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
            return module.DownloadThread

        raise RuntimeError("未找到可用的 DownloadThread 实现")

    def _start_batch(self):
        if self._running:
            return

        main_thread = getattr(self.app, "download_thread", None)
        if main_thread is not None and main_thread.isRunning():
            QMessageBox.information(
                self,
                t("提示"),
                t("当前有下载任务正在进行，请先取消后再使用批量下载。"),
            )
            return

        valid, _duplicate, _invalid = self._parse_urls()
        if not valid:
            QMessageBox.warning(self, t("提示"), t("请先输入至少一个有效链接"))
            return

        self._tasks = [_BatchTask(i + 1, url) for i, url in enumerate(valid)]
        self._current_index = -1
        self._current_thread = None
        self._refresh_table()
        self._set_running_ui(True)
        self._log(t_format("开始批量下载，共 {} 个任务", len(self._tasks)))
        self._start_next()

    def _start_next(self):
        if not self._running:
            return

        idx = next(
            (i for i, task in enumerate(self._tasks) if task.status == STATUS_WAITING),
            None,
        )
        if idx is None:
            self._finish_batch()
            return

        task = self._tasks[idx]
        self._current_index = idx
        task.status = STATUS_DOWNLOADING
        task.progress = 0.0
        task.message = ""
        self._update_row(idx)
        self._log(t_format("开始下载 [{}/{}] {}", idx + 1, len(self._tasks), task.url))
        self.current_label.setText(t_format("当前任务：{}", task.url))
        self.progress_bar.setValue(0)

        try:
            output_path = (self.app.output_path.text() or "").strip()
            format_option = self.app.get_format_option()
            additional_options = self.app.get_additional_options()
            playlist_enabled = bool(self.app.playlist_check.isChecked())
        except Exception as exc:
            task.status = STATUS_FAILED
            task.message = t_format("读取下载参数失败: {}", exc)
            self._update_row(idx)
            self._schedule_next()
            return

        if not output_path:
            task.status = STATUS_FAILED
            task.message = t("未设置输出目录")
            self._update_row(idx)
            self._schedule_next()
            return

        try:
            thread_class = self._load_thread_class()
            # 记录下载前的目录快照，用于下载完成后定位本次真实产物
            task.before_files = self._snapshot_output_files(output_path)
            thread = thread_class(
                task.url,
                output_path,
                format_option,
                additional_options,
                playlist_enabled=playlist_enabled,
                main_app=self.app,
            )
        except Exception as exc:
            task.status = STATUS_FAILED
            task.message = t_format("创建下载线程失败: {}", exc)
            self._update_row(idx)
            self._schedule_next()
            return

        thread.progress_signal.connect(lambda msg, i=idx: self._on_progress(i, msg))
        thread.progress_percent_signal.connect(lambda pct, i=idx: self._on_percent(i, pct))
        thread.finished_signal.connect(
            lambda ok, msg, i=idx: self._on_finished(i, bool(ok), msg)
        )
        self._current_thread = thread
        thread.start()

    def _schedule_next(self):
        QTimer.singleShot(0, self._start_next)

    # ------------------------------------------------------------ signals

    def _on_progress(self, idx, message):
        if idx != self._current_index:
            return
        text = str(message or "").strip()
        if not text:
            return
        now = time.time()
        important = any(
            key in text
            for key in ("错误", "失败", "完成", "ERROR", "Error", "✓", "⚠")
        )
        if not important and now - self._last_log_ts < 0.1:
            return
        self._last_log_ts = now
        self._log(text)

    def _on_percent(self, idx, percent):
        if idx != self._current_index:
            return
        try:
            value = float(percent)
        except (TypeError, ValueError):
            return
        now = time.time()
        if now - self._last_percent_ts < 0.1:
            return
        self._last_percent_ts = now
        value = max(0.0, min(100.0, value))
        self._tasks[idx].progress = value
        self.progress_bar.setValue(int(value))
        self._update_row(idx, status_only=True)

    def _on_finished(self, idx, success, message):
        if idx < 0 or idx >= len(self._tasks):
            return
        task = self._tasks[idx]
        if task.status in (STATUS_CANCELLED, STATUS_PAUSED, STATUS_SKIPPED):
            return

        if success:
            task.status = STATUS_SUCCESS
            task.progress = 100.0
            task.message = self._success_summary()
            self._record_history(task)
            self.progress_bar.setValue(100)
        else:
            if self.auto_retry.isChecked() and task.retry_count < self.MAX_RETRY:
                task.retry_count += 1
                task.status = STATUS_WAITING
                task.message = t("失败，正在重试")
                self._log(t_format("任务失败，自动重试一次：{}", task.url))
            else:
                task.status = STATUS_FAILED
                task.message = self._brief(message)

        self._update_row(idx)
        self._update_summary()
        # 保留局部引用直到线程真正结束，避免 QThread 在运行中被回收
        old_thread = self._current_thread
        self._current_thread = None
        if old_thread is not None:
            try:
                old_thread.wait(2000)
            except Exception:
                pass
        if task.status != STATUS_SUCCESS:
            self.progress_bar.setValue(0)
        self._schedule_next()

    # ------------------------------------------------------------- actions

    def _stop_batch(self):
        if not self._running and self._current_thread is None:
            return

        thread = self._current_thread
        self._running = False
        if thread is not None and thread.isRunning():
            try:
                thread.stop()
                thread.wait(2000)
            except Exception as exc:
                self._log(t_format("停止当前任务失败: {}", exc))

        for task in self._tasks:
            if task.status in (STATUS_WAITING, STATUS_DOWNLOADING, STATUS_PAUSED):
                task.status = STATUS_CANCELLED
                task.message = t("已取消")

        self._current_thread = None
        self._current_index = -1
        self._refresh_table()
        self._set_running_ui(False)
        self._update_summary()
        self.progress_bar.setValue(0)
        self.current_label.setText(t("当前任务：无"))
        self._log(t("已停止批量下载"))

    def _finish_batch(self):
        self._running = False
        self._current_index = -1
        self._current_thread = None
        self._set_running_ui(False)
        self._update_summary()

        success = sum(1 for task in self._tasks if task.status == STATUS_SUCCESS)
        failed = sum(1 for task in self._tasks if task.status == STATUS_FAILED)
        cancelled = sum(1 for task in self._tasks if task.status == STATUS_CANCELLED)
        paused = sum(1 for task in self._tasks if task.status == STATUS_PAUSED)
        self.current_label.setText(t("当前任务：无"))
        self._log(
            t_format(
                "批量任务结束：成功 {}，失败 {}，取消 {}，暂停 {}",
                success,
                failed,
                cancelled,
                paused,
            )
        )

        detail = t_format(
            "成功 {} 个，失败 {} 个，取消 {} 个，暂停 {} 个",
            success,
            failed,
            cancelled,
            paused,
        )
        QMessageBox.information(self, t("批量下载完成"), detail)

    def _pause_task(self, idx):
        task = self._tasks[idx]
        if task.status != STATUS_WAITING:
            return
        task.status = STATUS_PAUSED
        task.message = t("已暂停")
        self._update_row(idx)
        self._update_summary()

    def _resume_task(self, idx):
        task = self._tasks[idx]
        if task.status != STATUS_PAUSED:
            return
        task.status = STATUS_WAITING
        task.message = ""
        self._update_row(idx)
        self._update_summary()
        if self._running and self._current_index < 0:
            self._start_next()

    def _skip_task(self, idx):
        task = self._tasks[idx]
        if task.status not in (STATUS_WAITING, STATUS_PAUSED):
            return
        task.status = STATUS_SKIPPED
        task.message = t("已跳过")
        self._update_row(idx)
        self._update_summary()
        if self._running and self._current_index < 0:
            self._start_next()

    def _retry_task(self, idx):
        task = self._tasks[idx]
        if task.status not in (STATUS_FAILED, STATUS_CANCELLED, STATUS_SKIPPED):
            return
        task.status = STATUS_WAITING
        task.retry_count = 0
        task.message = ""
        task.progress = 0.0
        self._update_row(idx)
        self._update_summary()
        if not self._running:
            self._set_running_ui(True)
            self._start_next()

    def _copy_url(self, idx):
        from PyQt5.QtWidgets import QApplication

        QApplication.clipboard().setText(self._tasks[idx].url)
        self._log(t("链接已复制到剪贴板"))

    def _open_output_dir(self):
        try:
            path = (self.app.output_path.text() or "").strip()
        except Exception:
            path = ""
        if path and os.path.isdir(path):
            os.startfile(path)

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._tasks):
            return
        task = self._tasks[row]
        menu = QMenu(self)
        if task.status == STATUS_WAITING:
            menu.addAction(t("暂停"), lambda: self._pause_task(row))
            menu.addAction(t("跳过"), lambda: self._skip_task(row))
        elif task.status == STATUS_PAUSED:
            menu.addAction(t("继续"), lambda: self._resume_task(row))
            menu.addAction(t("跳过"), lambda: self._skip_task(row))
        if task.status in (STATUS_FAILED, STATUS_CANCELLED, STATUS_SKIPPED):
            menu.addAction(t("重试"), lambda: self._retry_task(row))
        menu.addSeparator()
        menu.addAction(t("复制链接"), lambda: self._copy_url(row))
        menu.addAction(t("打开下载目录"), self._open_output_dir)
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    # -------------------------------------------------------------- render

    def _set_running_ui(self, running):
        self._running = running
        if running:
            self.start_btn.setEnabled(False)
        else:
            valid, _duplicate, _invalid = self._parse_urls()
            self.start_btn.setEnabled(bool(valid))
        self.stop_btn.setEnabled(running)
        self.input_edit.setEnabled(not running)
        self.auto_retry.setEnabled(not running)
        try:
            self.app.download_button.setEnabled(not running)
        except Exception:
            pass

    def _refresh_table(self):
        self.table.setRowCount(len(self._tasks))
        for idx in range(len(self._tasks)):
            self._update_row(idx)

    def _update_row(self, idx, status_only=False):
        if idx < 0 or idx >= len(self._tasks):
            return
        task = self._tasks[idx]
        if not status_only:
            index_item = QTableWidgetItem(str(task.index))
            index_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(idx, 0, index_item)
            self.table.setItem(idx, 1, QTableWidgetItem(self._short_url(task.url)))
            self.table.setItem(idx, 3, QTableWidgetItem(task.message))

        status_item = QTableWidgetItem("● " + self._status_text(task))
        status_item.setForeground(QColor(_STATUS_COLORS.get(task.status, "#7a7a7a")))
        self.table.setItem(idx, 2, status_item)

        if task.status == STATUS_DOWNLOADING:
            self.table.selectRow(idx)
            self.table.scrollToItem(status_item, QAbstractItemView.PositionAtCenter)

    def _update_summary(self):
        total = len(self._tasks)
        done = sum(
            1
            for task in self._tasks
            if task.status in (STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED, STATUS_SKIPPED)
        )
        success = sum(1 for task in self._tasks if task.status == STATUS_SUCCESS)
        failed = sum(1 for task in self._tasks if task.status == STATUS_FAILED)
        paused = sum(1 for task in self._tasks if task.status == STATUS_PAUSED)

        self.summary_label.setText(
            t_format(
                "总进度 {}/{} · 成功 {} · 失败 {} · 暂停 {}",
                done,
                total,
                success,
                failed,
                paused,
            )
        )
        self.overall_bar.setRange(0, max(total, 1))
        self.overall_bar.setValue(done)

    def _status_text(self, task):
        if task.status == STATUS_DOWNLOADING:
            return t_format("下载中 {}%", int(task.progress))
        mapping = {
            STATUS_WAITING: t("等待"),
            STATUS_PAUSED: t("已暂停"),
            STATUS_SUCCESS: t("成功"),
            STATUS_FAILED: t("失败"),
            STATUS_CANCELLED: t("已取消"),
            STATUS_SKIPPED: t("已跳过"),
        }
        return mapping.get(task.status, t("等待"))

    @staticmethod
    def _short_url(url, limit=72):
        text = str(url or "")
        for prefix in ("https://", "http://"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):]
                break
        if len(text) > limit:
            text = text[: limit - 1] + "…"
        return text

    @staticmethod
    def _brief(message, limit=110):
        text = " ".join(str(message or "").split())
        if len(text) > limit:
            text = text[: limit - 1] + "…"
        return text

    def _success_summary(self):
        thread = self._current_thread
        if thread is None:
            return t("成功")
        title = ""
        for attr in ("current_video_title", "preferred_title"):
            try:
                value = getattr(thread, attr, "") or ""
            except Exception:
                value = ""
            if value:
                title = str(value)
                break
        return self._short_url(title) if title else t("成功")

    @staticmethod
    def _snapshot_output_files(output_path):
        """记录输出目录当前文件名单，用于后续识别本次新增产物。"""
        try:
            if output_path and os.path.isdir(output_path):
                return set(os.listdir(output_path))
        except Exception:
            pass
        return set()

    def _find_new_file(self, task, output_path):
        """在输出目录中找出本次任务新增的最终产物（绝对路径）。"""
        if not output_path or not os.path.isdir(output_path):
            return ""

        preferred = (
            ".mp4", ".mkv", ".webm", ".mov", ".flv",
            ".mp3", ".m4a", ".opus", ".wav", ".aac",
        )
        skip_suffix = (".part", ".ytdl", ".temp", ".tmp", ".m4s", ".ts")

        candidates = []
        try:
            names = os.listdir(output_path)
        except Exception:
            return ""

        for name in names:
            if name in task.before_files:
                continue
            lower = name.lower()
            if lower.endswith(skip_suffix):
                continue
            path = os.path.join(output_path, name)
            if not os.path.isfile(path):
                continue
            candidates.append(path)

        if not candidates:
            return ""

        def sort_key(path):
            lower = path.lower()
            ext_rank = len(preferred)
            for idx, ext in enumerate(preferred):
                if lower.endswith(ext):
                    ext_rank = idx
                    break
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            return (ext_rank, -mtime)

        candidates.sort(key=sort_key)
        return os.path.abspath(candidates[0])

    def _record_history(self, task):
        """写入历史记录；定位不到真实文件时跳过，避免产生无法定位的脏记录。"""
        try:
            try:
                output_path = (self.app.output_path.text() or "").strip()
            except Exception:
                output_path = ""

            file_path = self._find_new_file(task, output_path)
            if not file_path:
                self._log(t_format("未定位到下载文件，已跳过历史记录：{}", task.url))
                return

            title = ""
            thread = self._current_thread
            if thread is not None:
                title = str(getattr(thread, "current_video_title", "") or "")
            if not title:
                title = os.path.splitext(os.path.basename(file_path))[0]

            file_size = None
            try:
                file_size = os.path.getsize(file_path)
            except OSError:
                file_size = None

            self.app.add_download_to_history(
                task.url, title, file_path, file_size=file_size, format_info=t("批量下载")
            )
        except Exception:
            pass

    def _log(self, text):
        text = str(text or "").strip()
        if not text:
            return
        self.log_edit.appendPlainText(text)

    # ------------------------------------------------------------- events

    def closeEvent(self, event):
        if self._running:
            active = [
                task
                for task in self._tasks
                if task.status in (STATUS_WAITING, STATUS_DOWNLOADING)
            ]
            if active:
                reply = QMessageBox.question(
                    self,
                    t("确认关闭"),
                    t_format(
                        "还有 {} 个任务未完成，关闭将取消它们。已完成的文件会保留。",
                        len(active),
                    ),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    event.ignore()
                    return
            self._stop_batch()
        event.accept()
