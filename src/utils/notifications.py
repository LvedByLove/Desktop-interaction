# -*- coding: utf-8 -*-
"""异步提醒的本地提示展示.

日程提醒、倒计时结束需要提示用户，但服务端的 ``listen/detect`` 通道
只接受唤醒词——发送长文本会收到服务端告警
"Detect is only for wake words, do not send long texts."。

因此提醒改为本地路径：只在主胶囊的 TTS 提示界面弹出文本，不做语音播报，
也不依赖网络连接。日程到点提醒会额外播放一次提示音
（``assets/music/cut_2sec.mp3``），在胶囊中固定展示 7 秒，支持鼠标点击提前收起；
提醒结束后再作为一条记录写入对话记录。
"""

from __future__ import annotations

import asyncio
import json

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# 日程到点提醒在胶囊里的固定展示时长（秒）
REMINDER_DISPLAY_SECONDS = 7.0

# 没有 Application.spawn 时，保持提醒音任务的强引用，避免被 GC 提前回收
_pending_sound_tasks: set[asyncio.Task] = set()


def _parse_payload(payload: str) -> tuple[str, bool]:
    """解析提醒载荷，返回 ``(展示文本, 是否日程到点提醒)``。

    提醒服务传入的可能是带 type/message 字段的 JSON；倒计时等其它
    通知传入的是纯文本。只有 ``type == "calendar_reminder"`` 才是
    需要提示音的日程到点提醒（启动时的当日日程摘要不响铃）。
    """
    text = str(payload or "").strip()
    if not text:
        return "", False
    if not text.startswith("{"):
        return text, False

    try:
        data = json.loads(text)
    except Exception:
        return text, False
    if not isinstance(data, dict):
        return text, False

    is_reminder = data.get("type") == "calendar_reminder"
    message = str(data.get("message") or "").strip()
    if message:
        return message, is_reminder

    event = data.get("event")
    if isinstance(event, dict):
        # 提醒内容直接使用 AI 创建日程时给出的标题，不加任何前缀
        title = str(event.get("title") or "").strip()
        if title:
            return title, is_reminder
    return text, is_reminder


def _play_reminder_sound(app) -> None:
    """异步播放日程提醒音效；失败静默降级，不影响文本提示。"""
    try:
        from src.utils.hold_talk_sound import HoldTalkSound

        coro = HoldTalkSound().play("reminder")
        spawn = getattr(app, "spawn", None) if app is not None else None
        if spawn is not None:
            if spawn(coro, name="calendar:reminder_sound") is not None:
                return
        task = asyncio.create_task(coro)
        _pending_sound_tasks.add(task)
        task.add_done_callback(_pending_sound_tasks.discard)
    except Exception as e:
        logger.debug(f"播放日程提醒音效失败: {e}")


async def announce(payload: str) -> None:
    """在主胶囊的 TTS 提示界面弹出一条提醒（只展示，不发声）。"""
    text, is_reminder = _parse_payload(payload)
    if not text:
        return

    # 复用正常 TTS 的胶囊外观
    try:
        from src.application import Application

        app = Application.get_instance()
        ui = app.plugins.get_plugin("ui") if app is not None else None
        display = getattr(ui, "display", None) if ui is not None else None
        if display is not None and hasattr(display, "show_tts"):
            show_reminder = (
                getattr(display, "show_reminder", None) if is_reminder else None
            )
            if show_reminder is not None:
                # GUI 显示层：计入对话记录、固定展示、可点击提前收起
                await show_reminder(text, REMINDER_DISPLAY_SECONDS)
            else:
                try:
                    # 日程提醒计入对话记录；其它临时提示不写入 transcript
                    await display.show_tts(text, record=is_reminder)
                except TypeError:
                    await display.show_tts(text)
                if hasattr(display, "hide_tts"):
                    delay = (
                        REMINDER_DISPLAY_SECONDS
                        if is_reminder
                        else max(4.0, min(20.0, 2.0 + len(text) * 0.25))
                    )
                    await display.hide_tts(delay)
            # 日程到点提醒：文本出现的同时响一声提示音
            if is_reminder:
                _play_reminder_sound(app)
            logger.info(f"已显示提醒: {text[:40]}")
        else:
            logger.warning("无法显示提醒：主胶囊显示组件不可用")
    except Exception as e:
        logger.debug(f"展示提醒文本失败: {e}")
