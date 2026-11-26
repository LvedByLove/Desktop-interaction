# -*- coding: utf-8 -*-
"""外部播放器媒体会话：SMTC 读取/控制 + 歌词匹配。"""

from .lyrics_external import ExternalLyrics
from .manager import get_media_tools_manager
from .smtc import (
    KIND_MUSIC,
    KIND_UNKNOWN,
    KIND_VIDEO,
    NowPlaying,
    SmtcService,
    classify_media,
    get_smtc_service,
    source_label,
)

__all__ = [
    "KIND_MUSIC",
    "KIND_UNKNOWN",
    "KIND_VIDEO",
    "ExternalLyrics",
    "NowPlaying",
    "SmtcService",
    "classify_media",
    "get_media_tools_manager",
    "get_smtc_service",
    "source_label",
]
