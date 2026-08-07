"""
授权对话框模块 - License Dialogs
提供登录、激活、购买等 UI 组件

定价：
- 15天免费试用
- 终身版 ¥28
- 设备限制：1台
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QFrame, QMessageBox, QProgressBar, QGroupBox,
    QRadioButton, QButtonGroup, QSpacerItem, QSizePolicy, QWidget
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, QUrl
from PyQt5.QtGui import QFont, QDesktopServices, QPixmap, QPainter, QColor, QIcon

import webbrowser
import os
from license_manager import LicenseManager, TRIAL_DAYS, generate_license_key
from i18n import t, t_format

PURCHASE_URL = "https://pay.ldxp.cn/shop/Q23SQVYU"


def _simplify_activation_message(message: str) -> str:
    """将底层授权错误归一化为简短提示（当前语言）。"""
    text = str(message or "").strip()
    lowered = text.lower()
    _l = lowered
    if not text:
        return t("激活码不可用，请核对后重试")

    if "hw_hash" in lowered or "invalid app" in lowered:
        return t("客户端与授权服务不匹配，请更新安装包后重试")
    if "authentication failed" in lowered or "signing key" in lowered or "401" in lowered:
        return t("授权服务校验失败，请使用最新安装包或联系管理员检查服务配置")
    if "格式" in text or t("格式").lower() in _l:
        return t("激活码格式不正确")
    if "无效" in text or "invalid" in lowered or "not found" in lowered or "license_key" in lowered or t("无效").lower() in _l:
        return t("激活码无效，请核对后重试")
    if "过期" in text or "失效" in text or t("过期").lower() in _l or t("失效").lower() in _l:
        return t("激活码已失效")
    if "其他设备" in text or "已绑定" in text or t("其他设备").lower() in _l:
        return t("激活码已在其他设备使用")
    if "网络" in text or t("网络").lower() in _l:
        return t("网络异常，请稍后重试")
    if "连接" in text or "超时" in text or "服务器" in text or t("连接").lower() in _l:
        return t("连接失败，请稍后重试")
    if "未配置" in text or t("未配置").lower() in _l:
        return t("服务暂不可用，请联系客服")
    return t("激活码不可用，请核对后重试")


class PaymentDialog(QDialog):
    """自动发卡平台购买对话框"""
    
    # 信号：用户点击已购买成功
    purchase_completed = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
    
    def init_ui(self):
        self.setWindowTitle(t("购买激活码"))
        self.setFixedSize(450, 740)  # 增加高度以容纳商铺链接按钮
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(30, 20, 30, 20)
        
        # 标题
        title = QLabel(t("🛒 扫码购买激活码"))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            QLabel {
                font-size: 22px;
                font-weight: bold;
                color: #2c3e50;
            }
        """)
        layout.addWidget(title)
        
        # 定价信息
        pricing = QLabel(
            "<center>"
            "<span style='font-size: 16px;'>"
            + t("终身版 <b style='color: #e74c3c;'>¥28</b> 永久使用")
            + "</span>"
            "</center>"
        )
        layout.addWidget(pricing)
        
        # 获取脚本所在目录
        script_dir = os.path.dirname(os.path.abspath(__file__))
        
        # 自动发卡平台二维码区域
        qr_frame = QFrame()
        qr_frame.setStyleSheet("""
            QFrame {
                background-color: #f0f7ff;
                border: 2px solid #3498db;
                border-radius: 12px;
                padding: 8px;
            }
        """)
        qr_layout = QVBoxLayout(qr_frame)
        qr_layout.setSpacing(5)
        qr_layout.setContentsMargins(10, 10, 10, 10)
        
        # 二维码标题
        qr_title = QLabel(t("📱 请扫码支付"))
        qr_title.setAlignment(Qt.AlignCenter)
        qr_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #3498db;")
        qr_layout.addWidget(qr_title)
        
        # 二维码图片
        qr_label = QLabel()
        shop_qr_path = os.path.join(script_dir, "shop_qr.png")
        if os.path.exists(shop_qr_path):
            pixmap = QPixmap(shop_qr_path)
            qr_label.setPixmap(pixmap.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            qr_label.setText(t("二维码未找到\n请确认 shop_qr.png 文件存在"))
            qr_label.setStyleSheet("color: #e74c3c; font-size: 14px;")
        qr_label.setAlignment(Qt.AlignCenter)
        qr_label.setMinimumSize(260, 260)
        qr_layout.addWidget(qr_label)
        
        layout.addWidget(qr_frame)
        
        # 购买说明
        steps_info = QLabel(
            "<center>"
            "<span style='color: #7f8c8d; font-size: 13px;'>" +
            t("1. 扫码或点击下方链接选择套餐并支付<br>") +
            t("2. 支付成功后自动获取激活码<br>") +
            t("3. 点击「已购买成功」按钮输入激活码") +
            "</span>"
            "</center>"
        )
        steps_info.setStyleSheet("margin-top: 5px;")
        layout.addWidget(steps_info)
        
        # 商铺链接按钮（电脑用户可直接点击）
        shop_btn = QPushButton(t("🔗 点击打开商铺链接购买"))
        shop_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 10px 25px;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5dade2;
            }
            QPushButton:pressed {
                background-color: #2980b9;
            }
        """)
        shop_btn.clicked.connect(lambda: webbrowser.open(PURCHASE_URL))
        layout.addWidget(shop_btn)
        
        # 购买成功按钮
        success_btn = QPushButton(t("✅ 已购买成功，输入激活码"))
        success_btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                border: none;
                padding: 12px 30px;
                border-radius: 5px;
                font-size: 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
            QPushButton:pressed {
                background-color: #1e8449;
            }
        """)
        success_btn.clicked.connect(self._on_purchase_completed)
        layout.addWidget(success_btn)
        
        # 联系方式（可选）
        contact_info = QLabel(
            "<center>"
            "<span style='color: #95a5a6; font-size: 12px;'>" +
            t("遇到问题？联系邮箱：haolongli84@gmail.com") +
            t("<br>QQ：378952277") +
            "</span>"
            "</center>"
        )
        layout.addWidget(contact_info)
        
        # 关闭按钮
        close_btn = QPushButton(t("暂不购买"))
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #95a5a6;
                color: white;
                border: none;
                padding: 10px 40px;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #7f8c8d;
            }
        """)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn, alignment=Qt.AlignCenter)
    
    def _on_purchase_completed(self):
        """用户确认购买成功"""
        self.purchase_completed.emit()
        self.accept()


class LicenseCheckThread(QThread):
    """后台验证许可证线程"""
    finished = pyqtSignal(bool, str)
    
    def __init__(self, license_manager, license_key=None):
        super().__init__()
        self.license_manager = license_manager
        self.license_key = license_key
    
    def run(self):
        if self.license_key:
            success, message = self.license_manager.activate(self.license_key)
        else:
            success, message = self.license_manager.can_use_app()
        self.finished.emit(success, message)


class ActivationDialog(QDialog):
    """激活码输入对话框"""
    
    activated = pyqtSignal(bool)  # 激活成功信号
    
    def __init__(self, license_manager: LicenseManager, parent=None):
        super().__init__(parent)
        self.license_manager = license_manager
        self.init_ui()
        # 确保对话框可以接收输入
        self.setModal(True)
        self.activateWindow()
        self.raise_()
    
    def init_ui(self):
        self.setWindowTitle(t("软件激活"))
        self.setFixedSize(450, 450)  # 增加高度以容纳进度条
        # 移除可能导致问题的窗口标志，使用标准对话框标志
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # 标题
        title = QLabel(t("🎬 视频下载工具"))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            QLabel {
                font-size: 24px;
                font-weight: bold;
                color: #2c3e50;
                margin-bottom: 10px;
            }
        """)
        layout.addWidget(title)
        
        # 状态显示
        status = self.license_manager.get_status()
        status_text = status['message']
        
        if status['is_trial'] and status['trial_remaining'] > 0:
            status_color = "#27ae60"  # 绿色
            status_icon = "✅"
        elif status['is_licensed']:
            status_color = "#3498db"  # 蓝色
            status_icon = "🔓"
        else:
            status_color = "#e74c3c"  # 红色
            status_icon = "⚠️"
        
        status_label = QLabel(f"{status_icon} {status_text}")
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setStyleSheet(f"""
            QLabel {{
                font-size: 14px;
                color: {status_color};
                padding: 10px;
                background-color: #f8f9fa;
                border-radius: 5px;
            }}
        """)
        layout.addWidget(status_label)
        
        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #dee2e6;")
        layout.addWidget(line)
        
        # 激活码输入区 - 使用简单布局避免遮挡
        input_label = QLabel(t("输入激活码："))
        input_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #2c3e50; margin-top: 10px;")
        layout.addWidget(input_label)
        
        self.license_input = QLineEdit()
        self.license_input.setPlaceholderText("YRXX-XXXX-XXXX-XXXX")
        self.license_input.setMinimumHeight(50)  # 确保足够高度
        self.license_input.setStyleSheet("""
            QLineEdit {
                padding: 15px;
                font-size: 16px;
                font-family: 'Consolas', 'Monaco', monospace;
                border: 2px solid #dee2e6;
                border-radius: 8px;
                letter-spacing: 3px;
                background-color: #ffffff;
                color: #2c3e50;
            }
            QLineEdit:focus {
                border-color: #3498db;
            }
        """)
        self.license_input.textChanged.connect(self._format_license_key)
        self.license_input.returnPressed.connect(self.activate)
        layout.addWidget(self.license_input)
        
        # 确保输入框获得焦点
        QTimer.singleShot(200, self.license_input.setFocus)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        # 购买按钮
        buy_btn = QPushButton(t("🛒 购买激活码"))
        buy_btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                border: none;
                padding: 12px 20px;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
            QPushButton:pressed {
                background-color: #1e8449;
            }
        """)
        buy_btn.clicked.connect(self.open_purchase_page)
        button_layout.addWidget(buy_btn)
        
        # 激活按钮
        self.activate_btn = QPushButton(t("✓ 激活"))
        self.activate_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 12px 25px;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5dade2;
            }
            QPushButton:pressed {
                background-color: #2980b9;
            }
            QPushButton:disabled {
                background-color: #bdc3c7;
            }
        """)
        self.activate_btn.clicked.connect(self.activate)
        button_layout.addWidget(self.activate_btn)
        
        layout.addLayout(button_layout)
        
        # 定价信息
        pricing_label = QLabel(
            "<center>"
            "<div style='line-height: 1.6;'>" +
            t("<span style='color: #7f8c8d;'>💡 定价方案</span><br>") +
            t("<span style='color: #2c3e50; font-size: 13px;'>¥28 <b style='color: #e74c3c; font-size: 18px;'>终身免费</b>使用</span>") + t("</div>") +
            "</center>"
        )
        pricing_label.setStyleSheet("margin-top: 10px; min-height: 50px;")
        layout.addWidget(pricing_label)
        
        # 底部按钮
        bottom_layout = QHBoxLayout()
        
        # 如果是试用中或已激活，显示继续使用按钮
        if status['is_trial'] or status['is_licensed']:
            continue_btn = QPushButton(t("继续使用"))
            continue_btn.setStyleSheet("""
                QPushButton {
                    background-color: #95a5a6;
                    color: white;
                    border: none;
                    padding: 8px 15px;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #7f8c8d;
                }
            """)
            continue_btn.clicked.connect(self.accept)
            bottom_layout.addWidget(continue_btn)
        
        bottom_layout.addStretch()
        
        # 退出按钮
        exit_btn = QPushButton(t("退出程序"))
        exit_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #7f8c8d;
                border: 1px solid #bdc3c7;
                padding: 8px 15px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #ecf0f1;
            }
        """)
        exit_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(exit_btn)
        
        layout.addLayout(bottom_layout)
        
        # 进度条（初始透明，验证时显示）
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximum(0)  # 不确定进度
        self.progress.setFixedHeight(4)  # 固定高度
        # 初始状态完全透明
        self.progress.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: transparent;
                height: 4px;
            }
            QProgressBar::chunk {
                background-color: transparent;
            }
        """)
        layout.addWidget(self.progress)
    
    def _format_license_key(self, text: str):
        """格式化激活码输入（自动添加横线）"""
        # 移除所有非字母数字字符
        clean = ''.join(c for c in text.upper() if c.isalnum())
        
        # 每4个字符添加横线
        formatted = '-'.join(clean[i:i+4] for i in range(0, len(clean), 4))
        
        # 限制长度
        if len(formatted) > 19:  # XXXX-XXXX-XXXX-XXXX
            formatted = formatted[:19]
        
        # 避免无限循环
        if formatted != text:
            self.license_input.blockSignals(True)
            self.license_input.setText(formatted)
            self.license_input.blockSignals(False)
    
    def activate(self):
        """激活软件"""
        license_key = self.license_input.text().strip()
        
        if not license_key:
            QMessageBox.warning(self, t("提示"), t("请输入激活码"))
            return
        
        # 显示加载状态
        self.activate_btn.setEnabled(False)
        self.activate_btn.setText(t("验证中..."))
        # 显示进度条（切换背景色）
        self.progress.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: #ecf0f1;
                height: 4px;
            }
            QProgressBar::chunk {
                background-color: #3498db;
            }
        """)
        
        # 在后台线程中验证
        self.check_thread = LicenseCheckThread(self.license_manager, license_key)
        self.check_thread.finished.connect(self._on_activation_result)
        self.check_thread.start()
    
    def _on_activation_result(self, success: bool, message: str):
        """激活结果回调"""
        self.activate_btn.setEnabled(True)
        self.activate_btn.setText(t("✓ 激活"))
        # 隐藏进度条（切换为透明背景）
        self.progress.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: transparent;
                height: 4px;
            }
            QProgressBar::chunk {
                background-color: transparent;
            }
        """)
        
        if success:
            QMessageBox.information(self, t("激活成功"), f"🎉 {message}" + t("\n\n感谢您的支持！"))
            self.activated.emit(True)
            self.accept()
        else:
            QMessageBox.warning(self, t("激活失败"), _simplify_activation_message(message))
    
    def open_purchase_page(self):
        """打开购买页面 - 显示收款码"""
        dialog = PaymentDialog(self)
        dialog.purchase_completed.connect(self._on_purchase_success)
        dialog.exec_()
    
    def _on_purchase_success(self):
        """购买成功回调"""
        QMessageBox.information(
            self, 
            t("购买成功"), 
            t("🎉 恭喜您购买成功！\n\n") +
            t("请将发卡平台显示的激活码复制粘贴到下方输入框中，\n") +
            t("然后点击「激活」按钮完成激活。")
        )
        # 将焦点移到激活码输入框
        self.license_input.setFocus()
        self.license_input.selectAll()


class TrialExpiredDialog(QDialog):
    """试用过期提醒对话框"""
    
    def __init__(self, license_manager: LicenseManager, parent=None):
        super().__init__(parent)
        self.license_manager = license_manager
        self.init_ui()
    
    def init_ui(self):
        self.setWindowTitle(t("试用期已结束"))
        self.setFixedSize(400, 350)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # 图标和标题
        title = QLabel(t("⏰ 试用期已结束"))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            QLabel {
                font-size: 22px;
                font-weight: bold;
                color: #e74c3c;
            }
        """)
        layout.addWidget(title)
        
        # 说明文字
        info = QLabel(
            t("感谢您试用视频下载工具！\n\n") +
            t_format("您的 {} 天免费试用已结束。\n", TRIAL_DAYS) +
            t("请购买激活码以继续使用完整功能。")
        )
        info.setAlignment(Qt.AlignCenter)
        info.setStyleSheet("""
            QLabel {
                font-size: 14px;
                color: #2c3e50;
                line-height: 1.5;
            }
        """)
        info.setWordWrap(True)
        layout.addWidget(info)
        
        # 定价
        pricing = QLabel(
            "<center>"
            "<span style='font-size: 16px; color: #27ae60; font-weight: bold;'>" +
            t("终身版 ¥28") +
            "</span>"
            "</center>"
        )
        layout.addWidget(pricing)
        
        layout.addStretch()
        
        # 按钮
        button_layout = QHBoxLayout()
        
        activate_btn = QPushButton(t("🔑 输入激活码"))
        activate_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 12px 25px;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5dade2;
            }
        """)
        activate_btn.clicked.connect(self.show_activation)
        button_layout.addWidget(activate_btn)
        
        buy_btn = QPushButton(t("🛒 立即购买"))
        buy_btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                border: none;
                padding: 12px 25px;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
        """)
        buy_btn.clicked.connect(self.open_purchase)
        button_layout.addWidget(buy_btn)
        
        layout.addLayout(button_layout)
        
        # 退出按钮
        exit_btn = QPushButton(t("退出程序"))
        exit_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #7f8c8d;
                border: none;
                padding: 8px;
            }
            QPushButton:hover {
                color: #2c3e50;
            }
        """)
        exit_btn.clicked.connect(self.reject)
        layout.addWidget(exit_btn, alignment=Qt.AlignCenter)
    
    def show_activation(self):
        """显示激活对话框"""
        # 先隐藏当前窗口
        self.hide()
        dialog = ActivationDialog(self.license_manager, self)
        if dialog.exec_() == QDialog.Accepted:
            self.accept()
        else:
            # 用户取消激活，重新显示试用期窗口
            self.show()
    
    def open_purchase(self):
        """打开购买页面 - 直接跳转商铺链接"""
        # 直接打开发卡平台商铺链接
        webbrowser.open(PURCHASE_URL)


class LicenseStatusWidget(QFrame):
    """许可证状态显示控件（可嵌入主窗口）"""
    
    clicked = pyqtSignal()
    
    def __init__(self, license_manager: LicenseManager, parent=None):
        super().__init__(parent)
        self.license_manager = license_manager
        self.init_ui()
    
    def init_ui(self):
        # 设置固定高度与工具栏按钮一致（34px）
        self.setFixedHeight(34)
        
        # 单层边框样式，与工具栏按钮风格一致
        self.setStyleSheet("""
            LicenseStatusWidget {
                background-color: #ffffff;
                border: 1px solid #dcdcdc;
                border-radius: 6px;
                padding: 0px 10px;
            }
            LicenseStatusWidget:hover {
                background-color: #f5f5f5;
                border-color: #cccccc;
            }
        """)
        self.setCursor(Qt.PointingHandCursor)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(0)
        
        self.status_label = QLabel()
        self.status_label.setStyleSheet("border: none; background: transparent;")
        self.update_status()
        layout.addWidget(self.status_label)
    
    def update_status(self):
        """更新状态显示"""
        status = self.license_manager.get_status()
        
        if status['is_licensed']:
            if status['license_type'] == 'lifetime':
                # 终身版：隐藏显示框
                self.hide()
                return
            else:
                # 显示年卡剩余天数
                if status.get('expires_at'):
                    from datetime import datetime
                    try:
                        expires_str = status['expires_at']
                        if 'Z' in expires_str or '+' in expires_str:
                            expires = datetime.fromisoformat(expires_str.replace('Z', '+00:00'))
                            days_left = (expires - datetime.now(expires.tzinfo)).days
                        else:
                            expires = datetime.fromisoformat(expires_str)
                            days_left = (expires - datetime.now()).days
                        
                        if days_left > 0:
                            # 年卡剩余天数使用绿色
                            self.status_label.setText(f"🔓 年卡剩余 <span style='color: #27ae60;'>{days_left}</span> 天")
                            self.status_label.setStyleSheet("border: none; background: transparent; font-weight: bold; font-size: 12px;")
                        else:
                            # 已过期
                            self.status_label.setText(t("🔒 年卡已过期"))
                            self.status_label.setStyleSheet("border: none; background: transparent; color: #e74c3c; font-weight: bold; font-size: 12px;")
                        return
                    except:
                        pass
                # 回退显示
                self.status_label.setText(t("🔓 已激活"))
                self.status_label.setStyleSheet("border: none; background: transparent; color: #3498db; font-weight: bold; font-size: 12px;")
        elif status['is_trial'] and status['trial_remaining'] > 0:
            # 试用期内：隐藏显示框，不展示任何收费相关信息
            self.hide()
            return
        else:
            # 未激活/过期使用红色
            self.status_label.setText(t("🔒 未激活"))
            self.status_label.setStyleSheet("border: none; background: transparent; color: #e74c3c; font-weight: bold; font-size: 12px;")
    
    def mousePressEvent(self, event):
        """点击事件"""
        self.clicked.emit()
        super().mousePressEvent(event)


class LoginScreen(QDialog):
    """
    专业登录/激活界面
    作为程序启动后的第一屏，只有激活或试用后才能进入主界面
    """
    
    def __init__(self, license_manager: LicenseManager, parent=None):
        super().__init__(parent)
        self.license_manager = license_manager
        self.init_ui()
        self.setModal(True)
        
    def init_ui(self):
        self.setWindowTitle(t("软件登录 - 视频下载工具"))
        self.setFixedSize(500, 650)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint)
        
        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 顶部横幅
        banner = QFrame()
        banner.setFixedHeight(180)
        banner.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3498db, stop:1 #2980b9);
                border-bottom-left-radius: 30px;
                border-bottom-right-radius: 30px;
            }
        """)
        banner_layout = QVBoxLayout(banner)
        
        logo_label = QLabel("🎬")
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setStyleSheet("font-size: 60px; background: transparent;")
        banner_layout.addWidget(logo_label)
        
        title_label = QLabel(t("视频下载工具"))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("""
            font-size: 24px; 
            font-weight: bold; 
            color: white; 
            background: transparent;
            margin-bottom: 10px;
        """)
        banner_layout.addWidget(title_label)
        
        main_layout.addWidget(banner)
        
        # 内容区域
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(40, 30, 40, 30)
        content_layout.setSpacing(20)

        status = self.license_manager.get_status()
        
        # 欢迎语
        welcome_label = QLabel(t("欢迎回来，请激活您的软件"))
        welcome_label.setStyleSheet("font-size: 16px; color: #7f8c8d;")
        welcome_label.setAlignment(Qt.AlignCenter)
        content_layout.addWidget(welcome_label)

        status_text = str(status.get("message") or "").strip()
        if status_text:
            if status.get("is_licensed"):
                tip_color = "#1f618d"
                tip_bg = "#ebf5fb"
            elif status.get("is_trial") and int(status.get("trial_remaining") or 0) > 0:
                tip_color = "#b9770e"
                tip_bg = "#fef9e7"
            else:
                tip_color = "#922b21"
                tip_bg = "#fdecea"
            status_hint = QLabel(status_text)
            status_hint.setWordWrap(True)
            status_hint.setAlignment(Qt.AlignCenter)
            status_hint.setStyleSheet(
                "font-size: 13px; "
                f"color: {tip_color}; "
                f"background-color: {tip_bg}; "
                "border-radius: 8px; "
                "padding: 10px;"
            )
            content_layout.addWidget(status_hint)
        
        # 激活码输入框
        input_group = QVBoxLayout()
        input_group.setSpacing(8)
        
        label_key = QLabel(t("激活码 (License Key)"))
        label_key.setStyleSheet("font-weight: bold; color: #2c3e50;")
        input_group.addWidget(label_key)
        
        self.license_input = QLineEdit()
        self.license_input.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
        self.license_input.setFixedHeight(50)
        self.license_input.setStyleSheet("""
            QLineEdit {
                padding: 0 15px;
                font-size: 16px;
                font-family: 'Consolas', 'Monaco', monospace;
                border: 2px solid #ecf0f1;
                border-radius: 10px;
                background-color: #f9f9f9;
                color: #2c3e50;
            }
            QLineEdit:focus {
                border-color: #3498db;
                background-color: white;
            }
        """)
        self.license_input.textChanged.connect(self._format_license_key)
        input_group.addWidget(self.license_input)
        content_layout.addLayout(input_group)
        
        # 激活按钮
        self.activate_btn = QPushButton(t("立即激活"))
        self.activate_btn.setFixedHeight(50)
        self.activate_btn.setCursor(Qt.PointingHandCursor)
        self.activate_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #2471a3;
            }
            QPushButton:disabled {
                background-color: #bdc3c7;
            }
        """)
        self.activate_btn.clicked.connect(self.activate)
        content_layout.addWidget(self.activate_btn)
        
        # 试用按钮
        if status['is_trial'] and status['trial_remaining'] > 0:
            self.trial_btn = QPushButton(
                t_format("开始 {} 天免费试用 (剩余 {} 天)", TRIAL_DAYS, status['trial_remaining'])
            )
            self.trial_btn.setFixedHeight(45)
            self.trial_btn.setCursor(Qt.PointingHandCursor)
            self.trial_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #27ae60;
                    border: 2px solid #27ae60;
                    border-radius: 10px;
                    font-size: 14px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #f0fff4;
                }
            """)
            self.trial_btn.clicked.connect(self.accept)
            content_layout.addWidget(self.trial_btn)
        
        # 底部链接
        links_layout = QHBoxLayout()
        links_layout.setContentsMargins(0, 10, 0, 0)
        
        buy_btn = QPushButton(t("💳 获取激活码"))
        buy_btn.setCursor(Qt.PointingHandCursor)
        buy_btn.setFixedHeight(32)
        buy_btn.setStyleSheet("""
            QPushButton {
                color: #3498db;
                border: 1px solid #ebf5fb;
                background-color: #f4faff;
                padding: 0 15px;
                border-radius: 16px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #e1f0fa;
                border-color: #3498db;
                color: #2980b9;
            }
        """)
        buy_btn.clicked.connect(self.open_purchase_page)
        links_layout.addWidget(buy_btn)
        
        links_layout.addStretch()
        
        help_btn = QPushButton(t("💬 咨询与帮助"))
        help_btn.setCursor(Qt.PointingHandCursor)
        help_btn.setFixedHeight(32)
        help_btn.setStyleSheet("""
            QPushButton {
                color: #7f8c8d;
                border: 1px solid #f2f4f4;
                background-color: #f8f9f9;
                padding: 0 15px;
                border-radius: 16px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #f2f4f4;
                border-color: #bdc3c7;
                color: #2c3e50;
            }
        """)
        help_btn.clicked.connect(lambda: QMessageBox.information(self, t("帮助"), t("如有疑问，请联系邮箱：haolongli84@gmail.com") + t("\nQQ：378952277")))
        links_layout.addWidget(help_btn)
        
        content_layout.addLayout(links_layout)
        
        main_layout.addWidget(content_widget)
        main_layout.addStretch()
        
        # 进度条
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximum(0)
        self.progress.setFixedHeight(4)
        self.progress.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: transparent;
            }
            QProgressBar::chunk {
                background-color: transparent;
            }
        """)
        main_layout.addWidget(self.progress)
        
    def _format_license_key(self, text: str):
        clean = ''.join(c for c in text.upper() if c.isalnum())
        formatted = '-'.join(clean[i:i+4] for i in range(0, len(clean), 4))
        if len(formatted) > 19:
            formatted = formatted[:19]
        if formatted != text:
            self.license_input.blockSignals(True)
            self.license_input.setText(formatted)
            self.license_input.blockSignals(False)
            
    def activate(self):
        license_key = self.license_input.text().strip()
        if not license_key:
            QMessageBox.warning(self, t("提示"), t("请输入激活码"))
            return
            
        self.activate_btn.setEnabled(False)
        self.activate_btn.setText(t("正在验证授权..."))
        self.progress.setStyleSheet("""
            QProgressBar { border: none; background-color: #ecf0f1; }
            QProgressBar::chunk { background-color: #3498db; }
        """)
        
        self.check_thread = LicenseCheckThread(self.license_manager, license_key)
        self.check_thread.finished.connect(self._on_activation_result)
        self.check_thread.start()
        
    def _on_activation_result(self, success: bool, message: str):
        self.activate_btn.setEnabled(True)
        self.activate_btn.setText(t("立即激活"))
        self.progress.setStyleSheet("QProgressBar { border: none; background-color: transparent; }")
        
        if success:
            QMessageBox.information(self, t("激活成功"), f"🎉 {message}" + t("\n\n欢迎使用专业版！"))
            self.accept()
        else:
            QMessageBox.warning(self, t("激活失败"), _simplify_activation_message(message))
            
    def open_purchase_page(self):
        webbrowser.open(PURCHASE_URL)


def check_license_on_startup(parent=None, manager: LicenseManager = None) -> bool:
    """
    启动时检查许可证
    
    返回：
        True = 可以继续使用
        False = 用户选择退出
    """
    manager = manager or LicenseManager()
    status = manager.get_status()
    
    # 如果已经激活，直接进入程序
    if status['is_licensed']:
        print(f"[Auth] 已激活: {status['license_type']}")
        return True
    
    # 试用期内，静默进入程序，不弹出任何窗口
    if status['is_trial'] and status['trial_remaining'] > 0:
        print(f"[Auth] 试用中: 剩余 {status['trial_remaining']} 天")
        return True
    
    # 试用到期或未激活，显示试用到期对话框
    dialog = TrialExpiredDialog(manager, parent)
    result = dialog.exec_()
    
    # 重新获取状态，确认是否已激活
    status = manager.get_status()
    if result == QDialog.Accepted:
        if status['is_licensed']:
            return True
            
    return False


# 导出
__all__ = [
    'LoginScreen',
    'ActivationDialog',
    'TrialExpiredDialog', 
    'LicenseStatusWidget',
    'check_license_on_startup',
    'LicenseManager'
]
