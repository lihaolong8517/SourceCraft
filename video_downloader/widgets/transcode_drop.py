import os
"""转码拖放控件"""
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, pyqtSignal

class TranscodeDropWidget(QWidget):
    """支持拖放视频文件的转码Tab容器"""
    
    # 定义信号，当文件被拖放时发出
    file_dropped = pyqtSignal(str)
    
    # 支持的视频文件扩展名
    VIDEO_EXTENSIONS = ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.m4v', '.wmv', '.mpg', '.mpeg', '.3gp', '.ts', '.mts', '.m2ts']
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
    
    def dragEnterEvent(self, event):
        """拖拽进入事件"""
        try:
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if url.isLocalFile():
                        file_path = url.toLocalFile()
                        ext = os.path.splitext(file_path)[1].lower()
                        if ext in self.VIDEO_EXTENSIONS:
                            event.acceptProposedAction()
                            return
            event.ignore()
        except Exception as e:
            print(t_format("转码Tab拖拽进入事件错误: {}", e))
            event.ignore()
    
    def dragMoveEvent(self, event):
        """拖拽移动事件"""
        try:
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if url.isLocalFile():
                        file_path = url.toLocalFile()
                        ext = os.path.splitext(file_path)[1].lower()
                        if ext in self.VIDEO_EXTENSIONS:
                            event.acceptProposedAction()
                            return
            event.ignore()
        except Exception as e:
            print(t_format("转码Tab拖拽移动事件错误: {}", e))
            event.ignore()
    
    def dropEvent(self, event):
        """拖拽放下事件"""
        try:
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if url.isLocalFile():
                        file_path = url.toLocalFile()
                        ext = os.path.splitext(file_path)[1].lower()
                        if ext in self.VIDEO_EXTENSIONS:
                            self.file_dropped.emit(file_path)
                            event.acceptProposedAction()
                            return
            event.ignore()
        except Exception as e:
            print(t_format("转码Tab拖拽放下事件错误: {}", e))
            event.ignore()


