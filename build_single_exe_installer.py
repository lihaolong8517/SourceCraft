#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
将 Inno Setup 生成的 exe + bin 分卷再封装成单个自解压 exe。

实现方式：
1. 生成一个很小的 Python 引导器。
2. 用 PyInstaller --onefile 将引导器和现有安装器/分卷一起打进一个 exe。
3. 运行单文件 exe 时，PyInstaller 会先解包到临时目录，再启动内层安装器 exe。

输出:
  installer/SourceCraft_Installer_vX.Y.Z_single.exe
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INSTALLER_DIR = ROOT / "installer"
WORK_DIR = INSTALLER_DIR / "_single_exe_build"
APP_ICON = ROOT / "app_icon.ico"
BUILD_ENV_PYTHON = Path(
    os.environ.get(
        "SOURCECRAFT_BUILD_PYTHON",
        r"C:\Users\Administrator\miniconda3\envs\sourcecraft_realesrgan_cuda\python.exe",
    )
)


def find_base_installer() -> tuple[Path, list[Path]]:
    exe_files = sorted(
        (
            p
            for p in INSTALLER_DIR.glob("SourceCraft_Installer_v*.exe")
            if not p.stem.endswith("_single")
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not exe_files:
        raise FileNotFoundError("installer 目录下未找到 SourceCraft_Installer_v*.exe")
    base_exe = exe_files[0]
    stem = base_exe.stem
    bins = sorted(INSTALLER_DIR.glob(f"{stem}-*.bin"))
    if not bins:
        raise FileNotFoundError(f"未找到与 {base_exe.name} 配套的 .bin 分卷")
    return base_exe, bins


def resolve_pyinstaller_python() -> Path:
    candidates = [Path(sys.executable), BUILD_ENV_PYTHON]
    seen = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            subprocess.check_call(
                [str(candidate), "-m", "PyInstaller", "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return candidate
        except Exception:
            continue
    raise RuntimeError("未找到可用的 PyInstaller Python 运行时")


def write_launcher_script(base_exe: Path, bins: list[Path]) -> Path:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    launcher_path = WORK_DIR / "single_installer_launcher.py"
    payload_names = [base_exe.name, *[p.name for p in bins]]
    payload_lines = ",\n    ".join(repr(name) for name in payload_names)
    launcher_code = f"""# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PAYLOAD_FILES = [
    {payload_lines}
]
BASE_EXE = {base_exe.name!r}


def main() -> int:
    if getattr(sys, "frozen", False):
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base_dir = Path(__file__).resolve().parent

    missing = [name for name in PAYLOAD_FILES if not (base_dir / name).exists()]
    if missing:
        raise FileNotFoundError("缺少安装包内容: " + ", ".join(missing))

    installer = base_dir / BASE_EXE
    cmd = [str(installer), *sys.argv[1:]]
    return subprocess.call(cmd, cwd=str(base_dir))


if __name__ == "__main__":
    raise SystemExit(main())
"""
    launcher_path.write_text(launcher_code, encoding="utf-8")
    return launcher_path


def cleanup_previous_build(target_name: Path) -> None:
    for path in [WORK_DIR / "build", WORK_DIR / "dist", WORK_DIR / "__pycache__"]:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    spec_file = WORK_DIR / f"{target_name.stem}.spec"
    if spec_file.exists():
        spec_file.unlink()
    if target_name.exists():
        target_name.unlink()


def cleanup_work_dir() -> None:
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR, ignore_errors=True)


def build_single_exe(base_exe: Path, bins: list[Path]) -> Path:
    pyinstaller_python = resolve_pyinstaller_python()
    target_name = INSTALLER_DIR / f"{base_exe.stem}_single.exe"
    cleanup_previous_build(target_name)
    launcher = write_launcher_script(base_exe, bins)

    cmd = [
        str(pyinstaller_python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        target_name.stem,
        "--distpath",
        str(WORK_DIR / "dist"),
        "--workpath",
        str(WORK_DIR / "build"),
        "--specpath",
        str(WORK_DIR),
        str(launcher),
    ]
    if APP_ICON.is_file():
        cmd.extend(["--icon", str(APP_ICON)])
    for payload in [base_exe, *bins]:
        cmd.extend(["--add-data", f"{payload}{os.pathsep}."])

    print(f"[SINGLE] 使用 PyInstaller: {pyinstaller_python}")
    print(f"[SINGLE] 基础安装器: {base_exe.name}")
    print(f"[SINGLE] 分卷数量: {len(bins)}")
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    user_base = WORK_DIR / "_pyinstaller_userbase"
    user_base.mkdir(parents=True, exist_ok=True)
    env["PYTHONUSERBASE"] = str(user_base)
    env["APPDATA"] = str(user_base)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    subprocess.check_call(cmd, cwd=str(ROOT), env=env)

    built_exe = WORK_DIR / "dist" / f"{target_name.stem}.exe"
    if not built_exe.is_file():
        raise RuntimeError(f"未生成单文件安装器: {built_exe}")
    shutil.copy2(built_exe, target_name)
    return target_name


def main() -> int:
    base_exe, bins = find_base_installer()
    try:
        target_name = build_single_exe(base_exe, bins)
        size_mb = target_name.stat().st_size / (1024 * 1024)
        print(f"[SINGLE] 输出文件: {target_name} ({size_mb:.1f} MB)")
    finally:
        cleanup_work_dir()
    return 0


if __name__ == "__main__":
    sys.exit(main())
