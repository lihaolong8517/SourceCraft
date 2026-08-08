"""下载历史记录管理器"""
import json
from i18n import t_format
import os
import sys
import time
from datetime import datetime
from collections import deque

# Import constants for path resolution
from .constants import RUNTIME_APP_DIR, APP_DISPLAY_NAME

class DownloadHistoryManager:
    """下载历史记录管理器"""
    
    def __init__(self, settings_file="download_history.json"):
        # 使用绝对路径，防止 QFileDialog 改变 CWD 后读写到错误位置
        if not os.path.isabs(settings_file):
            self.settings_file = os.path.join(RUNTIME_APP_DIR, settings_file)
        else:
            self.settings_file = settings_file
        self.history = self.load_history()
    
    def load_history(self):
        """从文件加载历史记录"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                if isinstance(history, list):
                    return sorted(history, key=self._record_sort_time, reverse=True)
        except Exception as e:
            print(t_format("加载历史记录失败: {}", e))
        return []

    @staticmethod
    def _record_sort_time(record):
        """获取历史记录排序时间，确保刷新后最新记录显示在顶部。"""
        if not isinstance(record, dict):
            return 0

        record_time = (
            record.get("download_time")
            or record.get("transcode_time")
            or record.get("process_time")
            or ""
        )
        try:
            return datetime.fromisoformat(str(record_time)).timestamp()
        except Exception:
            return 0
    
    def save_history(self):
        """保存历史记录到文件"""
        try:
            # 确保目标目录存在
            target_dir = os.path.dirname(self.settings_file)
            if target_dir and not os.path.isdir(target_dir):
                os.makedirs(target_dir, exist_ok=True)
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(t_format("保存历史记录失败: {}, 路径: {}", e, self.settings_file))
    
    def add_record(self, url, title, file_path, download_time=None, file_size=None, format_info=None):
        """添加下载记录"""
        if download_time is None:
            download_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        record = {
            "url": url,
            "title": title,
            "file_path": file_path,
            "download_time": download_time,
            "file_size": file_size,
            "format_info": format_info,
            "type": "download"  # 标记为下载记录
        }
        
        # 避免重复记录
        for index, existing in enumerate(self.history):
            # 只检查下载记录的重复，跳过转码记录
            if existing.get("type") == "download" and existing.get("url") == url and existing.get("file_path") == file_path:
                # 更新现有记录，并移动到顶部，避免用户误以为本次下载没有显示
                updated_record = dict(existing)
                updated_record.update(record)
                self.history.pop(index)
                self.history.insert(0, updated_record)
                self.save_history()
                return
        
        # 添加新记录到列表开头
        self.history.insert(0, record)
        
        # 限制历史记录数量（最多保留100条）
        if len(self.history) > 100:
            self.history = self.history[:100]
        
        self.save_history()
    
    def add_transcode_record(self, input_file, output_file, transcode_time=None, file_size=None, transcode_options=None, success=True, error_message=None):
        """添加转码记录"""
        if transcode_time is None:
            transcode_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 获取文件大小
        if file_size is None and success and os.path.exists(output_file):
            try:
                file_size = os.path.getsize(output_file)
            except:
                file_size = None
        
        record = {
            "input_file": input_file,
            "output_file": output_file,
            "title": os.path.basename(input_file),
            "file_path": output_file,
            "transcode_time": transcode_time,
            "file_size": file_size,
            "transcode_options": transcode_options,
            "success": success,
            "error_message": error_message,
            "type": "transcode"  # 标记为转码记录
        }
        
        # 避免重复记录
        for existing in self.history:
            if (existing.get("type") == "transcode" and 
                existing.get("input_file") == input_file and 
                existing.get("output_file") == output_file):
                # 更新现有记录
                existing.update(record)
                self.save_history()
                return
        
        # 添加新记录到列表开头
        self.history.insert(0, record)
        
        # 限制历史记录数量（最多保留100条）
        if len(self.history) > 100:
            self.history = self.history[:100]
        
        self.save_history()
    
    def add_image_process_record(self, input_files, output_dir, process_time=None, 
                                  success_count=0, fail_count=0, total_saved=0, 
                                  process_options=None, output_files=None):
        """添加图片处理记录"""
        if process_time is None:
            process_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 生成简短的输入文件描述
        if len(input_files) == 1:
            title = os.path.basename(input_files[0])
        else:
            title = t_format("{} 张图片", len(input_files))
        
        record = {
            "input_files": input_files[:10],  # 只保存前10个文件名，避免过长
            "input_count": len(input_files),
            "output_dir": output_dir,
            "output_files": output_files[:10] if output_files else [],  # 保存输出文件列表
            "title": title,
            "file_path": output_dir,  # 用于"定位文件"功能
            "process_time": process_time,
            "success_count": success_count,
            "fail_count": fail_count,
            "total_saved": total_saved,  # 节省的字节数
            "process_options": process_options,
            "type": "image_process"  # 标记为图片处理记录
        }
        
        # 添加新记录到列表开头
        self.history.insert(0, record)
        
        # 限制历史记录数量（最多保留100条）
        if len(self.history) > 100:
            self.history = self.history[:100]
        
        self.save_history()

    def add_quality_repair_record(self, input_files, output_dir, process_time=None,
                                  success_count=0, fail_count=0, process_options=None,
                                  output_files=None):
        """添加画质修复记录"""
        if process_time is None:
            process_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if len(input_files) == 1:
            title = os.path.basename(input_files[0])
        else:
            title = t_format("{} 个修复文件", len(input_files))

        record = {
            "input_files": input_files[:10],
            "input_count": len(input_files),
            "output_dir": output_dir,
            "output_files": output_files[:10] if output_files else [],
            "title": title,
            "file_path": output_files[0] if output_files else output_dir,
            "process_time": process_time,
            "success_count": success_count,
            "fail_count": fail_count,
            "process_options": process_options,
            "type": "quality_repair",
        }

        self.history.insert(0, record)
        if len(self.history) > 100:
            self.history = self.history[:100]
        self.save_history()
    
    def get_history(self):
        """获取历史记录列表"""
        return self.history
    
    def remove_record(self, index):
        """删除指定索引的记录"""
        if 0 <= index < len(self.history):
            self.history.pop(index)
            self.save_history()
    
    def clear_history(self):
        """清空所有历史记录"""
        self.history = []
        self.save_history()

