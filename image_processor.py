# -*- coding: utf-8 -*-
"""
图片处理模块 - 提供图片格式转换、质量压缩、尺寸调整和批量处理功能
使用 Pillow 库作为核心图片处理引擎
"""

import os
import subprocess
from PyQt5.QtCore import QThread, pyqtSignal

# 尝试导入 Pillow
try:
    from PIL import Image, ImageOps
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False


class ImageProcessor:
    """图片处理核心类"""
    
    # 支持的输入格式
    SUPPORTED_INPUT_FORMATS = {
        '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', 
        '.tiff', '.tif', '.ico', '.heic', '.heif'
    }
    
    # 支持的输出格式及其配置
    OUTPUT_FORMATS = {
        'JPEG': {'ext': '.jpg', 'quality_range': (1, 100), 'default_quality': 85},
        'PNG': {'ext': '.png', 'compress_range': (0, 9), 'default_compress': 6},
        'WebP': {'ext': '.webp', 'quality_range': (1, 100), 'default_quality': 80},
        'BMP': {'ext': '.bmp'},
        'GIF': {'ext': '.gif'},
        'TIFF': {'ext': '.tiff'},
        'ICO': {'ext': '.ico'},
    }
    
    # 重采样方法
    RESAMPLE_METHODS = {
        '最近邻 (最快)': Image.Resampling.NEAREST if PILLOW_AVAILABLE else 0,
        '双线性': Image.Resampling.BILINEAR if PILLOW_AVAILABLE else 1,
        '双三次': Image.Resampling.BICUBIC if PILLOW_AVAILABLE else 2,
        'Lanczos (最佳质量)': Image.Resampling.LANCZOS if PILLOW_AVAILABLE else 3,
    }
    
    @staticmethod
    def is_supported(file_path):
        """检查文件是否为支持的图片格式"""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in ImageProcessor.SUPPORTED_INPUT_FORMATS
    
    @staticmethod
    def get_image_info(file_path):
        """获取图片信息"""
        if not PILLOW_AVAILABLE:
            return None
        try:
            with Image.open(file_path) as img:
                return {
                    'width': img.width,
                    'height': img.height,
                    'format': img.format,
                    'mode': img.mode,
                    'size': os.path.getsize(file_path)
                }
        except Exception:
            return None
    
    @staticmethod
    def process_image(input_path, output_path, options):
        """
        处理单张图片
        
        Args:
            input_path: 输入图片路径
            output_path: 输出图片路径
            options: 处理选项字典，包含以下键：
                - format: 输出格式 (JPEG/PNG/WebP/BMP/GIF/TIFF/ICO)
                - quality: 质量 (1-100, 用于JPEG和WebP)
                - compress_level: PNG压缩级别 (0-9)
                - resize_mode: 尺寸调整模式 ('none'/'percent'/'fixed'/'max_side')
                - resize_percent: 缩放百分比
                - resize_width: 目标宽度
                - resize_height: 目标高度
                - resize_max_side: 最大边长
                - keep_aspect_ratio: 是否保持宽高比
                - resample: 重采样方法
        
        Returns:
            (success: bool, message: str, output_size: int)
        """
        if not PILLOW_AVAILABLE:
            return False, "Pillow 库未安装，请运行: pip install Pillow", 0
        
        try:
            # 打开图片
            with Image.open(input_path) as img:
                # 转换模式（处理RGBA到RGB的转换等）
                output_format = options.get('format', 'JPEG')
                
                # 处理透明通道
                if output_format in ['JPEG', 'BMP'] and img.mode in ['RGBA', 'LA', 'P']:
                    # 创建白色背景
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    if img.mode in ['RGBA', 'LA']:
                        # 分离alpha通道并合成
                        if img.mode == 'LA':
                            img = img.convert('RGBA')
                        background.paste(img, mask=img.split()[3])
                        img = background
                    else:
                        img = img.convert('RGB')
                elif output_format == 'JPEG' and img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # 尺寸调整
                resize_mode = options.get('resize_mode', 'none')
                if resize_mode != 'none':
                    original_width, original_height = img.size
                    new_width, new_height = original_width, original_height
                    
                    resample_name = options.get('resample', 'Lanczos (最佳质量)')
                    resample = ImageProcessor.RESAMPLE_METHODS.get(resample_name, Image.Resampling.LANCZOS)
                    
                    if resize_mode == 'percent':
                        percent = options.get('resize_percent', 100) / 100.0
                        new_width = int(original_width * percent)
                        new_height = int(original_height * percent)
                    
                    elif resize_mode == 'fixed':
                        new_width = options.get('resize_width', original_width)
                        new_height = options.get('resize_height', original_height)
                        
                        if options.get('keep_aspect_ratio', True):
                            # 计算保持比例的尺寸
                            ratio = min(new_width / original_width, new_height / original_height)
                            new_width = int(original_width * ratio)
                            new_height = int(original_height * ratio)
                    
                    elif resize_mode == 'max_side':
                        max_side = options.get('resize_max_side', max(original_width, original_height))
                        if original_width > original_height:
                            ratio = max_side / original_width
                        else:
                            ratio = max_side / original_height
                        new_width = int(original_width * ratio)
                        new_height = int(original_height * ratio)
                    
                    # 确保尺寸至少为1
                    new_width = max(1, new_width)
                    new_height = max(1, new_height)
                    
                    if (new_width, new_height) != (original_width, original_height):
                        img = img.resize((new_width, new_height), resample)
                
                # 保存选项
                save_kwargs = {}
                
                if output_format == 'JPEG':
                    save_kwargs['quality'] = options.get('quality', 85)
                    save_kwargs['optimize'] = True
                    # 渐进式JPEG
                    save_kwargs['progressive'] = True
                
                elif output_format == 'PNG':
                    save_kwargs['compress_level'] = options.get('compress_level', 6)
                    save_kwargs['optimize'] = True
                
                elif output_format == 'WebP':
                    save_kwargs['quality'] = options.get('quality', 80)
                    save_kwargs['method'] = 4  # 压缩质量，0-6，越高越慢但压缩率越好
                
                elif output_format == 'ICO':
                    # ICO需要特殊处理尺寸
                    if img.size[0] > 256 or img.size[1] > 256:
                        img.thumbnail((256, 256), Image.Resampling.LANCZOS)
                
                # 确保输出目录存在
                output_dir = os.path.dirname(output_path)
                if output_dir and not os.path.exists(output_dir):
                    os.makedirs(output_dir)
                
                # 保存图片
                img.save(output_path, output_format, **save_kwargs)
                
                output_size = os.path.getsize(output_path)
                return True, f"处理成功", output_size
                
        except Exception as e:
            return False, f"处理失败: {str(e)}", 0


class ImageProcessThread(QThread):
    """图片处理线程"""
    
    # 信号定义
    progress_signal = pyqtSignal(str)  # 日志消息
    progress_percent_signal = pyqtSignal(float)  # 进度百分比
    file_processed_signal = pyqtSignal(str, bool, str, int, int)  # 文件名, 成功, 消息, 原始大小, 新大小
    finished_signal = pyqtSignal(bool, str)  # 完成状态, 消息
    cancel_signal = pyqtSignal()  # 取消信号
    
    def __init__(self, file_list, output_dir, options):
        """
        初始化图片处理线程
        
        Args:
            file_list: 要处理的文件路径列表
            output_dir: 输出目录
            options: 处理选项字典
        """
        super().__init__()
        self.file_list = file_list
        self.output_dir = output_dir
        self.options = options
        self._cancelled = False
    
    def run(self):
        """执行图片处理"""
        if not PILLOW_AVAILABLE:
            self.finished_signal.emit(False, "Pillow 库未安装，请运行: pip install Pillow")
            return
        
        total_files = len(self.file_list)
        if total_files == 0:
            self.finished_signal.emit(False, "没有要处理的文件")
            return
        
        self.progress_signal.emit(f"开始处理 {total_files} 个文件...")
        
        success_count = 0
        fail_count = 0
        skipped_count = 0
        total_saved = 0  # 总共节省的字节数
        
        for i, input_path in enumerate(self.file_list):
            if self._cancelled:
                self.progress_signal.emit("⚠️ 处理已取消")
                break
            
            # 计算进度
            progress = (i / total_files) * 100
            self.progress_percent_signal.emit(progress)
            
            # 获取文件名
            filename = os.path.basename(input_path)
            self.progress_signal.emit(f"📷 正在处理 ({i+1}/{total_files}): {filename}")
            
            # 获取原始文件大小
            original_size = os.path.getsize(input_path) if os.path.exists(input_path) else 0
            
            # 生成输出文件名
            output_format = self.options.get('format', 'JPEG')
            format_info = ImageProcessor.OUTPUT_FORMATS.get(output_format, {})
            ext = format_info.get('ext', '.jpg')
            
            base_name = os.path.splitext(filename)[0]
            suffix = self.options.get('suffix', '')
            output_filename = f"{base_name}{suffix}{ext}"
            output_path = os.path.join(self.output_dir, output_filename)
            
            # 处理文件已存在的情况
            if os.path.exists(output_path):
                overwrite = self.options.get('overwrite', False)
                if not overwrite:
                    skipped_count += 1
                    self.progress_signal.emit(f"  ⏭️ 跳过（文件已存在）: {output_filename}")
                    self.file_processed_signal.emit(filename, False, "文件已存在，已跳过", original_size, 0)
                    continue
            
            # 处理图片
            success, message, output_size = ImageProcessor.process_image(
                input_path, output_path, self.options
            )
            
            if success:
                success_count += 1
                saved = original_size - output_size
                total_saved += saved
                
                # 计算压缩比
                if original_size > 0:
                    ratio = (1 - output_size / original_size) * 100
                    size_info = f"({self._format_size(original_size)} → {self._format_size(output_size)}, {ratio:.1f}%)"
                else:
                    size_info = f"({self._format_size(output_size)})"
                
                self.progress_signal.emit(f"  ✅ 成功 {size_info}")
                self.file_processed_signal.emit(filename, True, message, original_size, output_size)
            else:
                fail_count += 1
                self.progress_signal.emit(f"  ❌ 失败: {message}")
                self.file_processed_signal.emit(filename, False, message, original_size, 0)
        
        # 完成处理
        self.progress_percent_signal.emit(100)
        
        if self._cancelled:
            summary = f"处理已取消。已完成 {success_count} 个文件，失败 {fail_count} 个，跳过 {skipped_count} 个"
            self.finished_signal.emit(False, summary)
        else:
            if total_saved > 0:
                saved_info = f"，总共节省 {self._format_size(total_saved)}"
            elif total_saved < 0:
                saved_info = f"，文件增大 {self._format_size(-total_saved)}"
            else:
                saved_info = ""
            
            skip_info = f"，跳过 {skipped_count} 个" if skipped_count > 0 else ""
            summary = f"处理完成！成功 {success_count} 个，失败 {fail_count} 个{skip_info}{saved_info}"
            self.progress_signal.emit(f"🎉 {summary}")
            self.finished_signal.emit((success_count > 0) or (skipped_count > 0 and fail_count == 0), summary)
    
    def stop(self):
        """停止处理"""
        self._cancelled = True
        self.progress_signal.emit("正在取消...")
        self.cancel_signal.emit()
    
    def _format_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


# 检查 Pillow 是否可用的辅助函数
def check_pillow():
    """检查 Pillow 是否可用"""
    return PILLOW_AVAILABLE


def install_pillow():
    """尝试安装 Pillow"""
    try:
        import subprocess
        result = subprocess.run(
            ['pip', 'install', 'Pillow'],
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)
