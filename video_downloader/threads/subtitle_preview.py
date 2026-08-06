import json
import subprocess
import auth_utils
from PyQt5.QtCore import QUrl
"""字幕预览线程"""
import os
import sys
import re
import glob
from PyQt5.QtCore import QThread, pyqtSignal

try:
    from ..constants import RUNTIME_APP_DIR
except ImportError:
    from ..constants import RUNTIME_APP_DIR

class SubtitlePreviewThread(QThread):
    subtitle_info_signal = pyqtSignal(dict)  # 用于传递字幕信息
    error_signal = pyqtSignal(str)  # 用于传递错误信息
    progress_signal = pyqtSignal(str)  # 用于传递进度信息
    
    def __init__(self, url):
        super().__init__()
        self.url = url
        
    def _is_internal_cookie_file(self, path):
        """检查是否为内置浏览器提取的Cookie文件
        
        【关键修复】使用标记文件而非解析cookie文件内容
        因为yt-dlp使用cookie后会重写文件头
        """
        try:
            marker_path = path.replace('_cookies.txt', '_cookies.internal')
            if os.path.exists(marker_path):
                return True
            # 兼容旧版：允许通过文件头标记识别
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    head = f.read(4096)
                if 'Source: internal_browser' in head or 'internal_browser' in head:
                    return True
            return False
        except Exception:
            return False

    def _find_latest_internal_cookie(self):
        """回退查找最近的内置浏览器 Cookie 文件"""
        try:
            cookie_dir = getattr(auth_utils, 'COOKIE_DIR', '')
            if not cookie_dir or not os.path.isdir(cookie_dir):
                return None
            candidates = []
            for name in os.listdir(cookie_dir):
                if not name.endswith('_cookies.txt'):
                    continue
                full_path = os.path.join(cookie_dir, name)
                if os.path.isfile(full_path) and self._is_internal_cookie_file(full_path):
                    candidates.append(full_path)
            if not candidates:
                return None
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return candidates[0]
        except Exception:
            return None

    def _build_internal_cookie_candidates(self, host):
        """根据主机名构建 Cookie 文件候选列表。"""
        candidates = []
        seen = set()

        def _add_host(h):
            h = str(h or "").strip().lower()
            if not h:
                return
            if h in seen:
                return
            seen.add(h)
            try:
                candidates.append(auth_utils.get_cookie_file(h))
            except Exception:
                pass

        try:
            host = str(host or "").strip().lower()
            if not host:
                return candidates

            _add_host(host)

            if host.startswith("www."):
                _add_host(host[4:])

            parts = host.split('.')
            if len(parts) >= 2:
                base = '.'.join(parts[-2:])
                if base != host:
                    _add_host(base)

            # YouTube 常见账号域名
            if ('youtu.be' in host) or ('youtube.' in host):
                for alias in [
                    'www.youtube.com',
                    'youtube.com',
                    'm.youtube.com',
                    'google.com',
                    'accounts.google.com',
                    'youtube-nocookie.com',
                ]:
                    _add_host(alias)

            # X/Twitter 常见域名
            if ('x.com' in host) or ('twitter.com' in host):
                for alias in [
                    'x.com',
                    'www.x.com',
                    'twitter.com',
                    'www.twitter.com',
                    'mobile.twitter.com',
                    'm.twitter.com',
                ]:
                    _add_host(alias)
        except Exception:
            pass

        return candidates

    def _resolve_internal_cookie_for_url(self, url):
        """根据 URL 解析可用的内置浏览器 Cookie，带最近文件回退。"""
        host = ""
        try:
            host = QUrl(url).host().lower().strip()
        except Exception:
            host = ""

        candidates = self._build_internal_cookie_candidates(host)
        for cf in candidates:
            try:
                if os.path.exists(cf) and self._is_internal_cookie_file(cf):
                    return cf, "direct", host, candidates
            except Exception:
                continue

        fallback = self._find_latest_internal_cookie()
        if fallback and os.path.exists(fallback):
            return fallback, "fallback", host, candidates

        return None, "none", host, candidates

    def _attach_internal_cookie(self, cmd, url=None, direct_msg=None, fallback_msg=None, missing_msg=None):
        """向 yt-dlp 命令附加内置浏览器 Cookie。"""
        target_url = url or self.url
        cookie_file, mode, host, candidates = self._resolve_internal_cookie_for_url(target_url)

        if cookie_file:
            cmd.extend(["--cookies", cookie_file])
            self.internal_cookie_attached = True
            if mode == "direct" and direct_msg:
                self.progress_signal.emit(f"{direct_msg}{os.path.basename(cookie_file)}")
            elif mode == "fallback" and fallback_msg:
                self.progress_signal.emit(f"{fallback_msg}{os.path.basename(cookie_file)}")
            return cookie_file, mode, host, candidates

        if missing_msg:
            self.progress_signal.emit(missing_msg)
        return None, mode, host, candidates

    def run(self):
        try:
            cmd = ["yt-dlp.exe", "--dump-json", "--list-subs", "--no-playlist", self.url]
            self._attach_internal_cookie(
                cmd,
                url=self.url,
            )
            try:
                if hasattr(self, "_apply_base_yt_dlp_safety"):
                    self._apply_base_yt_dlp_safety(cmd)
            except Exception:
                pass

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            stdout, stderr = process.communicate()

            if process.returncode != 0:
                error_text = (stderr or stdout or "").strip()
                raise RuntimeError(error_text or t_format("yt-dlp.exe 执行失败，退出码: {}", process.returncode))

            lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
            json_line = ""
            for line in reversed(lines):
                if line.startswith("{") and line.endswith("}"):
                    json_line = line
                    break
            if not json_line:
                raise RuntimeError("未获取到有效的字幕信息数据")

            info = json.loads(json_line)
            subtitle_info = {
                'subtitles': info.get('subtitles', {}),
                'automatic_captions': info.get('automatic_captions', {})
            }
            self.subtitle_info_signal.emit(subtitle_info)
        except Exception as e:
            self.error_signal.emit(str(e))

