from ..utils_update import _open_url_with_fallback
"""依赖更新线程 - yt-dlp/FFmpeg/Deno 更新"""
import sys
import os
import re
import json
import subprocess
import urllib.request
import tempfile
import shutil
import zipfile
from PyQt5.QtCore import QThread, pyqtSignal


class DependencyUpdateThread(QThread):
    """统一依赖更新线程 - 检查并更新 yt-dlp, ffmpeg, ffprobe, deno"""
    progress_signal = pyqtSignal(str)
    component_signal = pyqtSignal(str, str, str, str)  # component_name, current_ver, latest_ver, status
    finished_signal = pyqtSignal(bool, str)  # success, summary_message
    
    # 组件配置
    COMPONENTS = {
        'yt-dlp': {
            'file': 'yt-dlp.exe',
            'repo': 'yt-dlp/yt-dlp',
            'asset_pattern': 'yt-dlp.exe',
            'version_cmd': ['yt-dlp.exe', '--version'],
        },
        'ffmpeg': {
            'file': 'ffmpeg.exe',
            'repo': 'yt-dlp/FFmpeg-Builds',
            'asset_pattern': 'ffmpeg-master-latest-win64-gpl.zip',
            'version_cmd': ['ffmpeg.exe', '-version'],
            'extract_from_zip': 'ffmpeg.exe',
        },
        'ffprobe': {
            'file': 'ffprobe.exe',
            'repo': 'yt-dlp/FFmpeg-Builds',
            'asset_pattern': 'ffmpeg-master-latest-win64-gpl.zip',
            'version_cmd': ['ffprobe.exe', '-version'],
            'extract_from_zip': 'ffprobe.exe',
        },
        'deno': {
            'file': 'deno.exe',
            'repo': 'denoland/deno',
            'asset_pattern': 'deno-x86_64-pc-windows-msvc.zip',
            'version_cmd': ['deno.exe', '--version'],
            'extract_from_zip': 'deno.exe',
        },
    }
    
    def __init__(self, perform_update=False, components_to_update=None, proxy_enabled=False, auto_update_components=None):
        super().__init__()
        self.perform_update = perform_update
        self.components_to_update = components_to_update or []  # 要更新的组件列表
        self.proxy_enabled = proxy_enabled
        self.update_results = {}
        self.auto_update_components = auto_update_components or []  # 静默自动更新的组件列表
    
    def run(self):
        try:
            import urllib.request
            import json
            import zipfile
            import shutil
            import tempfile
            
            updates_available = []
            updates_performed = []
            errors = []
            
            # 跳过 ffprobe 单独检查（与 ffmpeg 一起更新）
            check_components = ['yt-dlp', 'ffmpeg', 'deno']
            
            for comp_name in check_components:
                config = self.COMPONENTS[comp_name]
                self.progress_signal.emit(t_format("🔍 检查 {} 版本...", comp_name))
                
                # 获取当前版本
                current_ver = self._get_current_version(config)
                if not current_ver:
                    current_ver = "未安装"
                
                # 获取最新版本
                latest_ver, download_url = self._get_latest_version(config['repo'], config['asset_pattern'])
                if not latest_ver:
                    self.component_signal.emit(comp_name, current_ver, t("获取失败"), "error")
                    continue
                
                # 比较版本
                if self._needs_update(current_ver, latest_ver, comp_name):
                    self.component_signal.emit(comp_name, current_ver, latest_ver, "update_available")
                    updates_available.append((comp_name, current_ver, latest_ver, download_url))
                else:
                    self.component_signal.emit(comp_name, current_ver, latest_ver, "up_to_date")
            
            # 对 auto_update_components 中的组件执行静默自动更新
            if self.auto_update_components:
                auto_remaining = []
                for comp_name, current_ver, latest_ver, download_url in updates_available:
                    if comp_name in self.auto_update_components:
                        self.progress_signal.emit(t_format("⬇️ 自动更新 {}...", comp_name))
                        config = self.COMPONENTS[comp_name]
                        try:
                            success = self._download_and_update(comp_name, config, download_url)
                            if success:
                                updates_performed.append(comp_name)
                                self.component_signal.emit(comp_name, current_ver, latest_ver, "auto_updated")
                            else:
                                errors.append(comp_name)
                        except Exception as e:
                            errors.append(f"{comp_name}: {str(e)}")
                    else:
                        auto_remaining.append((comp_name, current_ver, latest_ver, download_url))
                updates_available = auto_remaining
            
            # 如果只是检查（非手动 perform_update），返回结果
            if not self.perform_update:
                if updates_available:
                    names = [u[0] for u in updates_available]
                    self.finished_signal.emit(True, t_format("发现 {} 个组件可更新: {}", len(updates_available), ', '.join(names)))
                elif updates_performed:
                    self.finished_signal.emit(True, t_format("已自动更新: {}", ', '.join(updates_performed)))
                else:
                    self.finished_signal.emit(True, t("所有组件已是最新版本"))
                return
            
            # 执行更新
            for comp_name, current_ver, latest_ver, download_url in updates_available:
                if self.components_to_update and comp_name not in self.components_to_update:
                    continue
                    
                self.progress_signal.emit(t_format("⬇️ 正在更新 {}...", comp_name))
                config = self.COMPONENTS[comp_name]
                
                try:
                    success = self._download_and_update(comp_name, config, download_url)
                    if success:
                        updates_performed.append(comp_name)
                        self.component_signal.emit(comp_name, current_ver, latest_ver, "updated")
                    else:
                        errors.append(comp_name)
                        self.component_signal.emit(comp_name, current_ver, latest_ver, "update_failed")
                except Exception as e:
                    errors.append(f"{comp_name}: {str(e)}")
                    self.component_signal.emit(comp_name, current_ver, latest_ver, "update_failed")
            
            # 汇总结果
            if updates_performed:
                msg = t_format("✅ 成功更新: {}", ', '.join(updates_performed))
                if errors:
                    msg += t_format("\n❌ 更新失败: {}", ', '.join(errors))
                self.finished_signal.emit(True, msg)
            elif errors:
                self.finished_signal.emit(False, t_format("更新失败: {}", ', '.join(errors)))
            else:
                self.finished_signal.emit(True, t("没有需要更新的组件"))
                
        except Exception as e:
            self.finished_signal.emit(False, t_format("更新过程出错: {}", str(e)))
    
    def _get_current_version(self, config):
        """获取组件当前版本"""
        try:
            result = subprocess.run(
                config['version_cmd'],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode == 0:
                output = result.stdout.strip()
                # 提取版本号
                import re
                # 尝试多种版本号格式
                patterns = [
                    r'(\d{4}\.\d{1,2}\.\d{1,2})',  # yt-dlp: 2024.01.01
                    r'N-\d+-g[a-f0-9]+-(\d{8})',    # ffmpeg nightly: N-122509-gd25d133df3-20260120 -> 20260120
                    r'version\s+(\d+\.\d+(?:\.\d+)?)',  # ffmpeg release: version 6.1
                    r'deno\s+(\d+\.\d+\.\d+)',  # deno: deno 1.38.0
                    r'(\d+\.\d+\.\d+)',  # 通用: x.y.z
                ]
                for pattern in patterns:
                    match = re.search(pattern, output, re.IGNORECASE)
                    if match:
                        return match.group(1)
                return output.split('\n')[0][:50]  # 返回第一行

        except Exception:
            pass
        return None
    
    def _get_latest_version(self, repo, asset_pattern):
        """从 GitHub API 获取最新版本"""
        try:
            import urllib.request
            import json
            import re
            
            url = f"https://api.github.com/repos/{repo}/releases/latest"
            headers = {'User-Agent': 'Mozilla/5.0'}
            
            with _open_url_with_fallback(url, timeout=15, headers=headers) as response:
                data = json.loads(response.read().decode())
                tag = data.get('tag_name', '').lstrip('v')
                
                # 对于 ffmpeg-builds，提取日期作为版本号
                if 'FFmpeg-Builds' in repo:
                    # 优先从 tag_name 提取 (autobuild-2026-01-20 -> 20260120)
                    date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', tag)
                    if not date_match:
                        # 如果 tag 是 'latest'，从 release name 提取 (Latest Auto-Build (2026-01-20 14:28) -> 20260120)
                        name = data.get('name', '')
                        date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', name)
                    
                    if not date_match:
                        # 如果还是没找到，从 published_at 提取 (2026-01-20T14:40:12Z -> 20260120)
                        published_at = data.get('published_at', '')
                        date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', published_at)
                        
                    if date_match:
                        tag = f"{date_match.group(1)}{date_match.group(2)}{date_match.group(3)}"

                
                # 查找下载链接
                download_url = None
                for asset in data.get('assets', []):
                    if asset_pattern in asset.get('name', ''):
                        download_url = asset.get('browser_download_url')
                        break
                
                return tag, download_url
        except Exception as e:
            self.progress_signal.emit(t_format("获取版本信息失败: {}", str(e)))
        return None, None

    
    def _needs_update(self, current_ver, latest_ver, comp_name):
        """判断是否需要更新"""
        if current_ver == "未安装":
            return True
        try:
            # 简单比较：如果版本号不同就认为需要更新
            current_clean = current_ver.replace('v', '').strip()
            latest_clean = latest_ver.replace('v', '').strip()
            
            # 对于带点的版本号 (如 2025.01.15 或 1.38.0)
            if '.' in current_clean and '.' in latest_clean:
                try:
                    c_parts = [int(p) for p in current_clean.split('.')]
                    l_parts = [int(p) for p in latest_clean.split('.')]
                    return c_parts < l_parts
                except:
                    return current_clean < latest_clean
            
            # 对于纯数字日期格式 (如 20260120)
            if current_clean.isdigit() and latest_clean.isdigit():
                return int(current_clean) < int(latest_clean)
                
            return current_clean != latest_clean
        except Exception:
            return False

    
    def _download_and_update(self, comp_name, config, download_url):
        """下载并更新组件"""
        import urllib.request
        import tempfile
        import zipfile
        import shutil
        
        if not download_url:
            self.progress_signal.emit(t_format("❌ {}: 未找到下载链接", comp_name))
            return False
        
        try:
            self.progress_signal.emit(t_format("⬇️ 下载 {}...", comp_name))
            
            # 下载文件
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_file = os.path.join(temp_dir, 'download')
                headers = {'User-Agent': 'Mozilla/5.0'}
                with _open_url_with_fallback(download_url, timeout=600, headers=headers) as response:
                    with open(temp_file, 'wb') as f:
                        shutil.copyfileobj(response, f)
                
                # 确定目标文件路径
                target_file = config['file']
                
                # 如果是压缩包，解压
                if 'extract_from_zip' in config:
                    self.progress_signal.emit(t_format("📦 解压 {}...", comp_name))
                    extract_name = config['extract_from_zip']
                    
                    with zipfile.ZipFile(temp_file, 'r') as zf:
                        # 查找文件（可能在子目录中）
                        for name in zf.namelist():
                            if name.endswith(extract_name):
                                # 解压到临时目录
                                extracted = zf.extract(name, temp_dir)
                                
                                # 备份原文件
                                if os.path.exists(target_file):
                                    backup = target_file + '.backup'
                                    if os.path.exists(backup):
                                        os.remove(backup)
                                    shutil.move(target_file, backup)
                                
                                # 移动新文件
                                shutil.move(extracted, target_file)
                                
                                # 如果是 ffmpeg，同时更新 ffprobe
                                if comp_name == 'ffmpeg':
                                    for n in zf.namelist():
                                        if n.endswith('ffprobe.exe'):
                                            extracted_probe = zf.extract(n, temp_dir)
                                            probe_target = 'ffprobe.exe'
                                            if os.path.exists(probe_target):
                                                probe_backup = probe_target + '.backup'
                                                if os.path.exists(probe_backup):
                                                    os.remove(probe_backup)
                                                shutil.move(probe_target, probe_backup)
                                            shutil.move(extracted_probe, probe_target)
                                            self.progress_signal.emit(t_format("✅ ffprobe 已同步更新", ))
                                            break
                                break
                else:
                    # 直接是可执行文件
                    if os.path.exists(target_file):
                        backup = target_file + '.backup'
                        if os.path.exists(backup):
                            os.remove(backup)
                        shutil.move(target_file, backup)
                    shutil.move(temp_file, target_file)
                
                self.progress_signal.emit(t_format("✅ {} 更新成功", comp_name))
                return True
                
        except PermissionError:
            self.progress_signal.emit(t_format("❌ {}: 权限不足，请以管理员身份运行", comp_name))
            return False
        except Exception as e:
            self.progress_signal.emit(f"❌ {comp_name}: {str(e)}")
            return False


