# -*- coding: utf-8 -*-
"""播放面板数据模型.

支持两类播放源，优先级如下：

1. **本程序自己的播放器**（本地文件 / 在线搜索播放），由插件每 0.5 秒同步；
2. 外部播放器的 Windows SMTC 会话。

QML 面板绑定这里的属性，并通过 ``toggle/next/previous/seekToFraction`` 控制。
外部视频/未知会话只展示信息，按钮全部禁用。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from src.mcp.media.smtc import KIND_MUSIC, NowPlaying, source_label
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class MediaControlModel(QObject):
    """播放面板属性 + 控制槽。"""

    # 按字段拆分 notify：每 0.5s 同步时只让真正变化的绑定重新求值，
    # 避免面板 16 个属性共用一个 changed 信号导致整面板重算。
    availableChanged = Signal()
    kindChanged = Signal()
    localActiveChanged = Signal()
    titleChanged = Signal()
    artistChanged = Signal()
    sourceLabelChanged = Signal()
    playingChanged = Signal()
    positionChanged = Signal()
    durationChanged = Signal()
    canNextChanged = Signal()
    canPreviousChanged = Signal()
    canToggleChanged = Signal()
    canSeekChanged = Signal()

    _FIELD_SIGNALS = {
        "available": "availableChanged",
        "kind": "kindChanged",
        "local_active": "localActiveChanged",
        "title": "titleChanged",
        "artist": "artistChanged",
        "source_label": "sourceLabelChanged",
        "playing": "playingChanged",
        "position": "positionChanged",
        "duration": "durationChanged",
        "can_next": "canNextChanged",
        "can_prev": "canPreviousChanged",
        "can_toggle": "canToggleChanged",
        "can_seek": "canSeekChanged",
    }

    # 拖动进度后先用目标值显示，等播放器追上；超过这个时间就交还真实值。
    _SEEK_SETTLE = 1.8
    _SEEK_TOLERANCE = 2.5

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._service = None
        self._local_control: Optional[Callable[[str, Optional[float]], None]] = None
        self._local_active = False

        self._available = False
        self._kind = ""
        self._title = ""
        self._artist = ""
        self._source_label = ""
        self._playing = False
        self._position = 0.0
        self._duration = 0.0
        self._can_next = False
        self._can_prev = False
        self._can_toggle = False
        self._can_seek = False

        self._seek_target = 0.0
        self._seek_until = 0.0

    # ---- 由插件调用 ----

    def set_service(self, service) -> None:
        self._service = service

    def set_local_controller(
        self, callback: Optional[Callable[[str, Optional[float]], None]]
    ) -> None:
        """插件提供的本地播放器控制入口（在主线程调度异步任务）。"""
        self._local_control = callback

    def _apply(self, **fields) -> None:
        """按字段写入并只发出真正变化的 notify 信号."""
        pending = []
        for name, value in fields.items():
            attr = f"_{name}"
            if getattr(self, attr) == value:
                continue
            setattr(self, attr, value)
            signal_name = self._FIELD_SIGNALS.get(name)
            if signal_name:
                pending.append(getattr(self, signal_name))
        for signal in pending:
            signal.emit()

    def update(self, snapshot: Optional[NowPlaying]) -> None:
        """更新外部 SMTC 会话状态；本程序播放器在播时忽略。"""
        if self._local_active:
            return
        if snapshot is None:
            self._reset()
            return

        is_music = snapshot.kind == KIND_MUSIC
        duration = max(0.0, float(snapshot.duration or 0.0))
        if snapshot.title != self._title:
            self._seek_until = 0.0
        self._apply(
            available=True,
            kind=snapshot.kind,
            title=snapshot.title,
            artist=snapshot.artist,
            source_label=source_label(snapshot.source),
            playing=bool(snapshot.playing),
            duration=duration,
            can_next=bool(is_music and snapshot.can_next),
            can_prev=bool(is_music and snapshot.can_prev),
            can_toggle=bool(is_music and snapshot.can_toggle),
            can_seek=bool(is_music and snapshot.can_seek and duration > 0),
            position=self._apply_pending(snapshot.position_now()),
        )

    def update_local(self, state: Optional[dict]) -> None:
        """更新本程序播放器状态；``None`` 表示已停止并让位给外部会话。"""
        if state is None:
            if not self._local_active:
                return
            self._apply(local_active=False)
            self._reset()
            return

        title = str(state.get("title") or "")
        duration = max(0.0, float(state.get("duration") or 0.0))
        if title != self._title:
            self._seek_until = 0.0
        self._apply(
            local_active=True,
            available=True,
            kind=KIND_MUSIC,
            title=title,
            artist=str(state.get("artist") or ""),
            source_label=str(state.get("source_label") or "音乐"),
            playing=bool(state.get("playing")),
            duration=duration,
            can_next=bool(state.get("can_next")),
            can_prev=bool(state.get("can_prev")),
            can_toggle=bool(state.get("can_toggle", True)),
            can_seek=bool(state.get("can_seek")) and duration > 0,
            position=self._apply_pending(float(state.get("position") or 0.0)),
        )

    def _reset(self) -> None:
        self._seek_until = 0.0
        self._apply(
            available=False,
            kind="",
            title="",
            artist="",
            source_label="",
            playing=False,
            position=0.0,
            duration=0.0,
            can_next=False,
            can_prev=False,
            can_toggle=False,
            can_seek=False,
        )

    def _apply_pending(self, reported: float) -> float:
        """拖动/跳转后先显示目标位置，避免播放器回报延迟导致滑块回弹。"""
        now = time.monotonic()
        if self._seek_until and now < self._seek_until:
            if abs(reported - self._seek_target) <= self._SEEK_TOLERANCE:
                self._seek_until = 0.0
                return reported
            return self._seek_target
        self._seek_until = 0.0
        return reported

    # ---- QML 属性 ----

    @Property(bool, notify=availableChanged)
    def available(self) -> bool:
        return self._available

    @Property(str, notify=kindChanged)
    def kind(self) -> str:
        return self._kind

    @Property(bool, notify=kindChanged)
    def isMusic(self) -> bool:
        return self._kind == KIND_MUSIC

    @Property(bool, notify=localActiveChanged)
    def localActive(self) -> bool:
        return self._local_active

    @Property(str, notify=titleChanged)
    def title(self) -> str:
        return self._title

    @Property(str, notify=artistChanged)
    def artist(self) -> str:
        return self._artist

    @Property(str, notify=sourceLabelChanged)
    def sourceLabel(self) -> str:
        return self._source_label

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self._playing

    @Property(float, notify=positionChanged)
    def position(self) -> float:
        return self._position

    @Property(float, notify=durationChanged)
    def duration(self) -> float:
        return self._duration

    @Property(bool, notify=canNextChanged)
    def canNext(self) -> bool:
        return self._can_next

    @Property(bool, notify=canPreviousChanged)
    def canPrevious(self) -> bool:
        return self._can_prev

    @Property(bool, notify=canToggleChanged)
    def canToggle(self) -> bool:
        return self._can_toggle

    @Property(bool, notify=canSeekChanged)
    def canSeek(self) -> bool:
        return self._can_seek

    # ---- 控制 ----

    @Slot()
    def toggle(self) -> None:
        self._send("toggle")

    @Slot()
    def next(self) -> None:
        self._send("next")

    @Slot()
    def previous(self) -> None:
        self._send("prev")

    @Slot(float)
    def seekToFraction(self, fraction: float) -> None:
        """QML 进度条拖动松手后调用，fraction 为 0..1。"""
        if not self._can_seek or self._duration <= 0:
            return
        fraction = max(0.0, min(1.0, float(fraction)))
        self.seek(fraction * self._duration)

    @Slot(float)
    def seek(self, seconds: float) -> None:
        if not self._can_seek or self._duration <= 0:
            return
        seconds = max(0.0, min(self._duration, float(seconds)))
        self._seek_target = seconds
        self._seek_until = time.monotonic() + self._SEEK_SETTLE
        self._apply(position=seconds)

        if self._local_active:
            if self._local_control is not None:
                self._local_control("seek", seconds)
            return
        service = self._service
        if service is not None:
            threading.Thread(
                target=self._run_seek,
                args=(service, seconds),
                name="media-control-seek",
                daemon=True,
            ).start()

    def _send(self, action: str) -> None:
        if self._kind != KIND_MUSIC:
            return
        if self._local_active:
            if self._local_control is not None:
                self._local_control(action, None)
            return
        service = self._service
        if service is None:
            return
        # 外部控制命令要等后台 SMTC 线程执行，放到线程里，避免卡住 QML。
        threading.Thread(
            target=self._run_control,
            args=(service, action),
            name=f"media-control-{action}",
            daemon=True,
        ).start()

    @staticmethod
    def _run_control(service, action: str) -> None:
        try:
            service.control(action)
        except Exception as exc:
            logger.debug("媒体控制失败 %s: %s", action, exc)

    @staticmethod
    def _run_seek(service, seconds: float) -> None:
        try:
            service.control("seek", seconds)
        except Exception as exc:
            logger.debug("媒体跳转失败: %s", exc)
