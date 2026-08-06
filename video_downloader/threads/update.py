import socket
from ..utils_update import _open_url_with_fallback, compare_version_text
"""应用更新线程"""
import sys
import os
import re
import json
import time
import subprocess
import urllib.request
import urllib.error
import tempfile
import shutil
from PyQt5.QtCore import QThread, pyqtSignal

from .. import utils
from .. import utils_update
from i18n import t, t_format


class UpdateThread(QThread):
    """yt-dlp更新线程"""
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, str)  # success, message, changelog
    
    def __init__(self, perform_update=False):
        super().__init__()
        self.process = None
        self.perform_update = perform_update  # 是否执行实际更新
        self.before_update_version = None  # 更新前的版本
    
    def run(self):
        try:
            self.progress_signal.emit(t("正在检查当前yt-dlp版本..."))
            
            # 获取当前版本
            current_version = self.get_current_version()
            if not current_version:
                self.finished_signal.emit(False, "无法获取当前yt-dlp版本", "")
                return
            
            self.progress_signal.emit(f"当前版本: {current_version}")
            
            # 获取最新版本信息
            self.progress_signal.emit(t("正在获取最新版本信息..."))
            latest_info = self.get_latest_version_info()
            if not latest_info:
                self.finished_signal.emit(False, "无法获取最新版本信息", "")
                return
            
            latest_version = latest_info.get('tag_name', 'Unknown')
            self.progress_signal.emit(f"最新版本: {latest_version}")
            
            # 如果需要执行更新
            if self.perform_update:
                if self.compare_versions(current_version, latest_version) < 0:
                    # 保存更新前的版本
                    self.before_update_version = current_version
                    # 执行更新
                    self.progress_signal.emit(t("开始更新yt-dlp..."))
                    success, error_msg = self.update_yt_dlp()
                    
                    if success:
                        # 验证版本是否真正更新
                        self.progress_signal.emit(t("正在验证更新结果..."))
                        import time
                        time.sleep(2)  # 等待文件系统更新
                        
                        new_version = self.get_current_version()
                        if new_version and new_version != self.before_update_version:
                            # 版本确实改变了
                            self.finished_signal.emit(True, f"success|{self.before_update_version}|{new_version}", "")
                        else:
                            # 版本没有改变，更新实际失败
                            reason = f"更新命令执行完成，但版本未发生变化（仍为 {current_version}）。可能原因：\n1. 当前已是最新版本\n2. 更新包下载失败\n3. 权限不足无法替换文件"
                            self.finished_signal.emit(False, reason, "")
                    else:
                        self.finished_signal.emit(False, error_msg, "")
                else:
                    self.finished_signal.emit(True, "已是最新版本，无需更新", "")
                return
            
            # 比较版本 - 只检查，不自动更新
            if self.compare_versions(current_version, latest_version) >= 0:
                # 已是最新版本
                self.finished_signal.emit(True, f"current|{current_version}|{latest_version}|", "")
            else:
                # 发现新版本
                changelog = self.extract_changelog(latest_info)
                self.finished_signal.emit(True, f"update_available|{current_version}|{latest_version}|{changelog}", changelog)
                
        except Exception as e:
            self.finished_signal.emit(False, f"检查过程中发生错误: {str(e)}", "")
    
    def get_current_version(self):
        """获取当前yt-dlp版本"""
        try:
            result = subprocess.run(
                ["yt-dlp.exe", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None
    
    def get_latest_version_info(self):
        """获取最新版本信息"""
        try:
            import urllib.request
            import json
            
            # 从GitHub API获取最新版本信息
            url = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
            headers = {'User-Agent': 'Mozilla/5.0'}
            with _open_url_with_fallback(url, timeout=15, headers=headers) as response:
                data = json.loads(response.read().decode())
                return data
        except Exception:
            return None
    
    def compare_versions(self, version1, version2):
        """比较版本号，返回 -1, 0, 1"""
        return compare_version_text(version1, version2)
    
    def update_yt_dlp(self):
        """执行yt-dlp更新，返回 (success, error_message)"""
        last_error = ""
        
        try:
            # 方法1: 使用yt-dlp自身的更新功能
            self.progress_signal.emit(t("尝试使用yt-dlp自更新..."))
            result = subprocess.run(
                ["yt-dlp.exe", "-U"],
                capture_output=True,
                text=True,
                timeout=300,  # 5分钟超时
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            # 检查输出内容，而不仅仅是返回码
            output = (result.stdout + result.stderr).lower()
            full_output = result.stdout + result.stderr
            
            if result.returncode == 0:
                # 检查输出中是否包含更新成功的标志
                if "updated" in output or "downloading" in output:
                    self.progress_signal.emit(t("yt-dlp自更新命令执行成功"))
                    return True, ""
                elif "already up to date" in output or "latest version" in output:
                    last_error = "当前已是最新版本"
                    self.progress_signal.emit(f"yt-dlp自更新: {last_error}")
                else:
                    # 返回码为0但输出不明确
                    self.progress_signal.emit(t("yt-dlp自更新命令完成"))
                    return True, ""
            else:
                # 识别具体错误类型
                error_type, error_detail = self._classify_error(full_output, "yt-dlp自更新")
                last_error = f"{error_type}: {error_detail}"
                self.progress_signal.emit(last_error)
                
        except subprocess.TimeoutExpired:
            last_error = "yt-dlp自更新超时（超过5分钟）。可能原因：网络连接缓慢或服务器响应慢"
            self.progress_signal.emit(last_error)
        except FileNotFoundError:
            last_error = "yt-dlp自更新失败: 未找到yt-dlp.exe文件"
            self.progress_signal.emit(last_error)
        except Exception as e:
            error_type, error_detail = self._classify_error(str(e), "yt-dlp自更新")
            last_error = f"{error_type}: {error_detail}"
            self.progress_signal.emit(last_error)
        
        # 方法2: 使用pip更新（如果是通过pip安装的）
        try:
            self.progress_signal.emit(t("尝试使用pip更新..."))
            result = subprocess.run(
                ["python", "-m", "pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            output = (result.stdout + result.stderr).lower()
            full_output = result.stdout + result.stderr
            
            if result.returncode == 0:
                if "successfully installed" in output:
                    self.progress_signal.emit(t("pip更新成功"))
                    return True, ""
                elif "requirement already satisfied" in output or "already up-to-date" in output:
                    last_error = "pip显示当前已是最新版本"
                    self.progress_signal.emit(last_error)
                else:
                    self.progress_signal.emit(t("pip更新命令完成"))
                    return True, ""
            else:
                # 识别具体错误类型
                error_type, error_detail = self._classify_error(full_output, "pip更新")
                last_error = f"{error_type}: {error_detail}"
                self.progress_signal.emit(last_error)
                
        except subprocess.TimeoutExpired:
            last_error = "pip更新超时（超过5分钟）。可能原因：网络连接缓慢、PyPI服务器响应慢或需要配置代理"
            self.progress_signal.emit(last_error)
        except FileNotFoundError:
            last_error = "pip更新失败: 未找到Python或pip"
            self.progress_signal.emit(last_error)
        except Exception as e:
            error_type, error_detail = self._classify_error(str(e), "pip更新")
            last_error = f"{error_type}: {error_detail}"
            self.progress_signal.emit(last_error)
        
        # 方法3: 手动下载更新（Windows）
        if os.name == 'nt':
            try:
                self.progress_signal.emit(t("尝试手动下载更新..."))
                success, error_msg = self.manual_download_update()
                if success:
                    return True, ""
                else:
                    last_error = error_msg
            except Exception as e:
                error_type, error_detail = self._classify_error(str(e), "手动下载")
                last_error = f"{error_type}: {error_detail}"
                self.progress_signal.emit(last_error)
        
        # 所有方法都失败
        return False, last_error if last_error else "所有更新方法均失败"
    
    def _classify_error(self, error_text, context=""):
        """分类错误类型，返回 (错误类型, 详细信息)"""
        error_lower = error_text.lower()
        
        # 网络相关错误（优先检查）
        network_keywords = [
            ('connection', 'timed out', '网络连接超时'),
            ('connection', 'refused', '连接被拒绝（目标服务器不可达）'),
            ('connection', 'reset', '网络连接被重置'),
            ('connection', 'aborted', '网络连接中断'),
            ('failed to establish', 'connection', '无法建立网络连接'),
            ('network', 'unreachable', '网络不可达'),
            ('name resolution', 'failed', 'DNS解析失败（无法解析域名）'),
            ('getaddrinfo', 'failed', 'DNS解析失败'),
            ('no route to host', '', '无法路由到目标主机'),
            ('timeout', 'error', '网络超时'),
            ('socket', 'timeout', '网络套接字超时'),
            ('unable to download', '', '无法下载（可能是网络问题）'),
            ('urlopen error', '', 'URL打开失败（网络或代理问题）'),
            ('http error 50', '', 'HTTP服务器错误（500-599）'),
            ('http error 40', '', 'HTTP客户端错误（400-499）'),
            ('ssl', 'error', 'SSL/TLS证书错误'),
            ('certificate', 'verify', 'SSL证书验证失败'),
            ('proxy', 'error', '代理服务器错误'),
            ('tunnel connection', 'failed', '代理隧道连接失败'),
        ]
        
        for kw1, kw2, description in network_keywords:
            if kw1 in error_lower and (not kw2 or kw2 in error_lower):
                hint = "\n建议：检查网络连接，如访问外网需配置代理或更新镜像" if context else ""
                return ("网络错误", f"{description}{hint}")
        
        # 权限相关错误
        permission_keywords = ['permission denied', 'access denied', 'permissionerror', 'errno 13']
        if any(kw in error_lower for kw in permission_keywords):
            return ("权限错误", "权限不足。建议以管理员身份运行程序")
        
        # 磁盘空间错误
        disk_keywords = ['no space left', 'disk full', 'errno 28']
        if any(kw in error_lower for kw in disk_keywords):
            return ("磁盘错误", "磁盘空间不足")
        
        # 文件不存在
        file_keywords = ['no such file', 'filenotfounderror', 'errno 2', 'cannot find']
        if any(kw in error_lower for kw in file_keywords):
            return ("文件错误", "找不到所需文件或程序")
        
        # 未知错误
        return (f"{context}失败" if context else "错误", error_text.strip()[:200])
    
    def manual_download_update(self):
        """手动下载yt-dlp最新版本（Windows），返回 (success, error_message)"""
        try:
            import urllib.request
            import urllib.error
            import tempfile
            import shutil
            
            # 下载最新的yt-dlp.exe
            download_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".exe") as temp_file:
                temp_path = temp_file.name
            
            self.progress_signal.emit(t("正在从GitHub下载yt-dlp.exe..."))
            
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                with _open_url_with_fallback(download_url, timeout=600, headers=headers) as response:
                    with open(temp_path, 'wb') as f:
                        shutil.copyfileobj(response, f)
            except urllib.error.URLError as url_error:
                # 精确识别网络错误
                error_type, error_detail = self._classify_error(str(url_error), "GitHub下载")
                return False, f"{error_type}: {error_detail}"
            except urllib.error.HTTPError as http_error:
                return False, f"网络错误: HTTP {http_error.code} - {http_error.reason}\n建议：检查网络连接，如访问外网需配置代理"
            except socket.timeout:
                return False, "网络错误: 下载超时\n建议：检查网络连接速度，如访问外网需配置代理"
            except Exception as download_error:
                error_type, error_detail = self._classify_error(str(download_error), "GitHub下载")
                return False, f"{error_type}: {error_detail}"
            
            # 验证下载的文件
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) < 1000:
                return False, "文件错误: 下载的文件无效或损坏（可能因网络中断）"
            
            # 备份当前版本
            current_path = "yt-dlp.exe"
            backup_path = "yt-dlp.exe.backup"
            
            try:
                if os.path.exists(current_path):
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                    shutil.move(current_path, backup_path)
                
                # 替换为新版本
                shutil.move(temp_path, current_path)
                
                # 验证新文件可执行
                try:
                    test_result = subprocess.run(
                        [current_path, "--version"],
                        capture_output=True,
                        timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    if test_result.returncode != 0:
                        # 新文件无法执行，恢复备份
                        if os.path.exists(backup_path):
                            shutil.move(backup_path, current_path)
                        return False, "文件错误: 下载的文件无法正常执行，已恢复原版本"
                except Exception:
                    # 新文件无法执行，恢复备份
                    if os.path.exists(backup_path):
                        shutil.move(backup_path, current_path)
                    return False, "文件错误: 下载的文件无法正常执行，已恢复原版本"
                
                self.progress_signal.emit(t("手动更新完成"))
                return True, ""
                
            except PermissionError:
                return False, "权限错误: 权限不足，无法替换yt-dlp.exe文件。请尝试以管理员身份运行程序"
            except Exception as replace_error:
                error_type, error_detail = self._classify_error(str(replace_error), "文件替换")
                return False, f"{error_type}: {error_detail}"
            
        except Exception as e:
            error_type, error_detail = self._classify_error(str(e), "手动下载更新")
            error_msg = f"{error_type}: {error_detail}"
            self.progress_signal.emit(error_msg)
            return False, error_msg
    
    def extract_changelog(self, release_info):
        """提取更新日志"""
        try:
            if release_info and 'body' in release_info:
                return release_info['body'][:1000]  # 限制长度
        except Exception:
            pass
        return "更新日志获取失败"
    
    def stop(self):
        """停止更新进程"""
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.quit()
        self.wait()


