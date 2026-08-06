"""下载线程 - 基于 yt-dlp 的视频下载工作线程"""
import threading
from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import QApplication
from ..utils import (cleanup_download_title, choose_better_download_title, is_generated_download_title, sanitize_output_title_component, decode_subprocess_bytes, _dedupe_preserve_order, _is_x_platform_url, build_subtitle_lang_pattern, FFMPEG_CMD)
from .. import _load_playwright_browser_module
_playwright_browser_module = _load_playwright_browser_module()
import sys
import os
import re
import json
import time
import glob
import shutil
import tempfile
import subprocess
import traceback
import signal
import urllib.request
import urllib.error
import socket
from datetime import datetime
from PyQt5.QtCore import QThread, pyqtSignal

# Import sibling modules
from ..constants import RUNTIME_APP_DIR
from .. import utils
import auth_utils
from download_sections_utils import (
    split_time_range_tokens, parse_time_token_to_seconds, validate_download_section
)
from special_scene_utils import build_wait_for_video_arg, fragment_unavailable_flag
from i18n import t, t_format


class DownloadThread(QThread):
    progress_signal = pyqtSignal(str)
    progress_percent_signal = pyqtSignal(float)  # 用于传递下载进度百分比
    finished_signal = pyqtSignal(bool, str)
    info_signal = pyqtSignal(dict)  # 用于传递视频信息
    formats_signal = pyqtSignal(list)  # 用于传递视频格式列表
    cancel_signal = pyqtSignal()  # 用于传递取消下载成功的信号
    failed_videos_signal = pyqtSignal(list)  # 用于传递失败视频列表
    
    def __init__(self, url, output_path, format_option, additional_options, extract_info=False, list_formats=False, extract_playlist=False, playlist_enabled=False, main_app=None, preferred_title="", media_capture_download=False, media_capture_format_id=""):
        super().__init__()
        self.url = url
        self.output_path = output_path
        self.format_option = format_option
        self.additional_options = additional_options
        self.extract_info = extract_info  # 是否只提取信息而不下载
        self.list_formats = list_formats  # 是否只列出可用格式
        self.extract_playlist = extract_playlist  # 是否专门提取播放列表信息
        self.playlist_enabled = playlist_enabled  # 用户是否启用了播放列表功能
        self.main_app = main_app  # 主应用程序引用，用于访问UI控件
        self.preferred_title = choose_better_download_title(preferred_title)
        self.media_capture_download = bool(media_capture_download)
        self.media_capture_format_id = str(media_capture_format_id or "").strip()
        self.video_info = None
        
        # 【调试】打印接收到的additional_options
        print(f"[调试] DownloadThread.__init__ 接收到的additional_options: {additional_options}")
        self.process = None
        self._cancelled = False  # 取消标志，用于控制下载线程的停止
        self.progress_sent = False  # 跟踪是否已发送过进度信号
        self.age_restriction_retry_attempted = False  # 跟踪是否已尝试年龄限制重试
        self.browser_media_fallback_attempted = False  # 跟踪是否已尝试浏览器媒体嗅探回退
        self.ip_location_retry_count = 0  # 跟踪IP位置相关的重试次数
        self.failed_videos = []  # 用于记录下载失败的视频信息
        self.skipped_videos = []  # 用于记录因限制而跳过的视频信息（播放列表模式）
        self.current_video_title = self._get_default_single_title()  # 当前正在处理的视频标题
        self.current_video_id = ""  # 当前正在处理的视频ID
        self.total_videos = 0  # 播放列表中的总视频数
        self.successful_downloads = 0  # 成功下载的视频数
        self.downloaded_files = set()  # 追踪实际下载的文件路径（去重）
        self.download_start_files = set()  # 下载开始前的文件列表
        self.playlist_total_count = 0  # 播放列表总视频数
        self.playlist_processed_count = 0  # 已处理的视频数
        self.playlist_skipped_count = 0  # 跳过的视频数（日期过滤等非错误原因）
        self.playlist_restriction_skipped_count = 0  # 跳过的视频数（因限制跳过）
        self.playlist_attempted_count = 0  # 实际尝试下载的视频数（范围内的）
        self.expected_download_count = 0  # 用户设置的预期下载数量（从播放列表范围或最大下载数量解析）
        self.has_playlist_items_set = False  # 标记用户是否设置了 --playlist-items 参数
        self.playlist_video_map = {}  # 存储播放列表视频ID到标题的映射 {video_id: title}
        self.in_range_video_ids = set()  # 跟踪在日期范围内尝试下载的视频ID（用于精准统计）
        self.current_item_skipped_by_date = False  # 标记当前视频是否被日期过滤跳过
        self._calculate_expected_download_count()  # 计算预期下载数量
        self.internal_cookie_attached = False  # 仅当加载了内置浏览器提取的Cookie文件时为True
        self.current_downloading_files = set()  # 【新增】追踪正在下载的文件完整路径，用于取消时清理
        self._yt_dlp_temp_dir = None  # yt-dlp 临时文件目录（分片/中间文件）
        self._yt_tmp_run_key = f"{int(time.time())}_{os.getpid()}_{id(self)}"
        self._staging_output_dir = None  # 单视频下载暂存目录（避免目标目录出现中间文件）

    def _get_default_single_title(self):
        if self.playlist_enabled:
            return ""
        return choose_better_download_title(self.preferred_title)

    def _remember_video_title(self, title):
        """记录可信的视频标题，供命名、历史记录和重命名共用。"""
        resolved = choose_better_download_title(
            title,
            self.preferred_title,
            self.current_video_title,
            (self.video_info or {}).get('title', '') if isinstance(self.video_info, dict) else "",
        )
        if not resolved or is_generated_download_title(resolved):
            return ""

        self.preferred_title = resolved
        self.current_video_title = resolved
        base_info = dict(self.video_info) if isinstance(self.video_info, dict) else {}
        base_info['title'] = resolved
        self.video_info = base_info
        return resolved

    def _probe_single_video_title(self):
        """主下载器单视频直下时先拿原标题，避免落盘文件名退化成内部运行键。"""
        if self._is_effective_playlist_job() or self.media_capture_download:
            return ""

        cached_title = choose_better_download_title(
            self.preferred_title,
            self.current_video_title,
            (self.video_info or {}).get('title', '') if isinstance(self.video_info, dict) else "",
        )
        if cached_title and not is_generated_download_title(cached_title):
            return cached_title

        cmd = ["yt-dlp.exe", "--print", "title", "--no-playlist", self.url]
        try:
            self._attach_internal_cookie(cmd, url=self.url)
        except Exception:
            pass

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
            stdout_bytes, stderr_bytes = process.communicate(timeout=60)
            stdout = decode_subprocess_bytes(stdout_bytes) if stdout_bytes else ""
            stderr = decode_subprocess_bytes(stderr_bytes) if stderr_bytes else ""
        except Exception as e:
            self.progress_signal.emit(f"⚠️ 预取视频标题失败: {e}")
            return ""

        if process.returncode != 0:
            err_lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
            if err_lines:
                self.progress_signal.emit(f"⚠️ 预取视频标题失败: {err_lines[-1]}")
            return ""

        title_lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
        if not title_lines:
            return ""

        resolved = self._remember_video_title(title_lines[-1])
        if resolved:
            self.progress_signal.emit(f"✓ 已预取视频标题: {resolved}")
        return resolved

    def _build_output_template(self, output_base_dir, suffix=""):
        """统一生成输出模板，单视频优先使用已知真实标题。"""
        is_playlist_job = self._is_effective_playlist_job()
        if is_playlist_job:
            return os.path.join(output_base_dir, f"%(playlist_title)s/%(playlist_index)s-%(title)s{suffix}.%(ext)s")

        forced_title = sanitize_output_title_component(self.preferred_title)
        title_token = forced_title if forced_title else "%(title)s"
        return os.path.join(output_base_dir, f"{title_token}{suffix}.%(ext)s")

    def _looks_like_direct_media_url(self, url):
        text = str(url or "").strip().lower()
        if not text:
            return False

        direct_tokens = (
            ".mp4", ".m3u8", ".mpd", ".webm", ".mkv", ".mov", ".flv",
            "googlevideo.com", "videoplayback", "bilivideo", "upos-",
            "video.twimg.com", "douyinvod.com", "douyincdn.com",
            "tiktokcdn.com", "bytecdn", "playlist.m3u8", "master.m3u8",
        )
        return any(token in text for token in direct_tokens)

    def _is_internal_cookie_file(self, path):
        """检查是否是由内置浏览器提取的Cookie文件
        
        【关键修复】不再解析cookie文件内容，而是检查对应的.internal标记文件
        因为yt-dlp使用cookie后会重写文件头，导致原有的Source标记丢失
        """
        try:
            marker_path = path.replace('_cookies.txt', '_cookies.internal')
            if os.path.exists(marker_path):
                return True
            # 兼容旧版本 Cookie 文件（尚未生成 .internal 标记）
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
            if not h or h in seen:
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

        # 国内平台（B站等）无匹配 cookies 时不回退到其他平台 cookies：
        # 携带无关 cookies 可能触发平台风控（如 B 站 CDN 限流）；免费视频无需登录。
        no_fallback_hosts = ('bilibili.com', 'b23.tv', 'acg.tv', 'bilivideo.com', 'bilivideo.cn')
        if any(d in host for d in no_fallback_hosts):
            return None, "none", host, candidates

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
    
    def _generate_unique_filename(self, file_path):
        """
        生成唯一的文件名。如果文件已存在，在文件名和扩展名之间添加递增序号。
        
        Args:
            file_path: 原始文件路径
            
        Returns:
            唯一的文件路径（如果原路径无冲突则返回原路径）
        """
        try:
            if not os.path.exists(file_path):
                return file_path
            
            directory = os.path.dirname(file_path)
            filename = os.path.basename(file_path)
            name, ext = os.path.splitext(filename)
            
            counter = 1
            while True:
                new_filename = f"{name}_{counter}{ext}"
                new_path = os.path.join(directory, new_filename)
                if not os.path.exists(new_path):
                    self.progress_signal.emit(
                        f"📝 检测到重复文件名，已重命名: {filename} → {new_filename}"
                    )
                    return new_path
                counter += 1
                if counter > 1000:  # 防止无限循环
                    self.progress_signal.emit(
                        f"⚠️ 无法为文件 {filename} 生成唯一文件名（已尝试1000次）"
                    )
                    return file_path  # 返回原路径，让yt-dlp自己处理
        except Exception as e:
            self.progress_signal.emit(f"⚠️ 生成唯一文件名时出错: {str(e)}")
            return file_path  # 出错时返回原路径

    def _build_preferred_output_path(self, target_dir, name):
        """必要时将内部运行键/临时名修正为可信标题。"""
        dst = os.path.join(target_dir, name)
        if self._is_effective_playlist_job():
            return dst

        current_title = cleanup_download_title(name)
        preferred_title = choose_better_download_title(
            self.preferred_title,
            self.current_video_title,
            (self.video_info or {}).get('title', '') if isinstance(self.video_info, dict) else "",
        )
        if not preferred_title or is_generated_download_title(preferred_title):
            return dst
        if not is_generated_download_title(current_title):
            return dst

        safe_title = sanitize_output_title_component(preferred_title)
        if not safe_title:
            return dst

        _, ext = os.path.splitext(name)
        return os.path.join(target_dir, f"{safe_title}{ext}")

    def _needs_audio_lang_timeline_fix(self):
        """是否需要对“指定音频语言”的视频执行时间轴稳定化处理。"""
        try:
            if not self.main_app or not hasattr(self.main_app, 'get_audio_language_code'):
                return False
            audio_lang_code = str(self.main_app.get_audio_language_code() or "").strip().lower()
            if not audio_lang_code or audio_lang_code == "orig":
                return False

            fmt = str(self.format_option or "")
            # 仅音频/仅缩略图模式不做视频稳定化
            if " -x " in fmt or "--extract-audio" in fmt:
                return False
            if fmt.strip() == "--skip-download --write-thumbnail":
                return False
            return True
        except Exception:
            return False

    def _collect_recent_video_files(self):
        """收集本次任务生成的近期视频文件。"""
        video_exts = {'.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv'}
        files = []
        try:
            if not self.output_path or not os.path.isdir(self.output_path):
                return files
            start_time = getattr(self, 'process_start_time', time.time() - 3600)
            for root, _, names in os.walk(self.output_path):
                for name in names:
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in video_exts:
                        continue
                    full_path = os.path.join(root, name)
                    try:
                        if os.path.getmtime(full_path) >= start_time - 300:
                            files.append(full_path)
                    except Exception:
                        continue
            return files
        except Exception:
            return files

    def _ensure_yt_dlp_temp_path(self, cmd):
        """
        将 yt-dlp 的临时文件重定向到系统临时目录，避免在目标下载目录出现多个中间文件。
        """
        try:
            if not isinstance(cmd, list):
                return
            # 若命令中已存在 temp 路径配置，尊重已有配置
            for idx, token in enumerate(cmd):
                if token == "--paths" and idx + 1 < len(cmd) and str(cmd[idx + 1]).startswith("temp:"):
                    return

            import tempfile
            if not self._yt_dlp_temp_dir:
                temp_root = os.path.join(tempfile.gettempdir(), "video_downloader_yt_tmp")
                os.makedirs(temp_root, exist_ok=True)
                self._yt_dlp_temp_dir = os.path.join(temp_root, self._yt_tmp_run_key)
                os.makedirs(self._yt_dlp_temp_dir, exist_ok=True)

            cmd.extend(["--paths", f"temp:{self._yt_dlp_temp_dir}"])
            self.progress_signal.emit(t("✓ 临时分片已重定向到系统临时目录（下载目录仅保留最终文件）"))
        except Exception as e:
            self.progress_signal.emit(f"⚠️ 临时目录重定向失败，继续默认模式: {str(e)}")

    def _cleanup_yt_dlp_temp_path(self):
        """清理 yt-dlp 临时目录。"""
        try:
            temp_dir = getattr(self, "_yt_dlp_temp_dir", None)
            if temp_dir and os.path.isdir(temp_dir):
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            self._yt_dlp_temp_dir = None
        except Exception:
            pass

    def _prepare_staging_output_dir(self):
        """
        为单视频下载准备暂存目录。
        下载过程中所有中间文件都写入暂存目录，完成后再搬运最终文件到用户目录。
        """
        try:
            import tempfile
            if self._staging_output_dir and os.path.isdir(self._staging_output_dir):
                return self._staging_output_dir
            stage_root = os.path.join(tempfile.gettempdir(), "video_downloader_stage")
            os.makedirs(stage_root, exist_ok=True)
            self._staging_output_dir = os.path.join(stage_root, self._yt_tmp_run_key)
            os.makedirs(self._staging_output_dir, exist_ok=True)
            return self._staging_output_dir
        except Exception:
            return None

    def _move_staging_outputs_to_target(self):
        """将暂存目录中的最终文件搬运到用户输出目录。"""
        moved_files = []
        stage_dir = getattr(self, "_staging_output_dir", None)
        try:
            if not stage_dir or not os.path.isdir(stage_dir):
                return moved_files
            target_dir = self.output_path
            if not target_dir:
                return moved_files
            os.makedirs(target_dir, exist_ok=True)

            temp_suffixes = (".part", ".ytdl", ".tmp", ".temp")
            import shutil
            for root, _, files in os.walk(stage_dir):
                for name in files:
                    low = name.lower()
                    if low.endswith(temp_suffixes) or ".part-frag" in low:
                        continue
                    src = os.path.join(root, name)
                    rel = os.path.relpath(src, stage_dir)
                    rel_dir = os.path.dirname(rel)
                    base_name = os.path.basename(rel)
                    dst_base_dir = os.path.join(target_dir, rel_dir) if rel_dir and rel_dir != "." else target_dir
                    dst = self._build_preferred_output_path(dst_base_dir, base_name)
                    dst_dir = os.path.dirname(dst)
                    if dst_dir:
                        os.makedirs(dst_dir, exist_ok=True)
                    if os.path.exists(dst):
                        dst = self._generate_unique_filename(dst)
                    shutil.move(src, dst)
                    moved_files.append(dst)

            # 兜底：某些情况下 yt-dlp 会把最终文件写到 stage 根目录而不是 run 子目录。
            # 仅搬运“本次任务”文件，避免误移动历史文件。
            if not moved_files:
                stage_root = os.path.dirname(stage_dir)
                run_prefix = "_".join(str(self._yt_tmp_run_key or "").split("_")[:2]).strip("_")
                start_time = float(getattr(self, "process_start_time", time.time() - 7200))
                if stage_root and os.path.isdir(stage_root):
                    for name in os.listdir(stage_root):
                        low = name.lower()
                        if low.endswith(temp_suffixes) or ".part-frag" in low:
                            continue
                        if run_prefix and not name.startswith(run_prefix):
                            continue
                        src = os.path.join(stage_root, name)
                        if not os.path.isfile(src):
                            continue
                        try:
                            if os.path.getmtime(src) < (start_time - 120):
                                continue
                        except Exception:
                            continue
                        dst = self._build_preferred_output_path(target_dir, name)
                        if os.path.exists(dst):
                            dst = self._generate_unique_filename(dst)
                        shutil.move(src, dst)
                        moved_files.append(dst)

            return moved_files
        except Exception as e:
            self.progress_signal.emit(f"⚠️ 搬运下载结果失败: {str(e)}")
            return moved_files

    def _cleanup_staging_output_dir(self):
        """清理下载暂存目录。"""
        stage_dir = getattr(self, "_staging_output_dir", None)
        try:
            if stage_dir and os.path.isdir(stage_dir):
                import shutil
                shutil.rmtree(stage_dir, ignore_errors=True)
            # 同时清理可能落在 stage 根目录的本次任务遗留文件。
            stage_root = os.path.dirname(stage_dir) if stage_dir else ""
            run_prefix = "_".join(str(self._yt_tmp_run_key or "").split("_")[:2]).strip("_")
            if stage_root and os.path.isdir(stage_root) and run_prefix:
                for name in os.listdir(stage_root):
                    if not name.startswith(run_prefix):
                        continue
                    path = os.path.join(stage_root, name)
                    if os.path.isfile(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
        except Exception:
            pass
        finally:
            self._staging_output_dir = None

    def _normalize_video_timeline_for_selected_audio_language(self, candidate_files=None):
        """
        当用户指定了音频语言时，优先做“直接修复”：
        1) 先尝试无损 remux（copy）修正时间戳；
        2) 仅在直修失败时，再回退到重编码稳定化。
        """
        if not self._needs_audio_lang_timeline_fix():
            return

        ffmpeg_bin = FFMPEG_CMD or "ffmpeg"
        targets = list(candidate_files) if candidate_files else self._collect_recent_video_files()
        # 只处理视频文件，去重并保持顺序
        targets = _dedupe_preserve_order(targets)
        if not targets:
            self.progress_signal.emit(t("ℹ 未找到可稳定化处理的视频文件"))
            return

        self.progress_signal.emit(t("🛠️ 检测到指定音频语言，开始执行直接时间轴修复..."))
        creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        for src_path in targets:
            try:
                if not os.path.isfile(src_path):
                    continue
                ext = os.path.splitext(src_path)[1].lower()
                # 仅处理 mp4，避免“容器内容与扩展名不一致”
                if ext != '.mp4':
                    continue

                # 第一阶段：无损 remux（最快，几乎不损失质量）
                fast_out = src_path + ".timeline_fast.mp4"
                fast_cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-fflags", "+genpts",
                    "-i", src_path,
                    "-map", "0:v:0",
                    "-map", "0:a:0?",
                    "-c", "copy",
                    "-max_interleave_delta", "0",
                    "-avoid_negative_ts", "make_zero",
                    "-movflags", "+faststart",
                    fast_out,
                ]
                fast_proc = subprocess.run(
                    fast_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creation_flags,
                )
                if fast_proc.returncode == 0 and os.path.exists(fast_out) and os.path.getsize(fast_out) > 0:
                    os.replace(fast_out, src_path)
                    self.progress_signal.emit(f"✓ 直接修复完成: {os.path.basename(src_path)}")
                    continue
                if os.path.exists(fast_out):
                    try:
                        os.remove(fast_out)
                    except Exception:
                        pass

                # 第二阶段：重编码兜底（仅在直修失败时）
                self.progress_signal.emit(f"↪ 直接修复失败，回退重编码: {os.path.basename(src_path)}")
                tmp_out = src_path + ".timeline_fix.mp4"
                ff_cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-fflags", "+genpts",
                    "-i", src_path,
                    "-map", "0:v:0",
                    "-map", "0:a:0?",
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-crf", "22",
                    "-vsync", "cfr",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-af", "aresample=async=1:first_pts=0",
                    "-movflags", "+faststart",
                    tmp_out,
                ]
                proc = subprocess.run(
                    ff_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creation_flags,
                )
                if proc.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
                    # 覆盖原文件，保持最终文件名不变
                    os.replace(tmp_out, src_path)
                    self.progress_signal.emit(f"✓ 稳定化完成: {os.path.basename(src_path)}")
                else:
                    if os.path.exists(tmp_out):
                        try:
                            os.remove(tmp_out)
                        except Exception:
                            pass
                    self.progress_signal.emit(f"⚠️ 稳定化失败，保留原文件: {os.path.basename(src_path)}")
            except Exception as e:
                self.progress_signal.emit(f"⚠️ 稳定化处理异常: {str(e)}")
    
    def _calculate_expected_download_count(self):

        """从additional_options中解析用户设置的预期下载数量"""
        print(f"[调试] _calculate_expected_download_count 开始, additional_options: {self.additional_options}")
        
        # 【关键修复】即使additional_options为空，也检查主应用程序的UI状态
        playlist_items_from_ui = None
        if hasattr(self, 'main_app') and self.main_app and hasattr(self.main_app, 'playlist_range'):
            # 从主应用程序的UI直接获取播放列表范围
            playlist_range_text = self.main_app.playlist_range.text().strip()
            if playlist_range_text:
                playlist_items_from_ui = playlist_range_text
                print(f"[调试] 从主应用程序UI获取到播放列表范围: {playlist_items_from_ui}")
        
        if not self.additional_options and not playlist_items_from_ui:
            print(f"[调试] additional_options 为空且UI中没有范围，返回")
            return
        
        try:
            # 首先检查 --max-downloads 参数
            if self.additional_options and "--max-downloads" in self.additional_options:
                idx = self.additional_options.index("--max-downloads")
                if idx + 1 < len(self.additional_options):
                    max_downloads = self.additional_options[idx + 1]
                    try:
                        self.expected_download_count = int(max_downloads)
                        # 注意：这里不发送信号，因为可能在__init__中调用，信号可能还未连接
                        print(f"[调试] 检测到 --max-downloads: {max_downloads}, 设置预期下载数: {self.expected_download_count}")
                        return  # 如果设置了最大下载数量，优先使用它
                    except ValueError:
                        print(f"[调试] --max-downloads 参数值无效: {max_downloads}")
                        pass
            
            # 检查 --playlist-items 参数（从additional_options或UI获取）
            playlist_items = None
            
            # 优先从additional_options中获取
            if self.additional_options and "--playlist-items" in self.additional_options:
                idx = self.additional_options.index("--playlist-items")
                if idx + 1 < len(self.additional_options):
                    playlist_items = self.additional_options[idx + 1]
                    print(f"[调试] 从additional_options获取到 --playlist-items: {playlist_items}")
            
            # 如果additional_options中没有，但UI中有，使用UI中的值
            if not playlist_items and playlist_items_from_ui:
                playlist_items = playlist_items_from_ui
                print(f"[调试] 使用从UI获取的播放列表范围: {playlist_items}")
            
            if playlist_items:
                self.has_playlist_items_set = True  # 标记用户设置了范围
                try:
                    # 解析播放列表范围，例如 "1-10", "1-10,15,20-25", "1:10", "1:10:2" 等
                    count = self._parse_playlist_items_count(playlist_items)
                    if count > 0:
                        self.expected_download_count = count
                        print(f"[调试] 检测到 --playlist-items: {playlist_items}, 计算预期下载数: {count}")
                        # 注意：这里不发送信号，因为可能在__init__中调用，信号可能还未连接
                    else:
                        print(f"[调试] --playlist-items 参数解析失败: {playlist_items}")
                except Exception as e:
                    print(f"[调试] --playlist-items 参数解析异常: {e}")
                    pass
            else:
                print(f"[调试] 未检测到 --playlist-items 参数")
                
            print(f"[调试] 最终预期下载数: {self.expected_download_count}, 范围设置标志: {self.has_playlist_items_set}")
        except Exception as e:
            # 解析失败不影响下载，只是无法准确显示预期数量
            print(f"[调试] 预期下载数计算异常: {e}")
            pass

    def _url_looks_like_playlist(self, url):
        """基于 URL 特征判断是否是播放列表任务。"""
        text = str(url or "").strip().lower()
        if not text:
            return False
        if "list=" in text:
            return True
        playlist_markers = (
            "/playlist",
            "/channel/",
            "/@",
            "/user/",
            "/sets/",
        )
        return any(marker in text for marker in playlist_markers)

    def _is_effective_playlist_job(self):
        """
        判断当前任务是否应按播放列表处理。
        关键点：避免仅因 UI 默认勾选“下载播放列表”而误判单视频任务。
        """
        opts = self.additional_options or []
        if "--no-playlist" in opts:
            return False
        if "--yes-playlist" in opts or "--playlist-items" in opts:
            return True
        if not self.playlist_enabled:
            return False
        return self._url_looks_like_playlist(self.url)

    def _time_section_mode_active(self):
        """
        兼容主界面时间片段模式状态查询。
        DownloadThread 在部分统计路径会调用该方法，主界面未注入时默认关闭。
        """
        try:
            if self.main_app and hasattr(self.main_app, "_time_section_mode_active"):
                return bool(self.main_app._time_section_mode_active())
        except Exception:
            pass
        return False

    def _get_download_sections_summary(self):
        """
        兼容主界面“下载片段摘要”查询。
        主界面未注入或异常时返回空字符串，不影响主下载流程。
        """
        try:
            if self.main_app and hasattr(self.main_app, "_get_download_sections_summary"):
                result = self.main_app._get_download_sections_summary()
                return str(result or "")
        except Exception:
            pass
        return ""

    def _build_time_section_friendly_format(self, original_format):
        """
        时间片段下载优先使用“单流（内含音视频）”格式，减少中段裁剪时的额外回源开销。
        若单流不可用，自动回退到用户原格式选择，不破坏原有功能。
        """
        original = str(original_format or "").strip()
        if not original:
            return original

        # 仅在明显为分离音视频策略时才注入单流优先，避免干扰普通单格式选择
        looks_split_stream = ("+" in original) or ("bestvideo" in original and "bestaudio" in original)
        if not looks_split_stream:
            return original

        # 提取用户的筛选条件（分辨率上限/音频语言/音轨排除），注入单流选择器，
        # 保证时间片段模式下用户的筛选依然生效；无筛选时使用默认 1080P 上限。
        # 仅提取"过滤型"条件（改变可选集合），忽略格式偏好型（ext=、vcodec^= 等）。
        # 注意：语言条件可能内嵌字符类（如 [language~='(?i)^ja(?:[-_].*)?$']），
        # 必须用引号包裹整体匹配，避免在内部 ] 处截断。
        filter_conditions = []
        _seen = set()
        _filter_re = re.compile(
            r'\[[^\]]*height<=[^\]]*\]'
            r'|\[language~=[\'"][^\'"]*[\'"]\]'
            r'|\[language~=[^\[\]]*\]'
            r'|\[[^\]]*(?:acodec|vcodec)!=+[^\]]*\]'
        )
        for _block in _filter_re.findall(original):
            _b = _block.strip()
            if _b in _seen:
                continue
            _seen.add(_b)
            filter_conditions.append(_b)
        if not filter_conditions:
            # 无筛选条件：保持用户原格式链（最佳质量 + 默认音频语言），
            # 不注入单流限制——避免把最佳质量误限制为 1080P。
            return original

        conditions_str = "".join(filter_conditions)
        single_stream_pref = (
            f"best{conditions_str}[ext=mp4][vcodec^=avc][acodec^=mp4a]/"
            f"best{conditions_str}[ext=mp4][vcodec*=h264][acodec*=mp4a]/"
            f"best{conditions_str}[ext=mp4]/"
            f"best{conditions_str}"
        )
        if original.startswith(single_stream_pref):
            return original
        # 单流仅作兜底（放在用户格式链之后）：保证用户的筛选与最佳质量优先，
        # 避免单流（HLS 最高 1080P）抢占导致画质被限制。
        return f"{original}/{single_stream_pref}"
    
    def _get_current_video_index(self):
        """获取当前正在处理的视频索引"""
        # 尝试从最近的进度消息中提取当前索引
        # 通常在 "正在下载第 X/Y 个视频" 这样的消息中可以找到
        try:
            # 检查是否有保存的当前索引
            if hasattr(self, 'current_downloading_index'):
                return self.current_downloading_index
            
            # 如果没有保存的索引，返回-1表示无法确定
            return -1
        except Exception:
            return -1

    def _is_index_in_range(self, index):
        """检查给定的索引是否在用户设置的播放列表范围内"""
        if not self.has_playlist_items_set or not self.additional_options:
            return True  # 如果没有设置范围，默认包含
        
        try:
            # 找到--playlist-items参数
            if "--playlist-items" in self.additional_options:
                idx = self.additional_options.index("--playlist-items")
                if idx + 1 < len(self.additional_options):
                    playlist_items = self.additional_options[idx + 1]
                    
                    # 解析范围字符串，例如 "1-7", "1,3,5", "1:10:2"
                    parts = playlist_items.split(',')
                    for part in parts:
                        part = part.strip()
                        if not part:
                            continue
                        
                        # 处理范围格式 (支持 "-" 和 ":")
                        if '-' in part or ':' in part:
                            if '-' in part:
                                range_parts = part.split('-', 1)
                            else:
                                range_parts = part.split(':', 2)
                            
                            if len(range_parts) >= 2:
                                try:
                                    start = int(range_parts[0].strip())
                                    end = int(range_parts[1].strip())
                                    # 检查步长（如果有）
                                    step = 1
                                    if len(range_parts) == 3:
                                        step = int(range_parts[2].strip())
                                    
                                    # 检查索引是否在范围内
                                    if start <= end:
                                        if start <= index <= end and (index - start) % step == 0:
                                            return True
                                    else:
                                        if end <= index <= start and (start - index) % step == 0:
                                            return True
                                except ValueError:
                                    continue
                        else:
                            # 单个索引
                            try:
                                if int(part) == index:
                                    return True
                            except ValueError:
                                continue
        except Exception:
            pass
        
        return False  # 默认不包含

    def _parse_playlist_items_count(self, playlist_items_str):
        """解析播放列表范围字符串，计算预期下载的视频数量"""
        if not playlist_items_str:
            return 0
        
        try:
            total_count = 0
            
            # 支持多种格式：
            # 1. "1-10" (范围)
            # 2. "1:10" (范围，使用冒号)
            # 3. "1:10:2" (范围+步长)
            # 4. "1,3,5" (单个索引)
            # 5. "1-10,15,20-25" (混合)
            
            # 分割逗号分隔的部分
            parts = playlist_items_str.split(',')
            
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                
                # 检查是否是范围格式 (支持 "-" 和 ":")
                if '-' in part or ':' in part:
                    # 处理范围
                    if '-' in part:
                        range_parts = part.split('-', 1)
                    else:
                        range_parts = part.split(':', 2)
                    
                    if len(range_parts) >= 2:
                        try:
                            start = int(range_parts[0].strip())
                            end = int(range_parts[1].strip())
                            # 计算步长（如果有）
                            step = 1
                            if len(range_parts) == 3:
                                step = int(range_parts[2].strip())
                            # 计算范围内的数量
                            if start <= end:
                                count = ((end - start) // step) + 1
                                total_count += count
                        except ValueError:
                            pass
                else:
                    # 单个索引
                    try:
                        int(part)  # 验证是数字
                        total_count += 1
                    except ValueError:
                        pass
            
            return total_count
        except Exception:
            return 0
    
    def _fetch_playlist_video_map(self):
        """
        【关键修复】在下载开始前获取播放列表信息，建立视频ID到标题的映射
        这样在视频因限制失败时，可以从映射中获取准确的标题
        """
        try:
            self.progress_signal.emit(t("🔍 正在获取播放列表信息（用于准确记录失败视频标题）..."))
            
            # 使用 --flat-playlist 快速获取播放列表信息
            cmd = [
                "yt-dlp.exe",
                "--ignore-config",
                "--no-warnings",
                "--flat-playlist",
                "--dump-single-json",
                "--yes-playlist",
                self.url
            ]
            
            # 如果用户设置了 --playlist-items，也应用到这个命令中
            if self.has_playlist_items_set and "--playlist-items" in self.additional_options:
                idx = self.additional_options.index("--playlist-items")
                if idx + 1 < len(self.additional_options):
                    cmd.extend(["--playlist-items", self.additional_options[idx + 1]])
            
            # 统一 Cookie 逻辑：候选域名匹配 + 最近内置 Cookie 回退
            try:
                self._attach_internal_cookie(
                    cmd,
                    url=self.url,
                    direct_msg="🍪 播放列表获取使用Cookie: ",
                    fallback_msg="🍪 播放列表获取回退Cookie: ",
                )
            except Exception:
                pass
            
            # 设置超时为30秒（不要太长，避免拖慢下载）
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            
            try:
                stdout, stderr = process.communicate(timeout=30)
                
                if process.returncode == 0 and stdout.strip():
                    import json
                    playlist_data = json.loads(stdout)
                    
                    # 建立视频ID到标题的映射
                    entries = playlist_data.get('entries', [])
                    for entry in entries:
                        video_id = entry.get('id')
                        video_title = entry.get('title', entry.get('fulltitle', ''))
                        if video_id and video_title:
                            self.playlist_video_map[video_id] = video_title
                    
                    if self.playlist_video_map:
                        self.progress_signal.emit(f"✅ 成功获取 {len(self.playlist_video_map)} 个视频的标题信息")
                    else:
                        self.progress_signal.emit(t("⚠️ 未能获取视频标题映射，将使用实时提取的标题"))
                else:
                    self.progress_signal.emit(t("⚠️ 获取播放列表信息失败，将使用实时提取的标题"))
                    
            except subprocess.TimeoutExpired:
                process.kill()
                self.progress_signal.emit(t("⚠️ 获取播放列表信息超时，将使用实时提取的标题"))
                
        except Exception as e:
            self.progress_signal.emit(f"⚠️ 获取播放列表信息出错: {str(e)}，将使用实时提取的标题")
        
    def _apply_base_yt_dlp_safety(self, cmd_list):
        """统一追加yt-dlp基础安全与兼容参数。"""
        try:
            def ensure_append(args):
                key = args[0] if args else None
                if key and key in cmd_list:
                    return
                cmd_list.extend(args)

            ensure_append(["--ignore-config"])          # 忽略用户全局配置
            ensure_append(["--no-cache-dir"])           # 关闭缓存
            ensure_append(["--windows-filenames"])      # Windows文件名兼容
            if "--trim-filenames" not in cmd_list:
                cmd_list.extend(["--trim-filenames", "128"])  # 限制文件名长度

            # 【商业版关键优化】启用远程组件自动获取
            # 当 YouTube 更新 n/sig 加密算法时，yt-dlp 可以自动从 GitHub 获取最新解密脚本
            # 这样即使用户不更新软件，也能继续正常下载 YouTube 视频
            ensure_append(["--remote-components", "ejs:github"])

            # 指定ffmpeg所在位置（优先当前目录）
            try:
                ffmpeg_exe = os.path.join(RUNTIME_APP_DIR, "ffmpeg.exe")
                if os.path.exists(ffmpeg_exe):
                    ensure_append(["--ffmpeg-location", RUNTIME_APP_DIR])
            except Exception:
                pass
        except Exception:
            pass

    def _maybe_apply_download_archive(self, cmd_list):
        """在输出目录存在`.yt-dlp-archive`时启用下载存档功能。"""
        try:
            if self.output_path:
                archive_path = os.path.join(self.output_path, ".yt-dlp-archive")
                if os.path.isfile(archive_path):
                    cmd_list.extend(["--download-archive", archive_path])
                    self.progress_signal.emit(f"✓ 已启用下载存档：{archive_path}")
        except Exception:
            pass

    def _is_x_platform_url(self, url):
        """检测是否为X平台（原Twitter）的URL — 委托给模块级函数"""
        return _is_x_platform_url(url)
    
    
    def _get_x_platform_best_format(self):
        """
        为X平台自动选择最佳格式，避免手动选择对话框
        基于yt-dlp官方推荐和X平台视频特点优化
        """
        # X平台视频特点：
        # 1. 通常提供多种分辨率（720p, 480p, 360p等）
        # 2. 视频编码主要是H.264
        # 3. 音频编码主要是AAC
        # 4. 容器格式主要是MP4
        
        # 优先选择最佳视频+音频组合，如果不可用则回退到单一最佳格式
        # 限制最高分辨率为1080p，确保兼容性和合理的文件大小
        x_format_selector = (
            "bestvideo[vcodec^=avc1][height<=1080]+bestaudio[acodec=aac]/"
            "bestvideo[vcodec*=h264][height<=1080]+bestaudio[acodec=aac]/"
            "best[vcodec^=avc1][height<=1080]/"
            "best[ext=mp4][height<=1080]/"
            "best[height<=720]/"
            "best"
        )
        
        return x_format_selector
    
    def _analyze_x_platform_no_video_error(self):
        """
        分析X平台"No video could be found in this tweet"错误
        提供详细的错误原因分析和解决方案
        """
        analysis_message = [
            "🔍 X平台错误分析: 'No video could be found in this tweet'",
            "",
            "📝 可能的原因:",
            "1️⃣ 该推文只包含文本或图片，没有视频内容",
            "2️⃣ 视频已被删除或设为私人可见",
            "3️⃣ 需要登录才能访问该内容",
            "4️⃣ 该推文包含GIF或其他特殊媒体格式",
            "5️⃣ X平台API限制或反爬虫机制",
            "",
            "🔧 建议的解决方案:",
            "✅ 1. 检查推文内容: 手动访问该URL确认是否包含视频",
            "✅ 2. 登录账户: 在内置浏览器中登录X平台并提取Cookie",
            "✅ 3. 尝试不同的API: 程序将自动尝试不同API模式",
            "✅ 4. 检查网络: 确保网络连接稳定且可访问X平台",
            "✅ 5. 等待重试: 某些情况下稍后重试可能成功",
            "",
            "💡 提示: 如果该推文确实包含视频，请先登录X平台再试"
        ]
        
        return "\n".join(analysis_message)
    
    def _retry_x_platform_with_different_api(self):
        """
        使用不同API模式重试X平台下载
        按照优先级尝试: syndication -> graphql -> legacy
        """
        api_modes = ['syndication', 'graphql', 'legacy']
        current_mode = getattr(self, 'x_api_retry_mode', 'syndication')
        
        try:
            current_index = api_modes.index(current_mode)
            if current_index < len(api_modes) - 1:
                # 还有下一个API模式可以尝试
                next_mode = api_modes[current_index + 1]
                self.x_api_retry_mode = next_mode
                self.progress_signal.emit(f"🔄 尝试使用不同API模式: {next_mode}")
                self.progress_signal.emit(t("🔄 正在重新启动下载..."))
                
                # 重新调用run方法
                self.run()
                return True
            else:
                # 所有API模式都已尝试
                self.progress_signal.emit(t("⚠️ 所有X平台API模式都已尝试，无法下载该视频"))
                return False
        except ValueError:
            # 当前API模式不在列表中
            self.x_api_retry_mode = 'graphql'
            self.progress_signal.emit(t("🔄 尝试使用graphql API模式"))
            self.run()
            return True
    
    def _test_proxy_before_download(self):
        """
        在下载开始前测试代理连接
        如果启用了代理，进行快速连接测试并在日志中显示结果
        """
        try:
            # 检查是否启用了代理
            if not hasattr(self, 'additional_options') or not self.additional_options:
                return
            
            # 查找代理选项
            proxy_url = None
            for i, opt in enumerate(self.additional_options):
                if opt == "--proxy" and i + 1 < len(self.additional_options):
                    proxy_url = self.additional_options[i + 1]
                    break
            
            if not proxy_url:
                return  # 没有启用代理
            
            # 显示代理信息
            self.progress_signal.emit("=" * 60)
            self.progress_signal.emit(t("🔐 检测到代理设置"))
            self.progress_signal.emit(f"代理地址: {proxy_url}")
            self.progress_signal.emit(t("正在测试代理连接..."))
            self.progress_signal.emit("=" * 60)
            
            # 快速测试代理连接
            import urllib.request
            import urllib.error
            import socket
            
            test_success = False
            test_error = None
            
            try:
                # 设置代理
                proxy_handler = urllib.request.ProxyHandler({
                    'http': proxy_url,
                    'https': proxy_url
                })
                opener = urllib.request.build_opener(proxy_handler)
                opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
                
                # 注意：不设置全局socket超时，而是在每个请求中使用局部超时参数
                # 这样不会影响应用程序的其他网络操作
                
                # 尝试访问一个简单的网站
                test_url = 'http://www.google.com'
                response = opener.open(test_url, timeout=5)  # 使用局部超时参数
                
                if response.status == 200 or response.code == 200:
                    test_success = True
                    
            except urllib.error.HTTPError as e:
                # HTTP错误码，但连接成功
                if e.code in [301, 302, 307, 308, 403, 404]:
                    test_success = True
                else:
                    test_error = f"HTTP错误 {e.code}"
                    
            except urllib.error.URLError as e:
                error_str = str(e.reason) if hasattr(e, 'reason') else str(e)
                if 'refused' in error_str.lower():
                    test_error = "代理服务器拒绝连接"
                elif 'timeout' in error_str.lower():
                    test_error = "代理连接超时"
                else:
                    test_error = f"连接错误: {error_str}"
                    
            except socket.timeout:
                test_error = "代理连接超时"
                
            except Exception as e:
                test_error = f"测试失败: {str(e)}"
            
            # 显示测试结果
            self.progress_signal.emit("-" * 60)
            if test_success:
                self.progress_signal.emit(t("✅ 代理连接成功！"))
                self.progress_signal.emit(t("✅ 代理服务器工作正常，可以继续下载"))
                self.progress_signal.emit(t("💡 下载将通过代理服务器进行"))
            else:
                self.progress_signal.emit(t("⚠️ 代理连接测试失败"))
                if test_error:
                    self.progress_signal.emit(f"⚠️ 错误信息: {test_error}")
                self.progress_signal.emit(t("⚠️ 将继续尝试下载，但可能会失败"))
                self.progress_signal.emit(t("💡 建议检查代理设置或点击'测试连接'按钮进行详细测试"))
            
            self.progress_signal.emit("=" * 60)
            self.progress_signal.emit("")  # 空行分隔
            
        except Exception as e:
            # 代理测试失败不应该中断下载流程
            # 捕获所有异常，确保不会导致程序崩溃
            import traceback
            self.progress_signal.emit(f"⚠️ 代理连接测试时出现异常: {str(e)}")
            self.progress_signal.emit(f"⚠️ 异常类型: {type(e).__name__}")
            # 在调试模式下显示详细的堆栈跟踪
            # self.progress_signal.emit(f"堆栈跟踪:\n{traceback.format_exc()}")
            self.progress_signal.emit(t("将继续执行下载任务..."))
        
    def run(self):
        import subprocess  # 确保 subprocess 在整个 run 方法中可用
        import os  # 确保 os 在整个 run 方法中可用
        import time  # 确保 time 在整个 run 方法中可用
        import re  # 确保 re 在整个 run 方法中可用
        try:
            # 在任务开始时检查是否已被取消
            if self._cancelled:
                self.progress_signal.emit(t("检测到下载已被取消，停止执行"))
                self.cancel_signal.emit()
                return
                
            self.progress_signal.emit(t("ℹ 开始执行下载任务..."))
            self.progress_signal.emit(f"目标URL: {self.url}")
            self.progress_signal.emit(f"输出路径: {self.output_path}")
            self.progress_signal.emit(f"格式选项: {self.format_option}")
            
            # 检查是否启用了代理并进行连接测试
            self._test_proxy_before_download()
            
            # Cookie调试信息已移除 - 限制绕过功能已被删除
            
            if self.extract_info:
                # 只提取视频信息
                self.progress_signal.emit(t("模式: 提取视频信息"))
                self._extract_video_info()
                return
                
            if self.extract_playlist:
                # 专门提取播放列表信息
                self.progress_signal.emit(t("模式: 提取播放列表信息"))
                self._extract_playlist_info()
                return
                
            if self.list_formats:
                # 只列出可用格式
                self.progress_signal.emit(t("模式: 列出可用格式"))
                self._list_formats()
                return
                
            self.progress_signal.emit(t("模式: 正常下载"))
            time_section_mode = self._time_section_mode_active()
            is_youtube_url = ('youtube' in self.url.lower() or 'youtu.be' in self.url.lower())
            
            # 【关键修复】如果是播放列表下载，先获取播放列表信息以建立视频ID到标题的映射
            # 这样在视频因限制失败时，可以从映射中获取准确的标题
            if self._is_effective_playlist_job():
                self._fetch_playlist_video_map()
            else:
                self._probe_single_video_title()
                
            # 构建yt-dlp命令
            cmd = ["yt-dlp.exe"]
            self._apply_base_yt_dlp_safety(cmd)
            
            # 自动加载 Cookie（仅允许内置浏览器提取的 Cookie 文件）
            try:
                self._attach_internal_cookie(
                    cmd,
                    url=self.url,
                    direct_msg="🍪 已加载内置浏览器Cookie: ",
                    fallback_msg="🍪 回退使用最近内置浏览器Cookie: ",
                    missing_msg="ℹ 未检测到内置浏览器Cookie；若为受限视频，请先在内置浏览器登录并点击🍪提取",
                )
            except Exception as e:
                import traceback
                print(f"Cookie加载出错: {e}")
                print(f"详细错误: {traceback.format_exc()}")
            
            # 添加简化的绕过参数（如果启用）
            # 旧的简化绕过模式已移除，现在使用高级选项中的限制绕过设置
            
            # 媒体捕获下载优先走兼容模式，忽略主下载器页面的格式选择
            if self.media_capture_download:
                if self._looks_like_direct_media_url(self.url):
                    self.progress_signal.emit(t("✓ 媒体捕获下载检测到直链，已忽略格式筛选并使用兼容模式"))
                    if any(token in str(self.url).lower() for token in (".m3u8", ".mpd", "playlist.m3u8", "master.m3u8")):
                        cmd.extend(["--hls-use-mpegts"])
                elif self.media_capture_format_id and self.media_capture_format_id.lower() != "best":
                    cmd.extend(["-f", self.media_capture_format_id])
                    self.progress_signal.emit(f"✓ 媒体捕获下载使用捕获面板所选格式: {self.media_capture_format_id}")
                    cmd.extend(["--merge-output-format", "mp4"])
                else:
                    cmd.extend(["-f", "best"])
                    self.progress_signal.emit(t("✓ 媒体捕获下载使用兼容最佳格式"))
                    cmd.extend(["--merge-output-format", "mp4"])
            # X平台（原Twitter）自动格式选择处理
            elif self._is_x_platform_url(self.url):
                self.progress_signal.emit(t("🔄 检测到X平台视频，启用自动格式选择..."))
                # 对X平台使用自动选择的最佳格式，避免手动选择对话框
                auto_format = self._get_x_platform_best_format()
                cmd.extend(["-f", auto_format])
                cmd.extend(["-S", "res,fps,vcodec:h264:vp9,acodec:aac:m4a"])
                cmd.extend(["--merge-output-format", "mp4"])
                self.progress_signal.emit(f"✅ X平台自动选择格式: {auto_format}")
            # 添加视频格式选项 - 与官方yt-dlp完全一致
            elif self.format_option:
                # 特殊处理“仅缩略图”选项
                if self.format_option == "--skip-download --write-thumbnail":
                    cmd.extend(["--skip-download", "--write-thumbnail"])
                    self.progress_signal.emit(t("ℹ 仅下载缩略图模式"))
                # 处理音频提取选项
                elif self.format_option and (" -x " in self.format_option or "--extract-audio" in self.format_option):
                    parts = self.format_option.split(" -x ") if self.format_option and " -x " in self.format_option else [self.format_option.replace("--extract-audio", "").strip()]
                    if parts[0].strip():
                        cmd.extend(["-f", parts[0].strip()])
                    cmd.extend(["--extract-audio"])
                    
                    # 处理音频格式
                    if len(parts) > 1 and "--audio-format" in parts[1]:
                        audio_format = parts[1].split("--audio-format")[1].strip()
                        cmd.extend(["--audio-format", audio_format])
                    elif self.format_option and "--audio-format" in self.format_option:
                        audio_format = self.format_option.split("--audio-format")[1].strip()
                        cmd.extend(["--audio-format", audio_format])
                    else:
                        # 默认音频格式
                        cmd.extend(["--audio-format", "mp3"])
                    
                    # 音频质量设置
                    if self.format_option and "--audio-quality" in self.format_option:
                        quality = self.format_option.split("--audio-quality")[1].strip().split()[0]
                        cmd.extend(["--audio-quality", quality])
                    else:
                        cmd.extend(["--audio-quality", "0"])  # 最高质量
                    
                    self.progress_signal.emit(t("✓ 启用音频提取模式"))
                else:
                    # 标准视频格式选择 - 与官方yt-dlp完全一致
                    if self.format_option == "best":
                        cmd.extend(["-f", "best"])
                    elif self.format_option == "worst":
                        cmd.extend(["-f", "worst"])
                    elif self.format_option == "bestvideo+bestaudio/best":
                        cmd.extend(["-f", "bestvideo+bestaudio/best"])
                    elif self.format_option == "bestvideo":
                        cmd.extend(["-f", "bestvideo"])
                    elif self.format_option == "bestaudio":
                        cmd.extend(["-f", "bestaudio"])
                    else:
                        # 自定义格式选择器
                        cmd.extend(["-f", self.format_option])
                    
                    self.progress_signal.emit(f"✓ 视频格式: {self.format_option}")
                    
                    # 简化的兼容性处理：使用后处理确保mp4容器
                    # yt-dlp会自动使用最合适的编码器，我们只需确保输出为mp4容器
                    if not "--skip-download" in self.format_option:  # 不是仅缩略图模式
                        cmd.extend(["--merge-output-format", "mp4"])
                        self.progress_signal.emit(t("✓ 合并输出格式设置为mp4（最大兼容性）"))
            else:
                # 默认格式 - 使用优化的兼容性格式选择器
                cmd.extend(["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"])
                cmd.extend(["--merge-output-format", "mp4"])
                self.progress_signal.emit(t("✓ 使用默认最佳质量格式（mp4+m4a优先，最大兼容性）"))

            # 时间片段 + YouTube 场景：优先单流格式，加速中段裁剪；失败时回退到原格式链
            if time_section_mode and is_youtube_url:
                explicit_format_id = False
                try:
                    if self.main_app and hasattr(self.main_app, 'format_combo'):
                        explicit_format_id = (self.main_app.format_combo.currentText() == "指定格式ID")
                except Exception:
                    explicit_format_id = False

                if not explicit_format_id and "-f" in cmd:
                    try:
                        f_index = len(cmd) - 1 - cmd[::-1].index("-f")
                        if f_index + 1 < len(cmd):
                            old_format = str(cmd[f_index + 1] or "")
                            new_format = self._build_time_section_friendly_format(old_format)
                            if new_format and new_format != old_format:
                                cmd[f_index + 1] = new_format
                                self.progress_signal.emit(t("✓ 时间片段模式：单流格式兜底（用户格式优先，画质不受限）"))
                    except Exception:
                        pass
            
            # 添加基础下载选项和后处理选项确保兼容性
            # 针对“指定音频语言”场景使用更稳妥的时间戳生成策略，避免部分站点多音轨合并后卡顿。
            postprocessor_args = "ffmpeg:-avoid_negative_ts make_zero"
            selected_audio_lang = ""
            try:
                if self.main_app and hasattr(self.main_app, 'get_audio_language_code'):
                    selected_audio_lang = str(self.main_app.get_audio_language_code() or "").strip().lower()
            except Exception:
                selected_audio_lang = ""
            if selected_audio_lang and selected_audio_lang != "orig":
                postprocessor_args = "ffmpeg:-fflags +genpts -movflags +faststart"
                self.progress_signal.emit(t("✓ 指定音频语言场景启用稳健时间戳后处理"))

            cmd.extend([
                "--prefer-free-formats",               # 优先选择免费格式
                "--no-warnings",                       # 不显示警告
                "--fixup", "detect_or_warn",           # 自动修复已知的文件问题
                "--postprocessor-args", postprocessor_args,  # 修复时间戳问题
                "--no-abort-on-error",                 # 遇到错误不中止（如果用户启用了忽略错误）
                "--no-check-certificates",             # 不检查SSL证书
                "--geo-bypass",                        # 尝试绕过地理限制
                "--geo-bypass-country", "CN",          # 设置绕过国家为中国
                "--windows-filenames",                 # 确保文件名在Windows上合法（替换非法字符如: * ? " < > |）
                "--trim-filenames", "80",              # 限制文件名长度最大80字符，避免过长标题导致[Errno 22]错误
                "--no-overwrites",                     # 【新增】不覆盖已存在的文件，与重复文件名处理配合使用
                
                # 【新增】增强下载稳定性
                "--retries", "10",                     # 增加重试次数
                "--fragment-retries", "10",            # 增加片段重试次数
                "--buffer-size", "1024K",              # 优化缓冲区大小
                "--http-chunk-size", "10M",            # 增加HTTP块大小，减少请求次数
                "--socket-timeout", "30",              # 增加套接字超时时间
                "--file-access-retries", "5",          # 增加文件访问重试次数
            ])
            
            # 添加中国平台专用的User-Agent和Headers
            china_platforms = [
                'douyin.com', 'douyinvod.com', 'douyincdn.com', 'douyinpic.com',
                'iesdouyin.com', 'tos-cn-v',
                'kuaishou.com', 'kwai.com', 'ksurl.cn', 'ndcimgs.com',
                'bilibili.com', 'bilivideo.com', 'bilivideo.cn', 'b23.tv',
            ]
            is_china_platform = any(platform in self.url.lower() for platform in china_platforms)
            
            if is_china_platform:
                # 为中国平台添加专用的User-Agent，模拟真实浏览器
                china_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                cmd.extend([
                    "--user-agent", china_user_agent,
                    "--add-header", "Accept-Language:zh-CN,zh;q=0.9,en;q=0.8",
                    "--add-header", "Accept:text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "--add-header", "Accept-Encoding:gzip, deflate, br",
                    "--add-header", "DNT:1",
                    "--add-header", "Connection:keep-alive",
                    "--add-header", "Upgrade-Insecure-Requests:1",
                ])
                self.progress_signal.emit(t("✓ 已为中国平台添加专用Headers和User-Agent"))
            
            # 移除全局referer设置，让yt-dlp的官方extractor自己处理referer头
            # 这样可以避免B站、Facebook、Instagram等网站的403拒绝错误
            
            self.progress_signal.emit(t("✓ 已应用基础yt-dlp下载选项和错误处理"))
            
            # 添加输出路径和模板
            if self.output_path:
                is_playlist_job = self._is_effective_playlist_job()
                output_base_dir = self.output_path
                if not is_playlist_job:
                    staged_dir = self._prepare_staging_output_dir()
                    if staged_dir:
                        output_base_dir = staged_dir
                        self.progress_signal.emit(t("✓ 单视频下载启用暂存目录，下载中不会在目标目录产生中间文件"))
                output_template = self._build_output_template(output_base_dir)
                cmd.extend(["-o", output_template])
                # 记录使用的输出模板，便于调试
                self.progress_signal.emit(f"使用输出模板: {output_template}")
                # 可选：输出目录若存在 .yt-dlp-archive 则启用跳过已下载
                self._maybe_apply_download_archive(cmd)

            
            # Cookie应用逻辑已移除 - 限制绕过功能已被删除
            
            # YouTube特定配置 - 智能客户端选择策略
            if 'youtube' in self.url.lower() or 'youtu.be' in self.url.lower():
                self.progress_signal.emit(t("检测到YouTube URL..."))
                
                # 【关键修复】添加 --impersonate 参数来绕过YouTube的TLS指纹检测
                # 这可以解决 curl error (35) TLS connect error 问题
                # 使用 curl_cffi 库模拟 Chrome 浏览器的 TLS 指纹
                cmd.extend(["--impersonate", "chrome"])
                self.progress_signal.emit(t("✓ 已启用浏览器指纹模拟 (Chrome)，解决TLS连接问题"))
                
                # 根据用户的下载需求智能选择客户端策略：
                # 1. 如果用户指定了特定分辨率或音频语言，不限制客户端，让yt-dlp自动选择最佳客户端
                # 2. 如果是普通下载（默认最佳质量），也不限制客户端
                # 3. 只有在遇到特殊限制（如年龄限制）时才会在重试中使用tv_simply等特殊客户端
                
                has_resolution_limit = "height<=" in self.format_option if self.format_option else False
                has_audio_lang = "[language" in self.format_option if self.format_option else False
                
                if has_resolution_limit or has_audio_lang:
                    # 用户指定了分辨率或音频语言，使用默认客户端以获取完整的格式列表
                    self.progress_signal.emit(t("✓ 检测到特定格式要求，使用默认客户端以确保格式可用性"))
                    self.progress_signal.emit(f"  - 分辨率限制: {has_resolution_limit}")
                    self.progress_signal.emit(f"  - 音频语言过滤: {has_audio_lang}")
                else:
                    # 普通下载，也使用默认客户端，让yt-dlp自动优化
                    self.progress_signal.emit(t("✓ 使用默认客户端策略，确保最佳兼容性"))
                
                # 注意：不再强制设置player_client，让yt-dlp根据视频情况自动选择
                # 如果遇到401/403等错误，会在重试逻辑中尝试特殊客户端
            # 简化TikTok/抖音配置 - 添加专用优化
            # 匹配抖音页面域名和 CDN 直链域名（douyinvod.com, douyincdn.com, tos-cn-v 等）
            elif any(domain in self.url.lower() for domain in ['tiktok.com', 'douyin.com', 'douyinvod.com', 'douyincdn.com', 'douyinpic.com', 'iesdouyin.com', 'tos-cn-v']):
                self.progress_signal.emit(t("检测到TikTok/抖音 URL，应用专用优化..."))
                
                # 检测是否为 CDN 直链（媒体捕获场景）
                is_douyin_cdn = any(d in self.url.lower() for d in ['douyinvod.com', 'douyincdn.com', 'douyinpic.com', 'tos-cn-v'])
                
                # 抖音专用优化参数
                douyin_options = []
                if not is_douyin_cdn:
                    # 页面 URL 场景：使用 extractor 参数
                    douyin_options.extend([
                        "--extractor-args", "douyin:api_hostname=aweme.snssdk.com",  # 使用官方API域名
                        "--sleep-interval", "1",                                     # 请求间隔，避免被限制
                        "--max-sleep-interval", "3",                                # 最大请求间隔
                    ])
                
                # 为抖音相关域名（包括 CDN 直链）添加 Referer 和 Origin
                douyin_options.extend([
                    "--add-header", "Referer:https://www.douyin.com/",
                    "--add-header", "Origin:https://www.douyin.com",
                ])
                
                cmd.extend(douyin_options)
                if is_douyin_cdn:
                    self.progress_signal.emit(t("✓ 已为抖音CDN直链添加Referer和Origin头"))
                else:
                    self.progress_signal.emit(t("✓ 已应用TikTok/抖音专用优化配置"))
                
            # 优化快手配置 - 添加专用参数
            elif any(domain in self.url.lower() for domain in ['kuaishou.com', 'kwai.com', 'ksurl.cn']):
                self.progress_signal.emit(t("检测到快手URL，应用专用优化..."))
                
                # 快手专用优化参数
                kuaishou_options = [
                    "--sleep-interval", "1",                                     # 请求间隔，避免被限制
                    "--max-sleep-interval", "3",                                # 最大请求间隔
                    "--add-header", "Referer:https://www.kuaishou.com/",
                    "--add-header", "Origin:https://www.kuaishou.com",
                ]
                
                cmd.extend(kuaishou_options)
                self.progress_signal.emit(t("✓ 已应用快手专用优化配置"))
                
            # 简化X平台配置
            elif any(domain in self.url.lower() for domain in ['twitter.com', 'x.com', 't.co', 'm.twitter.com']):
                self.progress_signal.emit(t("检测到X平台URL，使用默认配置..."))
                # X平台使用yt-dlp默认配置
                self.progress_signal.emit(t("✓ 使用X平台默认配置"))
                
            # 简化Facebook配置
            elif any(domain in self.url.lower() for domain in ['facebook.com', 'fb.com', 'fb.watch', 'm.facebook.com']):
                self.progress_signal.emit(t("检测到Facebook URL，使用默认配置..."))
                # Facebook使用yt-dlp默认配置
                self.progress_signal.emit(t("✓ 使用Facebook默认配置"))
                
            # 简化B站配置
            elif any(domain in self.url.lower() for domain in ['bilibili.com', 'b23.tv', 'acg.tv']):
                self.progress_signal.emit(t("检测到B站URL，使用默认配置..."))
                # B站使用yt-dlp默认配置
                self.progress_signal.emit(t("✓ 使用B站默认配置"))
                
            else:
                self.progress_signal.emit(t("使用标准下载模式"))
            
            # 添加其他选项
            if self.additional_options:
                cmd.extend(self.additional_options)

            # 将下载过程中的分片/中间文件重定向到系统临时目录
            self._ensure_yt_dlp_temp_path(cmd)
            
            # 添加基于官方yt-dlp推荐的高性能优化参数
            # 时间片段模式使用更精简参数，避免与 ffmpeg 分段下载冲突/相互抵消
            if time_section_mode:
                optimization_options = [
                    "--retries", "3",
                    "--fragment-retries", "3",
                    "--file-access-retries", "2",
                    "--retry-sleep", "exp=0.5:2:1.5",
                    "--buffer-size", "1024K",
                    "--http-chunk-size", "10M",
                    "--socket-timeout", "30",
                ]
                self.progress_signal.emit(t("✓ 时间片段模式启用精简网络参数（避免节流检测与无效并发）"))
            else:
                optimization_options = [
                    # 重试机制优化 - 基于官方文档推荐，减少重试次数以加快失败恢复
                    "--retries", "5",  # 减少重试次数，加快失败恢复
                    "--fragment-retries", "3",  # 减少片段重试次数
                    "--file-access-retries", "2",  # 减少文件访问重试
                    "--retry-sleep", "exp=0.5:3:1.5",  # 使用指数退避，减少延迟
                    
                    # 高性能网络配置 - 基于官方文档优化
                    "--buffer-size", "1024K",  # 增大缓冲区，提高下载效率
                    "--http-chunk-size", "10M",  # 增大HTTP块大小，提高传输效率
                    # B站 CDN 对高并发分片易限流，降为 3；其余平台保持 6
                    "--concurrent-fragments", ("3" if any(d in self.url.lower() for d in ['bilibili.com', 'b23.tv', 'acg.tv']) else "6"),  # 增加并发数，提高下载速度
                    
                    # 网络超时优化 - 基于官方推荐，减少超时时间
                    "--socket-timeout", "30",  # 减少超时时间，加快失败检测
                    
                    # 启用throttled-rate检测
                    "--throttled-rate", "100K",  # 当速度低于100KB/s时重新提取
                    
                    # 兼容性和稳定性选项 - 基于官方最佳实践
                    "--force-ipv4",             # 强制使用IPv4，避免IPv6问题
                    "--no-check-certificates",  # 不检查SSL证书，提高兼容性
                    "--prefer-insecure",        # 优先使用不安全连接获取信息
                    "--legacy-server-connect",  # 支持旧版服务器连接
                    
                    # 下载行为优化
                    "--no-part",                # 不使用.part文件，直接写入
                    "--no-mtime",               # 不使用Last-modified头设置文件时间
                    "--hls-use-mpegts",         # HLS使用mpegts容器
                    "--no-lazy-playlist",       # 禁用懒惰播放列表处理
                    
                    # 注意：不添加全局format-sort参数，避免与分辨率选择器冲突
                ]
            
            # 检查并添加aria2c外部下载器支持（时间片段模式禁用，避免与 ffmpeg 分段冲突）
            if (not time_section_mode) and self._check_aria2c_available_in_thread():
                optimization_options.extend([
                    "--downloader", "aria2c",
                    "--downloader-args", "aria2c:-x 8 -s 8 -k 1M"
                ])
                print("DEBUG: DownloadThread启用aria2c外部下载器")
            
            # 如果是中国平台，添加额外的网络优化和反爬策略
            if is_china_platform:
                optimization_options.extend([
                    # 反爬虫策略 - 基于官方文档推荐
                    "--sleep-requests", "1",            # 请求间隔，避免被反爬
                    "--max-sleep-interval", "5",        # 最大睡眠间隔
                    "--min-sleep-interval", "1",        # 最小睡眠间隔
                    
                    # 中国网络环境特殊优化
                    "--geo-bypass",                     # 尝试绕过地理限制
                    "--geo-bypass-country", "CN",       # 设置绕过国家为中国
                    "--extractor-retries", "3",         # 增加提取器重试次数
                ])
                self.progress_signal.emit(t("✓ 已为中国平台添加额外网络优化和反爬策略"))
            
            cmd.extend(optimization_options)
            # 移除不建议默认启用的选项，确保与官方README更一致
            try:
                risky_flags = {
                    "--no-check-certificates",
                    "--prefer-insecure",
                    "--legacy-server-connect",
                    "--no-part",
                    "--no-lazy-playlist",
                    "--force-ipv4",
                    "--no-mtime",
                }
                cmd = [arg for arg in cmd if arg not in risky_flags]
            except Exception:
                pass
            self.progress_signal.emit(t("✓ 已应用yt-dlp高性能优化参数（基于官方最佳实践）"))

            # 检查高级选项是否启用 - 所有新功能都需要高级选项启用才能生效
            advanced_enabled = (self.main_app and 
                              hasattr(self.main_app, 'advanced_group') and 
                              self.main_app.advanced_group.isChecked())
            
            if not advanced_enabled:
                self.progress_signal.emit(t("⚠️ 高级选项未启用，跳过所有新功能"))
            
            # 添加元数据下载选项 (2025年新功能) - 需要高级选项启用
            metadata_options = []
            if advanced_enabled and self.main_app and hasattr(self.main_app, 'write_description_check') and self.main_app.write_description_check.isChecked():
                metadata_options.extend(["--write-description"])
                self.progress_signal.emit(t("✓ 启用视频描述下载"))
            
            if advanced_enabled and self.main_app and hasattr(self.main_app, 'write_info_check') and self.main_app.write_info_check.isChecked():
                metadata_options.extend(["--write-info-json"])
                self.progress_signal.emit(t("✓ 启用JSON元数据下载"))
            
            if advanced_enabled and self.main_app and hasattr(self.main_app, 'write_comments_check') and self.main_app.write_comments_check.isChecked():
                metadata_options.extend(["--write-comments"])
                self.progress_signal.emit(t("✓ 启用视频评论下载"))
            
            if advanced_enabled and self.main_app and hasattr(self.main_app, 'embed_metadata_check') and self.main_app.embed_metadata_check.isChecked():
                metadata_options.extend(["--embed-metadata"])
                self.progress_signal.emit(t("✓ 启用元数据嵌入"))
            
            if advanced_enabled and self.main_app and hasattr(self.main_app, 'embed_thumbnail_check') and self.main_app.embed_thumbnail_check.isChecked():
                metadata_options.extend(["--embed-thumbnail"])
                self.progress_signal.emit(t("✓ 启用缩略图嵌入"))
            
            # 注意：embed_chapters_check、embed_info_json_check、xattrs_check 
            # 现在统一在 get_additional_options() 中处理，避免重复添加
            
            if metadata_options:
                cmd.extend(metadata_options)
                self.progress_signal.emit(f"✓ 已应用 {len(metadata_options)} 个元数据选项")
            
            # 添加SponsorBlock支持 - 与官方yt-dlp完全一致
            if advanced_enabled and self.main_app and hasattr(self.main_app, 'sponsorblock_check') and self.main_app.sponsorblock_check.isChecked():
                sponsorblock_remove_categories = []
                sponsorblock_mark_categories = []
                
                # 收集需要移除的类别
                if hasattr(self.main_app, 'sponsorblock_sponsor_check') and self.main_app.sponsorblock_sponsor_check.isChecked():
                    sponsorblock_remove_categories.append('sponsor')
                
                if hasattr(self.main_app, 'sponsorblock_intro_check') and self.main_app.sponsorblock_intro_check.isChecked():
                    sponsorblock_remove_categories.append('intro')
                
                if hasattr(self.main_app, 'sponsorblock_outro_check') and self.main_app.sponsorblock_outro_check.isChecked():
                    sponsorblock_remove_categories.append('outro')
                
                if hasattr(self.main_app, 'sponsorblock_selfpromo_check') and self.main_app.sponsorblock_selfpromo_check.isChecked():
                    sponsorblock_remove_categories.append('selfpromo')
                
                # 添加其他可用的SponsorBlock类别
                if hasattr(self.main_app, 'sponsorblock_preview_check') and self.main_app.sponsorblock_preview_check.isChecked():
                    sponsorblock_remove_categories.append('preview')
                
                if hasattr(self.main_app, 'sponsorblock_filler_check') and self.main_app.sponsorblock_filler_check.isChecked():
                    sponsorblock_remove_categories.append('filler')
                
                if hasattr(self.main_app, 'sponsorblock_interaction_check') and self.main_app.sponsorblock_interaction_check.isChecked():
                    sponsorblock_remove_categories.append('interaction')
                
                if hasattr(self.main_app, 'sponsorblock_music_offtopic_check') and self.main_app.sponsorblock_music_offtopic_check.isChecked():
                    sponsorblock_remove_categories.append('music_offtopic')
                
                # 应用SponsorBlock选项
                if sponsorblock_remove_categories:
                    cmd.extend(["--sponsorblock-remove", ','.join(sponsorblock_remove_categories)])
                    self.progress_signal.emit(f"✓ SponsorBlock移除类型: {', '.join(sponsorblock_remove_categories)}")
                
                # 检查是否需要标记章节（而不是移除）
                if hasattr(self.main_app, 'sponsorblock_mark_chapters') and self.main_app.sponsorblock_mark_chapters.isChecked():
                    if sponsorblock_remove_categories:
                        # 为标记使用相同的类别
                        cmd.extend(["--sponsorblock-mark", ','.join(sponsorblock_remove_categories)])
                        self.progress_signal.emit(f"✓ SponsorBlock标记章节: {', '.join(sponsorblock_remove_categories)}")
                    else:
                        # 默认标记所有类别
                        cmd.extend(["--sponsorblock-mark", "all"])
                        self.progress_signal.emit(t("✓ SponsorBlock标记所有类别"))
                
                # 如果没有选择任何类别，使用默认设置
                if not sponsorblock_remove_categories and not (hasattr(self.main_app, 'sponsorblock_mark_chapters') and self.main_app.sponsorblock_mark_chapters.isChecked()):
                    cmd.extend(["--sponsorblock-remove", "sponsor"])
                    self.progress_signal.emit(t("✓ SponsorBlock默认移除赞助商内容"))
                
                # 添加SponsorBlock API设置（如果有自定义）
                if hasattr(self.main_app, 'sponsorblock_api_url') and self.main_app.sponsorblock_api_url.text().strip():
                    api_url = self.main_app.sponsorblock_api_url.text().strip()
                    cmd.extend(["--sponsorblock-api", api_url])
                    self.progress_signal.emit(f"✓ 自定义SponsorBlock API: {api_url}")
                
                # 添加章节标题模板（如果有自定义）
                if hasattr(self.main_app, 'sponsorblock_chapter_title') and self.main_app.sponsorblock_chapter_title.text().strip():
                    title_template = self.main_app.sponsorblock_chapter_title.text().strip()
                    cmd.extend(["--sponsorblock-chapter-title", title_template])
                    self.progress_signal.emit(f"✓ SponsorBlock章节标题模板: {title_template}")
            
            # 添加章节支持选项 - 与官方yt-dlp完全一致
            if advanced_enabled and self.main_app:
                # 注意：--download-sections 已在 get_proxy_options() 中处理，这里不再重复添加
                # 这里只保留章节相关的其他选项
                
                # 按章节分割视频
                if hasattr(self.main_app, 'split_chapters_check') and self.main_app.split_chapters_check.isChecked():
                    cmd.extend(["--split-chapters"])
                    self.progress_signal.emit(t("✓ 启用按章节分割视频 (--split-chapters)"))
                
                # 移除匹配的章节（正则表达式）
                if hasattr(self.main_app, 'chapters_to_remove') and self.main_app.chapters_to_remove:
                    regex = "|".join(self.main_app.chapters_to_remove)
                    if regex:
                        cmd.extend(["--remove-chapters", regex])
                        self.progress_signal.emit(f"✓ 移除匹配章节: {regex} (--remove-chapters)")
                
                # 在切割点强制关键帧
                if hasattr(self.main_app, 'force_keyframes_check') and self.main_app.force_keyframes_check.isChecked():
                    cmd.extend(["--force-keyframes-at-cuts"])
                    self.progress_signal.emit(t("✓ 启用强制关键帧（提高切割质量）"))
            
            # 添加高级下载选项 - 与官方yt-dlp完全一致
            if advanced_enabled and self.main_app:
                # 检查特殊场景下载主控选项是否启用
                special_scene_enabled = (hasattr(self.main_app, 'special_scene_master_check') and 
                                     self.main_app.special_scene_master_check.isChecked())
                
                if special_scene_enabled:
                    # 从开头下载直播
                    if hasattr(self.main_app, 'live_from_start_check') and self.main_app.live_from_start_check.isChecked():
                        cmd.extend(["--live-from-start"])
                        self.progress_signal.emit(t("✓ 从开头下载直播"))
                    
                    # 等待视频可用
                    if hasattr(self.main_app, 'wait_for_video_check') and self.main_app.wait_for_video_check.isChecked():
                        wait_min = (
                            self.main_app.wait_for_video_time.value()
                            if hasattr(self.main_app, 'wait_for_video_time')
                            else 60
                        )
                        use_wait_range = (
                            hasattr(self.main_app, 'wait_for_video_use_range_check')
                            and self.main_app.wait_for_video_use_range_check.isChecked()
                        )
                        wait_max = (
                            self.main_app.wait_for_video_max_time.value()
                            if use_wait_range and hasattr(self.main_app, 'wait_for_video_max_time')
                            else None
                        )
                        wait_time = build_wait_for_video_arg(
                            wait_min,
                            use_range=use_wait_range,
                            max_seconds=wait_max,
                        )
                        cmd.extend(["--wait-for-video", wait_time])
                        if "-" in wait_time:
                            self.progress_signal.emit(f"✓ 等待视频可用（重试间隔 {wait_time} 秒）")
                        else:
                            self.progress_signal.emit(f"✓ 等待视频可用（重试间隔 {wait_time} 秒）")
                    
                    # 忽略错误继续下载
                    if hasattr(self.main_app, 'ignore_errors_check') and self.main_app.ignore_errors_check.isChecked():
                        cmd.extend(["--ignore-errors"])
                        self.progress_signal.emit(t("✓ 忽略错误继续下载"))
                    
                    # 片段可用性策略（主控启用时才显式设置）
                    if hasattr(self.main_app, 'skip_unavailable_fragments_check'):
                        skip_fragments = self.main_app.skip_unavailable_fragments_check.isChecked()
                        fragment_flag = fragment_unavailable_flag(skip_fragments)
                        cmd.append(fragment_flag)
                        if skip_fragments:
                            self.progress_signal.emit(t("✓ 跳过不可用的片段"))
                        else:
                            self.progress_signal.emit(t("✓ 遇到不可用片段时中止下载"))
                    
                    # 保留下载的片段
                    if hasattr(self.main_app, 'keep_fragments_check') and self.main_app.keep_fragments_check.isChecked():
                        cmd.extend(["--keep-fragments"])
                        self.progress_signal.emit(t("✓ 保留下载的片段"))
                else:
                    self.progress_signal.emit(t("⚠️ 特殊场景下载功能未启用，跳过相关选项"))
                
                # 播放列表排序参数已移除

            # 添加字幕下载选项 - 与官方yt-dlp完全一致
            if advanced_enabled and self.main_app:
                preview_langs = getattr(self.main_app, "_last_preview_subtitle_langs", [])
                # 下载字幕文件
                if hasattr(self.main_app, 'subtitle_check') and self.main_app.subtitle_check.isChecked():
                    cmd.extend(["--write-subs"])
                    self.progress_signal.emit(t("✓ 启用字幕下载"))
                    
                    # 字幕语言选择 - 使用用户在UI中选择的语言
                    if hasattr(self.main_app, 'subtitle_lang'):
                        selected_lang = self.main_app.subtitle_lang.currentData()
                        has_sub_langs = "--sub-langs" in cmd
                        if selected_lang and selected_lang != "all":
                            lang_pattern, normalized_lang = build_subtitle_lang_pattern(
                                selected_lang,
                                available_langs=preview_langs,
                            )
                            if not has_sub_langs:
                                cmd.extend(["--sub-langs", lang_pattern])
                                self.progress_signal.emit(f"✓ 字幕语言: {normalized_lang} (匹配: {lang_pattern})")
                            else:
                                self.progress_signal.emit(f"✓ 字幕语言沿用预设参数: {normalized_lang}")
                        elif selected_lang == "all":
                            lang_pattern, _ = build_subtitle_lang_pattern("all", available_langs=preview_langs)
                            if not has_sub_langs:
                                cmd.extend(["--sub-langs", lang_pattern])
                                self.progress_signal.emit(t("✓ 字幕语言: 全部语言（排除直播聊天）"))
                            else:
                                self.progress_signal.emit(t("✓ 字幕语言沿用预设参数: 全部语言"))
                        else:
                            default_langs, _ = build_subtitle_lang_pattern("", available_langs=preview_langs)
                            if not has_sub_langs:
                                cmd.extend(["--sub-langs", default_langs])
                                self.progress_signal.emit(t("✓ 默认字幕语言: 中文和英文"))
                            else:
                                self.progress_signal.emit(t("✓ 默认字幕语言沿用预设参数"))
                    else:
                        # 兼容旧版本：如果没有新的语言选择器，使用文本框
                        if hasattr(self.main_app, 'subtitle_langs') and self.main_app.subtitle_langs.text().strip():
                            langs = self.main_app.subtitle_langs.text().strip()
                            cmd.extend(["--sub-langs", langs])
                            self.progress_signal.emit(f"✓ 字幕语言: {langs}")
                        else:
                            default_langs, _ = build_subtitle_lang_pattern("", available_langs=preview_langs)
                            cmd.extend(["--sub-langs", default_langs])
                            self.progress_signal.emit(t("✓ 默认字幕语言: 中文和英文"))
                    
                    # 字幕格式选择
                    if hasattr(self.main_app, 'subtitle_format') and self.main_app.subtitle_format.currentData():
                        sub_format = self.main_app.subtitle_format.currentData()
                        cmd.extend(["--sub-format", sub_format])
                        self.progress_signal.emit(f"✓ 字幕格式: {sub_format}")
                    else:
                        # 默认优先srt格式
                        cmd.extend(["--sub-format", "srt/ass/vtt/best"])
                        self.progress_signal.emit(t("✓ 默认字幕格式: srt/ass/vtt/best"))
                
                # 下载自动生成字幕
                if hasattr(self.main_app, 'auto_subtitle_check') and self.main_app.auto_subtitle_check.isChecked():
                    cmd.extend(["--write-auto-subs"])
                    self.progress_signal.emit(t("✓ 启用自动生成字幕下载"))
                    
                    # 自动字幕也使用相同的语言和格式设置
                    if not (hasattr(self.main_app, 'subtitle_check') and self.main_app.subtitle_check.isChecked()):
                        # 如果没有启用普通字幕，则为自动字幕设置语言和格式
                        if hasattr(self.main_app, 'subtitle_lang'):
                            selected_lang = self.main_app.subtitle_lang.currentData()
                            has_sub_langs = "--sub-langs" in cmd
                            if selected_lang and selected_lang != "all":
                                lang_pattern, normalized_lang = build_subtitle_lang_pattern(
                                    selected_lang,
                                    available_langs=preview_langs,
                                )
                                if not has_sub_langs:
                                    cmd.extend(["--sub-langs", lang_pattern])
                                    self.progress_signal.emit(f"✓ 自动字幕语言: {normalized_lang} (匹配: {lang_pattern})")
                                else:
                                    self.progress_signal.emit(f"✓ 自动字幕语言沿用预设参数: {normalized_lang}")
                            else:
                                all_langs, _ = build_subtitle_lang_pattern("all", available_langs=preview_langs)
                                if not has_sub_langs:
                                    cmd.extend(["--sub-langs", all_langs])
                                    self.progress_signal.emit(t("✓ 自动字幕语言: 全部语言（排除直播聊天）"))
                                else:
                                    self.progress_signal.emit(t("✓ 自动字幕语言沿用预设参数: 全部语言"))
                        else:
                            default_langs, _ = build_subtitle_lang_pattern("", available_langs=preview_langs)
                            cmd.extend(["--sub-langs", default_langs])
                            self.progress_signal.emit(t("✓ 默认自动字幕语言: 中文或英语"))
                        
                        if hasattr(self.main_app, 'subtitle_format') and self.main_app.subtitle_format.currentData():
                            sub_format = self.main_app.subtitle_format.currentData()
                            cmd.extend(["--sub-format", sub_format])
                        else:
                            cmd.extend(["--sub-format", "srt/ass/vtt/best"])
                
                # 【修复】自动添加字幕格式转换：清除 WebVTT 内联时间标签
                # 只在以下条件满足时才自动转换：
                # 1. 至少启用了一种字幕下载（普通字幕或自动字幕）
                # 2. 用户未手动启用转换功能
                # 3. 用户选择的格式不是 ASS 或 TTML（这两种格式有自己的样式系统，不需要转换）
                subtitle_enabled = (hasattr(self.main_app, 'subtitle_check') and self.main_app.subtitle_check.isChecked()) or \
                                   (hasattr(self.main_app, 'auto_subtitle_check') and self.main_app.auto_subtitle_check.isChecked())
                
                if subtitle_enabled:
                    convert_subs_enabled = hasattr(self.main_app, 'convert_subtitle_check') and self.main_app.convert_subtitle_check.isChecked()
                    # 检查用户选择的字幕格式
                    user_format = None
                    if hasattr(self.main_app, 'subtitle_format'):
                        user_format = self.main_app.subtitle_format.currentData()
                    
                    # 判断是否需要自动转换清理内联标签
                    # ASS 和 TTML 有自己的样式系统，不需要转换
                    # VTT、best 和以 srt/ 开头的格式优先级可能返回带内联标签的内容，需要转换
                    is_ass_or_ttml = user_format in ("ass", "ttml") or (user_format and user_format.startswith("ass/"))
                    
                    should_auto_convert = not convert_subs_enabled and not is_ass_or_ttml
                    
                    if should_auto_convert:
                        # 自动添加转换参数清理字幕（将 VTT/best 等格式的内联标签转换掉）
                        cmd.extend(["--convert-subs", "srt"])
                        self.progress_signal.emit(t("✓ 自动转换字幕为 SRT 格式（清理内联时间标签）"))
            
            # 添加缩略图下载选项 - 与官方yt-dlp完全一致
            if advanced_enabled and self.main_app and hasattr(self.main_app, 'thumbnail_check') and self.main_app.thumbnail_check.isChecked():
                cmd.extend(["--write-thumbnail"])
                self.progress_signal.emit(t("✓ 启用缩略图下载"))
                
                # 缩略图格式选择
                if hasattr(self.main_app, 'thumbnail_format') and self.main_app.thumbnail_format.currentText():
                    thumb_format = self.main_app.thumbnail_format.currentText().lower()
                    if thumb_format != '默认':
                        cmd.extend(["--convert-thumbnails", thumb_format])
                        self.progress_signal.emit(f"✓ 缩略图转换格式: {thumb_format}")
            
            # 添加速度限制选项 - 与官方yt-dlp完全一致
            if advanced_enabled and self.main_app and hasattr(self.main_app, 'limit_speed_check') and self.main_app.limit_speed_check.isChecked():
                if hasattr(self.main_app, 'speed_limit') and self.main_app.speed_limit.value() > 0:
                    speed_limit = f"{self.main_app.speed_limit.value()}K"
                    cmd.extend(["--limit-rate", speed_limit])
                    self.progress_signal.emit(f"✓ 启用速度限制: {speed_limit}/s")
            
            # 注意：播放列表选项已在 get_additional_options() 中统一处理
            # 这里不再重复添加，避免冲突
            
            # 添加音频语言优先选择排序（如果是自动选择原始语言模式）
            if self.main_app and hasattr(self.main_app, 'get_audio_language_code'):
                audio_lang_code = self.main_app.get_audio_language_code()
                if audio_lang_code == "orig":
                    # 自动选择原始语言：使用排序优先选择原始语言音频
                    # 优先选择原始语言，然后是英语，最后是中文
                    cmd.extend(["-S", "lang,quality,acodec:aac:m4a"])
                    self.progress_signal.emit(t("✓ 启用音频语言优先排序（原始语言优先）"))
                elif audio_lang_code:
                    # 选择了特定音频语言时，优先兼容性更好的视频/音频编码组合，减少播放器卡顿
                    # 注意：不使用 --format-sort-force，因为它会覆盖 -f 中的语言过滤器
                    cmd.extend(["-S", "vcodec:h264,res,fps,acodec:aac:mp4a,m4a"])
                    self.progress_signal.emit(t("✓ 启用音频语言过滤 + 兼容排序（优先 H.264/AAC）"))
            
            # 添加进度显示格式 - 优化中文显示
            cmd.extend(["--progress-template", "download:[%(progress.downloaded_bytes)s/%(progress.total_bytes)s] %(progress.percent)s 速度:%(progress.speed)s ETA:%(progress.eta)s"])
            
            # 添加URL
            cmd.append(self.url)
            
            self.progress_signal.emit(f"执行命令: {' '.join(cmd)}")
            self.progress_signal.emit(f"命令参数数量: {len(cmd)}")
            self.progress_signal.emit(f"输出路径: {self.output_path}")
            
            # 记录下载开始前的文件列表（用于准确统计实际下载的文件）
            try:
                if os.path.exists(self.output_path) and os.path.isdir(self.output_path):
                    for root, dirs, files in os.walk(self.output_path):
                        for file in files:
                            # 只记录视频/音频文件（排除临时文件、元数据文件和字幕文件）
                            file_ext = os.path.splitext(file)[1].lower()
                            if file_ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', 
                                          '.m4a', '.mp3', '.aac', '.opus', '.flac', '.wav']:
                                # 排除临时文件
                                if not file.endswith(('.part', '.ytdl', '.temp')):
                                    file_path = os.path.join(root, file)
                                    self.download_start_files.add(file_path)
                    self.progress_signal.emit(f"📊 检测到现有文件: {len(self.download_start_files)} 个")
            except Exception as e:
                self.progress_signal.emit(f"⚠️ 记录现有文件时出错: {e}")
            
            # 执行命令
            try:
                # 【关键修复】记录进程开始时间，用于统计实际下载的文件
                self.process_start_time = time.time()
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                self.progress_signal.emit(t("✓ 进程启动成功，开始监控输出..."))
            except Exception as e:
                self.progress_signal.emit(f"✗ 启动进程失败: {str(e)}")
                self.finished_signal.emit(False, f"启动下载进程失败: {str(e)}")
                return
            
            # 读取输出并检测格式错误
            error_output = ""
            # 用于解析下载进度的正则表达式，支持整数和小数百分比
            progress_pattern = re.compile(r'(\d+(?:\.\d+)?)%')
            # 改进下载信息匹配模式，支持更多格式变体和单位
            downloaded_pattern = re.compile(r'下载中: ([\d.]+[KMGT]?i?B)/([\d.]+[KMGT]?i?B)')
            # 备用下载信息匹配模式，处理可能的格式变化
            alt_downloaded_pattern = re.compile(r'([\d.]+[KMGT]?i?B)\s+of\s+([\d.]+[KMGT]?i?B)')
            speed_pattern = re.compile(r'速度: ([\d.]+[KMGT]?i?B/s)')
            eta_pattern = re.compile(r'\(([\d:]+)\)')
            
            # 添加调试信息
            self.progress_signal.emit(t("已初始化进度解析正则表达式，开始监控下载进度..."))
            
            # 添加上次进度更新时间记录，避免过于频繁的更新
            last_progress_update = time.time()
            last_percent = 0
            
            # 添加下载开始时间记录，用于在没有进度信息时估算进度
            download_start_time = time.time()
            # 确保在开始时发送一个非常小的初始进度信号，避免进度条直接跳跃
            self.progress_percent_signal.emit(0.01)
            self.progress_sent = True
            
            # 添加一个变量来跟踪上次发送的估算进度
            last_estimated_progress = 0.01
            
            # 【修复】时间片段下载进度跟踪
            # 检测是否启用了时间片段下载（YouTube等需要分别下载视频和音频流，然后ffmpeg裁剪合并）
            time_section_download_enabled = False
            if self.main_app and hasattr(self.main_app, 'time_section_check') and self.main_app.time_section_check.isChecked():
                if hasattr(self.main_app, 'download_sections') and self.main_app.download_sections:
                    time_section_download_enabled = True
                    self.progress_signal.emit(t("⏱️ 检测到时间片段下载模式，将跟踪多流下载和后处理进度"))
            
            # 时间片段下载的进度跟踪变量
            ts_stream_count = 0  # 已检测到的流数量（通过Destination行计数）
            ts_stream_100_count = 0  # 已完成100%的流数量
            ts_postprocess_started = False  # 后处理是否已开始
            ts_postprocess_completed = False  # 后处理是否已完成
            ts_expected_streams = 2  # 预期的流数量（通常是视频+音频=2）
            ts_last_destination = ""  # 上一个Destination文件路径

            for line_bytes in self.process.stdout:
                line = decode_subprocess_bytes(line_bytes)
                # 首先检查是否被取消，如果是则立即终止进程并退出循环
                if hasattr(self, '_cancelled') and self._cancelled:
                    self.progress_signal.emit(t("🔴 检测到下载已被取消，正在终止进程..."))
                    if self.process:
                        try:
                            self.process.terminate()
                            self.process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            self.progress_signal.emit(t("⚠️ 进程终止超时，使用强制终止"))
                            self.process.kill()
                        except Exception as e:
                            self.progress_signal.emit(f"⚠️ 终止进程时出错: {str(e)}")
                    self.progress_signal.emit(t("✅ 下载进程已终止"))
                    self.cancel_signal.emit()
                    return
                    
                # 检查进程是否被外部终止
                if self.process and self.process.poll() is not None:
                    self.progress_signal.emit(t("检测到进程已终止，退出下载循环"))
                    break
                elif not self.process:
                    self.progress_signal.emit(t("检测到进程已终止，退出下载循环"))
                    break
                    
                stripped_line = line.strip()
                self.progress_signal.emit(stripped_line)
                
                # 【修复】时间片段下载模式：检测ffmpeg后处理阶段
                if time_section_download_enabled:
                    # 检测后处理开始的标志
                    # yt-dlp 在后处理时会输出类似 [Merger], [FixupM3u8], [ModifyChapters], [ffmpeg] 等
                    postprocess_start_patterns = [
                        "[Merger]", "[FixupM3u8]", "[FixupM4a]", "[FixupDuration]",
                        "[ModifyChapters]", "[ffmpeg]", "[ExtractAudio]",
                        "Merging formats into", "Fixing", "Post-process"
                    ]
                    if any(pattern in stripped_line for pattern in postprocess_start_patterns):
                        if not ts_postprocess_started:
                            ts_postprocess_started = True
                            self.progress_signal.emit(t("⏱️ 时间片段下载: ffmpeg后处理开始"))
                            # 后处理开始时，进度设为80%
                            self.progress_percent_signal.emit(80.0)
                            last_percent = 80.0
                    
                    # 检测后处理完成的标志
                    # 通常是 "Deleting original file" 或文件写入完成
                    postprocess_complete_patterns = [
                        "Deleting original file",
                        "has already been recorded in the archive",
                        "Writing video subtitles",  # 字幕写入通常是最后步骤之一
                    ]
                    if any(pattern in stripped_line for pattern in postprocess_complete_patterns):
                        if ts_postprocess_started and not ts_postprocess_completed:
                            ts_postprocess_completed = True
                            self.progress_signal.emit(t("⏱️ 时间片段下载: 后处理完成"))
                            # 后处理完成，进度设为95%（留5%给最终验证）
                            self.progress_percent_signal.emit(95.0)
                            last_percent = 95.0
                
                # 提取播放列表总数
                if "Playlist" in stripped_line and "Downloading" in stripped_line and "items of" in stripped_line:
                    match = re.search(r'Downloading\s+(\d+)\s+items?\s+of\s+(\d+)', stripped_line)
                    if match:
                        self.playlist_total_count = int(match.group(2))
                        self.progress_signal.emit(f"📊 检测到播放列表总共 {self.playlist_total_count} 个视频")
                
                # 提取当前视频ID
                if "[youtube] Extracting URL:" in stripped_line or re.match(r'\[youtube\]\s+[a-zA-Z0-9_-]+:', stripped_line):
                    # 从 "[youtube] Extracting URL: https://www.youtube.com/watch?v=xxxxx" 提取
                    if "Extracting URL:" in stripped_line and "watch?v=" in stripped_line:
                        match = re.search(r'watch\?v=([a-zA-Z0-9_-]+)', stripped_line)
                        if match:
                            self.current_video_id = match.group(1)
                            self.current_video_title = self._get_default_single_title()  # 单视频保留已知真实标题
                    # 从 "[youtube] xxxxx: Downloading webpage" 提取
                    elif not "Extracting URL:" in stripped_line:
                        match = re.match(r'\[youtube\]\s+([a-zA-Z0-9_-]+):', stripped_line)
                        if match:
                            self.current_video_id = match.group(1)
                
                # 检测跳过的视频（日期过滤等）
                if "upload date is not in range" in stripped_line:
                    self.playlist_skipped_count += 1
                    # 【精准统计修复】由于日期过滤跳过的视频，应该从预计下载数中扣除
                    # 因为这些视频不会被尝试下载，不应该算在预计下载数中
                    if self.playlist_enabled and self.expected_download_count > 0:
                        # 只有当预计下载数已经被设置且大于0时才调整
                        # 这样可以更准确地反映实际会尝试下载的视频数量
                        self.progress_signal.emit(f"📊 日期过滤跳过的视频不计入预计下载数")
                
                # 捕获播放列表中视频的标题和错误信息
                # 检测当前正在下载的视频
                if "[download] Downloading item" in stripped_line or "[download] Downloading video" in stripped_line:
                    # 提取视频序号
                    match = re.search(r'(\d+)\s+of\s+(\d+)', stripped_line)
                    if match:
                        current = int(match.group(1))
                        total = int(match.group(2))
                        
                        # 【关键修复】保存当前下载的索引，用于范围判断
                        self.current_downloading_index = current
                        
                        # 【关键修复】total是播放列表的总大小，但我们需要区分不同情况
                        self.total_videos = total  # 保存总播放列表大小用于显示
                        
                        # 【精准统计修复】只有当用户没有设置预期数量时，才使用解析的总数
                        # 如果用户通过--playlist-items设置了范围，必须保留该设置，不能覆盖
                        if self.playlist_enabled:
                            if self.expected_download_count == 0:
                                # 用户没有设置范围，使用总数
                                self.expected_download_count = total
                                self.progress_signal.emit(f"📊 更新预期下载数量: {self.expected_download_count} (从播放列表解析)")
                            elif self.expected_download_count != total and total > 0 and not self.has_playlist_items_set:
                                # 只有当用户没有设置--playlist-items时，才记录警告
                                self.progress_signal.emit(f"📊 注意：用户设置预期数量 {self.expected_download_count}，实际播放列表有 {total} 个视频")
                        
                        # 【关键修复】显示进度时，使用用户设置的范围作为总数
                        display_total = self.expected_download_count if self.has_playlist_items_set and self.expected_download_count > 0 else total
                        self.progress_signal.emit(f"📹 正在下载第 {current}/{display_total} 个视频 (范围内)")
                
                # 检测视频标题
                if "[download] Destination:" in stripped_line or "Destination:" in stripped_line:
                    # 【修复】时间片段下载模式：跟踪流数量
                    if time_section_download_enabled:
                        # 从Destination行提取文件路径，检测是否是新的流
                        if "Destination:" in stripped_line:
                            dest_path = stripped_line.split("Destination:", 1)[1].strip()
                            # 检查是否是不同的文件（视频流和音频流通常有不同的文件名）
                            if dest_path != ts_last_destination:
                                ts_last_destination = dest_path
                                ts_stream_count += 1
                                self.progress_signal.emit(f"⏱️ 时间片段下载: 检测到第 {ts_stream_count} 个流")
                    
                    # 从Destination行提取文件路径
                    try:
                        # 提取路径
                        if "Destination:" in stripped_line:
                            path_part = stripped_line.split("Destination:", 1)[1].strip()
                            
                            # 【新增】将正在下载的文件路径保存，用于取消时清理
                            if path_part and os.path.isabs(path_part):
                                self.current_downloading_files.add(path_part)
                            elif path_part and self.output_path:
                                # 如果是相对路径，转换为绝对路径
                                full_path = os.path.join(self.output_path, path_part)
                                self.current_downloading_files.add(full_path)
                            
                            # 尝试从实际文件系统获取文件名（避免编码问题）
                            if self.output_path and os.path.exists(self.output_path):
                                # 查找最近创建的文件
                                try:
                                    current_time = time.time()
                                    recent_files = []
                                    for root, dirs, files in os.walk(self.output_path):
                                        for filename in files:
                                            file_path = os.path.join(root, filename)
                                            try:
                                                ctime = os.path.getctime(file_path)
                                                # 只考虑最近5秒内创建的文件
                                                if current_time - ctime < 5:
                                                    # 排除缩略图等辅助文件
                                                    if not any(x in filename.lower() for x in ['thumbnail', 'thumb', '.jpg', '.jpeg', '.png', '.webp']):
                                                        recent_files.append((filename, ctime))
                                            except:
                                                continue
                                    
                                    # 找到最新的文件
                                    if recent_files:
                                        recent_files.sort(key=lambda x: x[1], reverse=True)
                                        latest_filename = recent_files[0][0]
                                        # 移除扩展名和可能的格式后缀（如.f135.mp4中的.f135）
                                        title = cleanup_download_title(latest_filename)
                                        self.current_video_title = choose_better_download_title(
                                            title,
                                            self.current_video_title,
                                        )
                                    else:
                                        # 如果没找到文件，使用从输出解析的标题（可能有乱码）
                                        filename = os.path.basename(path_part)
                                        self.current_video_title = choose_better_download_title(
                                            filename,
                                            self.current_video_title,
                                        )
                                except:
                                    # 出错时使用从输出解析的标题
                                    filename = os.path.basename(path_part)
                                    self.current_video_title = choose_better_download_title(
                                        filename,
                                        self.current_video_title,
                                    )
                            else:
                                # 如果输出路径不存在，使用从输出解析的标题
                                filename = os.path.basename(path_part)
                                self.current_video_title = choose_better_download_title(
                                    filename,
                                    self.current_video_title,
                                )
                    except Exception:
                        pass
                
                # 检测错误和跳过的视频
                # 注意：排除章节处理相关的正常消息（如 SponsorBlock、章节跳过等）
                error_keywords = [
                    "ERROR:", "Error:",
                    "unavailable", "Unavailable",
                    "Private video", "private video",
                    "Video unavailable", "video unavailable",
                    "Deleted video", "deleted video",
                    "has already been downloaded",
                    "Premium content", "premium content",
                    "Members-only", "members-only",
                    "This video is only available",
                    "Sign in to confirm your age",
                    "age-restricted",
                    "Geographic restriction",
                    "This video contains content from",
                    "blocked it in your country",
                    "Requested format is not available"
                ]
                
                # 排除章节处理相关的正常消息（不应被视为错误）
                chapter_processing_keywords = [
                    "Removing chapters",
                    "Removing chapter",
                    "SponsorBlock",
                    "sponsor segment",
                    "chapters have been marked",
                    "Split video by chapters"
                ]
                
                # 只有当包含错误关键词，且不是章节处理消息时，才认为是错误
                is_error = (any(keyword in stripped_line for keyword in error_keywords) and
                           not any(chapter_keyword in stripped_line for chapter_keyword in chapter_processing_keywords) and
                           # 重试中/等待中的瞬时网络消息不算最终失败（避免误报"记录失败视频"）
                           "Retrying" not in stripped_line and "Sleeping" not in stripped_line)
                
                # 【关键修复】即使没有current_video_title，也要尝试记录失败（可能是限制等原因）
                if is_error:
                    # 提取错误原因
                    error_reason = "未知错误"
                    
                    if "unavailable" in stripped_line.lower():
                        error_reason = "视频不可用"
                    elif "private" in stripped_line.lower():
                        error_reason = "私人视频"
                    elif "deleted" in stripped_line.lower():
                        error_reason = "视频已删除"
                    elif "already been downloaded" in stripped_line.lower():
                        error_reason = "已下载过（跳过）"
                        # 已下载过的不算失败，跳过记录
                        is_error = False
                    elif "premium" in stripped_line.lower() or "members-only" in stripped_line.lower():
                        error_reason = "会员专享内容"
                    elif "age" in stripped_line.lower() and "restrict" in stripped_line.lower():
                        error_reason = "年龄限制"
                    elif "geographic" in stripped_line.lower() or "country" in stripped_line.lower():
                        error_reason = "地区限制"
                    elif "blocked" in stripped_line.lower():
                        error_reason = "版权限制"
                    elif "format" in stripped_line.lower() and "not available" in stripped_line.lower():
                        error_reason = "请求的格式不可用"
                    elif "ERROR" in stripped_line:
                        # 尝试从ERROR消息中提取更具体的原因
                        if ":" in stripped_line:
                            reason_extract = stripped_line.split(":", 1)[1].strip()[:100]  # 限制长度
                            error_reason = reason_extract if reason_extract else "未知错误"
                    
                    # 记录失败的视频（如果确实是错误）
                    if is_error:
                        # 尝试从错误消息中提取视频ID
                        video_id = self.current_video_id
                        if not video_id and "ERROR:" in stripped_line:
                            # 尝试从 "ERROR: [youtube] xxxxx: ..." 提取
                            id_match = re.search(r'\[youtube\]\s+([a-zA-Z0-9_-]+):', stripped_line)
                            if id_match:
                                video_id = id_match.group(1)
                        
                        # 尝试从当前行提取视频ID（如果没有）
                        if not video_id:
                            if "watch?v=" in stripped_line:
                                url_match = re.search(r'watch\?v=([a-zA-Z0-9_-]+)', stripped_line)
                                if url_match:
                                    video_id = url_match.group(1)
                            
                            id_match = re.search(r'\[youtube\]\s+([a-zA-Z0-9_-]+):', stripped_line)
                            if id_match:
                                video_id = id_match.group(1)
                        
                        # 【关键修复】清理标题中的临时文件标识，避免记录临时文件名
                        video_title_clean = self.current_video_title if self.current_video_title else ""
                        if video_title_clean:
                            # 移除临时格式标识（如 .f135, .f140）
                            video_title_clean = re.sub(r'\.f\d+$', '', video_title_clean)
                            # 如果清理后的标题仍然包含临时文件扩展名，则不使用
                            if video_title_clean.endswith(('.m4a', '.mp4', '.webm', '.mkv')):
                                # 这可能是临时文件名，移除扩展名
                                video_title_clean = os.path.splitext(video_title_clean)[0]
                        
                        # 【精准统计修复】如果没有标题，尝试构造一个标识
                        # 对于某些错误（如年龄限制），yt-dlp无法提前获取视频标题
                        if not video_title_clean or video_title_clean.endswith(('.m4a', '.mp4', '.webm', '.mkv', '.temp', '.part')):
                            # 首先尝试从播放列表映射中获取标题
                            if video_id and video_id in self.playlist_video_map:
                                video_title_clean = self.playlist_video_map[video_id]
                                self.progress_signal.emit(f"✅ 从播放列表映射中获取到失败视频标题: {video_title_clean}")
                            elif video_id:
                                video_title_clean = f"[ID: {video_id}] - 无法获取标题"
                            else:
                                # 尝试从错误原因构造描述
                                video_title_clean = f"未知视频 - {error_reason}"
                        
                        # 优先使用清理后的标题，如果没有则使用视频ID
                        video_identifier = video_title_clean if video_title_clean else (video_id if video_id else f"未知视频 (#{len(self.failed_videos) + 1})")
                        
                        failed_video = {
                            'id': video_id,
                            'title': video_identifier,
                            'reason': error_reason,
                            'full_error': stripped_line[:200] if stripped_line else error_reason  # 保存完整错误信息（限制长度）
                        }
                        
                        # 避免重复记录同一个视频（使用视频ID去重）
                        if video_id:
                            print(f"[调试] 检查失败视频重复性 - video_id: {video_id}, 当前失败列表: {[v.get('id') for v in self.failed_videos]}")
                            is_duplicate = any(v.get('id') == video_id for v in self.failed_videos)
                            if not is_duplicate:
                                self.failed_videos.append(failed_video)
                                print(f"[调试] 添加失败视频到列表 - ID: {video_id}, 标题: {video_identifier}, 原因: {error_reason}")
                                print(f"[调试] 当前失败列表长度: {len(self.failed_videos)}")
                                self.playlist_processed_count += 1  # 失败的视频也算作已处理
                                # 【精准统计修复】如果标识符已经包含ID信息，不再重复附加
                                if f"(ID: {video_id})" in video_identifier or video_identifier.startswith("视频 (ID:"):
                                    self.progress_signal.emit(f"⚠️ 记录失败视频: {video_identifier} - {error_reason}")
                                else:
                                    self.progress_signal.emit(f"⚠️ 记录失败视频: {video_identifier} (ID: {video_id}) - {error_reason}")
                                
                                # 【关键修复】计算并发送播放列表进度百分比
                                # 【修复】时间片段下载模式下不发送播放列表进度
                                if self.expected_download_count > 0 and not time_section_download_enabled:
                                    progress_percent = (self.playlist_processed_count / self.expected_download_count) * 100
                                    self.progress_percent_signal.emit(progress_percent)
                                    self.progress_signal.emit(f"📊 播放列表进度: {self.playlist_processed_count}/{self.expected_download_count} ({progress_percent:.1f}%)")
                            else:
                                self.progress_signal.emit(f"📊 跳过重复失败记录: {video_identifier} (ID: {video_id})")
                                print(f"[调试] 跳过重复失败记录 - video_id: {video_id} 已存在")
                        else:
                            # 如果没有ID，使用标题和原因去重
                            print(f"[调试] 检查失败视频重复性 - 无video_id, video_identifier: {video_identifier}, 当前失败列表标题: {[v.get('title') for v in self.failed_videos]}")
                            is_duplicate = any(v['title'] == video_identifier and v['reason'] == error_reason for v in self.failed_videos)
                            if not is_duplicate:
                                self.failed_videos.append(failed_video)
                                print(f"[调试] 添加失败视频到列表 - 无ID, 标题: {video_identifier}, 原因: {error_reason}")
                                print(f"[调试] 当前失败列表长度: {len(self.failed_videos)}")
                                self.playlist_processed_count += 1  # 失败的视频也算作已处理
                                self.progress_signal.emit(f"⚠️ 记录失败视频: {video_identifier} - {error_reason}")
                                
                                # 【关键修复】计算并发送播放列表进度百分比
                                # 【修复】时间片段下载模式下不发送播放列表进度
                                if self.expected_download_count > 0 and not time_section_download_enabled:
                                    progress_percent = (self.playlist_processed_count / self.expected_download_count) * 100
                                    self.progress_percent_signal.emit(progress_percent)
                                    self.progress_signal.emit(f"📊 播放列表进度: {self.playlist_processed_count}/{self.expected_download_count} ({progress_percent:.1f}%)")
                            else:
                                self.progress_signal.emit(f"📊 跳过重复失败记录: {video_identifier}")
                                print(f"[调试] 跳过重复失败记录 - 标题: '{video_identifier}' 已存在")
                
                # 检测下载成功 - 改为基于实际文件名来去重统计
                if "[download] 100%" in stripped_line or "has already been downloaded" in stripped_line:
                    # 【修复】时间片段下载模式：不要在这里发送100%进度
                    # 因为可能还有其他流需要下载，以及ffmpeg后处理
                    if time_section_download_enabled:
                        # 记录流完成，递增完成计数
                        ts_stream_100_count += 1
                        self.progress_signal.emit(f"⏱️ 时间片段下载: 第 {ts_stream_100_count} 个流下载完成 (100%)")
                        # 更新进度：每个流占40%
                        stream_progress = min(80, ts_stream_100_count * 40.0)
                        if stream_progress > last_percent:
                            self.progress_percent_signal.emit(stream_progress)
                            last_percent = stream_progress
                            self.progress_signal.emit(f"⏱️ 时间片段进度: {stream_progress:.1f}% (已完成 {ts_stream_100_count} 个流)")
                    
                    # 尝试从消息中提取文件名
                    # yt-dlp 通常会显示 "[download] 100% of 10.5MiB in 00:05" 或者 "Destination: xxx.mp4"
                    # 我们需要找到实际的文件名
                    try:
                        # 检查最近的 Destination 行是否提供了文件路径
                        if self.current_video_title or self.current_video_id:
                            video_identifier = self.current_video_title if self.current_video_title else (f"ID: {self.current_video_id}" if self.current_video_id else "")
                            
                            # 如果当前视频在失败列表中，将其移除（说明实际下载成功了）
                            if self.failed_videos:
                                # 使用视频ID或标题来匹配失败列表中的条目
                                for i in range(len(self.failed_videos) - 1, -1, -1):
                                    failed_video = self.failed_videos[i]
                                    matched = False
                                    
                                    if self.current_video_id and failed_video.get('id') == self.current_video_id:
                                        matched = True
                                    elif self.current_video_title and failed_video.get('title'):
                                        # 检查标题是否匹配（支持部分匹配）
                                        failed_title = failed_video.get('title', '')
                                        if (self.current_video_title in failed_title or 
                                            failed_title in self.current_video_title):
                                            matched = True
                                    
                                    if matched:
                                        self.failed_videos.pop(i)
                                        self.progress_signal.emit(f"✅ 修正: '{video_identifier}' 实际下载成功，从失败列表移除")
                            
                            # 使用当前视频标题作为唯一标识
                            if self.current_video_title and self.current_video_title not in self.downloaded_files:
                                self.downloaded_files.add(self.current_video_title)
                                # 【修复】不再在这里更新successful_downloads，只在下载结束时统一统计
                                self.playlist_processed_count += 1  # 成功下载也算作已处理
                                self.progress_signal.emit(f"✅ 成功下载: {video_identifier} (已记录: {len(self.downloaded_files)})")
                                
                                # 【关键修复】计算并发送播放列表进度百分比
                                # 【修复】时间片段下载模式下不发送播放列表进度，因为进度已经在别处处理
                                if self.expected_download_count > 0 and not time_section_download_enabled:
                                    progress_percent = (self.playlist_processed_count / self.expected_download_count) * 100
                                    self.progress_percent_signal.emit(progress_percent)
                                    self.progress_signal.emit(f"📊 播放列表进度: {self.playlist_processed_count}/{self.expected_download_count} ({progress_percent:.1f}%)")
                    except Exception as e:
                        # 如果提取失败，使用旧的计数方法
                        pass
                    # 清空当前视频标题，准备下一个
                    self.current_video_title = ""
                    self.current_video_id = ""
                    # 【新增】下载成功完成后，清空当前下载文件列表，因为这些文件不再需要被清理
                    self.current_downloading_files.clear()
                
                # 检测地理限制绕过成功
                if any(keyword in stripped_line.lower() for keyword in [
                    "bypassing geo restriction", "geo restriction bypassed", "using proxy",
                    "geo-bypass", "bypassed geo", "proxy connected", "geo restriction: bypassed"
                ]):
                    self.progress_signal.emit(t("✅ 地理限制绕过成功！正在继续下载..."))
                
                # 检测代理连接成功 - 增强版
                if any(keyword in stripped_line.lower() for keyword in ["proxy", "socks", "http proxy"]) and \
                   any(status in stripped_line.lower() for status in ["connected", "established", "success"]):
                    self.progress_signal.emit("=" * 60)
                    self.progress_signal.emit(t("🌐 代理连接已建立！"))
                    self.progress_signal.emit(t("✅ 代理服务器响应正常"))
                    self.progress_signal.emit(t("✅ 下载流量正通过代理传输"))
                    self.progress_signal.emit("=" * 60)
                
                # 检测X平台特有错误模式
                x_platform_error_patterns = [
                    "no video could be found in this tweet",
                    "tweet not found", 
                    "tweet has been deleted",
                    "tweet is protected",
                    "user has protected their tweets",
                    "this tweet is unavailable",
                    "tweet does not exist",
                    "media not found",
                    "video not available"
                ]
                
                # 检查是否为X平台特有错误
                is_x_platform_error = any(pattern in stripped_line.lower() for pattern in x_platform_error_patterns)
                
                if is_x_platform_error and any(domain in self.url.lower() for domain in ['twitter.com', 'x.com', 't.co', 'm.twitter.com']):
                    self.progress_signal.emit(t("🔍 检测到X平台特有错误，正在分析原因..."))
                    
                    # 提供详细的错误分析和解决方案
                    if "no video could be found in this tweet" in stripped_line.lower():
                        error_analysis = self._analyze_x_platform_no_video_error()
                        self.progress_signal.emit(error_analysis)
                        
                        # 检查是否已经尝试过API重试
                        if not hasattr(self, 'x_api_retry_attempted') or not self.x_api_retry_attempted:
                            self.x_api_retry_attempted = True
                            self.progress_signal.emit(t("🔄 正在尝试使用不同API模式重试..."))
                            
                            # 终止当前进程
                            if self.process:
                                self.process.terminate()
                            
                            # 尝试使用不同API模式重试
                            if self._retry_x_platform_with_different_api():
                                return  # 重试成功，退出当前执行
                        else:
                            self.progress_signal.emit(t("⚠️ 已尝试所有API模式，该推文可能确实不包含视频内容"))
                    elif "tweet not found" in stripped_line.lower() or "tweet has been deleted" in stripped_line.lower():
                        self.progress_signal.emit(t("⚠️ 该推文可能已被删除或不存在"))
                        self.progress_signal.emit(t("📝 请检查URL是否正确，或者该推文是否仍然存在"))
                    elif "tweet is protected" in stripped_line.lower() or "user has protected their tweets" in stripped_line.lower():
                        self.progress_signal.emit(t("🔒 该推文来自私人账户，需要登录才能访问"))
                        self.progress_signal.emit(t("📝 请在内置浏览器中登录X平台并提取Cookie"))
                
                # 实时检测2024年更新的年龄限制和地理限制错误模式
                age_restriction_patterns = [
                    "sign in to confirm your age", "age-restricted", "content warning",
                    "mature content", "inappropriate content", "restricted mode",
                    "age verification", "confirm your age", "age gate",
                    "this video is only available for registered users",
                    "video unavailable", "private video", "deleted video",
                    "requires sign in", "login required", "account required",
                    "age-gated", "18+ content", "adult content",
                    "community guidelines", "policy violation"
                ]
                
                geo_restriction_patterns = [
                    "not available in your country", "geo-blocked", "region blocked",
                    "geographic restriction", "location restricted", "country restriction",
                    "regional block", "territory restriction", "ip blocked"
                ]
                
                # 检查是否匹配任何限制模式
                is_age_restricted = any(pattern in stripped_line.lower() for pattern in age_restriction_patterns)
                is_geo_restricted = any(pattern in stripped_line.lower() for pattern in geo_restriction_patterns)
                
                if is_age_restricted or is_geo_restricted:
                    restriction_type = "年龄限制" if is_age_restricted else "地理限制"
                    
                    # 检查是否已被取消，如果是则不进行重试
                    if self._cancelled:
                        self.progress_signal.emit(f"🔴 检测到{restriction_type}，但下载已被取消，不进行重试")
                        if self.process:
                            self.process.terminate()
                        self.cancel_signal.emit()
                        return
                    
                    # 如果是播放列表下载，不要中断进程，让yt-dlp的--ignore-errors自动跳过
                    if self.playlist_enabled:
                        self.progress_signal.emit(f"⚠️ 检测到{restriction_type}，播放列表模式下将自动跳过该视频继续下载")
                        # 【关键修复】记录跳过的视频（而不是失败），优先从播放列表映射中获取标题
                        error_reason = restriction_type
                        video_id = self.current_video_id
                        video_title_clean = self.current_video_title if self.current_video_title else ""
                        
                        # 如果没有标题，尝试从错误消息中提取视频ID
                        if not video_id:
                            # 尝试从当前行提取视频ID或URL
                            if "watch?v=" in stripped_line:
                                url_match = re.search(r'watch\?v=([a-zA-Z0-9_-]+)', stripped_line)
                                if url_match:
                                    video_id = url_match.group(1)
                            
                            # 尝试从yt-dlp输出中提取视频ID
                            id_match = re.search(r'\[youtube\]\s+([a-zA-Z0-9_-]+):', stripped_line)
                            if id_match:
                                video_id = id_match.group(1)
                        
                        # 【关键修复】如果有视频ID但没有标题，尝试从播放列表映射中获取
                        if video_id and not video_title_clean:
                            if video_id in self.playlist_video_map:
                                video_title_clean = self.playlist_video_map[video_id]
                                self.progress_signal.emit(f"✅ 从播放列表映射中获取到标题: {video_title_clean}")
                        
                        # 如果还是没有标题但有ID，使用ID构造标识
                        if not video_title_clean or video_title_clean.endswith(('.m4a', '.mp4', '.webm', '.mkv', '.temp', '.part')):
                            # 首先尝试从播放列表映射中获取标题
                            if video_id and video_id in self.playlist_video_map:
                                video_title_clean = self.playlist_video_map[video_id]
                                self.progress_signal.emit(f"✅ 从播放列表映射中获取到跳过视频标题: {video_title_clean}")
                            elif video_id:
                                video_title_clean = f"[ID: {video_id}] - 无法获取标题（{restriction_type}）"
                            else:
                                video_title_clean = f"未知视频 - {restriction_type}"
                        
                        skipped_video = {
                            'id': video_id,
                            'title': video_title_clean,
                            'reason': error_reason,
                            'full_error': stripped_line[:200] if stripped_line else error_reason
                        }
                        
                        # 避免重复记录同一个视频
                        if video_id:
                            if not any(v.get('id') == video_id for v in self.skipped_videos):
                                # 【关键修复】检查这个跳过是否在用户设置的范围内
                                should_count = True
                                if self.has_playlist_items_set and self.expected_download_count > 0:
                                    # 尝试从当前上下文获取视频索引
                                    current_index = self._get_current_video_index()
                                    if current_index > 0:
                                        should_count = self._is_index_in_range(current_index)
                                        if not should_count:
                                            self.progress_signal.emit(f"📊 跳过视频 {video_title_clean} 在范围外，不计入统计")
                                
                                if should_count:
                                    self.skipped_videos.append(skipped_video)
                                    self.playlist_restriction_skipped_count += 1
                                    self.playlist_processed_count += 1  # 跳过的视频也算作已处理
                                
                                # 【精准统计修复】如果标题已经包含ID信息，不再重复附加
                                if f"(ID: {video_id})" in video_title_clean or video_title_clean.startswith("视频 (ID:"):
                                    self.progress_signal.emit(f"⏭️ 记录跳过视频: {video_title_clean} - {error_reason}")
                                else:
                                    self.progress_signal.emit(f"⏭️ 记录跳过视频: {video_title_clean} (ID: {video_id}) - {error_reason}")
                                
                                # 【关键修复】计算并发送播放列表进度百分比
                                # 【修复】时间片段下载模式下不发送播放列表进度
                                if self.expected_download_count > 0 and should_count and not time_section_download_enabled:
                                    progress_percent = (self.playlist_processed_count / self.expected_download_count) * 100
                                    self.progress_percent_signal.emit(progress_percent)
                                    self.progress_signal.emit(f"📊 播放列表进度: {self.playlist_processed_count}/{self.expected_download_count} ({progress_percent:.1f}%)")
                        else:
                            # 如果没有ID，使用标题去重
                            if not any(v['title'] == video_title_clean and v['reason'] == error_reason for v in self.skipped_videos):
                                # 【关键修复】对于没有ID的视频，保守处理 - 默认计入统计
                                self.skipped_videos.append(skipped_video)
                                self.playlist_restriction_skipped_count += 1
                                self.playlist_processed_count += 1  # 跳过的视频也算作已处理
                                self.progress_signal.emit(f"⏭️ 记录跳过视频: {video_title_clean} - {error_reason}")
                                
                                # 【关键修复】计算并发送播放列表进度百分比
                                # 【修复】时间片段下载模式下不发送播放列表进度
                                if self.expected_download_count > 0 and not time_section_download_enabled:
                                    progress_percent = (self.playlist_processed_count / self.expected_download_count) * 100
                                    self.progress_percent_signal.emit(progress_percent)
                                    self.progress_signal.emit(f"📊 播放列表进度: {self.playlist_processed_count}/{self.expected_download_count} ({progress_percent:.1f}%)")
                        
                        # 不要终止进程，让yt-dlp自动跳过并继续下载播放列表中的其他视频
                        continue  # 继续处理后续输出
                    
                    # 只在单个视频下载时才尝试使用增强认证参数重试
                    if not self.age_restriction_retry_attempted:
                        self.progress_signal.emit(f"🔍 检测到{restriction_type}（单个视频），启动2024年最新绕过策略...")
                        if self.process:
                            self.process.terminate()
                        self._retry_with_enhanced_auth()
                        return
                    else:
                        self.progress_signal.emit(t("增强认证重试失败，停止下载"))
                        self.process.terminate()
                        # 提供更详细的错误信息和解决方案
                        error_message = self._generate_age_restriction_error_message(stripped_line)
                        self.finished_signal.emit(False, error_message)
                        return
                
                # 检查是否长时间没有进度更新，如果超过3秒没有更新，则发送估算进度
                current_time = time.time()
                if current_time - last_progress_update > 3 and not "ERROR" in stripped_line and not "Error" in stripped_line:
                    # 计算估算进度，使用更平缓的增长曲线
                    # 计算已经过去的时间（秒）
                    elapsed_time = current_time - download_start_time
                    # 使用更平缓的进度估算算法，避免进度跳跃
                    # 前30秒内，进度增长非常缓慢，每3秒仅增加0.5%
                    if elapsed_time < 30:
                        new_estimated_progress = min(5, last_estimated_progress + 0.5)
                    # 30-60秒，进度增长稍快，每3秒增加1%
                    elif elapsed_time < 60:
                        new_estimated_progress = min(15, last_estimated_progress + 1)
                    # 60-120秒，进度增长适中，每3秒增加2%
                    elif elapsed_time < 120:
                        new_estimated_progress = min(30, last_estimated_progress + 2)
                    # 120秒后，进度增长正常，每3秒增加3%，但总体不超过60%
                    else:
                        new_estimated_progress = min(60, last_estimated_progress + 3)
                    
                    # 只有当新估算进度大于上次发送的进度时才更新
                    if new_estimated_progress > last_percent:
                        self.progress_signal.emit(f"长时间无进度更新，发送估算进度: {new_estimated_progress:.1f}%")
                        self.progress_percent_signal.emit(new_estimated_progress)
                        last_progress_update = current_time
                        last_estimated_progress = new_estimated_progress
                        self.progress_sent = True
                
                # 检查是否长时间没有进度更新，如果超过3秒没有更新，则发送估算进度
                current_time = time.time()
                if current_time - last_progress_update > 3 and not "ERROR" in stripped_line and not "Error" in stripped_line:
                    # 计算估算进度，使用更平缓的增长曲线
                    # 计算已经过去的时间（秒）
                    elapsed_time = current_time - download_start_time
                    # 使用更平缓的进度估算算法，避免进度跳跃
                    # 前30秒内，进度增长非常缓慢，每3秒仅增加0.5%
                    if elapsed_time < 30:
                        new_estimated_progress = min(5, last_estimated_progress + 0.5)
                    # 30-60秒，进度增长稍快，每3秒增加1%
                    elif elapsed_time < 60:
                        new_estimated_progress = min(15, last_estimated_progress + 1)
                    # 60-120秒，进度增长适中，每3秒增加2%
                    elif elapsed_time < 120:
                        new_estimated_progress = min(30, last_estimated_progress + 2)
                    # 120秒后，进度增长正常，每3秒增加3%，但总体不超过60%
                    else:
                        new_estimated_progress = min(60, last_estimated_progress + 3)
                    
                    # 只有当新估算进度大于上次发送的进度时才更新
                    if new_estimated_progress > last_percent:
                        self.progress_signal.emit(f"长时间无进度更新，发送估算进度: {new_estimated_progress:.1f}%")
                        self.progress_percent_signal.emit(new_estimated_progress)
                        last_progress_update = current_time
                        last_estimated_progress = new_estimated_progress
                        self.progress_sent = True
                
                # 解析下载进度
                progress_match = progress_pattern.search(stripped_line)
                if progress_match:
                    try:
                        percent = float(progress_match.group(1))
                        # 确保百分比在有效范围内
                        percent = max(0, min(100, percent))
                        
                        # 【修复】时间片段下载模式：调整进度计算
                        # 当启用时间片段下载时，需要下载多个流（视频+音频），然后ffmpeg裁剪合并
                        # 进度分配：每个流下载占40%（共80%），后处理占20%
                        if time_section_download_enabled:
                            # 计算调整后的进度
                            # 假设有2个流（视频+音频），每个流占40%，后处理占20%
                            if ts_stream_count > 0:
                                # 当前流的进度贡献
                                stream_progress_per_stream = 40.0  # 每个流占40%
                                # 已完成流的进度（ts_stream_100_count 在 [download] 100% 检测时更新）
                                completed_streams_progress = ts_stream_100_count * stream_progress_per_stream
                                # 当前流的进度（如果还没完成100%）
                                # 注意：当前流是第 (ts_stream_100_count + 1) 个流
                                if percent < 99.5:
                                    current_stream_progress = (percent / 100.0) * stream_progress_per_stream
                                else:
                                    # 接近100%时，不在这里更新进度，等待 [download] 100% 检测
                                    current_stream_progress = 0
                                
                                # 总进度 = 已完成流进度 + 当前流进度
                                adjusted_percent = completed_streams_progress + current_stream_progress
                                # 确保不超过80%（留20%给后处理）
                                adjusted_percent = min(80, adjusted_percent)
                                
                                # 只有当进度变化超过0.5%或距离上次更新超过0.5秒时才发送信号
                                current_time = time.time()
                                if abs(adjusted_percent - last_percent) >= 0.5 or (current_time - last_progress_update) >= 0.5:
                                    self.progress_percent_signal.emit(adjusted_percent)
                                    last_progress_update = current_time
                                    last_percent = adjusted_percent
                                    self.progress_sent = True
                                    self.progress_signal.emit(f"⏱️ 时间片段进度: {adjusted_percent:.1f}% (流 {ts_stream_count}, 原始 {percent:.1f}%)")
                            else:
                                # 还没检测到流，使用原始进度的40%（假设是第一个流）
                                adjusted_percent = percent * 0.4
                                current_time = time.time()
                                if abs(adjusted_percent - last_percent) >= 0.5 or (current_time - last_progress_update) >= 0.5:
                                    self.progress_percent_signal.emit(adjusted_percent)
                                    last_progress_update = current_time
                                    last_percent = adjusted_percent
                                    self.progress_sent = True
                                    self.progress_signal.emit(f"⏱️ 时间片段进度: {adjusted_percent:.1f}% (原始 {percent:.1f}%)")
                        else:
                            # 非时间片段下载模式，使用原始进度
                            # 只有当进度变化超过0.5%或距离上次更新超过0.5秒时才发送信号
                            current_time = time.time()
                            if abs(percent - last_percent) >= 0.5 or (current_time - last_progress_update) >= 0.5:
                                self.progress_percent_signal.emit(percent)
                                last_progress_update = current_time
                                last_percent = percent
                                # 标记已发送过进度信号
                                self.progress_sent = True
                                # 输出调试信息
                                self.progress_signal.emit(f"进度更新: {percent:.2f}%")
                    except (ValueError, IndexError) as e:
                        self.progress_signal.emit(f"解析进度出错: {str(e)}")
                
                # 解析已下载/总大小信息
                downloaded_match = downloaded_pattern.search(stripped_line)
                # 如果主模式没匹配到，尝试使用备用模式
                if not downloaded_match:
                    downloaded_match = alt_downloaded_pattern.search(stripped_line)
                    if downloaded_match:
                        self.progress_signal.emit(f"使用备用模式匹配到下载信息: {downloaded_match.group(1)}/{downloaded_match.group(2)}")
                        
                # 如果检测到下载开始但还没有进度信息，发送一个初始进度信号
                # 但只在尚未发送过进度信号时才发送，避免重置已有进度
                if "下载中" in stripped_line and not progress_match and not downloaded_match and not self.progress_sent:
                    self.progress_signal.emit(t("检测到下载开始，但尚无进度信息，发送初始进度信号"))
                    self.progress_percent_signal.emit(0.1)  # 发送一个很小的非零进度值
                    self.progress_sent = True  # 标记已发送过初始进度信号
                
                if downloaded_match and not progress_match:
                    try:
                        # 尝试从下载信息中估算进度
                        downloaded = downloaded_match.group(1)
                        total = downloaded_match.group(2)
                        
                        # 记录原始匹配信息，帮助调试
                        self.progress_signal.emit(f"原始下载信息: {downloaded}/{total}")
                        
                        # 使用改进的单位转换
                        downloaded_value = self._parse_size(downloaded)
                        total_value = self._parse_size(total)
                        
                        # 记录转换后的值，帮助调试
                        self.progress_signal.emit(f"转换后的大小: {downloaded_value:.2f}/{total_value:.2f} 字节")
                        
                        # 处理总大小未知的情况（NA或0）
                        if total == "NA" or total_value <= 0:
                            # 使用已下载大小和时间估算进度
                            elapsed_time = time.time() - download_start_time
                            if elapsed_time > 0:
                                # 使用与前面相同的平缓进度估算算法
                                # 前30秒内，进度增长非常缓慢
                                if elapsed_time < 30:
                                    estimated_percent = min(5, (elapsed_time / 30) * 5)
                                # 30-60秒，进度增长稍快
                                elif elapsed_time < 60:
                                    estimated_percent = 5 + min(10, ((elapsed_time - 30) / 30) * 10)
                                # 60-120秒，进度增长适中
                                elif elapsed_time < 120:
                                    estimated_percent = 15 + min(15, ((elapsed_time - 60) / 60) * 15)
                                # 120-300秒，进度继续增长
                                elif elapsed_time < 300:
                                    estimated_percent = 30 + min(30, ((elapsed_time - 120) / 180) * 30)
                                # 300秒后，进度增长更慢，最多到80%
                                else:
                                    estimated_percent = 60 + min(20, ((elapsed_time - 300) / 300) * 20)
                                
                                # 确保估算进度不会低于之前的进度
                                estimated_percent = max(last_percent, estimated_percent)
                                
                                # 只有当估算进度变化超过0.5%或距离上次更新超过1秒时才发送信号
                                current_time = time.time()
                                if abs(estimated_percent - last_percent) >= 0.5 or (current_time - last_progress_update) >= 1:
                                    self.progress_signal.emit(f"总大小未知，使用时间估算进度: {estimated_percent:.2f}%")
                                    self.progress_percent_signal.emit(estimated_percent)
                                    last_progress_update = current_time
                                    last_percent = estimated_percent
                                    self.progress_sent = True
                        elif total_value > 0:
                            percent = (downloaded_value / total_value) * 100
                            # 确保百分比在有效范围内
                            percent = max(0, min(100, percent))
                            
                            # 只有当进度变化超过0.5%或距离上次更新超过0.5秒时才发送信号
                            current_time = time.time()
                            if abs(percent - last_percent) >= 0.5 or (current_time - last_progress_update) >= 0.5:
                                # 发送进度信号前记录详细信息
                                self.progress_signal.emit(f"进度估算: {downloaded}/{total} = {percent:.2f}%")
                                self.progress_percent_signal.emit(percent)
                                last_progress_update = current_time
                                last_percent = percent
                                self.progress_sent = True
                    except (ValueError, IndexError) as e:
                        self.progress_signal.emit(f"解析下载信息出错: {str(e)}")
                        # 记录更详细的错误信息，帮助调试
                        import traceback
                        self.progress_signal.emit(traceback.format_exc())

                
                # 收集错误信息以及特殊状态信息以便后续分析
                if ("ERROR" in stripped_line or "Error" in stripped_line or "error" in stripped_line or
                    "Maximum number of downloads reached" in stripped_line or
                    "Aborting remaining downloads" in stripped_line):
                    error_output += stripped_line + "\n"
            
            # 等待进程完成
            self.process.wait()
            
            # 不要无条件显示100%，先检查下载是否成功
            self.progress_signal.emit(t("进程完成，检查下载结果..."))
            
            # 检查是否因为达到最大下载限制而停止（这是正常情况，应视为成功）
            reached_max_downloads = ("Maximum number of downloads reached" in error_output or
                                    "Aborting remaining downloads" in error_output)
            
            # 如果返回码为0，或者是因为达到最大下载限制而停止，都视为正常完成
            if self.process.returncode == 0 or reached_max_downloads:
                # 单视频下载时：先将暂存目录结果搬运到目标目录，再做成功校验
                if self._staging_output_dir:
                    moved = self._move_staging_outputs_to_target()
                    if moved:
                        self.progress_signal.emit(f"✓ 已将 {len(moved)} 个文件从暂存目录写入目标目录")
                    else:
                        self.progress_signal.emit(t("⚠️ 暂存目录未检测到可搬运的最终文件"))
                # 检查是否真的有文件被下载
                download_success = self._verify_download_success()
                if download_success:
                    needs_timeline_fix = self._needs_audio_lang_timeline_fix()
                    target_progress = 99.0 if needs_timeline_fix else 100.0
                    if needs_timeline_fix:
                        self.progress_signal.emit(t("下载成功，正在执行后处理（覆盖修复）..."))
                    else:
                        self.progress_signal.emit("下载成功，开始平滑过渡到100%")
                    current_progress = last_percent
                    if current_progress < target_progress:
                        # 计算需要增加的进度步数（每次增加1%）
                        steps = int((target_progress - current_progress) / 1) + 1
                        # 计算每步之间的延迟时间（总共不超过1秒）
                        delay = min(1.0 / steps, 0.1)  # 最大延迟100毫秒
                        # 逐步增加进度
                        for i in range(steps):
                            next_progress = min(target_progress, current_progress + 1 + i)
                            self.progress_percent_signal.emit(next_progress)
                            # 短暂延迟，创造平滑效果
                            time.sleep(delay)
                    
                    # 【稳定性修复】指定音频语言时，对输出视频执行时间轴稳定化处理
                    # 解决“不是纯卡顿”的播放异常（如跳帧、节奏异常、音画轻微抖动）
                    if needs_timeline_fix:
                        # 明确告诉用户：当前仍在后处理，尚未完成
                        self.progress_percent_signal.emit(99.0)
                        self.progress_signal.emit(t("后处理进行中：正在覆盖修复输出文件..."))
                        self._normalize_video_timeline_for_selected_audio_language()

                    # 最后确保仅在全部处理完成后发送100%
                    self.progress_percent_signal.emit(100.0)
                    self.progress_signal.emit("下载完成，进度100%")
                    
                    # 【关键修复】扫描输出目录，统计实际下载的新文件数量
                    try:
                        if os.path.exists(self.output_path) and os.path.isdir(self.output_path):
                            current_files = set()
                            download_start_time_record = time.time() - 3600  # 默认1小时前，如果找不到开始时间记录
                            
                            # 记录下载开始时间（从进程启动开始计算）
                            if hasattr(self, 'process_start_time'):
                                download_start_time_record = self.process_start_time
                                self.progress_signal.emit(f"📊 使用进程开始时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(download_start_time_record))}")
                            
                            for root, dirs, files in os.walk(self.output_path):
                                for file in files:
                                    # 只统计视频/音频文件（排除临时文件、元数据文件和字幕文件）
                                    file_ext = os.path.splitext(file)[1].lower()
                                    if file_ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', 
                                                  '.m4a', '.mp3', '.aac', '.opus', '.flac', '.wav']:
                                        # 排除临时文件
                                        if not file.endswith(('.part', '.ytdl', '.temp')):
                                            file_path = os.path.join(root, file)
                                            try:
                                                # 使用文件修改时间判断是否是本次下载的文件
                                                mtime = os.path.getmtime(file_path)
                                                # 如果是下载开始后创建的文件，才认为是新下载的
                                                if mtime >= download_start_time_record - 300:  # 允许5分钟的误差
                                                    current_files.add(file_path)
                                            except:
                                                # 如果无法获取修改时间，则只通过文件名判断
                                                current_files.add(file_path)
                            
                            # 计算新增的文件（实际下载的文件）
                            new_files = current_files - self.download_start_files
                            if new_files:
                                # 【修复】使用实际新增文件数作为成功下载数，不再重复设置
                                actual_success_count = len(new_files)
                                self.progress_signal.emit(f"📊 实际统计: 成功下载 {actual_success_count} 个新文件")
                                self.progress_signal.emit(f"   开始前文件数: {len(self.download_start_files)}")
                                self.progress_signal.emit(f"   结束后文件数: {len(current_files)}")
                                self.progress_signal.emit(f"   新增文件数: {len(new_files)}")
                                
                                # 检查是否启用了时间片段或章节功能
                                time_sections_enabled = self._time_section_mode_active()
                                split_chapters_enabled = (
                                    hasattr(self, 'split_chapters_check')
                                    and self.split_chapters_check.isChecked()
                                )
                                
                                if time_sections_enabled:  # 仅保留时间片段检测
                                    # 分析生成的文件类型
                                    segment_files = []
                                    chapter_files = []
                                    main_files = []
                                    
                                    for file_path in new_files:
                                        filename = os.path.basename(file_path)
                                        # 检查是否是片段文件（通常包含时间信息或分段标记）
                                        if re.search(r'\d{2}:\d{2}:\d{2}|\d{2}-\d{2}-\d{2}', filename):
                                            segment_files.append(filename)
                                        # 检查是否是章节文件（通常包含chapter或分段编号）
                                        elif re.search(r'chapter|分段|part\d+|分段\d+', filename, re.IGNORECASE):
                                            chapter_files.append(filename)
                                        else:
                                            main_files.append(filename)
                                    
                                    if time_sections_enabled and segment_files:
                                        self.progress_signal.emit(f"   ⏱️ 时间片段文件: {len(segment_files)} 个")
                                        for seg_file in segment_files[:3]:  # 显示前3个
                                            self.progress_signal.emit(f"      └─ {seg_file}")
                                        if len(segment_files) > 3:
                                            self.progress_signal.emit(f"      └─ ... 还有 {len(segment_files) - 3} 个片段文件")
                                    
                                    if split_chapters_enabled and chapter_files:
                                        self.progress_signal.emit(f"   📑 章节分割文件: {len(chapter_files)} 个")
                                        for chap_file in chapter_files[:3]:  # # 显示前3个
                                            self.progress_signal.emit(f"      └─ {chap_file}")
                                        if len(chapter_files) > 3:
                                            self.progress_signal.emit(f"      └─ ... 还有 {len(chapter_files) - 3} 个章节文件")
                            else:
                                # 如果没有新增文件，检查是否有文件存在
                                if len(current_files) > 0 and len(self.download_start_files) == 0:
                                    # 开始时目录为空，结束时有了文件，说明都下载成功了
                                    actual_success_count = len(current_files)
                                    self.progress_signal.emit(f"📊 实际统计: 成功下载 {actual_success_count} 个文件（起始目录为空）")
                                else:
                                    actual_success_count = 0
                    except Exception as e:
                        self.progress_signal.emit(f"⚠️ 统计实际文件时出错: {e}")
                    
                    # 如果是因为达到最大下载限制而停止，给出明确提示
                    if reached_max_downloads:
                        self.progress_signal.emit(t("\n✓ 已达到设置的最大下载数量，下载任务正常完成"))
                    
                    # 【关键修复】只在用户主动启用"下载播放列表"功能时才显示下载报告
                    # 单个视频下载、批量下载但未勾选播放列表等情况都不显示报告
                    # 无论是否有失败，都要显示报告（精准统计）
                    if self.playlist_enabled:
                        # 【精准统计修复】从失败列表中移除实际已下载的视频
                        self.progress_signal.emit(f"🔍 检查失败列表中是否有实际下载成功的视频...")
                        actual_failed_videos = []
                        new_files_names = {os.path.basename(f) for f in new_files} if new_files else set()
                        
                        for failed_video in self.failed_videos:
                            failed_title = failed_video.get('title', '')
                            failed_id = failed_video.get('id', '')
                            file_exists = False
                            
                            # 首先检查新增文件列表中是否有匹配的文件名
                            if new_files_names:
                                for filename in new_files_names:
                                    name_without_ext = os.path.splitext(filename)[0]
                                    name_without_ext = re.sub(r'\.f\d+$', '', name_without_ext)
                                    failed_title_clean = re.sub(r'\.f\d+$', '', failed_title)
                                    
                                    if (name_without_ext in failed_title_clean or 
                                        failed_title_clean in name_without_ext or
                                        name_without_ext.replace('_', ' ') in failed_title_clean or
                                        failed_title_clean.replace('_', ' ') in name_without_ext):
                                        file_exists = True
                                        self.progress_signal.emit(f"✅ 修正: '{failed_title}' 实际已下载，从失败列表移除")
                                        break
                            
                            # 如果在上一步没找到，再检查整个输出目录
                            if not file_exists:
                                try:
                                    if self.output_path and os.path.exists(self.output_path):
                                        for root, dirs, files in os.walk(self.output_path):
                                            for filename in files:
                                                # 去除文件扩展名进行比较
                                                name_without_ext = os.path.splitext(filename)[0]
                                                
                                                # 去除临时格式标识（如 .f135, .f140）
                                                name_without_ext = re.sub(r'\.f\d+$', '', name_without_ext)
                                                failed_title_clean = re.sub(r'\.f\d+$', '', failed_title)
                                                
                                                # 如果文件名包含失败视频的标题（或部分匹配）
                                                if (name_without_ext in failed_title_clean or 
                                                    failed_title_clean in name_without_ext or
                                                    name_without_ext.replace('_', ' ') in failed_title_clean or
                                                    failed_title_clean.replace('_', ' ') in name_without_ext):
                                                    # 检查是否是最终视频文件（而非临时文件）
                                                    file_ext = os.path.splitext(filename)[1].lower()
                                                    if file_ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', 
                                                                  '.m4a', '.mp3', '.aac', '.opus', '.flac', '.wav']:
                                                        # 检查文件修改时间，确保是本次下载的
                                                        file_path = os.path.join(root, filename)
                                                        try:
                                                            mtime = os.path.getmtime(file_path)
                                                            # 使用已经设置好的下载开始时间，不再重复赋值
                                                            if hasattr(self, 'process_start_time'):
                                                                download_start_time = self.process_start_time
                                                            else:
                                                                download_start_time = time.time() - 3600  # 默认1小时前
                                                            
                                                            if mtime >= download_start_time - 300:
                                                                file_exists = True
                                                                self.progress_signal.emit(f"✅ 修正: '{failed_title}' 实际已下载，从失败列表移除")
                                                                break
                                                        except:
                                                            file_exists = True  # 如果无法获取时间，假设文件存在
                                                            break
                                            if file_exists:
                                                break
                                except Exception as e:
                                    self.progress_signal.emit(f"⚠️ 检查失败视频时出错: {e}")
                            
                            if not file_exists:
                                actual_failed_videos.append(failed_video)
                        
                        # 更新失败视频列表
                        self.failed_videos = actual_failed_videos
                        self.progress_signal.emit(f"📊 修正后统计: {len(self.failed_videos)} 个真正失败的视频")
                        
                        # 【关键修复】在修正失败列表后，设置最终的成功下载数
                        # 确保成功下载数 = 实际新文件数（已经计算好的actual_success_count）
                        if new_files:
                            # 【关键修复】如果设置了播放列表范围，需要验证这些文件是否在范围内
                            if self.has_playlist_items_set and self.expected_download_count > 0:
                                # 检查新增文件是否对应范围内的视频
                                range_aware_files = []
                                self.progress_signal.emit(f"📊 开始范围感知文件统计，新增文件数: {len(new_files)}")
                                for file_path in new_files:
                                    filename = os.path.basename(file_path)
                                    self.progress_signal.emit(f"📁 检查文件: {filename}")
                                    # 尝试从文件名中提取索引信息（yt-dlp通常包含索引）
                                    index_match = re.search(r'(\d+)', filename)
                                    if index_match:
                                        file_index = int(index_match.group(1))
                                        self.progress_signal.emit(f"   提取到索引: {file_index}")
                                        # 检查这个索引是否在用户设置的范围内
                                        if self._is_index_in_range(file_index):
                                            range_aware_files.append(file_path)
                                            self.progress_signal.emit(f"   ✅ 索引在范围内，包含此文件")
                                        else:
                                            self.progress_signal.emit(f"   ❌ 索引不在范围内，排除此文件")
                                    else:
                                        # 如果没有索引信息，默认包含（保守策略）
                                        range_aware_files.append(file_path)
                                        self.progress_signal.emit(f"   ⚠️ 未提取到索引，默认包含")
                                
                                self.successful_downloads = len(range_aware_files)
                                self.progress_signal.emit(f"📊 最终成功下载数（范围感知）: {self.successful_downloads} (范围内)")
                            else:
                                # 没有设置范围，使用所有新文件
                                self.successful_downloads = actual_success_count
                                self.progress_signal.emit(f"📊 最终成功下载数: {self.successful_downloads}")
                        elif len(current_files) > 0 and len(self.download_start_files) == 0:
                            # 如果开始时目录为空，现在有文件，说明都下载成功了
                            if self.has_playlist_items_set and self.expected_download_count > 0:
                                # 设置了范围，但无法确定具体索引，使用预期数量作为上限
                                self.successful_downloads = min(len(current_files), self.expected_download_count)
                                self.progress_signal.emit(f"📊 最终成功下载数（范围感知，保守估计）: {self.successful_downloads}")
                            else:
                                self.successful_downloads = actual_success_count
                                self.progress_signal.emit(f"📊 最终成功下载数（起始目录为空）: {self.successful_downloads}")
                        else:
                            # 其他情况，使用计算好的数量
                            self.successful_downloads = actual_success_count
                        
                        # 【最终确认】确保成功下载数与实际新增文件数一致
                        self.progress_signal.emit(f"📊 最终确认: 实际新增文件数 = {actual_success_count}, 成功下载数 = {self.successful_downloads}")
                        if self.successful_downloads != actual_success_count:
                            self.progress_signal.emit(f"⚠️ 注意：范围过滤导致成功下载数从 {actual_success_count} 调整为 {self.successful_downloads}")
                        
                        # 【精准统计修复】确保预计下载数和总计处理数准确
                        # 预计下载数：优先使用用户设置的expected_download_count，如果没有则使用total_videos
                        # 但需要考虑日期过滤跳过的视频（这些视频不会被尝试下载）
                        actual_expected = self.expected_download_count if self.expected_download_count > 0 else self.total_videos
                        
                        # 【关键修复】总计处理数 = 成功下载数 + 失败数 + 因限制跳过的视频数
                        # 这样才能准确反映实际尝试下载的总数量
                        restriction_skipped = len(self.skipped_videos) if hasattr(self, 'skipped_videos') else 0
                        total_processed = self.successful_downloads + len(self.failed_videos) + restriction_skipped
                        
                        # 如果有跳过的视频，说明预计下载数应该小于总数
                        # 如果预计下载数仍然等于总数，说明可能有日期过滤，需要调整
                        if self.playlist_skipped_count > 0 and actual_expected == self.total_videos and self.total_videos > 0:
                            # 如果有跳过的视频，但预计数还等于总数，说明需要从预计数中扣除跳过的
                            # 但更准确的方法是使用实际处理数作为预计数（因为跳过的视频已经被yt-dlp排除了）
                            self.progress_signal.emit(f"📊 检测到日期过滤跳过了 {self.playlist_skipped_count} 个视频，实际预计下载数应为尝试下载的数量")
                        
                        # 预计下载数应该反映实际会尝试下载的数量
                        # 如果有日期过滤，yt-dlp会跳过不满足条件的视频
                        # 所以预计下载数 = 实际处理数（成功+失败），因为跳过的视频不会进入下载流程
                        if total_processed > 0:
                            # 实际处理的视频数就是实际尝试下载的数量
                            # 如果总数很大但处理数很小，说明有很多视频被过滤了
                            if actual_expected > total_processed + self.playlist_skipped_count:
                                # 预计下载数应该调整为：实际处理数（这是最准确的）
                                self.progress_signal.emit(f"📊 预计下载数调整为实际尝试下载数（考虑日期过滤）")
                                self.expected_download_count = total_processed
                            else:
                                # 如果预计数合理，保持不变
                                self.expected_download_count = actual_expected
                        else:
                            # 如果没有处理任何视频，保持原预计数
                            self.expected_download_count = actual_expected
                        
                        # 【关键报告】显示最终准确统计（使用修正后的数据）
                        # 统计因限制失败的视频数
                        restriction_failed = sum(1 for v in self.failed_videos 
                                               if any(keyword in v.get('reason', '') 
                                                     for keyword in ['限制', '年龄', '地区', '会员', 'restricted', 
                                                                    'age', 'geo', 'membership', 'premium']))
                        
                        self.progress_signal.emit(f"📊 最终统计:")
                        
                        # 【关键修复】如果设置了范围，特别标注（更准确的检测）
                        has_range_set = (self.expected_download_count > 0 and 
                                       self.expected_download_count < self.total_videos and 
                                       self.total_videos > 0)
                        
                        # 检查是否启用了时间片段下载
                        time_sections_enabled = self._time_section_mode_active()
                        sections_summary = self._get_download_sections_summary()
                        
                        if has_range_set:
                            self.progress_signal.emit(f"   播放列表总数: {self.total_videos}")
                            self.progress_signal.emit(f"   设置范围: {self.expected_download_count} 个视频")
                            if time_sections_enabled:
                                self.progress_signal.emit(f"   ⏱️ 时间片段: {sections_summary}")
                            self.progress_signal.emit(f"   ✅ 成功下载: {self.successful_downloads} (基于实际文件统计)")
                            self.progress_signal.emit(f"   ❌ 下载失败: {len(self.failed_videos)} (已移除误报)")
                            if restriction_failed > 0:
                                self.progress_signal.emit(f"      └─ 其中因限制失败: {restriction_failed} 个")
                            if restriction_skipped > 0:
                                self.progress_signal.emit(f"   ⏭️ 因限制跳过: {restriction_skipped} 个")
                            self.progress_signal.emit(f"   📹 范围内处理: {total_processed}")
                        else:
                            self.progress_signal.emit(f"   预计下载: {self.expected_download_count if self.expected_download_count > 0 else self.total_videos}")
                            if time_sections_enabled:
                                self.progress_signal.emit(f"   ⏱️ 时间片段: {sections_summary}")
                            self.progress_signal.emit(f"   ✅ 成功下载: {self.successful_downloads} (基于实际文件统计)")
                            self.progress_signal.emit(f"   ❌ 下载失败: {len(self.failed_videos)} (已移除误报)")
                            if restriction_failed > 0:
                                self.progress_signal.emit(f"      └─ 其中因限制失败: {restriction_failed} 个")
                            if restriction_skipped > 0:
                                self.progress_signal.emit(f"   ⏭️ 因限制跳过: {restriction_skipped} 个")
                            self.progress_signal.emit(f"   📹 总计处理: {total_processed}")
                        
                        # 【关键修复】无论是否有失败，都显示播放列表下载报告
                        # 使用实际统计的数据：预期下载数量、成功数量、失败数量
                        self.progress_signal.emit(f"\n📊 播放列表下载完成，正在生成下载报告...")
                        self.failed_videos_signal.emit(self.failed_videos)
                    
                    # 转换元数据文件为可读格式
                    self._convert_metadata_files_to_readable()
                    
                    self.finished_signal.emit(True, "下载完成！")
                else:
                    # 虽然返回码为0，但没有找到下载的文件，可能是年龄限制等问题
                    # 检查错误输出中是否包含年龄限制相关信息
                    if ("Sign in to confirm your age" in error_output or
                        "age-restricted" in error_output.lower() or
                        "content warning" in error_output.lower() or
                        "mature content" in error_output.lower() or
                        "inappropriate content" in error_output.lower() or
                        "restricted mode" in error_output.lower() or
                        "age verification" in error_output.lower() or
                        "confirm your age" in error_output.lower() or
                        "age gate" in error_output.lower() or
                        "This video is only available for registered users" in error_output):
                        self.progress_signal.emit(t("警告：下载过程完成但因年龄限制或地理位置限制未生成文件"))
                        
                        # 如果是播放列表下载，检查是否有其他文件下载成功
                        if self.playlist_enabled:
                            # 检查输出目录是否有下载的文件（使用更宽松的时间范围）
                            has_any_files = False
                            if self.output_path and os.path.exists(self.output_path):
                                current_time = time.time()
                                for root, dirs, files in os.walk(self.output_path):
                                    for filename in files:
                                        file_path = os.path.join(root, filename)
                                        try:
                                            mtime = os.path.getmtime(file_path)
                                            # 扩大到15分钟以捕获所有可能的下载文件
                                            if current_time - mtime < 900:
                                                file_ext = os.path.splitext(filename)[1].lower()
                                                if file_ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', 
                                                              '.m4a', '.mp3', '.aac', '.opus', '.flac', '.wav']:
                                                    has_any_files = True
                                                    break
                                        except:
                                            continue
                                    if has_any_files:
                                        break
                            
                            if has_any_files:
                                # 有其他视频下载成功，视为部分成功
                                # 转换元数据文件为可读格式
                                self._convert_metadata_files_to_readable()
                                skip_count = len(self.failed_videos) if hasattr(self, 'failed_videos') and self.failed_videos else "部分"
                                success_message = f"下载完成！\n\n✓ 已成功下载可用的视频\n⚠️ {skip_count} 个视频因限制被跳过"
                                self.progress_signal.emit(f"✓ 播放列表下载完成（部分视频因限制跳过）")
                                self.finished_signal.emit(True, success_message)
                            else:
                                # 没有文件下载成功
                                error_message = self._generate_simple_error_message(error_output)
                                self.finished_signal.emit(False, error_message)
                        else:
                            # 单个视频下载失败
                            error_message = self._generate_simple_error_message(error_output)
                            self.finished_signal.emit(False, error_message)
                    else:
                        self.progress_signal.emit(t("警告：下载过程完成但未找到输出文件"))
                        self.finished_signal.emit(False, "下载失败：未找到输出文件")
            else:
                # 【关键修复】如果是播放列表下载且返回码非0，先检查是否有文件下载成功
                # 这样可以正确处理"部分成功"的情况（有些视频成功，有些失败）
                if self.playlist_enabled:
                    self.progress_signal.emit(t("⏳ 播放列表下载进程结束（返回码非0），检查是否有文件下载成功..."))
                    has_downloaded_files = False
                    if self.output_path and os.path.exists(self.output_path):
                        current_time = time.time()
                        for root, dirs, files in os.walk(self.output_path):
                            for filename in files:
                                file_path = os.path.join(root, filename)
                                try:
                                    mtime = os.path.getmtime(file_path)
                                    # 如果是最近10分钟内的文件，认为是本次下载的
                                    if current_time - mtime < 600:
                                        # 排除缩略图等辅助文件，只检查视频/音频文件
                                        file_ext = os.path.splitext(filename)[1].lower()
                                        if file_ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', 
                                                      '.m4a', '.mp3', '.aac', '.opus', '.flac', '.wav']:
                                            has_downloaded_files = True
                                            break
                                except:
                                    continue
                            if has_downloaded_files:
                                break
                    
                    if has_downloaded_files:
                        # 有文件下载成功，视为部分成功
                        self.progress_signal.emit(t("✅ 检测到已有视频下载成功，播放列表下载部分完成"))
                        # 发送100%进度
                        self.progress_percent_signal.emit(100.0)
                        self.progress_signal.emit("下载完成，进度100%")
                        
                        # 【关键修复】统计实际下载成功的视频数量
                        actual_success_count = 0
                        new_files = set()
                        # 使用已经设置好的进程开始时间
                        download_start_time = self.process_start_time if hasattr(self, 'process_start_time') else (time.time() - 3600)
                        
                        if self.output_path and os.path.exists(self.output_path):
                            for root, dirs, files in os.walk(self.output_path):
                                for filename in files:
                                    file_ext = os.path.splitext(filename)[1].lower()
                                    if file_ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', 
                                                  '.m4a', '.mp3', '.aac', '.opus', '.flac', '.wav']:
                                        # 排除临时文件
                                        if not filename.endswith(('.part', '.ytdl', '.temp')):
                                            file_path = os.path.join(root, filename)
                                            try:
                                                mtime = os.path.getmtime(file_path)
                                                # 使用已经设置好的进程开始时间
                                                if mtime >= download_start_time - 300:  # 允许5分钟的误差
                                                    new_files.add(file_path)
                                            except:
                                                # 如果无法获取修改时间，则只通过文件名判断
                                                new_files.add(file_path)
                        
                        actual_success_count = len(new_files)
                        self.successful_downloads = actual_success_count  # 更新成功下载计数
                        self.progress_signal.emit(f"📊 实际统计: 成功下载 {actual_success_count} 个新文件")
                        
                        # 检查是否启用了时间片段或章节功能
                        time_sections_enabled = self._time_section_mode_active()
                        split_chapters_enabled = (
                            hasattr(self, 'split_chapters_check')
                            and self.split_chapters_check.isChecked()
                        )
                        
                        if time_sections_enabled:  # 仅保留时间片段检测
                            # 分析生成的文件类型
                            segment_files = []
                            chapter_files = []
                            main_files = []
                            
                            for file_path in new_files:
                                filename = os.path.basename(file_path)
                                # 检查是否是片段文件（通常包含时间信息或分段标记）
                                if re.search(r'\d{2}:\d{2}:\d{2}|\d{2}-\d{2}-\d{2}', filename):
                                    segment_files.append(filename)
                                # 检查是否是章节文件（通常包含chapter或分段编号）
                                elif re.search(r'chapter|分段|part\d+|分段\d+', filename, re.IGNORECASE):
                                    chapter_files.append(filename)
                                else:
                                    main_files.append(filename)
                            
                            if time_sections_enabled and segment_files:
                                self.progress_signal.emit(f"   ⏱️ 时间片段文件: {len(segment_files)} 个")
                                for seg_file in segment_files[:3]:  # 显示前3个
                                    self.progress_signal.emit(f"      └─ {seg_file}")
                                if len(segment_files) > 3:
                                    self.progress_signal.emit(f"      └─ ... 还有 {len(segment_files) - 3} 个片段文件")
                            
                            if split_chapters_enabled and chapter_files:
                                self.progress_signal.emit(f"   📑 章节分割文件: {len(chapter_files)} 个")
                                for chap_file in chapter_files[:3]:  # # 显示前3个
                                    self.progress_signal.emit(f"      └─ {chap_file}")
                                if len(chapter_files) > 3:
                                    self.progress_signal.emit(f"      └─ ... 还有 {len(chapter_files) - 3} 个章节文件")
                        
                        # 【精准统计修复】修正失败视频列表 - 移除实际已下载成功的视频
                        if self.failed_videos:
                            self.progress_signal.emit(f"🔍 检查失败列表中是否有实际下载成功的视频...")
                            actual_failed_videos = []
                            new_files_names = {os.path.basename(f) for f in new_files} if new_files else set()
                            
                            for failed_video in self.failed_videos:
                                failed_title = failed_video.get('title', '')
                                failed_id = failed_video.get('id', '')
                                file_exists = False
                                
                                # 首先检查新增文件列表中是否有匹配的文件名
                                if new_files_names:
                                    for filename in new_files_names:
                                        name_without_ext = os.path.splitext(filename)[0]
                                        name_without_ext = re.sub(r'\.f\d+$', '', name_without_ext)
                                        failed_title_clean = re.sub(r'\.f\d+$', '', failed_title)
                                        
                                        if (name_without_ext in failed_title_clean or 
                                            failed_title_clean in name_without_ext or
                                            name_without_ext.replace('_', ' ') in failed_title_clean or
                                            failed_title_clean.replace('_', ' ') in name_without_ext):
                                            file_exists = True
                                            self.progress_signal.emit(f"✅ 修正: '{failed_title}' 实际已下载，从失败列表移除")
                                            break
                                
                                # 如果在上一步没找到，再检查整个输出目录
                                if not file_exists:
                                    try:
                                        if self.output_path and os.path.exists(self.output_path):
                                            for root, dirs, files in os.walk(self.output_path):
                                                for filename in files:
                                                    # 去除文件扩展名进行比较
                                                    name_without_ext = os.path.splitext(filename)[0]
                                                    
                                                    # 去除临时格式标识（如 .f135, .f140）
                                                    name_without_ext = re.sub(r'\.f\d+$', '', name_without_ext)
                                                    failed_title_clean = re.sub(r'\.f\d+$', '', failed_title)
                                                    
                                                    # 如果文件名包含失败视频的标题（或部分匹配）
                                                    if (name_without_ext in failed_title_clean or 
                                                        failed_title_clean in name_without_ext or
                                                        name_without_ext.replace('_', ' ') in failed_title_clean or
                                                        failed_title_clean.replace('_', ' ') in name_without_ext):
                                                        # 检查是否是最终视频文件（而非临时文件）
                                                        file_ext = os.path.splitext(filename)[1].lower()
                                                        if file_ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', 
                                                                      '.m4a', '.mp3', '.aac', '.opus', '.flac', '.wav']:
                                                            # 检查文件修改时间，确保是本次下载的
                                                            file_path = os.path.join(root, filename)
                                                            try:
                                                                mtime = os.path.getmtime(file_path)
                                                                # 使用已经设置好的下载开始时间，不再重复赋值
                                                                if hasattr(self, 'process_start_time'):
                                                                    download_start_time = self.process_start_time
                                                                else:
                                                                    download_start_time = time.time() - 3600  # 默认1小时前
                                                                
                                                                if mtime >= download_start_time - 300:
                                                                    file_exists = True
                                                                    self.progress_signal.emit(f"✅ 修正: '{failed_title}' 实际已下载，从失败列表移除")
                                                                    break
                                                            except:
                                                                file_exists = True  # 如果无法获取时间，假设文件存在
                                                                break
                                                if file_exists:
                                                    break
                                    except Exception as e:
                                        self.progress_signal.emit(f"⚠️ 检查失败视频时出错: {e}")
                                
                                if not file_exists:
                                    actual_failed_videos.append(failed_video)
                            
                            # 更新失败视频列表
                            self.failed_videos = actual_failed_videos
                            self.progress_signal.emit(f"📊 修正后统计: {len(self.failed_videos)} 个真正失败的视频")
                        
                        # 【关键修复】在修正失败列表后，确保成功下载数准确
                        # actual_success_count 已经基于实际文件统计，但需要确保与修正后的失败列表一致
                        self.successful_downloads = actual_success_count
                        self.progress_signal.emit(f"📊 确认成功下载数（基于实际文件）: {self.successful_downloads}")
                        
                        # 【精准统计修复】确保预计下载数和总计处理数准确
                        # 预计下载数：优先使用用户设置的expected_download_count，如果没有则使用total_videos
                        # 但需要考虑日期过滤跳过的视频（这些视频不会被尝试下载）
                        actual_expected = self.expected_download_count if self.expected_download_count > 0 else self.total_videos
                        
                        # 【关键修复】总计处理数 = 成功下载数 + 失败数 + 因限制跳过的视频数
                        # 这样才能准确反映实际尝试下载的总数量
                        restriction_skipped = len(self.skipped_videos) if hasattr(self, 'skipped_videos') else 0
                        total_processed = self.successful_downloads + len(self.failed_videos) + restriction_skipped
                        
                        # 预计下载数应该反映实际会尝试下载的数量
                        # 如果有日期过滤，yt-dlp会跳过不满足条件的视频
                        # 所以预计下载数 = 实际处理数（成功+失败），因为跳过的视频不会进入下载流程
                        if total_processed > 0:
                            # 实际处理的视频数就是实际尝试下载的数量
                            # 如果总数很大但处理数很小，说明有很多视频被过滤了
                            if actual_expected > total_processed + self.playlist_skipped_count:
                                # 预计下载数应该调整为：实际处理数（这是最准确的）
                                self.progress_signal.emit(f"📊 预计下载数调整为实际尝试下载数（考虑日期过滤，跳过了 {self.playlist_skipped_count} 个视频）")
                                self.expected_download_count = total_processed
                            else:
                                # 如果预计数合理，保持不变
                                self.expected_download_count = actual_expected
                        else:
                            # 如果没有处理任何视频，保持原预计数
                            self.expected_download_count = actual_expected
                        
                        # 【关键报告】显示最终准确统计（使用修正后的数据）
                        # 统计因限制失败的视频数
                        restriction_failed = sum(1 for v in self.failed_videos 
                                               if any(keyword in v.get('reason', '') 
                                                     for keyword in ['限制', '年龄', '地区', '会员', 'restricted', 
                                                                    'age', 'geo', 'membership', 'premium']))
                        
                        self.progress_signal.emit(f"📊 最终统计:")
                        
                        # 【关键修复】如果设置了范围，特别标注（更准确的检测）
                        has_range_set = (self.expected_download_count > 0 and 
                                       self.expected_download_count < self.total_videos and 
                                       self.total_videos > 0)
                        
                        # 检查是否启用了时间片段下载
                        time_sections_enabled = self._time_section_mode_active()
                        sections_summary = self._get_download_sections_summary()
                        
                        if has_range_set:
                            self.progress_signal.emit(f"   播放列表总数: {self.total_videos}")
                            self.progress_signal.emit(f"   设置范围: {self.expected_download_count} 个视频")
                            if time_sections_enabled:
                                self.progress_signal.emit(f"   ⏱️ 时间片段: {sections_summary}")
                            self.progress_signal.emit(f"   ✅ 成功下载: {self.successful_downloads} (基于实际文件统计)")
                            self.progress_signal.emit(f"   ❌ 下载失败: {len(self.failed_videos)} (已移除误报)")
                            if restriction_failed > 0:
                                self.progress_signal.emit(f"      └─ 其中因限制失败: {restriction_failed} 个")
                            if restriction_skipped > 0:
                                self.progress_signal.emit(f"   ⏭️ 因限制跳过: {restriction_skipped} 个")
                            self.progress_signal.emit(f"   📹 范围内处理: {total_processed}")
                        else:
                            self.progress_signal.emit(f"   预计下载: {self.expected_download_count if self.expected_download_count > 0 else self.total_videos}")
                            if time_sections_enabled:
                                self.progress_signal.emit(f"   ⏱️ 时间片段: {sections_summary}")
                            self.progress_signal.emit(f"   ✅ 成功下载: {self.successful_downloads} (基于实际文件统计)")
                            self.progress_signal.emit(f"   ❌ 下载失败: {len(self.failed_videos)} (已移除误报)")
                            if restriction_failed > 0:
                                self.progress_signal.emit(f"      └─ 其中因限制失败: {restriction_failed} 个")
                            if restriction_skipped > 0:
                                self.progress_signal.emit(f"   ⏭️ 因限制跳过: {restriction_skipped} 个")
                            self.progress_signal.emit(f"   📹 总计处理: {total_processed}")
                        
                        # 【关键修复】无论是否有失败，都显示播放列表下载报告
                        # 使用实际统计的数据：预期下载数量、成功数量、失败数量
                        self.progress_signal.emit(f"\n📊 播放列表下载完成，正在生成下载报告...")
                        self.failed_videos_signal.emit(self.failed_videos)
                        
                        # 转换元数据文件为可读格式
                        self._convert_metadata_files_to_readable()
                        
                        # 构建更详细的完成消息
                        success_message = f"下载完成！\n\n✓ 已成功下载 {actual_success_count} 个视频"
                        if len(self.failed_videos) > 0:
                            success_message += f"\n⚠️ {len(self.failed_videos)} 个视频下载失败"
                            if restriction_failed > 0:
                                success_message += f"（其中 {restriction_failed} 个因限制失败）"
                        self.finished_signal.emit(True, success_message)
                        return  # 重要：直接返回，不再执行后续的错误处理逻辑
                    else:
                        self.progress_signal.emit(t("❌ 未检测到任何下载成功的文件，继续错误处理流程..."))
                
                # 检查是否是格式不可用的错误
                if "Requested format is not available" in error_output or ("format" in error_output.lower() and "not available" in error_output.lower()):
                    # 检查是否指定了音频语言（可能是语言不匹配导致格式不可用）
                    has_audio_lang = False
                    try:
                        if self.main_app and hasattr(self.main_app, 'get_audio_language_code'):
                            code = self.main_app.get_audio_language_code()
                            has_audio_lang = bool(code and code != "orig")
                    except Exception:
                        pass
                    
                    if has_audio_lang:
                        self.progress_signal.emit(t("⚠ 指定音频语言可能不可用，尝试使用带语言过滤的通用格式重试..."))
                    else:
                        self.progress_signal.emit(t("所选格式不可用，正在尝试使用最佳可用格式重新下载..."))
                    self._retry_with_best_format()
                # 检查是否是后处理错误（视频和音频合并失败）
                elif "Postprocessing: Error opening input files" in error_output or "Invalid data found when processing input" in error_output:
                    self.progress_signal.emit(t("视频和音频合并失败，正在尝试使用兼容格式重新下载..."))
                    self._retry_with_best_format(postprocessing_error=True)
                elif ("This video is only available for registered users" in error_output or 
                      "Sign in to confirm your age" in error_output or
                      "age-restricted" in error_output.lower() or
                      "content warning" in error_output.lower() or
                      "mature content" in error_output.lower() or
                      "inappropriate content" in error_output.lower() or
                      "restricted mode" in error_output.lower() or
                      "age verification" in error_output.lower() or
                      "confirm your age" in error_output.lower() or
                      "age gate" in error_output.lower() or
                      "Video unavailable" in error_output or
                      "not available in your country" in error_output.lower() or
                      "geo-blocked" in error_output.lower() or
                      "cookies" in error_output.lower() or
                      "fresh cookies" in error_output.lower() or
                      "sign in" in error_output.lower()):
                    
                    # 注意：如果是播放列表下载，应该已经在前面的通用检查中处理了
                    # 这里只处理单个视频下载的情况
                    
                    # 如果有内置Cookie，且没尝试过增强重试，先尝试增强重试
                    if not self.age_restriction_retry_attempted and self.internal_cookie_attached:
                        self.progress_signal.emit(t("检测到需要Cookie或年龄限制错误，正在尝试增强认证重试..."))
                        self.age_restriction_retry_attempted = True
                        self._retry_with_enhanced_auth()
                        return
                    
                    # 否则，或者增强重试失败后，尝试浏览器媒体嗅探回退
                    self.progress_signal.emit(t("解析遇到限制或错误，正在尝试浏览器媒体嗅探回退..."))
                    if self._retry_with_browser_media_fallback():
                        return
                    
                    # 回退失败才显示最终错误
                    if not self.internal_cookie_attached:
                        self.progress_signal.emit(t("❌ 提示：如果下载失败，请先在内置浏览器登录目标网站并点击\"🍪提取Cookie\""))
                    error_message = self._generate_simple_error_message(error_output)
                    self.finished_signal.emit(False, error_message)
                elif "Video unavailable" in error_output or "Private video" in error_output:
                    if self._retry_with_browser_media_fallback():
                        return
                    self.finished_signal.emit(False, "视频不可用或为私有视频")
                elif "Unable to extract" in error_output or "Unsupported URL" in error_output:
                    if self._retry_with_browser_media_fallback():
                        return
                    self.finished_signal.emit(False, "无法提取视频信息，URL可能不受支持")
                else:
                    # 其他任何未被捕获的错误
                    self.progress_signal.emit(t("下载出错，正在尝试浏览器媒体嗅探回退..."))
                    if self._retry_with_browser_media_fallback():
                        return
                    # 生成简洁的错误信息
                    error_message = self._generate_simple_error_message(error_output)
                    self.finished_signal.emit(False, error_message)
                
        except Exception as e:
            self.finished_signal.emit(False, f"程序异常: {str(e)}")
        finally:
            self._cleanup_yt_dlp_temp_path()
            self._cleanup_staging_output_dir()
    
    def _generate_simple_error_message(self, error_output):
        """生成详细的错误信息和解决方案"""
        # 定义错误类型及其详细信息
        error_catalog = {
            # 年龄限制相关
            'age_restriction': {
                'keywords': ('sign in to confirm your age', 'age-restricted', 'age verification', 
                           'age gate', 'content warning', 'inappropriate for some users',
                           'this video may be inappropriate'),
                'title': '❌ 年龄限制错误',
                'description': '该视频设置了年龄限制，需要登录验证',
                'solutions': [
                    '1. 在"高级选项"中启用"限制绕过"功能',
                    '2. 使用内置浏览器登录YouTube账号后提取Cookie',
                    '3. 尝试使用其他下载工具或网站',
                    '4. 确认您的账号已满18岁'
                ],
                'technical': '通常由YouTube的年龄验证机制触发'
            },
            
            # 地理限制相关
            'geo_restriction': {
                'keywords': ('not available in your country', 'geo-blocked', 'geo-restricted', 
                           'blocked in your region', 'copyright restrictions', 'location',
                           'this video is not available in your country'),
                'title': '🌍 地理位置限制',
                'description': '该视频在您所在的地区不可用',
                'solutions': [
                    '1. 在"高级选项"中配置代理服务器',
                    '2. 使用VPN连接到视频允许的地区',
                    '3. 尝试使用--geo-bypass参数（部分网站有效）',
                    '4. 检查视频是否在其他平台可用'
                ],
                'technical': '视频上传者或平台限制了特定地区访问'
            },
            
            # 登录和权限问题
            'authentication': {
                'keywords': ('only available for registered users', 'please sign in', 'login required',
                           'permission denied', 'access denied', 'forbidden', 'authentication',
                           'members only', 'subscribers only', 'premium', 'paid'),
                'title': '🔐 访问权限不足',
                'description': '该视频需要特定权限才能访问',
                'solutions': [
                    '1. 使用内置浏览器登录相关账号',
                    '2. 确认账号是否有观看权限（会员/订阅）',
                    '3. 检查视频是否为私有或仅限好友',
                    '4. 尝试使用Cookie导入功能'
                ],
                'technical': '视频设置为私有、会员专属或需要付费'
            },
            
            # 网络连接问题
            'network': {
                'keywords': ('connection', 'timeout', 'network', 'unable to connect',
                           'timed out', 'connection refused', 'connection reset',
                           'temporary failure', 'name resolution', 'dns', '10054', '10060'),
                'title': '🌐 网络连接问题',
                'description': '无法连接到视频服务器，或连接被远程主机强制关闭',
                'solutions': [
                    '1. 检查您的网络连接是否正常且稳定',
                    '2. 尝试使用代理服务器（特别是云电脑环境）',
                    '3. 检查防火墙或杀毒软件是否拦截了下载器',
                    '4. 尝试刷新DNS缓存（ipconfig /flushdns）',
                    '5. 稍后重试，可能是服务器暂时过载或网络波动'
                ],
                'technical': '网络连接超时、DNS解析失败或远程主机强制关闭连接(10054/10060)'
            },
            
            # 视频不存在或私有
            'unavailable': {
                'keywords': ('video unavailable', 'private video', 'removed', 'deleted',
                           'removed by the user', 'no longer available', 'video has been removed',
                           'this video is unavailable', 'this video does not exist'),
                'title': '📹 视频不可用',
                'description': '该视频不存在、已被删除或设为私有',
                'solutions': [
                    '1. 确认视频链接是否正确',
                    '2. 检查视频是否已被作者删除',
                    '3. 确认视频是否设置为私有',
                    '4. 尝试在浏览器中直接访问链接',
                    '5. 如果是播放列表，检查是否有权限访问'
                ],
                'technical': '视频已被删除、设为私有或URL无效'
            },
            
            # 格式问题
            'format_error': {
                'keywords': ('format not available', 'no suitable formats', 'requested format',
                           'no video formats found', 'format', '--list-formats',
                           'unable to download video data', 'no formats found'),
                'title': '📊 视频格式不支持',
                'description': '请求的视频格式不可用或不支持',
                'solutions': [
                    '1. 点击"格式选择"按钮查看可用格式',
                    '2. 尝试选择"最佳质量"或其他格式',
                    '3. 检查视频是否为直播或特殊格式',
                    '4. 降低分辨率要求（如选择720p而非1080p）',
                    '5. 尝试下载音频而非视频'
                ],
                'technical': '所选格式不存在或视频源不提供该格式'
            },
            
            # 提取失败
            'extraction_error': {
                'keywords': ('unable to extract', 'extraction failed', 'unsupported url',
                           'no video found', 'could not find', 'unable to download webpage',
                           'http error 404', '404 not found'),
                'title': '⚠️ 信息提取失败',
                'description': '无法从网页中提取视频信息',
                'solutions': [
                    '1. 确认URL是否正确且完整',
                    '2. 检查该网站是否被yt-dlp支持',
                    '3. 尝试更新yt-dlp到最新版本',
                    '4. 查看进度窗口中的详细错误信息',
                    '5. 尝试直接在浏览器中打开链接确认可访问'
                ],
                'technical': 'yt-dlp无法解析该网站或视频信息'
            },
            
            # 服务器问题
            'server_error': {
                'keywords': ('server error', '500', '503', 'service unavailable',
                           'internal server error', 'bad gateway', '502', '504',
                           'gateway timeout', 'server is down'),
                'title': '🔧 服务器错误',
                'description': '视频平台服务器出现问题',
                'solutions': [
                    '1. 稍等几分钟后重试',
                    '2. 检查视频网站是否正常运行',
                    '3. 尝试使用代理或VPN',
                    '4. 降低并发连接数',
                    '5. 在非高峰时段重试'
                ],
                'technical': '视频平台服务器返回5xx错误代码'
            },
            
            # 违规内容
            'policy_violation': {
                'keywords': ('violates', 'terms of service', 'community guidelines',
                           'policy violation', 'copyright', 'dmca', 'blocked due to',
                           'content id claim'),
                'title': '⚖️ 内容违规',
                'description': '该视频因违反平台规定而受限',
                'solutions': [
                    '1. 视频可能因版权问题被限制',
                    '2. 检查是否有替代链接可用',
                    '3. 确认视频内容是否违反使用条款',
                    '4. 尝试从原作者处获取授权'
                ],
                'technical': '视频因违反平台政策或版权问题被限制'
            },
            
            # 速率限制
            'rate_limit': {
                'keywords': ('too many requests', 'rate limit', 'slow down', 'throttled',
                           '429', 'quota exceeded', 'api limit'),
                'title': '⏱️ 请求过于频繁',
                'description': '触发了平台的访问频率限制',
                'solutions': [
                    '1. 等待几分钟后重试',
                    '2. 在"高级选项"中增加下载延迟',
                    '3. 减少并发下载数量',
                    '4. 使用不同的IP地址或代理',
                    '5. 避免短时间内大量下载'
                ],
                'technical': '超过平台允许的请求频率限制'
            },
            
            # Cookie/认证问题
            'cookie_error': {
                'keywords': ('cookie', 'authentication', 'session', 'token', 'csrf',
                           'invalid credentials', 'login failed', 'authentication failed'),
                'title': '🍪 Cookie认证问题',
                'description': 'Cookie已过期或认证失败',
                'solutions': [
                    '1. 重新使用内置浏览器登录并提取Cookie',
                    '2. 检查Cookie文件是否存在且有效',
                    '3. 确认账号登录状态是否正常',
                    '4. 清除浏览器缓存后重新登录',
                    '5. 检查账号是否被限制或封禁'
                ],
                'technical': 'Cookie已过期或认证令牌无效'
            },
            
            # FFmpeg问题
            'ffmpeg_error': {
                'keywords': ('ffmpeg', 'postprocessor', 'conversion failed', 'merging',
                           'muxing', 'could not write header', 'invalid data found'),
                'title': '🎬 视频处理失败',
                'description': 'FFmpeg处理视频时出错',
                'solutions': [
                    '1. 确认ffmpeg.exe在程序目录下',
                    '2. 尝试重新下载ffmpeg',
                    '3. 检查磁盘空间是否充足',
                    '4. 尝试选择不同的输出格式',
                    '5. 关闭字幕嵌入或其他后处理功能'
                ],
                'technical': 'FFmpeg后处理或格式转换失败'
            },
            
            # SSL/证书/TLS问题
            'ssl_error': {
                'keywords': ('ssl', 'certificate', 'cert', 'https', 'tls',
                           'certificate verify failed', 'ssl handshake',
                           'curl: (35)', 'tls connect error', 'openssl_internal',
                           'invalid library', 'tls fingerprint'),
                'title': '🔒 SSL/TLS连接问题',
                'description': 'HTTPS连接或TLS握手失败，可能是TLS指纹检测被拦截',
                'solutions': [
                    '1. 程序已自动启用浏览器指纹模拟，请重试下载',
                    '2. 检查网络连接是否正常，尝试切换网络',
                    '3. 如使用代理，检查代理设置是否正确',
                    '4. 检查系统时间是否正确（时间偏差会导致证书验证失败）',
                    '5. 更新yt-dlp到最新版本以获取最新的指纹模拟支持'
                ],
                'technical': 'YouTube等网站使用TLS指纹检测，需要curl_cffi模拟浏览器请求。curl error (35)表示TLS握手失败。'
            }
        }
        
        # 将错误输出转换为小写以便匹配
        error_lower = error_output.lower()
        
        # 检查错误类型
        matched_error = None
        for error_type, error_info in error_catalog.items():
            if any(keyword in error_lower for keyword in error_info['keywords']):
                matched_error = error_info
                break
        
        # 如果匹配到特定错误类型，生成详细信息
        if matched_error:
            error_msg = f"{matched_error['title']}\n"
            error_msg += f"{'='*50}\n"
            error_msg += f"📋 问题描述：{matched_error['description']}\n\n"
            error_msg += f"💡 解决方案：\n"
            for solution in matched_error['solutions']:
                error_msg += f"   {solution}\n"
            error_msg += f"\n🔧 技术细节：{matched_error['technical']}\n"
            
            # 添加原始错误的关键部分（如果有）
            key_error_line = self._extract_key_error_line(error_output)
            if key_error_line:
                error_msg += f"\n📝 原始错误：{key_error_line}"
            
            return error_msg
        
        # 如果没有匹配到特定模式，尝试提取并格式化关键错误信息
        key_error = self._extract_key_error_line(error_output)
        if key_error:
            return (f"❌ 下载失败\n"
                   f"{'='*50}\n"
                   f"📋 错误信息：{key_error}\n\n"
                   f"💡 建议：\n"
                   f"   1. 检查网络连接是否正常\n"
                   f"   2. 确认视频链接是否有效\n"
                   f"   3. 查看进度窗口中的详细日志\n"
                   f"   4. 尝试选择不同的视频格式\n"
                   f"   5. 更新yt-dlp到最新版本")
        
        # 默认通用错误信息
        return (f"❌ 下载失败\n"
               f"{'='*50}\n"
               f"📋 未能识别具体错误类型\n\n"
               f"💡 通用解决方案：\n"
               f"   1. 检查网络连接和防火墙设置\n"
               f"   2. 确认视频链接是否正确完整\n"
               f"   3. 尝试更换下载格式或分辨率\n"
               f"   4. 查看进度窗口中的详细错误日志\n"
               f"   5. 更新yt-dlp和ffmpeg到最新版本\n"
               f"   6. 如问题持续，尝试使用代理或VPN")
    
    def _extract_key_error_line(self, error_output):
        """从错误输出中提取关键错误信息"""
        lines = error_output.strip().split('\n')
        
        # 优先查找ERROR标记的行
        for line in lines:
            line = line.strip()
            if line and 'ERROR:' in line:
                # 清理ERROR:前缀
                clean_line = line.replace('ERROR:', '').strip()
                if clean_line and len(clean_line) < 200:  # 限制长度
                    return clean_line
        
        # 查找包含error或failed的行
        for line in lines:
            line = line.strip()
            if line and ('error' in line.lower() or 'failed' in line.lower()):
                # 清理技术细节
                if ':' in line:
                    parts = line.split(':', 1)
                    if len(parts) > 1:
                        clean_msg = parts[1].strip()
                        if len(clean_msg) < 200:
                            return clean_msg
                elif len(line) < 200:
                    return line
        
        # 返回最后几行非空内容
        for line in reversed(lines):
            line = line.strip()
            if line and len(line) < 200 and not line.startswith('['):
                return line
        
        return None
    
    def _convert_metadata_files_to_readable(self):
        """将元数据文件转换为可直接打开查看的格式
        
        功能：
        1. 将 .description 文件转换为 .description.txt 文件
        2. 从 .info.json 文件中提取评论，生成 .comments.txt 文件
        3. 从 .info.json 文件中提取描述，生成 .description.txt 文件（如果 .description 不存在）
        """
        import os
        import json
        import glob
        
        try:
            if not self.output_path or not os.path.exists(self.output_path):
                return
            
            converted_count = 0
            
            # 遍历输出目录下的所有文件
            for root, dirs, files in os.walk(self.output_path):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    
                    # 处理 .description 文件 - 转换为 .txt 格式
                    if filename.endswith('.description'):
                        try:
                            txt_path = file_path + '.txt'
                            if not os.path.exists(txt_path):
                                # 读取描述内容
                                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                                    description_content = f.read()
                                
                                # 写入 .txt 文件
                                with open(txt_path, 'w', encoding='utf-8') as f:
                                    f.write("=" * 60 + "\n")
                                    f.write("视频描述 (Video Description)\n")
                                    f.write("=" * 60 + "\n\n")
                                    f.write(description_content)
                                
                                self.progress_signal.emit(f"📄 已转换描述文件: {os.path.basename(txt_path)}")
                                converted_count += 1
                        except Exception as e:
                            self.progress_signal.emit(f"⚠️ 转换描述文件时出错: {e}")
                    
                    # 处理 .info.json 文件 - 提取评论和描述
                    if filename.endswith('.info.json'):
                        try:
                            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                                info_data = json.load(f)
                            
                            base_name = file_path[:-10]  # 移除 .info.json
                            
                            # 提取视频描述（如果 .description.txt 不存在）
                            description_txt_path = base_name + '.description.txt'
                            if not os.path.exists(description_txt_path) and not os.path.exists(base_name + '.description'):
                                description = info_data.get('description', '')
                                if description:
                                    title = info_data.get('title', '未知标题')
                                    uploader = info_data.get('uploader', info_data.get('channel', '未知作者'))
                                    upload_date = info_data.get('upload_date', '')
                                    if upload_date and len(upload_date) == 8:
                                        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
                                    view_count = info_data.get('view_count', 0)
                                    like_count = info_data.get('like_count', 0)
                                    
                                    with open(description_txt_path, 'w', encoding='utf-8') as f:
                                        f.write("=" * 60 + "\n")
                                        f.write("视频信息 (Video Information)\n")
                                        f.write("=" * 60 + "\n\n")
                                        f.write(f"标题: {title}\n")
                                        f.write(f"作者: {uploader}\n")
                                        if upload_date:
                                            f.write(f"发布日期: {upload_date}\n")
                                        if view_count:
                                            f.write(f"观看次数: {view_count:,}\n")
                                        if like_count:
                                            f.write(f"点赞次数: {like_count:,}\n")
                                        f.write("\n" + "-" * 60 + "\n")
                                        f.write("视频描述:\n")
                                        f.write("-" * 60 + "\n\n")
                                        f.write(description)
                                    
                                    self.progress_signal.emit(f"📄 已生成描述文件: {os.path.basename(description_txt_path)}")
                                    converted_count += 1
                            
                            # 提取评论
                            comments = info_data.get('comments', [])
                            if comments:
                                comments_txt_path = base_name + '.comments.txt'
                                if not os.path.exists(comments_txt_path):
                                    title = info_data.get('title', '未知标题')
                                    
                                    with open(comments_txt_path, 'w', encoding='utf-8') as f:
                                        f.write("=" * 60 + "\n")
                                        f.write(f"视频评论 (Video Comments)\n")
                                        f.write(f"视频标题: {title}\n")
                                        f.write(f"评论数量: {len(comments)}\n")
                                        f.write("=" * 60 + "\n\n")
                                        
                                        for i, comment in enumerate(comments, 1):
                                            author = comment.get('author', '匿名用户')
                                            text = comment.get('text', '')
                                            like_count = comment.get('like_count', 0)
                                            timestamp = comment.get('timestamp', 0)
                                            
                                            # 格式化时间戳
                                            time_str = ''
                                            if timestamp:
                                                try:
                                                    from datetime import datetime
                                                    time_str = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
                                                except:
                                                    time_str = str(timestamp)
                                            
                                            f.write(f"【评论 #{i}】\n")
                                            f.write(f"用户: {author}\n")
                                            if time_str:
                                                f.write(f"时间: {time_str}\n")
                                            if like_count:
                                                f.write(f"点赞: {like_count}\n")
                                            f.write(f"\n{text}\n")
                                            f.write("\n" + "-" * 40 + "\n\n")
                                            
                                            # 处理回复评论
                                            replies = comment.get('replies', [])
                                            for j, reply in enumerate(replies, 1):
                                                reply_author = reply.get('author', '匿名用户')
                                                reply_text = reply.get('text', '')
                                                reply_likes = reply.get('like_count', 0)
                                                
                                                f.write(f"  └─ 回复 #{j}: {reply_author}\n")
                                                if reply_likes:
                                                    f.write(f"     点赞: {reply_likes}\n")
                                                f.write(f"     {reply_text}\n\n")
                                    
                                    self.progress_signal.emit(f"💬 已生成评论文件: {os.path.basename(comments_txt_path)} ({len(comments)} 条评论)")
                                    converted_count += 1
                        except Exception as e:
                            self.progress_signal.emit(f"⚠️ 处理JSON元数据时出错: {e}")
            
            if converted_count > 0:
                self.progress_signal.emit(f"✅ 元数据文件转换完成，共转换 {converted_count} 个文件为可读格式")
            
            # 第二遍：清理原始元数据文件（转换成功后删除）
            deleted_count = 0
            for root, dirs, files in os.walk(self.output_path):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    
                    # 删除 .description 文件（如果对应的 .description.txt 存在）
                    if filename.endswith('.description') and not filename.endswith('.description.txt'):
                        txt_path = file_path + '.txt'
                        if os.path.exists(txt_path):
                            try:
                                os.remove(file_path)
                                self.progress_signal.emit(f"🗑️ 已删除原始描述文件: {filename}")
                                deleted_count += 1
                            except Exception as e:
                                self.progress_signal.emit(f"⚠️ 删除原始描述文件失败: {e}")
                    
                    # 删除 .info.json 文件（如果对应的 .comments.txt 或 .description.txt 存在）
                    if filename.endswith('.info.json'):
                        base_name = file_path[:-10]  # 移除 .info.json
                        comments_txt = base_name + '.comments.txt'
                        description_txt = base_name + '.description.txt'
                        # 只有当生成了可读文件后才删除原始JSON
                        if os.path.exists(comments_txt) or os.path.exists(description_txt):
                            try:
                                os.remove(file_path)
                                self.progress_signal.emit(f"🗑️ 已删除原始JSON文件: {filename}")
                                deleted_count += 1
                            except Exception as e:
                                self.progress_signal.emit(f"⚠️ 删除原始JSON文件失败: {e}")
            
            if deleted_count > 0:
                self.progress_signal.emit(f"🧹 清理完成，共删除 {deleted_count} 个原始元数据文件")
            
        except Exception as e:
            self.progress_signal.emit(f"⚠️ 转换元数据文件时出现异常: {e}")
    
    def _retry_with_enhanced_auth(self):
        """使用增强的认证参数重试下载"""
        try:
            # 构建增强的认证选项
            enhanced_options = list(self.additional_options) if self.additional_options else []  # 复制原始选项
            
            # 移除可能冲突的选项
            enhanced_options = [opt for opt in enhanced_options if not opt.startswith('--extractor-args')]
            
            # 添加增强的年龄限制绕过选项（遵循yt-dlp README）
            enhanced_options.extend([
                "--extractor-args", "youtube:player_client=default;playback_wait=6",
                "--retries", "8",
                "--fragment-retries", "8",
                "--ignore-errors",
            ])
        
            self.progress_signal.emit(t("正在使用增强认证参数重新启动下载..."))
            
            # 标记已尝试重试
            self.age_restriction_retry_attempted = True
            
            # 构建新的下载命令
            cmd = ["yt-dlp.exe"]
            self._apply_base_yt_dlp_safety(cmd)
            
            # 添加格式选项
            if self.format_option:
                if self.format_option and " -x " in self.format_option:
                    parts = self.format_option.split(" -x ")
                    if len(parts) == 2:
                        format_part = parts[0].strip()
                        audio_part = parts[1].strip()
                        if format_part:
                            cmd.extend(["-f", format_part])
                        cmd.extend(["-x", "--audio-format", audio_part])
                    else:
                        cmd.extend(["-f", self.format_option])
                else:
                    cmd.extend(["-f", self.format_option])
            
            # 添加输出路径
            if self.output_path:
                is_playlist_job = self._is_effective_playlist_job()
                output_base_dir = self.output_path
                if not is_playlist_job:
                    staged_dir = self._prepare_staging_output_dir()
                    if staged_dir:
                        output_base_dir = staged_dir
                output_template = self._build_output_template(output_base_dir)
                cmd.extend(["-o", output_template])
            
            # 注入 Cookie（统一逻辑：候选域名 + 最近回退）
            try:
                self._attach_internal_cookie(
                    cmd,
                    url=self.url,
                    direct_msg="🍪 增强重试加载内置浏览器Cookie: ",
                    fallback_msg="🍪 增强重试使用内置浏览器Cookie: ",
                )
            except Exception:
                pass

            # 添加增强选项
            cmd.extend(enhanced_options)

            # 将分片/中间文件放到系统临时目录
            self._ensure_yt_dlp_temp_path(cmd)
            
            # 添加进度显示格式
            cmd.extend(["--progress-template", "下载中: %(progress.downloaded_bytes)s/%(progress.total_bytes)s (%(progress.eta)s) 速度: %(progress.speed)s"])
            
            # 添加URL
            cmd.append(self.url)
            
            self.progress_signal.emit(f"执行增强认证重试命令: {' '.join(cmd)}")
            
            # 增加IP位置重试计数
            self.ip_location_retry_count += 1
            
            # 终止当前进程（如果存在）
            if self.process and self.process.poll() is None:
                self.process.terminate()
                self.process.wait()
            
            # 启动新的下载进程
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            # 继续监控新进程的输出
            error_output = ""
            progress_pattern = re.compile(r'(\d+(?:\.\d+)?)%')
            downloaded_pattern = re.compile(r'下载中: ([\d.]+[KMGT]?i?B)/([\d.]+[KMGT]?i?B)')
            alt_downloaded_pattern = re.compile(r'([\d.]+[KMGT]?i?B)\s+of\s+([\d.]+[KMGT]?i?B)')
            speed_pattern = re.compile(r'速度: ([\d.]+[KMGT]?i?B/s)')
            eta_pattern = re.compile(r'\(([\d:]+)\)')
            
            last_progress_update = time.time()
            last_percent = 0
            download_start_time = time.time()
            # 【修复】发送 -1 信号重置进度条，而不是 0.01（会被"只增不减"逻辑拒绝）
            self.progress_percent_signal.emit(-1)
            last_estimated_progress = 0.01
            
            # 【修复】多流下载进度跟踪（受限视频通常需要分别下载视频和音频流）
            stream_count = 0  # 已检测到的流数量
            current_stream_total = 0  # 当前流的总大小
            
            for line in self.process.stdout:
                # 检查进程是否被外部终止
                if self.process and self.process.poll() is not None:
                    self.progress_signal.emit(t("检测到进程已终止，退出下载循环"))
                    break
                elif not self.process:
                    self.progress_signal.emit(t("检测到进程已终止，退出下载循环"))
                    break
                    
                stripped_line = line.strip()
                self.progress_signal.emit(stripped_line)
                
                # 检测地理限制绕过成功的提示
                if any(keyword in stripped_line.lower() for keyword in [
                    "bypassing geo restriction", "geo restriction bypassed", 
                    "using proxy", "proxy connection established",
                    "x-forwarded-for", "geo bypass", "country bypass"]):
                    self.progress_signal.emit(t("✅ 地理限制绕过成功！正在继续下载..."))
                    
                # 检测代理连接成功 - 增强版
                if any(keyword in stripped_line.lower() for keyword in [
                    "proxy", "socks", "http proxy"]):
                    if any(success_keyword in stripped_line.lower() for success_keyword in [
                        "connected", "established", "success", "ok"]):
                        self.progress_signal.emit("=" * 60)
                        self.progress_signal.emit(t("🌐 代理连接已建立！"))
                        self.progress_signal.emit(t("✅ 代理服务器响应正常"))
                        self.progress_signal.emit(t("✅ 下载流量正通过代理传输"))
                        self.progress_signal.emit("=" * 60)
                
                # 检测2024年更新的年龄限制和地理限制错误模式
                age_restriction_patterns = [
                    "sign in to confirm your age", "age-restricted", "content warning",
                    "mature content", "inappropriate content", "restricted mode",
                    "age verification", "confirm your age", "age gate",
                    "this video is only available for registered users",
                    "video unavailable", "private video", "deleted video",
                    "requires sign in", "login required", "account required",
                    "age-gated", "18+ content", "adult content",
                    "community guidelines", "policy violation"
                ]
                
                geo_restriction_patterns = [
                    "not available in your country", "geo-blocked", "region blocked",
                    "geographic restriction", "location restricted", "country restriction",
                    "regional block", "territory restriction", "ip blocked"
                ]
                
                # 检查是否匹配任何限制模式
                is_age_restricted = any(pattern in stripped_line.lower() for pattern in age_restriction_patterns)
                is_geo_restricted = any(pattern in stripped_line.lower() for pattern in geo_restriction_patterns)
                
                if is_age_restricted or is_geo_restricted:
                    restriction_type = "年龄限制" if is_age_restricted else "地理限制"
                    self.progress_signal.emit(f"❌ {restriction_type}绕过失败，增强认证重试无效")
                    
                    if self.process:
                        self.process.terminate()
                    
                    # 生成简洁的错误信息
                    error_message = self._generate_simple_error_message(stripped_line)
                    self.finished_signal.emit(False, error_message)
                    return
                
                # 【修复】检测新的流开始（通过 Destination 行）
                if "[download] Destination:" in stripped_line:
                    stream_count += 1
                    current_stream_total = 0
                    self.progress_signal.emit(f"📥 检测到第 {stream_count} 个流开始下载")
                
                # 解析下载进度
                # 【修复】支持多种进度格式：
                # 1. 百分比格式：85.5%
                # 2. 纯字节数格式：下载中: 1024/7180326
                # 3. 带单位格式：下载中: 1.5MiB/10.5MiB
                
                # 【修复】多流下载进度权重分配：
                # 流1（视频）: 0-45%
                # 流2（音频）: 45-90%
                # 合并: 90-100%
                
                raw_percent = None
                
                progress_match = progress_pattern.search(stripped_line)
                if progress_match:
                    try:
                        raw_percent = float(progress_match.group(1))
                    except ValueError:
                        pass
                else:
                    # 【修复】尝试从纯字节数格式解析进度：下载中: 1024/7180326
                    bytes_pattern = re.compile(r'下载中:\s*(\d+)/(\d+)')
                    bytes_match = bytes_pattern.search(stripped_line)
                    if bytes_match:
                        try:
                            downloaded = int(bytes_match.group(1))
                            total = int(bytes_match.group(2))
                            if total > 0:
                                raw_percent = (downloaded / total) * 100
                                current_stream_total = total
                        except (ValueError, ZeroDivisionError):
                            pass
                
                # 如果成功解析出进度，计算调整后的总体进度
                if raw_percent is not None:
                    raw_percent = max(0, min(100, raw_percent))
                    
                    # 根据当前流数量计算调整后的进度
                    if stream_count <= 1:
                        # 第一个流（视频）：占 0-45%
                        adjusted_percent = raw_percent * 0.45
                    elif stream_count == 2:
                        # 第二个流（音频）：占 45-90%
                        adjusted_percent = 45 + (raw_percent * 0.45)
                    else:
                        # 更多流或单流情况：直接使用原始进度
                        adjusted_percent = raw_percent
                    
                    adjusted_percent = max(0, min(100, adjusted_percent))
                    
                    current_time = time.time()
                    # 只在进度变化超过1%或时间间隔超过0.5秒时更新
                    if abs(adjusted_percent - last_percent) >= 1 or (current_time - last_progress_update) >= 0.5:
                        self.progress_percent_signal.emit(adjusted_percent)
                        self.progress_signal.emit(f"收到进度信号: {adjusted_percent:.1f}% (流{stream_count}: {raw_percent:.1f}%)")
                        last_progress_update = current_time
                        last_percent = adjusted_percent
                
                # 检测合并阶段
                if "[Merger]" in stripped_line:
                    self.progress_signal.emit(t("🔄 正在合并视频和音频流..."))
                    self.progress_percent_signal.emit(92.0)
                    last_percent = 92.0
                
                # 检测删除临时文件（合并完成的标志）
                if "Deleting original file" in stripped_line:
                    self.progress_percent_signal.emit(98.0)
                    last_percent = 98.0
                
                error_output += stripped_line + "\n"
                
                if "[download] 100%" in stripped_line and stream_count <= 1:
                    # 只有单流下载时，100%才是真正的完成
                    self.progress_percent_signal.emit(100.0)
            
            # 等待进程结束
            return_code = self.process.wait()
            
            self.progress_signal.emit(f"进程结束，返回代码: {return_code}")
            
            if return_code == 0:
                if self._staging_output_dir:
                    moved = self._move_staging_outputs_to_target()
                    if moved:
                        self.progress_signal.emit(f"✓ 增强重试已搬运 {len(moved)} 个结果文件")
                self.progress_signal.emit(t("✓ 下载成功完成"))
                self.finished_signal.emit(True, "下载完成")
            else:
                self.progress_signal.emit(t("增强重试下载失败，尝试使用浏览器媒体嗅探回退..."))
                if self._retry_with_browser_media_fallback():
                    return
                # 使用简洁的错误信息生成器
                error_msg = self._generate_simple_error_message(error_output)
                self.progress_signal.emit(f"✗ {error_msg}")
                self.finished_signal.emit(False, error_msg)
            
        except Exception as e:
            self.finished_signal.emit(False, f"重试失败: {str(e)}")
    

    
    def _load_browser_capture_preference(self):
        try:
            settings_file = getattr(_playwright_browser_module, "SETTINGS_FILE", "")
            if settings_file and os.path.exists(settings_file):
                with open(settings_file, "r", encoding="utf-8") as f:
                    browser_id = str(json.load(f).get("browser", "") or "").strip().lower()
                    if browser_id in ("chromium", "chrome", "edge"):
                        return browser_id
        except Exception:
            pass
        return "chromium"

    def _get_existing_browser_captured_media(self):
        try:
            browser_window = getattr(self.main_app, "browser_window", None)
            if not browser_window or not hasattr(browser_window, "get_captured_media_snapshot"):
                return []

            page_url = str(self.url or "").strip()
            page_host = QUrl(page_url).host().lower().strip()
            snapshot = browser_window.get_captured_media_snapshot() or []
            matched = []

            for item in snapshot:
                candidate_page = str(item.get("page_url", "") or "").strip()
                candidate_url = str(item.get("url", "") or "").strip()
                if candidate_page and candidate_page == page_url:
                    matched.append(dict(item))
                    continue
                if candidate_page and page_host and QUrl(candidate_page).host().lower().strip() == page_host:
                    matched.append(dict(item))
                    continue
                if candidate_url and candidate_url == page_url:
                    matched.append(dict(item))

            return matched
        except Exception:
            return []

    def _is_stable_captured_media(self, media_info):
        url = str((media_info or {}).get("url", "") or "").lower()
        content_type = str((media_info or {}).get("content_type", "") or "").lower()
        media_type = str((media_info or {}).get("type", "") or "")
        is_playing = bool((media_info or {}).get("is_playing"))
        is_preload = bool((media_info or {}).get("is_preload"))

        if is_playing and not is_preload:
            return True
        if ".mp4" in url or media_type == "MP4":
            return True
        if "video/" in content_type and not is_preload:
            return True
        return False

    def _pick_best_captured_media(self, media_candidates):
        if not media_candidates:
            return None

        page_host = QUrl(self.url).host().lower().strip()

        def _score(item):
            url = str(item.get("url", "") or "")
            url_l = url.lower()
            content_type = str(item.get("content_type", "") or "").lower()
            media_type = str(item.get("type", "") or "").strip()
            page_url = str(item.get("page_url", "") or "")
            source = str(item.get("source", "") or "").lower()
            is_playing = bool(item.get("is_playing"))
            is_preload = bool(item.get("is_preload"))

            score = {
                "MP4": 150,
                "Video": 132,
                "HLS": 126,
                "DASH": 122,
                "Smooth": 130,
                "Media": 100,
                "YouTube": 95,
                "Bilibili": 95,
                "Douyin": 95,
                "TikTok": 95,
                "Audio": 30,
            }.get(media_type, 80)

            if ".m3u8" in url_l or "mpegurl" in content_type:
                score += 12
            elif ".mpd" in url_l or "dash" in content_type:
                score += 10
            elif ".mp4" in url_l or "video/" in content_type:
                score += 28

            if is_playing:
                score += 20
            if is_preload and not is_playing:
                score -= 24
            if source == "network":
                score += 8

            if any(fragment_ext in url_l for fragment_ext in (".ts", ".m4s", ".m4a")):
                score -= 80
            if "audio/" in content_type or media_type == "Audio":
                score -= 35

            if page_url and page_host and QUrl(page_url).host().lower().strip() == page_host:
                score += 5

            return score

        return max(media_candidates, key=_score)

    def _capture_media_with_playwright(self, page_url, timeout_seconds=25.0):
        try:
            build_launcher = getattr(_playwright_browser_module, "_build_launcher_bootstrap_script", None)
            get_python_with_playwright = getattr(_playwright_browser_module, "get_python_with_playwright", None)
            browser_data_dir = getattr(_playwright_browser_module, "BROWSER_DATA_DIR", "")
            if not callable(build_launcher) or not callable(get_python_with_playwright) or not browser_data_dir:
                self.progress_signal.emit(t("⚠️ Playwright 媒体捕获模块不可用，无法自动回退"))
                return None

            os.makedirs(browser_data_dir, exist_ok=True)
            script_path = os.path.join(browser_data_dir, "_launcher_capture.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(build_launcher(os.path.abspath(getattr(_playwright_browser_module, "__file__", "playwright_browser.py"))))

            cookies_dir = getattr(auth_utils, "COOKIE_DIR", os.path.join(browser_data_dir, "cookies"))
            os.makedirs(cookies_dir, exist_ok=True)

            selected_browser = self._load_browser_capture_preference()
            python_exe = get_python_with_playwright()
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8:replace"

            if getattr(sys, "frozen", False):
                cmd = [python_exe, "--playwright-launcher", script_path, selected_browser, cookies_dir]
            else:
                cmd = [python_exe, script_path, selected_browser, cookies_dir]

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            browser_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creationflags,
            )

            ready_event = threading.Event()
            media_lock = threading.Lock()
            media_candidates = []
            last_media_at = [0.0]
            capture_logs = []

            def _reader():
                try:
                    for raw_line in browser_process.stdout:
                        line = raw_line.strip()
                        if not line:
                            continue
                        capture_logs.append(line)
                        if len(capture_logs) > 40:
                            capture_logs.pop(0)
                        if "[BROWSER] READY" in line:
                            ready_event.set()
                            continue
                        if "[MEDIA]" not in line:
                            continue
                        try:
                            media_info = json.loads(line.split("[MEDIA]", 1)[1].strip())
                            media_url = str(media_info.get("url", "") or "").strip()
                            if not media_url:
                                continue
                            with media_lock:
                                media_candidates.append(media_info)
                                last_media_at[0] = time.time()
                        except Exception:
                            continue
                except Exception:
                    pass

            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()

            if not ready_event.wait(timeout=20):
                try:
                    browser_process.terminate()
                except Exception:
                    pass
                self.progress_signal.emit(t("⚠️ 浏览器媒体捕获启动超时"))
                return None

            if browser_process.stdin:
                browser_process.stdin.write("ENABLE_CAPTURE\n")
                browser_process.stdin.write(f"GOTO {page_url}\n")
                browser_process.stdin.flush()

            capture_deadline = time.time() + max(8.0, float(timeout_seconds))
            settle_after_stable_hit = 1.5

            while time.time() < capture_deadline:
                if self._cancelled:
                    break
                if browser_process.poll() is not None:
                    break
                with media_lock:
                    current_candidates = list(media_candidates)
                    last_hit_at = last_media_at[0]
                has_media = bool(current_candidates)
                has_stable_media = any(self._is_stable_captured_media(item) for item in current_candidates)
                if has_stable_media and last_hit_at and (time.time() - last_hit_at) >= settle_after_stable_hit:
                    break
                if (not has_stable_media) and has_media and (time.time() - last_hit_at) >= 5.0:
                    # 只有预加载或不稳定候选时，多等一会儿，给主流地址出现留时间。
                    time.sleep(0.2)
                    continue
                time.sleep(0.2)

            try:
                if browser_process.stdin:
                    browser_process.stdin.write("QUIT\n")
                    browser_process.stdin.flush()
            except Exception:
                pass

            try:
                browser_process.wait(timeout=5)
            except Exception:
                try:
                    browser_process.terminate()
                    browser_process.wait(timeout=3)
                except Exception:
                    try:
                        browser_process.kill()
                    except Exception:
                        pass

            with media_lock:
                selected_media = self._pick_best_captured_media(list(media_candidates))

            if not selected_media and capture_logs:
                self.progress_signal.emit(f"⚠️ 媒体嗅探未命中可下载资源，最后日志: {capture_logs[-1]}")

            return selected_media
        except Exception as e:
            self.progress_signal.emit(f"⚠️ 浏览器媒体嗅探失败: {e}")
            return None

    def _retry_with_captured_media(self, media_info):
        direct_url = str((media_info or {}).get("url", "") or "").strip()
        if not direct_url:
            self.finished_signal.emit(False, "媒体嗅探成功，但未获取到有效的媒体地址")
            return

        try:
            media_type = str(media_info.get("type", "") or "Media")
            media_title = str(media_info.get("title", "") or "").strip()
            referer_url = str(media_info.get("page_url", "") or self.url).strip() or self.url
            resolved_media_title = choose_better_download_title(media_title, self.preferred_title)
            if resolved_media_title:
                self.preferred_title = resolved_media_title
                self.current_video_title = resolved_media_title

            self.progress_signal.emit(f"✅ 嗅探到媒体直链，类型: {media_type}")
            self.progress_signal.emit(f"🔁 使用媒体直链重试下载: {direct_url[:160]}")

            cmd = ["yt-dlp.exe"]
            self._apply_base_yt_dlp_safety(cmd)

            if self.media_capture_download:
                self.progress_signal.emit(t("ℹ 媒体直链重试已忽略主页面格式设置，继续使用兼容下载策略"))
            elif self.format_option and (" -x " in self.format_option or "--extract-audio" in self.format_option):
                parts = self.format_option.split(" -x ") if " -x " in self.format_option else [self.format_option.replace("--extract-audio", "").strip()]
                if parts and parts[0].strip():
                    cmd.extend(["-f", parts[0].strip()])
                cmd.extend(["--extract-audio"])
                if len(parts) > 1 and "--audio-format" in parts[1]:
                    audio_format = parts[1].split("--audio-format", 1)[1].strip().split()[0]
                    cmd.extend(["--audio-format", audio_format])
                elif "--audio-format" in self.format_option:
                    audio_format = self.format_option.split("--audio-format", 1)[1].strip().split()[0]
                    cmd.extend(["--audio-format", audio_format])
                else:
                    cmd.extend(["--audio-format", "mp3"])
                if "--audio-quality" in self.format_option:
                    audio_quality = self.format_option.split("--audio-quality", 1)[1].strip().split()[0]
                    cmd.extend(["--audio-quality", audio_quality])
                else:
                    cmd.extend(["--audio-quality", "0"])
            elif self.format_option:
                self.progress_signal.emit(t("ℹ 媒体直链回退已忽略原复杂格式筛选，避免与直链冲突"))

            try:
                self._attach_internal_cookie(
                    cmd,
                    url=referer_url,
                    direct_msg="🍪 媒体直链重试加载内置浏览器Cookie: ",
                    fallback_msg="🍪 媒体直链重试使用内置浏览器Cookie: ",
                )
            except Exception:
                pass

            if referer_url:
                cmd.extend(["--referer", referer_url])
                parsed_referer = QUrl(referer_url)
                if parsed_referer.scheme() and parsed_referer.host():
                    cmd.extend(["--add-header", f"Origin:{parsed_referer.scheme()}://{parsed_referer.host()}"])

            if self.output_path:
                output_base_dir = self.output_path
                staged_dir = self._prepare_staging_output_dir()
                if staged_dir:
                    output_base_dir = staged_dir
                    self.progress_signal.emit(t("✓ 媒体直链重试启用暂存目录"))
                output_template = self._build_output_template(output_base_dir)
                cmd.extend(["-o", output_template])
                self.progress_signal.emit(f"媒体直链输出模板: {output_template}")

            cmd.extend([
                "--prefer-free-formats",
                "--no-warnings",
                "--fixup", "detect_or_warn",
                "--postprocessor-args", "ffmpeg:-avoid_negative_ts make_zero",
                "--windows-filenames",
                "--trim-filenames", "80",
                "--no-overwrites",
                "--retries", "5",
                "--fragment-retries", "3",
                "--file-access-retries", "2",
                "--retry-sleep", "exp=0.5:3:1.5",
                "--buffer-size", "1024K",
                "--http-chunk-size", "10M",
                "--socket-timeout", "30",
            ])

            if media_type in ("HLS", "DASH", "Smooth"):
                cmd.extend(["--hls-use-mpegts"])

            self._ensure_yt_dlp_temp_path(cmd)
            cmd.extend(["--progress-template", "download:[%(progress.downloaded_bytes)s/%(progress.total_bytes)s] %(progress.percent)s 速度:%(progress.speed)s ETA:%(progress.eta)s"])
            cmd.append(direct_url)

            self.progress_signal.emit(f"执行媒体直链重试命令: {' '.join(cmd)}")

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )

            error_output = ""
            progress_pattern = re.compile(r'(\d+(?:\.\d+)?)%')
            last_progress_update = time.time()
            last_percent = 0.0

            for line in self.process.stdout:
                if hasattr(self, "_cancelled") and self._cancelled:
                    self.progress_signal.emit(t("🔴 检测到下载已被取消，正在终止媒体直链重试进程..."))
                    try:
                        self.process.terminate()
                    except Exception:
                        pass
                    self.cancel_signal.emit()
                    return

                stripped_line = line.strip()
                if not stripped_line:
                    continue
                self.progress_signal.emit(stripped_line)
                error_output += stripped_line + "\n"

                progress_match = progress_pattern.search(stripped_line)
                if progress_match:
                    try:
                        percent = max(0.0, min(100.0, float(progress_match.group(1))))
                        now_ts = time.time()
                        if abs(percent - last_percent) >= 1 or (now_ts - last_progress_update) >= 0.5:
                            self.progress_percent_signal.emit(percent)
                            last_progress_update = now_ts
                            last_percent = percent
                    except Exception:
                        pass

            return_code = self.process.wait()
            self.progress_signal.emit(f"媒体直链重试进程结束，返回代码: {return_code}")

            if return_code == 0:
                if self._staging_output_dir:
                    moved = self._move_staging_outputs_to_target()
                    if moved:
                        self.progress_signal.emit(f"✓ 媒体直链重试已搬运 {len(moved)} 个结果文件")
                success_title = choose_better_download_title(self.current_video_title, media_title) or "媒体直链"
                self.finished_signal.emit(True, f"下载完成（已自动从网页中嗅探真实媒体地址）\n\n来源: {success_title}")
                return

            error_message = self._generate_simple_error_message(error_output)
            self.finished_signal.emit(False, f"网页URL不受支持，且媒体直链重试失败：{error_message}")
        except Exception as e:
            self.finished_signal.emit(False, f"媒体直链重试失败: {e}")

    def _retry_with_browser_media_fallback(self):
        if self.browser_media_fallback_attempted:
            return False
        if self.playlist_enabled or self.extract_info or self.list_formats or self.extract_playlist:
            return False

        self.browser_media_fallback_attempted = True
        existing_media = self._get_existing_browser_captured_media()
        reusable_media = self._pick_best_captured_media(
            [item for item in existing_media if self._is_stable_captured_media(item)]
        )
        if reusable_media:
            self.progress_signal.emit(t("✓ 复用当前浏览器已捕获的稳定媒体地址，跳过重复打开浏览器"))
            self._retry_with_captured_media(reusable_media)
            return True

        self.progress_signal.emit(t("检测到网页 URL 未被 yt-dlp 直接支持，正在尝试浏览器媒体嗅探回退..."))
        media_info = self._capture_media_with_playwright(self.url, timeout_seconds=25.0)
        if not media_info:
            self.progress_signal.emit(t("⚠️ 浏览器媒体嗅探未找到可下载的真实媒体地址"))
            return False

        self._retry_with_captured_media(media_info)
        return True

    def _verify_download_success(self):
        """验证下载是否真的成功"""
        try:
            # 检查输出目录是否存在
            if not os.path.exists(self.output_path):
                self.progress_signal.emit(f"输出目录不存在: {self.output_path}")
                return False
            
            # 查找最近创建的文件（5分钟内）
            current_time = time.time()
            recent_files = []
            
            for root, dirs, files in os.walk(self.output_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        # 检查文件是否是最近5分钟内创建或修改的
                        ctime = os.path.getctime(file_path)
                        mtime = os.path.getmtime(file_path)
                        recent_time = max(ctime, mtime)
                        
                        if recent_time > current_time - 600:  # 10分钟
                            file_size = os.path.getsize(file_path)
                            # 忽略太小的文件（可能是临时文件或元数据文件）
                            if file_size > 1024:  # 大于1KB
                                recent_files.append((file_path, file_size, recent_time))
                                self.progress_signal.emit(f"找到下载文件: {file_path} ({file_size} 字节)")
                    except (OSError, IOError):
                        continue
            
            if recent_files:
                # 按时间排序，选择最新的文件
                recent_files.sort(key=lambda x: x[2], reverse=True)
                latest_file = recent_files[0]
                self.progress_signal.emit(f"验证成功：找到下载文件 {latest_file[0]}")
                return True
            else:
                self.progress_signal.emit(t("验证失败：未找到最近下载的文件"))
                return False
                
        except Exception as e:
            self.progress_signal.emit(f"验证下载时出错: {str(e)}")
            return False
    
    def _extract_video_info(self):
        """提取视频信息而不下载"""
        try:
            # 使用更详细的日志输出
            self.progress_signal.emit(f"正在获取视频信息: {self.url}")
            
            # 首先尝试使用标准JSON方法获取信息，同时获取字幕信息
            cmd = ["yt-dlp.exe", "--dump-json", "--list-subs", "--no-playlist", self.url]
            try:
                self._attach_internal_cookie(
                    cmd,
                    url=self.url,
                    direct_msg="🍪 使用内置浏览器Cookie: ",
                    fallback_msg="🍪 使用内置浏览器Cookie文件: ",
                )
            except Exception:
                pass
            try:
                self._apply_base_yt_dlp_safety(cmd)
            except Exception:
                pass
            self.progress_signal.emit(f"执行命令: {' '.join(cmd)}")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # 分开捕获标准错误输出
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            stdout, stderr = process.communicate()
            
            # 记录完整的输出信息以便调试
            if stdout:
                self.progress_signal.emit(t("获取到输出数据"))
            if stderr:
                self.progress_signal.emit(f"错误输出: {stderr}")
            
            if process.returncode == 0:
                try:
                    # 尝试解析JSON输出
                    video_info = json.loads(stdout)
                    
                    # 检查是否为播放列表类型，只有在用户启用播放列表功能时才处理
                    if (video_info.get('_type') == 'playlist' or 'entries' in video_info) and self.playlist_enabled:
                        self.progress_signal.emit(t("检测到播放列表，提取播放列表信息..."))
                        self.info_signal.emit(video_info)
                        self.finished_signal.emit(True, "播放列表信息获取成功")
                        return
                    elif video_info.get('_type') == 'playlist' or 'entries' in video_info:
                        # 如果检测到播放列表但用户未启用播放列表功能，只提取第一个视频信息
                        if 'entries' in video_info and video_info['entries']:
                            first_video = video_info['entries'][0]
                            self.progress_signal.emit(t("检测到播放列表，但未启用播放列表功能，仅获取第一个视频信息"))
                            self.info_signal.emit(first_video)
                            self.finished_signal.emit(True, "视频信息获取成功")
                            return
                    
                    # 单个视频信息
                    self.info_signal.emit(video_info)
                    self.finished_signal.emit(True, "视频信息获取成功")
                    return
                except json.JSONDecodeError as json_err:
                    # JSON解析失败，记录详细错误
                    self.progress_signal.emit(f"JSON解析错误: {str(json_err)}")
                    self.progress_signal.emit(t("尝试使用备用方法获取信息..."))
                    
                    # 检查是否是播放列表，只有在用户启用播放列表功能时才处理
                    if ("playlist" in stdout.lower() or "entries" in stdout) and self.playlist_enabled:
                        self.progress_signal.emit(t("检测到可能是播放列表，尝试提取播放列表信息..."))
                        self._extract_playlist_info()
                        return
                    
                    # 尝试备用方法
                    self._extract_info_fallback()
                    return
            else:
                # 命令执行失败，记录错误代码和输出
                self.progress_signal.emit(f"命令执行失败，返回码: {process.returncode}")
                if stderr:
                    self.progress_signal.emit(f"错误详情: {stderr}")
                
                # 尝试备用方法
                self.progress_signal.emit(t("尝试使用备用方法获取信息..."))
                self._extract_info_fallback()
        except Exception as e:
            self.progress_signal.emit(f"获取视频信息时出错: {str(e)}")
            self.progress_signal.emit(t("尝试使用备用方法..."))
            self._extract_info_fallback()
    
    def _extract_info_fallback(self):
        """当标准JSON方法失败时的备用信息获取方法"""
        try:
            # 使用-j选项代替--dump-json，有时可能更稳定
            cmd = ["yt-dlp.exe", "-j", self.url]
            try:
                self._attach_internal_cookie(
                    cmd,
                    url=self.url,
                    direct_msg="🍪 使用内置浏览器Cookie: ",
                    fallback_msg="🍪 使用内置浏览器Cookie文件: ",
                )
            except Exception:
                pass
            try:
                self._apply_base_yt_dlp_safety(cmd)
            except Exception:
                pass
            self.progress_signal.emit(f"尝试备用命令: {' '.join(cmd)}")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            stdout, stderr = process.communicate()
            
            if process.returncode == 0 and stdout.strip():
                try:
                    # 尝试解析JSON输出
                    video_info = json.loads(stdout)
                    self.info_signal.emit(video_info)
                    self.finished_signal.emit(True, "使用备用方法获取视频信息成功")
                    return
                except json.JSONDecodeError:
                    self.progress_signal.emit(t("备用JSON解析失败，尝试使用简单信息提取..."))
            
            # 如果备用JSON方法也失败，尝试使用-F选项获取基本信息
            self._extract_basic_info()
        except Exception as e:
            self.progress_signal.emit(f"备用方法出错: {str(e)}")
            self._extract_basic_info()
    
    def _extract_basic_info(self):
        """使用-F选项获取基本视频信息"""
        try:
            # 使用-F选项获取格式信息，同时可以提取一些基本视频信息
            cmd = ["yt-dlp.exe", "-F", "--no-playlist", self.url]
            try:
                self._attach_internal_cookie(
                    cmd,
                    url=self.url,
                    direct_msg="🍪 已自动加载Cookie: ",
                    fallback_msg="🍪 使用内置浏览器Cookie文件: ",
                )
            except Exception:
                pass
            try:
                self._apply_base_yt_dlp_safety(cmd)
            except Exception:
                pass
            self.progress_signal.emit(f"尝试获取基本信息: {' '.join(cmd)}")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                # 从输出中提取基本信息
                lines = stdout.strip().split('\n')
                
                # 构建基本信息字典
                basic_info = {}
                
                # 尝试从输出中提取标题和上传者信息
                for line in lines:
                    if ":" in line and not line.startswith("["): 
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            key = parts[0].strip().lower()
                            value = parts[1].strip()
                            
                            if "title" in key:
                                basic_info["title"] = value
                            elif "uploader" in key or "channel" in key:
                                basic_info["uploader"] = value
                
                # 提取可用格式列表
                formats = []
                format_section_started = False
                
                for line in lines:
                    # 跳过空行和信息行
                    if not line.strip() or line.startswith('['): 
                        continue
                    
                    # 检测格式表头部
                    if ('ID' in line or 'id' in line.lower()) and any(keyword in line.lower() for keyword in ['resolution', 'format', 'ext']):
                        format_section_started = True
                        continue
                    
                    # 如果已经开始解析格式部分
                    if format_section_started:
                        # 跳过可能的分隔线或注释
                        if line.startswith('-') or line.startswith('='): 
                            continue
                        
                        # 尝试提取格式ID
                        format_id_match = re.search(r'^([\w\d\+\-\.]+)', line.strip())
                        if format_id_match:
                            format_id = format_id_match.group(1)
                            formats.append({
                                'format_id': format_id,
                                'description': line.strip()
                            })
                
                # 添加格式信息
                if formats:
                    basic_info["formats"] = formats
                
                # 如果提取到了一些基本信息
                if basic_info:
                    self.progress_signal.emit(t("成功提取基本视频信息"))
                    self.info_signal.emit(basic_info)
                    self.finished_signal.emit(True, "获取基本视频信息成功")
                    return
            
            # 如果所有方法都失败
            self.progress_signal.emit(t("所有信息获取方法均失败"))
            if stderr:
                self.progress_signal.emit(f"最终错误: {stderr}")
            
            # 创建一个最小的信息对象，至少包含URL
            minimal_info = {"url": self.url, "title": "未知标题", "_type": "video"}
            self.info_signal.emit(minimal_info)
            self.finished_signal.emit(False, "无法获取完整视频信息，仅返回基本URL信息")
        except Exception as e:
            self.progress_signal.emit(f"获取基本信息时出错: {str(e)}")
            # 创建一个最小的信息对象，至少包含URL
            minimal_info = {"url": self.url, "title": "未知标题", "_type": "video"}
            self.info_signal.emit(minimal_info)
            self.finished_signal.emit(False, f"所有获取方法均失败: {str(e)}")
    
    def _extract_playlist_info(self):
        """提取播放列表信息"""
        try:
            # 检查URL类型，对于频道/用户主页使用特殊处理
            url_lower = self.url.lower()
            is_channel_url = any(pattern in url_lower for pattern in [
                '/channel/', '/user/', '/c/', '/@', '/playlist?list=', 
                'youtube.com/watch?list=', 'bilibili.com/space/',
                'space.bilibili.com', '/video/', '/medialist'
            ])
            
            if is_channel_url:
                self.progress_signal.emit(t("检测到频道/用户主页URL，使用专门的提取方法..."))
                # 对于频道URL，使用flat-playlist并输出单个JSON
                cmd = ["yt-dlp.exe", "--flat-playlist", "--dump-single-json", "--yes-playlist", self.url]
            else:
                # 首先尝试获取详细的播放列表信息（包含标题等），输出单个JSON
                cmd = ["yt-dlp.exe", "--dump-single-json", "--no-download", "--yes-playlist", self.url]
            
            try:
                self._attach_internal_cookie(
                    cmd,
                    url=self.url,
                    direct_msg="🍪 播放列表使用Cookie: ",
                    fallback_msg="🍪 播放列表回退Cookie: ",
                )
            except Exception:
                pass
            try:
                self._apply_base_yt_dlp_safety(cmd)
            except Exception:
                pass
            self.progress_signal.emit(f"尝试获取播放列表信息: {' '.join(cmd)}")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            stdout, stderr = process.communicate()
            
            if process.returncode == 0 and stdout.strip():
                try:
                    # 尝试解析JSON输出
                    raw_data = json.loads(stdout.strip())
                    
                    # 如果返回的是 playlist 类型，直接提取 entries
                    if isinstance(raw_data, dict) and raw_data.get("_type") == "playlist":
                        playlist_info = raw_data
                        if "entries" not in playlist_info:
                            playlist_info["entries"] = []
                    else:
                        # 否则，可能是多行JSON或单个视频，按原逻辑处理
                        playlist_info = {"_type": "playlist", "entries": []}
                        for line in stdout.strip().split('\n'):
                            if line.strip():
                                try:
                                    entry = json.loads(line)
                                    if 'title' not in entry and 'id' in entry:
                                        entry['title'] = f"视频 {entry['id']}"
                                    playlist_info["entries"].append(entry)
                                except json.JSONDecodeError:
                                    continue
                    
                    # 如果找到了条目
                    if playlist_info.get("entries"):
                        # 提取播放列表标题
                        if "title" not in playlist_info or not playlist_info["title"]:
                            first_entry = playlist_info["entries"][0]
                            if "playlist" in first_entry:
                                playlist_info["title"] = first_entry["playlist"]
                            elif "playlist_title" in first_entry:
                                playlist_info["title"] = first_entry["playlist_title"]
                            else:
                                playlist_info["title"] = "未知播放列表"
                        
                        self.info_signal.emit(playlist_info)
                        self.finished_signal.emit(True, f"成功获取播放列表信息，包含 {len(playlist_info['entries'])} 个视频")
                        return
                except Exception as parse_err:
                    self.progress_signal.emit(f"解析播放列表详细信息出错: {str(parse_err)}")
            
            # 如果详细信息提取失败，尝试使用flat-playlist模式并输出单个JSON
            self.progress_signal.emit(t("尝试使用简化模式获取播放列表信息..."))
            cmd = ["yt-dlp.exe", "--flat-playlist", "--dump-single-json", "--yes-playlist", self.url]
            try:
                self._attach_internal_cookie(
                    cmd,
                    url=self.url,
                    direct_msg="🍪 简化播放列表使用Cookie: ",
                    fallback_msg="🍪 简化播放列表回退Cookie: ",
                )
            except Exception:
                pass
            try:
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
            
            if process.returncode == 0 and stdout.strip():
                try:
                    raw_data = json.loads(stdout.strip())
                    
                    if isinstance(raw_data, dict) and raw_data.get("_type") == "playlist":
                        playlist_info = raw_data
                        if "entries" not in playlist_info:
                            playlist_info["entries"] = []
                    else:
                        playlist_info = {"_type": "playlist", "entries": []}
                        for line in stdout.strip().split('\n'):
                            if line.strip():
                                try:
                                    entry = json.loads(line)
                                    if 'title' not in entry and 'id' in entry:
                                        entry['title'] = f"视频 {entry['id']}"
                                    elif 'title' not in entry:
                                        entry['title'] = f"视频 #{len(playlist_info['entries'])+1}"
                                    playlist_info["entries"].append(entry)
                                except json.JSONDecodeError:
                                    continue
                    
                    if playlist_info.get("entries"):
                        if "title" not in playlist_info or not playlist_info["title"]:
                            first_entry = playlist_info["entries"][0]
                            if "playlist" in first_entry:
                                playlist_info["title"] = first_entry["playlist"]
                            elif "playlist_title" in first_entry:
                                playlist_info["title"] = first_entry["playlist_title"]
                            else:
                                playlist_info["title"] = "未知播放列表"
                        
                        self.info_signal.emit(playlist_info)
                        self.finished_signal.emit(True, f"成功获取播放列表信息，包含 {len(playlist_info['entries'])} 个视频")
                        return
                except Exception as parse_err:
                    self.progress_signal.emit(f"解析播放列表信息出错: {str(parse_err)}")
            
            # 如果播放列表提取失败，回退到基本信息提取
            self.progress_signal.emit(t("播放列表信息提取失败，尝试获取基本信息..."))
            self._extract_basic_info()
        except Exception as e:
            self.progress_signal.emit(f"提取播放列表信息时出错: {str(e)}")
            self._extract_basic_info()
            
    def _list_formats(self):
        """列出视频可用的格式"""
        try:
            if self._is_x_platform_url(self.url):
                self.progress_signal.emit(t("🔄 检测到X平台视频，启用自动格式选择..."))
                auto_format = self._get_x_platform_best_format()
                x_formats = [{
                    'format_id': 'best',
                    'ext': 'mp4',
                    'resolution': '自动选择',
                    'format': 'X平台自动选择最佳格式'
                }]
                self.progress_signal.emit(f"✅ X平台自动选择格式: {auto_format}")
                self.progress_signal.emit(t("ℹ️ 已自动选择最佳格式，无需手动选择"))
                self.formats_signal.emit(x_formats)
                self.finished_signal.emit(True, "X平台自动格式选择完成")
                return

            cmd = ["yt-dlp.exe", "-j", "--no-playlist", self.url]
            
            # 统一 Cookie 逻辑：候选域名匹配 + 最近内置 Cookie 回退
            try:
                self._attach_internal_cookie(
                    cmd,
                    url=self.url,
                    direct_msg="🍪 格式列表使用Cookie: ",
                    fallback_msg="🍪 格式列表回退Cookie: ",
                )
            except Exception:
                pass
            
            self.progress_signal.emit(t("正在获取可用格式列表..."))
            self.progress_signal.emit(f"[日志] 格式列表请求使用URL: {self.url}")
            try:
                vp = subprocess.Popen(["yt-dlp.exe", "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, creationflags=subprocess.CREATE_NO_WINDOW)
                v = vp.communicate()[0].strip()
                self.progress_signal.emit(f"[日志] yt-dlp版本: {v}")
            except Exception:
                pass
            try:
                ts = int(time.time())
                self.progress_signal.emit(f"[日志] 请求时间戳: {ts}")
            except Exception:
                pass

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            stdout = process.communicate()[0]
            if process.returncode == 0:
                try:
                    video_info = json.loads(stdout)
                    json_formats = video_info.get('formats') or []
                    parsed = []
                    for f in json_formats:
                        width = f.get('width')
                        height = f.get('height')
                        resolution = f.get('resolution')
                        if not resolution:
                            if width and height:
                                resolution = f"{width}x{height}"
                            elif height:
                                resolution = f"{height}p"
                            else:
                                resolution = ''
                        filesize = f.get('filesize')
                        fs_approx = f.get('filesize_approx')
                        bd = None
                        try:
                            if f.get('tbr'):
                                bd = f"{float(f.get('tbr')):.0f}k"
                            else:
                                vb = f.get('vbr') or 0
                                ab = f.get('abr') or 0
                                if (vb or ab):
                                    bd = f"{(vb or 0) + (ab or 0):.0f}k"
                        except Exception:
                            bd = None
                        sd = None
                        try:
                            if filesize and isinstance(filesize, (int, float)) and filesize > 0:
                                if filesize > 1024*1024*1024:
                                    sd = f"{filesize/(1024*1024*1024):.1f}GB"
                                elif filesize > 1024*1024:
                                    sd = f"{filesize/(1024*1024):.1f}MB"
                                else:
                                    sd = f"{filesize/1024:.1f}KB"
                            elif fs_approx and isinstance(fs_approx, (int, float)) and fs_approx > 0:
                                if fs_approx > 1024*1024*1024:
                                    sd = f"~{fs_approx/(1024*1024*1024):.1f}GB"
                                elif fs_approx > 1024*1024:
                                    sd = f"~{fs_approx/(1024*1024):.1f}MB"
                                else:
                                    sd = f"~{fs_approx/1024:.1f}KB"
                        except Exception:
                            sd = None
                        parsed.append({
                            'format_id': f.get('format_id'),
                            'ext': f.get('ext') or '',
                            'width': width,
                            'height': height,
                            'resolution': resolution,
                            'language': f.get('language') or '',
                            'vcodec': f.get('vcodec') or 'none',
                            'acodec': f.get('acodec') or 'none',
                            'tbr': f.get('tbr'),
                            'vbr': f.get('vbr'),
                            'abr': f.get('abr'),
                            'fps': f.get('fps'),
                            'filesize': filesize,
                            'fs_approx': fs_approx,
                            'protocol': f.get('protocol') or 'N/A',
                            'format_note': f.get('format_note') or '',
                            'format': f.get('format') or '',
                            'dynamic_range': f.get('dynamic_range') or '',
                            'channels': f.get('audio_channels') or f.get('channels') or 0,
                            'bitrate_display': bd or '/',
                            'size_display': sd or '/',
                        })
                    self.formats_signal.emit(parsed)
                    self.finished_signal.emit(True, f"格式列表获取成功，找到 {len(parsed)} 个可用格式")
                    return
                except json.JSONDecodeError:
                    pass

            # Fallback 到文本解析
            cmd = ["yt-dlp.exe", "-F", self.url]
            
            # Fallback 格式列表同样使用统一 Cookie 逻辑
            try:
                self._attach_internal_cookie(
                    cmd,
                    url=self.url,
                    direct_msg="🍪 Fallback格式列表使用Cookie: ",
                    fallback_msg="🍪 Fallback格式列表回退Cookie: ",
                )
            except Exception:
                pass
            
            self.progress_signal.emit(f"[日志] Fallback格式列表请求使用URL: {self.url}")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            output = process.communicate()[0]
            if process.returncode == 0:
                formats = []
                lines = output.strip().split('\n')
                for line in lines:
                    self.progress_signal.emit(line)
                format_section_started = False
                for line in lines:
                    if not line.strip() or line.startswith('['):
                        continue
                    if ('ID' in line or 'id' in line.lower()) and any(keyword in line.lower() for keyword in ['resolution', 'format', 'ext', 'note', 'codec', 'size']):
                        format_section_started = True
                        continue
                    if format_section_started:
                        if line.startswith('-') or line.startswith('=') or line.startswith('['):
                            continue
                        try:
                            format_id_match = re.search(r'^([\w\d\+\-\.]+)', line.strip())
                            if format_id_match:
                                format_id = format_id_match.group(1)
                                ext_match = re.search(r'\b(mp4|webm|m4a|mp3|opus|ogg|wav|mkv|flv|avi)\b', line.lower())
                                ext = ext_match.group(1) if ext_match else ''
                                resolution_match = re.search(r'\b(\d+x\d+|\d+p|\d+P)\b', line)
                                resolution = resolution_match.group(1) if resolution_match else ''
                                description = line.strip()[len(format_id):].strip()
                                lang_match = re.search(r'\[([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)?)\]', line)
                                language = lang_match.group(1).lower() if lang_match else ''
                                proto = ''
                                low = line.lower()
                                if 'm3u8_native' in low or 'm3u8' in low:
                                    proto = 'm3u8_native' if 'm3u8_native' in low else 'm3u8'
                                elif 'http_dash_segments' in low or 'dash' in low:
                                    proto = 'http_dash_segments' if 'http_dash_segments' in low else 'dash'
                                elif 'websocket_frag' in low:
                                    proto = 'websocket_frag'
                                elif 'rtmp' in low:
                                    proto = 'rtmp'
                                elif 'ftps' in low:
                                    proto = 'ftps'
                                elif 'ftp' in low:
                                    proto = 'ftp'
                                elif 'mms' in low:
                                    proto = 'mms'
                                elif 'rtsp' in low:
                                    proto = 'rtsp'
                                elif 'f4m' in low:
                                    proto = 'f4m'
                                elif 'f4f' in low:
                                    proto = 'f4f'
                                elif 'https' in low:
                                    proto = 'https'
                                elif 'http' in low:
                                    proto = 'http'
                                audio_exts = {'m4a','mp3','opus','ogg','wav'}
                                is_audio_only = ('audio only' in low) or (ext in audio_exts and not resolution)
                                is_video_only = ('video only' in low) or (resolution and ('video only' in low))
                                vcodec_field = 'none' if is_audio_only else ''
                                acodec_field = 'none' if is_video_only else ''
                                formats.append({
                                    'format_id': format_id,
                                    'ext': ext,
                                    'resolution': resolution,
                                    'language': language,
                                    'format': description,
                                    'protocol': proto or 'N/A',
                                    'vcodec': vcodec_field,
                                    'acodec': acodec_field,
                                    'tbr': None,
                                    'abr': None,
                                    'vbr': None,
                                    'fps': None,
                                    'filesize': None,
                                    'fs_approx': None,
                                    'format_note': '',
                                    'dynamic_range': '',
                                    'channels': 0,
                                    'bitrate_display': '/',
                                    'size_display': '/',
                                    'raw_line': line,
                                })
                        except Exception:
                            continue
                unique = []
                seen = set()
                for f in formats:
                    if f['format_id'] not in seen:
                        seen.add(f['format_id'])
                        unique.append(f)
                self.formats_signal.emit(unique)
                self.finished_signal.emit(True, f"格式列表获取成功，找到 {len(unique)} 个可用格式")
            else:
                self.progress_signal.emit(output)
                self.finished_signal.emit(False, "获取格式列表失败")
        except Exception as e:
            self.finished_signal.emit(False, f"获取格式列表时出错: {str(e)}")
    
    def _parse_format_line_text(self, line):
        try:
            s = line.strip()
            m_id = re.search(r'^([\w\d\+\-\.]+)', s)
            if not m_id:
                return None
            format_id = m_id.group(1)
            low = s.lower()
            
            # 提取扩展名
            m_ext = re.search(r'\b(mp4|webm|m4a|mp3|opus|ogg|wav|mkv|flv|avi|mhtml)\b', low)
            ext = m_ext.group(1) if m_ext else ''
            
            # 提取分辨率
            m_res = re.search(r'\b(\d+x\d+|\d+p|\d+P)\b', s)
            resolution = m_res.group(1) if m_res else ''
            
            # 提取文件大小（支持 "5.14MiB", "~17.88MiB" 等格式）
            size_str = None
            filesize = None
            fs_approx = None
            # 匹配带波浪号的近似值
            m_size = re.search(r'(~\s*)(\d+(?:\.\d+)?)\s*(GiB|MiB|KiB|GB|MB|KB)', s, re.IGNORECASE)
            if m_size:
                size_str = f"~{m_size.group(2)}{m_size.group(3)}"
                fs_approx = self._parse_size(f"{m_size.group(2)}{m_size.group(3)}")
            else:
                # 匹配精确值
                m_size = re.search(r'(?<!~)\s+(\d+(?:\.\d+)?)\s*(GiB|MiB|KiB|GB|MB|KB)\b', s, re.IGNORECASE)
                if m_size:
                    size_str = f"{m_size.group(1)}{m_size.group(2)}"
                    filesize = self._parse_size(size_str)
            
            # 提取比特率（支持 "170k", "803k" 等格式）
            bitrate_str = None
            tbr = None
            # 匹配比特率（带k后缀的数字）
            m_br = re.search(r'\s+(\d+)k\b', low)
            if m_br:
                tbr = int(m_br.group(1))
                bitrate_str = f"{tbr}k"
            
            # 提取编码器（支持 "avc1.4D400C", "mp4a.40.5" 等）
            vcodec = ''
            acodec = ''
            # 视频编码器
            m_vcodec = re.search(r'\b(avc1[\w\.]*|vp9|av01[\w\.]*|h264|h265|hevc)\b', low)
            if m_vcodec:
                vcodec = m_vcodec.group(1)
            # 音频编码器
            m_acodec = re.search(r'\b(mp4a[\w\.]*|opus|aac|vorbis|mp3|flac)\b', low)
            if m_acodec:
                acodec = m_acodec.group(1)
            
            # 构建描述
            description = s[len(format_id):].strip()
            
            # 提取协议
            proto = None
            if 'm3u8_native' in low or 'm3u8' in low:
                proto = 'm3u8_native' if 'm3u8_native' in low else 'm3u8'
            elif 'http_dash_segments' in low or 'dash' in low:
                proto = 'http_dash_segments' if 'http_dash_segments' in low else 'dash'
            elif 'websocket_frag' in low:
                proto = 'websocket_frag'
            elif 'rtmp' in low:
                proto = 'rtmp'
            elif 'ftps' in low:
                proto = 'ftps'
            elif 'ftp' in low:
                proto = 'ftp'
            elif 'mms' in low:
                proto = 'mms'
            elif 'rtsp' in low:
                proto = 'rtsp'
            elif 'f4m' in low:
                proto = 'f4m'
            elif 'f4f' in low:
                proto = 'f4f'
            elif 'https' in low:
                proto = 'https'
            elif 'http' in low:
                proto = 'http'
            
            return {
                'format_id': format_id,
                'ext': ext,
                'resolution': resolution,
                'format': description,
                'protocol': proto or 'N/A',
                'vcodec': vcodec if vcodec else 'none',
                'acodec': acodec if acodec else 'none',
                'tbr': tbr,
                'abr': None,
                'vbr': None,
                'fps': None,
                'filesize': filesize,
                'filesize_approx': fs_approx,
                'format_note': '',
                'dynamic_range': '',
                'channels': 0,
                'raw_line': line,
            }
        except Exception:
            return None
    
    def _parse_size(self, size_str):
        """解析大小字符串，将KB、MB、GB等单位转换为数值"""
        try:
            # 移除可能的空格
            size_str = size_str.strip()
            
            # 如果是空字符串或None，返回0
            if not size_str:
                return 0
                
            # 使用正则表达式提取数值部分
            match = re.search(r'([\d\.]+)', size_str)
            if not match:
                return 0
                
            value = float(match.group(1))
            
            # 记录调试信息
            self.progress_signal.emit(f"解析大小: {size_str} -> 提取数值: {value}")
            
            # 提取数值和单位 - 改进单位检测逻辑
            size_str = size_str.upper()  # 转换为大写以简化匹配
            
            # 检查各种单位并进行相应转换
            if any(unit in size_str for unit in ['KIB', 'KB', 'K']):
                result = value * 1024
                self.progress_signal.emit(f"单位转换: KB -> {result:.2f} 字节")
                return result
                    
            elif any(unit in size_str for unit in ['MIB', 'MB', 'M']):
                result = value * 1024 * 1024
                self.progress_signal.emit(f"单位转换: MB -> {result:.2f} 字节")
                return result
                    
            elif any(unit in size_str for unit in ['GIB', 'GB', 'G']):
                result = value * 1024 * 1024 * 1024
                self.progress_signal.emit(f"单位转换: GB -> {result:.2f} 字节")
                return result
                
            elif any(unit in size_str for unit in ['TIB', 'TB', 'T']):
                result = value * 1024 * 1024 * 1024 * 1024
                self.progress_signal.emit(f"单位转换: TB -> {result:.2f} 字节")
                return result
                
            elif 'B' in size_str:
                # 如果包含B单位，假设是字节
                self.progress_signal.emit(f"单位: 字节 -> {value:.2f} 字节")
                return value
                
            # 如果没有单位，假设是字节
            self.progress_signal.emit(f"无单位: 假设为字节 -> {value:.2f} 字节")
            return value
        except Exception as e:
            self.progress_signal.emit(f"解析大小出错: {str(e)}")
            return 0
            return value
        except Exception as e:
            self.progress_signal.emit(f"解析大小出错: {str(e)}")
            return 0
            
        except Exception as e:
            # 出错时记录错误并返回0
            print(f"解析大小字符串出错: {str(e)}, 原始字符串: {size_str}")
            return 0
        except (ValueError, TypeError) as e:
            print(f"解析大小字符串出错: {size_str}, 错误: {str(e)}")
            return 0
    
    def _retry_with_best_format(self, postprocessing_error=False):
        """当指定格式不可用或后处理错误时，使用最佳兼容格式重试下载
        
        Args:
            postprocessing_error: 是否是由于后处理错误（视频和音频合并失败）导致的重试
        """
        try:
            # 构建新的yt-dlp命令
            cmd = ["yt-dlp.exe"]
            
            # 根据错误类型选择不同的格式策略
            if postprocessing_error:
                # 后处理错误（合并失败）情况下，使用更严格的格式限制
                # 1. 尝试下载已合并的单文件
                # 2. 如果需要合并，确保使用高兼容性的编解码器和容器格式
                self.progress_signal.emit(t("使用高兼容性格式重试下载，确保视频和音频能够成功合并..."))
                
                # 优先选择已合并的mp4文件，或确保视频和音频格式高度兼容
                cmd.extend(["-f", "best[ext=mp4]/best[ext=mkv]/best[ext=webm]/"
                           "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a][acodec!=opus]/"
                        "bestvideo[ext=webm][vcodec^=vp9]+bestaudio[ext=webm][acodec!=opus]/"
                        "bestvideo[ext=mp4]+bestaudio[ext=m4a][acodec!=opus]/"
                           "bestvideo+bestaudio[acodec!=opus]/best[acodec!=opus]"])
                
                # 添加排序参数，优先选择更好的编解码器和容器格式
                cmd.extend(["-S", "codec:h264,ext:mp4,res,fps"])
                
                # 添加额外的后处理参数，提高合并成功率
                cmd.extend(["--merge-output-format", "mp4"])
                cmd.extend(["--audio-quality", "0"])
                
                # 添加调试信息，帮助诊断问题
                cmd.extend(["--verbose"])
            else:
                # 格式不可用的情况，使用更通用的格式选项
                self.progress_signal.emit(t("使用通用格式重试下载..."))
                
                # 优先选择高质量的mp4格式和h264编码
                # 保留原始语言过滤器（如果用户指定了音频语言）
                audio_lang_filter = ""
                try:
                    if self.main_app and hasattr(self.main_app, '_get_audio_language_filter'):
                        audio_lang_filter = self.main_app._get_audio_language_filter()
                except Exception:
                    pass
                retry_fmt = (
                    f"bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]{audio_lang_filter}/"
                    f"bestvideo[ext=mp4]+bestaudio[ext=m4a]{audio_lang_filter}/"
                    f"bestvideo+bestaudio{audio_lang_filter}/best{audio_lang_filter}[ext=mp4]/best{audio_lang_filter}"
                )
                cmd.extend(["-f", retry_fmt])
                
                # 添加排序参数，优先选择更好的编解码器和mp4格式
                cmd.extend(["-S", "codec:h264,ext:mp4,res,fps"])
            
            # 添加输出路径和模板
            if self.output_path:
                # 确保路径使用正确的编码
                safe_output_path = os.path.abspath(self.output_path)
                is_playlist_job = self._is_effective_playlist_job()
                if not is_playlist_job:
                    staged_dir = self._prepare_staging_output_dir()
                    if staged_dir:
                        safe_output_path = staged_dir
                output_template = self._build_output_template(safe_output_path)
                cmd.extend(["-o", output_template])
                # 记录使用的输出模板
                self.progress_signal.emit(f"使用输出模板: {output_template}")
            
            # 添加其他选项，但排除可能导致格式问题的选项
            if self.additional_options:
                filtered_options = [opt for opt in self.additional_options 
                                   if not opt.startswith("-f") 
                                   and not opt.startswith("--format") 
                                   and not opt.startswith("-S") 
                                   and not opt.startswith("--format-sort")
                                   and not opt.startswith("--merge-output-format")]  # 排除可能冲突的合并格式选项
                cmd.extend(filtered_options)

            # 将分片/中间文件放到系统临时目录
            self._ensure_yt_dlp_temp_path(cmd)
            
            # 添加进度显示格式
            cmd.extend(["--progress-template", "下载中: %(progress.downloaded_bytes)s/%(progress.total_bytes)s (%(progress.eta)s) 速度: %(progress.speed)s"])
            
            # 添加URL
            cmd.append(self.url)
            
            # 记录完整命令用于调试
            self.progress_signal.emit(f"执行重试命令: {' '.join(cmd)}")
            
            # 执行命令
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            # 读取输出并检测错误
            error_output = ""
            # 用于解析下载进度的正则表达式
            progress_pattern = re.compile(r'(\d+(?:\.\d+)?)%')
            # 下载信息匹配模式
            downloaded_pattern = re.compile(r'下载中: ([\d.]+[KMGT]?i?B)/([\d.]+[KMGT]?i?B)')
            alt_downloaded_pattern = re.compile(r'([\d.]+[KMGT]?i?B)\s+of\s+([\d.]+[KMGT]?i?B)')
            
            # 初始化进度跟踪变量
            last_progress_update = time.time()
            last_percent = 0
            # 【修复】发送 -1 信号重置进度条，确保重试时进度从头开始显示
            self.progress_percent_signal.emit(-1)
            self.progress_sent = True
            last_estimated_progress = 0.1
            
            for line in self.process.stdout:
                # 检查进程是否被外部终止
                if self.process and self.process.poll() is not None:
                    self.progress_signal.emit(t("检测到进程已终止，退出下载循环"))
                    break
                elif not self.process:
                    self.progress_signal.emit(t("检测到进程已终止，退出下载循环"))
                    break
                    
                stripped_line = line.strip()
                self.progress_signal.emit(stripped_line)
                
                # 实时检测年龄限制错误
                if ("Sign in to confirm your age" in stripped_line or
                    "age-restricted" in stripped_line.lower() or
                    "content warning" in stripped_line.lower() or
                    "mature content" in stripped_line.lower() or
                    "inappropriate content" in stripped_line.lower() or
                    "restricted mode" in stripped_line.lower() or
                    "age verification" in stripped_line.lower() or
                    "confirm your age" in stripped_line.lower() or
                    "age gate" in stripped_line.lower() or
                    "This video is only available for registered users" in stripped_line or
                    "Video unavailable" in stripped_line or
                    "not available in your country" in stripped_line.lower() or
                    "geo-blocked" in stripped_line.lower()):
                    
                    # 如果是播放列表下载，不要中断进程，让yt-dlp的--ignore-errors自动跳过
                    if self.playlist_enabled:
                        self.progress_signal.emit(t("⚠️ 检测到年龄/地理限制，播放列表模式下将自动跳过该视频继续下载"))
                        continue  # 继续处理后续输出，不中断进程
                    
                    # 只在单个视频下载时才尝试使用增强认证参数重试
                    if not self.age_restriction_retry_attempted:
                        self.progress_signal.emit(t("检测到年龄限制或地理位置限制错误（单个视频），正在尝试使用增强认证参数重试..."))
                        self.process.terminate()
                        self._retry_with_enhanced_auth()
                        return
                    else:
                        self.progress_signal.emit(t("增强认证重试失败，停止下载"))
                        self.process.terminate()
                        # 提供更详细的错误信息和解决方案
                        error_message = self._generate_age_restriction_error_message(stripped_line)
                        self.finished_signal.emit(False, error_message)
                        return
                
                # 解析下载进度
                progress_match = progress_pattern.search(stripped_line)
                if progress_match:
                    try:
                        percent = float(progress_match.group(1))
                        percent = max(0, min(100, percent))
                        
                        # 只有当进度变化超过0.5%或距离上次更新超过0.5秒时才发送信号
                        current_time = time.time()
                        if abs(percent - last_percent) >= 0.5 or (current_time - last_progress_update) >= 0.5:
                            self.progress_percent_signal.emit(percent)
                            last_progress_update = current_time
                            last_percent = percent
                            self.progress_sent = True
                    except (ValueError, IndexError) as e:
                        self.progress_signal.emit(f"解析进度出错: {str(e)}")
                
                # 收集错误信息
                if "ERROR" in stripped_line or "Error" in stripped_line or "error" in stripped_line:
                    error_output += stripped_line + "\n"
            
            # 等待进程完成
            self.process.wait()
            
            # 在转码完成时发送平滑的进度信号，确保从当前进度平滑过渡到100%
            self.progress_signal.emit("转码完成，开始发送平滑过渡到100%的进度信号")
            # 获取当前进度值
            current_progress = last_percent
            # 如果当前进度小于100%，则平滑过渡到100%
            if current_progress < 100:
                # 计算需要增加的进度步数（每次增加1%）
                steps = int((100 - current_progress) / 1) + 1
                # 计算每步之间的延迟时间（总共不超过1秒）
                delay = min(1.0 / steps, 0.1)  # 最大延迟100毫秒
                # 逐步增加进度
                for i in range(steps):
                    next_progress = min(100, current_progress + 1 + i)
                    self.progress_percent_signal.emit(next_progress)
                    # 短暂延迟，创造平滑效果
                    time.sleep(delay)
            # 最后确保发送100%的进度信号
            self.progress_percent_signal.emit(100.0)
            # 强制更新UI，确保进度条显示完成状态
            QApplication.processEvents()
            
            if self.process.returncode == 0:
                if self._staging_output_dir:
                    moved = self._move_staging_outputs_to_target()
                    if moved:
                        self.progress_signal.emit(f"✓ 重试流程已搬运 {len(moved)} 个结果文件")
                # 检查是否真的有文件被下载
                download_success = self._verify_download_success()
                if download_success:
                    # 确保在下载完成时发送100%的进度信号
                    self.progress_percent_signal.emit(100.0)
                    self.progress_signal.emit("重试下载成功，进度100%")
                    self.finished_signal.emit(True, "下载完成！")
                else:
                    self.progress_signal.emit(t("警告：重试下载过程完成但未找到输出文件"))
                    self.finished_signal.emit(False, "重试下载失败：未找到输出文件")
            else:
                # 如果重试也失败了
                if postprocessing_error:
                    # 如果是后处理错误的重试失败，尝试使用单一格式下载
                    self.progress_signal.emit(t("合并格式重试失败，尝试使用单一格式下载..."))
                    self._retry_with_single_format()
                else:
                    # 其他错误，直接报告失败
                    self.finished_signal.emit(False, f"重试下载失败，返回码：{self.process.returncode}\n{error_output}")
        
        except Exception as e:
            self.progress_signal.emit(f"重试下载时发生错误: {str(e)}")
            self.finished_signal.emit(False, f"重试下载时发生错误: {str(e)}")
    
    def _retry_with_single_format(self):
        """当合并格式也失败时，尝试使用单一格式下载（不需要合并的格式）"""
        try:
            # 构建使用单一格式的命令
            cmd = ["yt-dlp.exe"]
            
            # 使用单一格式，避免需要合并
            self.progress_signal.emit(t("使用单一格式下载，避免后处理合并步骤..."))
            audio_lang_filter = ""
            try:
                if self.main_app and hasattr(self.main_app, '_get_audio_language_filter'):
                    audio_lang_filter = self.main_app._get_audio_language_filter()
            except Exception:
                pass
            cmd.extend(["-f", f"best[ext=mp4]{audio_lang_filter}/best[ext=mkv]{audio_lang_filter}/best[ext=webm]{audio_lang_filter}/best{audio_lang_filter}"])
            
            # 添加排序参数
            cmd.extend(["-S", "ext:mp4,codec:h264"])
            
            # 添加输出路径和模板
            if self.output_path:
                safe_output_path = os.path.abspath(self.output_path)
                is_playlist_job = self._is_effective_playlist_job()
                if not is_playlist_job:
                    staged_dir = self._prepare_staging_output_dir()
                    if staged_dir:
                        safe_output_path = staged_dir
                output_template = self._build_output_template(safe_output_path, "_单文件")
                cmd.extend(["-o", output_template])
                self.progress_signal.emit(f"使用输出模板: {output_template}")
            
            # 添加其他选项，但排除可能导致格式问题的选项
            if self.additional_options:
                filtered_options = [opt for opt in self.additional_options 
                                   if not opt.startswith("-f") 
                                   and not opt.startswith("--format") 
                                   and not opt.startswith("-S") 
                                   and not opt.startswith("--format-sort")
                                   and not opt.startswith("--merge-output-format")]
                cmd.extend(filtered_options)

            # 将分片/中间文件放到系统临时目录
            self._ensure_yt_dlp_temp_path(cmd)
            
            # 添加进度显示格式
            cmd.extend(["--progress-template", "下载中: %(progress.downloaded_bytes)s/%(progress.total_bytes)s (%(progress.eta)s) 速度: %(progress.speed)s"])
            
            # 添加URL
            cmd.append(self.url)
            
            self.progress_signal.emit(f"执行单一格式命令: {' '.join(cmd)}")
            
            # 执行命令
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            # 读取输出
            error_output = ""
            for line in self.process.stdout:
                # 检查进程是否被外部终止
                if self.process and self.process.poll() is not None:
                    self.progress_signal.emit(t("检测到进程已终止，退出下载循环"))
                    break
                elif not self.process:
                    self.progress_signal.emit(t("检测到进程已终止，退出下载循环"))
                    break
                    
                stripped_line = line.strip()
                self.progress_signal.emit(stripped_line)
                
                # 实时检测年龄限制错误
                if ("Sign in to confirm your age" in stripped_line or
                    "age-restricted" in stripped_line.lower() or
                    "content warning" in stripped_line.lower() or
                    "mature content" in stripped_line.lower() or
                    "inappropriate content" in stripped_line.lower() or
                    "restricted mode" in stripped_line.lower() or
                    "age verification" in stripped_line.lower() or
                    "confirm your age" in stripped_line.lower() or
                    "age gate" in stripped_line.lower() or
                    "This video is only available for registered users" in stripped_line or
                    "Video unavailable" in stripped_line or
                    "not available in your country" in stripped_line.lower() or
                    "geo-blocked" in stripped_line.lower()):
                    
                    # 如果是播放列表下载，不要中断进程，让yt-dlp的--ignore-errors自动跳过
                    if self.playlist_enabled:
                        self.progress_signal.emit(t("⚠️ 检测到年龄/地理限制，播放列表模式下将自动跳过该视频继续下载"))
                        continue  # 继续处理后续输出，不中断进程
                    
                    # 只在单个视频下载时才尝试使用增强认证参数重试
                    if not self.age_restriction_retry_attempted:
                        self.progress_signal.emit(t("检测到年龄限制或地理位置限制错误（单个视频），正在尝试使用增强认证参数重试..."))
                        self.process.terminate()
                        self._retry_with_enhanced_auth()
                        return
                    else:
                        self.progress_signal.emit(t("增强认证重试失败，停止下载"))
                        self.process.terminate()
                        # 提供更详细的错误信息和解决方案
                        error_message = self._generate_age_restriction_error_message(stripped_line)
                        self.finished_signal.emit(False, error_message)
                        return
                
                # 收集错误信息
                if "ERROR" in stripped_line or "Error" in stripped_line or "error" in stripped_line:
                    error_output += stripped_line + "\n"
            
            # 等待进程完成
            self.process.wait()
            
            # 不要无条件显示100%，先检查下载是否成功
            self.progress_signal.emit(t("重试进程完成，检查下载结果..."))
            
            if self.process.returncode == 0:
                if self._staging_output_dir:
                    moved = self._move_staging_outputs_to_target()
                    if moved:
                        self.progress_signal.emit(f"✓ 单一格式重试已搬运 {len(moved)} 个结果文件")
                # 确保在下载完成时发送100%的进度信号
                self.progress_percent_signal.emit(100.0)
                self.progress_signal.emit("单一格式下载成功，进度100%")
                self.finished_signal.emit(True, "下载完成！")
            else:
                # 如果所有尝试都失败了
                self.finished_signal.emit(False, f"所有下载尝试均失败，返回码：{self.process.returncode}\n{error_output}")
        
        except Exception as e:
            self.progress_signal.emit(f"单一格式下载时发生错误: {str(e)}")
            self.finished_signal.emit(False, f"单一格式下载时发生错误: {str(e)}")
    
    def _check_aria2c_available_in_thread(self):
        """在下载线程中检查aria2c是否可用"""
        try:
            import subprocess
            result = subprocess.run(
                ["aria2c", "--version"],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            return False
    
    def stop(self):
        """安全地停止进程 - 改进版，专门处理并发片段下载，并清理残余文件"""
        self.progress_signal.emit(t("🔴 stop()方法被调用，开始取消下载进程..."))
        cancel_signal_sent = False  # 标记是否已发送取消信号
        # 设置取消标志，让run方法中的循环能够检测到
        self._cancelled = True
        self.progress_signal.emit(f"⚙️ 设置取消标志: _cancelled = {self._cancelled}")
        
        try:
            if self.process:
                self.progress_signal.emit(t("正在停止下载进程..."))
                process_pid = self.process.pid
                
                # 对于并发片段下载，直接使用最强力的终止方式
                if os.name == 'nt':
                    # Windows系统：使用taskkill强制终止进程树
                    try:
                        self.progress_signal.emit(t("检测到并发下载，使用强制终止模式..."))
                        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                        startupinfo = None
                        if hasattr(subprocess, "STARTUPINFO"):
                            startupinfo = subprocess.STARTUPINFO()
                            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                            startupinfo.wShowWindow = 0
                        # 使用/F /T参数强制终止进程及其所有子进程
                        result = subprocess.run(
                            ['taskkill', '/F', '/T', '/PID', str(process_pid)], 
                            capture_output=True, 
                            text=True,
                            timeout=10,
                            creationflags=creationflags,
                            startupinfo=startupinfo
                        )
                        if result.returncode == 0:
                            self.progress_signal.emit(t("✓ 已成功终止下载进程及所有子进程"))
                        else:
                            self.progress_signal.emit(f"taskkill返回码: {result.returncode}, 错误: {result.stderr}")
                    except subprocess.TimeoutExpired:
                        self.progress_signal.emit(t("⚠️ taskkill命令超时，进程可能已被终止"))
                    except Exception as e:
                        self.progress_signal.emit(f"taskkill执行失败: {str(e)}")
                        # 备用方案：直接kill
                        try:
                            self.process.kill()
                            self.progress_signal.emit(t("已使用备用方案终止进程"))
                        except:
                            pass
                else:
                    # Unix系统：使用信号终止进程组
                    try:
                        import signal
                        # 首先尝试SIGTERM
                        os.killpg(os.getpgid(process_pid), signal.SIGTERM)
                        time.sleep(1)
                        # 然后使用SIGKILL确保终止
                        os.killpg(os.getpgid(process_pid), signal.SIGKILL)
                        self.progress_signal.emit(t("✓ 已终止进程组"))
                    except Exception as e:
                        self.progress_signal.emit(f"终止进程组失败: {str(e)}")
                        try:
                            self.process.kill()
                        except:
                            pass
                
                # 等待进程确实终止
                try:
                    self.process.wait(timeout=2)
                    self.progress_signal.emit(t("✓ 进程已确认终止"))
                except subprocess.TimeoutExpired:
                    self.progress_signal.emit(t("⚠️ 进程终止确认超时，但应该已被强制终止"))
                except:
                    pass
                
                # 清理进程引用
                self.process = None
                
                # 清理下载残余文件
                self._cleanup_partial_downloads()
                
                # 发送取消成功信号
                if not cancel_signal_sent:
                    self.cancel_signal.emit()
                    cancel_signal_sent = True
                    self.progress_signal.emit(t("✓ 下载取消完成"))
                    
        except Exception as e:
            self.progress_signal.emit(f"停止进程时发生错误: {str(e)}")
            # 最后的备用方案
            try:
                if self.process and self.process.poll() is None:
                    self.process.kill()
                    self.progress_signal.emit(t("已使用最后备用方案终止进程"))
                # 清理下载残余文件
                self._cleanup_partial_downloads()
                # 确保发送取消信号
                if not cancel_signal_sent:
                    self.cancel_signal.emit()
                    cancel_signal_sent = True
            except:
                # 即使出错也要发送取消信号，让UI恢复正常状态
                # 尝试清理残余文件
                try:
                    self._cleanup_partial_downloads()
                except:
                    pass
                if not cancel_signal_sent:
                    self.cancel_signal.emit()
                    cancel_signal_sent = True
    
    def _cleanup_partial_downloads(self):
        """清理取消下载后的残余文件"""
        try:
            self.progress_signal.emit(t("🧹 开始清理残余文件..."))
            cleaned_files = []
            
            # 【新增】首先删除正在下载的文件（这些是被中断的实际视频文件）
            if hasattr(self, 'current_downloading_files') and self.current_downloading_files:
                self.progress_signal.emit(f"🔍 检测到 {len(self.current_downloading_files)} 个正在下载的文件需要清理...")
                for file_path in list(self.current_downloading_files):
                    try:
                        if file_path and os.path.exists(file_path):
                            # 验证文件确实是不完整的下载文件
                            os.remove(file_path)
                            cleaned_files.append(file_path)
                            self.progress_signal.emit(f"  ✓ 已删除未完成的下载: {os.path.basename(file_path)}")
                        # 同时检查对应的.part文件（如果存在）
                        part_file = file_path + ".part"
                        if os.path.exists(part_file):
                            os.remove(part_file)
                            cleaned_files.append(part_file)
                            self.progress_signal.emit(f"  ✓ 已删除: {os.path.basename(part_file)}")
                    except Exception as e:
                        self.progress_signal.emit(f"  ⚠️ 删除文件失败 {os.path.basename(file_path)}: {str(e)}")
                # 清空追踪列表
                self.current_downloading_files.clear()
            
            if not self.output_path or not os.path.exists(self.output_path):
                if cleaned_files:
                    self.progress_signal.emit(f"✓ 共清理了 {len(cleaned_files)} 个残余文件")
                return
            
            
            # 要清理的文件扩展名和模式
            patterns_to_clean = [
                '*.part',           # yt-dlp 部分下载文件
                '*.ytdl',           # youtube-dl 临时文件
                '*.f*',             # 临时格式文件 (如 .f137, .f140 等)
                '*.temp',           # 临时文件
                '*.tmp',            # 临时文件
                '*.download',       # 下载中的文件
                '*.aria2',          # aria2 控制文件
                '*.fragment',       # 片段文件
                '*.frag',           # 片段文件
                '*.m4s',            # DASH 片段
                '*.ts',             # HLS 片段（谨慎处理，只删除临时的）
            ]
            
            # 遍历输出目录
            for pattern in patterns_to_clean:
                for file_path in glob.glob(os.path.join(self.output_path, '**', pattern), recursive=True):
                    try:
                        # 检查文件是否是最近创建的（5分钟内），避免误删旧文件
                        file_mtime = os.path.getmtime(file_path)
                        current_time = time.time()
                        
                        # 如果文件在最近5分钟内修改过，认为是本次下载的残余文件
                        if current_time - file_mtime < 300:  # 300秒 = 5分钟
                            # 特殊处理 .ts 文件，只删除明显的临时文件
                            if pattern == '*.ts':
                                filename = os.path.basename(file_path)
                                # 只删除明显的片段命名模式（如 seg-1-v1-a1.ts）
                                if not (filename.startswith('seg-') or filename.startswith('frag-') or 
                                       filename.startswith('chunk-') or '-temp' in filename):
                                    continue
                            
                            os.remove(file_path)
                            cleaned_files.append(file_path)
                            self.progress_signal.emit(f"  ✓ 已删除: {os.path.basename(file_path)}")
                    except Exception as e:
                        self.progress_signal.emit(f"  ⚠️ 删除文件失败 {os.path.basename(file_path)}: {str(e)}")
            
            # 清理空的子目录
            try:
                for root, dirs, files in os.walk(self.output_path, topdown=False):
                    for dir_name in dirs:
                        dir_path = os.path.join(root, dir_name)
                        try:
                            if not os.listdir(dir_path):  # 如果目录为空
                                os.rmdir(dir_path)
                                self.progress_signal.emit(f"  ✓ 已删除空目录: {dir_name}")
                        except:
                            pass
            except:
                pass
            
            if cleaned_files:
                self.progress_signal.emit(f"✓ 共清理了 {len(cleaned_files)} 个残余文件")
            else:
                self.progress_signal.emit(t("✓ 未发现需要清理的残余文件"))
                
        except Exception as e:
            self.progress_signal.emit(f"清理残余文件时发生错误: {str(e)}")
            import traceback
            self.progress_signal.emit(f"详细错误: {traceback.format_exc()}")


