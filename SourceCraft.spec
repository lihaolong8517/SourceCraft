# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
import os
import sys


def _env_flag(name, default=False):
    value = str(os.environ.get(name, "")).strip().lower()
    if not value:
        return bool(default)
    return value in ("1", "true", "yes", "on")

# 基础资源文件
datas = [
    ('app_icon.ico', '.'), 
    ('app_icon.png', '.'), 
    ('app_icon.svg', '.'), 
    ('shop_qr.png', '.'),
]

# locales：仅收集 .json 翻译文件，排除开发期文件（如 _pending_zh_keys.txt）
if os.path.isdir('locales'):
    for _fname in sorted(os.listdir('locales')):
        if _fname.endswith('.json'):
            datas.append((os.path.join('locales', _fname), 'locales'))

if os.path.exists('release_settings.json'):
    datas.append(('release_settings.json', '.'))

# 外部可执行文件
binaries = [
    ('ffmpeg.exe', '.'), 
    ('ffprobe.exe', '.'), 
    ('yt-dlp.exe', '.'), 
    ('deno.exe', '.')
]

# 隐藏导入
hiddenimports = [
    'playwright',
    'playwright.driver',
    'playwright.sync_api',
    'playwright.async_api',
    'PyQt5',
    'PyQt5.QtWidgets',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtNetwork',
    'PIL',
    'PIL.Image',
    'requests',
    'urllib3',
    'certifi',
    'cryptography',
    'license_manager',
    'license_dialogs',
    'batch_download_dialog',
]

try:
    tmp_ret = collect_all('cryptography')
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]
    crypto_hiddenimports = collect_submodules('cryptography')
    hiddenimports += crypto_hiddenimports
    print(f'[打包] [OK] 已完整收集 cryptography ({len(crypto_hiddenimports)} 个子模块)')
except Exception as e:
    print(f'[打包] [ERR] 无法收集 cryptography: {e}')
    raise


def _normalize_dest(dest):
    return str(dest or "").replace("\\", "/").lower().strip("/")


def _normalize_src(src):
    return str(src or "").replace("\\", "/").lower()


def _should_skip_runtime_entry(src, dest):
    src_norm = _normalize_src(src)
    dest_norm = _normalize_dest(dest)

    if src_norm.endswith(".lib"):
        return True
    if src_norm.endswith(".pdb"):
        return True

    # torch 的 include/share/testing 等开发资源对运行时无用，但体积很大
    if dest_norm.startswith("torch/include") or "/torch/include/" in src_norm:
        return True
    if dest_norm.startswith("torch/share") or "/torch/share/" in src_norm:
        return True
    if dest_norm.startswith("torch/testing") or "/torch/testing/" in src_norm:
        return True
    # torch 分布式训练模块（单机推理不需要）
    if dest_norm.startswith("torch/distributed") or "/torch/distributed/" in src_norm:
        return True
    # torch FX pass 测试
    if "torch/fx/passes/tests" in dest_norm or "/torch/fx/passes/tests/" in src_norm:
        return True
    # torch _numpy 测试
    if "torch/_numpy/testing" in dest_norm or "/torch/_numpy/testing/" in src_norm:
        return True
    # .dist-info 中的 RECORD 文件（泄露 pip 路径信息）
    if dest_norm.endswith("/record") and ".dist-info" in dest_norm:
        return True

    return False


def _filter_runtime_entries(entries):
    filtered = []
    for entry in entries:
        if not isinstance(entry, tuple) or len(entry) < 2:
            filtered.append(entry)
            continue
        src, dest = entry[0], entry[1]
        if _should_skip_runtime_entry(src, dest):
            continue
        filtered.append(entry)
    return filtered

# ========== 已移除画质修复运行时 ==========
optional_runtime_modules = ['cv2', 'ffmpeg']
print('[打包] [OK] 画质修复功能已移除：不收集 torch/basicsr/realesrgan/gfpgan/facexlib')

for module_name in optional_runtime_modules:
    try:
        tmp_ret = collect_all(module_name)
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]
        print(f'[打包] [OK] 已收集可选运行时模块: {module_name}')
    except Exception as e:
        print(f'[打包] [WARN] 跳过可选运行时模块 {module_name}: {e}')

runtime_source_packages = []
for package_name in runtime_source_packages:
    try:
        package_sources = collect_data_files(package_name, include_py_files=True)
        if package_sources:
            datas += package_sources
            print(f'[打包] [OK] 已包含运行时源码目录: {package_name} ({len(package_sources)} 项)')
        package_hiddenimports = collect_submodules(package_name)
        if package_hiddenimports:
            hiddenimports += package_hiddenimports
            print(f'[打包] [OK] 已补充隐藏导入: {package_name} ({len(package_hiddenimports)} 项)')
    except Exception as e:
        print(f'[打包] [WARN] 无法补充运行时源码目录 {package_name}: {e}')

datas = _filter_runtime_entries(datas)
binaries = _filter_runtime_entries(binaries)

# ========== 【关键修复1】收集 Playwright 包 ==========
print('[打包] 收集 Playwright 包...')
datas += collect_data_files('playwright')
hiddenimports += collect_submodules('playwright')
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# ========== 【关键修复2】显式添加 Playwright 驱动程序 ==========
# 这是最关键的部分！PyInstaller 的 hook 不会自动收集驱动程序
try:
    import playwright
    playwright_path = os.path.dirname(playwright.__file__)
    driver_path = os.path.join(playwright_path, 'driver')
    
    if os.path.exists(driver_path):
        # 递归添加整个 driver 目录
        datas += [(driver_path, 'playwright/driver')]
        print(f'[打包] [OK] 已包含 Playwright 驱动目录: {driver_path}')
        
        # 统计驱动文件数量
        driver_file_count = 0
        driver_size = 0
        for root, dirs, files in os.walk(driver_path):
            for file in files:
                driver_file_count += 1
                try:
                    driver_size += os.path.getsize(os.path.join(root, file))
                except:
                    pass
        
        print(f'[打包]   驱动文件数: {driver_file_count}')
        print(f'[打包]   驱动大小: {driver_size / 1024 / 1024:.1f} MB')
    else:
        print(f'[打包] [WARN] Playwright 驱动目录不存在: {driver_path}')
        print(f'[打包]   用户环境将无法启动浏览器！')
except Exception as e:
    print(f'[打包] [ERR] 无法收集 Playwright 驱动: {e}')
    import traceback
    traceback.print_exc()

# ========== 【关键修复3】添加本地浏览器文件（可选，作为后备） ==========
bundle_playwright_browsers = _env_flag("SOURCECRAFT_BUNDLE_PLAYWRIGHT_BROWSERS", default=False)
if bundle_playwright_browsers and os.path.exists('playwright_browsers'):
    datas += [('playwright_browsers', 'playwright_browsers')]
    print('[打包] [OK] 已包含本地浏览器目录: playwright_browsers')
    
    # 统计浏览器文件
    browser_size = 0
    for root, dirs, files in os.walk('playwright_browsers'):
        for file in files:
            try:
                browser_size += os.path.getsize(os.path.join(root, file))
            except:
                pass
    print(f'[打包]   浏览器大小: {browser_size / 1024 / 1024:.1f} MB')
elif bundle_playwright_browsers:
    print('[打包] [WARN] 未找到 playwright_browsers 目录')
    print('[打包]   用户需要系统已安装 Edge/Chrome 才能使用浏览器功能')
else:
    print('[打包] [OK] 当前为瘦身打包模式：不内置 playwright_browsers，运行时优先使用系统 Edge/Chrome')

# ========== 【关键修复4】业务模块编译进 PYZ（不再明文复制）==========
# 历史方案：这些 .py 当 datas 复制到 _internal/ → 明文裸露，商业代码可被直接读取
# 当前方案：加入 hiddenimports 编译进 PYZ 归档。
# 兼容性：__init__.py 的 _load_playwright_browser_module() 与子进程 launcher 均有
#         import playwright_browser 兜底，文件路径找不到时自动走 PYZ 加载，逻辑不变。
required_py_files = [
    'playwright_fix',
    'playwright_browser',
    'auth_utils',
    'format_dialog',
    'image_processor',
]

for module_name in required_py_files:
    py_file = f'{module_name}.py'
    if os.path.exists(py_file):
        if module_name not in hiddenimports:
            hiddenimports.append(module_name)
        print(f'[打包] [OK] 已编译进 PYZ: {py_file}')
    else:
        print(f'[打包] [WARN] 未找到: {py_file}')

print('[打包] [OK] 跳过 Real-ESRGAN 运行时文件')

print('[打包] Playwright 配置完成')


a = Analysis(
    ['video_downloader/__main__.py'],
    pathex=[os.path.abspath('.')],  # 项目根：i18n.py / locales 在根目录
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['win32ui'],  # PyWin32 的 Pythonwin IDE 组件（win32ui.pyd+mfc140u.dll，6.5MB），运行时不需要
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SourceCraft',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    icon=['app_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SourceCraft',
)
