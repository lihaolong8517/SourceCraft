"""Utilities for building special-scene yt-dlp options.

This module is intentionally UI-agnostic so it can be unit tested.
"""

from __future__ import annotations

from typing import Optional


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

