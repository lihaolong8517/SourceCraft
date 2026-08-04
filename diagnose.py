#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
视频下载器诊断工具
检查环境配置和可能的问题
"""

import os
import sys
import subprocess
import shutil
import io

# 设置标准输出编码为 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("=" * 60)
print("  视频下载器诊断工具")
print("=" * 60)
print()

# 0. 检查 Deno 运行时（yt-dlp 需要）
print("[0] Deno 运行时检查 (yt-dlp YouTube 下载必需)")

# 添加 Deno 到 PATH
if sys.platform == 'win32':
    deno_path = os.path.join(os.environ.get('USERPROFILE', ''), '.deno', 'bin')
    if os.path.exists(deno_path) and deno_path not in os.environ.get('PATH', ''):
        os.environ['PATH'] = deno_path + os.pathsep + os.environ.get('PATH', '')

deno_exe = shutil.which('deno')
if deno_exe:
    try:
        result = subprocess.run(['deno', '--version'], capture_output=True, text=True, timeout=10)
        version_line = result.stdout.strip().split('\n')[0] if result.stdout else '未知版本'
        print(f"    [OK] Deno 已安装: {version_line}")
        print(f"         路径: {deno_exe}")
        
        # 检查版本是否满足 yt-dlp 要求 (>= 2.0.0)
        import re
        version_match = re.search(r'deno (\d+)\.(\d+)\.(\d+)', version_line)
        if version_match:
            major, minor, patch = map(int, version_match.groups())
            if major >= 2:
                print(f"    [OK] 版本满足 yt-dlp 要求 (>= 2.0.0)")
            else:
                print(f"    [WARN] 版本过低，yt-dlp 需要 Deno >= 2.0.0")
    except Exception as e:
        print(f"    [WARN] Deno 检测失败: {e}")
else:
    print("    [FAIL] Deno 未安装")
    print("           YouTube 下载可能会失败！")
    print("           安装方法: irm https://deno.land/install.ps1 | iex")
print()

# 1. 检查 Python 版本
print("[1] Python 版本检查")
print(f"    Python 版本: {sys.version}")
print(f"    Python 路径: {sys.executable}")
print()

# 2. 检查环境变量
print("[2] 环境变量检查")
qtwebengine_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
qtwebengine_sandbox = os.environ.get("QTWEBENGINE_DISABLE_SANDBOX", "")

if qtwebengine_flags:
    print(f"    [OK] QTWEBENGINE_CHROMIUM_FLAGS 已设置")
    print(f"         值: {qtwebengine_flags[:100]}...")
    
    # 检查关键标志
    critical_flags = [
        "disable-blink-features=AutomationControlled",
        "exclude-switches=enable-automation",
        "disable-automation"
    ]
    
    for flag in critical_flags:
        if flag in qtwebengine_flags:
            print(f"         [OK] 包含: {flag}")
        else:
            print(f"         [FAIL] 缺失: {flag}")
else:
    print("    [FAIL] QTWEBENGINE_CHROMIUM_FLAGS 未设置")

if qtwebengine_sandbox:
    print(f"    [OK] QTWEBENGINE_DISABLE_SANDBOX = {qtwebengine_sandbox}")
else:
    print("    [WARN] QTWEBENGINE_DISABLE_SANDBOX 未设置")
print()

# 3. 检查必要文件
print("[3] 文件检查")
required_files = [
    "cef_browser.py",
    "stealth_inject.js",
    "test_stealth.html",
    "test_webdriver.html",
    "start_browser.bat",
    "clear_browser_data.bat"
]

for filename in required_files:
    if os.path.exists(filename):
        size = os.path.getsize(filename)
        print(f"    [OK] {filename} ({size:,} bytes)")
    else:
        print(f"    [FAIL] {filename} 不存在")
print()

# 4. 检查 PyQt5 和 QtWebEngine
print("[4] PyQt5 模块检查")
try:
    from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
    print(f"    [OK] PyQt5 已安装")
    print(f"         PyQt5 版本: {PYQT_VERSION_STR}")
    print(f"         Qt 版本: {QT_VERSION_STR}")
except ImportError as e:
    print(f"    [FAIL] PyQt5 未安装: {e}")

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    print(f"    [OK] QtWebEngineWidgets 可用")
except ImportError as e:
    print(f"    [FAIL] QtWebEngineWidgets 不可用: {e}")

try:
    from PyQt5.QtWebEngineCore import QWebEngineUrlRequestInterceptor
    print(f"    [OK] QWebEngineUrlRequestInterceptor 可用")
except ImportError as e:
    print(f"    [FAIL] QWebEngineUrlRequestInterceptor 不可用: {e}")
print()

# 5. 检查 stealth_inject.js 内容
print("[5] stealth_inject.js 内容检查")
if os.path.exists("stealth_inject.js"):
    with open("stealth_inject.js", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查关键代码
    checks = [
        ("delete navigator.webdriver", "删除 webdriver"),
        ("Object.defineProperty(navigator, 'webdriver'", "重定义 webdriver"),
        ("window.chrome", "Chrome 对象模拟"),
        ("navigator.plugins", "Plugins 模拟"),
        ("WebGLRenderingContext", "WebGL 伪装"),
        ("HTMLCanvasElement", "Canvas 伪装")
    ]
    
    for code, desc in checks:
        if code in content:
            print(f"    [OK] 包含: {desc}")
        else:
            print(f"    [FAIL] 缺失: {desc}")
    
    print(f"    文件大小: {len(content):,} 字符")
else:
    print("    [FAIL] stealth_inject.js 不存在")
print()

# 6. 检查浏览器数据目录
print("[6] 浏览器数据目录检查")
if os.path.exists("browser_data"):
    print("    [WARN] browser_data 目录存在")
    print("           建议: 运行 clear_browser_data.bat 清除旧数据")
    
    # 统计大小
    total_size = 0
    file_count = 0
    for root, dirs, files in os.walk("browser_data"):
        for file in files:
            try:
                total_size += os.path.getsize(os.path.join(root, file))
                file_count += 1
            except:
                pass
    
    print(f"           文件数: {file_count}")
    print(f"           总大小: {total_size / 1024 / 1024:.2f} MB")
else:
    print("    [OK] browser_data 目录不存在（干净状态）")
print()

# 7. 建议
print("[7] 诊断建议")
print()

issues = []

if not deno_exe:
    issues.append("Deno 未安装 - YouTube 下载需要")

if not qtwebengine_flags or "disable-blink-features=AutomationControlled" not in qtwebengine_flags:
    issues.append("环境变量未正确设置")

if not os.path.exists("stealth_inject.js"):
    issues.append("缺少 stealth_inject.js 文件")

if os.path.exists("browser_data"):
    issues.append("存在旧的浏览器数据")

if issues:
    print("    发现以下问题:")
    for i, issue in enumerate(issues, 1):
        print(f"    {i}. {issue}")
    print()
    print("    建议操作:")
    if not deno_exe:
        print("    - 安装 Deno: irm https://deno.land/install.ps1 | iex")
    print("    - 运行 clear_browser_data.bat 清除旧数据")
    print("    - 使用 start_browser.bat 启动浏览器")
    print("    - 打开 test_webdriver.html 检查伪装效果")
else:
    print("    [OK] 未发现明显问题")
    print()
    print("    如果 navigator.webdriver 仍然是 false:")
    print("    1. 这可能是 QtWebEngine 版本的限制")
    print("    2. 尝试在 JavaScript 中手动删除")
    print("    3. 考虑使用 Selenium + undetected-chromedriver")
    print("    4. 或使用真实的 Chrome 浏览器")

print()
print("=" * 60)
print("  诊断完成")
print("=" * 60)
print()
print("按任意键退出...")
try:
    input()
except:
    pass
