"""VDH 风格的内联预览素材生成。

调查结论（REA 静态分析 + 源码扫描，规格见 ``docs/VDH-UI-预览规格.md``）：
Video Download Helper 的预览不是"播放原始地址"，而是让下载 worker 只取一条
视频分片，remux 成小 mp4 存进 OPFS，再由面板 ``URL.createObjectURL(file)`` 交给
``<video autoplay loop muted playsinline>`` 内联播放。对应代码：

- ``download_worker/main.js`` 的 ``Br``（strategy = ``m3u8_video_preview``）：
  ``s = is_live ? i.length - 1 : Math.floor(i.length / 2)`` 取播放列表中间那条分片，
  只解码视频轨，再 ``ff_init_muxer(...)`` + ``ff_write_multi(...)`` 写出小 mp4。
- ``service/main.js`` 的 ``Cf`` 处理 ``request_preview``：按 ``tab_id`` + ``media_hash``
  缓存，成功回 ``on_preview_available{filename}``，失败回 ``on_no_preview``。

这里不搬 wasm，交给项目自带的 ffmpeg：取"中间一小段"并 ``-c copy`` 转封装，既快
又不需要重编码；产物落临时目录并缓存，供 Qt 播放器内联播放。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

PREVIEW_VERSION = "1.0.0"

#: 预览片段默认时长（秒）。VDH 取的是"一条分片"，HLS 分片通常是 2–6 秒。
DEFAULT_CLIP_SECONDS = 6.0

#: 小于这个体积的产物视为失败（避免把 0 字节 / 错误页当预览）。
MIN_PREVIEW_BYTES = 8 * 1024

#: 默认超时：HLS 取一条分片通常 2–8 秒；直链 Range 同样很快。
DEFAULT_TIMEOUT_S = 25.0

#: 送给 ffmpeg 的请求头白名单（其余丢弃，尤其 Host / Range / sec-*）。
_HEADER_ALLOW = (
    "referer",
    "origin",
    "user-agent",
    "accept",
    "accept-language",
    "cookie",
    "authorization",
)

_HEADER_NAMES = {
    "referer": "Referer",
    "origin": "Origin",
    "user-agent": "User-Agent",
    "accept": "Accept",
    "accept-language": "Accept-Language",
    "cookie": "Cookie",
    "authorization": "Authorization",
}


@dataclass
class PreviewSpec:
    """描述"要预览什么"。"""

    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    page_url: str = ""
    duration_s: Optional[float] = None
    is_manifest: bool = False
    media_type: str = ""
    is_live: bool = False
    quality: str = ""

    def cache_key(self) -> str:
        """同一段素材的预览可复用：URL（去掉签名参数）+ 起始点。"""
        from .bridge import normalize_url_key

        key = normalize_url_key(self.url) or self.url
        start = preview_start_seconds(self.duration_s, self.is_live)
        raw = f"{key}|{start:.1f}"
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


def preview_start_seconds(duration_s: Optional[float], is_live: bool = False) -> float:
    """对齐 VDH：点播取中间，直播从头（≈最后一条分片的意思）。"""
    if is_live:
        return 0.0
    try:
        duration = float(duration_s or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        return 0.0
    return max(0.0, round(duration / 2.0 - DEFAULT_CLIP_SECONDS / 2.0, 1))


def _clean_headers(headers: Optional[Mapping[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not headers:
        return out
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        name = key.strip().lower()
        if name not in _HEADER_ALLOW:
            continue
        text = value.strip()
        if not text or "\r" in text or "\n" in text:
            continue
        out[_HEADER_NAMES[name]] = text
    return out


def _append_input_options(
    cmd: list[str],
    spec: PreviewSpec,
    *,
    is_manifest: bool = False,
) -> None:
    """把请求头 / UA / Referer 放到 ``-i`` 之前。

    历史 bug：这些是输入选项，之前被追加到输出参数之后，ffmpeg 会直接报
    "Trailing option(s) found in the command" 并失败，表现为"hover 即黑屏"。
    """
    headers = _clean_headers(spec.headers)
    if headers:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    agent = headers.get("User-Agent")
    if agent:
        cmd += ["-user_agent", agent]
    referer = headers.get("Referer") or spec.page_url
    if referer:
        cmd += ["-referer", referer]
    if is_manifest:
        # HLS 的分片可能是 crypto/tls，显式放开这些协议，避免 "Protocol not on whitelist"。
        cmd += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto", "-allowed_extensions", "ALL"]


def build_preview_command(
    ffmpeg_path: str,
    spec: PreviewSpec,
    out_path: str,
    *,
    clip_seconds: float = DEFAULT_CLIP_SECONDS,
    start_seconds: Optional[float] = None,
    include_audio: bool = True,
    reencode: bool = False,
    seek_after_input: bool = False,
) -> list[str]:
    """构造 ffmpeg 命令：取中间一小段并转封装成 mp4。

    默认 ``-c copy``（不重编码，快且无损）；``reencode=True`` 时降级为
    小尺寸 H.264 重编码，用于 copy 失败的编码（如 HEVC/AC3 组合）。
    """
    start = preview_start_seconds(spec.duration_s, spec.is_live) if start_seconds is None else max(0.0, float(start_seconds))
    clip = max(1.0, float(clip_seconds))

    cmd: list[str] = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    cmd += ["-rw_timeout", "15000000"]
    if start > 0 and not seek_after_input:
        cmd += ["-ss", f"{start:.3f}"]
    _append_input_options(cmd, spec, is_manifest=spec.is_manifest)
    cmd += ["-i", spec.url]
    if start > 0 and seek_after_input:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-t", f"{clip:.3f}"]
    cmd += ["-map", "0:v:0?"]
    if include_audio:
        cmd += ["-map", "0:a:0?"]
    if reencode:
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-vf", "scale=trunc(min(1280,iw)/2)*2:-2",
        ]
        cmd += ["-c:a", "aac", "-b:a", "96k"] if include_audio else ["-an"]
    else:
        cmd += ["-c", "copy"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", out_path]
    return cmd


def _default_runner(cmd: list[str], timeout: float) -> "subprocess.CompletedProcess[bytes]":
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _preview_attempts(spec: PreviewSpec) -> list[dict]:
    """预览提取的降级链（越靠后越"重"，但成功率越高）。"""
    attempts: list[dict] = [
        {"include_audio": True, "reencode": False, "seek_after_input": False},
        {"include_audio": False, "reencode": False, "seek_after_input": False},
        {"include_audio": False, "reencode": True, "seek_after_input": False},
        {"include_audio": False, "reencode": True, "seek_after_input": True},
    ]
    if spec.is_live:
        # 直播没有可靠时间轴，全部从头取。
        for attempt in attempts:
            attempt["start_seconds"] = 0.0
    return attempts


def extract_preview(
    ffmpeg_path: str,
    spec: PreviewSpec,
    out_path: str | Path,
    *,
    clip_seconds: float = DEFAULT_CLIP_SECONDS,
    timeout: float = DEFAULT_TIMEOUT_S,
    runner: Optional[Callable[[list[str], float], "subprocess.CompletedProcess[bytes]"]] = None,
) -> Optional[Path]:
    """生成预览文件；成功返回路径，失败返回 None（不抛异常，可在 UI 线程调用）。

    按 ``_preview_attempts`` 逐级降级：copy+音频 → copy 仅视频 → 重编码 →
    重编码+输入后 seek。任何一级产出 ≥ ``MIN_PREVIEW_BYTES`` 即成功。
    """
    target = Path(out_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    run = runner or _default_runner
    for index, attempt in enumerate(_preview_attempts(spec)):
        _safe_unlink(target)
        cmd = build_preview_command(
            ffmpeg_path,
            spec,
            str(target),
            clip_seconds=clip_seconds,
            **attempt,
        )
        try:
            result = run(cmd, timeout)
            returncode = getattr(result, "returncode", 0)
            if returncode != 0:
                stderr = getattr(result, "stderr", b"") or b""
                print(
                    f"[PREVIEW] ffmpeg失败(尝试{index + 1}) rc={returncode}, "
                    f"stderr={stderr.decode('utf-8', errors='replace')[:400]}"
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[PREVIEW] ffmpeg异常(尝试{index + 1}): {exc}")
            continue
        try:
            if target.is_file() and target.stat().st_size >= MIN_PREVIEW_BYTES:
                if index:
                    print(f"[PREVIEW] 降级成功(尝试{index + 1}): {target.stat().st_size} B")
                return target
        except OSError:
            pass
    _safe_unlink(target)
    return None


FRAME_FPS = 12
FRAME_WIDTH = 176
FRAME_HEIGHT = 99
MAX_FRAMES = 96


def build_frames_command(
    ffmpeg_path: str,
    clip_path: str | Path,
    *,
    width: int = FRAME_WIDTH,
    height: int = FRAME_HEIGHT,
    fps: int = FRAME_FPS,
    max_frames: int = MAX_FRAMES,
) -> list[str]:
    """把预览片段解码成固定尺寸的 raw RGB 帧（stdout 输出，便于 UI 帧动画播放）。"""
    vf = (
        f"fps={int(fps)},"
        f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=increase,"
        f"crop={int(width)}:{int(height)}"
    )
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(clip_path),
        "-vf",
        vf,
        "-frames:v",
        str(int(max_frames)),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]


def extract_frames(
    ffmpeg_path: str,
    clip_path: str | Path,
    *,
    width: int = FRAME_WIDTH,
    height: int = FRAME_HEIGHT,
    fps: int = FRAME_FPS,
    max_frames: int = MAX_FRAMES,
    timeout: float = DEFAULT_TIMEOUT_S,
    runner: Optional[Callable[[list[str], float], "subprocess.CompletedProcess[bytes]"]] = None,
) -> Optional[tuple[int, int, int, list[bytes]]]:
    """从预览片段提取帧序列；返回 (width, height, fps, frames)，失败返回 None。"""
    if not clip_path:
        return None
    clip = Path(clip_path)
    try:
        if not clip.is_file() or clip.stat().st_size < MIN_PREVIEW_BYTES:
            return None
    except OSError:
        return None
    run = runner or _default_runner
    cmd = build_frames_command(
        ffmpeg_path, clip, width=width, height=height, fps=fps, max_frames=max_frames
    )
    try:
        result = run(cmd, timeout)
    except Exception as exc:  # noqa: BLE001
        print(f"[PREVIEW] 帧提取异常: {exc}")
        return None
    if getattr(result, "returncode", 1) != 0:
        stderr = getattr(result, "stderr", b"") or b""
        print(f"[PREVIEW] 帧提取失败: {stderr.decode('utf-8', errors='replace')[:300]}")
        return None
    data = getattr(result, "stdout", b"") or b""
    frame_size = int(width) * int(height) * 3
    if frame_size <= 0 or len(data) < frame_size:
        return None
    frames = [data[i : i + frame_size] for i in range(0, len(data) - frame_size + 1, frame_size)]
    if not frames:
        return None
    return int(width), int(height), int(fps), frames


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


class PreviewCache:
    """预览文件缓存：按 URL+起始点复用，超出数量淘汰最旧。"""

    def __init__(self, directory: str | Path, max_items: int = 42, ttl_s: float = 6 * 3600):
        self.directory = Path(directory)
        self.max_items = max(1, int(max_items))
        self.ttl_s = float(ttl_s)
        self._entries: dict[str, tuple[Path, float]] = {}

    def path_for(self, spec: PreviewSpec) -> Path:
        return self.directory / f"preview_{spec.cache_key()}.mp4"

    def get(self, spec: PreviewSpec) -> Optional[Path]:
        key = spec.cache_key()
        entry = self._entries.get(key)
        if entry is None:
            candidate = self.path_for(spec)
            if not candidate.is_file():
                return None
            try:
                if candidate.stat().st_size < MIN_PREVIEW_BYTES:
                    return None
            except OSError:
                return None
            entry = (candidate, time.time())
        path, stamp = entry
        if self.ttl_s > 0 and time.time() - stamp > self.ttl_s:
            self._entries.pop(key, None)
            return None
        if not path.is_file():
            self._entries.pop(key, None)
            return None
        self._entries[key] = (path, time.time())
        return path

    def put(self, spec: PreviewSpec, path: Path) -> Path:
        self._entries[spec.cache_key()] = (Path(path), time.time())
        self._evict()
        return Path(path)

    def _evict(self) -> None:
        if len(self._entries) <= self.max_items:
            return
        ordered = sorted(self._entries.items(), key=lambda kv: kv[1][1])
        for key, (path, _stamp) in ordered[: len(self._entries) - self.max_items]:
            self._entries.pop(key, None)
            _safe_unlink(path)

    def clear(self) -> None:
        for path, _stamp in list(self._entries.values()):
            _safe_unlink(path)
        self._entries.clear()


def ffmpeg_candidates() -> Iterable[str]:
    """按优先级给出可用的 ffmpeg 路径（项目自带优先）。"""
    yield str(Path(__file__).resolve().parent.parent / "ffmpeg.exe")
    yield "ffmpeg"


def resolve_ffmpeg() -> Optional[str]:
    for candidate in ffmpeg_candidates():
        if candidate == "ffmpeg":
            found = shutil.which("ffmpeg")
            if found:
                return found
            continue
        if Path(candidate).is_file():
            return candidate
    return None


def default_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "sourcecraft_vdh_preview"


def extract_thumbnail(
    ffmpeg_path: str,
    spec: PreviewSpec,
    out_path: str | Path,
    *,
    at_seconds: float = 1.0,
    timeout: float = 15.0,
    runner=None,
) -> Optional[Path]:
    """从视频/流里抽一帧当缩略图，输出 JPG。

    对齐 VDH：用网站现成封面图，没有的话就自己抽一帧。
    这里走 ffmpeg 直接抓第 at_seconds 秒的一帧，-frames:v 1 只取一帧。
    """
    target = Path(out_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    cmd: list[str] = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    cmd += ["-rw_timeout", "15000000"]
    if at_seconds > 0:
        cmd += ["-ss", f"{at_seconds:.3f}"]
    _append_input_options(cmd, spec, is_manifest=spec.is_manifest)
    cmd += ["-i", spec.url]
    cmd += ["-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "5", "-f", "image2", out_path]

    run = runner or _default_runner
    try:
        run(cmd, timeout)
    except Exception:
        _safe_unlink(target)
        return None
    try:
        if target.is_file() and target.stat().st_size >= 1024:
            return target
    except OSError:
        pass
    _safe_unlink(target)
    return None
