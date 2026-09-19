import json
import re
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INSTALLER_DIR = ROOT / "installer"
WEBSITE_VERSION_JSON = ROOT / "website" / "version.json"
RELEASE_SETTINGS_JSON = ROOT / "release_settings.json"

# 安装包**不再由自建服务器/OSS 托管**：每次下载数百 MB 都会走自己的带宽/流量
# （按量计费 + 挤占官网带宽），海外版已改 GitHub Release、国内版走网盘。
# 因此下载地址必须由发布者显式提供，且禁止指向自建域名/OSS。
_FORBIDDEN_DOWNLOAD_HOSTS = ("hundunxiazaiqi.cn/downloads", "aliyuncs.com")


def get_release_settings() -> dict:
    if RELEASE_SETTINGS_JSON.exists():
        try:
            return json.loads(RELEASE_SETTINGS_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def get_download_url(settings: dict) -> str:
    """取出（并校验）本次发布的下载地址 —— 缺省不再回退到自建服务器。"""
    url = str(settings.get("download_url_override") or "").strip()
    if not url:
        raise SystemExit(
            "release_settings.json 缺少 download_url_override。\n"
            "安装包不再由自建服务器托管：请填写网盘分享链接（国内版）或 GitHub Release "
            "资源地址（海外版，见海外版仓库 tools/make_version_intl.py --url）。"
        )
    for bad in _FORBIDDEN_DOWNLOAD_HOSTS:
        if bad in url:
            raise SystemExit(
                f"download_url_override 指向自建服务器/OSS（命中 {bad}）——\n"
                "会消耗你自己的带宽/流量，已拒绝写入 version.json。"
            )
    return url


def format_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "未知"

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if size < 1024 or candidate == units[-1]:
            break
        size /= 1024.0

    if unit in {"GB", "TB"}:
        return f"约 {size:.2f} {unit}"
    if unit == "MB":
        return f"约 {size:.1f} {unit}"
    if unit == "KB":
        return f"约 {size:.0f} {unit}"
    return f"{int(size)} {unit}"


def find_latest_installer() -> Path:
    candidates = sorted(
        INSTALLER_DIR.glob("SourceCraft_Installer_v*.exe"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("installer 目录下未找到 SourceCraft_Installer_v*.exe")
    return candidates[0]


def extract_version(filename: str) -> str:
    match = re.search(r"SourceCraft_Installer_v(\d+\.\d+\.\d+)\.exe$", filename, re.IGNORECASE)
    if not match:
        raise ValueError(f"无法从文件名解析版本号: {filename}")
    return match.group(1)


def main():
    installer = find_latest_installer()
    version = extract_version(installer.name)
    size_bytes = installer.stat().st_size
    
    settings = get_release_settings()
    download_url = get_download_url(settings)
    
    payload = {
        "version": version,
        "download_url": download_url,
        "file_size": format_size(size_bytes),
        "file_size_bytes": size_bytes,
        "published_at": str(date.today()),
        "notes": [
            f"发布 SourceCraft v{version}",
            "官网首页将自动读取最新版本号与下载链接",
            "安装包大小已按最新安装包文件自动更新",
        ],
    }

    WEBSITE_VERSION_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"已更新: {WEBSITE_VERSION_JSON}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
