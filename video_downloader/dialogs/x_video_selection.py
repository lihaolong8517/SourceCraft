from PyQt5.QtWidgets import QFrame, QMessageBox
"""X平台视频选择对话框"""
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QCheckBox, QScrollArea, QWidget)
from PyQt5.QtCore import Qt
from i18n import t, t_format

class XVideoSelectionDialog(QDialog):
    """X平台（Twitter）多视频推文 - 视频选择对话框"""

    def __init__(self, video_entries, parent=None):
        super().__init__(parent)
        self.video_entries = video_entries or []
        self.selected_indices = []  # 用户选择的视频索引列表（1-based，对应 --playlist-items）
        self.setWindowTitle(t("🐦 X平台多视频选择"))
        self.setMinimumSize(560, 320)
        self.resize(620, min(420, 180 + 52 * len(self.video_entries)))
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._init_ui()

    # ── UI ──────────────────────────────────────────────
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 顶部提示
        tip = QLabel(
            t_format("<b>该推文包含 {} 个视频</b>，请勾选要下载的视频：", len(self.video_entries))
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("font-size: 13px; padding: 6px 2px;")
        layout.addWidget(tip)

        # 视频列表区域（滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(4, 4, 4, 4)
        list_layout.setSpacing(6)

        self._checkboxes = []
        for idx, entry in enumerate(self.video_entries):
            cb = QCheckBox()
            label_text = self._build_entry_label(idx + 1, entry)
            cb.setText(label_text)
            cb.setChecked(True)  # 默认全部勾选
            cb.setStyleSheet("QCheckBox { font-size: 12px; padding: 4px 0; }")
            list_layout.addWidget(cb)
            self._checkboxes.append(cb)

        list_layout.addStretch()
        scroll.setWidget(list_widget)
        layout.addWidget(scroll, 1)

        # 底部按钮栏
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        select_all_btn = QPushButton(t("全选"))
        select_all_btn.clicked.connect(self._select_all)
        btn_layout.addWidget(select_all_btn)

        deselect_all_btn = QPushButton(t("取消全选"))
        deselect_all_btn.clicked.connect(self._deselect_all)
        btn_layout.addWidget(deselect_all_btn)

        btn_layout.addStretch()

        download_btn = QPushButton(t("📥 下载所选"))
        download_btn.setDefault(True)
        download_btn.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px 18px; }"
        )
        download_btn.clicked.connect(self._accept_selection)
        btn_layout.addWidget(download_btn)

        cancel_btn = QPushButton(t("取消"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    # ── helpers ─────────────────────────────────────────
    @staticmethod
    def _build_entry_label(index, entry):
        """为每个视频条目构建可读标签"""
        title = entry.get('title') or entry.get('fulltitle') or t_format("视频 {}", index)
        # 截断过长标题
        if len(title) > 60:
            title = title[:57] + "..."

        parts = [t_format("视频 {}: {}", index, title)]

        # 时长
        duration = entry.get('duration')
        if duration and isinstance(duration, (int, float)) and duration > 0:
            mins, secs = divmod(int(duration), 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                parts.append(f"{hours}:{mins:02d}:{secs:02d}")
            else:
                parts.append(f"{mins}:{secs:02d}")

        # 分辨率
        height = entry.get('height')
        width = entry.get('width')
        if height and width:
            parts.append(f"{width}x{height}")
        elif height:
            parts.append(f"{height}p")

        return "  |  ".join(parts)

    def _select_all(self):
        for cb in self._checkboxes:
            cb.setChecked(True)

    def _deselect_all(self):
        for cb in self._checkboxes:
            cb.setChecked(False)

    def _accept_selection(self):
        self.selected_indices = [
            i + 1 for i, cb in enumerate(self._checkboxes) if cb.isChecked()
        ]
        if not self.selected_indices:
            QMessageBox.warning(self, t("提示"), t("请至少选择一个视频"))
            return
        self.accept()

    def get_playlist_items_str(self):
        """返回可直接传给 --playlist-items 的字符串，如 '1,3' 或 ''（全选时返回空表示不限制）"""
        if len(self.selected_indices) == len(self.video_entries):
            return ""  # 全选 → 不需要 --playlist-items，直接 --yes-playlist
        return ",".join(str(i) for i in sorted(self.selected_indices))


