#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
浏览器准备脚本 - 在打包前准备浏览器文件
确保用户环境能正常使用内置浏览器
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def download_chromium():
    """下载 Playwright Chromium 浏览器"""
    print("\n" + "=" * 60)
    print("📥 下载 Playwright Chromium 浏览器")
    print("=" * 60)
    
    try:
        # 设置环境变量，让浏览器下载到项目目录
        browsers_dir = Path("playwright_browsers").absolute()
        browsers_dir.mkdir(exist_ok=True)
        
        # 设置 Playwright 使用本地目录
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(browsers_dir)
        
        print(f"\n目标目录: {browsers_dir}")
        print("开始下载 Chromium...")
        print("-" * 60)
        
        # 执行下载命令
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            env=os.environ.copy(),
            timeout=600
        )
        
        if result.returncode == 0:
            print("\n✅ Chromium 下载成功")
            
            # 验证下载
            chromium_dirs = list(browsers_dir.glob("chromium-*"))
            if chromium_dirs:
                chromium_dir = chromium_dirs[0]
                
                # 查找 chrome.exe
                chrome_exe = list(chromium_dir.rglob("chrome.exe"))
                if chrome_exe:
                    print(f"✓ 找到浏览器可执行文件: {chrome_exe[0].relative_to(browsers_dir)}")
                    
                    # 统计大小
                    total_size = sum(f.stat().st_size for f in chromium_dir.rglob("*") if f.is_file())
                    size_mb = total_size / 1024 / 1024
                    print(f"✓ 浏览器大小: {size_mb:.1f} MB")
                    
                    return True
                else:
                    print("✗ 未找到 chrome.exe")
                    return False
            else:
                print("✗ 未找到 chromium 目录")
                return False
        else:
            print(f"\n✗ 下载失败，退出码: {result.returncode}")
            return False
            
    except subprocess.TimeoutExpired:
        print("\n✗ 下载超时（超过 10 分钟）")
        return False
    except Exception as e:
        print(f"\n✗ 下载出错: {e}")
        import traceback
        traceback.print_exc()
        return False

def copy_from_system():
    """从系统 Playwright 目录复制浏览器"""
    print("\n" + "=" * 60)
    print("📋 从系统目录复制浏览器")
    print("=" * 60)
    
    # 查找系统 Playwright 浏览器目录
    home = Path.home()
    system_browsers = home / "AppData" / "Local" / "ms-playwright"
    
    if not system_browsers.exists():
        print(f"✗ 系统浏览器目录不存在: {system_browsers}")
        return False
    
    print(f"✓ 找到系统浏览器目录: {system_browsers}")
    
    # 查找 chromium
    chromium_dirs = list(system_browsers.glob("chromium-*"))
    if not chromium_dirs:
        print("✗ 未找到 chromium 目录")
        return False
    
    chromium_dir = chromium_dirs[0]
    print(f"✓ 找到 Chromium: {chromium_dir.name}")
    
    # 复制到项目目录
    target_dir = Path("playwright_browsers")
    target_dir.mkdir(exist_ok=True)
    
    target_chromium = target_dir / chromium_dir.name
    
    if target_chromium.exists():
        print(f"⚠ 目标目录已存在，删除旧版本...")
        shutil.rmtree(target_chromium)
    
    print(f"开始复制...")
    print(f"  源: {chromium_dir}")
    print(f"  目标: {target_chromium}")
    print("-" * 60)
    
    try:
        shutil.copytree(chromium_dir, target_chromium)
        
        # 验证
        chrome_exe = list(target_chromium.rglob("chrome.exe"))
        if chrome_exe:
            print(f"\n✅ 复制成功")
            print(f"✓ 浏览器可执行文件: {chrome_exe[0].relative_to(target_dir)}")
            
            # 统计大小
            total_size = sum(f.stat().st_size for f in target_chromium.rglob("*") if f.is_file())
            size_mb = total_size / 1024 / 1024
            print(f"✓ 浏览器大小: {size_mb:.1f} MB")
            
            return True
        else:
            print("\n✗ 复制后未找到 chrome.exe")
            return False
            
    except Exception as e:
        print(f"\n✗ 复制失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_browsers():
    """验证浏览器文件"""
    print("\n" + "=" * 60)
    print("🔍 验证浏览器文件")
    print("=" * 60)
    
    browsers_dir = Path("playwright_browsers")
    
    if not browsers_dir.exists():
        print(f"✗ 浏览器目录不存在: {browsers_dir}")
        return False
    
    print(f"✓ 浏览器目录存在: {browsers_dir}")
    
    # 查找 chromium
    chromium_dirs = list(browsers_dir.glob("chromium-*"))
    if not chromium_dirs:
        print("✗ 未找到 chromium 目录")
        return False
    
    chromium_dir = chromium_dirs[0]
    print(f"✓ Chromium 目录: {chromium_dir.name}")
    
    # 查找 chrome.exe
    chrome_exe = list(chromium_dir.rglob("chrome.exe"))
    if not chrome_exe:
        print("✗ 未找到 chrome.exe")
        return False
    
    print(f"✓ chrome.exe: {chrome_exe[0].relative_to(browsers_dir)}")
    
    # 统计文件
    all_files = list(chromium_dir.rglob("*"))
    file_count = sum(1 for f in all_files if f.is_file())
    total_size = sum(f.stat().st_size for f in all_files if f.is_file())
    size_mb = total_size / 1024 / 1024
    
    print(f"✓ 文件数: {file_count}")
    print(f"✓ 总大小: {size_mb:.1f} MB")
    
    print("\n✅ 浏览器文件验证通过")
    return True

def main():
    print("=" * 60)
    print("🚀 SourceCraft - 浏览器准备工具")
    print("=" * 60)
    print("\n此工具将准备打包所需的浏览器文件")
    print("确保用户安装后能正常使用内置浏览器功能")
    
    # 检查是否已存在
    if Path("playwright_browsers").exists():
        print("\n⚠ 检测到已存在的浏览器目录")
        
        if verify_browsers():
            print("\n浏览器文件已就绪，无需重新下载")
            response = input("\n是否重新下载？(y/N): ").strip().lower()
            if response != 'y':
                print("\n跳过下载")
                return
        else:
            print("\n浏览器文件不完整，需要重新下载")
    
    # 方式 1: 从系统复制（更快）
    print("\n" + "=" * 60)
    print("选择准备方式:")
    print("  1. 从系统 Playwright 目录复制（推荐，更快）")
    print("  2. 重新下载 Chromium")
    print("=" * 60)
    
    choice = input("\n请选择 (1/2，默认 1): ").strip()
    
    success = False
    
    if choice == '2':
        # 方式 2: 重新下载
        success = download_chromium()
    else:
        # 方式 1: 从系统复制
        success = copy_from_system()
        
        if not success:
            print("\n从系统复制失败，尝试重新下载...")
            success = download_chromium()
    
    # 验证
    if success:
        print("\n" + "=" * 60)
        verify_browsers()
        print("=" * 60)
        print("\n✅ 浏览器准备完成！")
        print("\n下一步: 运行 python build_all.py 进行打包")
    else:
        print("\n" + "=" * 60)
        print("✗ 浏览器准备失败")
        print("=" * 60)
        print("\n请检查:")
        print("  1. 是否已安装 Playwright: pip install playwright")
        print("  2. 网络连接是否正常")
        print("  3. 磁盘空间是否充足（需要约 200MB）")
    
    print("\n按回车键退出...")
    try:
        input()
    except:
        pass

if __name__ == "__main__":
    main()
