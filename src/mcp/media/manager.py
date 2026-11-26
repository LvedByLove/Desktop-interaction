# -*- coding: utf-8 -*-
"""媒体会话 MCP 工具.

让语音助手能查询“现在在放什么歌”，并控制任意注册了 Windows 媒体会话的
播放器（网易云、QQ 音乐、酷狗、Spotify、浏览器等）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from src.utils.logging_config import get_logger

from .smtc import KIND_MUSIC, KIND_VIDEO, get_smtc_service, source_label

logger = get_logger(__name__)

_CONTROL_ACTIONS = {
    "toggle": "toggle",
    "play_pause": "toggle",
    "playpause": "toggle",
    "播放暂停": "toggle",
    "play": "play",
    "pause": "pause",
    "暂停": "pause",
    "播放": "play",
    "继续": "play",
    "继续播放": "play",
    "next": "next",
    "skip": "next",
    "switch": "next",
    "切歌": "next",
    "下一首": "next",
    "下一曲": "next",
    "previous": "previous",
    "prev": "previous",
    "上一首": "previous",
    "上一曲": "previous",
    "stop": "stop",
    "停止": "stop",
}


def _unavailable(message: str) -> str:
    return json.dumps(
        {"success": False, "available": False, "message": message},
        ensure_ascii=False,
    )


def _not_music_message(snapshot) -> str:
    if snapshot is None or not snapshot.title:
        return "当前没有正在播放的音乐"
    if snapshot.kind == KIND_VIDEO:
        return "当前正在播放视频，未执行音乐控制"
    return "无法确认当前媒体是音乐，未执行控制"


async def media_now_playing(args: Dict[str, Any]) -> str:
    """查询当前系统媒体会话的播放状态。"""
    service = get_smtc_service()
    if not service.supported():
        return _unavailable("当前平台不支持媒体会话读取（需要 Windows 10/11）")
    snapshot = service.latest()
    if snapshot is None or not snapshot.title:
        return json.dumps(
            {
                "success": True,
                "available": service.available,
                "playing": False,
                "message": "当前没有正在播放的媒体",
            },
            ensure_ascii=False,
        )
    data = snapshot.to_dict()
    if snapshot.kind == KIND_MUSIC:
        message = f"正在播放音乐：{snapshot.title}"
    elif snapshot.kind == KIND_VIDEO:
        message = f"正在播放视频：{snapshot.title}"
    else:
        message = f"当前媒体：{snapshot.title}"
    data.update(success=True, message=message)
    return json.dumps(data, ensure_ascii=False)


async def media_control(args: Dict[str, Any]) -> str:
    """控制当前媒体会话（播放/暂停、上一首、下一首、停止）。"""
    service = get_smtc_service()
    if not service.supported():
        return _unavailable("当前平台不支持媒体控制（需要 Windows 10/11）")

    raw = str(args.get("action", "") or "").strip()
    action = _CONTROL_ACTIONS.get(raw.lower(), _CONTROL_ACTIONS.get(raw, ""))
    if not action:
        return json.dumps(
            {
                "success": False,
                "message": "未知的控制动作，可用：toggle / play / pause / next / previous / stop",
            },
            ensure_ascii=False,
        )

    snapshot = service.latest()
    if snapshot is None or snapshot.kind != KIND_MUSIC:
        return json.dumps(
            {
                "success": False,
                "kind": snapshot.kind if snapshot else "unknown",
                "message": _not_music_message(snapshot),
            },
            ensure_ascii=False,
        )

    ok = await asyncio.to_thread(service.control, action)
    label = source_label(snapshot.source) if snapshot else "媒体"
    messages = {
        "toggle": "已切换播放/暂停",
        "play": "已继续播放",
        "pause": "已暂停",
        "next": "已切换到下一首",
        "previous": "已切换到上一首",
        "stop": "已停止播放",
    }
    message = messages.get(action, "控制指令已发送")
    if not ok:
        message = f"控制失败：{label} 没有响应该操作"
    return json.dumps(
        {"success": ok, "action": action, "message": message},
        ensure_ascii=False,
    )


async def media_seek(args: Dict[str, Any]) -> str:
    """把当前媒体播放位置跳转到指定秒数。"""
    service = get_smtc_service()
    if not service.supported():
        return _unavailable("当前平台不支持媒体控制（需要 Windows 10/11）")
    try:
        seconds = float(args.get("seconds", 0))
    except (TypeError, ValueError):
        seconds = 0.0
    seconds = max(0.0, seconds)

    snapshot = service.latest()
    if snapshot is None or snapshot.kind != KIND_MUSIC:
        return json.dumps(
            {
                "success": False,
                "kind": snapshot.kind if snapshot else "unknown",
                "message": _not_music_message(snapshot),
            },
            ensure_ascii=False,
        )

    ok = await asyncio.to_thread(service.control, "seek", seconds)
    if ok:
        message = f"已跳转到 {int(seconds // 60)} 分 {int(seconds % 60)} 秒"
    else:
        label = source_label(snapshot.source) if snapshot else "当前播放器"
        message = f"跳转失败：{label} 不支持或未响应进度调整"
    return json.dumps(
        {"success": ok, "position": round(seconds, 1), "message": message},
        ensure_ascii=False,
    )


class MediaToolsManager:
    """媒体会话工具注册器。"""

    def __init__(self) -> None:
        self._initialized = False

    def init_tools(self, add_tool, PropertyList, Property, PropertyType):
        logger.info("[MediaTools] 开始注册媒体会话工具")
        add_tool(
            (
                "media.now_playing",
                "【正在播放】查询 Windows 当前媒体会话正在播放的歌曲/视频。"
                "当用户问：现在在放什么歌、这首是什么歌、谁唱的、播放到哪里了、"
                "用的是哪个播放器 时调用本工具。"
                "返回 source/title/artist/album/playing/position/duration 等字段。"
                "English: Get the current system media session (now playing), "
                "including title, artist, playback state and position.",
                PropertyList([]),
                media_now_playing,
            )
        )
        add_tool(
            (
                "media.control",
                "【媒体控制】控制当前 Windows 媒体会话的播放。"
                "当用户说：暂停、继续播放、播放/暂停、下一首、上一首、切歌、停止播放 时调用。"
                "action 取值：toggle（播放/暂停切换）、play（继续播放）、pause（暂停）、"
                "next（下一首）、previous（上一首）、stop（停止）。"
                "可控制网易云、QQ 音乐、酷狗、Spotify、浏览器等任何注册了系统媒体会话的播放器。"
                "English: Control the current media session: toggle/play/pause/next/previous/stop.",
                PropertyList([Property("action", PropertyType.STRING)]),
                media_control,
            )
        )
        add_tool(
            (
                "media.seek",
                "【媒体进度】把当前播放器的播放位置跳转到指定秒数。"
                "当用户说：跳到 1 分 30 秒、拖到副歌、从 90 秒开始 时调用。"
                "seconds 为目标位置的秒数（从歌曲开头计算）。"
                "English: Seek the current media session to the given position in seconds.",
                PropertyList(
                    [
                        Property(
                            "seconds",
                            PropertyType.INTEGER,
                            min_value=0,
                            max_value=86400,
                        )
                    ]
                ),
                media_seek,
            )
        )
        self._initialized = True
        logger.info("[MediaTools] 媒体会话工具注册完成")


_manager = None


def get_media_tools_manager() -> MediaToolsManager:
    global _manager
    if _manager is None:
        _manager = MediaToolsManager()
    return _manager
