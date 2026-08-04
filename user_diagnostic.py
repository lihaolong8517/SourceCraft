#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户环境诊断脚本
让用户运行此脚本，收集诊断信息
"""

import os
import sys
import subprocess
import json
from pathlib import Path
from datetime import datetime

def get_system_info():
    """获取系统信息"""
    info = {
        "操作系统": sys.platform,
        "Python版本": sys.version,
        "Python路径": sys.executable,
        "是否打包环境": getattr(sys, 'frozen', False),
        "工作目录": os.getcwd(),
    }
    
    if getattr(sys, 'frozen', False):
        info["_MEIPASS"] = getattr(sys, '_MEIPASS', 'N/A')
        info["可执行文件路径"] = sys.executable
        info["可执行文件目录"] = os.path.dirname(sys.executable)
    
    return info

def check_playwright_module():
    """检查 Playwright 模块"""
    result = {"状态": "未知", "详情": {}}
    
    try:
        import playwright
        result["状态"] = "已安装"
        result["详情"]["包路径"] = os.path.dirname(playwright.__file__)
        
        # 检查驱动目录
        driver_dir = Path(playwright.__file__).parent / "driver"
        result["详情"]["驱动目录存在"] = driver_dir.exists()
        
        if driver_dir.exists():
            driver_files = list(driver_dir.rglob("*"))
            result["详情"]["驱动文件数"] = len([f for f in driver_files if f.is_file()])
        
        # 尝试导入 API
        try:
            from playwright.async_api import async_playwright
            result["详情"]["async_api可导入"] = True
        except ImportError as e:
            result["详情"]["async_api可导入"] = False
            result["详情"]["async_api错误"] = str(e)
            
    except ImportError as e:
        result["状态"] = "未安装"
        result["详情"]["错误"] = str(e)
    
    return result

def check_browsers():
    """检查浏览器"""
    result = {}
    
    # 检查系统浏览器
    if sys.platform == 'win32':
        # Edge
        edge_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for path in edge_paths:
            if os.path.exists(path):
                result["Edge"] = path
                break
        
        # Chrome
        chrome_paths = [
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for path in chrome_paths:
            if os.path.exists(path):
                result["Chrome"] = path
                break
    
    # 检查 Playwright 浏览器
    home = Path.home()
    ms_playwright = home / "AppData" / "Local" / "ms-playwright"
    if ms_playwright.exists():
        result["Playwright系统浏览器目录"] = str(ms_playwright)
        browsers = [d.name for d in ms_playwright.iterdir() if d.is_dir()]
        result["Playwright系统浏览器"] = browsers
    
    # 检查打包的浏览器
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        local_browsers = Path(exe_dir) / "playwright_browsers"
        if local_browsers.exists():
            result["打包浏览器目录"] = str(local_browsers)
            browsers = [d.name for d in local_browsers.iterdir() if d.is_dir()]
            result["打包浏览器"] = browsers
    
    return result

def check_environment_variables():
    """检查环境变量"""
    env_vars = {}
    
    keys = [
        "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD",
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE",
        "PATH",
    ]
    
    for key in keys:
        value = os.environ.get(key)
        if value:
            if key == "PATH":
                # PATH 太长，只显示前500字符
                env_vars[key] = value[:500] + "..." if len(value) > 500 else value
            else:
                env_vars[key] = value
        else:
            env_vars[key] = "<未设置>"
    
    return env_vars

def test_playwright_launch():
    """测试 Playwright 启动"""
    result = {"状态": "未测试", "详情": {}}
    
    try:
        import playwright
        from playwright.sync_api import sync_playwright
        
        result["详情"]["导入成功"] = True
        
        # 尝试启动（超时5秒）
        try:
            with sync_playwright() as p:
                result["详情"]["playwright上下文创建"] = True
                
                # 尝试启动 Edge
                try:
                    browser = p.chromium.launch(channel="msedge", headless=True, timeout=5000)
                    browser.close()
                    result["详情"]["Edge启动"] = "成功"
                except Exception as e:
                    result["详情"]["Edge启动"] = f"失败: {str(e)[:100]}"
                
                # 尝试启动 Chrome
                try:
                    browser = p.chromium.launch(channel="chrome", headless=True, timeout=5000)
                    browser.close()
                    result["详情"]["Chrome启动"] = "成功"
                except Exception as e:
                    result["详情"]["Chrome启动"] = f"失败: {str(e)[:100]}"
                
                result["状态"] = "测试完成"
        except Exception as e:
            result["状态"] = "测试失败"
            result["详情"]["错误"] = str(e)[:200]
            
    except ImportError as e:
        result["状态"] = "导入失败"
        result["详情"]["错误"] = str(e)
    
    return result

def check_log_file():
    """检查日志文件"""
    result = {}
    
    # 检查 APPDATA 日志
    appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
    log_dir = Path(appdata) / 'SourceCraft'
    log_file = log_dir / 'browser_debug.log'
    
    if log_file.exists():
        result["日志文件路径"] = str(log_file)
        result["日志文件大小"] = f"{log_file.stat().st_size / 1024:.1f} KB"
        
        # 读取最后50行
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                result["最后50行"] = ''.join(lines[-50:])
        except Exception as e:
            result["读取日志错误"] = str(e)
    else:
        result["日志文件"] = "不存在"
    
    return result

def main():
    print("=" * 70)
    print("  SourceCraft - 用户环境诊断")
    print("=" * 70)
    print("\n正在收集诊断信息，请稍候...\n")
    
    # 收集所有信息
    diagnostic = {
        "诊断时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "系统信息": get_system_info(),
        "Playwright模块": check_playwright_module(),
        "浏览器检测": check_browsers(),
        "环境变量": check_environment_variables(),
        "Playwright启动测试": test_playwright_launch(),
        "日志文件": check_log_file(),
    }
    
    # 保存到文件
    output_file = "diagnostic_report.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(diagnostic, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 诊断信息已保存到: {output_file}")
    print("\n" + "=" * 70)
    print("  诊断摘要")
    print("=" * 70)
    
    # 显示关键信息
    print(f"\n系统: {diagnostic['系统信息']['操作系统']}")
    print(f"Python: {diagnostic['系统信息']['Python版本'].split()[0]}")
    print(f"打包环境: {'是' if diagnostic['系统信息']['是否打包环境'] else '否'}")
    
    print(f"\nPlaywright模块: {diagnostic['Playwright模块']['状态']}")
    if diagnostic['Playwright模块']['状态'] == '已安装':
        print(f"  驱动目录: {'存在' if diagnostic['Playwright模块']['详情'].get('驱动目录存在') else '不存在'}")
        print(f"  async_api: {'可导入' if diagnostic['Playwright模块']['详情'].get('async_api可导入') else '不可导入'}")
    
    print(f"\n浏览器检测:")
    browsers = diagnostic['浏览器检测']
    if 'Edge' in browsers:
        print(f"  ✓ Edge: {browsers['Edge']}")
    else:
        print(f"  ✗ Edge: 未找到")
    
    if 'Chrome' in browsers:
        print(f"  ✓ Chrome: {browsers['Chrome']}")
    else:
        print(f"  ✗ Chrome: 未找到")
    
    print(f"\nPlaywright启动测试: {diagnostic['Playwright启动测试']['状态']}")
    if 'Edge启动' in diagnostic['Playwright启动测试']['详情']:
        print(f"  Edge: {diagnostic['Playwright启动测试']['详情']['Edge启动']}")
    if 'Chrome启动' in diagnostic['Playwright启动测试']['详情']:
        print(f"  Chrome: {diagnostic['Playwright启动测试']['详情']['Chrome启动']}")
    
    print("\n" + "=" * 70)
    print(f"\n请将 {output_file} 文件发送给开发者进行分析")
    print("\n按回车键退出...")
    try:
        input()
    except:
        pass

if __name__ == "__main__":
    main()
