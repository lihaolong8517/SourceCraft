"""
浏览器预安装脚本 - 在打包前下载浏览器
确保安装程序包含所有必要的浏览器文件
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def download_playwright_browsers():
    """下载 Playwright 浏览器到本地目录"""
    
    print("[预安装] 开始下载 Playwright 浏览器...")
    
    # 创建浏览器存储目录
    browsers_dir = Path("playwright_browsers")
    browsers_dir.mkdir(exist_ok=True)
    
    try:
        # 安装 Chromium 浏览器
        print("[预安装] 安装 Chromium 浏览器...")
        result = subprocess.run([
            sys.executable, "-m", "playwright", "install", "chromium"
        ], capture_output=True, text=True, timeout=600)
        
        if result.returncode == 0:
            print("[预安装] Chromium 安装成功")
            
            # 复制浏览器文件到项目目录
            src_dir = Path.home() / "AppData" / "Local" / "ms-playwright"
            if src_dir.exists():
                print(f"[预安装] 复制浏览器文件从 {src_dir}")
                
                # 查找 chromium 目录
                for item in src_dir.iterdir():
                    if item.is_dir() and "chromium" in item.name.lower():
                        dst_dir = browsers_dir / item.name
                        print(f"[预安装] 复制 {item} -> {dst_dir}")
                        
                        # 删除旧目录并复制新目录
                        if dst_dir.exists():
                            shutil.rmtree(dst_dir)
                        shutil.copytree(item, dst_dir)
                        
                        # 验证浏览器文件
                        chrome_exe = find_chrome_executable(dst_dir)
                        if chrome_exe and chrome_exe.exists():
                            print(f"[预安装] ✓ 浏览器可执行文件已找到: {chrome_exe}")
                            return True
                        else:
                            print(f"[预安装] ✗ 未找到浏览器可执行文件")
                            return False
                
        else:
            print(f"[预安装] Chromium 安装失败: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("[预安装] 浏览器下载超时")
        return False
    except Exception as e:
        print(f"[预安装] 下载过程出错: {e}")
        return False
    
    return False

def find_chrome_executable(browser_dir):
    """在浏览器目录中查找 chrome 可执行文件"""
    
    # 可能的 chrome 路径
    possible_paths = [
        "chrome-win/chrome.exe",
        "chrome-linux/chrome", 
        "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        "**/chrome.exe",
        "**/chrome",
        "**/Chromium.app/Contents/MacOS/Chromium"
    ]
    
    for path_pattern in possible_paths:
        import glob
        full_pattern = browser_dir / path_pattern
        matches = glob.glob(str(full_pattern), recursive=True)
        for match in matches:
            match_path = Path(match)
            if match_path.is_file():
                return match_path
    
    return None

def verify_installation():
    """验证浏览器安装"""
    
    browsers_dir = Path("playwright_browsers")
    if not browsers_dir.exists():
        print("[预安装] ✗ 浏览器目录不存在")
        return False
    
    # 查找浏览器可执行文件
    chrome_exe = find_chrome_executable(browsers_dir)
    if chrome_exe and chrome_exe.exists():
        print(f"[预安装] ✓ 浏览器已准备好: {chrome_exe}")
        return True
    else:
        print("[预安装] ✗ 浏览器文件不完整")
        return False

if __name__ == '__main__':
    print("=" * 50)
    print("Playwright 浏览器预安装工具")
    print("=" * 50)
    
    # 检查是否已安装
    if verify_installation():
        print("[预安装] 浏览器已存在，跳过下载")
    else:
        # 下载浏览器
        if download_playwright_browsers():
            print("[预安装] ✓ 浏览器预安装成功")
        else:
            print("[预安装] ✗ 浏览器预安装失败")
            sys.exit(1)
    
    print("[预安装] 完成")