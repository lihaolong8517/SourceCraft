"""
SourceCraft - 一键打包脚本
============================
自动完成以下步骤：
0. 自动递增软件版本号（完整打包时）
1. 使用 PyInstaller 打包成可执行文件
2. 清理并验证分发目录（确保不包含本机运行数据）
3. 使用 Inno Setup 生成安装程序

用法:
    python build_all.py                    # 正常模式（默认，支持交互输入发布配置）
    python build_all.py --skip-build       # 跳过 PyInstaller，只生成安装程序
    python build_all.py --skip-installer   # 只打包，不生成安装程序
    python build_all.py --ci               # 无人值守模式，遇错直接失败
"""

import argparse
import base64
import getpass
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from urllib.parse import urlparse


BUILD_NAME = "SourceCraft"
FINAL_NAME = "SourceCraft"
SPEC_FILE = "SourceCraft.spec"
INSTALLER_SCRIPT = "installer.iss"
VERSION_INFO_FILE = "version_info.txt"
SECURE_CONFIG_FILE = "secure_config.py"
RELEASE_CONFIG_FILE = ".build_release_config.json"
REALESRGAN_RELEASE_SETTINGS_FILE = "release_settings.json"

ENV_RELEASE_LICENSE_SALT = "VDL_RELEASE_LICENSE_SALT"
ENV_RELEASE_STRICT_ONLINE = "VDL_RELEASE_STRICT_ONLINE"
ENV_RELEASE_ALLOW_SELF_REGISTER = "VDL_RELEASE_ALLOW_TRIAL_SELF_REGISTER"
ENV_RELEASE_TRIAL_REGISTER_ENDPOINT = "VDL_RELEASE_TRIAL_REGISTER_ENDPOINT"
ENV_RELEASE_TRIAL_REGISTER_SIGNING_KEY = "VDL_RELEASE_TRIAL_REGISTER_SIGNING_KEY"

EXTERNAL_BINARIES = [
    "ffmpeg.exe",
    "ffprobe.exe",
    "yt-dlp.exe",
    "deno.exe",
]

REALESRGAN_VIDEO_DEMO_CANDIDATES = [
    os.path.join("realesrgan_runtime", "ncnn", "pkg_20260406_131158", "onepiece_demo.mp4"),
    os.path.join("realesrgan_runtime", "ncnn", "onepiece_demo.mp4"),
]

PLAYWRIGHT_DRIVER_CRITICAL_FILES = [
    "package/cli.js",
    "node.exe",
    "package/package.json",
]

# 这些目录/文件属于运行时数据，不允许进入分发包
FORBIDDEN_RELEASE_DIRS = [
    "browser_data",
    "playwright_data",
    "cookies",
    "__pycache__",
]

FORBIDDEN_RELEASE_FILES = [
    "browser_debug.log",
    "download_history.json",
    "browser_history.json",
    "browser_bookmarks.json",
]

FORBIDDEN_REALESRGAN_DIRS = [
    # os.path.join("realesrgan_runtime", "weights"),  # MODIFIED: No longer delete weights dir entirely
    os.path.join("realesrgan_runtime", "onnx"),
    os.path.join("realesrgan_runtime", "ncnn", "models"),  # We'll handle this selectively below
    os.path.join("realesrgan_runtime", "ncnn", "pkg_20260406_131158"),
]

FORBIDDEN_REALESRGAN_FILES = [
    os.path.join("realesrgan_runtime", "ncnn", "realesrgan-ncnn-vulkan-20220424-windows.zip"),
]


def _env_flag(name, default=False):
    value = str(os.environ.get(name, "")).strip().lower()
    if not value:
        return bool(default)
    return value in ("1", "true", "yes", "on")


def should_bundle_ai_runtime() -> bool:
    return _env_flag("SOURCECRAFT_BUNDLE_AI_RUNTIME", default=False)


def should_bundle_playwright_browsers() -> bool:
    return _env_flag("SOURCECRAFT_BUNDLE_PLAYWRIGHT_BROWSERS", default=False)


def _load_release_settings():
    settings_path = os.path.join(os.getcwd(), REALESRGAN_RELEASE_SETTINGS_FILE)
    if not os.path.isfile(settings_path):
        return None, f"缺少 {REALESRGAN_RELEASE_SETTINGS_FILE}"
    try:
        with open(settings_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None, f"{REALESRGAN_RELEASE_SETTINGS_FILE} 格式无效，顶层必须为 JSON 对象"
        return data, ""
    except Exception as e:
        return None, f"读取 {REALESRGAN_RELEASE_SETTINGS_FILE} 失败: {e}"


def _split_release_setting_urls(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\r\n;|]+", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            result.extend(_split_release_setting_urls(item))
        return result
    return []


def validate_realesrgan_release_settings():
    return True, ["画质修复功能已移除：跳过 Real-ESRGAN 发布配置检查"]


def pause_if_interactive(ci_mode):
    """仅在交互模式下暂停，CI 下直接返回。"""
    if ci_mode:
        return
    if not sys.stdin or not sys.stdin.isatty():
        return
    try:
        input("\n按回车键退出...")
    except EOFError:
        pass


def _is_interactive_terminal():
    return bool(sys.stdin and sys.stdin.isatty())


def _mask_secret(secret):
    if not secret:
        return "(未设置)"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def _parse_bool_option(value, default):
    if value is None:
        return default
    text = str(value).strip().lower()
    if text == "":
        return default
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    raise RuntimeError(f"无法解析布尔参数: {value}")


def _generate_release_salt():
    """生成 24 字节随机盐值（48 位十六进制字符串）。"""
    return os.urandom(24).hex()


def _load_saved_release_config(current_dir):
    """加载本地保存的发布配置（便于非命令行用户复用）。"""
    path = os.path.join(current_dir, RELEASE_CONFIG_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {
            "salt": str(data.get("salt", "")).strip(),
            "strict_online": _parse_bool_option(data.get("strict_online"), True),
            "allow_trial_self_register": _parse_bool_option(
                data.get("allow_trial_self_register"),
                False,
            ),
            "trial_register_endpoint": str(data.get("trial_register_endpoint", "")).strip(),
            "trial_register_signing_key": str(data.get("trial_register_signing_key", "")).strip(),
            "build_python": str(data.get("build_python", "")).strip(),
        }
    except Exception as exc:
        print(f"\n[WARN] 读取 {RELEASE_CONFIG_FILE} 失败: {exc}")
        return {}


def _save_release_config(current_dir, config):
    """保存发布配置到本地文件，方便下次直接打包。"""
    path = os.path.join(current_dir, RELEASE_CONFIG_FILE)
    payload = {
        "salt": config.get("salt", ""),
        "strict_online": bool(config.get("strict_online", True)),
        "allow_trial_self_register": bool(config.get("allow_trial_self_register", False)),
        "trial_register_endpoint": config.get("trial_register_endpoint", ""),
        "trial_register_signing_key": config.get("trial_register_signing_key", ""),
        "build_python": config.get("build_python", ""),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"   [OK] 已保存发布配置: {path}")


def _prompt_text(label, current_value):
    shown = current_value if current_value else "(未设置)"
    user_input = input(f"   {label} [{shown}]: ").strip()
    return user_input if user_input else current_value


def _prompt_secret(label, current_value):
    if current_value:
        prompt = f"   {label} [{_mask_secret(current_value)}，回车沿用]: "
    else:
        prompt = f"   {label}: "
    try:
        user_input = getpass.getpass(prompt).strip()
    except Exception:
        # 某些终端不支持隐藏输入时回退普通输入
        user_input = input(prompt).strip()
    return user_input if user_input else current_value


def _contains_control_char(value):
    if not value:
        return False
    return any((ord(ch) < 32 or ord(ch) == 127) for ch in value)


def _validate_signing_key(signing_key):
    key = (signing_key or "").strip()
    if not key:
        return
    if _contains_control_char(key):
        raise RuntimeError(
            "试用/激活服务签名密钥包含不可见控制字符，通常是终端粘贴异常导致。\n"
            "请重新输入，或通过 --release-trial-register-signing-key 传入。"
        )
    if len(key) < 32:
        raise RuntimeError(
            "商用安全要求：签名密钥长度过短，至少 32 个字符。"
        )


def _prompt_bool(label, current_value):
    shown = "Y" if current_value else "N"
    user_input = input(f"   {label} [当前 {shown}, 输入 y/n]: ").strip().lower()
    if user_input == "":
        return current_value
    if user_input in ("y", "yes", "1", "true", "on"):
        return True
    if user_input in ("n", "no", "0", "false", "off"):
        return False
    print("   [WARN] 输入无效，沿用当前值")
    return current_value


def _can_use_gui():
    if os.environ.get("BUILD_ALL_NO_GUI", "").strip() in ("1", "true", "yes", "on"):
        return False
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return False
    return True


def _module_available(module_name):
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


def _missing_build_modules():
    missing = []
    for name in ("PyInstaller", "playwright", "cv2", "cryptography"):
        if not _module_available(name):
            missing.append(name)
    return missing


def _python_has_required_build_modules(python_exe):
    candidate = str(python_exe or "").strip()
    if not candidate or not os.path.isfile(candidate):
        return False
    test_cmd = [
        candidate,
        "-c",
        (
            "import importlib.util as u; "
            "mods=['PyInstaller','playwright','cv2','cryptography']; "
            "print('ok' if all(u.find_spec(m) for m in mods) else 'missing')"
        ),
    ]
    try:
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    return result.returncode == 0 and result.stdout.strip() == "ok"


def _python_cuda_runtime_info(python_exe):
    candidate = str(python_exe or "").strip()
    if not candidate or not os.path.isfile(candidate):
        return (False, "python 不存在")

    test_cmd = [
        candidate,
        "-c",
        (
            "import torch; "
            "print('cuda_available=' + str(bool(torch.cuda.is_available()))); "
            "print('cuda_built=' + str(bool(torch.version.cuda))); "
            "print('torch_version=' + str(torch.__version__)); "
            "print('cuda_version=' + str(torch.version.cuda))"
        ),
    ]
    try:
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return (False, f"检测失败: {exc}")

    output = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = (result.stderr or output or "未知错误").strip()
        return (False, detail)

    values = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()

    cuda_available = values.get("cuda_available", "").lower() == "true"
    cuda_built = values.get("cuda_built", "").lower() == "true"
    torch_version = values.get("torch_version", "unknown")
    cuda_version = values.get("cuda_version", "None")
    detail = (
        f"torch={torch_version}, cuda={cuda_version}, "
        f"cuda_built={cuda_built}, cuda_available={cuda_available}"
    )
    return (cuda_built, detail)


def _discover_cuda_build_python(saved):
    candidates = [
        (saved.get("build_python") or "").strip(),
        r"C:\Users\Administrator\miniconda3\envs\sourcecraft_realesrgan_cuda\python.exe",
        r"C:\Users\Administrator\miniconda3\envs\py310aiwatermark\python.exe",
        r"C:\Users\Administrator\miniconda3\envs\py312aiwatermark\python.exe",
        r"C:\Users\Administrator\miniconda3\envs\iopaint_env\python.exe",
        r"C:\Users\Administrator\miniconda3\envs\voxcpm\python.exe",
        os.path.abspath(sys.executable),
    ]
    seen = set()
    for candidate in candidates:
        candidate = os.path.abspath(str(candidate or "").strip())
        if not candidate or candidate in seen or not os.path.isfile(candidate):
            continue
        seen.add(candidate)
        if not _python_has_required_build_modules(candidate):
            continue
        ok, _detail = _python_cuda_runtime_info(candidate)
        if ok:
            return candidate
    return ""


def ensure_build_python_cuda_ready(current_dir, args, saved):
    print("[CHECK] 画质修复功能已移除，跳过 CUDA 运行时检查")
    return
    build_python = os.path.abspath((saved.get("build_python") or sys.executable).strip())
    ok, detail = _python_cuda_runtime_info(build_python)
    if ok:
        print(f"[CHECK] 画质修复 CUDA 运行时正常: {detail}")
        return

    fallback_python = _discover_cuda_build_python(saved)
    if fallback_python and os.path.abspath(fallback_python) != build_python:
        print(f"[CHECK] 自动切换 CUDA 打包环境: {fallback_python}")
        saved["build_python"] = fallback_python
        _save_release_config(current_dir, saved)
        if os.path.abspath(sys.executable) != os.path.abspath(fallback_python):
            exit_code = _relaunch_with_python(fallback_python, sys.argv[1:])
            sys.exit(exit_code)
        ok, detail = _python_cuda_runtime_info(fallback_python)
        if ok:
            print(f"[CHECK] 画质修复 CUDA 运行时正常: {detail}")
            return

    raise RuntimeError(
        "当前打包 Python 的画质修复运行时不是 CUDA 版环境，"
        "会导致用户即使有 NVIDIA 显卡也只能走 CPU。\n"
        f"build_python: {build_python}\n"
        f"检测结果: {detail}\n"
        "请切换到安装了 CUDA 版 PyTorch 的 Python 环境后重新打包。"
    )


def _discover_fallback_build_python(saved):
    candidates = [
        r"C:\Users\Administrator\miniconda3\envs\sourcecraft_realesrgan_cuda\python.exe",
        (saved.get("build_python") or "").strip(),
        r"C:\Users\Administrator\miniconda3\envs\py310aiwatermark\python.exe",
    ]
    current_python = os.path.abspath(sys.executable)
    for candidate in candidates:
        if not candidate:
            continue
        candidate_abs = os.path.abspath(candidate)
        if not os.path.isfile(candidate_abs):
            continue
        if candidate_abs == current_python:
            continue
        if _python_has_required_build_modules(candidate_abs):
            return candidate_abs
    return ""


def _relaunch_with_python(python_exe, args):
    env = os.environ.copy()
    env["BUILD_ALL_RELAUNCHED"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    cmd = [python_exe, "-s", os.path.abspath(__file__)] + list(args)
    return subprocess.call(cmd, env=env)


def _select_python_via_gui(message):
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except Exception as exc:
        raise RuntimeError(f"无法加载 Python 选择窗口: {exc}")

    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo("需要选择 Python", message)
    path = filedialog.askopenfilename(
        title="选择可打包的 python.exe",
        filetypes=[("Python", "python.exe"), ("All files", "*.*")],
    )
    root.destroy()
    return path


def ensure_build_python(current_dir, args, saved):
    if args.skip_build:
        return
    if os.environ.get("BUILD_ALL_RELAUNCHED") == "1":
        return

    missing = _missing_build_modules()
    if not missing:
        return

    fallback_python = _discover_fallback_build_python(saved)
    if fallback_python:
        saved["build_python"] = fallback_python
        _save_release_config(current_dir, saved)
        exit_code = _relaunch_with_python(fallback_python, sys.argv[1:])
        sys.exit(exit_code)

    if args.ci:
        raise RuntimeError(
            "当前 Python 环境缺少打包依赖: "
            + ", ".join(missing)
            + "，且未找到可自动切换的 build_python。"
        )

    if not _can_use_gui():
        raise RuntimeError(
            "当前 Python 环境缺少打包依赖: "
            + ", ".join(missing)
            + "。请在正确的 Python 环境中运行，或设置 build_python。"
        )

    message = (
        "当前 Python 环境缺少打包依赖: "
        + ", ".join(missing)
        + "。\n请选择已安装依赖的 python.exe。"
    )
    selected = _select_python_via_gui(message)
    if not selected:
        raise RuntimeError("未选择 Python，无法继续打包")

    test_cmd = [selected, "-c", "import PyInstaller, playwright; print('ok')"]
    try:
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=15)
    except Exception as exc:
        raise RuntimeError(f"检测 Python 环境失败: {exc}")
    if result.returncode != 0:
        raise RuntimeError(
            "选择的 Python 仍缺少依赖，错误信息:\n" + (result.stderr or result.stdout)
        )

    saved["build_python"] = selected
    _save_release_config(current_dir, saved)
    exit_code = _relaunch_with_python(selected, sys.argv[1:])
    sys.exit(exit_code)


def _gui_collect_release_config(saved, defaults):
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception as exc:
        raise RuntimeError(f"无法加载 GUI 输入窗口: {exc}")

    root = tk.Tk()
    root.title("SourceCraft - 发布配置")
    root.geometry("640x520")
    root.resizable(False, False)

    entries = {}
    vars_bool = {}
    result = {"ok": False}

    def add_label(text, row):
        label = tk.Label(root, text=text, anchor="w")
        label.grid(row=row, column=0, padx=12, pady=6, sticky="w")

    def add_entry(key, row, width=72, mask=False):
        entry = tk.Entry(root, width=width, show="*" if mask else "")
        entry.grid(row=row, column=1, padx=12, pady=6, sticky="w")
        entries[key] = entry
        return entry

    def add_checkbox(key, text, row, default):
        var = tk.BooleanVar(value=default)
        chk = tk.Checkbutton(root, text=text, variable=var)
        chk.grid(row=row, column=1, padx=12, pady=6, sticky="w")
        vars_bool[key] = var
        return chk

    title = tk.Label(
        root,
        text="发布配置（双击打包时填写）",
        font=("Microsoft YaHei", 13, "bold"),
    )
    title.grid(row=0, column=0, columnspan=2, padx=12, pady=12, sticky="w")

    hint = tk.Label(
        root,
        text="未填写的项将沿用已保存配置或默认值",
        fg="#555555",
    )
    hint.grid(row=1, column=0, columnspan=2, padx=12, pady=6, sticky="w")

    add_label("License Salt (48位 hex)", 2)
    salt_entry = add_entry("salt", 2)

    add_label("严格在线验证", 3)
    add_checkbox("strict_online", "启用", 3, defaults.get("strict_online", True))

    add_label("允许客户端自助试用登记", 4)
    add_checkbox(
        "allow_trial_self_register",
        "启用（安全性较低）",
        4,
        defaults.get("allow_trial_self_register", False),
    )

    add_label("试用/激活服务 URL", 5)
    endpoint_entry = add_entry("trial_register_endpoint", 5)

    add_label("试用/激活服务签名密钥", 6)
    signing_entry = add_entry("trial_register_signing_key", 6, mask=True)

    info = tk.Label(
        root,
        text="提示：签名密钥至少 32 位。URL 必须为 https://.../register-trial",
        fg="#555555",
    )
    info.grid(row=7, column=0, columnspan=2, padx=12, pady=6, sticky="w")

    def fill_defaults():
        salt_entry.delete(0, tk.END)
        salt_entry.insert(0, defaults.get("salt", ""))
        endpoint_entry.delete(0, tk.END)
        endpoint_entry.insert(0, defaults.get("trial_register_endpoint", ""))
        signing_entry.delete(0, tk.END)
        signing_entry.insert(0, defaults.get("trial_register_signing_key", ""))

    def on_confirm():
        result["salt"] = salt_entry.get().strip() or defaults.get("salt", "")
        result["strict_online"] = vars_bool["strict_online"].get()
        result["allow_trial_self_register"] = vars_bool["allow_trial_self_register"].get()
        result["trial_register_endpoint"] = (
            endpoint_entry.get().strip() or defaults.get("trial_register_endpoint", "")
        )
        result["trial_register_signing_key"] = (
            signing_entry.get().strip() or defaults.get("trial_register_signing_key", "")
        )
        result["ok"] = True
        root.destroy()

    def on_cancel():
        if messagebox.askyesno("取消", "确定要取消打包吗？"):
            result["ok"] = False
            root.destroy()

    btn_frame = tk.Frame(root)
    btn_frame.grid(row=8, column=0, columnspan=2, pady=18)
    btn_fill = tk.Button(btn_frame, text="载入已保存", width=12, command=fill_defaults)
    btn_fill.pack(side="left", padx=8)
    btn_ok = tk.Button(btn_frame, text="开始打包", width=12, command=on_confirm)
    btn_ok.pack(side="left", padx=8)
    btn_cancel = tk.Button(btn_frame, text="取消", width=12, command=on_cancel)
    btn_cancel.pack(side="left", padx=8)

    fill_defaults()
    root.mainloop()

    if not result.get("ok"):
        raise RuntimeError("用户取消打包")
    return result


def find_inno_setup():
    """查找 Inno Setup 编译器路径"""
    possible_paths = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        r"C:\Program Files\Inno Setup 5\ISCC.exe",
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return path

    try:
        result = subprocess.run(
            ["where", "ISCC.exe"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0]
    except Exception:
        pass

    return None


def compute_sha256(path):
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def _read_version_from_version_info(version_info_path):
    """从 version_info.txt 读取 filevers 四段版本号。"""
    with open(version_info_path, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(
        r"filevers=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)",
        content,
    )
    if not match:
        raise RuntimeError(f"无法解析版本号: {version_info_path}")

    return tuple(int(group) for group in match.groups())


def _write_version_info(version_info_path, version_tuple):
    """更新 version_info.txt 内的 filevers/prodvers 及字符串版本。"""
    major, minor, patch, build = version_tuple
    version_4 = f"{major}.{minor}.{patch}.{build}"

    with open(version_info_path, "r", encoding="utf-8") as f:
        content = f.read()

    updated = content
    updated = re.sub(
        r"filevers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)",
        f"filevers=({major}, {minor}, {patch}, {build})",
        updated,
        count=1,
    )
    updated = re.sub(
        r"prodvers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)",
        f"prodvers=({major}, {minor}, {patch}, {build})",
        updated,
        count=1,
    )
    updated = re.sub(
        r"StringStruct\(u'FileVersion',\s*u'[^']+'\)",
        f"StringStruct(u'FileVersion', u'{version_4}')",
        updated,
        count=1,
    )
    updated = re.sub(
        r"StringStruct\(u'ProductVersion',\s*u'[^']+'\)",
        f"StringStruct(u'ProductVersion', u'{version_4}')",
        updated,
        count=1,
    )

    if updated == content:
        raise RuntimeError(f"更新版本号失败: {version_info_path}")

    with open(version_info_path, "w", encoding="utf-8") as f:
        f.write(updated)


def _write_installer_version(installer_iss_path, version_3):
    """同步 installer.iss 的 MyAppVersion。"""
    with open(installer_iss_path, "r", encoding="utf-8") as f:
        content = f.read()

    updated = re.sub(
        r'(#define\s+MyAppVersion\s+")([^"]+)(")',
        rf'\g<1>{version_3}\g<3>',
        content,
        count=1,
    )

    if updated == content:
        raise RuntimeError(f"更新安装脚本版本失败: {installer_iss_path}")

    with open(installer_iss_path, "w", encoding="utf-8") as f:
        f.write(updated)


def _encode_release_value(value):
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _parse_endpoint_scheme(url):
    try:
        parsed = urlparse((url or "").strip())
        return parsed.scheme.lower(), bool(parsed.netloc)
    except Exception:
        return "", False


def _is_ip_literal_host(url):
    try:
        host = (urlparse((url or "").strip()).hostname or "").strip()
        if not host:
            return False
        ipaddress.ip_address(host)
        return True
    except Exception:
        return False


def _replace_release_config_line(content, key, encoded_value):
    pattern = rf'^{re.escape(key)}\s*=\s*".*?"'
    replacement = f'{key} = "{encoded_value}"'
    updated, count = re.subn(pattern, replacement, content, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"未找到配置项: {key}")
    return updated


def resolve_release_config(current_dir, args):
    """
    解析发布配置来源（命令行参数 > 环境变量 > 本地保存），
    在交互模式下允许用户直接输入并可选择保存。
    """
    saved = _load_saved_release_config(current_dir)

    salt = (
        (args.release_salt or "").strip()
        or (os.environ.get(ENV_RELEASE_LICENSE_SALT) or "").strip()
        or saved.get("salt", "")
    )
    strict_online = _parse_bool_option(
        args.release_strict_online
        if args.release_strict_online is not None
        else os.environ.get(ENV_RELEASE_STRICT_ONLINE),
        saved.get("strict_online", True),
    )
    allow_trial_self_register = _parse_bool_option(
        args.release_allow_self_register
        if args.release_allow_self_register is not None
        else os.environ.get(ENV_RELEASE_ALLOW_SELF_REGISTER),
        saved.get("allow_trial_self_register", False),
    )
    trial_register_endpoint = (
        (args.release_trial_register_endpoint or "").strip()
        or (os.environ.get(ENV_RELEASE_TRIAL_REGISTER_ENDPOINT) or "").strip()
        or saved.get("trial_register_endpoint", "")
    )
    trial_register_signing_key = (
        (args.release_trial_register_signing_key or "").strip()
        or (os.environ.get(ENV_RELEASE_TRIAL_REGISTER_SIGNING_KEY) or "").strip()
        or saved.get("trial_register_signing_key", "")
    )

    interactive = _is_interactive_terminal() and not args.ci
    prefer_gui = (not args.ci) and _can_use_gui() and (not args.cli)
    if prefer_gui:
        defaults = {
            "salt": salt,
            "strict_online": strict_online,
            "allow_trial_self_register": allow_trial_self_register,
            "trial_register_endpoint": trial_register_endpoint,
            "trial_register_signing_key": trial_register_signing_key,
        }
        print("\n[SECURE] 使用 GUI 输入发布配置")
        gui_result = _gui_collect_release_config(saved, defaults)
        salt = gui_result.get("salt", salt)
        strict_online = gui_result.get("strict_online", strict_online)
        allow_trial_self_register = gui_result.get("allow_trial_self_register", allow_trial_self_register)
        trial_register_endpoint = gui_result.get("trial_register_endpoint", trial_register_endpoint)
        trial_register_signing_key = gui_result.get("trial_register_signing_key", trial_register_signing_key)
    elif interactive:
        print("\n[SECURE] 发布配置输入")
        print("   直接回车可沿用已有值")

        if salt:
            salt = _prompt_text("License Salt", salt)
        else:
            salt_input = input("   License Salt [回车自动生成]: ").strip()
            salt = salt_input
            if not salt:
                salt = _generate_release_salt()
                print("   [OK] 已自动生成 License Salt")
        strict_online = _prompt_bool("严格在线验证", strict_online)
        allow_trial_self_register = _prompt_bool(
            "允许客户端自助试用登记(安全性较低)",
            allow_trial_self_register,
        )
        trial_register_endpoint = _prompt_text(
            "试用/激活服务 URL(可选)",
            trial_register_endpoint,
        )
        trial_register_signing_key = _prompt_text(
            "试用/激活服务请求/响应签名密钥(必填, 至少32位)",
            trial_register_signing_key,
        )

    if not trial_register_endpoint:
        raise RuntimeError(
            "未配置试用登记服务 URL，无法完成在线校验配置。\n"
            "请提供 --release-trial-register-endpoint（服务端试用+激活方案）。"
        )

    if trial_register_endpoint:
        scheme, has_netloc = _parse_endpoint_scheme(trial_register_endpoint)
        if scheme not in ("http", "https") or (not has_netloc):
            raise RuntimeError(
                "试用登记服务 URL 格式不正确，必须是 http(s)://host[:port]/register-trial"
            )
        if scheme != "https":
            raise RuntimeError(
                "商用发布要求试用登记服务 URL 必须使用 HTTPS。"
            )
        if not trial_register_signing_key:
            raise RuntimeError(
                "已配置试用登记服务 URL，但缺少响应签名密钥。\n"
                "请提供 --release-trial-register-signing-key。"
            )
        _validate_signing_key(trial_register_signing_key)
        if _is_ip_literal_host(trial_register_endpoint):
            print(
                "   [WARN] 试用/激活服务 URL 使用了 IP 地址。"
                "建议改为域名+受信证书，避免 TLS 主机名兼容问题。"
            )

    if (not allow_trial_self_register) and (not trial_register_endpoint):
        raise RuntimeError(
            "当前已禁用客户端自助试用登记，但未配置试用登记服务 URL。\n"
            "请填写 --release-trial-register-endpoint（方案二），"
            "或将 --release-allow-self-register 设为 true（方案一）。"
        )

    if salt and re.fullmatch(r"[0-9a-fA-F]{48}", salt):
        salt = salt.lower()

    config = {
        "salt": salt,
        "strict_online": strict_online,
        "allow_trial_self_register": allow_trial_self_register,
        "trial_register_endpoint": trial_register_endpoint,
        "trial_register_signing_key": trial_register_signing_key,
    }

    should_save = False
    if args.save_release_config:
        should_save = True
    elif args.no_save_release_config:
        should_save = False
    elif interactive:
        changed = (
            config["salt"] != saved.get("salt", "")
            or config["strict_online"] != saved.get("strict_online", True)
            or config["allow_trial_self_register"] != saved.get("allow_trial_self_register", False)
            or config["trial_register_endpoint"] != saved.get("trial_register_endpoint", "")
            or config["trial_register_signing_key"] != saved.get("trial_register_signing_key", "")
        )
        if changed:
            choice = input(
                f"   是否保存本次配置到 {RELEASE_CONFIG_FILE} 供下次直接打包？(Y/n): "
            ).strip().lower()
            should_save = (choice in ("", "y", "yes"))

    if should_save:
        _save_release_config(current_dir, config)

    return config


def prepare_release_secure_config(current_dir, release_config):
    """
    打包前将发布配置写入 secure_config.py。
    URL/Token/SALT 等会写入 secure_config.py，打包后恢复源码。
    返回 (config_path, original_content) 以便打包后恢复源码。
    """
    config_path = os.path.join(current_dir, SECURE_CONFIG_FILE)
    if not os.path.isfile(config_path):
        raise RuntimeError(f"未找到配置文件: {config_path}")

    salt = (release_config.get("salt") or "").strip()
    strict_online = _parse_bool_option(
        release_config.get("strict_online"),
        True,
    )
    allow_trial_self_register = _parse_bool_option(
        release_config.get("allow_trial_self_register"),
        False,
    )
    trial_register_endpoint = (
        (release_config.get("trial_register_endpoint") or "").strip()
    )

    with open(config_path, "r", encoding="utf-8") as f:
        original = f.read()

    updated = original
    if salt:
        updated = _replace_release_config_line(
            updated,
            "_RELEASE_LICENSE_SALT",
            _encode_release_value(salt),
        )

    updated = _replace_release_config_line(
        updated,
        "_RELEASE_REQUIRE_STRICT_ONLINE",
        _encode_release_value("1" if strict_online else "0"),
    )
    updated = _replace_release_config_line(
        updated,
        "_RELEASE_ALLOW_TRIAL_SELF_REGISTER",
        _encode_release_value("1" if allow_trial_self_register else "0"),
    )
    updated = _replace_release_config_line(
        updated,
        "_RELEASE_TRIAL_REGISTER_ENDPOINT",
        _encode_release_value(trial_register_endpoint) if trial_register_endpoint else "",
    )
    # 注入试用/激活服务 HMAC 签名密钥
    trial_register_signing_key = (
        (release_config.get("trial_register_signing_key") or "").strip()
    )
    updated = _replace_release_config_line(
        updated,
        "_RELEASE_TRIAL_REGISTER_SIGNING_KEY",
        _encode_release_value(trial_register_signing_key) if trial_register_signing_key else "",
    )

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(updated)

    print("\n[SECURE] 已注入发布配置到 secure_config.py")
    print(f"   [OK] SALT: {'使用输入/配置值' if salt else '沿用 secure_config.py 默认值'}")
    print(f"   [OK] 严格在线验证: {'开启' if strict_online else '关闭'}")
    print(
        "   [OK] 客户端自助试用登记: "
        + ("开启(兼容模式)" if allow_trial_self_register else "关闭(推荐)")
    )
    print(
        "   [OK] 试用/激活服务 URL: "
        + (trial_register_endpoint if trial_register_endpoint else "(未配置)")
    )

    return (config_path, original)


def restore_release_secure_config(backup):
    """将 secure_config.py 恢复为打包前内容，避免密钥留在源码。"""
    if not backup:
        return
    config_path, original = backup
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(original)
        print("\n[SECURE] 已恢复 secure_config.py 到打包前状态")
    except Exception as exc:
        print(f"\n[WARN] 恢复 secure_config.py 失败: {exc}")


def bump_project_version(current_dir):
    """
    每次完整打包前自动递增补丁号：
    例如 1.1.0.0 -> 1.1.1.0。
    """
    version_info_path = os.path.join(current_dir, VERSION_INFO_FILE)
    installer_iss_path = os.path.join(current_dir, INSTALLER_SCRIPT)

    if not os.path.isfile(version_info_path):
        raise RuntimeError(f"未找到版本文件: {version_info_path}")
    if not os.path.isfile(installer_iss_path):
        raise RuntimeError(f"未找到安装脚本: {installer_iss_path}")

    old_major, old_minor, old_patch, _old_build = _read_version_from_version_info(
        version_info_path
    )
    new_version_tuple = (old_major, old_minor, old_patch + 1, 0)
    new_major, new_minor, new_patch, new_build = new_version_tuple
    old_version_4 = f"{old_major}.{old_minor}.{old_patch}.0"
    new_version_4 = f"{new_major}.{new_minor}.{new_patch}.{new_build}"
    new_version_3 = f"{new_major}.{new_minor}.{new_patch}"

    _write_version_info(version_info_path, new_version_tuple)
    _write_installer_version(installer_iss_path, new_version_3)

    print("\n[VERSION] 自动递增版本号...")
    print(f"   [OK] {old_version_4} -> {new_version_4}")

    return new_version_tuple


def directory_size_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                total += os.path.getsize(file_path)
            except OSError:
                pass
    return total


def find_in_dist(dist_dir, relative_name):
    """在根目录或 _internal 中查找文件。"""
    candidates = [
        os.path.join(dist_dir, relative_name),
        os.path.join(dist_dir, "_internal", relative_name),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def find_playwright_driver_dir(dist_dir):
    candidates = [
        os.path.join(dist_dir, "playwright", "driver"),
        os.path.join(dist_dir, "_internal", "playwright", "driver"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def find_playwright_browsers_dir(dist_dir):
    candidates = [
        os.path.join(dist_dir, "playwright_browsers"),
        os.path.join(dist_dir, "_internal", "playwright_browsers"),
        os.path.join(
            dist_dir,
            "_internal",
            "playwright",
            "driver",
            "package",
            ".local-browsers",
        ),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def ensure_local_browser_bundle(current_dir):
    """
    确保本地浏览器目录完整。
    分发包要求离线可用，因此这里强制检查 chrome.exe。
    """
    if not should_bundle_playwright_browsers():
        print("   [OK] 已启用瘦身模式：跳过内置浏览器检查，运行时将优先使用系统 Edge/Chrome")
        return
    browsers_dir = os.path.join(current_dir, "playwright_browsers")
    if not os.path.isdir(browsers_dir):
        raise RuntimeError(
            "缺少 playwright_browsers 目录。请先运行 python prepare_browsers.py"
        )

    chrome_found = False
    for root, _dirs, files in os.walk(browsers_dir):
        if "chrome.exe" in files:
            chrome_found = True
            break

    if not chrome_found:
        raise RuntimeError(
            "playwright_browsers 不完整，未找到 chrome.exe。请先运行 python prepare_browsers.py"
        )

    size_mb = directory_size_bytes(browsers_dir) / 1024 / 1024
    print(f"   [OK] 本地浏览器已就绪: {browsers_dir} ({size_mb:.1f} MB)")


def ensure_playwright_driver_ready(ci_mode):
    print("\n[CHECK] 检查 Playwright 驱动程序...")
    try:
        import playwright
    except ImportError as exc:
        raise RuntimeError(f"Playwright 未安装: {exc}. 请先执行: pip install playwright")

    playwright_path = os.path.dirname(playwright.__file__)
    driver_path = os.path.join(playwright_path, "driver")

    if not os.path.isdir(driver_path):
        msg = f"Playwright 驱动目录不存在: {driver_path}"
        if ci_mode:
            raise RuntimeError(msg)
        print(f"   [ERR] {msg}")
        response = input("\n   是否继续打包？(y/N): ").strip().lower()
        if response != "y":
            raise RuntimeError("用户取消打包")
        return

    driver_files = []
    driver_size = 0
    for root, _dirs, files in os.walk(driver_path):
        for file in files:
            file_path = os.path.join(root, file)
            driver_files.append(file_path)
            try:
                driver_size += os.path.getsize(file_path)
            except OSError:
                pass

    size_mb = driver_size / 1024 / 1024
    print("   [OK] Playwright 驱动程序已就绪")
    print(f"     路径: {driver_path}")
    print(f"     文件数: {len(driver_files)}")
    print(f"     大小: {size_mb:.1f} MB")

    missing_files = []
    for rel_path in PLAYWRIGHT_DRIVER_CRITICAL_FILES:
        if not os.path.exists(os.path.join(driver_path, rel_path)):
            missing_files.append(rel_path)
    if missing_files:
        raise RuntimeError(f"Playwright 驱动缺少关键文件: {', '.join(missing_files)}")


def clean_build_dirs():
    for folder in ["build", "dist"]:
        if not os.path.exists(folder):
            continue
        print(f"\n[CLEAN] 清理 {folder} 目录...")
        try:
            shutil.rmtree(folder)
        except PermissionError:
            print("   [WARN] 清理失败，尝试移除只读属性后重试...")
            try:
                subprocess.run(
                    ["attrib", "-R", f"{folder}\\*", "/S", "/D"],
                    capture_output=True,
                    shell=True,
                    check=False,
                )
                shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                pass
        except Exception as exc:
            print(f"   [WARN] 清理目录时出错: {exc}")
            shutil.rmtree(folder, ignore_errors=True)


def safe_move_dir(src, dst):
    src_path = Path(src)
    dst_path = Path(dst)

    if dst_path.exists():
        try:
            shutil.rmtree(dst_path)
        except Exception as exc:
            print(f"   [WARN] 警告: 无法删除旧目录 {dst}: {exc}")

    for _ in range(5):
        try:
            src_path.rename(dst_path)
            print(f"   [OK] 重命名目录: {src_path.name} -> {dst_path.name}")
            return True
        except (PermissionError, OSError):
            time.sleep(0.5)

    try:
        shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
        shutil.rmtree(src_path)
        print(f"   [OK] 复制并替换目录: {src_path.name} -> {dst_path.name}")
        return True
    except Exception as exc:
        print(f"   [ERR] 目录移动失败: {exc}")
        return False


def copy_missing_root_binaries(current_dir, build_dist_dir):
    for binary in EXTERNAL_BINARIES:
        src = os.path.join(current_dir, binary)
        dst = os.path.join(build_dist_dir, binary)
        existing = find_in_dist(build_dist_dir, binary)
        if existing:
            rel = os.path.relpath(existing, build_dist_dir)
            print(f"   [OK] 已包含依赖: {binary} ({rel})")
            continue
        if os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"   [OK] 补充复制: {binary}")


def copy_local_browser_bundle(current_dir, build_dist_dir):
    if not should_bundle_playwright_browsers():
        print("   [OK] 已启用瘦身模式：跳过复制内置浏览器目录")
        return
    src = os.path.join(current_dir, "playwright_browsers")
    dst = os.path.join(build_dist_dir, "playwright_browsers")
    internal_dst = os.path.join(build_dist_dir, "_internal", "playwright_browsers")
    if not os.path.isdir(src):
        raise RuntimeError("未找到 playwright_browsers 目录，无法生成离线可用安装包")

    if os.path.isdir(dst):
        print("   [OK] 已检测到根目录内置浏览器，跳过额外复制")
        return
    if os.path.isdir(internal_dst):
        print("   [OK] 已检测到 _internal 内置浏览器，跳过额外复制")
        return

    if os.path.exists(dst):
        shutil.rmtree(dst)

    print("   [PACK] 正在复制内置浏览器目录 (这可能需要几分钟)...")
    shutil.copytree(src, dst)
    print("   [OK] 复制内置浏览器目录完成")


def sanitize_release_directory(final_dist_dir):
    """
    删除不应进入分发包的本机运行痕迹。
    """
    print("\n[SANITIZE] 清理分发目录中的运行时数据...")
    removed_entries = []

    for rel_dir in FORBIDDEN_RELEASE_DIRS:
        for base in (final_dist_dir, os.path.join(final_dist_dir, "_internal")):
            target = os.path.join(base, rel_dir)
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
                removed_entries.append(target)

    for rel_file in FORBIDDEN_RELEASE_FILES:
        for base in (final_dist_dir, os.path.join(final_dist_dir, "_internal")):
            target = os.path.join(base, rel_file)
            if os.path.isfile(target):
                try:
                    os.remove(target)
                    removed_entries.append(target)
                except OSError:
                    pass

    for rel_dir in FORBIDDEN_REALESRGAN_DIRS:
        for base in (final_dist_dir, os.path.join(final_dist_dir, "_internal")):
            target = os.path.join(base, rel_dir)
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
                removed_entries.append(target)
                
    # Selective cleanup for models to keep the bundle slim but ready-to-use
    for base in (final_dist_dir, os.path.join(final_dist_dir, "_internal")):
        # 1. Weights directory
        weights_dir = os.path.join(base, "realesrgan_runtime", "weights")
        if os.path.isdir(weights_dir):
            for f in os.listdir(weights_dir):
                if f.endswith(".pth") and "animevideov3" not in f.lower():
                    try:
                        os.remove(os.path.join(weights_dir, f))
                        removed_entries.append(os.path.join(weights_dir, f))
                    except OSError:
                        pass
        
        # 2. NCNN models directory
        ncnn_models_dir = os.path.join(base, "realesrgan_runtime", "ncnn", "models")
        if os.path.isdir(ncnn_models_dir):
            for f in os.listdir(ncnn_models_dir):
                if (f.endswith(".bin") or f.endswith(".param")) and "animevideov3" not in f.lower():
                    try:
                        os.remove(os.path.join(ncnn_models_dir, f))
                        removed_entries.append(os.path.join(ncnn_models_dir, f))
                    except OSError:
                        pass

    for rel_file in FORBIDDEN_REALESRGAN_FILES:
        for base in (final_dist_dir, os.path.join(final_dist_dir, "_internal")):
            target = os.path.join(base, rel_file)
            if os.path.isfile(target):
                try:
                    os.remove(target)
                    removed_entries.append(target)
                except OSError:
                    pass

    # 清理 .dist-info/RECORD 文件（泄露 pip 安装路径）
    internal_dir = os.path.join(final_dist_dir, "_internal")
    if os.path.isdir(internal_dir):
        for name in os.listdir(internal_dir):
            if name.endswith(".dist-info"):
                record_file = os.path.join(internal_dir, name, "RECORD")
                if os.path.isfile(record_file):
                    try:
                        os.remove(record_file)
                        removed_entries.append(record_file)
                    except OSError:
                        pass

    if removed_entries:
        for item in removed_entries:
            print(f"   [OK] 已移除: {item}")
    else:
        print("   [OK] 未发现需要移除的运行时数据")


def verify_release_bundle(final_dist_dir):
    """
    对分发目录执行硬校验。
    不通过则中止，避免生成“可安装但不可用”的安装包。
    """
    print("\n[CHECK] 验证打包结果...")

    errors = []
    app_exe = os.path.join(final_dist_dir, f"{FINAL_NAME}.exe")
    if os.path.isfile(app_exe):
        size_mb = os.path.getsize(app_exe) / 1024 / 1024
        print(f"   [OK] 主程序: {app_exe} ({size_mb:.1f} MB)")
    else:
        errors.append(f"主程序不存在: {app_exe}")

    for binary in EXTERNAL_BINARIES:
        found = find_in_dist(final_dist_dir, binary)
        if found:
            size_mb = os.path.getsize(found) / 1024 / 1024
            print(f"   [OK] 依赖文件: {binary} ({size_mb:.1f} MB)")
        else:
            errors.append(f"缺少依赖文件: {binary}")

    driver_dir = find_playwright_driver_dir(final_dist_dir)
    if not driver_dir:
        errors.append("未找到 Playwright 驱动目录")
    else:
        print(f"   [OK] Playwright 驱动目录: {driver_dir}")
        for rel_file in PLAYWRIGHT_DRIVER_CRITICAL_FILES:
            driver_file = os.path.join(driver_dir, rel_file)
            if not os.path.exists(driver_file):
                errors.append(f"Playwright 驱动缺少关键文件: {rel_file}")

    browsers_dir = find_playwright_browsers_dir(final_dist_dir)
    if should_bundle_playwright_browsers():
        if not browsers_dir:
            errors.append("未找到浏览器目录 (playwright_browsers / .local-browsers)")
        else:
            print(f"   [OK] 浏览器目录: {browsers_dir}")
            chrome_candidates = list(Path(browsers_dir).rglob("chrome.exe"))
            if not chrome_candidates:
                errors.append("浏览器目录中未找到 chrome.exe")
            else:
                print(f"   [OK] 找到 chrome.exe: {chrome_candidates[0]}")
    else:
        print("   [OK] 已启用瘦身模式：发布包不内置 Chromium，运行时依赖系统 Edge/Chrome")

    realesrgan_release_ok, realesrgan_release_messages = validate_realesrgan_release_settings()
    if realesrgan_release_ok:
        for item in realesrgan_release_messages:
            print(f"   [OK] {item}")
    else:
        errors.extend(realesrgan_release_messages)

    for rel_dir in FORBIDDEN_RELEASE_DIRS:
        for base in (final_dist_dir, os.path.join(final_dist_dir, "_internal")):
            target = os.path.join(base, rel_dir)
            if os.path.isdir(target):
                errors.append(f"分发包仍包含运行时目录: {target}")

    for rel_file in FORBIDDEN_RELEASE_FILES:
        for base in (final_dist_dir, os.path.join(final_dist_dir, "_internal")):
            target = os.path.join(base, rel_file)
            if os.path.isfile(target):
                errors.append(f"分发包仍包含运行时文件: {target}")

    for rel_dir in FORBIDDEN_REALESRGAN_DIRS:
        for base in (final_dist_dir, os.path.join(final_dist_dir, "_internal")):
            target = os.path.join(base, rel_dir)
            if os.path.isdir(target):
                errors.append(f"分发包仍包含不应内置的画质修复模型目录: {target}")

    for rel_file in FORBIDDEN_REALESRGAN_FILES:
        for base in (final_dist_dir, os.path.join(final_dist_dir, "_internal")):
            target = os.path.join(base, rel_file)
            if os.path.isfile(target):
                errors.append(f"分发包仍包含不应内置的画质修复模型文件: {target}")

    if errors:
        print("\n[ERR] 分发校验失败:")
        for idx, err in enumerate(errors, 1):
            print(f"   {idx}. {err}")
        return False

    print("   [OK] 分发校验通过")
    return True


def write_release_manifest(final_dist_dir):
    """
    生成发布清单，便于回溯和用户问题排查。
    """
    print("\n[MANIFEST] 生成发布清单...")
    manifest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "app_name": FINAL_NAME,
        "dist_dir": ".",
        "total_size_mb": round(directory_size_bytes(final_dist_dir) / 1024 / 1024, 2),
        "required_files": {},
    }

    required_targets = [f"{FINAL_NAME}.exe"] + EXTERNAL_BINARIES
    for name in required_targets:
        found = find_in_dist(final_dist_dir, name)
        if not found:
            continue
        manifest["required_files"][name] = {
            "path": os.path.relpath(found, final_dist_dir),
            "size_bytes": os.path.getsize(found),
            "sha256": compute_sha256(found),
        }

    manifest_path = os.path.join(final_dist_dir, "release_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"   [OK] 已生成: {manifest_path}")


def _find_realesrgan_video_demo(base_dir):
    for rel_path in REALESRGAN_VIDEO_DEMO_CANDIDATES:
        candidate = find_in_dist(base_dir, rel_path)
        if candidate and os.path.isfile(candidate):
            return candidate
    for root, _, files in os.walk(base_dir):
        if "onepiece_demo.mp4" in files:
            return os.path.join(root, "onepiece_demo.mp4")
    return ""


def run_source_realesrgan_video_verify(current_dir):
    print("\n[VERIFY] 验证源码态画质修复视频链路...")
    script_path = os.path.join(current_dir, "verify_realesrgan_pipeline.py")
    if not os.path.isfile(script_path):
        raise RuntimeError(f"未找到验证脚本: {script_path}")
    result = subprocess.run(
        [sys.executable, script_path],
        cwd=current_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "源码态画质修复视频验证失败。\n"
            + (result.stdout or "")
            + "\n"
            + (result.stderr or "")
        )
    print("   [OK] 源码态视频链路验证通过")


def _run_packaged_realesrgan_job(exe_path, payload_path, *, current_dir, timeout_seconds, error_prefix):
    run_env = os.environ.copy()
    run_env["SOURCECRAFT_REALESRGAN_DATA_DIR"] = os.path.join(current_dir, "realesrgan_runtime")
    result = subprocess.run(
        [exe_path, "--realesrgan-job", payload_path],
        cwd=current_dir,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(
            error_prefix
            + f"\n退出码={result.returncode}\n"
            + (result.stdout or "")
            + "\n"
            + (result.stderr or "")
        )
    return result


def run_packaged_realesrgan_smoke_test(current_dir, dist_dir):
    """直接执行打包产物的画质修复子模式，验证用户侧可用。"""
    print("\n[SMOKE] 验证打包后的画质修复功能...")
    exe_path = os.path.join(dist_dir, f"{FINAL_NAME}.exe")
    if not os.path.isfile(exe_path):
        raise RuntimeError(f"未找到打包产物: {exe_path}")

    sample_input = os.path.join(current_dir, "app_icon.png")
    if not os.path.isfile(sample_input):
        raise RuntimeError(f"缺少冒烟测试输入文件: {sample_input}")

    smoke_dir = os.path.join(current_dir, "tmp_packaged_realesrgan_smoke")
    if os.path.isdir(smoke_dir):
        shutil.rmtree(smoke_dir, ignore_errors=True)
    os.makedirs(smoke_dir, exist_ok=True)

    output_dir = os.path.join(smoke_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    payload_path = os.path.join(smoke_dir, "job.json")
    payload = {
        "file_list": [sample_input],
        "output_dir": output_dir,
        "options": {
            "preset": "general",
            "model_name": "realesr-general-x4v3",
            "outscale": 2.0,
            "denoise_strength": 0.5,
            "tile": 512,
            "tile_pad": 24,
            "pre_pad": 10,
            "alpha_upsampler": "realesrgan",
            "device": "auto",
            "fp32": True,
            "face_enhance": False,
            "ext": "auto",
            "fps": None,
            "extract_frame_first": True,
            "suffix": "_smoke",
            "overwrite": True,
        },
    }
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    _run_packaged_realesrgan_job(
        exe_path,
        payload_path,
        current_dir=current_dir,
        timeout_seconds=600,
        error_prefix="打包后的画质修复图片冒烟测试失败",
    )
    expected_output = os.path.join(output_dir, "app_icon_smoke.png")

    if not os.path.isfile(expected_output):
        raise RuntimeError(
            "打包后的画质修复冒烟测试未生成输出文件: "
            + expected_output
        )

    output_size = os.path.getsize(expected_output)
    print(f"   [OK] 冒烟测试通过: {expected_output} ({output_size} bytes)")
    shutil.rmtree(smoke_dir, ignore_errors=True)


def run_packaged_realesrgan_video_smoke_test(current_dir, dist_dir):
    print("\n[SMOKE] 验证打包后的画质修复视频链路...")
    exe_path = os.path.join(dist_dir, f"{FINAL_NAME}.exe")
    if not os.path.isfile(exe_path):
        raise RuntimeError(f"未找到打包产物: {exe_path}")

    sample_input = _find_realesrgan_video_demo(current_dir)
    if not sample_input:
        raise RuntimeError("未找到视频验证样本 onepiece_demo.mp4")

    smoke_dir = os.path.join(current_dir, "tmp_packaged_realesrgan_video_smoke")
    if os.path.isdir(smoke_dir):
        shutil.rmtree(smoke_dir, ignore_errors=True)
    os.makedirs(smoke_dir, exist_ok=True)

    output_dir = os.path.join(smoke_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    payload_path = os.path.join(smoke_dir, "job.json")
    payload = {
        "file_list": [sample_input],
        "output_dir": output_dir,
        "options": {
            "preset": "anime_video",
            "model_name": "realesr-animevideov3",
            "outscale": 2.0,
            "denoise_strength": 1.0,
            "tile": 0,
            "tile_pad": 10,
            "pre_pad": 0,
            "alpha_upsampler": "realesrgan",
            "device": "auto",
            "fp32": False,
            "face_enhance": False,
            "ext": "auto",
            "fps": None,
            "extract_frame_first": True,
            "suffix": "_video_smoke",
            "overwrite": True,
            "video_backend": "auto",
        },
    }
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    _run_packaged_realesrgan_job(
        exe_path,
        payload_path,
        current_dir=current_dir,
        timeout_seconds=2400,
        error_prefix="打包后的画质修复视频冒烟测试失败",
    )
    expected_output = os.path.join(output_dir, "onepiece_demo_video_smoke.mp4")
    if not os.path.isfile(expected_output):
        raise RuntimeError("打包后的视频冒烟测试未生成输出文件: " + expected_output)

    output_size = os.path.getsize(expected_output)
    print(f"   [OK] 视频冒烟测试通过: {expected_output} ({output_size} bytes)")
    shutil.rmtree(smoke_dir, ignore_errors=True)


def build_executable(current_dir, ci_mode=False):
    """使用 PyInstaller 打包可执行文件"""
    print("\n" + "=" * 60)
    print("[PACK] 第 1 步: PyInstaller 打包")
    print("=" * 60)

    ensure_playwright_driver_ready(ci_mode=ci_mode)
    ensure_local_browser_bundle(current_dir)
    clean_build_dirs()

    spec_file = os.path.join(current_dir, SPEC_FILE)
    if not os.path.exists(spec_file):
        raise RuntimeError(f"未找到 {SPEC_FILE}，请确认打包配置存在")

    print(f"\n[SPEC] 使用 .spec 文件: {SPEC_FILE}")
    print("   .spec 文件已配置:")
    print("   [OK] Playwright 驱动程序打包")
    if should_bundle_playwright_browsers():
        print("   [OK] 浏览器文件打包")
    else:
        print("   [OK] 瘦身模式：不打包内置浏览器，运行时改用系统 Edge/Chrome")
    print("   [OK] 画质修复功能已移除：不打包 Real-ESRGAN/NCNN/模型文件")

    cmd = [sys.executable, "-s", "-m", "PyInstaller", "--noconfirm", spec_file]
    build_env = os.environ.copy()
    build_env["PYTHONNOUSERSITE"] = "1"
    # 某些机器的用户 site-packages 目录存在权限异常，PyInstaller 扫描依赖时会直接失败。
    # 将 user base 重定向到当前工作区下的可访问目录，避免探测 Roaming\Python\...\site-packages。
    pyinstaller_user_base = os.path.join(current_dir, ".pyinstaller_user_base")
    os.makedirs(pyinstaller_user_base, exist_ok=True)
    build_env["PYTHONUSERBASE"] = pyinstaller_user_base
    print("\n[RUN] 执行 PyInstaller...")
    print("-" * 60)
    subprocess.check_call(cmd, env=build_env)

    build_dist_dir = os.path.join(current_dir, "dist", BUILD_NAME)
    final_dist_dir = os.path.join(current_dir, "dist", FINAL_NAME)

    if not os.path.exists(build_dist_dir):
        raise RuntimeError(f"PyInstaller 输出目录不存在: {build_dist_dir}")

    print("\n[POST] 打包后处理...")

    old_exe = os.path.join(build_dist_dir, f"{BUILD_NAME}.exe")
    new_exe = os.path.join(build_dist_dir, f"{FINAL_NAME}.exe")
    if os.path.exists(old_exe) and old_exe != new_exe:
        os.rename(old_exe, new_exe)
        print(f"   [OK] 重命名: {BUILD_NAME}.exe -> {FINAL_NAME}.exe")
    elif not os.path.exists(new_exe):
        raise RuntimeError(f"未找到可执行文件: {old_exe}")

    copy_missing_root_binaries(current_dir=current_dir, build_dist_dir=build_dist_dir)
    copy_local_browser_bundle(current_dir=current_dir, build_dist_dir=build_dist_dir)

    if build_dist_dir != final_dist_dir:
        if not safe_move_dir(build_dist_dir, final_dist_dir):
            raise RuntimeError("输出目录重命名失败，无法继续")
    else:
        print(f"   [INFO] 构建名称与最终名称相同 ({BUILD_NAME})，跳过目录重命名")

    sanitize_release_directory(final_dist_dir)
    if not verify_release_bundle(final_dist_dir):
        raise RuntimeError("分发校验失败，请根据上方错误修复后重试")

    write_release_manifest(final_dist_dir)
    print("\n[OK] PyInstaller 打包完成！")
    return True


def build_installer(current_dir, iscc_path):
    """使用 Inno Setup 生成安装程序"""
    print("\n" + "=" * 60)
    print("[PACK] 第 2 步: Inno Setup 生成安装程序")
    print("=" * 60)

    iss_file = os.path.join(current_dir, INSTALLER_SCRIPT)
    if not os.path.exists(iss_file):
        raise RuntimeError(f"未找到 {INSTALLER_SCRIPT}")

    installer_dir = os.path.join(current_dir, "installer")
    os.makedirs(installer_dir, exist_ok=True)

    print(f"\n[RUN] 使用 Inno Setup: {iscc_path}")
    print(f"   ISS 文件: {iss_file}")
    print("-" * 60)

    cmd = [iscc_path, iss_file]
    subprocess.check_call(cmd)

    print("\n[OUTPUT] 查找生成的安装程序...")
    installers = []
    for file in os.listdir(installer_dir):
        if file.lower().endswith(".exe"):
            installer_path = os.path.join(installer_dir, file)
            installers.append(installer_path)

    if not installers:
        raise RuntimeError("未找到生成的安装程序，请检查 Inno Setup 输出日志")

    installers.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for installer_path in installers:
        size_mb = os.path.getsize(installer_path) / (1024 * 1024)
        print(f"   [OK] {os.path.basename(installer_path)} ({size_mb:.1f} MB)")

    print("\n[OK] 安装程序生成完成！")
    return True


def build_single_file_installer(current_dir):
    print("\n" + "=" * 60)
    print("[PACK] 第 3 步: 封装单文件安装器")
    print("=" * 60)

    script_path = os.path.join(current_dir, "build_single_exe_installer.py")
    if not os.path.isfile(script_path):
        raise RuntimeError(f"未找到单文件安装器脚本: {script_path}")

    python_exe = sys.executable
    build_python = os.environ.get("SOURCECRAFT_BUILD_PYTHON")
    if build_python and os.path.isfile(build_python):
        python_exe = build_python

    cmd = [python_exe, script_path]
    subprocess.check_call(cmd, cwd=current_dir)

    installer_dir = os.path.join(current_dir, "installer")
    single_exes = sorted(
        (
            os.path.join(installer_dir, name)
            for name in os.listdir(installer_dir)
            if name.lower().endswith("_single.exe")
        ),
        key=os.path.getmtime,
        reverse=True,
    )
    if not single_exes:
        raise RuntimeError("未找到生成的单文件安装器")

    single_exe = single_exes[0]
    base_stem = os.path.splitext(os.path.basename(single_exe))[0].removesuffix("_single")
    final_exe = os.path.join(installer_dir, f"{base_stem}.exe")

    for name in os.listdir(installer_dir):
        lower_name = name.lower()
        if lower_name == os.path.basename(single_exe).lower():
            continue
        if lower_name == f"{base_stem.lower()}.exe" or lower_name.startswith(f"{base_stem.lower()}-") and lower_name.endswith(".bin"):
            try:
                os.remove(os.path.join(installer_dir, name))
            except OSError:
                pass

    if os.path.abspath(single_exe) != os.path.abspath(final_exe):
        if os.path.exists(final_exe):
            os.remove(final_exe)
        os.replace(single_exe, final_exe)
        single_exe = final_exe

    size_mb = os.path.getsize(single_exe) / (1024 * 1024)
    print(f"\n[OK] 单文件安装器生成完成: {os.path.basename(single_exe)} ({size_mb:.1f} MB)")
    return True


def main():
    parser = argparse.ArgumentParser(description="SourceCraft build script")
    parser.add_argument("--skip-build", action="store_true", help="跳过 PyInstaller 打包")
    parser.add_argument("--skip-installer", action="store_true", help="跳过生成安装程序")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="强制使用命令行交互（不弹 GUI）",
    )
    parser.add_argument("--release-salt", help="发布 License Salt（可选，交互模式下可自动生成）")
    parser.add_argument(
        "--release-strict-online",
        choices=["true", "false"],
        help="发布策略：是否强制在线授权校验（true/false）",
    )
    parser.add_argument(
        "--release-allow-self-register",
        choices=["true", "false"],
        help="发布策略：是否允许客户端直写试用登记（true/false，不推荐 true）",
    )
    parser.add_argument(
        "--release-trial-register-endpoint",
        help="发布策略：试用/激活服务 URL（推荐服务端登记）",
    )
    parser.add_argument(
        "--release-trial-register-signing-key",
        help="发布策略：试用/激活服务请求/响应签名密钥（必填）",
    )
    save_group = parser.add_mutually_exclusive_group()
    save_group.add_argument(
        "--save-release-config",
        action="store_true",
        help=f"将本次发布配置保存到 {RELEASE_CONFIG_FILE}",
    )
    save_group.add_argument(
        "--no-save-release-config",
        action="store_true",
        help="本次不保存发布配置",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="无人值守模式：遇错直接退出，不等待人工输入（需通过参数/环境变量提供 Token）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("[START] SourceCraft - 一键打包脚本")
    print("=" * 60)

    start_time = time.time()
    current_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(current_dir)
    saved_config = _load_saved_release_config(current_dir)
    ensure_build_python(current_dir=current_dir, args=args, saved=saved_config)
    ensure_build_python_cuda_ready(current_dir=current_dir, args=args, saved=saved_config)

    if not args.skip_installer:
        print("\n[CHECK] 检查 Inno Setup...")
        iscc_path = find_inno_setup()
        if iscc_path:
            print(f"   [OK] 找到 Inno Setup: {iscc_path}")
        else:
            print("   [ERR] 未找到 Inno Setup!")
            print("   请先安装 Inno Setup: https://jrsoftware.org/isdl.php")
            if not args.skip_build:
                print("   (将只执行 PyInstaller 打包)")
                args.skip_installer = True
                iscc_path = None
            else:
                raise RuntimeError("既跳过 build 又缺少 Inno Setup，无法继续")
    else:
        iscc_path = None

    secure_config_backup = None
    try:
        if not args.skip_build:
            release_config = resolve_release_config(current_dir=current_dir, args=args)
            secure_config_backup = prepare_release_secure_config(
                current_dir=current_dir,
                release_config=release_config,
            )
            bump_project_version(current_dir)
            build_executable(current_dir=current_dir, ci_mode=args.ci)
        else:
            print("\n[SKIP] 跳过 PyInstaller 打包")
            final_dist_dir = os.path.join(current_dir, "dist", FINAL_NAME)
            if not os.path.isdir(final_dist_dir):
                raise RuntimeError(
                    f"跳过 build 时必须存在已打好的目录: {final_dist_dir}"
                )
            sanitize_release_directory(final_dist_dir)
            if not verify_release_bundle(final_dist_dir):
                raise RuntimeError("现有分发目录校验失败")
            write_release_manifest(final_dist_dir)

        if not args.skip_installer and iscc_path:
            dist_dir = os.path.join(current_dir, "dist", FINAL_NAME)
            if not os.path.isdir(dist_dir):
                raise RuntimeError(f"打包目录不存在: {dist_dir}")
            build_installer(current_dir=current_dir, iscc_path=iscc_path)
            build_single_file_installer(current_dir=current_dir)
        elif args.skip_installer:
            print("\n[SKIP] 跳过生成安装程序")

        elapsed = time.time() - start_time
        print("\n" + "=" * 60)
        print(f"[DONE] 全部完成！总耗时: {elapsed:.1f} 秒")
        print("=" * 60)
        print("\n[OUTPUT] 输出位置:")
        print("   可执行程序: dist\\SourceCraft\\")
        if not args.skip_installer and iscc_path:
            print("   安装程序:   installer\\")
    except subprocess.CalledProcessError as exc:
        print(f"\n[ERR] 命令执行失败: {exc}")
        pause_if_interactive(args.ci)
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERR] 发生错误: {exc}")
        traceback.print_exc()
        pause_if_interactive(args.ci)
        sys.exit(1)
    finally:
        restore_release_secure_config(secure_config_backup)

    pause_if_interactive(args.ci)


if __name__ == "__main__":
    main()
