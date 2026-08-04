#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包前检查脚本 - 确保所有必要文件都已准备好
"""

import os
import sys
from pathlib import Path

def check_browsers():
    """检查浏览器文件"""
    print("\n[1] 检查浏览器文件...")
    
    browsers_dir = Path("playwright_browsers")
    
    if not browsers_dir.exists():
        print("  [ERR] 浏览器目录不存在: playwright_browsers")
        print("  [TIP] 运行: python prepare_browsers.py")
        return False
    
    # 查找 chromium
    chromium_dirs = list(browsers_dir.glob("chromium-*"))
    if not chromium_dirs:
        print("  [ERR] 未找到 chromium 目录")
        print("  [TIP] 运行: python prepare_browsers.py")
        return False
    
    # 查找 chrome.exe
    chromium_dir = chromium_dirs[0]
    chrome_exe = list(chromium_dir.rglob("chrome.exe"))
    
    if not chrome_exe:
        print("  [ERR] 未找到 chrome.exe")
        print("  [TIP] 运行: python prepare_browsers.py")
        return False
    
    # 统计大小
    total_size = sum(f.stat().st_size for f in chromium_dir.rglob("*") if f.is_file())
    size_mb = total_size / 1024 / 1024
    
    print(f"  [OK] 浏览器文件已就绪")
    print(f"    目录: {chromium_dir.name}")
    print(f"    大小: {size_mb:.1f} MB")
    
    return True

def check_binaries():
    """检查外部可执行文件"""
    print("\n[2] 检查外部可执行文件...")
    
    required_binaries = {
        'ffmpeg.exe': '视频处理',
        'ffprobe.exe': '视频信息',
        'yt-dlp.exe': '视频下载',
        'deno.exe': 'JavaScript 运行时',
    }
    
    all_ok = True
    
    for binary, desc in required_binaries.items():
        if Path(binary).exists():
            size_kb = Path(binary).stat().st_size / 1024
            print(f"  [OK] {binary} ({desc}, {size_kb:.0f} KB)")
        else:
            print(f"  [ERR] {binary} ({desc}) - 缺失")
            all_ok = False
    
    if not all_ok:
        print("\n  [TIP] 请确保所有外部工具都已下载到项目目录")
    
    return all_ok

def check_assets():
    """检查资源文件"""
    print("\n[3] 检查资源文件...")
    
    assets = [
        'app_icon.ico',
        'app_icon.png',
        'app_icon.svg',
    ]
    
    all_ok = True
    
    for asset in assets:
        if Path(asset).exists():
            print(f"  [OK] {asset}")
        else:
            print(f"  [WARN] {asset} - 缺失（可选）")
    
    return True

def check_spec_file():
    """检查 .spec 文件"""
    print("\n[4] 检查 PyInstaller 配置...")
    
    spec_file = Path("SourceCraft.spec")
    
    if not spec_file.exists():
        print("  [ERR] SourceCraft.spec 不存在")
        return False
    
    with open(spec_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 检查是否包含浏览器目录
    if "playwright_browsers" in content:
        print("  [OK] .spec 文件已配置浏览器打包")
    else:
        print("  [WARN] .spec 文件可能未配置浏览器打包")
        print("    （build_all.py 会自动处理）")
    
    return True

def check_python_packages():
    """检查 Python 包"""
    print("\n[5] 检查 Python 包...")
    
    required_packages = {
        'playwright': 'Playwright',
        'PyQt5': 'PyQt5',
        'PIL': 'Pillow',
        'requests': 'Requests',
        'cryptography': 'Cryptography',
    }
    
    all_ok = True
    
    for package, name in required_packages.items():
        try:
            __import__(package)
            print(f"  [OK] {name}")
        except ImportError:
            print(f"  [ERR] {name} - 未安装")
            all_ok = False
    
    if not all_ok:
        print("\n  [TIP] 运行: pip install -r requirements.txt")
    
    return all_ok

def main():
    print("=" * 60)
    print("SourceCraft - 打包前检查")
    print("=" * 60)
    
    checks = [
        check_browsers,
        check_binaries,
        check_assets,
        check_spec_file,
        check_python_packages,
    ]
    
    results = []
    
    for check in checks:
        try:
            result = check()
            results.append(result)
        except Exception as e:
            print(f"  [ERR] 检查出错: {e}")
            results.append(False)
    
    print("\n" + "=" * 60)
    
    if all(results):
        print("[OK] 所有检查通过，可以开始打包！")
        print("\n下一步: python build_all.py")
    else:
        print("[WARN] 发现问题，请先解决后再打包")
        print("\n关键问题:")
        if not results[0]:
            print("  • 浏览器文件缺失 → python prepare_browsers.py")
        if not results[1]:
            print("  • 外部工具缺失 → 下载必要的可执行文件")
        if not results[4]:
            print("  • Python 包缺失 → pip install -r requirements.txt")
    
    print("=" * 60)
    
    print("\n按回车键退出...")
    try:
        input()
    except:
        pass

if __name__ == "__main__":
    main()
