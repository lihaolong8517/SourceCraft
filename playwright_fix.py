"""
Playwright 打包修复模块
解决 PyInstaller 打包后浏览器无法启动的问题
"""

import os
import sys
import subprocess
import shutil
import traceback
from pathlib import Path

# === 调试日志 ===
def _pf_debug_log(msg):
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{ts}] [PW_FIX] {msg}"
    try:
        print(line, flush=True)
    except: pass
    try:
        if getattr(sys, 'frozen', False):
            base = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'SourceCraft')
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        log_file = os.path.join(base, 'browser_debug.log')
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except: pass


def get_bundled_browser_path():
    """
    获取打包后内置浏览器的路径
    支持 PyInstaller --onedir 模式
    """
    # 判断是否是打包环境
    is_frozen = getattr(sys, 'frozen', False)
    _pf_debug_log(f"get_bundled_browser_path: frozen={is_frozen}")
    
    if is_frozen:
        # 打包环境：有多个可能的位置
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        exe_dir = os.path.dirname(sys.executable)
        _pf_debug_log(f"get_bundled_browser_path: _MEIPASS={base_path}, exe_dir={exe_dir}")
        
        # 【关键修复】优先查找与 exe 同级的 playwright_browsers 目录
        search_paths = [
            os.path.join(exe_dir, 'playwright_browsers'),  # 最优先：exe 同级目录
            os.path.join(exe_dir, '_internal', 'playwright_browsers'),  # PyInstaller _internal
            os.path.join(base_path, 'playwright_browsers'),  # _MEIPASS 临时目录
            os.path.join(base_path, 'playwright', 'driver', 'package', '.local-browsers'),
            os.path.join(exe_dir, '_internal', 'playwright', 'driver', 'package', '.local-browsers'),
        ]
    else:
        # 开发环境
        app_dir = os.path.dirname(os.path.abspath(__file__))
        search_paths = [
            os.path.join(app_dir, 'playwright_browsers'),
        ]
    
    for path in search_paths:
        exists = os.path.exists(path) and os.path.isdir(path)
        _pf_debug_log(f"get_bundled_browser_path: 检查 {path} -> {'存在' if exists else '不存在'}")
        if exists:
            try:
                contents = os.listdir(path)
                _pf_debug_log(f"get_bundled_browser_path: 目录内容 ({len(contents)} 项): {contents[:10]}")
                # 验证目录不为空
                if len(contents) == 0:
                    _pf_debug_log(f"get_bundled_browser_path: 目录为空，跳过")
                    continue
            except Exception as e:
                _pf_debug_log(f"get_bundled_browser_path: 列目录失败: {e}")
                continue
            print(f"[Playwright Fix] 找到浏览器目录: {path}")
            return os.path.normpath(path)
    
    _pf_debug_log("get_bundled_browser_path: 未找到任何浏览器目录")
    return None


def find_chromium_executable(browsers_dir):
    """
    在浏览器目录中查找 Chromium 可执行文件
    返回 chrome.exe 的完整路径
    """
    if not browsers_dir or not os.path.exists(browsers_dir):
        _pf_debug_log(f"find_chromium_executable: 目录无效: {browsers_dir}")
        return None
    
    _pf_debug_log(f"find_chromium_executable: 搜索目录: {browsers_dir}")
    
    # 常见的 Chromium 路径模式 (Windows)
    search_patterns = [
        'chromium-*/chrome-win/chrome.exe',
        '*/chrome-win/chrome.exe',
        'chrome-win/chrome.exe',
        '**/chrome.exe',
    ]
    
    import glob
    for pattern in search_patterns:
        full_pattern = os.path.join(browsers_dir, pattern)
        matches = glob.glob(full_pattern, recursive=True)
        _pf_debug_log(f"find_chromium_executable: glob '{full_pattern}' -> {len(matches)} 匹配: {matches[:5]}")
        for match in matches:
            if os.path.isfile(match):
                _pf_debug_log(f"find_chromium_executable: 找到: {match}")
                # 统一使用系统路径分隔符，修复 Errno 22
                final_path = os.path.normpath(match)
                print(f"[Playwright Fix] 找到 Chromium 可执行文件: {final_path}")
                return final_path
    
    _pf_debug_log("find_chromium_executable: 未找到 Chromium")
    return None


def setup_playwright_environment():
    """
    设置 Playwright 运行环境
    确保打包后能正确找到并使用内置浏览器
    """
    _pf_debug_log("setup_playwright_environment: 开始设置")
    print("[Playwright Fix] 正在设置 Playwright 环境...")
    
    # 1. 获取内置浏览器路径
    browsers_dir = get_bundled_browser_path()
    
    if browsers_dir:
        # 设置环境变量，让 Playwright 知道浏览器位置
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = browsers_dir
        os.environ['PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD'] = '1'
        _pf_debug_log(f"setup_playwright_environment: PLAYWRIGHT_BROWSERS_PATH = {browsers_dir}")
        print(f"[Playwright Fix] 已设置 PLAYWRIGHT_BROWSERS_PATH = {browsers_dir}")
        
        # 验证浏览器可执行文件
        chromium_exe = find_chromium_executable(browsers_dir)
        if chromium_exe:
            _pf_debug_log(f"setup_playwright_environment: Chromium 已就绪: {chromium_exe}")
            print(f"[Playwright Fix] [OK] Chromium 浏览器已就绪: {chromium_exe}")
            os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE'] = chromium_exe
        else:
            _pf_debug_log("setup_playwright_environment: 未找到 Chromium 可执行文件")
            print("[Playwright Fix] [WARN] 未找到 Chromium 可执行文件")
    else:
        _pf_debug_log("setup_playwright_environment: 未找到内置浏览器目录")
        print("[Playwright Fix] [WARN] 未找到内置浏览器目录")
    
    # 2. 确保 Playwright 驱动路径
    is_frozen = getattr(sys, 'frozen', False)
    if is_frozen:
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        potential_driver_dirs = [
            os.path.join(base_path, 'playwright', 'driver'),
            os.path.join(os.path.dirname(sys.executable), '_internal', 'playwright', 'driver'),
            os.path.join(os.path.dirname(sys.executable), 'playwright', 'driver'),
        ]
        
        for driver_dir in potential_driver_dirs:
            exists = os.path.exists(driver_dir)
            _pf_debug_log(f"setup_playwright_environment: 驱动目录 {driver_dir} -> {'存在' if exists else '不存在'}")
            if exists:
                print(f"[Playwright Fix] 找到 Playwright 驱动目录: {driver_dir}")
                break
    
    _pf_debug_log("setup_playwright_environment: 设置完成")
    return True


def get_chromium_executable_path():
    """
    获取 Chromium 可执行文件路径
    供 playwright_browser.py 使用
    """
    # 首先检查环境变量（由 setup_playwright_environment 设置）
    exe_path = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
    if exe_path and os.path.exists(exe_path):
        return exe_path
    
    # 否则尝试查找
    browsers_dir = get_bundled_browser_path()
    if browsers_dir:
        return find_chromium_executable(browsers_dir)
    
    return None


def install_playwright_browsers():
    """
    安装 Playwright 浏览器到本地目录
    注意：此函数在打包环境中无法使用 python -m playwright 命令
    """
    is_frozen = getattr(sys, 'frozen', False)
    
    if is_frozen:
        print("[Playwright Fix] 打包环境无法自动下载浏览器")
        print("[Playwright Fix] 请确保打包时已包含 playwright_browsers 目录")
        return False
    
    # 开发环境下可以尝试下载
    print("[Playwright Fix] 尝试下载 Playwright 浏览器...")
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'playwright', 'install', 'chromium'],
            capture_output=True,
            text=True,
            timeout=600
        )
        if result.returncode == 0:
            print("[Playwright Fix] Chromium 浏览器下载成功")
            return True
        else:
            print(f"[Playwright Fix] 浏览器下载失败: {result.stderr}")
            return False
    except Exception as e:
        print(f"[Playwright Fix] 下载异常: {e}")
        return False

def fix_windows_path_env():
    """修复可能导致 OSError [Errno 22] 的环境变量路径问题"""
    for key in ['PLAYWRIGHT_BROWSERS_PATH', 'PLAYWRIGHT_CHROMIUM_EXECUTABLE', 'PLAYWRIGHT_EXECUTABLE_PATH']:
        if key in os.environ:
            val = os.environ[key]
            if val:
                # 强制使用系统分隔符且去除多余双引号
                os.environ[key] = os.path.normpath(val.replace('"', '').replace("'", ""))
                _pf_debug_log(f"环境变量修复: {key} = {os.environ[key]}")


if __name__ == '__main__':
    print("=" * 50)
    print("Playwright 环境诊断")
    print("=" * 50)
    
    is_frozen = getattr(sys, 'frozen', False)
    print(f"运行环境: {'打包环境' if is_frozen else '开发环境'}")
    print(f"Python: {sys.executable}")
    
    if is_frozen:
        print(f"_MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}")
    
    print()
    setup_playwright_environment()
    
    print()
    exe_path = get_chromium_executable_path()
    if exe_path:
        print(f"[OK] Chromium 可执行文件: {exe_path}")
    else:
        print("[ERROR] 未找到 Chromium 可执行文件")
    
    print()
    print("=" * 50)
