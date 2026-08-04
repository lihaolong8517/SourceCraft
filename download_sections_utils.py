"""Utilities for yt-dlp --download-sections validation.

This module intentionally contains no UI dependencies so it can be unit tested.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


def split_time_range_tokens(content: str) -> Tuple[str, str]:
    text = str(content or "").strip()
    if not text:
        return "", ""
    start_idx = 1 if text.startswith("-") else 0
    sep_idx = text.find("-", start_idx)
    if sep_idx <= 0:
        return "", ""
    start = text[:sep_idx].strip()
    end = text[sep_idx + 1 :].strip()
    return start, end


def parse_time_token_to_seconds(token: str) -> Optional[int]:
    text = str(token or "").strip()
    if not text:
        return None

    negative = text.startswith("-")
    if negative:
        text = text[1:].strip()
    if not text:
        return None

    if text.isdigit():
        value = int(text)
        return -value if negative else value

    parts = text.split(":")
    if len(parts) not in (2, 3):
        return None
    if not all(part.isdigit() for part in parts):
        return None

    nums = [int(part) for part in parts]
    # SS in mm:ss/hh:mm:ss must be < 60
    if nums[-1] >= 60:
        return None

    if len(nums) == 2:
        total = nums[0] * 60 + nums[1]
    else:
        # MM in hh:mm:ss must be < 60
        if nums[1] >= 60:
            return None
        total = nums[0] * 3600 + nums[1] * 60 + nums[2]

    return -total if negative else total


def validate_download_section(section: str) -> Tuple[bool, str, str]:
    """Validate one --download-sections token.

    Returns:
      (is_valid, warning_message, section_type)
      section_type is one of: unknown, time_range, chapter_name
    """
    is_valid = True
    warning_msg = ""
    section_type = "unknown"

    section = str(section or "").strip()
    if not section:
        return False, "时间段不能为空", "unknown"

    if section.startswith("*"):
        section_type = "time_range"
        if section == "*from-url":
            return True, "", section_type

        content = section[1:]
        start, end = split_time_range_tokens(content)
        if not start or not end:
            return False, "时间范围格式应为 *START-END", section_type

        start_seconds = parse_time_token_to_seconds(start)
        if start_seconds is None:
            return False, "开始时间格式不正确", section_type

        end_is_inf = (end.lower() == "inf")
        end_seconds = None if end_is_inf else parse_time_token_to_seconds(end)
        if not end_is_inf and end_seconds is None:
            return False, "结束时间格式不正确", section_type

        # Only compare when both are in the same direction (both absolute or both relative-from-end).
        if end_seconds is not None:
            comparable = (
                (start_seconds >= 0 and end_seconds >= 0)
                or (start_seconds < 0 and end_seconds < 0)
            )
            if comparable and end_seconds <= start_seconds:
                return False, "结束时间应大于开始时间", section_type

        return is_valid, warning_msg, section_type

    section_type = "chapter_name"
    try:
        re.compile(section)
    except re.error as exc:
        return False, f"章节正则表达式无效: {exc}", section_type
    return True, "", section_type
