from PyQt5.QtWidgets import QAbstractItemView, QFrame
"""限高下拉框控件"""
from PyQt5.QtWidgets import QComboBox
from PyQt5.QtCore import Qt

class LimitedHeightComboBox(QComboBox):
    """自定义 ComboBox，实现与输入框的无缝衔接并彻底消除底部空白"""
    
    def __init__(self, max_visible_items=10, parent=None):
        super().__init__(parent)
        self._max_visible_items = max_visible_items
        
        # 1. 强制设置下拉视图的样式，消除所有可能的边距、边框和阴影
        # 使用更现代的边框颜色，并添加自定义滚动条样式
        self.setStyleSheet("""
            QComboBox QAbstractItemView {
                border: 1px solid #CBCBCB;
                background-color: white;
                selection-background-color: #E5F1FB;
                selection-color: black;
                outline: none;
            }
            QScrollBar:vertical {
                border: none;
                background: #F0F0F0;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #CDCDCD;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #A6A6A6;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        
        view = self.view()
        view.setVerticalScrollMode(QAbstractItemView.ScrollPerItem) # 回归按项滚动，更稳定
        view.setFrameShape(QFrame.NoFrame) # 移除内部视图的框架
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    
    def showPopup(self):
        """重写 showPopup 方法，实现无缝对齐和精确高度"""
        # 先调用基类方法生成弹出窗口
        super().showPopup()
        
        # 找到下拉框的容器窗口 (QFrame)
        popup = self.findChild(QFrame)
        if popup:
            view = self.view()
            # 确保布局已刷新
            view.doItemsLayout()
            
            # 获取单行高度
            row_height = view.sizeHintForRow(0)
            if row_height <= 0:
                row_height = 24 # 兜底高度
            
            # 计算目标显示项数
            display_count = min(self.count(), self._max_visible_items)
            
            # 精确计算高度：项数 * 行高
            # 注意：不需要额外加像素，因为我们已经移除了内部边框
            target_height = display_count * row_height
            
            # 关键：设置内部视图的固定高度
            view.setFixedHeight(target_height)
            
            # 关键：设置外部容器的高度，使其正好包裹视图（考虑1px的样式表边框）
            popup.setFixedHeight(target_height + 2)
            
            # 重新定位实现“无缝衔接”
            # 获取 ComboBox 左下角的全局坐标
            rect = self.rect()
            global_pos = self.mapToGlobal(rect.bottomLeft())
            
            # 向上修正 1 像素，使下拉框的顶边与 ComboBox 的底边重合（覆盖重叠部分）
            popup.move(global_pos.x(), global_pos.y() - 1)
            
            # 确保宽度一致
            popup.setFixedWidth(self.width())

