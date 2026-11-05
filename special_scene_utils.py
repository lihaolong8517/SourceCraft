"""Utilities for building special-scene yt-dlp options.

This module is intentionally UI-agnostic so it can be unit tested.
"""

from __future__ import annotations

from typing import Optional

#: Platforms where yt-dlp supports ``--live-from-start``
#: (yt-dlp README: experimental, only supported for YouTube/Twitch/TVer/mellow-fan).
#: Passing the flag to other platforms (e.g. bilibili live) makes yt-dlp raise
#: "no formats that can be downloaded from the start", so it must be dropped.
LIVE_FROM_START_PLATFORMS = (
    ("youtube.com", "YouTube"),
    ("youtu.be", "YouTube"),
    ("twitch.tv", "Twitch"),
    ("tver.jp", "TVer"),
    ("mellow-fan.com", "mellow-fan"),
)


def build_wait_for_video_arg(
    min_seconds: int,
    use_range: bool = False,
    max_seconds: Optional[int] = None,
    default_seconds: int = 60,
) -> str:
    """Build value for ``--wait-for-video``.

    Returns either:
    - ``MIN`` when ``use_range`` is False
    - ``MIN-MAX`` when ``use_range`` is True
    """
    try:
        min_sec = int(min_seconds)
    except Exception:
        min_sec = int(default_seconds)
    if min_sec <= 0:
        min_sec = int(default_seconds)

    if not use_range:
        return str(min_sec)

    try:
        max_raw = min_sec if max_seconds is None else int(max_seconds)
    except Exception:
        max_raw = min_sec
    if max_raw < min_sec:
        max_raw = min_sec
    return f"{min_sec}-{max_raw}"


def fragment_unavailable_flag(skip_unavailable: bool) -> str:
    """Map UI state to fragment availability strategy."""
    if skip_unavailable:
        return "--skip-unavailable-fragments"
    return "--abort-on-unavailable-fragments"


def live_from_start_supported(url: Optional[str]) -> Optional[str]:
    """Return the platform name when ``--live-from-start`` is supported for the URL.

    yt-dlp only supports ``--live-from-start`` for YouTube, Twitch, TVer, and
    mellow-fan.  For other platforms (e.g. bilibili live) yt-dlp raises
    "no formats that can be downloaded from the start", so the option must be
    dropped.  Returns ``None`` for empty URLs and unsupported platforms.
    """
    if not url:
        return None
    lowered = str(url).lower()
    for needle, platform in LIVE_FROM_START_PLATFORMS:
        if needle in lowered:
            return platform
    return None

