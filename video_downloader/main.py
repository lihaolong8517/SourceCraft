"""应用入口点"""
import sys
import os
import time
import subprocess
import json
import re
import ctypes
import multiprocessing
import runpy
import io
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMessageBox

from .constants import RUNTIME_APP_DIR, WINDOWS_APP_USER_MODEL_ID, APP_ORGANIZATION_NAME, APP_DISPLAY_NAME
from . import utils
from .utils import _load_app_icon
from .app import VideoDownloaderApp
from . import _set_windows_app_user_model_id


def check_dependencies():
    """检查必要的依赖是否存在"""
    global FFMPEG_CMD
    
    # 检查yt-dlp是否存在
    try:
        process = subprocess.Popen(
            ["yt-dlp.exe", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        process.communicate()
        if process.returncode != 0:
            return False, "yt-dlp.exe未找到或无法运行"
    except Exception:
        return False, "yt-dlp.exe未找到，请确保已安装yt-dlp或将yt-dlp.exe放在程序目录下"
    
    # 检查ffmpeg是否存在
    ffmpeg_cmd = None
    # 首先尝试使用当前目录下的ffmpeg.exe
    try:
        process = subprocess.Popen(
            ["ffmpeg.exe", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        process.communicate()
        if process.returncode == 0:
            ffmpeg_cmd = "ffmpeg.exe"
    except Exception:
        pass
    
    # 如果当前目录下没有ffmpeg.exe，尝试使用系统PATH中的ffmpeg
    if ffmpeg_cmd is None:
        try:
            process = subprocess.Popen(
                ["ffmpeg", "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            process.communicate()
            if process.returncode == 0:
                ffmpeg_cmd = "ffmpeg"
        except Exception:
            pass
    
    if ffmpeg_cmd is None:
        return False, "ffmpeg未找到，请确保已安装ffmpeg或将ffmpeg.exe放在程序目录下"
    
    FFMPEG_CMD = ffmpeg_cmd
    return True, "依赖检查通过"

def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--realesrgan-job":
        print("画质修复功能已从当前版本移除。")
        sys.exit(2)

    # === 【关键】在创建QApplication之前设置属性 ===
    # 使用软件OpenGL防止WebEngine崩溃
    QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL)
    # 共享OpenGL上下文，有助于音视频播放
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    _set_windows_app_user_model_id()
    
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_ORGANIZATION_NAME)
    app.setApplicationName(APP_DISPLAY_NAME)
    app_icon = _load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    
    # === 【音频修复】确保Chromium参数已设置 ===
    print("[App] 音频修复参数已设置:")
    print(f"  QTWEBENGINE_CHROMIUM_FLAGS: {os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', 'N/A')[:100]}...")
    
    # 检查依赖
    success, message = check_dependencies()
    if not success:
        QMessageBox.critical(None, "依赖检查失败", message)
        sys.exit(1)
    
    # === 【许可证检查】启动时验证授权状态 ===
    license_manager = None
    try:
        from license_dialogs import check_license_on_startup
        from license_manager import LicenseManager

        startup_begin = time.perf_counter()
        license_manager = LicenseManager()

        # 检查许可证状态
        if not check_license_on_startup(manager=license_manager):
            # 用户选择退出
            print("[App] 用户未激活，程序退出")
            sys.exit(0)
        
        print("[App] 许可证验证通过")
        print(f"[Startup] 授权阶段耗时: {time.perf_counter() - startup_begin:.2f}s")
    except ImportError as e:
        if getattr(sys, "frozen", False):
            QMessageBox.critical(None, "授权错误", f"授权模块缺失或损坏，请重新安装：\n{str(e)}")
            sys.exit(1)
        # 开发模式允许无授权模块启动，便于调试
        print(f"[App] 许可证模块未加载（开发模式）: {e}")
    except Exception as e:
        print(f"[App] 许可证检查异常: {e}")
        QMessageBox.critical(None, "授权错误", f"程序授权检查异常，请联系客服：\n{str(e)}")
        sys.exit(1)
    
    window_create_begin = time.perf_counter()
    window = VideoDownloaderApp(license_manager=license_manager)
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    print(f"[Startup] 主窗口构建耗时: {time.perf_counter() - window_create_begin:.2f}s")
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
