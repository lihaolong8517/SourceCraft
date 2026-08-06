from PyQt5.QtWidgets import QFrame
"""失败视频列表对话框"""
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QTableWidget, QTableWidgetItem, 
                             QHeaderView, QAbstractItemView, QFileDialog, QMessageBox)
from PyQt5.QtCore import Qt
from datetime import datetime

class FailedVideosDialog(QDialog):
    """显示播放列表中失败视频的对话框"""
    
    def __init__(self, failed_videos, total_videos=0, successful_downloads=0, expected_downloads=0, parent=None):
        super().__init__(parent)
        self.failed_videos = failed_videos
        self.total_videos = total_videos
        self.successful_downloads = successful_downloads
        self.expected_downloads = expected_downloads  # 用户设置的预期下载数量
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle(t("播放列表下载报告"))
        self.setModal(True)
        self.resize(900, 600)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 顶部统计信息
        stats_frame = QFrame()
        stats_frame.setStyleSheet(
            "QFrame { "
            "background-color: #f8f9fa; "
            "border: 2px solid #dee2e6; "
            "border-radius: 10px; "
            "padding: 15px; "
            "}"
        )
        stats_layout = QVBoxLayout(stats_frame)
        
        # 标题
        title_label = QLabel(t("📊 播放列表下载完成"))
        title_label.setStyleSheet(
            "font-size: 18px; "
            "font-weight: bold; "
            "color: #2c3e50; "
            "margin-bottom: 10px;"
        )
        stats_layout.addWidget(title_label)
        
        # 【精准统计修复】使用准确的统计数据并提供清晰的说明
        # 成功下载数：直接使用传递的successful_downloads（已经在下载过程中准确统计）
        actual_successful = self.successful_downloads if self.successful_downloads > 0 else 0
        
        # 【关键修复】分别统计失败和跳过的视频
        actual_failed = 0
        actual_skipped = 0
        
        if self.failed_videos:
            for video in self.failed_videos:
                reason = video.get('reason', '').lower()
                # 判断是失败还是跳过
                if any(keyword in reason for keyword in ['跳过', 'skipped', '限制', '年龄', '地区', '会员', 'restricted', 'age', 'geo', 'membership', 'premium']):
                    actual_skipped += 1
                else:
                    actual_failed += 1
        
        # 总计处理数 = 成功下载数 + 失败数 + 跳过数（实际尝试下载的视频数）
        actual_total = actual_successful + actual_failed + actual_skipped
        
        # 【关键修复】预计下载数：优先使用用户设置的范围（expected_downloads）
        # 如果用户设置了播放列表范围（如1-10），expected_downloads就是范围内的数量
        expected_count = self.expected_downloads if self.expected_downloads > 0 else self.total_videos
        
        # 如果预计下载数为0但实际有处理数，使用实际处理数作为预计数
        if expected_count == 0 and actual_total > 0:
            expected_count = actual_total
        
        # 【关键修复】构建统计信息文本 - 区分"设置范围"和"整个播放列表"的情况
        # 判断是否设置了范围：优先检查expected_downloads，但更准确的是检查是否与total_videos不同
        # 如果expected_downloads > 0 且 < total_videos，说明用户设置了范围
        has_range_set = (self.expected_downloads > 0 and 
                        self.expected_downloads < self.total_videos and 
                        self.total_videos > 0)
        
        if has_range_set:
            # 【情况1】用户设置了范围（如1-10），基于范围统计
            # 跳过数 = 范围内预期下载数 - 实际处理数（成功+失败）
            skipped_in_range = max(0, expected_count - actual_total)
            
            # 统计因限制失败的视频数（年龄限制、地区限制等）
            restriction_failed = sum(1 for v in self.failed_videos 
                                   if any(keyword in v.get('reason', '') 
                                         for keyword in ['限制', '年龄', '地区', '会员', 'restricted', 
                                                        'age', 'geo', 'membership', 'premium']))
            
            # 统计因限制跳过的视频数（这些视频被记录在失败列表中，但原因是跳过）
            restriction_skipped = sum(1 for v in self.failed_videos 
                                   if any(keyword in v.get('reason', '') 
                                         for keyword in ['跳过', 'skipped', '限制', '年龄', '地区', '会员', 'restricted', 
                                                        'age', 'geo', 'membership', 'premium']))
            
            # 【简化】只显示用户要求的4个核心信息：预计下载数、成功下载数、失败下载数、失败详情
            stats_text = (
                t_format("🎯 <b>预计下载:</b> {} 个视频<br>✅ <b>成功下载:</b> {} 个视频<br>❌ <b>下载失败:</b> {} 个视频", expected_count, actual_successful, actual_failed)

            )
            
        else:
            # 【情况2】没有设置范围，或范围等于总数，正常统计
            playlist_total = self.total_videos if self.total_videos > 0 else expected_count
            
            if playlist_total > actual_total and playlist_total > 0:
                # 有视频被过滤的情况，显示详细说明
                skipped = playlist_total - actual_total
                
                # 【关键修复】分别统计失败和跳过的视频
                actual_failed = 0
                actual_skipped = 0
                
                if self.failed_videos:
                    for video in self.failed_videos:
                        reason = video.get('reason', '').lower()
                        # 判断是失败还是跳过
                        if any(keyword in reason for keyword in ['跳过', 'skipped', '限制', '年龄', '地区', '会员', 'restricted', 'age', 'geo', 'membership', 'premium']):
                            actual_skipped += 1
                        else:
                            actual_failed += 1
                
                # 【简化】只显示用户要求的4个核心信息
                stats_text = (
                    t_format("🎯 <b>预计下载:</b> {} 个视频<br>✅ <b>成功下载:</b> {} 个视频<br>❌ <b>下载失败:</b> {} 个视频", expected_count, actual_successful, actual_failed)

                )
            else:
                # 没有过滤的情况，正常显示
                # 【关键修复】分别统计失败和跳过的视频
                actual_failed = 0
                actual_skipped = 0
                
                if self.failed_videos:
                    for video in self.failed_videos:
                        reason = video.get('reason', '').lower()
                        # 判断是失败还是跳过
                        if any(keyword in reason for keyword in ['跳过', 'skipped', '限制', '年龄', '地区', '会员', 'restricted', 'age', 'geo', 'membership', 'premium']):
                            actual_skipped += 1
                        else:
                            actual_failed += 1
                
                # 【简化】只显示用户要求的4个核心信息
                stats_text = (
                    t_format("🎯 <b>预计下载:</b> {} 个视频<br>✅ <b>成功下载:</b> {} 个视频<br>❌ <b>下载失败:</b> {} 个视频", expected_count, actual_successful, actual_failed)

                )
        
        stats_info = QLabel(stats_text)
        stats_info.setStyleSheet(
            "font-size: 14px; "
            "line-height: 1.6; "
            "color: #495057;"
        )
        stats_info.setWordWrap(True)
        stats_layout.addWidget(stats_info)
        
        layout.addWidget(stats_frame)
        
        # 失败视频列表标题（只在有失败视频时显示）
        if self.failed_videos:
            # 【简化】只显示"失败视频详情"，因为跳过的视频不再显示在这里
            list_title = QLabel(t_format("⚠️ 失败视频详情 ({} 个):", len(self.failed_videos)))
                
            list_title.setStyleSheet(
                "font-size: 16px; "
                "font-weight: bold; "
                "color: #e74c3c; "
                "margin-top: 10px;"
            )
            layout.addWidget(list_title)
            
            # 创建表格显示失败视频
            self.table = QTableWidget()
            self.table.setColumnCount(3)
            # 【简化】表头只显示失败视频信息
            self.table.setHorizontalHeaderLabels(["#", t("视频标题"), t("失败原因")])
            self.table.setRowCount(len(self.failed_videos))
            
            # 设置表格样式
            self.table.setStyleSheet(
                "QTableWidget { "
                "border: 1px solid #dee2e6; "
                "border-radius: 5px; "
                "gridline-color: #dee2e6; "
                "background-color: white; "
                "} "
                "QTableWidget::item { "
                "padding: 8px; "
                "} "
                "QHeaderView::section { "
                "background-color: #f8f9fa; "
                "padding: 10px; "
                "border: none; "
                "border-bottom: 2px solid #dee2e6; "
                "font-weight: bold; "
                "color: #495057; "
                "}"
            )
            
            # 填充表格数据
            for i, video in enumerate(self.failed_videos):
                # 序号
                index_item = QTableWidgetItem(str(i + 1))
                index_item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, 0, index_item)
                
                # 视频标题
                title_item = QTableWidgetItem(video.get('title', t('未知视频')))
                title_item.setToolTip(video.get('title', t('未知视频')))
                self.table.setItem(i, 1, title_item)
                
                # 失败原因
                reason_item = QTableWidgetItem(video.get('reason', t('未知错误')))
                reason_item.setToolTip(video.get('full_error', video.get('reason', t('未知错误'))))
                self.table.setItem(i, 2, reason_item)
            
            # 调整列宽
            self.table.setColumnWidth(0, 50)
            self.table.horizontalHeader().setStretchLastSection(False)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
            
            # 设置行高
            self.table.verticalHeader().setDefaultSectionSize(40)
            self.table.verticalHeader().setVisible(False)
            
            # 设置选择模式
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            
            layout.addWidget(self.table)
        else:
            # 如果没有失败视频，显示成功消息
            success_label = QLabel(t("✅ 所有视频均成功下载！"))
            success_label.setStyleSheet(
                "font-size: 16px; "
                "font-weight: bold; "
                "color: #2ecc71; "
                "margin-top: 10px; "
                "padding: 20px; "
                "background-color: #d4edda; "
                "border: 1px solid #c3e6cb; "
                "border-radius: 5px;"
            )
            success_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(success_label)
        
        # 底部提示信息（只在有失败视频时显示）
        if self.failed_videos:
            try:
                url_lower = ""
                if self.parent() and hasattr(self.parent(), 'url_input'):
                    url_lower = self.parent().url_input.text().strip().lower()
                
                is_target_platform = any(domain in url_lower for domain in [
                    'douyin.com', 'iesdouyin.com',
                    'kuaishou.com', 'gifshow.com', 'kwai.com',
                    'xiaohongshu.com', 'xhslink.com'
                ])
            except Exception:
                is_target_platform = False

            hint_text = (
                "💡 <b>提示:</b> 失败的视频可能是由于会员限制、地区限制、年龄限制等原因。<br>"
                "您可以尝试使用代理、Cookie登录或更换视频格式来解决问题。"
            )
            if is_target_platform:
                hint_text += "<br><font color='#e74c3c'><b>💡 提示：抖音、快手、小红书请尝试左上角媒体捕捉功能下载</b></font>"

            hint_label = QLabel(hint_text)
            hint_label.setStyleSheet(
                "QLabel { "
                "background-color: #fff3cd; "
                "border: 1px solid #ffc107; "
                "border-radius: 5px; "
                "padding: 10px; "
                "font-size: 12px; "
                "color: #856404; "
                "}"
            )
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        # 导出按钮（只在有失败视频时显示）
        if self.failed_videos:
            export_btn = QPushButton(t("📄 导出失败列表"))
            export_btn.setStyleSheet(
                "QPushButton { "
                "background-color: #3498db; "
                "color: white; "
                "border: none; "
                "padding: 10px 20px; "
                "border-radius: 5px; "
                "font-size: 13px; "
                "font-weight: bold; "
                "} "
                "QPushButton:hover { "
                "background-color: #2980b9; "
                "} "
                "QPushButton:pressed { "
                "background-color: #1c6ea4; "
                "}"
            )
            export_btn.clicked.connect(self.export_failed_list)
            button_layout.addWidget(export_btn)
        
        # 关闭按钮
        close_btn = QPushButton(t("✓ 确定"))
        close_btn.setStyleSheet(
            "QPushButton { "
            "background-color: #2ecc71; "
            "color: white; "
            "border: none; "
            "padding: 10px 20px; "
            "border-radius: 5px; "
            "font-size: 13px; "
            "font-weight: bold; "
            "} "
            "QPushButton:hover { "
            "background-color: #27ae60; "
            "} "
            "QPushButton:pressed { "
            "background-color: #1e8449; "
            "}"
        )
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        
    def export_failed_list(self):
        """导出失败视频列表到文本文件"""
        from PyQt5.QtWidgets import QFileDialog
        from datetime import datetime
        
        # 默认文件名
        default_filename = f"failed_videos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        # 打开保存文件对话框
        filename, _ = QFileDialog.getSaveFileName(
            self,
            t("导出失败视频列表"),
            default_filename,
            t("文本文件 (*.txt);;所有文件 (*.*)")
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write("=" * 60 + "\n")
                    f.write("播放列表下载失败视频报告\n")
                    f.write("=" * 60 + "\n\n")
                    
                    # 统计信息
                    has_range_set = self.expected_downloads > 0 and self.expected_downloads < self.total_videos
                    if has_range_set:
                        f.write(t_format("播放列表总数: {}\n", self.total_videos))
                        f.write(t_format("设置范围: {} 个视频\n", self.expected_downloads))
                    else:
                        if self.expected_downloads > 0:
                            f.write(t_format("预计下载数量: {}\n", self.expected_downloads))
                        if self.total_videos > 0:
                            f.write(t_format("总视频数: {}\n", self.total_videos))
                    
                    if self.successful_downloads > 0:
                        f.write(t_format("成功下载: {}\n", self.successful_downloads))
                    f.write(t_format("下载失败: {}\n", len(self.failed_videos)))
                    
                    # 统计因限制失败的视频数
                    restriction_failed = sum(1 for v in self.failed_videos 
                                           if any(keyword in v.get('reason', '') 
                                                 for keyword in ['限制', '年龄', '地区', '会员', 'restricted', 
                                                                'age', 'geo', 'membership', 'premium']))
                    if restriction_failed > 0:
                        f.write(t_format("  - 其中因限制失败: {}\n", restriction_failed))
                    
                    f.write("\n")
                    
                    f.write("-" * 60 + "\n")
                    f.write("失败视频详情:\n")
                    f.write("-" * 60 + "\n\n")
                    
                    for i, video in enumerate(self.failed_videos, 1):
                        f.write(t_format("{}. {}\n", i, video.get('title', '未知视频')))
                        f.write(t_format("   原因: {}\n", video.get('reason', '未知错误')))
                        if video.get('full_error'):
                            f.write(t_format("   详情: {}\n", video.get('full_error')))
                        f.write("\n")
                
                QMessageBox.information(
                    self,
                    t("导出成功"),
                    t_format("失败视频列表已成功导出到:\n{}", filename)
                )
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "导出失败",
                    t_format("导出文件时出错:\n{}", str(e))
                )

