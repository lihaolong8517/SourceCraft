import json
import re
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INSTALLER_DIR = ROOT / "installer"
WEBSITE_VERSION_JSON = ROOT / "website" / "version.json"
RELEASE_SETTINGS_JSON = ROOT / "release_settings.json"

# 默认下载基准 URL (ECS直链)
DEFAULT_DOWNLOAD_BASE_URL = "https://hundunxiazaiqi.cn/downloads"

def get_release_settings() -> dict:
    if RELEASE_SETTINGS_JSON.exists():
        try:
            return json.loads(RELEASE_SETTINGS_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def get_download_base_url() -> str:
    settings = get_release_settings()
    if "download_base_url" in settings:
        return settings["download_base_url"].rstrip("/")
    return DEFAULT_DOWNLOAD_BASE_URL


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
    download_base_url = settings.get("download_base_url", DEFAULT_DOWNLOAD_BASE_URL).rstrip("/")
    
    # Check if we should override the download URL (e.g. using the 123网盘 backup URL as main)
    download_url = settings.get("download_url_override")
    if not download_url:
        download_url = f"{download_base_url}/{installer.name}"
        
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

    # 如果启用了下载链接覆盖，自动将最新的主直链作为 backup 存入 json 以便备份/隐藏
    if settings.get("download_url_override"):
        payload["download_url_oss_backup"] = f"{download_base_url}/{installer.name}"

    WEBSITE_VERSION_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"已更新: {WEBSITE_VERSION_JSON}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
