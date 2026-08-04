#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包前准备脚本 - 确保所有依赖就绪
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def print_step(step, title):
    print("\n" + "=" * 70)
    print(f"  步骤 {step}: {title}")
    print("=" * 70)

def check_playwright_installed():
    """检查 Playwright 是否已安装"""
    print_step(1, "检查 Playwright 安装")
    
    try:
        import playwright
        print(f"✓ Playwright 已安装")
        print(f"  路径: {Path(playwright.__file__).parent}")
        return True
    except ImportError:
        print(f"✗ Playwright 未安装")
        print(f"  请运行: pip install playwright")
        return False

def download_browsers():
    """下载 Playwright 浏览器到本地目录"""
    print_step(2, "下载浏览器到本地目录")
    
    browsers_dir = Path("playwright_browsers")
    
    # 检查是否已存在
    if browsers_dir.exists():
        # 检查是否有内容
        contents = list(browsers_dir.iterdir())
        if contents:
            print(f"✓ 本地浏览器目录已存在: {browsers_dir}")
            
            # 统计大小
            total_size = 0
            for item in browsers_dir.rglob("*"):
                if item.is_file():
                    total_size += item.stat().st_size
            
            size_mb = total_size / 1024 / 1024
            print(f"  大小: {size_mb:.1f} MB")
            print(f"  文件数: {len(list(browsers_dir.rglob('*')))}")
            
            # 查找 chrome.exe
            chrome_exes = list(browsers_dir.rglob("chrome.exe"))
            if chrome_exes:
                print(f"  ✓ 找到 chrome.exe: {len(chrome_exes)} 个")
                for exe in chrome_exes[:2]:
                    print(f"    • {exe.relative_to(browsers_dir)}")
                return True
            else:
                print(f"  ⚠ 未找到 chrome.exe，需要重新下载")
                shutil.rmtree(browsers_dir)
    
    # 创建目录
    browsers_dir.mkdir(exist_ok=True)
    
    print(f"正在下载 Chromium 浏览器...")
    print(f"这可能需要几分钟时间，请耐心等待...")
    
    try:
        # 设置环境变量，让浏览器下载到本地目录
        env = os.environ.copy()
        env['PLAYWRIGHT_BROWSERS_PATH'] = str(browsers_dir.absolute())
        
        # 下载 Chromium
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            env=env,
            capture_output=True,
            text=True,
            timeout=600
        )
        
        if result.returncode == 0:
            print(f"✓ Chromium 下载成功")
            
            # 验证下载
            chrome_exes = list(browsers_dir.rglob("chrome.exe"))
            if chrome_exes:
                print(f"  ✓ 验证成功: 找到 {len(chrome_exes)} 个 chrome.exe")
                return True
            else:
                print(f"  ✗ 验证失败: 未找到 chrome.exe")
                print(f"  输出: {result.stdout}")
                return False
        else:
            print(f"✗ 下载失败")
            print(f"  错误: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"✗ 下载超时")
        return False
    except Exception as e:
        print(f"✗ 下载异常: {e}")
        return False

def verify_spec_file():
    """验证 .spec 文件配置"""
    print_step(3, "验证 PyInstaller 配置")
    
    spec_file = Path("SourceCraft.spec")
    if not spec_file.exists():
        print(f"✗ .spec 文件不存在: {spec_file}")
        return False
    
    print(f"✓ .spec 文件存在")
    
    with open(spec_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 检查关键配置
    checks = {
        "playwright_browsers": "本地浏览器目录",
        "collect_all('playwright')": "Playwright 完整收集",
        "playwright.async_api": "异步 API",
    }
    
    all_ok = True
    for pattern, desc in checks.items():
        if pattern in content:
            print(f"  ✓ {desc}")
        else:
            print(f"  ✗ 缺失: {desc}")
            all_ok = False
    
    return all_ok

def check_dependencies():
    """检查其他依赖文件"""
    print_step(4, "检查依赖文件")
    
    required_files = {
        "ffmpeg.exe": "FFmpeg 视频处理",
        "ffprobe.exe": "FFprobe 视频分析",
        "yt-dlp.exe": "yt-dlp 下载器",
        "deno.exe": "Deno 运行时",
        "playwright_fix.py": "环境修复模块",
        "playwright_browser.py": "浏览器控制模块",
    }
    
    all_ok = True
    for file, desc in required_files.items():
        if Path(file).exists():
            size = Path(file).stat().st_size / 1024 / 1024
            print(f"  ✓ {desc}: {file} ({size:.1f} MB)")
        else:
            print(f"  ✗ 缺失: {desc} ({file})")
            all_ok = False
    
    return all_ok

def estimate_package_size():
    """估算打包后的大小"""
    print_step(5, "估算打包大小")
    
    # 计算浏览器大小
    browsers_dir = Path("playwright_browsers")
    browser_size = 0
    if browsers_dir.exists():
        for item in browsers_dir.rglob("*"):
            if item.is_file():
                browser_size += item.stat().st_size
    
    # 计算其他依赖大小
    deps_size = 0
    for file in ["ffmpeg.exe", "ffprobe.exe", "yt-dlp.exe", "deno.exe"]:
        if Path(file).exists():
            deps_size += Path(file).stat().st_size
    
    # 估算 Python 包大小（约 50MB）
    python_size = 50 * 1024 * 1024
    
    total_size = browser_size + deps_size + python_size
    
    print(f"  浏览器: {browser_size / 1024 / 1024:.1f} MB")
    print(f"  依赖工具: {deps_size / 1024 / 1024:.1f} MB")
    print(f"  Python 包: {python_size / 1024 / 1024:.1f} MB")
    print(f"  预计总大小: {total_size / 1024 / 1024:.1f} MB")
    
    if total_size > 500 * 1024 * 1024:
        print(f"  ⚠ 警告: 安装包可能超过 500MB")

def main():
    print("=" * 70)
    print("  SourceCraft - 打包前准备工具")
    print("=" * 70)
    
    # 执行检查步骤
    steps = [
        check_playwright_installed,
        download_browsers,
        verify_spec_file,
        check_dependencies,
        estimate_package_size,
    ]
    
    all_passed = True
    for step in steps:
        if not step():
            all_passed = False
    
    # 总结
    print("\n" + "=" * 70)
    if all_passed:
        print("  ✓ 所有检查通过，可以开始打包")
        print("=" * 70)
        print("\n下一步:")
        print("  运行: python build_all.py")
    else:
        print("  ✗ 存在问题，请先解决")
        print("=" * 70)
        print("\n请根据上述提示解决问题后重新运行此脚本")
    
    print("\n按回车键退出...")
    try:
        input()
    except:
        pass

if __name__ == "__main__":
    main()
