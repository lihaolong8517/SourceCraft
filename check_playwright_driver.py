#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
检查 Playwright 驱动程序是否完整
"""

import os
import sys
from pathlib import Path

def check_playwright_package():
    """检查 Playwright 包结构"""
    print("=" * 60)
    print("检查 Playwright 包")
    print("=" * 60)
    
    try:
        import playwright
        pw_path = Path(playwright.__file__).parent
        print(f"\n✓ Playwright 包路径: {pw_path}")
        
        # 检查关键目录
        driver_dir = pw_path / "driver"
        if driver_dir.exists():
            print(f"✓ 驱动目录存在: {driver_dir}")
            
            # 列出驱动目录内容
            print("\n驱动目录结构:")
            for item in driver_dir.rglob("*"):
                if item.is_file():
                    rel_path = item.relative_to(driver_dir)
                    size_kb = item.stat().st_size / 1024
                    print(f"  {rel_path} ({size_kb:.1f} KB)")
                    
                    # 特别关注可执行文件
                    if item.suffix in ['.exe', '.node', '']:
                        print(f"    → 可执行文件")
        else:
            print(f"✗ 驱动目录不存在: {driver_dir}")
            
        return True
    except ImportError as e:
        print(f"✗ Playwright 未安装: {e}")
        return False

def check_playwright_cli():
    """检查 Playwright CLI 是否可用"""
    print("\n" + "=" * 60)
    print("检查 Playwright CLI")
    print("=" * 60)
    
    import subprocess
    
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print(f"\n✓ Playwright CLI 可用")
            print(f"  版本: {result.stdout.strip()}")
            return True
        else:
            print(f"\n✗ Playwright CLI 失败")
            print(f"  错误: {result.stderr}")
            return False
    except Exception as e:
        print(f"\n✗ Playwright CLI 检查失败: {e}")
        return False

def check_browser_binaries():
    """检查浏览器二进制文件"""
    print("\n" + "=" * 60)
    print("检查浏览器二进制文件")
    print("=" * 60)
    
    # 检查系统浏览器目录
    home = Path.home()
    ms_playwright = home / "AppData" / "Local" / "ms-playwright"
    
    if ms_playwright.exists():
        print(f"\n✓ 系统浏览器目录: {ms_playwright}")
        
        for browser_dir in ms_playwright.iterdir():
            if browser_dir.is_dir():
                size_mb = sum(f.stat().st_size for f in browser_dir.rglob("*") if f.is_file()) / 1024 / 1024
                print(f"  • {browser_dir.name}: {size_mb:.1f} MB")
    else:
        print(f"\n✗ 系统浏览器目录不存在: {ms_playwright}")
    
    # 检查项目本地浏览器
    local_browsers = Path("playwright_browsers")
    if local_browsers.exists():
        print(f"\n✓ 项目浏览器目录: {local_browsers}")
        
        for browser_dir in local_browsers.iterdir():
            if browser_dir.is_dir():
                size_mb = sum(f.stat().st_size for f in browser_dir.rglob("*") if f.is_file()) / 1024 / 1024
                print(f"  • {browser_dir.name}: {size_mb:.1f} MB")
    else:
        print(f"\n⚠ 项目浏览器目录不存在: {local_browsers}")

def test_playwright_import():
    """测试 Playwright 导入"""
    print("\n" + "=" * 60)
    print("测试 Playwright 导入")
    print("=" * 60)
    
    tests = [
        ("playwright", "基础包"),
        ("playwright.sync_api", "同步 API"),
        ("playwright.async_api", "异步 API"),
    ]
    
    for module, desc in tests:
        try:
            __import__(module)
            print(f"  ✓ {desc}: {module}")
        except ImportError as e:
            print(f"  ✗ {desc}: {module}")
            print(f"    错误: {e}")

def main():
    print("=" * 60)
    print("Playwright 驱动程序完整性检查")
    print("=" * 60)
    
    check_playwright_package()
    check_playwright_cli()
    check_browser_binaries()
    test_playwright_import()
    
    print("\n" + "=" * 60)
    print("检查完成")
    print("=" * 60)
    
    print("\n按回车键退出...")
    try:
        input()
    except:
        pass

if __name__ == "__main__":
    main()
