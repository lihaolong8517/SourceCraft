#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载诊断工具
帮助用户诊断下载失败的具体原因
"""

import sys
import os
import subprocess
import time
from PyQt5.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar, QMessageBox
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont, QIcon, QPixmap, QPainter, QColor

class DiagnosticThread(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    
    def __init__(self, url, output_path):
        super().__init__()
        self.url = url
        self.output_path = output_path
    
    def run(self):
        try:
            self.progress_signal.emit("🔍 开始诊断下载问题...")
            
            # 1. 检查yt-dlp是否存在
            self.progress_signal.emit("\n1️⃣ 检查yt-dlp.exe...")
            if os.path.exists("yt-dlp.exe"):
                self.progress_signal.emit("✅ yt-dlp.exe 存在")
                
                # 检查版本
                try:
                    result = subprocess.run(["yt-dlp.exe", "--version"], 
                                          capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        self.progress_signal.emit(f"✅ 版本: {result.stdout.strip()}")
                    else:
                        self.progress_signal.emit(f"❌ 版本检查失败: {result.stderr}")
                        self.finished_signal.emit(False, "yt-dlp.exe无法正常运行")
                        return
                except Exception as e:
                    self.progress_signal.emit(f"❌ 版本检查异常: {e}")
                    self.finished_signal.emit(False, f"yt-dlp.exe执行异常: {e}")
                    return
            else:
                self.progress_signal.emit("❌ yt-dlp.exe 不存在")
                self.finished_signal.emit(False, "yt-dlp.exe不存在，请确保文件在程序目录中")
                return
            
            # 2. 检查输出路径
            self.progress_signal.emit("\n2️⃣ 检查输出路径...")
            if not os.path.exists(self.output_path):
                try:
                    os.makedirs(self.output_path)
                    self.progress_signal.emit(f"✅ 创建输出目录: {self.output_path}")
                except Exception as e:
                    self.progress_signal.emit(f"❌ 无法创建输出目录: {e}")
                    self.finished_signal.emit(False, f"无法创建输出目录: {e}")
                    return
            else:
                self.progress_signal.emit(f"✅ 输出目录存在: {self.output_path}")
            
            # 检查写入权限
            test_file = os.path.join(self.output_path, "test_write.tmp")
            try:
                with open(test_file, 'w') as f:
                    f.write("test")
                os.remove(test_file)
                self.progress_signal.emit("✅ 输出目录有写入权限")
            except Exception as e:
                self.progress_signal.emit(f"❌ 输出目录没有写入权限: {e}")
                self.finished_signal.emit(False, f"输出目录没有写入权限: {e}")
                return
            
            # 3. 检查URL有效性
            self.progress_signal.emit("\n3️⃣ 检查URL有效性...")
            self.progress_signal.emit(f"URL: {self.url}")
            
            # 尝试提取视频信息
            cmd = [
                "yt-dlp.exe",
                "--no-download",
                "--print", "title",
                "--print", "duration",
                "--print", "uploader",
                self.url
            ]
            
            try:
                self.progress_signal.emit("正在提取视频信息...")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    if len(lines) >= 3:
                        title = lines[0]
                        duration = lines[1]
                        uploader = lines[2]
                        self.progress_signal.emit(f"✅ 视频信息提取成功:")
                        self.progress_signal.emit(f"   标题: {title}")
                        self.progress_signal.emit(f"   时长: {duration}秒")
                        self.progress_signal.emit(f"   上传者: {uploader}")
                    else:
                        self.progress_signal.emit(f"✅ 信息提取成功: {result.stdout}")
                else:
                    self.progress_signal.emit(f"❌ 信息提取失败:")
                    self.progress_signal.emit(f"   错误输出: {result.stderr}")
                    self.progress_signal.emit(f"   标准输出: {result.stdout}")
                    
                    # 分析具体错误
                    error_msg = result.stderr.lower()
                    if "not available" in error_msg:
                        self.finished_signal.emit(False, "视频不可用，可能已被删除或设为私有")
                    elif "geo" in error_msg or "region" in error_msg:
                        self.finished_signal.emit(False, "地理限制，视频在您的地区不可用")
                    elif "age" in error_msg:
                        self.finished_signal.emit(False, "年龄限制，需要登录验证")
                    elif "network" in error_msg or "connection" in error_msg:
                        self.finished_signal.emit(False, "网络连接问题，请检查网络连接")
                    else:
                        self.finished_signal.emit(False, f"URL无效或其他错误: {result.stderr}")
                    return
            except subprocess.TimeoutExpired:
                self.progress_signal.emit("❌ 信息提取超时")
                self.finished_signal.emit(False, "网络连接超时，请检查网络连接")
                return
            except Exception as e:
                self.progress_signal.emit(f"❌ 信息提取异常: {e}")
                self.finished_signal.emit(False, f"信息提取异常: {e}")
                return
            
            # 4. 尝试实际下载
            self.progress_signal.emit("\n4️⃣ 尝试实际下载...")
            
            download_cmd = [
                "yt-dlp.exe",
                "-f", "worst",  # 使用最低质量
                "-o", os.path.join(self.output_path, "diagnostic_test.%(ext)s"),
                self.url
            ]
            
            try:
                self.progress_signal.emit("开始下载测试...")
                result = subprocess.run(download_cmd, capture_output=True, text=True, timeout=60)
                
                if result.returncode == 0:
                    self.progress_signal.emit("✅ 下载测试成功")
                    
                    # 检查文件是否存在
                    test_files = [f for f in os.listdir(self.output_path) if f.startswith("diagnostic_test")]
                    if test_files:
                        test_file = os.path.join(self.output_path, test_files[0])
                        file_size = os.path.getsize(test_file)
                        self.progress_signal.emit(f"✅ 下载文件: {test_files[0]} ({file_size} 字节)")
                        
                        # 清理测试文件
                        try:
                            os.remove(test_file)
                            self.progress_signal.emit("✅ 清理测试文件完成")
                        except:
                            pass
                        
                        self.finished_signal.emit(True, "所有诊断测试通过，下载功能正常")
                    else:
                        self.finished_signal.emit(False, "下载命令成功但未找到文件")
                else:
                    self.progress_signal.emit(f"❌ 下载测试失败:")
                    self.progress_signal.emit(f"   错误输出: {result.stderr}")
                    self.progress_signal.emit(f"   标准输出: {result.stdout}")
                    self.finished_signal.emit(False, f"下载失败: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                self.progress_signal.emit("❌ 下载测试超时")
                self.finished_signal.emit(False, "下载超时，可能是网络问题")
                return
            except Exception as e:
                self.progress_signal.emit(f"❌ 下载测试异常: {e}")
                self.finished_signal.emit(False, f"下载异常: {e}")
                return
                
        except Exception as e:
            self.progress_signal.emit(f"❌ 诊断过程异常: {e}")
            self.finished_signal.emit(False, f"诊断异常: {e}")

class DownloadDiagnosticDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("下载问题诊断工具")
        self.setFixedSize(600, 500)
        
        layout = QVBoxLayout()
        
        # URL输入
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("视频URL:"))
        self.url_input = QLineEdit()
        self.url_input.setMinimumHeight(36)
        self.url_input.setFont(QFont('Segoe UI', 13))
        self.url_input.setStyleSheet("QLineEdit { padding: 8px 16px; border: 1px solid rgba(0,0,0,0.08); border-radius: 18px; background-color: #efe9e7; color: #1f1f1f; font-size: 14px; } QLineEdit:focus { background-color: #fdfcfc; border: 1px solid #8bb5e8; }")
        self.url_input.setPlaceholderText("请输入要测试的视频URL")
        self.url_input.setText("https://www.youtube.com/watch?v=dQw4w9WgXcQ")  # 默认测试URL
        url_layout.addWidget(self.url_input)
        layout.addLayout(url_layout)
        
        # 输出路径
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("输出路径:"))
        self.path_input = QLineEdit()
        self.path_input.setText(os.path.join(os.getcwd(), "diagnostic_downloads"))
        path_layout.addWidget(self.path_input)
        layout.addLayout(path_layout)
        
        # 开始诊断按钮
        self.start_button = QPushButton("开始诊断")
        self.start_button.clicked.connect(self.start_diagnostic)
        layout.addWidget(self.start_button)
        
        # 进度显示
        self.progress_text = QTextEdit()
        self.progress_text.setReadOnly(True)
        layout.addWidget(self.progress_text)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        self.setLayout(layout)
        
        self.diagnostic_thread = None

    def _make_text_icon(self, text, size=16, color="#5f6368"):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        f = QFont('Segoe UI', 10)
        f.setPixelSize(size)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(pm.rect(), Qt.AlignCenter, text)
        p.end()
        return QIcon(pm)
    
    def start_diagnostic(self):
        url = self.url_input.text().strip()
        output_path = self.path_input.text().strip()
        
        if not url:
            QMessageBox.warning(self, "警告", "请输入视频URL")
            return
        
        if not output_path:
            QMessageBox.warning(self, "警告", "请输入输出路径")
            return
        
        self.start_button.setEnabled(False)
        self.progress_text.clear()
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 不确定进度
        
        self.diagnostic_thread = DiagnosticThread(url, output_path)
        self.diagnostic_thread.progress_signal.connect(self.on_progress)
        self.diagnostic_thread.finished_signal.connect(self.on_finished)
        self.diagnostic_thread.start()
    
    def on_progress(self, message):
        self.progress_text.append(message)
        self.progress_text.verticalScrollBar().setValue(
            self.progress_text.verticalScrollBar().maximum()
        )
    
    def on_finished(self, success, message):
        self.start_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        
        if success:
            self.progress_text.append(f"\n🎉 诊断完成: {message}")
            QMessageBox.information(self, "诊断完成", f"✅ {message}")
        else:
            self.progress_text.append(f"\n❌ 诊断失败: {message}")
            url_lower = self.url_input.text().strip().lower()
            is_target_platform = any(domain in url_lower for domain in [
                'douyin.com', 'iesdouyin.com',
                'kuaishou.com', 'gifshow.com', 'kwai.com',
                'xiaohongshu.com', 'xhslink.com'
            ])
            err_msg = f"❌ {message}"
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Critical)
            msg_box.setWindowTitle("诊断失败")
            if is_target_platform:
                if "<br>" not in err_msg and "\n" in err_msg:
                    err_msg = err_msg.replace("\n", "<br>")
                err_msg += "<br><br><font color='#e74c3c'><b>💡 提示：抖音、快手、小红书请尝试左上角媒体捕捉功能下载</b></font>"
                msg_box.setTextFormat(Qt.RichText)
            msg_box.setText(err_msg)
            msg_box.setStandardButtons(QMessageBox.Ok)
            msg_box.exec_()

def main():
    app = QApplication(sys.argv)
    dialog = DownloadDiagnosticDialog()
    dialog.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
