#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包环境诊断工具 - 精准定位浏览器启动失败原因
"""

import os
import sys
import subprocess
import json
from pathlib import Path

def print_section(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def check_playwright_installation():
    """检查 Playwright 安装状态"""
    print_section("1. Playwright 安装检查")
    
    try:
        import playwright
        pw_path = Path(playwright.__file__).parent
        print(f"✓ Playwright 已安装: {pw_path}")
        print(f"  版本: {playwright.__version__ if hasattr(playwright, '__version__') else '未知'}")
        
        # 检查驱动目录
        driver_dir = pw_path / "driver"
        if driver_dir.exists():
            print(f"✓ 驱动目录存在: {driver_dir}")
            driver_files = list(driver_dir.rglob("*"))
            print(f"  驱动文件数: {len(driver_files)}")
        else:
            print(f"✗ 驱动目录不存在: {driver_dir}")
        
        return True
    except ImportError as e:
        print(f"✗ Playwright 未安装: {e}")
        return False

def check_browser_installation():
    """检查浏览器安装状态"""
    print_section("2. 浏览器安装检查")
    
    # 检查系统浏览器路径
    home = Path.home()
    ms_playwright = home / "AppData" / "Local" / "ms-playwright"
    
    if ms_playwright.exists():
        print(f"✓ Playwright 浏览器目录存在: {ms_playwright}")
        browsers = list(ms_playwright.iterdir())
        print(f"  已安装浏览器数: {len(browsers)}")
        for browser in browsers:
            if browser.is_dir():
                size_mb = sum(f.stat().st_size for f in browser.rglob("*") if f.is_file()) / 1024 / 1024
                print(f"  - {browser.name}: {size_mb:.1f} MB")
    else:
        print(f"✗ Playwright 浏览器目录不存在: {ms_playwright}")
    
    # 检查项目本地浏览器
    local_browsers = Path("playwright_browsers")
    if local_browsers.exists():
        print(f"✓ 项目本地浏览器目录存在: {local_browsers}")
        browsers = list(local_browsers.iterdir())
        print(f"  浏览器数: {len(browsers)}")
        for browser in browsers:
            if browser.is_dir():
                size_mb = sum(f.stat().st_size for f in browser.rglob("*") if f.is_file()) / 1024 / 1024
                print(f"  - {browser.name}: {size_mb:.1f} MB")
                
                # 查找可执行文件
                exe_files = list(browser.rglob("*.exe"))
                if exe_files:
                    print(f"    可执行文件: {len(exe_files)} 个")
                    for exe in exe_files[:3]:  # 只显示前3个
                        print(f"      • {exe.relative_to(local_browsers)}")
    else:
        print(f"✗ 项目本地浏览器目录不存在: {local_browsers}")
        print("  建议: 运行 python pre_install_browsers.py")

def check_spec_file():
    """检查 .spec 文件配置"""
    print_section("3. PyInstaller 配置检查")
    
    spec_file = Path("SourceCraft.spec")
    if not spec_file.exists():
        print(f"✗ .spec 文件不存在: {spec_file}")
        return
    
    print(f"✓ .spec 文件存在: {spec_file}")
    
    with open(spec_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 检查关键配置
    checks = {
        "collect_all('playwright')": "Playwright 完整收集",
        "collect_data_files('playwright')": "Playwright 数据文件",
        "collect_submodules('playwright')": "Playwright 子模块",
        "'playwright'": "Playwright 隐藏导入",
    }
    
    for pattern, desc in checks.items():
        if pattern in content:
            print(f"  ✓ {desc}")
        else:
            print(f"  ✗ 缺失: {desc}")
    
    # 检查是否包含浏览器目录
    if "playwright_browsers" in content:
        print(f"  ✓ 包含本地浏览器目录")
    else:
        print(f"  ⚠ 未包含本地浏览器目录")
        print(f"    建议添加: datas += [('playwright_browsers', 'playwright_browsers')]")

def check_build_output():
    """检查打包输出"""
    print_section("4. 打包输出检查")
    
    dist_dir = Path("dist") / "SourceCraft"
    if not dist_dir.exists():
        dist_dir = Path("dist") / "ChaosDownloader"
    
    if not dist_dir.exists():
        print(f"✗ 打包输出目录不存在")
        print(f"  请先运行: python build_all.py")
        return
    
    print(f"✓ 打包输出目录存在: {dist_dir}")
    
    # 检查关键文件
    key_files = {
        "playwright_fix.py": "环境修复模块",
        "playwright_browser.py": "浏览器控制模块",
        "_internal/playwright": "Playwright 包",
    }
    
    for file_path, desc in key_files.items():
        full_path = dist_dir / file_path
        if full_path.exists():
            print(f"  ✓ {desc}: {file_path}")
        else:
            print(f"  ✗ 缺失 {desc}: {file_path}")
    
    # 检查浏览器文件
    browser_paths = [
        dist_dir / "playwright_browsers",
        dist_dir / "_internal" / "playwright" / "driver" / "package" / ".local-browsers",
    ]
    
    browser_found = False
    for browser_path in browser_paths:
        if browser_path.exists():
            print(f"  ✓ 浏览器目录: {browser_path.relative_to(dist_dir)}")
            browser_found = True
            
            # 统计大小
            size_mb = sum(f.stat().st_size for f in browser_path.rglob("*") if f.is_file()) / 1024 / 1024
            print(f"    大小: {size_mb:.1f} MB")
            
            # 查找可执行文件
            exe_files = list(browser_path.rglob("chrome.exe"))
            if exe_files:
                print(f"    ✓ 找到 chrome.exe: {len(exe_files)} 个")
            else:
                print(f"    ✗ 未找到 chrome.exe")
    
    if not browser_found:
        print(f"  ✗ 未找到浏览器文件")
        print(f"    这是导致用户环境无法启动浏览器的主要原因！")

def check_environment_fix():
    """检查环境修复代码"""
    print_section("5. 环境修复代码检查")
    
    # 检查 playwright_fix.py
    fix_file = Path("playwright_fix.py")
    if fix_file.exists():
        print(f"✓ playwright_fix.py 存在")
        
        with open(fix_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查关键函数
        functions = [
            "setup_playwright_environment",
            "get_bundled_browser_path",
            "find_chromium_executable",
        ]
        
        for func in functions:
            if f"def {func}" in content:
                print(f"  ✓ 函数存在: {func}()")
            else:
                print(f"  ✗ 函数缺失: {func}()")
    else:
        print(f"✗ playwright_fix.py 不存在")
    
    # 检查主程序集成
    main_file = Path("video_downloader.py")
    if main_file.exists():
        with open(main_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if "playwright_fix" in content:
            print(f"✓ 主程序已集成环境修复")
        else:
            print(f"✗ 主程序未集成环境修复")
    
    # 检查浏览器模块集成
    browser_file = Path("playwright_browser.py")
    if browser_file.exists():
        with open(browser_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if "playwright_fix" in content and "setup_playwright_environment" in content:
            print(f"✓ 浏览器模块已集成环境修复")
        else:
            print(f"⚠ 浏览器模块可能未正确集成环境修复")

def generate_fix_recommendations():
    """生成修复建议"""
    print_section("6. 修复建议")
    
    issues = []
    
    # 检查本地浏览器
    if not Path("playwright_browsers").exists():
        issues.append({
            "问题": "缺少本地浏览器文件",
            "原因": "未运行浏览器预安装脚本",
            "解决": "运行: python pre_install_browsers.py"
        })
    
    # 检查 .spec 配置
    spec_file = Path("SourceCraft.spec")
    if spec_file.exists():
        with open(spec_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if "playwright_browsers" not in content:
            issues.append({
                "问题": ".spec 文件未包含浏览器目录",
                "原因": "打包配置不完整",
                "解决": "在 .spec 文件的 datas 中添加:\n         datas += [('playwright_browsers', 'playwright_browsers')]"
            })
    
    # 检查打包输出
    dist_dir = Path("dist") / "SourceCraft"
    if not dist_dir.exists():
        dist_dir = Path("dist") / "ChaosDownloader"
    
    if dist_dir.exists():
        browser_found = False
        for browser_path in [dist_dir / "playwright_browsers", 
                            dist_dir / "_internal" / "playwright_browsers"]:
            if browser_path.exists():
                browser_found = True
                break
        
        if not browser_found:
            issues.append({
                "问题": "打包后的程序缺少浏览器文件",
                "原因": "PyInstaller 未正确打包浏览器目录",
                "解决": "1. 确保 playwright_browsers 目录存在\n" +
                       "         2. 修改 .spec 文件包含浏览器\n" +
                       "         3. 重新运行: python build_all.py"
            })
    
    if issues:
        print("\n发现以下问题:\n")
        for i, issue in enumerate(issues, 1):
            print(f"{i}. {issue['问题']}")
            print(f"   原因: {issue['原因']}")
            print(f"   解决: {issue['解决']}")
            print()
    else:
        print("✓ 未发现明显问题")
        print("\n如果用户环境仍然无法启动浏览器，可能是:")
        print("  1. 用户系统缺少必要的运行时库 (VC++ Redistributable)")
        print("  2. 权限问题 (安装到 Program Files 需要管理员权限)")
        print("  3. 防病毒软件阻止浏览器启动")
        print("  4. 系统环境变量传递失败")

def main():
    print("=" * 70)
    print("  SourceCraft - 打包环境诊断工具")
    print("  用于诊断用户环境浏览器无法启动的问题")
    print("=" * 70)
    
    check_playwright_installation()
    check_browser_installation()
    check_spec_file()
    check_build_output()
    check_environment_fix()
    generate_fix_recommendations()
    
    print("\n" + "=" * 70)
    print("  诊断完成")
    print("=" * 70)
    print("\n按回车键退出...")
    try:
        input()
    except:
        pass

if __name__ == "__main__":
    main()
