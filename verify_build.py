#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
验证打包后的浏览器查找功能
"""
import os
import sys
import tempfile

print("=" * 60)
print("SourceCraft - 打包结果验证")
print("=" * 60)

# 模拟打包环境的 _MEIPASS
base_dir = os.path.dirname(os.path.abspath(__file__))
dist_dir = os.path.join(base_dir, 'dist', 'SourceCraft')

if not os.path.exists(dist_dir):
    print(f"[ERR] 错误: 打包输出目录不存在: {dist_dir}")
    sys.exit(1)

print(f"\n打包输出目录: {dist_dir}")
print()

# 1. 检查 playwright_browsers
print("1) 检查内置浏览器目录...")
browser_candidates = [
    os.path.join(dist_dir, 'playwright_browsers'),
    os.path.join(dist_dir, '_internal', 'playwright_browsers'),
    os.path.join(dist_dir, '_internal', 'playwright', 'driver', 'package', '.local-browsers'),
]

pb_path = None
for candidate in browser_candidates:
    if os.path.exists(candidate):
        pb_path = candidate
        break

if pb_path:
    print(f"   [OK] 浏览器目录存在: {pb_path}")
    browser_dirs = [d for d in os.listdir(pb_path) if os.path.isdir(os.path.join(pb_path, d)) and not d.startswith('.')]
    if browser_dirs:
        print(f"   [OK] 找到浏览器: {', '.join(browser_dirs)}")
    else:
        print(f"   [WARN] 浏览器目录为空")
else:
    print(f"   [ERR] 浏览器目录不存在")
    for candidate in browser_candidates:
        print(f"      预期位置: {candidate}")

print()

# 2. 检查 Playwright 驱动
pw_driver_path = os.path.join(dist_dir, '_internal', 'playwright', 'driver')
print("2) 检查 Playwright 驱动...")
if os.path.exists(pw_driver_path):
    print(f"   [OK] Playwright 驱动目录存在")
    node_path = os.path.join(pw_driver_path, 'node.exe')
    if os.path.exists(node_path):
        print(f"   [OK] node.exe 存在: {node_path}")
    else:
        print(f"   [WARN] node.exe 不存在")
else:
    print(f"   [ERR] Playwright 驱动目录不存在")

print()

# 3. 检查关键 Python 文件
print("3) 检查关键模块文件...")
required_files = [
    ('playwright_fix.py', '_internal'),
    ('playwright_browser.py', '_internal'),
    ('auth_utils.py', '_internal'),
    ('format_dialog.py', '_internal'),
    ('image_processor.py', '_internal'),
]

for filename, subdir in required_files:
    full_path = os.path.join(dist_dir, subdir, filename)
    if os.path.exists(full_path):
        print(f"   [OK] {filename}")
    else:
        print(f"   [ERR] {filename} 缺失")

print()

# 4. 检查可执行文件
exe_path = os.path.join(dist_dir, 'SourceCraft.exe')
print("4) 检查主程序...")
if os.path.exists(exe_path):
    print(f"   [OK] 主程序存在: SourceCraft.exe")
    size_mb = os.path.getsize(exe_path) / (1024 * 1024)
    print(f"   [OK] 大小: {size_mb:.1f} MB")
else:
    print(f"   [ERR] 主程序缺失")

print()

# 5. 检查安装程序
print("5) 检查安装程序...")
installer_dir = os.path.join(base_dir, 'installer')
if os.path.exists(installer_dir):
    installers = [f for f in os.listdir(installer_dir) if f.endswith('.exe')]
    if installers:
        for installer in installers:
            installer_path = os.path.join(installer_dir, installer)
            size_mb = os.path.getsize(installer_path) / (1024 * 1024)
            print(f"   [OK] {installer} ({size_mb:.1f} MB)")
    else:
        print(f"   [WARN] 安装程序目录存在，但没有找到 .exe 文件")
else:
    print(f"   [WARN] 安装程序目录不存在")

print()
print("=" * 60)
print("验证完成！")
print("=" * 60)
print("\n现在可以将安装程序分发给用户测试了！")
