from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                             QSizePolicy, QDialogButtonBox, QLineEdit, QComboBox, QCheckBox,
                             QGroupBox, QGridLayout, QSpinBox, QTabWidget, QWidget, QTextEdit,
                             QSplitter, QFrame, QApplication)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont
import time

class AdvancedFormatDialog(QDialog):
    """高级格式选择对话框 - 基于yt-dlp官方文档优化"""
    
    format_selected = pyqtSignal(str)  # 发送选择的格式表达式
    
    def __init__(self, formats, parent=None):
        super().__init__(parent)
        self.formats = formats
        self.filtered_formats = formats.copy()
        self.original_order = formats.copy()  # 保持原始顺序用于重置
        self.selected_format_expression = None
        self.sort_cache = {}  # 排序缓存，避免重复排序
        self.table_items_cache = {}  # 表格项缓存，提高性能
        self.setWindowTitle("高级格式选择器 - 基于yt-dlp官方规范")
        self.resize(1200, 800)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(800, 500)
        
        self.initUI()
        self.populate_table()
    
    def update_table_items(self):
        """更新表格项并尽量保持当前选中项"""
        if not self.filtered_formats:
            self.table.setRowCount(0)
            self._adjust_table_layout()
            return
        
        # 获取当前选中的行
        selected_rows = self.table.selectionModel().selectedRows()
        selected_format_ids = []
        if selected_rows:
            for row in selected_rows:
                if row.row() < self.table.rowCount():
                    item = self.table.item(row.row(), 0)
                    if item:
                        selected_format_ids.append(item.text())
        
        # 统一重建表格，避免在用户排序后出现“行号与数据索引错位”
        self.populate_table()
        
        # 恢复选中状态
        if selected_format_ids:
            for i in range(self.table.rowCount()):
                item = self.table.item(i, 0)
                if item and item.text() in selected_format_ids:
                    self.table.selectRow(i)
                    break
        self._adjust_table_layout()
    
    def _update_table_row(self, row, fmt):
        """更新单行表格数据"""
        format_id = fmt.get('format_id', '')
        
        # 只更新变化的项
        current_items = []
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                current_items.append(item.text())
        
        # 生成新数据
        new_data = self._generate_row_data(fmt)
        
        # 只更新变化的列
        for col, (old_val, new_val) in enumerate(zip(current_items, new_data)):
            if old_val != new_val:
                self.table.item(row, col).setText(new_val)
    
    def _generate_row_data(self, fmt):
        """生成单行表格数据"""
        # 格式ID
        format_id = fmt.get('format_id', '')
        
        # 构建描述文本用于智能提取（当结构化字段缺失时）
        format_desc = fmt.get('format', '') or ''
        format_note = fmt.get('format_note', '') or ''
        desc_text = f"{format_desc} {format_note}".lower()
        
        # 扩展名 - 直接从ext字段获取
        ext = fmt.get('ext', 'N/A')
        
        # 容器 - 优先从container字段获取，其次从ext推断
        container = fmt.get('container', '')
        if not container or container == 'none':
            ext_l = (ext or '').lower()
            container_map = {
                'mp4': 'mp4', 'm4a': 'mp4', 'webm': 'webm', 'mkv': 'matroska',
                'flv': 'flv', '3gp': '3gp', 'ogg': 'ogg', 'mp3': 'mp3', 'wav': 'wav'
            }
            container = container_map.get(ext_l, 'N/A')
        
        # 分辨率
        height = fmt.get('height')
        width = fmt.get('width')
        if height and width:
            resolution = f"{width}x{height}"
        elif height:
            resolution = f"{height}p"
        else:
            resolution = fmt.get('resolution') or 'N/A'
        
        # 编码器 - 优先从vcodec/acodec字段获取，如果都是none则从描述智能提取
        vcodec = fmt.get('vcodec', 'none')
        acodec = fmt.get('acodec', 'none')
        
        vcodec = (vcodec or 'none').strip()
        acodec = (acodec or 'none').strip()
        
        # 构建编码器显示字符串（仅当同时有视频和音频时才显示斜杠格式）
        if vcodec != 'none' and acodec != 'none':
            # 同时有视频和音频编码器
            codec = f"{vcodec}/{acodec}"
        elif vcodec != 'none':
            # 仅有视频编码器
            codec = vcodec
        elif acodec != 'none':
            # 仅有音频编码器
            codec = acodec
        else:
            # 当结构化字段都是none时，从描述中智能提取编码器信息
            import re
            # 视频编码器关键词（按常见度排序）
            v_patterns = [
                (r'\b(avc1?[\.\d]*)', 'avc'),  # avc1.640028 -> avc
                (r'\b(h\.?264)', 'h264'),
                (r'\b(h\.?265|hevc)', 'h265'),
                (r'\b(vp0?9)', 'vp9'),
                (r'\b(av0?1)', 'av1'),
                (r'\b(vp0?8)', 'vp8'),
            ]
            # 音频编码器关键词
            a_patterns = [
                (r'\b(mp4a[\.\d]*)', 'aac'),  # mp4a.40.2 -> aac
                (r'\b(aac)', 'aac'),
                (r'\b(opus)', 'opus'),
                (r'\b(vorbis)', 'vorbis'),
                (r'\b(mp3)', 'mp3'),
                (r'\b(flac)', 'flac'),
            ]
            
            v_found = None
            for pattern, name in v_patterns:
                if re.search(pattern, desc_text):
                    v_found = name
                    break
            
            a_found = None
            for pattern, name in a_patterns:
                if re.search(pattern, desc_text):
                    a_found = name
                    break
            
            if v_found and a_found:
                # 同时找到视频和音频编码器
                codec = f"{v_found}/{a_found}"
            elif v_found:
                # 仅找到视频编码器
                codec = v_found
            elif a_found:
                # 仅找到音频编码器
                codec = a_found
            else:
                codec = 'N/A'
        
        # 比特率 - 优先使用tbr，其次vbr+abr，最后从描述提取
        if fmt.get('bitrate_display') is not None:
            bitrate = fmt.get('bitrate_display')
        else:
            tbr = fmt.get('tbr')
            if tbr and tbr > 0:
                bitrate = f"{tbr:.0f}k"
            else:
                vbr = fmt.get('vbr', 0) or 0
                abr = fmt.get('abr', 0) or 0
                total_br = vbr + abr
                if total_br > 0:
                    bitrate = f"{total_br:.0f}k"
                else:
                    # 从描述中提取比特率（如 "2500k", "128kbps"）
                    import re
                    br_match = re.search(r'(\d+)\s*k(?:bps)?', desc_text)
                    if br_match:
                        bitrate = f"{br_match.group(1)}k"
                    else:
                        bitrate = 'N/A'
        
        # 文件大小 - 优先使用filesize字段，其次近似值，最后从描述提取
        filesize = fmt.get('filesize')
        fs_approx = fmt.get('filesize_approx') or fmt.get('fs_approx')
        
        # 忽略预设的size_display如果它是'/'
        size_display = fmt.get('size_display')
        if size_display == '/':
            size_display = None
        
        if size_display is not None:
            size_str = size_display
        else:
            # 优先使用精确文件大小
            if filesize and isinstance(filesize, (int, float)) and filesize > 0:
                if filesize >= 1024*1024*1024:
                    size_str = f"{filesize/(1024*1024*1024):.2f}GB"
                elif filesize >= 1024*1024:
                    size_str = f"{filesize/(1024*1024):.2f}MB"
                elif filesize >= 1024:
                    size_str = f"{filesize/1024:.2f}KB"
                else:
                    size_str = f"{filesize:.0f}B"
            # 其次使用近似文件大小
            elif fs_approx and isinstance(fs_approx, (int, float)) and fs_approx > 0:
                if fs_approx >= 1024*1024*1024:
                    size_str = f"~{fs_approx/(1024*1024*1024):.2f}GB"
                elif fs_approx >= 1024*1024:
                    size_str = f"~{fs_approx/(1024*1024):.2f}MB"
                elif fs_approx >= 1024:
                    size_str = f"~{fs_approx/1024:.2f}KB"
                else:
                    size_str = f"~{fs_approx:.0f}B"
            else:
                # 从描述中提取文件大小（如 "2.5MiB", "120.0MB", "~10MB", "2.5mb"）
                import re
                # 优先匹配带波浪号的或者MB/GB级别的（避免匹配到比特率）
                size_match = re.search(r'(~)(\d+(?:\.\d+)?)\s*(gb?|mb?)\b', desc_text, re.IGNORECASE)
                if not size_match:
                    # 匹配MB/GB（允许没有空格）
                    size_match = re.search(r'(\d+(?:\.\d+)?)(gb?|mb?)\b', desc_text, re.IGNORECASE)
                if not size_match:
                    # 匹配MiB/GiB（带空格）
                    size_match = re.search(r'(\d+(?:\.\d+)?)\s*(gib|mib)\b', desc_text, re.IGNORECASE)
                if size_match:
                    if len(size_match.groups()) == 3:
                        approx, num, unit = size_match.groups()
                    else:
                        approx = ''
                        num, unit = size_match.groups()
                    unit_lower = unit.lower()
                    # 统一单位映射
                    unit_map = {
                        'g': 'GB', 'gb': 'GB', 'gi': 'GB', 'gib': 'GiB',
                        'm': 'MB', 'mb': 'MB', 'mi': 'MB', 'mib': 'MiB',
                    }
                    unit_display = unit_map.get(unit_lower, unit.upper())
                    size_str = f"{approx}{num}{unit_display}"
                else:
                    size_str = 'N/A'
        
        # 语言 - 优先使用结构化字段，其次从描述中提取
        language = str(fmt.get('language') or '').strip().lower()
        if not language:
            import re
            lang_match = re.search(r'\[(\w{2,3})\]', desc_text)
            if lang_match:
                language = lang_match.group(1).lower()
        if not language:
            language = 'N/A'
        
        # 描述 - 构建完整描述
        if format_desc and format_note:
            description = f"{format_desc} - {format_note}"
        elif format_desc:
            description = format_desc
        elif format_note:
            description = format_note
        else:
            description = 'N/A'
        
        return [format_id, ext, container, resolution, codec, bitrate, size_str, language, description]

    def _parse_resolution_components(self, value):
        if not value:
            return None, None
        s = str(value).lower()
        w = None
        h = None
        if 'x' in s:
            parts = s.split('x', 1)
            try:
                w = int(''.join(ch for ch in parts[0] if ch.isdigit()))
            except Exception:
                w = None
            right = parts[1]
            try:
                h = int(''.join(ch for ch in right if ch.isdigit()))
            except Exception:
                h = None
        elif 'p' in s:
            num = ''.join(ch for ch in s if ch.isdigit())
            try:
                h = int(num) if num else None
            except Exception:
                h = None
        return w, h

    def _get_effective_height(self, fmt):
        h = fmt.get('height')
        if h:
            return h
        res = fmt.get('resolution') or fmt.get('format_note') or fmt.get('format')
        _, ph = self._parse_resolution_components(res)
        return ph

    def _get_effective_width(self, fmt):
        w = fmt.get('width')
        if w:
            return w
        res = fmt.get('resolution') or fmt.get('format_note') or fmt.get('format')
        pw, _ = self._parse_resolution_components(res)
        return pw
    
    def _get_codec_priority(self, codec):
        """获取编码器优先级 (基于yt-dlp官方规范)"""
        if not codec:
            return 999
        
        codec = codec.lower()
        
        # 视频编码器优先级 (av01 > vp9.2 > vp9 > h265 > h264 > vp8 > ...)
        video_priority = {
            'av01': 1, 'av1': 1,
            'vp9.2': 2,
            'vp9': 3,
            'h265': 4, 'hevc': 4,
            'h264': 5, 'avc': 5,
            'vp8': 6,
            'h263': 7,
            'theora': 8
        }
        
        # 音频编码器优先级 (flac > wav > opus > vorbis > aac > mp3 > ...)
        audio_priority = {
            'flac': 1, 'alac': 1,
            'wav': 2, 'aiff': 2,
            'opus': 3,
            'vorbis': 4,
            'aac': 5, 'mp4a': 5,
            'mp3': 6,
            'ac4': 7,
            'eac3': 8,
            'ac3': 9,
            'dts': 10
        }
        
        # 检查视频编码器
        for codec_name, priority in video_priority.items():
            if codec_name in codec:
                return priority
        
        # 检查音频编码器
        for codec_name, priority in audio_priority.items():
            if codec_name in codec:
                return priority + 50  # 音频编码器优先级较低
        
        return 999  # 未知编码器优先级最低
    
    def _get_protocol_priority(self, protocol):
        """获取协议优先级 (基于yt-dlp官方规范)"""
        if not protocol:
            return 999
        
        protocol = protocol.lower()
        
        # 协议优先级 (https/ftps > http/ftp > m3u8_native > m3u8 > ...)
        priority_map = {
            'https': 1, 'ftps': 1,
            'http': 2, 'ftp': 2,
            'm3u8_native': 3,
            'm3u8': 4,
            'http_dash_segments': 5, 'dash': 5,
            'websocket_frag': 6,
            'mms': 7, 'rtsp': 7,
            'f4f': 8, 'f4m': 8
        }
        
        for proto, priority in priority_map.items():
            if proto in protocol:
                return priority
        
        return 999  # 未知协议优先级最低
    
    def _get_hdr_priority(self, hdr_type):
        """获取HDR优先级 (基于yt-dlp官方规范)"""
        if not hdr_type:
            return 999
        
        hdr_type = hdr_type.upper()
        
        # HDR优先级 (DV > HDR12 > HDR10+ > HDR10 > HLG > SDR)
        priority_map = {
            'DV': 1, 'DOLBY VISION': 1,
            'HDR12': 2,
            'HDR10+': 3,
            'HDR10': 4,
            'HLG': 5,
            'SDR': 6
        }
        
        for hdr_name, priority in priority_map.items():
            if hdr_name in hdr_type:
                return priority
        
        return 999  # 未知HDR类型优先级最低

    def _get_ext_priority(self, fmt):
        """根据是否为视频/音频选择对应扩展名优先级"""
        ext = (fmt.get('ext') or '').lower()
        is_video = (fmt.get('vcodec') or 'none') != 'none'
        is_audio = (fmt.get('acodec') or 'none') != 'none' and not is_video

        # 视频扩展优先级 (默认) mp4 > mov > webm > flv > other
        vext_priority = {
            'mp4': 4,
            'mov': 3,
            'webm': 2,
            'flv': 1
        }

        # 音频扩展优先级 (默认) m4a > aac > mp3 > ogg > opus > webm > other
        aext_priority = {
            'm4a': 7,
            'aac': 6,
            'mp3': 5,
            'ogg': 4,
            'opus': 3,
            'webm': 2
        }

        if is_audio:
            return aext_priority.get(ext, 1)
        else:
            return vext_priority.get(ext, 1)
        
    def initUI(self):
        layout = QVBoxLayout(self)
        
        # 使用单列主布局
        
        # 左侧：格式列表和过滤器
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        
        # 格式过滤器组
        filter_group = QGroupBox("🔍 格式过滤器 (基于官方Filtering Formats)")
        filter_layout = QGridLayout(filter_group)
        
        # 基础过滤器
        filter_layout.addWidget(QLabel("分辨率:"), 0, 0)
        self.height_combo = QComboBox()
        self.height_combo.addItems(["全部", "≤4320p (8K)", "≤2160p (4K)", "≤1440p (2K)", 
                                   "≤1080p (FHD)", "≤720p (HD)", "≤480p (SD)", "≤360p", "≤240p"])
        
        # 添加防抖动定时器
        self.filter_timer = QTimer()
        self.filter_timer.setSingleShot(True)
        self.filter_timer.timeout.connect(self.apply_filters)
        
        # 使用定时器延迟过滤，避免频繁触发
        self.height_combo.currentTextChanged.connect(self.schedule_filter_update)
        filter_layout.addWidget(self.height_combo, 0, 1)
        
        filter_layout.addWidget(QLabel("编码器:"), 0, 2)
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["全部", "H.264 (AVC)", "H.265 (HEVC)", "VP9", "AV1", "VP8"])
        self.codec_combo.currentTextChanged.connect(self.schedule_filter_update)
        filter_layout.addWidget(self.codec_combo, 0, 3)
        
        filter_layout.addWidget(QLabel("容器:"), 1, 0)
        self.ext_combo = QComboBox()
        self.ext_combo.addItems(["全部", "mp4", "webm", "mkv", "flv", "3gp", "m4a", "mp3", "ogg"])
        self.ext_combo.currentTextChanged.connect(self.schedule_filter_update)
        filter_layout.addWidget(self.ext_combo, 1, 1)
        
        filter_layout.addWidget(QLabel("语言:"), 1, 2)
        self.language_combo = QComboBox()
        self.language_combo.addItems(["全部", "en", "zh", "ja", "es", "fr", "de", "pt", "ar", "hi"])
        self.language_combo.currentTextChanged.connect(self.schedule_filter_update)
        filter_layout.addWidget(self.language_combo, 1, 3)
        
        # 高级过滤器
        filter_layout.addWidget(QLabel("文件大小 (MB):"), 2, 0)
        self.filesize_min = QSpinBox()
        self.filesize_min.setRange(0, 10000)
        self.filesize_min.setSuffix(" MB")
        self.filesize_min.valueChanged.connect(self.schedule_filter_update)
        filter_layout.addWidget(self.filesize_min, 2, 1)
        
        self.filesize_max = QSpinBox()
        self.filesize_max.setRange(0, 10000)
        self.filesize_max.setValue(10000)
        self.filesize_max.setSuffix(" MB")
        self.filesize_max.valueChanged.connect(self.schedule_filter_update)
        filter_layout.addWidget(self.filesize_max, 2, 2)
        
        # 仅视频/仅音频过滤
        self.video_only_cb = QCheckBox("仅视频流")
        self.video_only_cb.stateChanged.connect(self.schedule_filter_update)
        filter_layout.addWidget(self.video_only_cb, 3, 0)
        
        self.audio_only_cb = QCheckBox("仅音频流")
        self.audio_only_cb.stateChanged.connect(self.schedule_filter_update)
        filter_layout.addWidget(self.audio_only_cb, 3, 1)
        
        # 重置过滤器按钮
        reset_btn = QPushButton("🔄 重置过滤器")
        reset_btn.clicked.connect(self.reset_filters)
        filter_layout.addWidget(reset_btn, 3, 2)
        
        # 添加过滤状态标签
        self.filter_status_label = QLabel("显示所有格式")
        self.filter_status_label.setStyleSheet("color: #666; font-size: 10px;")
        filter_layout.addWidget(self.filter_status_label, 3, 3)
        
        left_layout.addWidget(filter_group)
        
        # 格式表格
        table_label = QLabel("📋 可用格式列表")
        table_label.setFont(QFont("", 10, QFont.Bold))
        left_layout.addWidget(table_label)
        
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "格式ID", "扩展名", "容器", "分辨率", "编码器", "比特率", "文件大小", "语言", "描述"
        ])
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        
        # 设置表格列宽
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 格式ID
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 扩展名
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 容器
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 分辨率
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 编码器
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 比特率
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 文件大小
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 语言
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)  # 描述
        header.setStretchLastSection(True)

        # 横向滚动条与视觉提示
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setStyleSheet(self.table.styleSheet() + "\n"
            "QScrollBar:horizontal { height: 12px; background: transparent; }\n"
            "QScrollBar::handle:horizontal { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #eeeeee, stop:1 #cccccc); border-radius: 6px; }\n"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { background: none; width: 0; }\n"
            "QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal { width: 0; height: 0; }\n"
        )
        
        self.table.cellDoubleClicked.connect(self.on_format_double_click)
        self.table.itemSelectionChanged.connect(self.on_format_selection_changed)
        left_layout.addWidget(self.table)

        self.table.setColumnHidden(0, True)
        self.table.setColumnHidden(5, True)
        
        layout.addWidget(left_widget)
        
        # 已移除右侧面板，避免空白区域
        
        # 底部按钮
        button_layout = QHBoxLayout()
        
        # 快速选择按钮
        quick_best_btn = QPushButton("🚀 快速选择最佳")
        quick_best_btn.clicked.connect(lambda: self.accept_format("bv*+ba/b"))
        button_layout.addWidget(quick_best_btn)
        
        button_layout.addStretch()
        
        # 标准对话框按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept_selection)
        button_box.rejected.connect(self.reject)
        button_layout.addWidget(button_box)
        
        layout.addLayout(button_layout)
    
    def populate_table(self):
        """填充格式表格 - 高性能版本"""
        # 暂时禁用表格更新以提高性能
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)  # 禁用内部排序以提高性能
        
        # 清空表格但不重新分配内存
        self.table.clearContents()
        row_count = len(self.filtered_formats)
        self.table.setRowCount(row_count)
        
        # 批量创建表格项目，使用预计算的缓存
        for i, fmt in enumerate(self.filtered_formats):
            row_data = self._generate_row_data(fmt)
            
            # 批量设置项目，减少函数调用开销
            for col, data in enumerate(row_data):
                item = QTableWidgetItem(data)
                if col == 0:
                    item.setData(Qt.UserRole, fmt)
                if col in (3, 4, 6, 7):
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self.table.setItem(i, col, item)
        
        # 重新启用表格更新和排序
        self.table.setUpdatesEnabled(True)
        self.table.setSortingEnabled(True)
        
        # 延迟刷新以提高响应性
        QTimer.singleShot(50, lambda: self.table.viewport().update())
        self._adjust_table_layout()

    def _adjust_table_layout(self):
        try:
            header = self.table.horizontalHeader()
            header.setStretchLastSection(True)
            self.table.resizeColumnsToContents()
            self.table.resizeRowsToContents()
            vh = self.table.verticalHeader()
            row_count = self.table.rowCount()
            row_h = vh.defaultSectionSize() or 24
            desired = header.height() + (row_count if row_count > 0 else 1) * row_h + 16
            desired = max(240, desired)
            self.table.setMinimumHeight(240)
        except Exception:
            pass
    
    def schedule_filter_update(self):
        """调度过滤器更新 - 防抖动机制"""
        # 停止之前的定时器
        self.filter_timer.stop()
        # 设置300ms延迟，避免频繁触发
        self.filter_timer.start(300)
    
    def apply_filters(self):
        """应用过滤器 - 高性能版本"""
        # 获取过滤条件
        height_filter = self.height_combo.currentText()
        codec_filter = self.codec_combo.currentText()
        ext_filter = self.ext_combo.currentText()
        language_filter = self.language_combo.currentText()
        min_size = self.filesize_min.value() * 1024 * 1024
        max_size = self.filesize_max.value() * 1024 * 1024
        video_only = self.video_only_cb.isChecked()
        audio_only = self.audio_only_cb.isChecked()
        
        # 预编译过滤条件，避免在循环中重复计算
        filters = []
        
        # 分辨率过滤
        if height_filter != "全部":
            max_height = int(height_filter.split('≤')[1].split('p')[0])
            eff_h = self._get_effective_height
            filters.append(lambda fmt: ((eff_h(fmt) or 0) > 0 and (eff_h(fmt) or 0) <= max_height))
        
        # 编码器过滤
        if codec_filter != "全部":
            codec_map = {
                "H.264 (AVC)": ["h264", "avc"],
                "H.265 (HEVC)": ["h265", "hevc"],
                "VP9": ["vp9"],
                "AV1": ["av01", "av1"],
                "VP8": ["vp8"]
            }
            codec_keywords = codec_map.get(codec_filter, [])
            def check_codec(fmt):
                v = (fmt.get('vcodec') or '').lower()
                a = (fmt.get('acodec') or '').lower()
                t = f"{fmt.get('format', '') or ''} {fmt.get('format_note', '') or ''}".lower()
                for kw in codec_keywords:
                    if kw in v or kw in a or kw in t:
                        return True
                return False
            filters.append(check_codec)
        
        # 容器过滤
        if ext_filter != "全部":
            filters.append(lambda fmt: fmt.get('ext') == ext_filter)
        
        # 语言过滤（精确匹配语言标签 [xx]）
        if language_filter != "全部":
            def check_language(fmt):
                import re
                lang_struct = str(fmt.get('language') or '').strip().lower()
                wanted = language_filter.lower()
                if lang_struct:
                    if (
                        lang_struct == wanted
                        or lang_struct.startswith(f"{wanted}-")
                        or lang_struct.startswith(f"{wanted}_")
                    ):
                        return True
                format_text = fmt.get('format', '') or ''
                note_text = fmt.get('format_note', '') or ''
                combined = f"{format_text} {note_text}"
                # 使用正则表达式精确匹配 [语言代码]
                lang_pattern = r'\[' + re.escape(wanted) + r'\]'
                return bool(re.search(lang_pattern, combined.lower()))
            filters.append(check_language)
        
        # 文件大小过滤
        if min_size > 0 or max_size < 10000 * 1024 * 1024:
            filters.append(lambda fmt: self._check_filesize_range(fmt, min_size, max_size))
        
        # 仅视频/仅音频过滤
        if video_only and audio_only:
            filters.append(lambda fmt: fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none')
        elif video_only:
            filters.append(lambda fmt: fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none')
        elif audio_only:
            filters.append(lambda fmt: fmt.get('vcodec') == 'none' and fmt.get('acodec') != 'none')
        
        # 使用高效的多条件过滤
        self.filtered_formats = [
            fmt for fmt in self.formats 
            if all(filter_func(fmt) for filter_func in filters)
        ]
        
        # 清除排序缓存，因为数据已变化
        self.sort_cache.clear()
        
        # 异步更新表格以避免UI阻塞
        QApplication.processEvents()
        self.update_table_items()  # 使用增量更新
        
        # 更新过滤状态标签
        total_formats = len(self.formats)
        filtered_count = len(self.filtered_formats)
        if filtered_count == total_formats:
            self.filter_status_label.setText("显示所有格式")
            self.filter_status_label.setStyleSheet("color: #666; font-size: 10px;")
        else:
            self.filter_status_label.setText(f"已过滤: {filtered_count}/{total_formats} 个格式")
            self.filter_status_label.setStyleSheet("color: #2196F3; font-size: 10px; font-weight: bold;")
    
    def _check_filesize_range(self, fmt, min_size, max_size):
        """检查文件大小范围 - 优化版本"""
        filesize = fmt.get('filesize')
        if not filesize:
            return True  # 没有文件大小信息的格式不过滤
        
        return min_size <= filesize <= max_size
    
    def reset_filters(self):
        """重置所有过滤器 - 优化版本"""
        # 停止定时器避免重复触发
        self.filter_timer.stop()
        
        # 批量重置控件状态
        controls_to_reset = [
            (self.height_combo, 0),
            (self.codec_combo, 0),
            (self.ext_combo, 0),
            (self.language_combo, 0),
            (self.filesize_min, 0),
            (self.filesize_max, 10000),
            (self.video_only_cb, False),
            (self.audio_only_cb, False)
        ]
        
        for control, default_value in controls_to_reset:
            if hasattr(control, 'setCurrentIndex'):
                control.setCurrentIndex(default_value)
            elif hasattr(control, 'setValue'):
                control.setValue(default_value)
            elif hasattr(control, 'setChecked'):
                control.setChecked(default_value)
        
        # 清除过滤和排序缓存
        self.filtered_formats = self.formats.copy()
        self.sort_cache.clear()
        
        # 使用增量更新
        self.update_table_items()
        
        # 更新状态标签
        self.filter_status_label.setText("显示所有格式")
        self.filter_status_label.setStyleSheet("color: #666; font-size: 10px;")
    
    
    def set_format_expression(self, expression):
        """设置格式表达式"""
        self.selected_format_expression = expression
    
    def validate_expression(self):
        """验证格式表达式"""
        return bool(str(self.selected_format_expression or "").strip())

    def _find_format_by_id(self, format_id):
        target = str(format_id or "").strip()
        if not target:
            return None
        for fmt in self.filtered_formats:
            if str(fmt.get('format_id', '')).strip() == target:
                return fmt
        for fmt in self.formats:
            if str(fmt.get('format_id', '')).strip() == target:
                return fmt
        return None

    def _get_row_format(self, row):
        if row < 0 or row >= self.table.rowCount():
            return "", None
        item = self.table.item(row, 0)
        if not item:
            return "", None
        format_id = item.text().strip()
        fmt = item.data(Qt.UserRole)
        if not isinstance(fmt, dict):
            fmt = self._find_format_by_id(format_id)
        return format_id, fmt

    def _build_safe_expression(self, format_id, fmt):
        format_id = str(format_id or "").strip()
        if not format_id:
            return "bv*+ba/b"
        if not isinstance(fmt, dict):
            return format_id

        vcodec = str(fmt.get('vcodec') or '').strip().lower()
        acodec = str(fmt.get('acodec') or '').strip().lower()
        has_video = vcodec not in ('', 'none')
        has_audio = acodec not in ('', 'none')

        # 仅对单视频流自动补音频，避免误选导致“静音视频”
        if has_video and not has_audio:
            return f"{format_id}+ba/b"
        return format_id
    
    def on_format_double_click(self, row, column):
        """双击格式行"""
        format_id, fmt = self._get_row_format(row)
        if format_id:
            self.accept_format(self._build_safe_expression(format_id, fmt))
    
    def on_format_selection_changed(self):
        """格式选择改变"""
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            format_id, fmt = self._get_row_format(row)
            self.selected_format_expression = self._build_safe_expression(format_id, fmt)
    
    def accept_format(self, format_expression):
        """接受格式选择"""
        self.selected_format_expression = format_expression
        self.format_selected.emit(format_expression)
        self.accept()
    
    def accept_selection(self):
        """接受当前选择"""
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            format_id, fmt = self._get_row_format(row)
            if format_id:
                self.accept_format(self._build_safe_expression(format_id, fmt))
                return
        self.accept_format("bv*+ba/b")
    
    def get_selected_format(self):
        """获取选择的格式表达式"""
        return self.selected_format_expression or "bv*+ba/b"


# 保持向后兼容的简单对话框
class FormatListDialog(QDialog):
    """简单格式选择对话框 - 向后兼容"""
    def __init__(self, formats, parent=None):
        super().__init__(parent)
        self.formats = formats
        self.selected_format_id = None
        self.setWindowTitle("格式选择")
        self.resize(800, 500)
        self.initUI()
        
    def initUI(self):
        layout = QVBoxLayout(self)
        
        # 提示用户使用高级对话框
        info_label = QLabel("💡 提示：点击'高级选择'可使用基于官方文档的强大格式选择功能")
        info_label.setStyleSheet("background-color: #e3f2fd; padding: 8px; border-radius: 4px;")
        layout.addWidget(info_label)
        
        # 简单格式表格
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["格式ID", "扩展名", "分辨率", "描述"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        
        # 设置表格列宽
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setStretchLastSection(True)

        # 横向滚动条与视觉提示（简单表格）
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setStyleSheet(self.table.styleSheet() + "\n"
            "QScrollBar:horizontal { height: 12px; background: transparent; }\n"
            "QScrollBar::handle:horizontal { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #eeeeee, stop:1 #cccccc); border-radius: 6px; }\n"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { background: none; width: 0; }\n"
            "QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal { width: 0; height: 0; }\n"
        )
        
        self.populate_simple_table()
        self.table.cellDoubleClicked.connect(self.on_double_click)
        layout.addWidget(self.table)
        
        # 按钮布局
        button_layout = QHBoxLayout()
        
        # 高级选择按钮
        advanced_btn = QPushButton("🛠️ 高级选择")
        advanced_btn.clicked.connect(self.open_advanced_dialog)
        button_layout.addWidget(advanced_btn)
        
        # 快速选择按钮
        best_btn = QPushButton("🚀 最佳质量")
        best_btn.clicked.connect(lambda: self.select_format("best"))
        button_layout.addWidget(best_btn)
        
        button_layout.addStretch()
        
        # 标准按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        button_layout.addWidget(button_box)
        
        layout.addLayout(button_layout)
    
    def populate_simple_table(self):
        """填充简单表格"""
        # 添加预设选项
        presets = [
            ("best", "-", "-", "自动选择最佳质量"),
            ("bv*+ba/b", "-", "-", "官方推荐默认格式"),
        ]
        
        self.table.setRowCount(len(presets) + len(self.formats))
        
        # 添加预设
        for i, (format_id, ext, res, desc) in enumerate(presets):
            self.table.setItem(i, 0, QTableWidgetItem(format_id))
            self.table.setItem(i, 1, QTableWidgetItem(ext))
            self.table.setItem(i, 2, QTableWidgetItem(res))
            self.table.setItem(i, 3, QTableWidgetItem(desc))
        
        # 添加实际格式
        for i, fmt in enumerate(self.formats):
            row = len(presets) + i
            format_id = fmt.get('format_id', '')
            ext = fmt.get('ext', '')
            
            height = fmt.get('height')
            resolution = f"{height}p" if height else fmt.get('resolution', 'N/A')
            
            description = fmt.get('format_note', fmt.get('format', ''))
            
            self.table.setItem(row, 0, QTableWidgetItem(format_id))
            self.table.setItem(row, 1, QTableWidgetItem(ext))
            self.table.setItem(row, 2, QTableWidgetItem(resolution))
            self.table.setItem(row, 3, QTableWidgetItem(description))
    
    def open_advanced_dialog(self):
        """打开高级格式选择对话框"""
        dialog = AdvancedFormatDialog(self.formats, self)
        if dialog.exec_() == QDialog.Accepted:
            self.selected_format_id = dialog.get_selected_format()
            self.accept()
    
    def select_format(self, format_id):
        """选择格式"""
        self.selected_format_id = format_id
        self.accept()
    
    def on_double_click(self, row, column):
        """双击选择"""
        format_id = self.table.item(row, 0).text()
        self.select_format(format_id)
    
    def get_selected_format_id(self):
        """获取选择的格式ID"""
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows and self.selected_format_id is None:
            row = selected_rows[0].row()
            self.selected_format_id = self.table.item(row, 0).text()
        
        return self.selected_format_id or "best"
