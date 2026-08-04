"""播放列表范围选择对话框"""
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QLineEdit, QMessageBox, QSizePolicy)
from PyQt5.QtCore import Qt
import re

class PlaylistRangeDialog(QDialog):
    """播放列表范围选择对话框"""
    
    def __init__(self, playlist_info, parent=None):
        super().__init__(parent)
        self.playlist_info = playlist_info
        self.selected_indices = set()
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("播放列表范围选择")
        self.setModal(True)
        self.resize(800, 600)
        
        layout = QVBoxLayout(self)
        
        # 播放列表信息
        info_label = QLabel(f"播放列表: {self.playlist_info.get('title', '未知播放列表')}")
        info_label.setStyleSheet("font-weight: bold; font-size: 14px; margin-bottom: 10px;")
        layout.addWidget(info_label)
        
        entries = self.playlist_info.get('entries', [])
        count_label = QLabel(f"总共 {len(entries)} 个视频")
        layout.addWidget(count_label)
        
        # 快速选择按钮
        button_layout = QHBoxLayout()
        
        select_all_btn = QPushButton("全选")
        select_all_btn.setAutoDefault(False)  # 禁用自动默认
        select_all_btn.clicked.connect(self.select_all)
        button_layout.addWidget(select_all_btn)
        
        select_none_btn = QPushButton("全不选")
        select_none_btn.setAutoDefault(False)  # 禁用自动默认
        select_none_btn.clicked.connect(self.select_none)
        button_layout.addWidget(select_none_btn)
        
        select_range_btn = QPushButton("选择范围")
        select_range_btn.setAutoDefault(False)  # 禁用自动默认
        select_range_btn.clicked.connect(self.select_range)
        button_layout.addWidget(select_range_btn)
        
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        # 视频列表
        self.video_list = QListWidget()
        self.video_list.setSelectionMode(QAbstractItemView.MultiSelection)
        
        # 添加调试信息
        if not entries:
            debug_item = QListWidgetItem("没有找到视频条目，请检查播放列表URL")
            debug_item.setData(Qt.UserRole, 0)
            self.video_list.addItem(debug_item)
        else:
            for i, entry in enumerate(entries):
                title = entry.get('title', f'视频 #{i+1}')
                duration = entry.get('duration_string', entry.get('duration', ''))
                
                # 构建显示文本
                if duration:
                    if isinstance(duration, (int, float)):
                        # 如果duration是数字（秒），转换为时间格式
                        minutes = int(duration // 60)
                        seconds = int(duration % 60)
                        duration_str = f"{minutes:02d}:{seconds:02d}"
                    else:
                        duration_str = str(duration)
                    item_text = f"{i+1:3d}. {title} ({duration_str})"
                else:
                    item_text = f"{i+1:3d}. {title}"
                
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, i+1)  # 存储1基索引
                self.video_list.addItem(item)
        
        layout.addWidget(self.video_list)
        
        # 范围输入
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel("手动输入范围:"))
        self.range_input = QLineEdit()
        self.range_input.setPlaceholderText("例如: 1-10,15,20-25")
        self.range_input.returnPressed.connect(self.apply_range_input)  # 按回车键应用范围
        range_layout.addWidget(self.range_input)
        
        apply_range_btn = QPushButton("应用范围")
        apply_range_btn.setAutoDefault(False)  # 禁用自动默认
        apply_range_btn.clicked.connect(self.apply_range_input)
        range_layout.addWidget(apply_range_btn)
        
        layout.addLayout(range_layout)
        
        # 确定/取消按钮
        button_layout2 = QHBoxLayout()
        button_layout2.addStretch()
        
        ok_btn = QPushButton("确定")
        ok_btn.setDefault(True)  # 设置为默认按钮
        ok_btn.clicked.connect(self.accept)
        button_layout2.addWidget(ok_btn)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setAutoDefault(False)  # 禁用自动默认
        cancel_btn.clicked.connect(self.reject)
        button_layout2.addWidget(cancel_btn)
        
        layout.addLayout(button_layout2)
        
    def select_all(self):
        """全选"""
        for i in range(self.video_list.count()):
            self.video_list.item(i).setSelected(True)
            
    def select_none(self):
        """全不选"""
        self.video_list.clearSelection()
        
    def select_range(self):
        """选择范围对话框"""
        from PyQt5.QtWidgets import QInputDialog
        
        text, ok = QInputDialog.getText(self, '选择范围', 
                                       '请输入范围 (例如: 1-10,15,20-25):')
        if ok and text:
            self.range_input.setText(text)
            self.apply_range_input()
            
    def apply_range_input(self):
        """应用手动输入的范围"""
        range_text = self.range_input.text().strip()
        if not range_text:
            return
            
        try:
            indices = self.parse_range(range_text)
            self.video_list.clearSelection()
            
            for i in range(self.video_list.count()):
                item = self.video_list.item(i)
                index = item.data(Qt.UserRole)
                if index in indices:
                    item.setSelected(True)
                    
        except Exception as e:
            QMessageBox.warning(self, "错误", f"范围格式错误: {str(e)}")
            
    def parse_range(self, range_text):
        """解析范围字符串"""
        indices = set()
        
        for part in range_text.split(','):
            part = part.strip()
            if '-' in part:
                start, end = part.split('-', 1)
                start = int(start.strip())
                end = int(end.strip())
                indices.update(range(start, end + 1))
            else:
                indices.add(int(part))
                
        return indices
        
    def get_selected_range(self):
        """获取选中的范围字符串"""
        selected_items = self.video_list.selectedItems()
        if not selected_items:
            return ""
            
        indices = []
        for item in selected_items:
            index = item.data(Qt.UserRole)
            indices.append(index)
            
        indices.sort()
        
        # 将连续的数字合并为范围
        ranges = []
        start = indices[0]
        end = indices[0]
        
        for i in range(1, len(indices)):
            if indices[i] == end + 1:
                end = indices[i]
            else:
                if start == end:
                    ranges.append(str(start))
                else:
                    ranges.append(f"{start}-{end}")
                start = indices[i]
                end = indices[i]
                
        # 添加最后一个范围
        if start == end:
            ranges.append(str(start))
        else:
            ranges.append(f"{start}-{end}")
            
        return ','.join(ranges)

