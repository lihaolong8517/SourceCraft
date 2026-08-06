import re
from ..utils_update import DEFAULT_APP_UPDATE_METADATA_URL, DEFAULT_APP_WEBSITE_URL, _open_url_with_fallback, get_app_version, compare_version_text
"""应用更新检查线程"""
import json
import urllib.request
import urllib.error
from PyQt5.QtCore import QThread, pyqtSignal


class AppUpdateCheckThread(QThread):
    """检查网站上的软件版本。"""
    finished_signal = pyqtSignal(bool, str, dict)  # success, message, payload

    def __init__(self, metadata_url):
        super().__init__()
        self.metadata_url = (metadata_url or "").strip() or DEFAULT_APP_UPDATE_METADATA_URL

    def run(self):
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            payload = None

            try:
                with _open_url_with_fallback(self.metadata_url, timeout=15, headers=headers) as response:
                    text = response.read().decode("utf-8", errors="replace").strip()
                if text:
                    payload = json.loads(text)
            except Exception:
                payload = self._parse_payload_from_website(headers)

            latest_version = str(payload.get("version") or "").strip()
            if not latest_version:
                self.finished_signal.emit(False, "网站版本信息缺少 version 字段", {})
                return

            current_version = get_app_version()
            payload["current_version"] = current_version
            payload["metadata_url"] = self.metadata_url

            if compare_version_text(current_version, latest_version) < 0:
                self.finished_signal.emit(True, "update_available", payload)
            else:
                self.finished_signal.emit(True, "current", payload)
        except Exception as e:
            self.finished_signal.emit(False, self._friendly_error_message(e), {})

    def _parse_payload_from_website(self, headers):
        with _open_url_with_fallback(DEFAULT_APP_WEBSITE_URL, timeout=15, headers=headers) as response:
            html = response.read().decode("utf-8", errors="replace")

        version_match = re.search(r'v(\d+\.\d+\.\d+)\s*[·.]?\s*最新版本', html, re.IGNORECASE)
        download_match = re.search(r'href="(https://hundunxiazaiqi\.cn/downloads/[^"]+SourceCraft[^"]*\.exe)"', html, re.IGNORECASE)

        payload = {
            "version": version_match.group(1) if version_match else "",
            "download_url": download_match.group(1) if download_match else DEFAULT_APP_WEBSITE_URL,
            "metadata_url": self.metadata_url,
            "website_url": DEFAULT_APP_WEBSITE_URL,
        }
        if not payload["version"]:
            raise ValueError("官网首页中未找到版本号")
        return payload

    def _friendly_error_message(self, error):
        text = str(error or "").strip()
        lower_text = text.lower()

        if "10013" in lower_text or "访问套接字" in text:
            return "无法连接更新服务器：当前网络环境禁止建立连接，请检查防火墙、杀毒软件或代理设置。"
        if "timed out" in lower_text or "timeout" in lower_text:
            return "连接更新服务器超时，请稍后重试或检查网络/代理设置。"
        if "name or service not known" in lower_text or "getaddrinfo" in lower_text:
            return "无法解析官网地址，请检查 DNS、网络连接或代理设置。"
        if "ssl" in lower_text or "certificate" in lower_text:
            return "官网连接的证书校验失败，请检查系统时间、证书环境或网络代理。"

        return t_format("获取网站版本失败: {}", text) if text else "获取网站版本失败"


