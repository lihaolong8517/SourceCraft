"""视频下载器常量定义"""
import os
import sys

# Runtime app directory for both source and PyInstaller builds.
RUNTIME_APP_DIR = os.path.dirname(
    os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__)
)
APP_DISPLAY_NAME = "SourceCraft"
APP_ORGANIZATION_NAME = "SourceCraft"
WINDOWS_APP_USER_MODEL_ID = "SourceCraft.SourceCraft"
APP_ICON_CANDIDATES = ("app_icon.ico", "app_icon.png", "app_icon.svg")
