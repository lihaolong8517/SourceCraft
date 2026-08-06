import json
import ctypes
from collections import deque
from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication
from ..constants import RUNTIME_APP_DIR
from ..utils import FFMPEG_CMD
"""转码线程 - 基于 FFmpeg 的视频/音频转码工作线程"""
import sys
import os
import re
import subprocess
import traceback
import signal
import threading
import time
from PyQt5.QtCore import QThread, pyqtSignal
from i18n import t, t_format


class TranscodeThread(QThread):
    progress_signal = pyqtSignal(str)
    progress_percent_signal = pyqtSignal(float)  # 添加百分比进度信号
    finished_signal = pyqtSignal(bool, str)
    cancel_signal = pyqtSignal()  # 用于传递取消转码成功的信号
    
    def __init__(self, input_file, output_file, transcode_options, preset='faster', threads=0, use_gpu=False, gpu_type=None):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file
        self.transcode_options = transcode_options
        self.preset = self.map_chinese_preset_to_english(preset)  # 转码速度预设
        self.threads = threads  # 使用的线程数，0表示自动
        self.use_gpu = use_gpu  # 是否使用GPU加速
        self.gpu_type = gpu_type  # GPU类型：NVIDIA或AMD
        self.process = None
        self.progress_sent = False  # 跟踪是否已发送过进度信号
        self.last_progress_value = 0.0  # 记录上次进度值，确保从0%开始
        self.gpu_usage_monitor = None  # GPU使用率监控线程
        self.gpu_usage_stop_event = threading.Event()  # 用于停止GPU监控的事件
        self.gpu_usage = 0  # 当前GPU使用率
        self.gpu_encoder_active = False  # 标记GPU编码器是否真正被激活
        self.gpu_usage_history = []  # 用于记录GPU使用率历史
        self.max_gpu_usage = 0  # 记录最高GPU使用率
        self._creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._encoder_probe_cache = {}

    def _stop_gpu_monitor_thread(self):
        """停止GPU监控线程，避免回退CPU后仍持续探测。"""
        try:
            if self.gpu_usage_monitor and self.gpu_usage_monitor.is_alive():
                self.gpu_usage_stop_event.set()
                self.gpu_usage_monitor.join(2)
        except Exception as e:
            self.progress_signal.emit(t_format("停止GPU监控线程失败: {}", str(e)))
        finally:
            self.gpu_usage_monitor = None
            self.gpu_encoder_active = False

    def _fallback_to_cpu(self, reason):
        """统一处理GPU回退CPU逻辑。"""
        self.progress_signal.emit(reason)
        self.use_gpu = False
        self.gpu_type = None
        self._stop_gpu_monitor_thread()

    def _sanitize_command_for_cpu(self, cmd):
        """当回退CPU时，移除GPU专用参数并替换GPU编码器。"""
        if not cmd:
            return cmd

        gpu_args_with_value = {
            "-hwaccel", "-hwaccel_device", "-hwaccel_output_format", "-gpu", "-gpu_copy",
            "-extra_hw_frames", "-spatial-aq", "-temporal-aq", "-aq-strength", "-lookahead",
            "-no-scenecut", "-multipass", "-forced-idr", "-b_ref_mode", "-rc-lookahead",
            "-spatial_aq", "-look_ahead", "-global_quality", "-low_power", "-usage", "-rdo",
            "-max_frame_size", "-bitrate_limit", "-avbr_accuracy", "-avbr_convergence",
            "-rc", "-cq", "-qmin", "-qmax"
        }
        gpu_args_no_value = {"-cuda", "-qsv"}
        gpu_codec_map = {
            "h264_nvenc": "libx264",
            "hevc_nvenc": "libx265",
            "h264_amf": "libx264",
            "hevc_amf": "libx265",
        }
        nvenc_presets = {"p1", "p2", "p3", "p4", "p5", "p6", "p7"}
        gpu_tunes = {"ll", "ull", "hq", "uhq"}

        sanitized = []
        i = 0
        while i < len(cmd):
            token = cmd[i]
            token_lower = str(token).lower()
            next_token = cmd[i + 1] if i + 1 < len(cmd) else None
            next_lower = str(next_token).lower() if next_token is not None else ""

            if token_lower in gpu_args_with_value:
                i += 2
                continue
            if token_lower in gpu_args_no_value:
                i += 1
                continue

            if token_lower == "-preset" and next_token is not None and next_lower in nvenc_presets:
                sanitized.extend([token, "faster"])
                i += 2
                continue

            if token_lower == "-tune" and next_token is not None and next_lower in gpu_tunes:
                i += 2
                continue

            if token_lower == "-c:v" and next_token is not None:
                replacement = gpu_codec_map.get(next_lower)
                if replacement:
                    sanitized.extend([token, replacement])
                    i += 2
                    continue

            if token_lower in gpu_codec_map:
                sanitized.append(gpu_codec_map[token_lower])
                i += 1
                continue

            sanitized.append(token)
            i += 1

        has_video_codec = False
        for idx in range(len(sanitized) - 1):
            if str(sanitized[idx]).lower() == "-c:v":
                has_video_codec = True
                break

        if not has_video_codec:
            output_ext = os.path.splitext(self.output_file)[1].lower()
            if output_ext in [".hevc", ".h265"]:
                fallback_codec = "libx265"
            elif output_ext == ".webm":
                fallback_codec = "libvpx-vp9"
            else:
                fallback_codec = "libx264"

            if len(sanitized) >= 2:
                output_path = sanitized[-1]
                sanitized = sanitized[:-1] + ["-c:v", fallback_codec, output_path]
            else:
                sanitized = [FFMPEG_CMD, "-i", self.input_file, "-c:v", fallback_codec, self.output_file]

        return sanitized

    def _resolve_requested_video_codec(self, transcode_opts, output_ext):
        """根据用户选项解析目标视频编码类型，仅返回 h264/h265/vp9/av1/copy 之一。"""
        codec_aliases = {
            "libx264": "h264",
            "h264": "h264",
            "h264_nvenc": "h264",
            "h264_amf": "h264",
            "h264_qsv": "h264",
            "libx265": "h265",
            "hevc": "h265",
            "hevc_nvenc": "h265",
            "hevc_amf": "h265",
            "hevc_qsv": "h265",
            "h265": "h265",
            "libvpx-vp9": "vp9",
            "vp9": "vp9",
            "libaom-av1": "av1",
            "av1": "av1",
            "copy": "copy",
        }

        if transcode_opts:
            for idx in range(len(transcode_opts) - 1):
                if str(transcode_opts[idx]).lower() == "-c:v":
                    requested = str(transcode_opts[idx + 1]).lower()
                    normalized = codec_aliases.get(requested)
                    if normalized:
                        return normalized

        if output_ext in [".hevc", ".h265"]:
            return "h265"
        return "h264"

    def _test_ffmpeg_encoder(self, encoder_name):
        """统一测试 FFmpeg 编码器是否可用，并缓存结果避免重复探测。"""
        cache_key = str(encoder_name or "").strip().lower()
        if not cache_key:
            return False, "编码器名称为空"

        cached = self._encoder_probe_cache.get(cache_key)
        if cached is not None:
            return cached

        test_video_source = "color=c=black:s=1280x720:r=30"
        test_duration = "1"
        test_extra_args = []

        # NVENC 对过小分辨率更敏感，使用常规 720p 探测以避免误判“硬件不可用”。
        if "nvenc" in cache_key:
            test_extra_args.extend(["-pix_fmt", "nv12"])
        elif "amf" in cache_key:
            test_extra_args.extend(["-pix_fmt", "nv12"])

        test_cmd = [
            FFMPEG_CMD,
            "-hide_banner",
            "-f", "lavfi",
            "-i", test_video_source,
            "-t", test_duration,
            "-c:v", encoder_name,
        ] + test_extra_args + [
            "-f", "null",
            "-"
        ]
        try:
            test_process = subprocess.Popen(
                test_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=self._creationflags
            )
            _, test_err = test_process.communicate(timeout=8)
            error_text = (test_err or "").strip()
            success = test_process.returncode == 0 and "conversion failed" not in error_text.lower()
            result = (success, error_text[:400])
        except Exception as e:
            result = (False, str(e))

        self._encoder_probe_cache[cache_key] = result
        return result
    
    def map_chinese_preset_to_english(self, chinese_preset):
        """将中文转码速度选项映射到对应的英文FFmpeg参数"""
        preset_mapping = {
            "快速": "fast",
            "正常": "medium",
            "高质量": "slow"
        }
        return preset_mapping.get(chinese_preset, chinese_preset)  # 如果不是中文选项，直接返回原值
    
    def detect_gpu_type(self):
        """检测系统中可用的GPU类型，返回适合的GPU加速选项"""
        try:
            self.progress_signal.emit(t("正在检测系统GPU类型..."))
            # 使用更全面的GPU检测方法
            # 首先检查ffmpeg支持的编码器
            encoders_cmd = [FFMPEG_CMD, "-hide_banner", "-encoders"]
            encoders_process = subprocess.Popen(
                encoders_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            encoders_output, _ = encoders_process.communicate()
            
            # 检查硬件加速支持
            hwaccel_cmd = [FFMPEG_CMD, "-hide_banner", "-hwaccels"]
            hwaccel_process = subprocess.Popen(
                hwaccel_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            hwaccel_output, _ = hwaccel_process.communicate()
            
            # 记录检测到的编码器和硬件加速信息
            self.progress_signal.emit(t_format("检测到的硬件加速选项: {}", hwaccel_output.strip()))
            self.progress_signal.emit(t_format("检测到的编码器信息: {}...", encoders_output[:200]))
            
            # 检查NVIDIA GPU支持
            has_cuda = "cuda" in hwaccel_output.lower()
            has_nvenc = "h264_nvenc" in encoders_output or "hevc_nvenc" in encoders_output
            
            # 检查AMD GPU支持
            has_amf = "h264_amf" in encoders_output or "hevc_amf" in encoders_output
            has_d3d11va = "d3d11va" in hwaccel_output.lower()
            
            # 尝试获取更详细的GPU信息
            gpu_info = "未知"
            try:
                # 在Windows上使用PowerShell获取GPU信息
                gpu_cmd = ["powershell", "-Command", "Get-WmiObject Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion | Format-List"]
                gpu_process = subprocess.Popen(
                    gpu_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                gpu_output, _ = gpu_process.communicate()
                if gpu_output.strip():
                    gpu_info = gpu_output.strip()
                    self.progress_signal.emit(t_format("系统GPU信息:\n{}", gpu_info))
            except Exception as gpu_err:
                self.progress_signal.emit(t_format("获取GPU详细信息失败: {}", str(gpu_err)))
            
            # 验证GPU编码器是否真正可用
            # 测试NVIDIA编码器
            nvenc_available = False
            if has_nvenc:
                nvenc_available, nvenc_error = self._test_ffmpeg_encoder("h264_nvenc")
                self.progress_signal.emit(t_format("NVENC编码器测试结果: {}", '可用' if nvenc_available else '不可用'))
                if not nvenc_available and nvenc_error:
                    self.progress_signal.emit(t_format("NVENC编码器测试详情: {}", nvenc_error))
            
            # 测试AMD编码器
            amf_available = False
            if has_amf:
                amf_available, amf_error = self._test_ffmpeg_encoder("h264_amf")
                self.progress_signal.emit(t_format("AMF编码器测试结果: {}", '可用' if amf_available else '不可用'))
                if not amf_available and amf_error:
                    self.progress_signal.emit(t_format("AMF编码器测试详情: {}", amf_error))
            
            # 根据检测结果确定GPU类型
            if nvenc_available and (has_cuda or "nvidia" in gpu_info.lower()):
                self.progress_signal.emit(t("检测到NVIDIA GPU，支持NVENC硬件加速编码，并已验证可用"))
                return "NVIDIA (NVENC)"
            elif amf_available and (has_d3d11va or "amd" in gpu_info.lower() or "radeon" in gpu_info.lower()):
                self.progress_signal.emit(t("检测到AMD GPU，支持AMF硬件加速编码，并已验证可用"))
                return "AMD (AMF)"
            
            # 如果没有明确检测到支持的GPU，但有硬件加速选项，尝试自动选择
            if has_nvenc and "cuda" in hwaccel_output.lower():
                self.progress_signal.emit(t("检测到CUDA/NVENC支持，但未验证可用性，将尝试使用NVIDIA硬件加速"))
                return "NVIDIA (NVENC)"
            elif has_amf:
                self.progress_signal.emit(t("检测到AMF支持，但未验证可用性，将尝试使用AMD硬件加速"))
                return "AMD (AMF)"
            
            # 如果没有检测到支持的GPU，返回None
            self.progress_signal.emit(t("未检测到支持的GPU硬件加速编码器，将使用CPU编码"))
            return None
        except Exception as e:
            self.progress_signal.emit(t_format("GPU检测出错: {}，将使用CPU编码", str(e)))
            import traceback
            self.progress_signal.emit(traceback.format_exc())
            return None
        
    def _monitor_gpu_usage(self):
        """监控GPU使用率的线程函数"""
        try:
            # 记录GPU监控开始
            self.progress_signal.emit(t_format("开始监控GPU使用率: {}", self.gpu_type))
            self.gpu_usage_history = []  # 重置GPU使用率历史记录
            self.max_gpu_usage = 0  # 重置最高GPU使用率
            encoder_detected = False  # 用于标记是否检测到编码器活动
            check_count = 0  # 检查次数计数
            
            # 根据GPU类型选择不同的监控命令
            if "NVIDIA" in self.gpu_type:
                # 使用nvidia-smi监控NVIDIA GPU使用率
                while not self.gpu_usage_stop_event.is_set():
                    try:
                        # 使用nvidia-smi获取GPU使用率和编码器使用情况
                        usage_cmd = ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"]
                        usage_process = subprocess.Popen(
                            usage_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            creationflags=self._creationflags
                        )
                        usage_output, _ = usage_process.communicate()
                        
                        # 获取编码器使用情况
                        encoder_cmd = ["nvidia-smi", "--query-gpu=encoder.stats.sessionCount", "--format=csv,noheader,nounits"]
                        encoder_process = subprocess.Popen(
                            encoder_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            creationflags=self._creationflags
                        )
                        encoder_output, _ = encoder_process.communicate()
                        
                        # 获取GPU进程信息
                        process_cmd = ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader"]
                        process_process = subprocess.Popen(
                            process_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            creationflags=self._creationflags
                        )
                        process_output, _ = process_process.communicate()
                        
                        encoder_active = False
                        ffmpeg_found = False
                        avg_usage = 0.0
                        if usage_output.strip():
                            self.gpu_usage = float(usage_output.strip())
                            self.gpu_usage_history.append(self.gpu_usage)
                            # 只保留最近10次的记录
                            if len(self.gpu_usage_history) > 10:
                                self.gpu_usage_history.pop(0)
                            
                            # 更新最高GPU使用率
                            if self.gpu_usage > self.max_gpu_usage:
                                self.max_gpu_usage = self.gpu_usage
                                self.progress_signal.emit(t_format("检测到新的最高GPU使用率: {:.1f}%", self.max_gpu_usage))
                            
                            # 计算平均GPU使用率
                            avg_usage = sum(self.gpu_usage_history) / len(self.gpu_usage_history)
                            
                            # 检查编码器会话数
                            if encoder_output.strip() and int(encoder_output.strip()) > 0:
                                encoder_active = True
                                encoder_detected = True
                                self.gpu_encoder_active = True
                                self.progress_signal.emit(t_format("检测到NVIDIA编码器活动: {}个会话", encoder_output.strip()))
                            
                            # 检查ffmpeg是否在GPU进程列表中
                            if process_output.strip():
                                for line in process_output.strip().split('\n'):
                                    if 'ffmpeg' in line.lower():
                                        ffmpeg_found = True
                                        self.progress_signal.emit(t_format("在GPU进程列表中找到ffmpeg: {}", line))
                                        self.gpu_encoder_active = True
                                        encoder_detected = True
                            
                            # 仅在检测到编码器会话或ffmpeg GPU进程时，判定为GPU编码器激活，避免误报
                        if encoder_active or ffmpeg_found:
                            self.gpu_encoder_active = True
                            # 记录GPU编码器激活状态
                            if not encoder_detected:
                                self.progress_signal.emit(t_format("GPU编码器已激活! GPU使用率: {:.1f}%", self.gpu_usage))
                                encoder_detected = True
                            
                            # 输出详细的GPU使用信息
                            status_msg = t_format("NVIDIA GPU使用率: {:.1f}% (平均: {:.1f}%)", self.gpu_usage, avg_usage)
                            if encoder_active:
                                status_msg += t_format(", 编码器活动: {}个会话", encoder_output.strip())
                            if ffmpeg_found:
                                status_msg += ", ffmpeg在GPU进程列表中"
                            
                            self.progress_signal.emit(status_msg)
                            
                            # 每3次检查输出一次详细的GPU状态总结
                            check_count += 1
                            if check_count % 3 == 0:
                                # 计算GPU性能指标
                                efficiency_score = min(avg_usage / 10.0, 10.0)  # GPU效率评分(0-10)
                                
                                if self.gpu_encoder_active:
                                    status_msg = t("NVIDIA GPU加速状态: ✅ 已激活")
                                    status_msg += t_format(" | 平均使用率: {:.1f}% | 最高: {:.1f}%", avg_usage, self.max_gpu_usage)
                                    status_msg += t_format(" | 效率评分: {:.1f}/10", efficiency_score)
                                    self.progress_signal.emit(status_msg)
                                    
                                    if encoder_active:
                                        self.progress_signal.emit(t_format("🛠️ 活跃NVENC编码器: {}个会话", encoder_output.strip()))
                                    
                                    # 性能建议
                                    if avg_usage < 5:
                                        self.progress_signal.emit(t("⚠️ 建议: GPU使用率较低，考虑检查NVENC参数"))
                                    elif avg_usage > 80:
                                        self.progress_signal.emit(t("🔥 优秀: NVIDIA GPU利用率非常高！"))
                                else:
                                    self.progress_signal.emit(t_format("NVIDIA GPU加速状态: ❌ 未激活 (平均使用率: {:.1f}%)", avg_usage))
                                    if check_count > 10:  # 10次检查后仍未激活
                                        self.progress_signal.emit(t("⚠️ 警告: NVENC编码器可能未正常工作"))
                    except Exception as e:
                        self.progress_signal.emit(t_format("监控NVIDIA GPU使用率出错: {}", str(e)))
                    # 每2秒更新一次
                    self.gpu_usage_stop_event.wait(2)
            elif "AMD" in self.gpu_type:
                # 使用简化的AMD GPU监控方法
                self.progress_signal.emit(t("启动AMD GPU使用率监控..."))
                encoder_detected = False
                check_count = 0
                ffmpeg_process_detected = False
                self.gpu_usage = 0.0
                
                while not self.gpu_usage_stop_event.is_set():
                    try:
                        # 方法1: 检查ffmpeg进程是否在运行并使用AMF编码器
                        cmd_ffmpeg = ["powershell", "-Command", 
                                    "Get-Process | Where-Object {$_.Name -like '*ffmpeg*'} | "
                                    "Select-Object Name,CPU,WorkingSet,CommandLine"]
                        process_ffmpeg = subprocess.Popen(
                            cmd_ffmpeg,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            creationflags=self._creationflags
                        )
                        output_ffmpeg, _ = process_ffmpeg.communicate()
                        ffmpeg_running = bool(output_ffmpeg and "ffmpeg" in output_ffmpeg.lower())
                        
                        # 检查是否有ffmpeg进程在运行AMF编码
                        if ffmpeg_running:
                            if not ffmpeg_process_detected:
                                self.progress_signal.emit(t("检测到ffmpeg进程正在运行"))
                                ffmpeg_process_detected = True
                            
                        # 方法2: 使用改进的AMD GPU使用率检测
                        try:
                            # 使用更精确的AMD GPU监控命令
                            cmd_gpu = ["powershell", "-Command", 
                                     "Get-Counter -Counter '\\GPU Engine(pid_*_luid_*_phys_*_eng_*_engtype_VideoEncode)\\Utilization Percentage','\\GPU Engine(pid_*_luid_*_phys_*_eng_*_engtype_3D)\\Utilization Percentage' -ErrorAction SilentlyContinue | "
                                     "Select-Object -ExpandProperty CounterSamples | "
                                     "Where-Object {$_.CookedValue -gt 0.1} | "
                                     "Measure-Object -Property CookedValue -Sum | "
                                     "Select-Object -ExpandProperty Sum"]
                            
                            process_gpu = subprocess.Popen(
                                cmd_gpu,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                creationflags=self._creationflags
                            )
                            output_gpu, _ = process_gpu.communicate()
                            
                            if output_gpu.strip():
                                try:
                                    gpu_usage_value = float(output_gpu.strip())
                                    if gpu_usage_value > 0.5:  # 降低阈值以提高检测灵敏度
                                        self.gpu_usage = min(gpu_usage_value, 100.0)  # 限制最大值为100%
                                        self.gpu_encoder_active = True
                                        encoder_detected = True
                                        self.progress_signal.emit(t_format("AMD GPU活动检测: {:.1f}%", self.gpu_usage))
                                except ValueError:
                                    pass
                            
                            # 备用检测方法：检测AMD特定的GPU引擎
                            if not encoder_detected:
                                cmd_amd = ["powershell", "-Command", 
                                         "Get-Counter -Counter '\\GPU Engine(*)\\Utilization Percentage' -ErrorAction SilentlyContinue | "
                                         "Select-Object -ExpandProperty CounterSamples | "
                                         "Where-Object {$_.InstanceName -like '*AMD*' -or $_.InstanceName -like '*Radeon*'} | "
                                         "Where-Object {$_.CookedValue -gt 0.1} | "
                                         "Measure-Object -Property CookedValue -Average | "
                                         "Select-Object -ExpandProperty Average"]
                                
                                process_amd = subprocess.Popen(
                                    cmd_amd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True,
                                    creationflags=self._creationflags
                                )
                                output_amd, _ = process_amd.communicate()
                                
                                if output_amd.strip():
                                    try:
                                        amd_usage = float(output_amd.strip())
                                        if amd_usage > 0.5:
                                            self.gpu_usage = min(amd_usage, 100.0)
                                            self.gpu_encoder_active = True
                                            encoder_detected = True
                                            self.progress_signal.emit(t_format("AMD GPU备用检测: {:.1f}%", self.gpu_usage))
                                    except ValueError:
                                        pass
                        except Exception as e:
                            self.progress_signal.emit(t_format("AMD GPU监控错误: {}", str(e)))
                        
                        # 获取当前GPU使用率
                        current_usage = self.gpu_usage
                        
                        # 记录GPU使用率历史
                        self.gpu_usage_history.append(current_usage)
                        # 只保留最近10次的记录
                        if len(self.gpu_usage_history) > 10:
                            self.gpu_usage_history.pop(0)
                        
                        # 更新最高GPU使用率
                        if current_usage > self.max_gpu_usage:
                            self.max_gpu_usage = current_usage
                            self.progress_signal.emit(t_format("检测到新的最高AMD GPU使用率: {:.1f}%", self.max_gpu_usage))
                        
                        # 计算平均GPU使用率
                        avg_usage = sum(self.gpu_usage_history) / len(self.gpu_usage_history) if self.gpu_usage_history else 0

                        # 分析详细的GPU引擎使用情况
                        gpu_engines_active = []
                        if current_usage > 0.5:
                            gpu_engines_active.append("VideoEncode/3D")
                        
                        # 如果GPU使用率大于5%或检测到编码器活动，认为GPU编码器已激活
                        if current_usage > 5 or (ffmpeg_running and current_usage > 0.5):
                            self.gpu_encoder_active = True
                            encoder_detected = True
                        
                        # 输出详细的GPU使用信息
                        status_msg = t_format("AMD GPU使用率: {:.1f}% (平均: {:.1f}%)", current_usage, avg_usage)
                        if ffmpeg_running:
                            status_msg += ", ffmpeg进程正在运行"
                        if gpu_engines_active:
                            status_msg += t_format(", 活跃引擎: {}", len(gpu_engines_active))
                        
                        self.progress_signal.emit(status_msg)
                        
                        # 每3次检查输出一次详细的GPU状态总结
                        check_count += 1
                        if check_count % 3 == 0:
                            # 计算GPU性能指标
                            efficiency_score = min(avg_usage / 10.0, 10.0)  # GPU效率评分(0-10)
                            
                            if self.gpu_encoder_active:
                                status_msg = t("AMD GPU加速状态: ✅ 已激活")
                                status_msg += t_format(" | 平均使用率: {:.1f}% | 最高: {:.1f}%", avg_usage, self.max_gpu_usage)
                                status_msg += t_format(" | 效率评分: {:.1f}/10", efficiency_score)
                                self.progress_signal.emit(status_msg)
                                
                                if gpu_engines_active:
                                    self.progress_signal.emit(t_format("🛠️ 活跃GPU引擎: {}", ', '.join(gpu_engines_active[:3])))
                                
                                # 性能建议
                                if avg_usage < 5:
                                    self.progress_signal.emit(t("⚠️ 建议: GPU使用率较住，考虑检查编码参数"))
                                elif avg_usage > 80:
                                    self.progress_signal.emit(t("🔥 优秀: GPU利用率非常高！"))
                            else:
                                self.progress_signal.emit(t_format("AMD GPU加速状态: ❌ 未激活 (平均使用率: {:.1f}%)", avg_usage))
                                if check_count > 10:  # 10次检查后仍未激活
                                    self.progress_signal.emit(t("⚠️ 警告: GPU编码器可能未正常工作"))
                    except Exception as e:
                        self.progress_signal.emit(t_format("监控AMD GPU使用率出错: {}", str(e)))
                        import traceback
                        self.progress_signal.emit(traceback.format_exc())
                    # 每1秒更新一次，提高监控频率
                    self.gpu_usage_stop_event.wait(1)
            else:
                # 通用GPU监控方法
                while not self.gpu_usage_stop_event.is_set():
                    try:
                        # 使用PowerShell获取通用GPU使用率
                        cmd = ["powershell", "-Command", "Get-Counter -Counter \"\\GPU Engine(*)\\Utilization Percentage\" | Select-Object -ExpandProperty CounterSamples | Measure-Object -Property CookedValue -Average | Select-Object -ExpandProperty Average"]
                        process = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            creationflags=self._creationflags
                        )
                        output, _ = process.communicate()
                        if output.strip():
                            self.gpu_usage = float(output.strip())
                            if self.gpu_usage > 5:  # 如果GPU使用率大于5%，认为GPU编码器已激活
                                self.gpu_encoder_active = True
                            self.progress_signal.emit(t_format("GPU使用率: {:.1f}%", self.gpu_usage))
                    except Exception as e:
                        self.progress_signal.emit(t_format("监控GPU使用率出错: {}", str(e)))
                    # 每2秒更新一次
                    self.gpu_usage_stop_event.wait(2)
        except Exception as e:
            self.progress_signal.emit(t_format("GPU监控线程出错: {}", str(e)))
    
    def run(self):
        try:
            # 检查输入文件格式
            input_ext = os.path.splitext(self.input_file)[1].lower()
            output_ext = os.path.splitext(self.output_file)[1].lower()

            if self.use_gpu and (not self.gpu_type or self.gpu_type == "自动检测"):
                self.progress_signal.emit(t("正在自动检测可用的 GPU 硬件加速类型..."))
                self.gpu_type = self.detect_gpu_type()
                if self.gpu_type:
                    self.progress_signal.emit(t_format("自动检测到 GPU 类型: {}", self.gpu_type))
                else:
                    self.progress_signal.emit(t("未检测到可用的 GPU 硬件编码器，自动回退到 CPU 编码"))
                    self.use_gpu = False
             
            # 记录转码开始信息
            self.progress_signal.emit(t_format("开始转码: {} -> {}", os.path.basename(self.input_file), os.path.basename(self.output_file)))
            self.progress_signal.emit(t_format("使用GPU加速: {}", '是 - ' + self.gpu_type if self.use_gpu else '否 - 使用CPU编码'))
            
            # 构建ffmpeg命令的基础部分
            # 注意：我们不再在这里构建完整命令，而是根据是否使用GPU加速来决定命令构建方式
            
            # 准备转码选项
            transcode_opts = []
            if self.transcode_options:
                # 如果transcode_options是字典，转换为命令行参数
                if isinstance(self.transcode_options, dict):
                    for key, value in self.transcode_options.items():
                        if key == 'video_codec':
                            transcode_opts.extend(["-c:v", value])
                        elif key == 'audio_codec':
                            transcode_opts.extend(["-c:a", value])
                        elif key == 'video_bitrate':
                            transcode_opts.extend(["-b:v", value])
                        elif key == 'audio_bitrate':
                            transcode_opts.extend(["-b:a", value])
                        else:
                            # 其他参数直接添加
                            transcode_opts.extend([f"-{key}", str(value)])
                else:
                    # 如果是列表，直接扩展
                    transcode_opts.extend(self.transcode_options)

            requested_video_codec = self._resolve_requested_video_codec(transcode_opts, output_ext)

            def append_filtered_transcode_opts(target_cmd, source_opts, skip_params):
                """合并转码参数，同时保留单参数flag（如 -y）并过滤冲突参数。"""
                if not source_opts:
                    return
                i = 0
                while i < len(source_opts):
                    opt = source_opts[i]
                    if not isinstance(opt, str) or not opt.startswith("-"):
                        i += 1
                        continue

                    next_token = source_opts[i + 1] if i + 1 < len(source_opts) else None
                    has_value = (i + 1 < len(source_opts)) and (
                        not isinstance(next_token, str) or not next_token.startswith("-")
                    )

                    if opt in skip_params:
                        i += 2 if has_value else 1
                        continue

                    if has_value:
                        target_cmd.extend([opt, str(next_token)])
                        i += 2
                    else:
                        target_cmd.append(opt)
                        i += 1
            
            # 特殊处理webm格式的额外参数
            webm_extra_opts = []
            if input_ext == '.webm':
                self.progress_signal.emit(t("检测到webm格式文件，应用特殊处理..."))
                
                # 确保视频编码器设置正确
                if "-c:v" not in ' '.join(transcode_opts):
                    # 根据输出格式选择合适的编码器
                    if output_ext in ['.mp4', '.mkv']:
                        webm_extra_opts.extend(["-c:v", "libx264", "-crf", "23"])
                    elif output_ext == '.webm':
                        webm_extra_opts.extend(["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0"])
                
                # 确保音频编码器设置正确
                if "-c:a" not in ' '.join(transcode_opts):
                    if output_ext in ['.mp4', '.mkv']:
                        webm_extra_opts.extend(["-c:a", "aac", "-b:a", "192k"])
                    elif output_ext == '.webm':
                        webm_extra_opts.extend(["-c:a", "aac", "-b:a", "192k"])
                
                # 添加额外参数以确保转码成功
                webm_extra_opts.extend(["-pix_fmt", "yuv420p", "-max_muxing_queue_size", "9999"])
                
                # 添加更多参数以提高转码效率和兼容性
                if "-preset" not in ' '.join(transcode_opts) and output_ext in ['.mp4', '.mkv']:
                    webm_extra_opts.extend(["-preset", self.preset])
                
                # 添加更多参数以确保进度能够正确显示
                webm_extra_opts.extend(["-progress", "pipe:1"])
                
                # 为webm格式添加特殊参数，确保进度能够正确显示
                # 使用-stats参数强制输出更详细的统计信息
                webm_extra_opts.extend(["-stats_period", "0.5"])
                
                # 添加更多参数以提高webm转码的稳定性
                if "-threads" not in ' '.join(transcode_opts):
                    # 限制线程数，避免过高的CPU使用率导致进程不稳定
                    webm_extra_opts.extend(["-threads", "5"])
                
                # 添加-y参数以自动覆盖输出文件，避免转码中断
                if "-y" not in ' '.join(transcode_opts):
                    webm_extra_opts.extend(["-y"])
                
                # 再次确保pix_fmt参数正确设置，这对于某些webm文件至关重要
                if "-pix_fmt" not in ' '.join(transcode_opts):
                    webm_extra_opts.extend(["-pix_fmt", "yuv420p"])
                
                # 添加预设参数，即使不是输出到mp4/mkv，也可以提高转码效率
                if "-preset" not in ' '.join(transcode_opts):
                    webm_extra_opts.extend(["-preset", "faster"])
                
                # 添加详细日志输出，便于调试
                webm_extra_opts.extend(["-v", "info"])
            
            # 启动GPU使用率监控线程
            if self.use_gpu and self.gpu_type:
                self.progress_signal.emit(t("正在检测系统GPU类型..."))
                self.progress_signal.emit(t_format("启动GPU使用率监控，类型: {}", self.gpu_type))
                # 确保在启动新的监控线程前停止旧的监控线程
                if hasattr(self, 'gpu_usage_monitor') and self.gpu_usage_monitor and self.gpu_usage_monitor.is_alive():
                    self.progress_signal.emit(t("停止旧的GPU监控线程..."))
                    self.gpu_usage_stop_event.set()
                    self.gpu_usage_monitor.join(1)
                
                # 重置监控状态
                self.gpu_usage_stop_event.clear()
                self.gpu_usage_history = []  # 清空GPU使用率历史
                self.max_gpu_usage = 0       # 重置最高GPU使用率
                self.gpu_encoder_active = False  # 重置GPU编码器激活状态
                
                # 启动新的监控线程
                self.gpu_usage_monitor = threading.Thread(target=self._monitor_gpu_usage)
                self.gpu_usage_monitor.daemon = True
                self.gpu_usage_monitor.start()
                self.progress_signal.emit(t("GPU监控线程已启动，将实时监测GPU使用率"))
                
                # 短暂延迟，确保监控线程有足够时间初始化
                time.sleep(0.5)
            
            # 简化命令构建逻辑，避免混乱
            # 首先创建基本命令
            cmd = [FFMPEG_CMD]
            
            # 检查是否已经指定了视频编码器（在所有分支之前定义）
            has_video_encoder = any(opt.startswith("-c:v") for opt in transcode_opts) or any(opt == "-c:v" for i, opt in enumerate(transcode_opts) if i < len(transcode_opts) - 1)
            
            # 如果使用GPU加速，添加相应的硬件加速参数（在输入文件之前）
            if self.use_gpu and self.gpu_type:
                self.progress_signal.emit(t_format("正在使用{}硬件加速...", self.gpu_type))
                
                # 根据GPU类型添加硬件加速初始化参数
                if "AMD" in self.gpu_type:
                    cmd.extend(["-hwaccel", "auto"])  # 使用自动硬件加速检测，避免兼容性问题
                elif "Intel" in self.gpu_type:
                    cmd.extend(["-hwaccel", "qsv"])
                    cmd.extend(["-hwaccel_device", "0"])
            
            # 添加输入文件
            cmd.extend(["-i", self.input_file])
            
            # 添加转码选项
            if transcode_opts:
                cmd.extend(transcode_opts)
            
            # 添加webm特殊处理参数
            if webm_extra_opts:
                cmd.extend(webm_extra_opts)
                
            skip_common_params = False

            # 添加GPU加速相关参数 - 全面优化以充分利用GPU性能
            if self.use_gpu and self.gpu_type:
                # 根据GPU类型添加特定的硬件加速参数
                if self.gpu_type == "NVIDIA (NVENC)":
                    self.progress_signal.emit(t("尝试使用NVIDIA GPU硬件加速编码..."))
                    
                    # 首先测试NVIDIA GPU是否可用
                    try:
                        nvenc_available, nvenc_error = self._test_ffmpeg_encoder("h264_nvenc")
                        if not nvenc_available:
                            reason = "NVIDIA GPU不可用，回退到CPU编码模式"
                            if nvenc_error:
                                reason += t_format("。FFmpeg反馈: {}", nvenc_error)
                            self._fallback_to_cpu(reason)
                        else:
                            self.progress_signal.emit(t("NVIDIA GPU可用，启用硬件加速编码"))
                    except Exception as e:
                        self._fallback_to_cpu(t_format("NVIDIA GPU测试失败: {}，回退到CPU编码模式", str(e)))
                    
                    if self.use_gpu and self.gpu_type == "NVIDIA (NVENC)":
                         # 完全重构命令，确保硬件加速参数在正确的位置
                         # 创建一个全新的命令列表，确保参数顺序正确
                         new_cmd = [FFMPEG_CMD]
                         
                         # 确保cmd_prefix在NVIDIA GPU加速部分也被正确初始化
                         cmd_prefix = [FFMPEG_CMD]
                         
                         # 1. NVIDIA 使用 CPU 解码 + NVENC 编码，避免部分机器在 CUDA/NVDEC 初始化阶段直接失败
                         new_cmd.extend(["-thread_queue_size", "4096"])
                         
                         # 2. 添加输入文件
                         new_cmd.extend(["-i", self.input_file])
                         
                         # 3. 添加 NVENC 编码器参数（必须在输入文件之后）
                         if requested_video_codec == "h265":
                             self.progress_signal.emit(t("使用hevc_nvenc编码器"))
                             new_cmd.extend(["-c:v", "hevc_nvenc"])
                         else:
                             self.progress_signal.emit(t("使用h264_nvenc编码器"))
                             new_cmd.extend(["-c:v", "h264_nvenc"])
                    
                         # 4. 添加兼容性优先的 NVENC 参数，避免不同代际显卡/驱动因高级选项直接退出
                         self.progress_signal.emit(t("配置NVENC优化参数（兼容优先模式）..."))
                         new_cmd.extend(["-preset", "p4"])
                         new_cmd.extend(["-rc", "vbr"])
                         new_cmd.extend(["-cq", "19"])
                         new_cmd.extend(["-b:v", "6M"])
                         new_cmd.extend(["-maxrate", "12M"])
                         new_cmd.extend(["-bufsize", "12M"])
                         new_cmd.extend(["-pix_fmt", "yuv420p"])
                         new_cmd.extend(["-spatial-aq", "1"])
                         new_cmd.extend(["-aq-strength", "8"])
                         
                         # 5. 添加原始转码选项中的其他参数（除了已经添加的编码器参数）
                         # 从原始转码选项中提取非编码器参数
                         if transcode_opts:
                              skip_params = {
                                  "-c:v", "-preset", "-tune", "-rc", "-cq", "-qmin", "-qmax",
                                  "-b:v", "-maxrate", "-maxrate:v", "-bufsize",
                                  "-hwaccel", "-hwaccel_output_format", "-hwaccel_device", "-thread_queue_size",
                                  "-extra_hw_frames", "-spatial-aq", "-temporal-aq", "-aq-strength",
                                  "-lookahead", "-no-scenecut", "-gpu", "-bf", "-b_ref_mode",
                                  "-multipass", "-forced-idr", "-pix_fmt"
                              }
                              append_filtered_transcode_opts(new_cmd, transcode_opts, skip_params)
                         
                         # 使用新构建的命令替换原命令，输出文件统一在后续公共收尾阶段追加
                         cmd = new_cmd
                         self.progress_signal.emit(t_format("已重构NVENC命令，确保GPU资源被充分利用: {}", ' '.join(cmd)))
                         
                         # 记录NVENC参数设置
                         self.progress_signal.emit(t("已添加NVENC优化参数，强制使用GPU编码器并优化性能设置"))
                         self.progress_signal.emit(t("已添加高级NVENC质量优化参数，确保GPU资源被充分利用"))
                    
                elif self.gpu_type == "AMD (AMF)":
                    self.progress_signal.emit(t("尝试使用AMD GPU硬件加速编码..."))

                    if self.use_gpu and self.gpu_type == "AMD (AMF)":
                        # 完全重构AMD GPU命令，使用最简单有效的配置
                        self.progress_signal.emit(t("配置AMD GPU加速参数（优化模式）..."))
                        
                        # 创建全新的命令，确保参数顺序正确
                        new_cmd = [FFMPEG_CMD]
                        
                        # 1. 添加输入文件（不使用复杂的硬件加速初始化）
                        new_cmd.extend(["-i", self.input_file])
                        
                        # 2. 根据用户选择保留AMF编码器类型（h264/hevc）
                        selected_amf_codec = "h264_amf"
                        if requested_video_codec == "h265":
                            selected_amf_codec = "hevc_amf"

                        amf_available, amf_error = self._test_ffmpeg_encoder(selected_amf_codec)
                        if not amf_available:
                            reason = t_format("AMD GPU不可用，{} 编码器测试失败，回退到CPU编码模式", selected_amf_codec)
                            if amf_error:
                                reason += t_format("。FFmpeg反馈: {}", amf_error)
                            self._fallback_to_cpu(reason)
                        else:
                            self.progress_signal.emit(t_format("AMD GPU可用，启用{}硬件加速编码", selected_amf_codec))
                            self.gpu_encoder_active = True

                    if self.use_gpu and self.gpu_type == "AMD (AMF)":
                        new_cmd.extend(["-c:v", selected_amf_codec])
                        self.progress_signal.emit(t_format("强制使用{}编码器", selected_amf_codec))
                        
                        # 3. 添加保守且兼容性更高的 AMF 参数
                        new_cmd.extend(["-quality", "balanced"])
                        new_cmd.extend(["-usage", "transcoding"])
                        new_cmd.extend(["-rc", "vbr_peak"])
                        new_cmd.extend(["-b:v", "4M"])
                        new_cmd.extend(["-maxrate", "8M"])
                        new_cmd.extend(["-bufsize", "12M"])
                        
                        # 4. 添加音频编码（如果需要）
                        if "-c:a" not in ' '.join(transcode_opts) if transcode_opts else True:
                            new_cmd.extend(["-c:a", "aac", "-b:a", "128k"])
                        
                        # 5. 添加其他转码选项（排除冲突参数）
                        if transcode_opts:
                            skip_params = {
                                "-c:v", "-quality", "-rc", "-b:v", "-maxrate", "-bufsize", "-usage",
                                "-profile:v", "-level", "-hwaccel", "-hwaccel_output_format",
                                "-hwaccel_device", "-thread_queue_size"
                            }
                            append_filtered_transcode_opts(new_cmd, transcode_opts, skip_params)
                        
                        # 6. 添加输出文件
                        new_cmd.append(self.output_file)
                        
                        # 使用新构建的命令
                        cmd = new_cmd
                        self.progress_signal.emit(t_format("AMD GPU优化配置完成: {}...", ' '.join(cmd)[:100]))
                        self.progress_signal.emit(t("使用优化的AMD AMF参数，确保GPU硬件加速真正激活"))
                        
                        # AMD GPU命令已完整构建，跳过后续通用参数添加
                        skip_common_params = True
                    
                # 添加通用GPU加速优化参数（只在未跳过的情况下）
                if not skip_common_params:
                    cmd.extend(["-max_muxing_queue_size", "1024"])  # 增加复用队列大小
                    cmd.extend(["-vsync", "0"])  # 禁用视频同步
                    cmd.extend(["-async", "1"])  # 启用音频异步处理
                
                # 添加音频编码优化（只在未跳过的情况下）
                if not skip_common_params:
                    if "-c:a" not in ' '.join(cmd):
                        cmd.extend(["-c:a", "aac", "-b:a", "192k"])  # 使用AAC音频编码，192kbps比特率
                    
                    # 添加更多通用优化参数
                    cmd.extend(["-movflags", "+faststart"])  # 优化MP4文件结构，提高流媒体播放性能
                
            else:
                # CPU编码模式 - 添加速度预设（如果使用x264/x265编码器）
                if any(codec in ' '.join(cmd) for codec in ["libx264", "h264", "libx265", "h265"]):
                    # 使用更快的预设，提高转码速度
                    cmd.extend(["-preset", "faster"])
                    # 添加CPU优化参数
                    cmd.extend(["-tune", "film"])  # 优化电影内容
                    cmd.extend(["-crf", "23"])  # 平衡质量和速度
                    # 添加多线程优化
                    cmd.extend(["-threads", "0"])  # 使用所有可用线程
                    
                # 如果没有指定视频编码器，自动添加CPU编码器
                if not has_video_encoder:
                    # 根据输出文件格式选择合适的编码器
                    output_ext = os.path.splitext(self.output_file)[1].lower()
                    if output_ext == '.mp4' or output_ext == '.mkv':
                        self.progress_signal.emit(t("自动选择libx264编码器"))
                        cmd.extend(["-c:v", "libx264", "-preset", "faster", "-crf", "23"])
                    elif output_ext == '.hevc' or output_ext == '.h265':
                        self.progress_signal.emit(t("自动选择libx265编码器"))
                        cmd.extend(["-c:v", "libx265", "-preset", "faster", "-crf", "28"])
                    elif output_ext == '.webm':
                        self.progress_signal.emit(t("自动选择libvpx-vp9编码器"))
                        cmd.extend(["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0"])
                    else:
                        # 默认使用libx264
                        self.progress_signal.emit(t("默认使用libx264编码器"))
                        cmd.extend(["-c:v", "libx264", "-preset", "faster", "-crf", "23"])
                
                # 添加音频编码优化
                if "-c:a" not in ' '.join(cmd):
                    cmd.extend(["-c:a", "aac", "-b:a", "192k"])  # 使用AAC音频编码，192kbps比特率
            
            # 添加通用参数（只在未跳过的情况下）
            if not skip_common_params:
                # 添加线程数控制
                if self.threads > 0:
                    cmd.extend(["-threads", str(self.threads)])
                
                # 优化输入/输出缓冲区设置 - 显著增加缓冲区大小以处理更复杂的视频
                cmd.extend(["-probesize", "100M", "-analyzeduration", "100M"])
                
                # 添加输入缓冲区优化
                cmd.extend(["-thread_queue_size", "4096"])
                
                # 增加最大缓冲区大小，解决转码失败问题 - 显著提高缓冲区大小以处理复杂视频
                cmd.extend(["-max_muxing_queue_size", "99999"])
                
                # 添加进度显示参数，确保ffmpeg输出更多进度信息
                cmd.extend(["-stats", "-v", "info"])
                
                # 添加错误恢复和容错参数 - 增强错误处理能力
                cmd.extend(["-err_detect", "ignore_err"])
                cmd.extend(["-fflags", "+genpts+igndts+discardcorrupt+autobsf"])
            
            # 添加更多内存和缓冲区优化参数（只在未跳过的情况下）
            if not skip_common_params:
                cmd.extend(["-max_interleave_delta", "0"])  # 禁用交错限制
                cmd.extend(["-max_delay", "5000000"])  # 增加最大延迟
                cmd.extend(["-strict", "experimental"])  # 使用实验性功能，提高兼容性
            
            # 如果使用GPU加速，仅记录激活状态，避免后续重复探测和重复改写命令
            if self.use_gpu and self.gpu_type:
                self.progress_signal.emit(t_format("正在激活GPU硬件编码: {}", self.gpu_type))
                if self.gpu_encoder_active:
                    self.progress_signal.emit(t_format("GPU编码器探测通过: {}", self.gpu_type))
            
            # 添加输出文件（只在未跳过的情况下）
            if not skip_common_params:
                cmd.append(self.output_file)
            
            # 如果使用GPU加速，处理命令合并逻辑
            if self.use_gpu and self.gpu_type:
                # 检查命令是否已经被重构（NVIDIA、AMD GPU情况）
                if self.gpu_type in ["NVIDIA (NVENC)", "AMD (AMF)"]:
                    # 对于这些GPU类型，命令已经完全重构，直接使用
                    final_cmd = cmd
                else:
                    # 对于其他情况，需要合并cmd_prefix和cmd
                    # 正确合并命令前缀和主命令，确保硬件加速参数在输入文件之前
                    # 找到输入文件的位置
                    input_index = -1
                    for i, item in enumerate(cmd):
                        if item == "-i":
                            input_index = i
                            break
                    
                    if input_index > 0 and 'cmd_prefix' in locals():
                        # 将命令分为三部分：输入文件前、输入文件及其参数、输入文件后
                        cmd_before_input = cmd[:input_index]
                        input_part = cmd[input_index:input_index+2]  # -i 和文件路径
                        cmd_after_input = cmd[input_index+2:]
                        
                        # 正确的命令顺序：ffmpeg.exe + 硬件加速参数 + 输入文件 + 编码器参数
                        final_cmd = cmd_prefix + cmd_before_input + input_part + cmd_after_input
                    else:
                        # 如果找不到输入文件或cmd_prefix未定义，直接使用cmd
                        final_cmd = cmd
                
                # 记录命令信息用于调试
                self.progress_signal.emit(t_format("使用GPU加速模式，执行命令: {}", ' '.join(final_cmd)))
                # 记录GPU加速模式信息
                if "NVIDIA" in self.gpu_type:
                    self.progress_signal.emit(t("NVIDIA GPU加速已正确配置，硬件加速参数已放置在输入文件之前，编码器参数已放置在输入文件之后"))
                elif "AMD" in self.gpu_type:
                    self.progress_signal.emit(t("AMD GPU加速已正确配置"))
                
                # 启动GPU使用率监控线程
                if self.gpu_usage_monitor is None or not self.gpu_usage_monitor.is_alive():
                    self.progress_signal.emit(t_format("启动GPU使用率监控线程 - {}", self.gpu_type))
                    self.gpu_usage_stop_event.clear()
                    self.gpu_usage_monitor = threading.Thread(target=self._monitor_gpu_usage)
                    self.gpu_usage_monitor.daemon = True
                    self.gpu_usage_monitor.start()
                    self.progress_signal.emit(t("GPU监控线程已启动，将实时监测GPU使用率"))
            else:
                final_cmd = self._sanitize_command_for_cpu(cmd)
                # 记录命令信息用于调试
                self.progress_signal.emit(t_format("使用CPU模式，执行命令: {}", ' '.join(final_cmd)))
            
            # 执行命令 - 使用UTF-8编码处理输入输出
            try:
                self.process = subprocess.Popen(
                    final_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,  # 使用text=True替代universal_newlines=True
                    encoding='utf-8',  # 显式指定UTF-8编码，避免使用系统默认的GBK编码
                    errors='replace',  # 添加错误处理策略，遇到无法解码的字符时替换而不是报错
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                self.progress_signal.emit(t_format("转码进程已启动，PID: {}", self.process.pid))
            except Exception as e:
                self.progress_signal.emit(t_format("启动转码进程失败: {}", str(e)))
                self.finished_signal.emit(False, t_format("进程启动失败: {}", str(e)))
                return
            
            # 读取输出并处理编码
            start_time = time.time()
            last_update_time = start_time
            last_progress_check_time = start_time
            gpu_status_check_time = start_time  # 添加GPU状态检查时间记录
            
            # 添加进程监控变量
            progress_stall_count = 0  # 进程停滞计数器
            last_valid_progress = 0.0  # 最后一次有效进程
            progress_stall_threshold = 30  # 30秒无进程更新视为停滞
            error_line_count = 0  # 错误行计数器
            warning_line_count = 0  # 警告行计数器
            recent_ffmpeg_output = deque(maxlen=40)  # 保存最近输出，失败时回显关键错误
            
            # 改进进度解析正则表达式，支持更多格式
            progress_pattern = re.compile(r'time=(\d+):(\d+):(\d+\.\d+)')
            alt_progress_pattern = re.compile(r'time=(\d+\.?\d*)')
            duration_pattern = re.compile(r'Duration: (\d+):(\d+):(\d+\.\d+)')
            alt_duration_pattern = re.compile(r'Duration: (\d+\.?\d*)')
            frame_pattern = re.compile(r'frame=\s*(\d+)')
            fps_pattern = re.compile(r'fps=\s*(\d+\.?\d*)')
            speed_pattern = re.compile(r'speed=\s*(\d+\.?\d*)x')
            # 添加更多进度匹配模式，特别是针对ffmpeg的不同输出格式
            progress_percent_pattern = re.compile(r'(\d+(?:\.\d+)?)%')
            size_pattern = re.compile(r'size=\s*(\d+[KMG]?B?)')
            bitrate_pattern = re.compile(r'bitrate=\s*(\d+\.?\d*[KMG]?bits/s)')
            # 添加特殊的webm进度匹配模式
            webm_progress_pattern = re.compile(r'(\d+)/(\d+) frames')
            processing_pattern = re.compile(r'(\w+)ing\s+#')
            # 添加更多特殊的webm和ffmpeg进度匹配模式
            alt_frame_pattern = re.compile(r'Processed (\d+) frames')
            alt_webm_progress = re.compile(r'(\d+) frames: (\d+\.?\d*)fps')
            # 匹配ffmpeg的详细统计输出
            stats_pattern = re.compile(r'q=(\d+\.?\d*)\s+size=\s*(\d+)kB\s+time=(\d+):(\d+):(\d+\.\d+)')
            # 匹配ffmpeg的帧处理信息
            frame_processing = re.compile(r'frame=\s*(\d+)\s+fps=\s*(\d+\.?\d*)\s+q=\s*(\d+\.?\d*)\s+size=\s*(\d+)kB\s+time=(\d+):(\d+):(\d+\.\d+)')
            # 匹配编码器特定的输出
            encoder_pattern = re.compile(r'(libvpx|libx264|libx265|aac|opus)\s+\|\s+(\w+)')
            # 匹配进度百分比的更多变体
            alt_percent_pattern = re.compile(r'(\d+(?:\.\d+)?)\s*%\s*complete')
            # 匹配ffmpeg的警告和错误信息
            warning_pattern = re.compile(r'\[\w+\]\s+Warning:')
            error_pattern = re.compile(r'\[\w+\]\s+Error:')
            
            self.progress_signal.emit(t("初始化进度解析模式，准备开始转码..."))
            duration_seconds = 0
            
            # 发送初始进度信号，并确保它能被显示
            self.progress_signal.emit("开始转码，发送初始进度信号: 0%")
            self.progress_percent_signal.emit(0.0)
            self.progress_sent = True
            # 强制更新UI，确保进度条立即显示
            QApplication.processEvents()
            
            # 首先使用ffprobe获取视频总时长，这是最可靠的方法
            try:
                self.progress_signal.emit(t("使用ffprobe获取视频时长信息..."))
                # 使用ffprobe获取视频时长
                metadata_cmd = ["ffprobe.exe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration,r_frame_rate,nb_frames", "-show_entries", "format=duration", "-of", "json", self.input_file]
                metadata_process = subprocess.Popen(
                    metadata_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    creationflags=self._creationflags
                )
                metadata_output, metadata_error = metadata_process.communicate()
                
                if metadata_error:
                    self.progress_signal.emit(t_format("ffprobe错误输出: {}", metadata_error))
                
                if metadata_output.strip():
                    try:
                        metadata = json.loads(metadata_output)
                        self.progress_signal.emit(t_format("获取到元数据: {}", metadata))
                        
                        # 尝试从流信息中获取时长
                        if 'streams' in metadata and metadata['streams'] and 'duration' in metadata['streams'][0]:
                            duration_seconds = float(metadata['streams'][0]['duration'])
                            self.progress_signal.emit(t_format("从视频流获取总时长: {} ({:.2f}秒)", self._format_time(duration_seconds), duration_seconds))
                            
                            # 获取帧率和总帧数信息，用于更准确的进度计算
                            if 'r_frame_rate' in metadata['streams'][0]:
                                frame_rate_str = metadata['streams'][0]['r_frame_rate']
                                if '/' in frame_rate_str:
                                    num, den = map(int, frame_rate_str.split('/'))
                                    frame_rate = num / den if den != 0 else 0
                                    self.progress_signal.emit(t_format("视频帧率: {:.2f} fps", frame_rate))
                            
                            if 'nb_frames' in metadata['streams'][0] and metadata['streams'][0]['nb_frames'] != 'N/A':
                                total_frames = int(metadata['streams'][0]['nb_frames'])
                                self.progress_signal.emit(t_format("视频总帧数: {}", total_frames))
                        # 如果流信息中没有时长，尝试从格式信息中获取
                        elif 'format' in metadata and 'duration' in metadata['format']:
                            duration_seconds = float(metadata['format']['duration'])
                            self.progress_signal.emit(t_format("从格式信息获取总时长: {} ({:.2f}秒)", self._format_time(duration_seconds), duration_seconds))
                    except json.JSONDecodeError as e:
                        self.progress_signal.emit(t_format("解析JSON元数据时出错: {}", str(e)))
            except Exception as e:
                self.progress_signal.emit(t_format("获取视频元数据时出错: {}", str(e)))
            
            # 如果ffprobe无法获取时长，尝试从ffmpeg输出中解析
            if duration_seconds <= 0:
                self.progress_signal.emit(t("从ffmpeg输出中解析视频时长..."))
                # 读取ffmpeg输出的前几行，尝试获取时长信息
                line_count = 0
                while line_count < 50:  # 只读取前50行，避免无限循环
                    line = self.process.stdout.readline()
                    if not line:
                        break
                    
                    line_count += 1
                    try:
                        decoded_line = self._decode_line(line)
                        if decoded_line:
                            self.progress_signal.emit(decoded_line)
                            
                            # 尝试提取视频总时长 - 标准格式 (HH:MM:SS.ms)
                            duration_match = duration_pattern.search(decoded_line)
                            if duration_match:
                                h, m, s = duration_match.groups()
                                duration_seconds = int(h) * 3600 + int(m) * 60 + float(s)
                                self.progress_signal.emit(t_format("从ffmpeg输出获取视频总时长: {}:{}:{} ({:.2f}秒)", h, m, s, duration_seconds))
                                break
                            
                            # 尝试提取视频总时长 - 替代格式 (秒数)
                            alt_duration_match = alt_duration_pattern.search(decoded_line)
                            if alt_duration_match:
                                duration_seconds = float(alt_duration_match.group(1))
                                self.progress_signal.emit(t_format("从ffmpeg输出获取视频总时长(秒数格式): {:.2f}秒", duration_seconds))
                                break
                    except Exception as e:
                        self.progress_signal.emit(t_format("解析输出时出错: {}", str(e)))
            
            # 如果仍然无法获取时长，使用备用方法
            if duration_seconds <= 0:
                self.progress_signal.emit(t("警告: 无法获取视频时长，转码进度可能不准确"))
                # 设置一个默认值，避免除以零错误
                duration_seconds = 1000  # 设置一个较大的默认值
            
            # 添加一个变量来跟踪上次发送的估算进度
            last_estimated_progress = 0.0
            
            # 继续处理输出并显示进度
            while True:
                line = self.process.stdout.readline()
                if not line:
                    break
                    
                try:
                    decoded_line = self._decode_line(line)
                    if not decoded_line:
                        continue
                    recent_ffmpeg_output.append(decoded_line.rstrip())
                        
                    # 不再使用估算进度，只在实际检测到进度时更新
                    current_time = time.time()
                    # 仅记录当前时间，不进行估算
                    last_progress_check_time = current_time
                    
                    # 定期检查GPU状态，每5秒输出一次GPU使用情况
                    if self.use_gpu and self.gpu_type and (current_time - gpu_status_check_time) >= 5:
                        gpu_status_check_time = current_time
                        if self.gpu_encoder_active:
                            self.progress_signal.emit(t_format("GPU加速状态: 已激活 - {}，当前使用率: {:.1f}%，最高: {:.1f}%", self.gpu_type, self.gpu_usage, self.max_gpu_usage))
                        elif self.gpu_usage > 5:
                            self.progress_signal.emit(t_format("GPU使用率: {:.1f}%，但编码器可能未正确激活", self.gpu_usage))
                            # 如果GPU使用率大于5%但编码器未激活，尝试标记为激活
                            if not self.gpu_encoder_active and self.gpu_usage > 10:
                                self.gpu_encoder_active = True
                                self.progress_signal.emit(t("检测到GPU活动，标记GPU编码器为已激活"))
                        else:
                            self.progress_signal.emit(t_format("GPU加速状态: 未检测到活动 - {}，当前使用率: {:.1f}%", self.gpu_type, self.gpu_usage))
                    
                    # 只在有意义的时间间隔更新UI，避免过多输出
                    # 对于webm格式，使用更高的更新频率
                    update_interval = 0.2 if input_ext == '.webm' else 0.3  # webm格式使用更高的更新频率
                    if current_time - last_update_time >= update_interval:
                        # 尝试提取进度信息 - 首先尝试标准时间格式 (HH:MM:SS.ms)
                        progress_match = progress_pattern.search(decoded_line)
                        progress_found = False
                        
                        if progress_match and duration_seconds > 0:
                            h, m, s = progress_match.groups()
                            progress_seconds = int(h) * 3600 + int(m) * 60 + float(s)
                            progress_percent = min(99.9, (progress_seconds / duration_seconds) * 100)  # 限制最大为99.9%，留出完成时的100%
                            progress_found = True
                            self.progress_signal.emit(t_format("标准时间格式进度: {:.2f}/{:.2f}秒 = {:.1f}%", progress_seconds, duration_seconds, progress_percent))
                        
                        # 如果标准格式未匹配，尝试替代时间格式 (秒数)
                        if not progress_found:
                            alt_progress_match = alt_progress_pattern.search(decoded_line)
                            if alt_progress_match and duration_seconds > 0:
                                progress_seconds = float(alt_progress_match.group(1))
                                progress_percent = min(99.9, (progress_seconds / duration_seconds) * 100)
                                progress_found = True
                                self.progress_signal.emit(t_format("替代时间格式进度: {:.2f}/{:.2f}秒 = {:.1f}%", progress_seconds, duration_seconds, progress_percent))
                        
                        # 尝试直接匹配百分比格式
                        if not progress_found:
                            percent_match = progress_percent_pattern.search(decoded_line)
                            if percent_match:
                                progress_percent = float(percent_match.group(1))
                                progress_percent = min(99.9, progress_percent)  # 限制最大为99.9%
                                progress_found = True
                                self.progress_signal.emit(t_format("直接百分比进度: {:.1f}%", progress_percent))
                        
                        # 尝试从webm特有的帧处理信息中提取进度
                        if not progress_found and input_ext == '.webm':
                            # 改进webm进度匹配模式，支持更多格式变体
                            webm_match = webm_progress_pattern.search(decoded_line)
                            if webm_match and duration_seconds > 0:
                                current_frame = int(webm_match.group(1))
                                total_frames = int(webm_match.group(2))
                                if total_frames > 0:
                                    progress_percent = min(99.9, (current_frame / total_frames) * 100)
                                    progress_found = True
                                    self.progress_signal.emit(t_format("WebM帧进度: {}/{} = {:.1f}%", current_frame, total_frames, progress_percent))
                            
                            # 添加更多webm特有的进度匹配模式
                            # 匹配VP8/VP9编码器的进度输出
                            vp_progress = re.search(r'(\d+)\s*frames in\s*(\d+\.?\d*)\s*s', decoded_line)
                            if not progress_found and vp_progress and duration_seconds > 0:
                                frames_processed = int(vp_progress.group(1))
                                time_elapsed = float(vp_progress.group(2))
                                if time_elapsed > 0:
                                    # 估算总帧数和进度
                                    estimated_fps = frames_processed / time_elapsed
                                    estimated_total_frames = estimated_fps * duration_seconds
                                    if estimated_total_frames > 0:
                                        progress_percent = min(99.9, (frames_processed / estimated_total_frames) * 100)
                                        progress_found = True
                                        self.progress_signal.emit(t_format("VP8/VP9进度: {}帧/{:.2f}秒 = {:.1f}%", frames_processed, time_elapsed, progress_percent))
                                        
                            # 匹配webm处理阶段信息
                            webm_stage = re.search(r'(Pass \d+/\d+)', decoded_line)
                            if not progress_found and webm_stage:
                                stage_info = webm_stage.group(1)
                                self.progress_signal.emit(t_format("WebM处理阶段: {}", stage_info))
                                # 确保在处理阶段变化时至少有一些进度显示
                                if self.last_progress_value < 5:
                                    progress_percent = 5.0
                                    progress_found = True
                        
                        # 尝试匹配ffmpeg的详细帧处理信息
                        if not progress_found:
                            frame_proc_match = frame_processing.search(decoded_line)
                            if frame_proc_match and duration_seconds > 0:
                                frame = int(frame_proc_match.group(1))
                                fps = float(frame_proc_match.group(2))
                                q_value = float(frame_proc_match.group(3))
                                size_kb = int(frame_proc_match.group(4))
                                h = int(frame_proc_match.group(5))
                                m = int(frame_proc_match.group(6))
                                s = float(frame_proc_match.group(7))
                                
                                # 计算当前处理时间和进度
                                current_seconds = h * 3600 + m * 60 + s
                                progress_percent = min(99.9, (current_seconds / duration_seconds) * 100)
                                
                                # 记录详细的处理信息
                                self.progress_signal.emit(t_format("详细帧处理进度: 帧={}, FPS={}, Q={}, 大小={}kB, 时间={}:{}:{:.2f}, 进度={:.1f}%", frame, fps, q_value, size_kb, h, m, s, progress_percent))
                                
                                # 直接使用实际进度，不做特殊处理
                                self.progress_signal.emit(t_format("转码进度: {:.1f}%", progress_percent))
                                # 不再对进度值进行人为调整
                                
                                progress_found = True
                        
                        # 尝试匹配ffmpeg的统计输出
                        if not progress_found:
                            stats_match = stats_pattern.search(decoded_line)
                            if stats_match and duration_seconds > 0:
                                q_value = float(stats_match.group(1))
                                size_kb = int(stats_match.group(2))
                                h = int(stats_match.group(3))
                                m = int(stats_match.group(4))
                                s = float(stats_match.group(5))
                                
                                # 计算当前处理时间和进度
                                current_seconds = h * 3600 + m * 60 + s
                                progress_percent = min(99.9, (current_seconds / duration_seconds) * 100)
                                
                                # 记录统计信息
                                self.progress_signal.emit(t_format("统计输出进度: Q={}, 大小={}kB, 时间={}:{}:{:.2f}, 进度={:.1f}%", q_value, size_kb, h, m, s, progress_percent))
                                progress_found = True
                        
                        # 如果时间格式都未匹配，尝试从帧信息估算进度
                        if not progress_found and "frame=" in decoded_line and duration_seconds > 0:
                            frame_match = frame_pattern.search(decoded_line)
                            fps_match = fps_pattern.search(decoded_line)
                            if frame_match:
                                frame = int(frame_match.group(1))
                                # 即使没有fps信息，也尝试估算进度
                                if fps_match:
                                    fps = float(fps_match.group(1))
                                    if fps > 0:
                                        estimated_seconds = frame / fps
                                        progress_percent = min(99.9, (estimated_seconds / duration_seconds) * 100)
                                        progress_found = True
                                        self.progress_signal.emit(t_format("基于帧数估算进度: 帧={}, FPS={}, 估算时间={:.2f}秒, 进度={:.1f}%", frame, fps, estimated_seconds, progress_percent))
                                else:
                                    # 没有fps信息时，使用总帧数估算
                                    # 假设视频帧率为25fps（常见帧率）
                                    estimated_total_frames = duration_seconds * 25
                                    if estimated_total_frames > 0:
                                        progress_percent = min(99.9, (frame / estimated_total_frames) * 100)
                                        progress_found = True
                                        self.progress_signal.emit(t_format("基于帧数估算进度(无FPS): 帧={}, 估算总帧数={:.0f}, 进度={:.1f}%", frame, estimated_total_frames, progress_percent))
                        
                        # 尝试匹配替代帧处理信息
                        if not progress_found:
                            alt_frame_match = alt_frame_pattern.search(decoded_line)
                            if alt_frame_match and duration_seconds > 0:
                                frames = int(alt_frame_match.group(1))
                                # 使用当前经过时间估算帧率和进度
                                elapsed_time = current_time - start_time
                                if elapsed_time > 0:
                                    estimated_fps = frames / elapsed_time
                                    estimated_total_frames = estimated_fps * duration_seconds
                                    if estimated_total_frames > 0:
                                        progress_percent = min(99.9, (frames / estimated_total_frames) * 100)
                                        progress_found = True
                                        self.progress_signal.emit(t_format("替代帧处理进度: 已处理{}帧, 估算进度={:.1f}%", frames, progress_percent))
                        
                        # 尝试匹配替代百分比格式
                        if not progress_found:
                            alt_percent_match = alt_percent_pattern.search(decoded_line)
                            if alt_percent_match:
                                progress_percent = float(alt_percent_match.group(1))
                                progress_percent = min(99.9, progress_percent)  # 限制最大为99.9%
                                progress_found = True
                                self.progress_signal.emit(t_format("替代百分比进度: {:.1f}%", progress_percent))
                        
                        # 检测处理阶段信息
                        if not progress_found:
                            processing_match = processing_pattern.search(decoded_line)
                            if processing_match:
                                processing_stage = processing_match.group(1)
                                self.progress_signal.emit(t_format("当前处理阶段: {}ing", processing_stage))
                                # 如果检测到处理阶段，至少发送一个小的进度更新
                                if self.last_progress_value < 1:
                                    progress_percent = 1.0
                                    progress_found = True
                        
                        # 检测错误和警告信息
                        if warning_pattern.search(decoded_line):
                            warning_line_count += 1
                            self.progress_signal.emit(t_format("⚠️ 警告: {}", decoded_line.strip()))
                        elif error_pattern.search(decoded_line) or "error" in decoded_line.lower() or "failed" in decoded_line.lower():
                            error_line_count += 1
                            self.progress_signal.emit(t_format("❌ 错误: {}", decoded_line.strip()))
                            
                            # 如果错误太多，可能需要终止转码
                            if error_line_count > 10:
                                self.progress_signal.emit(t("⚠️ 检测到过多错误，转码可能失败"))
                        
                        # 如果找到了进度信息，更新UI和计算剩余时间
                        if progress_found and duration_seconds > 0:
                            # 更新最后一次有效进度
                            if progress_percent > last_valid_progress:
                                last_valid_progress = progress_percent
                                progress_stall_count = 0  # 重置停滞计数器
                            
                            # 计算预计剩余时间
                            elapsed_time = current_time - start_time
                            if progress_percent > 0:
                                estimated_total_time = elapsed_time / (progress_percent / 100)
                                remaining_time = estimated_total_time - elapsed_time
                                
                                # 检查转码速度
                                speed_match = speed_pattern.search(decoded_line)
                                speed_info = ""
                                if speed_match:
                                    speed = float(speed_match.group(1))
                                    speed_info = t_format("转码速度: {:.2f}x", speed)
                                
                                # 格式化显示
                                progress_info = t_format("转码进度: {:.0f}% ", progress_percent)
                                if remaining_time > 0:
                                    progress_info += t_format("预计剩余时间: {} ", self._format_time(remaining_time))
                                if speed_info:
                                    progress_info += speed_info
                                self.progress_signal.emit(progress_info)
                                
                                # 发送进度百分比信号 - 确保进度条实时更新
                                if abs(progress_percent - self.last_progress_value) >= 0.1:  # 只要有0.1%的变化就更新
                                    self.progress_percent_signal.emit(progress_percent)
                                    self.last_progress_value = progress_percent
                                    self.progress_sent = True
                                    # 强制更新UI，确保进度条立即显示
                                    QApplication.processEvents()
                            
                            last_progress_check_time = current_time  # 重置进度检查计时器
                        else:
                            # 如果没有找到进度信息，但有其他有用信息，则输出
                            if "frame=" in decoded_line or "fps=" in decoded_line or "size=" in decoded_line or "time=" in decoded_line:
                                self.progress_signal.emit(decoded_line)
                                
                                # 修改：更积极地更新进度条，即使没有完整的进度信息
                                elapsed_time = current_time - start_time
                                # 基于已经过时间估算进度
                                if duration_seconds > 0:
                                    # 使用更积极的估算策略
                                    estimated_progress = min(95, (elapsed_time / duration_seconds) * 100)
                                    # 确保估算进度不会低于之前的进度
                                    estimated_progress = max(self.last_progress_value, estimated_progress)
                                    
                                    # 修改：移除估算进度的更新阈值，确保更频繁地更新
                                    # 只要估算进度不小于上次进度，就发送更新
                                    if estimated_progress >= self.last_progress_value:
                                        self.progress_signal.emit(t_format("基于时间估算进度: {:.1f}%", estimated_progress))
                                        self.progress_percent_signal.emit(estimated_progress)
                                        self.last_progress_value = estimated_progress
                                        self.progress_sent = True
                                # 即使没有完整的进度信息，也尝试更新进度条，避免卡在0%
                                elif self.last_progress_value < 1 and elapsed_time > 3:
                                    self.progress_percent_signal.emit(1.0)
                                    self.last_progress_value = 1.0
                                    self.progress_sent = True
                                    self.progress_signal.emit("没有进度信息，发送初始进度信号: 1.0%")
                                
                        last_update_time = current_time
                        
                    # 检查进度是否停滞
                    if current_time - last_progress_check_time > progress_stall_threshold:
                        progress_stall_count += 1
                        self.progress_signal.emit(t_format("⚠️ 警告: 进度停滞检测 - 已{}秒无进度更新 (停滞次数: {})", progress_stall_threshold, progress_stall_count))
                        
                        # 如果停滞次数过多，可能需要采取行动
                        if progress_stall_count >= 3:  # 3次停滞后采取行动
                            self.progress_signal.emit(t("❌ 进度长时间停滞，可能出现问题"))
                            # 可以选择终止进程或继续等待
                            # 这里选择继续等待，但给出警告
                            
                        last_progress_check_time = current_time  # 重置计时器
                        
                except Exception as e:
                    self.progress_signal.emit(t_format("解析输出时出错: {}", str(e)))
            
            # 等待进程完成
            self.process.wait()
            
            # 计算总耗时
            total_time = time.time() - start_time
            time_str = self._format_time(total_time)
            
            # 在转码完成时发送平滑的进度信号，确保从当前进度平滑过渡到100%
            current_progress = self.last_progress_value
            
            # 记录当前进度，不再人为跳跃到95%或98%
            self.progress_signal.emit(t_format("转码即将完成，当前进度: {:.1f}%", current_progress))
            # 不再强制设置进度为95%或98%，保持实际进度值
            # 强制更新UI
            QApplication.processEvents()
            # 短暂延迟
            time.sleep(0.1)
            
            # 检查进程返回码
            if self.process.returncode == 0:
                # 转码成功，平滑过渡到100%
                self.progress_signal.emit("转码成功完成，平滑过渡到100%")
                # 获取当前进度值
                current_progress = self.last_progress_value
                # 如果当前进度小于100%，则平滑过渡到100%
                if current_progress < 100:
                    # 计算需要增加的进度步数（每次增加1%）
                    steps = int((100 - current_progress) / 1) + 1
                    # 计算每步之间的延迟时间（总共不超过0.5秒）
                    delay = min(0.5 / steps, 0.05)  # 最大延迟50毫秒
                    # 逐步增加进度
                    for i in range(steps):
                        next_progress = min(100, current_progress + 1 + i)
                        self.progress_percent_signal.emit(next_progress)
                        self.last_progress_value = next_progress
                        # 短暂延迟，创造平滑效果
                        time.sleep(delay)
                        # 强制更新UI
                        QApplication.processEvents()
                # 最后确保发送100%的进度信号
                self.progress_percent_signal.emit(100.0)
                self.last_progress_value = 100.0
                # 强制更新UI，确保进度条显示完成状态
                QApplication.processEvents()
                self.progress_signal.emit("转码完成，进度100%")
                
                # 增强的输出文件验证
                self.progress_signal.emit(t("验证输出文件有效性..."))
                validation_success = False
                validation_message = ""
                
                if os.path.exists(self.output_file):
                    file_size = os.path.getsize(self.output_file)
                    self.progress_signal.emit(t_format("输出文件存在，大小: {:,} 字节", file_size))
                    
                    if file_size > 1024:  # 至少需要有1KB
                        # 使用ffprobe验证文件完整性
                        try:
                            self.progress_signal.emit(t("使用ffprobe验证文件完整性..."))
                            verify_cmd = ["ffprobe.exe", "-v", "error", "-select_streams", "v:0", 
                                        "-show_entries", "stream=duration,codec_name", "-of", "json", self.output_file]
                            verify_result = subprocess.run(
                                verify_cmd,
                                capture_output=True,
                                text=True,
                                timeout=10,
                                creationflags=self._creationflags
                            )
                            
                            if verify_result.returncode == 0 and verify_result.stdout.strip():
                                # 文件可以被ffprobe读取，认为有效
                                validation_success = True
                                validation_message = t_format("转码成功完成，输出文件大小: {:,} 字节，文件格式验证通过", file_size)
                                self.progress_signal.emit(validation_message)
                            else:
                                # ffprobe无法读取文件，可能损坏
                                validation_message = t_format("输出文件可能损坏或不完整，ffprobe验证失败: {}", verify_result.stderr)
                                self.progress_signal.emit(validation_message)
                                # 但如果文件大小合理，仍然认为可能成功
                                if file_size > 10240:  # 10KB以上认为可能成功
                                    validation_success = True
                                    validation_message += "，但文件大小合理，可能仍然可用"
                        except subprocess.TimeoutExpired:
                            validation_message = "文件验证超时，但文件存在且非空"
                            validation_success = True  # 超时但文件存在，认为成功
                        except Exception as e:
                            validation_message = t_format("文件验证出错: {}，但文件存在且非空", str(e))
                            validation_success = True  # 验证出错但文件存在，认为成功
                    else:
                        validation_message = t_format("输出文件太小 ({} 字节)，可能转码失败", file_size)
                        self.progress_signal.emit(validation_message)
                else:
                    validation_message = "输出文件不存在"
                    self.progress_signal.emit(validation_message)
                
                # 根据验证结果发送信号
                if validation_success:
                    self.finished_signal.emit(True, t_format("转码完成！总耗时: {}", time_str))
                else:
                    # 即使返回码为0，如果文件验证失败，也认为转码失败
                    self.progress_signal.emit("转码结果不完整，进度未达到100%")
                    self.progress_percent_signal.emit(-1.0)  # 发送错误状态
                    self.progress_signal.emit("收到转码进度信号: -1.00%")
                    self.progress_signal.emit(t("错误: 转码失败，显示错误状态"))
                    self.finished_signal.emit(False, t_format("转码结果不完整，进度未达到100%，耗时: {}", time_str))
            else:
                # 处理特定错误码
                if self.process.returncode == 4294967274 or self.process.returncode == -22:  # 0xFFFFFF6A 或 -22
                    self.progress_signal.emit(t("检测到内存缓冲区不足错误(4294967274)，正在尝试使用安全参数重新转码..."))
                    # 不再发送90.5%的进度信号，而是保持当前进度值
                    # 记录当前进度值，以便在重试时能够平滑过渡
                    current_progress = self.last_progress_value
                    self.progress_signal.emit(t_format("保持当前进度: {:.1f}%，准备重试转码", current_progress))
                    # 尝试使用安全参数自动重试
                    retry_success = self._retry_with_safe_params()
                    
                    # 如果重试失败，显示错误信息和建议
                    if not retry_success:
                        error_msg = "转码失败：内存缓冲区不足或输入/输出错误。尝试以下解决方案：\n"
                        error_msg += "1. 降低视频分辨率或比特率\n"
                        error_msg += "2. 关闭GPU加速或尝试不同的编码器\n"
                        error_msg += "3. 确保视频文件未损坏\n"
                        error_msg += "4. 关闭其他占用内存的应用程序"
                        # 发送转码失败信号，但不使用负值
                        self.progress_signal.emit(t("转码失败，但保持进度条显示"))
                        self.finished_signal.emit(False, error_msg)
                else:
                    ffmpeg_error_excerpt = "\n".join(recent_ffmpeg_output).strip()
                    if ffmpeg_error_excerpt:
                        self.progress_signal.emit(t("FFmpeg最近输出（失败诊断）:"))
                        for excerpt_line in ffmpeg_error_excerpt.splitlines():
                            self.progress_signal.emit(excerpt_line[:500])

                    # 仅在 AMD 硬件加速模式下，将特定返回码视为 AMD 回退信号
                    if self.gpu_type == "AMD (AMF)" and str(self.process.returncode) == "2880417800":
                        self.progress_signal.emit(t_format("检测到AMD GPU加速特定错误码：{}，尝试回退到CPU模式...", str(self.process.returncode)))
                        # 记录错误信息，但不立即发送失败信号
                        error_msg = t_format("AMD GPU加速失败，错误码：{}，尝试使用CPU模式重新转码", str(self.process.returncode))
                        self.progress_signal.emit(error_msg)
                        
                        # 增强的输出文件验证
                        self.progress_signal.emit(t("验证输出文件有效性..."))
                        validation_success = False
                        validation_message = ""
                        
                        if os.path.exists(self.output_file):
                            file_size = os.path.getsize(self.output_file)
                            self.progress_signal.emit(t_format("输出文件存在，大小: {:,} 字节", file_size))
                            
                            if file_size > 1024:  # 至少需要有1KB
                                # 使用ffprobe验证文件完整性
                                try:
                                    self.progress_signal.emit(t("使用ffprobe验证文件完整性..."))
                                    verify_cmd = ["ffprobe.exe", "-v", "error", "-select_streams", "v:0", 
                                                "-show_entries", "stream=duration,codec_name", "-of", "json", self.output_file]
                                    verify_result = subprocess.run(
                                        verify_cmd,
                                        capture_output=True,
                                        text=True,
                                        timeout=10,
                                        creationflags=self._creationflags
                                    )
                                    
                                    if verify_result.returncode == 0 and verify_result.stdout.strip():
                                        # 文件可以被ffprobe读取，认为有效
                                        validation_success = True
                                        validation_message = t_format("转码成功完成，输出文件大小: {:,} 字节，文件格式验证通过", file_size)
                                        self.progress_signal.emit(validation_message)
                                    else:
                                        # ffprobe无法读取文件，可能损坏
                                        validation_message = t_format("输出文件可能损坏或不完整，ffprobe验证失败: {}", verify_result.stderr)
                                        self.progress_signal.emit(validation_message)
                                        # 但如果文件大小合理，仍然认为可能成功
                                        if file_size > 10240:  # 10KB以上认为可能成功
                                            validation_success = True
                                            validation_message += "，但文件大小合理，可能仍然可用"
                                except subprocess.TimeoutExpired:
                                    validation_message = "文件验证超时，但文件存在且非空"
                                    validation_success = True  # 超时但文件存在，认为成功
                                except Exception as e:
                                    validation_message = t_format("文件验证出错: {}，但文件存在且非空", str(e))
                                    validation_success = True  # 验证出错但文件存在，认为成功
                            else:
                                validation_message = t_format("输出文件太小 ({} 字节)，可能转码失败", file_size)
                                self.progress_signal.emit(validation_message)
                        else:
                            validation_message = "输出文件不存在"
                            self.progress_signal.emit(validation_message)
                        
                        # 根据验证结果发送信号
                        if validation_success:
                            self.finished_signal.emit(True, t_format("转码完成，耗时: {}", time_str))
                        else:
                            self.finished_signal.emit(False, t_format("转码失败: {}，耗时: {}", validation_message, time_str))
                    else:
                        # 发送转码失败信号，但不使用负值
                        self.progress_signal.emit(t_format("转码失败，返回码：{}", str(self.process.returncode)))
                        self.finished_signal.emit(False, t_format("转码失败，返回码：{}，耗时: {}", str(self.process.returncode), time_str))
                
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            self.progress_signal.emit(t_format("详细错误信息: {}", error_details))
            # 发送错误状态的进度信号
            self.progress_percent_signal.emit(-1.0)  # 使用负值表示错误状态
            self.progress_signal.emit(t_format("转码过程中发生异常: {}", str(e)))
            self.finished_signal.emit(False, t_format("发生错误: {}", str(e)))
        finally:
            had_gpu_monitor = bool(self.gpu_usage_monitor and self.gpu_usage_monitor.is_alive())
            original_gpu_type = self.gpu_type
            display_gpu_type = original_gpu_type or "GPU"
            original_gpu_active = self.gpu_encoder_active
            original_max_usage = self.max_gpu_usage
            usage_history = list(self.gpu_usage_history)

            # 确保在任何情况下都停止GPU使用率监控线程（包括已回退CPU）
            self._stop_gpu_monitor_thread()

            if had_gpu_monitor:
                self.progress_signal.emit(t("GPU监控已停止"))

                # 输出增强的GPU性能总结报告
                if original_gpu_active:
                    avg_gpu_usage = sum(usage_history) / max(1, len(usage_history))

                    # 计算GPU效率评分
                    efficiency_score = min(10.0, avg_gpu_usage / 8.0)  # 8%以上为满分
                    performance_grade = "A+" if efficiency_score >= 9 else "A" if efficiency_score >= 7 else "B" if efficiency_score >= 5 else "C" if efficiency_score >= 3 else "D"

                    # 主要总结信息
                    summary_msg = t("📊 GPU性能总结报告 📊")
                    self.progress_signal.emit(summary_msg)
                    self.progress_signal.emit(t_format("✅ GPU类型: {}", display_gpu_type))
                    self.progress_signal.emit(t_format("📈 最高使用率: {:.1f}% | 平均使用率: {:.1f}%", original_max_usage, avg_gpu_usage))
                    self.progress_signal.emit(t_format("🏆 效率评分: {:.1f}/10 ({}级)", efficiency_score, performance_grade))

                    # 性能分析和建议
                    if avg_gpu_usage < 2:
                        self.progress_signal.emit(t("⚠️ 性能警告: GPU使用率极低，可能未真正启用硬件加速"))
                        self.progress_signal.emit(t("💡 建议: 检查GPU驱动程序和编码器设置"))
                    elif avg_gpu_usage < 10:
                        self.progress_signal.emit(t("🟡 性能提示: GPU使用率较低，还有优化空间"))
                        self.progress_signal.emit(t("💡 建议: 尝试调整编码参数或提高视频质量"))
                    elif avg_gpu_usage < 30:
                        self.progress_signal.emit(t("🟠 性能状态: GPU使用率中等，硬件加速正常工作"))
                        self.progress_signal.emit(t("💡 建议: 可以考虑进一步优化参数提高效率"))
                    elif avg_gpu_usage < 60:
                        self.progress_signal.emit(t("🟢 性能良好: GPU使用率较高，硬件加速效果显著"))
                        self.progress_signal.emit(t("✨ 表现优秀: 当前设置已达到良好效果"))
                    else:
                        self.progress_signal.emit(t("🔥 性能卓越: GPU使用率非常高，硬件加速充分利用"))
                        self.progress_signal.emit(t("🏆 完美表现: GPU硬件加速已达到最佳状态"))

                    # 记录GPU使用率历史分布
                    if len(usage_history) > 0:
                        usage_ranges = {
                            "0-20%": 0,
                            "20-40%": 0,
                            "40-60%": 0,
                            "60-80%": 0,
                            "80-100%": 0
                        }

                        for usage in usage_history:
                            if usage < 20:
                                usage_ranges["0-20%"] += 1
                            elif usage < 40:
                                usage_ranges["20-40%"] += 1
                            elif usage < 60:
                                usage_ranges["40-60%"] += 1
                            elif usage < 80:
                                usage_ranges["60-80%"] += 1
                            else:
                                usage_ranges["80-100%"] += 1

                        # 计算百分比分布
                        total = len(usage_history)
                        distribution = ", ".join([f"{k}: {v/total*100:.1f}%" for k, v in usage_ranges.items() if v > 0])
                        self.progress_signal.emit(t_format("GPU使用率分布: {}", distribution))
                else:
                    avg_gpu_usage = sum(usage_history) / max(1, len(usage_history)) if usage_history else 0
                    self.progress_signal.emit(t_format("GPU硬件加速总结: 未检测到{}被有效使用，可能使用了CPU编码，最高GPU使用率: {:.1f}%，平均GPU使用率: {:.1f}%", display_gpu_type, original_max_usage, avg_gpu_usage))

                    # 分析可能的原因
                    if original_max_usage < 5:
                        self.progress_signal.emit(t("可能原因: GPU编码器未被正确初始化或不支持当前视频格式"))
                    elif original_max_usage >= 5 and original_max_usage < 20:
                        self.progress_signal.emit(t("可能原因: GPU编码器被初始化但未被充分利用，或与当前视频格式兼容性不佳"))
                    else:
                        self.progress_signal.emit(t("可能原因: 虽然GPU有一定活动，但编码工作可能主要由CPU完成"))
                    self.progress_signal.emit(t("建议检查GPU驱动是否最新，或尝试不同的编码器设置"))
    
    def _retry_with_cpu_mode(self):
        """使用CPU模式重新尝试转码，适用于AMD GPU加速错误"""
        try:
            self.progress_signal.emit(t("使用CPU模式重新尝试转码..."))
            self.progress_percent_signal.emit(0.0)  # 重置进度条
            
            # 构建CPU模式的ffmpeg命令
            cmd = [FFMPEG_CMD, "-i", self.input_file]
            
            # 使用CPU编码器，选择更稳定的参数
            output_ext = os.path.splitext(self.output_file)[1].lower()
            if output_ext in ['.mp4', '.mkv']:
                cmd.extend(["-c:v", "libx264", "-preset", "faster", "-crf", "23"])
                # 添加更多稳定性参数
                cmd.extend(["-profile:v", "high", "-level", "4.1"])
            elif output_ext == '.webm':
                cmd.extend(["-c:v", "libvpx", "-crf", "30", "-b:v", "2M"])
            else:
                cmd.extend(["-c:v", "libx264", "-preset", "faster", "-crf", "23"])
                cmd.extend(["-profile:v", "high", "-level", "4.1"])
            
            # 添加音频编码参数
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
            
            # 添加通用优化参数
            cmd.extend(["-threads", "4"])
            cmd.extend(["-max_muxing_queue_size", "9999"])  # 增大队列大小，避免缓冲区错误
            cmd.extend(["-movflags", "+faststart"])
            
            # 添加进度显示参数
            cmd.extend(["-stats", "-v", "info"])
            
            # 添加输出文件
            cmd.append(self.output_file)
            
            # 执行命令
            self.progress_signal.emit(t_format("执行CPU模式命令: {}", ' '.join(cmd)))
            start_time = time.time()
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            # 用于解析进度的正则表达式
            time_pattern = re.compile(r'time=(\d+):(\d+):(\d+\.\d+)')
            duration_pattern = re.compile(r'Duration: (\d+):(\d+):(\d+\.\d+)')
            
            # 记录总时长
            total_duration = None
            last_progress_update = time.time()
            last_percent = 0
            
            # 读取输出并更新进度
            for line in self.process.stdout:
                decoded_line = line.strip() if line else ""
                if decoded_line:
                    self.progress_signal.emit(decoded_line)
                    
                    # 解析总时长
                    if not total_duration:
                        duration_match = duration_pattern.search(decoded_line)
                        if duration_match:
                            h, m, s = map(float, duration_match.groups())
                            total_duration = h * 3600 + m * 60 + s
                            self.progress_signal.emit(t_format("视频总时长: {:.2f}秒", total_duration))
                    
                    # 解析当前进度
                    time_match = time_pattern.search(decoded_line)
                    if time_match and total_duration:
                        h, m, s = map(float, time_match.groups())
                        current_time = h * 3600 + m * 60 + s
                        percent = min(100, (current_time / total_duration) * 100)
                        
                        # 只有当进度变化超过0.5%或距离上次更新超过0.5秒时才发送信号
                        current_update_time = time.time()
                        if abs(percent - last_percent) >= 0.5 or (current_update_time - last_progress_update) >= 0.5:
                            self.progress_percent_signal.emit(percent)
                            last_progress_update = current_update_time
                            last_percent = percent
                            self.progress_signal.emit(t_format("CPU模式转码进度: {:.2f}%", percent))
                    
                    # 如果长时间没有进度更新，发送估算进度
                    current_time = time.time()
                    if total_duration and current_time - last_progress_update > 3:
                        elapsed_time = current_time - start_time
                        # 使用更平缓的进度估算算法
                        if elapsed_time < 30:
                            estimated_percent = min(5, last_percent + 0.5)
                        elif elapsed_time < 60:
                            estimated_percent = min(15, last_percent + 1)
                        elif elapsed_time < 120:
                            estimated_percent = min(30, last_percent + 2)
                        else:
                            estimated_percent = min(95, last_percent + 3)
                        
                        if estimated_percent > last_percent:
                            self.progress_signal.emit(t_format("CPU模式估算进度: {:.2f}%", estimated_percent))
                            self.progress_percent_signal.emit(estimated_percent)
                            last_progress_update = current_time
                            last_percent = estimated_percent
            
            # 等待进程完成
            self.process.wait()
            
            # 计算总耗时
            end_time = time.time()
            elapsed_seconds = end_time - start_time
            hours, remainder = divmod(elapsed_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            time_str = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
            
            # 检查进程返回码
            if self.process.returncode == 0:
                self.progress_signal.emit(t_format("CPU模式转码成功完成，耗时: {}", time_str))
                self.progress_percent_signal.emit(100.0)
                return True
            else:
                self.progress_signal.emit(t_format("CPU模式转码失败，返回码：{}，耗时: {}", str(self.process.returncode), time_str))
                return False
                
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            self.progress_signal.emit(t_format("CPU模式转码出错: {}", str(e)))
            self.progress_signal.emit(t_format("详细错误信息: {}", error_details))
            return False
    
    def _retry_with_safe_params(self):
        """使用更保守的参数重新尝试转码，适用于内存缓冲区不足错误"""
        try:
            self.progress_signal.emit(t("使用安全参数重新尝试转码..."))
            
            # 构建更保守的ffmpeg命令
            cmd = [FFMPEG_CMD, "-i", self.input_file]
            
            # 使用最基本的转码参数
            output_ext = os.path.splitext(self.output_file)[1].lower()
            input_ext = os.path.splitext(self.input_file)[1].lower()
            
            # 检查输入文件是否存在且可读
            if not os.path.exists(self.input_file) or os.path.getsize(self.input_file) <= 0:
                self.progress_signal.emit(t("错误: 输入文件不存在或为空文件"))
                return False
                
            # 尝试创建临时输出文件，确保输出路径可写
            output_dir = os.path.dirname(self.output_file)
            if not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir, exist_ok=True)
                    self.progress_signal.emit(t_format("创建输出目录: {}", output_dir))
                except Exception as e:
                    self.progress_signal.emit(t_format("创建输出目录失败: {}", str(e)))
                    return False
            
            # 添加基本视频编码参数 - 使用更保守的设置
            if output_ext in ['.mp4', '.mkv']:
                # 使用超快速预设和较低质量设置，优先保证转码成功
                cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30"])
                # 添加更多兼容性参数
                cmd.extend(["-pix_fmt", "yuv420p", "-movflags", "+faststart"])
                # 添加更多容错参数
                cmd.extend(["-tune", "fastdecode"])
            elif output_ext == '.webm':
                # 使用更保守的VP8编码器而不是VP9，VP8更稳定
                cmd.extend(["-c:v", "libvpx", "-crf", "35", "-b:v", "800k", "-cpu-used", "5"])
                # 为webm添加更多兼容性参数
                cmd.extend(["-deadline", "realtime", "-lag-in-frames", "0"])
                # 添加更多容错参数
                cmd.extend(["-auto-alt-ref", "0"])
            
            # 添加基本音频编码参数 - 使用更保守的设置
            if output_ext in ['.mp4', '.mkv']:
                cmd.extend(["-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2"])
            elif output_ext == '.webm':
                cmd.extend(["-c:a", "libvorbis", "-b:a", "96k", "-ar", "44100", "-ac", "2"])
            elif output_ext == '.mp3':
                # MP3转码使用最保守的设置
                cmd.extend(["-c:a", "libmp3lame", "-q:a", "4", "-ar", "44100", "-ac", "2"])
                # 不使用固定比特率，使用VBR质量模式避免内存问题
                self.progress_signal.emit(t("MP3安全模式：使用VBR质量4（中等质量）避免内存问题"))
            
            # 降低分辨率以减少内存使用 - 使用更低的分辨率
            cmd.extend(["-vf", "scale=854:-2"])  # 480p分辨率，更保守
            
            # 禁用GPU加速，使用CPU编码
            self.use_gpu = False
            
            # 限制线程数但不要太低，以保持一定的性能
            cmd.extend(["-threads", "2"])  # 减少线程数，降低资源占用
            
            # 添加基本缓冲区参数 - 使用更保守的设置
            if output_ext == '.mp3':
                # MP3转码使用最小缓冲区设置
                cmd.extend(["-max_muxing_queue_size", "256"])  # 最小队列大小
                cmd.extend(["-probesize", "1M", "-analyzeduration", "1M"])  # 最小分析缓冲区
                self.progress_signal.emit(t("MP3安全模式：使用最小缓冲区设置"))
            else:
                cmd.extend(["-max_muxing_queue_size", "1024"])  # 降低缓冲区大小，减少内存使用
                cmd.extend(["-probesize", "5M", "-analyzeduration", "5M"])  # 降低分析缓冲区大小
            
            # 添加错误恢复参数 - 增强错误处理能力
            cmd.extend(["-err_detect", "ignore_err"])
            cmd.extend(["-fflags", "+genpts+igndts+discardcorrupt+autobsf+bitexact"])
            
            # 添加更多容错参数
            cmd.extend(["-strict", "experimental"])
            cmd.extend(["-avoid_negative_ts", "1"])
            
            # 限制输出文件大小和时长，避免无限增长
            cmd.extend(["-fs", "2G"])  # 限制文件大小最大2GB
            
            # 添加进度显示参数
            cmd.extend(["-stats", "-v", "warning"])
            
            # 添加自动覆盖输出文件参数
            cmd.extend(["-y"])
            
            # 添加输出文件
            cmd.append(self.output_file)
            
            self.progress_signal.emit(t_format("重试命令: {}", ' '.join(cmd)))
            
            # 执行命令
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,  # 使用文本模式，避免编码问题
                encoding='utf-8',  # 显式指定UTF-8编码
                errors='replace',  # 替换无法解码的字符
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            # 读取输出并更新进度
            start_time = time.time()
            last_update_time = start_time
            last_progress_value = 0
            
            # 发送初始进度信号
            self.progress_signal.emit(t("开始安全模式转码，发送初始进度信号"))
            self.progress_percent_signal.emit(0.1)
            
            # 添加超时检测
            max_no_output_time = 10  # 最大无输出时间（秒）
            last_output_time = time.time()
            
            # 改进的进度解析
            for line in self.process.stdout:
                current_time = time.time()
                last_output_time = current_time  # 更新最后输出时间
                
                if not line or not line.strip():
                    continue
                    
                self.progress_signal.emit(line.strip())
                
                # 检查是否包含关键错误信息
                if "Error" in line or "错误" in line or "failed" in line.lower():
                    self.progress_signal.emit(t_format("检测到错误信息: {}", line.strip()))
                
                # 尝试解析进度
                if current_time - last_update_time >= 0.5:
                    # 尝试从输出中提取时间信息
                    time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                    if time_match:
                        h, m, s = map(float, time_match.groups())
                        progress_seconds = h * 3600 + m * 60 + s
                        # 使用更保守的进度估算
                        progress_percent = min(90, progress_seconds / 2)  # 限制最大进度为90%
                        self.progress_signal.emit(t_format("转码进度: {:.1f}%", progress_percent))
                        self.progress_percent_signal.emit(progress_percent)
                        last_progress_value = progress_percent
                    else:
                        # 估算进度 - 使用更保守的估算方法
                        elapsed_time = current_time - start_time
                        # 每5秒增加1%，最大90%，确保留出足够空间给完成阶段
                        estimated_progress = min(90, elapsed_time / 5)
                        
                        # 只有当进度增加时才更新
                        if estimated_progress > last_progress_value + 0.5:  # 至少增加0.5%才更新
                            self.progress_percent_signal.emit(estimated_progress)
                            self.progress_signal.emit(t_format("估算转码进度: {:.1f}%", estimated_progress))
                            last_progress_value = estimated_progress
                    
                    last_update_time = current_time
                
                # 检查是否长时间没有进度更新
                if current_time - last_output_time > max_no_output_time:
                    self.progress_signal.emit(t_format("警告: 转码进程{}秒内无输出，可能已卡住", max_no_output_time))
                    # 不立即终止，继续等待一段时间
            
            # 等待进程完成，添加超时机制
            max_wait_time = 600  # 最大等待时间（秒）
            wait_start = time.time()
            
            while self.process and self.process.poll() is None:
                # 检查是否超时
                if time.time() - wait_start > max_wait_time:
                    self.progress_signal.emit(t_format("错误: 转码进程超过{}秒未完成，强制终止", max_wait_time))
                    try:
                        self.process.terminate()
                        time.sleep(1)
                        if self.process and self.process.poll() is None:
                            self.process.kill()
                    except Exception as e:
                        self.progress_signal.emit(t_format("终止进程时出错: {}", str(e)))
                    return False
                
                # 短暂等待，避免CPU占用
                time.sleep(0.5)
            
            # 确保在转码完成时发送100%的进度信号
            self.progress_signal.emit(t("转码进程已结束，检查结果"))
            
            # 计算总耗时
            total_time = time.time() - start_time
            time_str = self._format_time(total_time)
            
            # 检查输出文件是否存在且大小合理
            output_file_valid = os.path.exists(self.output_file) and os.path.getsize(self.output_file) > 10240  # 至少10KB
            
            if self.process.returncode == 0 and output_file_valid:
                # 验证输出文件是否可以播放
                self.progress_signal.emit(t("验证输出文件有效性..."))
                try:
                    # 使用ffprobe验证文件
                    probe_cmd = ["ffprobe.exe", "-v", "error", "-select_streams", "v:0", "-show_entries", 
                                "stream=codec_type,width,height,duration", "-of", "json", self.output_file]
                    probe_process = subprocess.Popen(
                        probe_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        creationflags=self._creationflags
                    )
                    probe_output, probe_error = probe_process.communicate(timeout=10)
                    
                    if probe_process.returncode == 0 and "codec_type" in probe_output:
                        self.progress_signal.emit(t("输出文件验证成功，包含有效视频流"))
                        # 只有在重试转码真正成功时才发送100%的进度信号
                        self.progress_percent_signal.emit(100.0)
                        self.progress_signal.emit("转码完成，进度100%")
                        self.finished_signal.emit(True, t_format("使用安全参数重试转码成功！总耗时: {}", time_str))
                        return True
                    else:
                        self.progress_signal.emit(t_format("输出文件验证失败: {}", probe_error))
                        # 文件存在但验证失败，可能是不完整或损坏
                        error_msg = t_format("重试转码可能部分成功，但输出文件验证失败，耗时: {}\n建议尝试使用其他格式或编码器。", time_str)
                        self.progress_signal.emit("转码结果不完整，进度未达到100%")
                        self.progress_percent_signal.emit(-1.0)  # 使用负值表示错误状态
                        self.finished_signal.emit(False, error_msg)
                        return False
                except Exception as e:
                    self.progress_signal.emit(t_format("验证输出文件时出错: {}", str(e)))
                    # 继续执行，不因验证失败而判定转码失败
                    if output_file_valid:
                        self.progress_percent_signal.emit(100.0)
                        self.progress_signal.emit(t("转码可能成功，但验证过程出错"))
                        self.finished_signal.emit(True, t_format("使用安全参数重试转码可能成功，但无法验证文件完整性。总耗时: {}", time_str))
                        return True
            else:
                # 转码失败时发送失败信息，不更新进度为100%
                error_msg = t_format("重试转码失败，返回码：{}，耗时: {}", str(self.process.returncode), time_str)
                
                # 添加更详细的错误信息
                if not output_file_valid:
                    error_msg += "\n输出文件无效或大小异常，可能是格式不兼容或编码器错误。"
                if self.process.returncode == 1:
                    error_msg += "\n错误码1: 可能是命令行参数错误或不支持的格式。"
                elif self.process.returncode == 2:
                    error_msg += "\n错误码2: 可能是输入文件无效或损坏。"
                elif self.process.returncode < 0:
                    error_msg += t_format("\n错误码{}: 进程可能被强制终止或崩溃。", self.process.returncode)
                
                # 添加建议解决方案
                error_msg += "\n\n建议尝试以下解决方案:\n"
                error_msg += "1. 使用不同的输出格式（如MP4、MKV或WebM）\n"
                error_msg += "2. 降低视频分辨率或比特率\n"
                error_msg += "3. 尝试不同的编码器\n"
                error_msg += "4. 确保输入文件未损坏"
                
                self.progress_signal.emit("转码失败，进度未达到100%")
                self.progress_percent_signal.emit(-1.0)  # 使用负值表示错误状态
                self.finished_signal.emit(False, error_msg)
                return False
                
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            self.progress_signal.emit(t_format("详细错误信息: {}", error_details))
            self.progress_signal.emit(t_format("重试转码时出错: {}", str(e)))
            self.progress_percent_signal.emit(-1.0)  # 使用负值表示错误状态
            
            # 添加更友好的错误信息和建议
            error_msg = t_format("重试转码过程中发生异常: {}\n\n", str(e))
            error_msg += "建议尝试以下解决方案:\n"
            error_msg += "1. 重启应用程序\n"
            error_msg += "2. 检查输入文件是否可访问\n"
            error_msg += "3. 检查输出路径是否可写入\n"
            error_msg += "4. 尝试使用其他格式或编码器"
            
            self.finished_signal.emit(False, error_msg)
            return False
    
    def _decode_line(self, line):
        """处理输出行"""
        # 由于设置了text=True和encoding='utf-8'，line应该已经是UTF-8编码的字符串
        # 如果仍然收到字节类型，尝试多种编码方式解码
        if isinstance(line, bytes):
            # 为了兼容性，如果输入仍然是字节，尝试多种编码解码
            encodings = ['utf-8', 'gbk', 'cp936', 'latin-1']
            for encoding in encodings:
                try:
                    return line.decode(encoding, errors='replace').strip()
                except Exception:
                    continue
            # 如果所有编码都失败，使用latin-1（它能解码任何字节序列）
            return line.decode('latin-1', errors='replace').strip()
        else:
            # 正常情况下，直接返回处理后的字符串
            return line.strip()
    
    def _format_time(self, seconds):
        """将秒数格式化为时:分:秒格式"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    
    def stop(self):
        """安全地停止进程"""
        cancel_signal_sent = False  # 标记是否已发送取消信号
        try:
            if self.process:
                self.progress_signal.emit(t("正在停止进程..."))
                # 首先尝试温和地终止进程
                self.process.terminate()
                
                # 给进程一些时间来终止（增加超时时间）
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # 如果等待超时，强制终止进程
                    self.progress_signal.emit(t("进程未响应，正在强制终止..."))
                    self.process.kill()
                    self.progress_signal.emit(t("已强制终止进程"))
                
                # 再次检查进程状态
                if self.process and self.process.poll() is None:
                    # 如果进程仍在运行，使用更强力的方法终止
                    import signal
                    try:
                        # 在Windows上，使用taskkill命令强制终止进程树
                        if os.name == 'nt':
                            subprocess.run(
                                ['taskkill', '/F', '/T', '/PID', str(self.process.pid)],
                                stderr=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                creationflags=self._creationflags
                            )
                        else:
                            # 在类Unix系统上使用SIGKILL信号
                            os.kill(self.process.pid, signal.SIGKILL)
                        self.progress_signal.emit(t("已强制终止进程及其子进程"))
                    except Exception as kill_error:
                        self.progress_signal.emit(t_format("强制终止进程失败: {}", str(kill_error)))
                else:
                    self.progress_signal.emit(t("进程已正常终止"))
                
                # 标记进程为None，避免重复终止
                self.process = None
                
                # 只发送一次取消成功信号
                if not cancel_signal_sent:
                    self.cancel_signal.emit()
                    cancel_signal_sent = True
        except Exception as e:
            self.progress_signal.emit(t_format("停止进程时出错: {}", str(e)))
            # 尝试强制结束进程
            try:
                if self.process and self.process.poll() is None:
                    self.process.kill()
                    # 只有在之前没有发送过信号时才发送取消成功信号
                    if not cancel_signal_sent:
                        self.cancel_signal.emit()
                        cancel_signal_sent = True
            except:
                pass
    
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
            self.progress_signal.emit(t_format("解析大小: {} -> 提取数值: {}", size_str, value))
            
            # 提取数值和单位 - 改进单位检测逻辑
            size_str = size_str.upper()  # 转换为大写以简化匹配
            
            # 检查各种单位并进行相应转换
            if any(unit in size_str for unit in ['KIB', 'KB', 'K']):
                result = value * 1024
                self.progress_signal.emit(t_format("单位转换: KB -> {:.2f} 字节", result))
                return result
                    
            elif any(unit in size_str for unit in ['MIB', 'MB', 'M']):
                result = value * 1024 * 1024
                self.progress_signal.emit(t_format("单位转换: MB -> {:.2f} 字节", result))
                return result
                    
            elif any(unit in size_str for unit in ['GIB', 'GB', 'G']):
                result = value * 1024 * 1024 * 1024
                self.progress_signal.emit(t_format("单位转换: GB -> {:.2f} 字节", result))
                return result
                
            elif any(unit in size_str for unit in ['TIB', 'TB', 'T']):
                result = value * 1024 * 1024 * 1024 * 1024
                self.progress_signal.emit(t_format("单位转换: TB -> {:.2f} 字节", result))
                return result
                
            elif 'B' in size_str:
                # 如果包含B单位，假设是字节
                self.progress_signal.emit(t_format("单位: 字节 -> {:.2f} 字节", value))
                return value
                
            # 如果没有单位，假设是字节
            self.progress_signal.emit(t_format("无单位: 假设为字节 -> {:.2f} 字节", value))
            return value
        except Exception as e:
            self.progress_signal.emit(t_format("解析大小出错: {}", str(e)))
            return 0
            return value
        except Exception as e:
            self.progress_signal.emit(t_format("解析大小出错: {}", str(e)))
            return 0
            
        except Exception as e:
            # 出错时记录错误并返回0
            print(t_format("解析大小字符串出错: {}, 原始字符串: {}", str(e), size_str))
            return 0
        except (ValueError, TypeError) as e:
            print(t_format("解析大小字符串出错: {}, 错误: {}", size_str, str(e)))
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
                cmd.extend(["-f", "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a][acodec!=opus]/"
                           "bestvideo[ext=mp4]+bestaudio[ext=m4a][acodec!=opus]/"
                           "bestvideo+bestaudio[acodec!=opus]/best[acodec!=opus][ext=mp4]/best[acodec!=opus]"])
                
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
                self.progress_signal.emit(t_format("使用输出模板: {}", output_template))
            
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
            self.progress_signal.emit(t_format("执行重试命令: {}", ' '.join(cmd)))
            
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
            self.progress_percent_signal.emit(0.1)  # 发送初始进度信号
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
                        self.progress_signal.emit(t_format("解析进度出错: {}", str(e)))
                
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
                        self.progress_signal.emit(t_format("✓ 重试流程已搬运 {} 个结果文件", len(moved)))
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
                    self.finished_signal.emit(False, t_format("重试下载失败，返回码：{}\n{}", self.process.returncode, error_output))
        
        except Exception as e:
            self.progress_signal.emit(t_format("重试下载时发生错误: {}", str(e)))
            self.finished_signal.emit(False, t_format("重试下载时发生错误: {}", str(e)))
    
    def _retry_with_single_format(self):
        """当合并格式也失败时，尝试使用单一格式下载（不需要合并的格式）"""
        try:
            # 构建使用单一格式的命令
            cmd = ["yt-dlp.exe"]
            
            # 使用单一格式，避免需要合并
            self.progress_signal.emit(t("使用单一格式下载，避免后处理合并步骤..."))
            cmd.extend(["-f", "best[ext=mp4]/best[ext=mkv]/best[ext=webm]/best"])
            
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
                self.progress_signal.emit(t_format("使用输出模板: {}", output_template))
            
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
            
            self.progress_signal.emit(t_format("执行单一格式命令: {}", ' '.join(cmd)))
            
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
            self.progress_signal.emit(t("进程完成，检查下载结果..."))
            
            if self.process.returncode == 0:
                if self._staging_output_dir:
                    moved = self._move_staging_outputs_to_target()
                    if moved:
                        self.progress_signal.emit(t_format("✓ 单一格式重试已搬运 {} 个结果文件", len(moved)))
                # 确保在下载完成时发送100%的进度信号
                self.progress_percent_signal.emit(100.0)
                self.progress_signal.emit("单一格式下载成功，进度100%")
                self.finished_signal.emit(True, "下载完成！")
            else:
                # 如果所有尝试都失败了
                self.finished_signal.emit(False, t_format("所有下载尝试均失败，返回码：{}\n{}", self.process.returncode, error_output))
        
        except Exception as e:
            self.progress_signal.emit(t_format("单一格式下载时发生错误: {}", str(e)))
            self.finished_signal.emit(False, t_format("单一格式下载时发生错误: {}", str(e)))
    
    def stop(self):
        """安全地停止进程"""
        cancel_signal_sent = False  # 标记是否已发送取消信号
        try:
            if self.process:
                self.progress_signal.emit(t("正在停止进程..."))
                # 首先尝试温和地终止进程
                self.process.terminate()
                
                # 给进程一些时间来终止（增加超时时间）
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # 如果等待超时，强制终止进程
                    self.progress_signal.emit(t("进程未响应，正在强制终止..."))
                    self.process.kill()
                    self.progress_signal.emit(t("已强制终止进程"))
                
                # 再次检查进程状态
                if self.process and self.process.poll() is None:
                    # 如果进程仍在运行，使用更强力的方法终止
                    import signal
                    try:
                        # 在Windows上，使用taskkill命令强制终止进程树
                        if os.name == 'nt':
                            subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.process.pid)], 
                                          stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                        else:
                            # 在类Unix系统上使用SIGKILL信号
                            os.kill(self.process.pid, signal.SIGKILL)
                        self.progress_signal.emit(t("已强制终止进程及其子进程"))
                    except Exception as kill_error:
                        self.progress_signal.emit(t_format("强制终止进程失败: {}", str(kill_error)))
                else:
                    self.progress_signal.emit(t("进程已正常终止"))
                
                # 标记进程为None，避免重复终止
                self.process = None
                
                # 只发送一次取消成功信号
                if not cancel_signal_sent:
                    self.cancel_signal.emit()
                    cancel_signal_sent = True
        except Exception as e:
            self.progress_signal.emit(t_format("停止进程时出错: {}", str(e)))
            # 尝试强制结束进程
            try:
                if self.process and self.process.poll() is None:
                    self.process.kill()
                    # 只有在之前没有发送过信号时才发送取消成功信号
                    if not cancel_signal_sent:
                        self.cancel_signal.emit()
                        cancel_signal_sent = True
            except:
                pass


def _parse_update_mirror_prefixes(raw_text: str):
    if not raw_text:
        return []
    raw_text = raw_text.replace("\n", ",")
    prefixes = []
    for item in raw_text.split(","):
        value = (item or "").strip()
        if value:
            prefixes.append(value)
    return prefixes


DEFAULT_UPDATE_MIRROR_PREFIXES = [
    "https://gh-proxy.com/",
    "https://gh.ddlc.top/",
    "https://ghfast.top/",
    "https://gh.noki.icu/",
]

_GITHUB_DIRECT_CACHE = {
    "value": None,
    "ts": 0.0,
}
_GITHUB_DIRECT_TTL = 300  # seconds


def _is_github_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        host = urlparse(url).netloc.lower()
        return host.endswith("github.com") or host.endswith("githubusercontent.com")
    except Exception:
        return False


def _probe_github_direct_available() -> bool:
    try:
        import urllib.request
        import urllib.error

        # Use a lightweight endpoint to detect reachability.
        url = "https://api.github.com/rate_limit"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return True if response else False
        except urllib.error.HTTPError:
            # HTTP error still means the network path is reachable.
            return True
    except Exception:
        return False


def _is_github_direct_available() -> bool:
    try:
        now = time.time()
        cached = _GITHUB_DIRECT_CACHE.get("value", None)
        ts = _GITHUB_DIRECT_CACHE.get("ts", 0.0)
        if cached is not None and (now - ts) < _GITHUB_DIRECT_TTL:
            return cached
        available = _probe_github_direct_available()
        _GITHUB_DIRECT_CACHE["value"] = available
        _GITHUB_DIRECT_CACHE["ts"] = now
        return available
    except Exception:
        return False


def _get_update_mirror_prefixes():
    env = os.environ.get("CHAOS_UPDATE_MIRRORS") or os.environ.get("CHAOS_UPDATE_MIRROR_PREFIXES")
    if env:
        return _parse_update_mirror_prefixes(env)
    try:
        settings = QSettings("VideoDownloader", "Settings")
        val = str(settings.value("update_mirror_prefixes", "") or "").strip()
        prefixes = _parse_update_mirror_prefixes(val)
        if prefixes:
            return prefixes
    except Exception:
        pass
    return DEFAULT_UPDATE_MIRROR_PREFIXES


def _build_url_candidates(url: str):
    if not _is_github_url(url):
        return [url]
    
    candidates = []
    direct_first = _is_github_direct_available()
    
    # 获取镜像前缀
    prefixes = _get_update_mirror_prefixes()
    
    if direct_first:
        # 如果检测到直连可用，优先尝试直连
        candidates.append(url)
    
    # 添加镜像候选项
    if prefixes:
        for prefix in prefixes:
            try:
                if "{url}" in prefix:
                    candidate = prefix.replace("{url}", url)
                else:
                    candidate = prefix.rstrip("/") + "/" + url
                if candidate not in candidates:
                    candidates.append(candidate)
            except Exception:
                continue
    
    # 如果直连不可用，或者镜像已尝试完，最后一定补充直连作为兜底
    if url not in candidates:
        candidates.append(url)
        
    return candidates


def _open_url_with_fallback(url: str, timeout: int = 15, headers: dict = None):
    import urllib.request

    last_error = None
    for candidate in _build_url_candidates(url):
        try:
            req = urllib.request.Request(candidate, headers=headers or {})
            return urllib.request.urlopen(req, timeout=timeout)
        except Exception as e:
            last_error = e
            continue
    if last_error:
        raise last_error
    raise RuntimeError("No valid update URL candidates")


DEFAULT_APP_WEBSITE_URL = "https://hundunxiazaiqi.cn/"
DEFAULT_APP_UPDATE_METADATA_URL = DEFAULT_APP_WEBSITE_URL.rstrip("/") + "/version.json"
_APP_VERSION_CACHE = None


def get_app_version():
    """获取当前软件版本号。"""
    global _APP_VERSION_CACHE
    if _APP_VERSION_CACHE:
        return _APP_VERSION_CACHE

    version_info_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version_info.txt")
    candidates = [
        version_info_path,
        os.path.join(RUNTIME_APP_DIR, "version_info.txt"),
    ]

    for candidate in candidates:
        try:
            if not os.path.isfile(candidate):
                continue
            with open(candidate, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r"ProductVersion', u'(\d+\.\d+\.\d+)(?:\.\d+)?'", content)
            if match:
                _APP_VERSION_CACHE = match.group(1)
                return _APP_VERSION_CACHE
        except Exception:
            continue

    # 打包后的 exe 往往只带版本资源，不一定携带 version_info.txt。
    exe_candidates = []
    try:
        if getattr(sys, "frozen", False):
            exe_candidates.append(sys.executable)
    except Exception:
        pass

    try:
        main_exe = os.path.join(RUNTIME_APP_DIR, "SourceCraft.exe")
        if main_exe not in exe_candidates:
            exe_candidates.append(main_exe)
    except Exception:
        pass

    for candidate in exe_candidates:
        try:
            version = _read_windows_exe_version(candidate)
            if version:
                _APP_VERSION_CACHE = version
                return _APP_VERSION_CACHE
        except Exception:
            continue

    _APP_VERSION_CACHE = "0.0.0"
    return _APP_VERSION_CACHE


def _read_windows_exe_version(exe_path):
    """从 Windows 可执行文件的版本资源中读取 X.Y.Z 版本号。"""
    if os.name != "nt" or not exe_path or not os.path.isfile(exe_path):
        return None

    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(exe_path), None)
        if not size:
            return None

        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(exe_path), 0, size, buffer):
            return None

        value_ptr = ctypes.c_void_p()
        value_len = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(buffer, "\\", ctypes.byref(value_ptr), ctypes.byref(value_len)):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", ctypes.c_uint32),
                ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32),
                ("dwFileVersionLS", ctypes.c_uint32),
                ("dwProductVersionMS", ctypes.c_uint32),
                ("dwProductVersionLS", ctypes.c_uint32),
                ("dwFileFlagsMask", ctypes.c_uint32),
                ("dwFileFlags", ctypes.c_uint32),
                ("dwFileOS", ctypes.c_uint32),
                ("dwFileType", ctypes.c_uint32),
                ("dwFileSubtype", ctypes.c_uint32),
                ("dwFileDateMS", ctypes.c_uint32),
                ("dwFileDateLS", ctypes.c_uint32),
            ]

        ffi = ctypes.cast(value_ptr.value, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        major = (ffi.dwProductVersionMS >> 16) & 0xFFFF
        minor = ffi.dwProductVersionMS & 0xFFFF
        patch = (ffi.dwProductVersionLS >> 16) & 0xFFFF

        if any((major, minor, patch)):
            return f"{major}.{minor}.{patch}"
    except Exception as e:
        print(t_format("读取 exe 版本资源失败: {}", e))

    return None


def compare_version_text(version1, version2):
    """比较版本号，返回 -1 / 0 / 1。"""
    try:
        def clean_version(version):
            version = str(version or "").strip()
            if version.startswith("v"):
                version = version[1:]
            match = re.match(r"(\d+(?:\.\d+)+)", version)
            return match.group(1) if match else version

        v1_clean = clean_version(version1)
        v2_clean = clean_version(version2)

        if v1_clean == v2_clean:
            return 0

        v1_parts = []
        v2_parts = []

        for part in v1_clean.replace("-", ".").split("."):
            if part.isdigit():
                v1_parts.append(int(part))
            elif part:
                numbers = re.findall(r"\d+", part)
                if numbers:
                    v1_parts.append(int(numbers[0]))

        for part in v2_clean.replace("-", ".").split("."):
            if part.isdigit():
                v2_parts.append(int(part))
            elif part:
                numbers = re.findall(r"\d+", part)
                if numbers:
                    v2_parts.append(int(numbers[0]))

        if not v1_parts:
            v1_parts = [0]
        if not v2_parts:
            v2_parts = [0]

        max_len = max(len(v1_parts), len(v2_parts))
        v1_parts.extend([0] * (max_len - len(v1_parts)))
        v2_parts.extend([0] * (max_len - len(v2_parts)))

        for i in range(max_len):
            if v1_parts[i] < v2_parts[i]:
                return -1
            if v1_parts[i] > v2_parts[i]:
                return 1
        return 0
    except Exception as e:
        print(t_format("软件版本比较失败: {}", e))
        return 0


