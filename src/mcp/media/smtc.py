# -*- coding: utf-8 -*-
"""Windows 系统媒体会话（SMTC）读取与控制.

原理与岛仔（xiaotang-daozai, MIT）一致：使用 Windows 10/11 的
``GlobalSystemMediaTransportControlsSessionManager``。任何按系统规范注册媒体
会话的播放器（网易云、QQ 音乐、酷狗、酷我、Spotify、Apple Music、浏览器等）
都会出现在这里，可以直接读取歌名/歌手/进度并发送播放控制，不需要监听音频，
也不需要注入目标进程。

实现要点：
- WinRT 调用放在**独立后台线程**，线程内初始化 MTA 套房，避免与 Qt 主线程的
  COM/STA 状态冲突；主线程只通过回调接收快照。
- 播放中的进度按 ``last_updated_time`` 外推（SMTC 的 position 是事件驱动的）。
- 控制命令经线程安全队列交给后台线程执行，调用方最多等待 3 秒。

非 Windows 或缺少 ``winrt-*`` 依赖时整个模块安静降级为不可用。
"""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_WINDOWS = sys.platform == "win32"
_AVAILABLE = False
_load_attempted = False
_runtime = None
_Manager = None
_Status = None


def _load_winrt() -> bool:
    """在 SMTC 后台线程中延迟加载 WinRT。

    winrt 运行时 + COM 类型库约占 60ms 启动成本；默认（不使用外部播放器歌词时）
    没必要在主线程导入。本函数只应在 ``_SmtcWorker`` 线程里首次调用。
    """
    global _AVAILABLE, _load_attempted, _runtime, _Manager, _Status

    if _AVAILABLE:
        return True
    if _load_attempted or not _WINDOWS:
        return False
    _load_attempted = True

    try:
        import winrt.runtime as runtime
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
        )
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
        )
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        logger.warning("PyWinRT 不可用，媒体会话功能已关闭: %s", exc)
        return False

    _runtime = runtime
    _Manager = Manager
    _Status = Status
    _AVAILABLE = True
    return True


# Windows.Media.MediaPlaybackType
_PLAYBACK_VIDEO = 2

# 媒体类型：只有 music 才允许取歌词、弹胶囊。
KIND_MUSIC = "music"
KIND_VIDEO = "video"
KIND_UNKNOWN = "unknown"

# 浏览器会把 B 站/YouTube 的视频错报成“音乐”，只能靠标题/专辑特征识别。
_VIDEO_TITLE_SUFFIXES = (
    "_哔哩哔哩_bilibili",
    " - 哔哩哔哩",
    "_bilibili",
    " - youtube",
    "_youtube",
    " - 抖音",
    "_抖音",
    " - douyin",
    " - 腾讯视频",
    " - 爱奇艺",
    " - 优酷",
    " - vimeo",
)
_VIDEO_ALBUMS = {"youtube", "bilibili", "哔哩哔哩", "抖音", "douyin"}
_BROWSER_MARKERS = ("msedge", "chrome", "firefox", "brave", "opera")
_MUSIC_SOURCE_MARKERS = (
    "cloudmusic",
    "qqmusic",
    "tencent.qqmusic",
    "kugou",
    "kwmusic",
    "spotify",
    "applemusic",
    "sodamusic",
    "bodian_pc",
)


def classify_media(
    source: str, title: str, artist: str, album: str, playback_type: int
) -> str:
    """严格判定媒体类型：music / video / unknown.

    浏览器经常把网页视频上报为 ``playback_type=MUSIC``，因此浏览器来源必须同时
    具备专辑和歌手才认为是音乐；B 站/YouTube 等站点特征直接判为视频。
    """
    title = (title or "").strip()
    if not title:
        return KIND_UNKNOWN
    if playback_type == _PLAYBACK_VIDEO:
        return KIND_VIDEO

    title_lower = title.lower()
    if any(title_lower.endswith(suffix) for suffix in _VIDEO_TITLE_SUFFIXES):
        return KIND_VIDEO

    source_lower = (source or "").lower()
    album_lower = (album or "").strip().lower()
    if album_lower in _VIDEO_ALBUMS:
        return KIND_VIDEO

    if any(marker in source_lower for marker in _MUSIC_SOURCE_MARKERS):
        return KIND_MUSIC

    if any(marker in source_lower for marker in _BROWSER_MARKERS):
        # 浏览器：没有专辑或歌手都视为“无法确认”，宁可不出歌词。
        if not album_lower or not (artist or "").strip():
            return KIND_UNKNOWN
        return KIND_MUSIC

    # 其它来源：至少要有歌手才能确认是音乐。
    return KIND_MUSIC if (artist or "").strip() else KIND_UNKNOWN

# SourceAppUserModelId -> 界面显示名。未收录的按去掉 .exe 后原样显示。
_SOURCE_LABELS = {
    "cloudmusic.exe": "网易云音乐",
    "qqmusic.exe": "QQ音乐",
    "tencent.qqmusic": "QQ音乐",
    "kugou.exe": "酷狗音乐",
    "kugou": "酷狗音乐",
    "kwmusic.exe": "酷我音乐",
    "spotify.exe": "Spotify",
    "applemusic.exe": "Apple Music",
    "applemusicwin": "Apple Music",
    "sodamusic.exe": "汽水音乐",
    "汽水音乐": "汽水音乐",
    "bodian_pc.exe": "波点音乐",
    "msedge.exe": "浏览器",
    "chrome.exe": "浏览器",
    "firefox.exe": "浏览器",
    "brave.exe": "浏览器",
    "opera.exe": "浏览器",
}


def source_label(source: str) -> str:
    """把播放器的 AppUserModelId 转成简短中文名。"""
    raw = (source or "").strip()
    if not raw:
        return "媒体"
    key = raw.lower()
    if key in _SOURCE_LABELS:
        return _SOURCE_LABELS[key]
    # Apple Music 的 AUMID 是包名，例如 AppleInc.AppleMusicWin_xxx
    if "applemusic" in key:
        return "Apple Music"
    if "kugou" in key:
        return "酷狗音乐"
    if "qqmusic" in key:
        return "QQ音乐"
    if key.endswith(".exe"):
        return raw[:-4]
    return raw


@dataclass
class NowPlaying:
    """一次媒体会话快照。"""

    available: bool = False
    source: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    playing: bool = False
    position: float = 0.0
    duration: float = 0.0
    playback_type: int = 0
    can_next: bool = False
    can_prev: bool = False
    can_seek: bool = False
    can_toggle: bool = False
    kind: str = KIND_UNKNOWN
    # 播放器是否真的上报了时间轴；false 时由本地时钟估算 position。
    timeline_reported: bool = False
    sampled_at: float = field(default_factory=time.monotonic)

    @property
    def is_music(self) -> bool:
        return self.kind == KIND_MUSIC

    def position_now(self) -> float:
        """按采样时刻外推当前播放位置（暂停时冻结）。"""
        pos = self.position
        if self.playing:
            pos += max(0.0, time.monotonic() - self.sampled_at)
        if self.duration > 0:
            pos = min(pos, self.duration)
        return max(0.0, pos)

    @property
    def track_key(self) -> tuple[str, str, str]:
        return (self.source, self.title, self.artist)

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "source": self.source,
            "source_label": source_label(self.source),
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "kind": self.kind,
            "timeline_reported": self.timeline_reported,
            "playing": self.playing,
            "position": round(self.position_now(), 1),
            "duration": round(max(0.0, self.duration), 1),
            "can_next": self.can_next,
            "can_prev": self.can_prev,
            "can_seek": self.can_seek,
            "can_toggle": self.can_toggle,
        }


class _Result:
    """控制命令的线程间结果。"""

    __slots__ = ("event", "value")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value = False

    def set(self, value: bool) -> None:
        self.value = bool(value)
        self.event.set()


class _SmtcWorker:
    """后台线程：轮询媒体会话、执行控制命令。"""

    def __init__(self, on_update: Callable[[Optional[NowPlaying]], None], interval: float = 1.0):
        self._on_update = on_update
        self._interval = max(0.2, float(interval))
        self._commands: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._current_source = ""
        self._last_sig = object()
        self._available = False
        # 部分播放器（典型是酷狗）不上报时间轴，用本地时钟估算进度。
        self._fallback_key: Optional[tuple[str, str, str]] = None
        self._fallback_position = 0.0
        self._fallback_anchor: Optional[float] = None
        self._fallback_playing = False
        self._thread = threading.Thread(
            target=self._run, name="smtc-worker", daemon=True
        )

    @property
    def available(self) -> bool:
        return self._available

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def control(self, action: str, seconds: Optional[float] = None, timeout: float = 3.0) -> bool:
        """提交控制命令，阻塞最多 ``timeout`` 秒等待后台线程结果。"""
        if not self._thread.is_alive():
            return False
        holder = _Result()
        try:
            self._commands.put_nowait((action, seconds, holder))
        except queue.Full:  # 正常不会发生
            return False
        return holder.value if holder.event.wait(timeout) else False

    # ---- 后台线程 ----

    def _run(self) -> None:
        try:
            # WinRT 在这里按需导入（不在主线程/启动路径）。
            if not _load_winrt():
                logger.info("媒体会话不可用（非 Windows 或缺少 PyWinRT）")
                return
            # 新线程尚未初始化 COM；MTA 不改变 Qt 主线程的套房状态。
            try:
                _runtime.init_apartment(_runtime.ApartmentType.MULTI_THREADED)
            except Exception as exc:
                logger.debug("SMTC COM 套房已存在或初始化失败，继续尝试: %s", exc)
            asyncio.run(self._loop())
        except Exception as exc:
            logger.warning("SMTC 后台线程退出: %s", exc)
        finally:
            self._available = False

    async def _loop(self) -> None:
        manager = None
        while not self._stop.is_set():
            try:
                if manager is None:
                    manager = await _Manager.request_async()  # type: ignore[name-defined]
                    self._available = True
                    logger.info("SMTC 媒体会话管理器已就绪")
                await self._drain_commands(manager)
                snapshot = await self._read_best(manager)
                self._fill_missing_timeline(snapshot)
                self._emit(snapshot)
            except Exception as exc:
                logger.debug("SMTC 读取失败: %s", exc)
                manager = None
                self._available = False
                self._fallback_key = None
                snapshot = None
                self._emit(None)
            # 没有媒体会话时放慢轮询，减少空闲开销；停止最多再等一个间隔。
            delay = self._interval if snapshot is not None else max(1.0, self._interval * 2)
            await asyncio.sleep(delay)

    async def _read_best(self, manager) -> Optional[NowPlaying]:
        # 常见情况：系统当前会话就是正在播放的音乐。先只读这一条，
        # 避免每轮轮询都枚举全部浏览器标签/播放器。
        current = None
        try:
            current = manager.get_current_session()
        except Exception:
            current = None

        current_source = ""
        if current is not None:
            try:
                current_source = str(
                    getattr(current, "source_app_user_model_id", "") or ""
                )
            except Exception:
                pass
            snapshot = await self._read_session(current)
            if (
                snapshot is not None
                and snapshot.kind == KIND_MUSIC
                and snapshot.playing
            ):
                return snapshot

        try:
            sessions = list(manager.get_sessions())
        except Exception:
            sessions = []
        if not sessions and current is not None:
            sessions = [current]

        # 多个会话同为“音乐暂停”时，优先系统当前会话，避免显示另一个后台标签页。
        best: Optional[tuple[int, int, NowPlaying]] = None
        for session in sessions:
            snapshot = await self._read_session(session)
            if snapshot is None:
                continue
            rank = self._rank(snapshot)
            tie = 0 if snapshot.source == current_source else 1
            if best is None or (rank, tie) < (best[0], best[1]):
                best = (rank, tie, snapshot)
        if best is None:
            return None
        return best[2]

    def _fill_missing_timeline(self, snapshot: Optional[NowPlaying], now: Optional[float] = None) -> None:
        """为不上报时间轴的播放器（典型是酷狗）用本地时钟估算播放进度。

        - 首次看到某首歌时从 0 开始计时；
        - 播放中转暂停时冻结，恢复后继续；
        - 一旦播放器开始上报真实进度，立即改回用真实值。
        """
        if snapshot is None:
            self._fallback_key = None
            self._fallback_anchor = None
            return

        now = time.monotonic() if now is None else now
        key = snapshot.track_key
        has_timeline = snapshot.timeline_reported

        if key != self._fallback_key:
            self._fallback_key = key
            self._fallback_position = snapshot.position if has_timeline else 0.0
            self._fallback_anchor = now if snapshot.playing else None
            self._fallback_playing = snapshot.playing
            if not has_timeline:
                snapshot.sampled_at = now
            logger.info(
                "媒体会话: %s / %s - %s | kind=%s pos=%.1f dur=%.1f timeline=%s",
                snapshot.source,
                snapshot.title,
                snapshot.artist,
                snapshot.kind,
                snapshot.position,
                snapshot.duration,
                "reported" if has_timeline else "estimated",
            )
            return

        if has_timeline:
            # 播放器恢复上报后，以真实时间轴为准。
            self._fallback_position = snapshot.position
            self._fallback_anchor = now if snapshot.playing else None
            self._fallback_playing = snapshot.playing
            return

        if snapshot.playing:
            if not self._fallback_playing or self._fallback_anchor is None:
                self._fallback_anchor = now
            self._fallback_playing = True
            if self._fallback_anchor is not None:
                snapshot.position = self._fallback_position + (
                    now - self._fallback_anchor
                )
                snapshot.sampled_at = now
        else:
            if self._fallback_playing and self._fallback_anchor is not None:
                self._fallback_position += now - self._fallback_anchor
            self._fallback_anchor = None
            self._fallback_playing = False
            snapshot.position = self._fallback_position
            snapshot.sampled_at = now

    @staticmethod
    def _rank(snapshot: NowPlaying) -> int:
        """音乐优先；视频排在未知之后，避免抢占正在播放的音乐。"""
        if snapshot.kind == KIND_MUSIC:
            return 0 if snapshot.playing else 1
        if snapshot.kind == KIND_VIDEO:
            return 3 if snapshot.playing else 5
        return 2 if snapshot.playing else 4

    async def _read_session(self, session) -> Optional[NowPlaying]:
        try:
            props = await asyncio.wait_for(
                session.try_get_media_properties_async(), timeout=1.5
            )
        except Exception:
            return None
        title = str(getattr(props, "title", "") or "").strip()
        if not title:
            return None
        artist = str(getattr(props, "artist", "") or "").strip()
        album = str(getattr(props, "album_title", "") or "").strip()
        try:
            playback_type = int(getattr(props, "playback_type", 0) or 0)
        except Exception:
            playback_type = 0

        playing = False
        can_next = can_prev = can_seek = can_toggle = False
        try:
            info = session.get_playback_info()
            playing = info.playback_status == _Status.PLAYING  # type: ignore[name-defined]
            controls = getattr(info, "controls", None)
            if controls is not None:
                can_next = bool(getattr(controls, "is_next_enabled", False))
                can_prev = bool(getattr(controls, "is_previous_enabled", False))
                can_seek = bool(getattr(controls, "is_playback_position_enabled", False))
                can_toggle = bool(
                    getattr(controls, "is_play_pause_toggle_enabled", False)
                )
            # 部分播放器只在上报的 PlaybackInfo 里给出媒体类型。
            if playback_type == 0:
                try:
                    playback_type = int(getattr(info, "playback_type", 0) or 0)
                except Exception:
                    pass
        except Exception:
            pass

        position = 0.0
        duration = 0.0
        timeline_reported = False
        try:
            timeline = session.get_timeline_properties()
            position = float(timeline.position.total_seconds())
            duration = float(timeline.end_time.total_seconds())
            if playing:
                last = getattr(timeline, "last_updated_time", None)
                if last is not None:
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    lag = (datetime.now(timezone.utc) - last).total_seconds()
                    if 0.0 <= lag < 86400.0:
                        position += lag
        except Exception:
            pass

        if duration > 0:
            position = min(position, duration)
        source = str(getattr(session, "source_app_user_model_id", "") or "")
        return NowPlaying(
            available=True,
            source=source,
            title=title,
            artist=artist,
            album=album,
            playing=playing,
            position=max(0.0, position),
            duration=max(0.0, duration),
            playback_type=playback_type,
            can_next=can_next,
            can_prev=can_prev,
            can_seek=can_seek,
            can_toggle=can_toggle,
            kind=classify_media(source, title, artist, album, playback_type),
            timeline_reported=(duration > 0 or position > 0),
        )

    def _emit(self, snapshot: Optional[NowPlaying]) -> None:
        signature = (
            None
            if snapshot is None
            else (
                snapshot.source,
                snapshot.title,
                snapshot.artist,
                snapshot.playing,
                snapshot.kind,
            )
        )
        # 播放中每帧都发（歌词需要进度）；暂停/无会话只在状态变化时发。
        if signature != self._last_sig or (snapshot is not None and snapshot.playing):
            self._last_sig = signature
            if snapshot is not None:
                self._current_source = snapshot.source
            try:
                self._on_update(snapshot)
            except Exception:
                logger.exception("SMTC 更新回调失败")

    async def _drain_commands(self, manager) -> None:
        while True:
            try:
                action, seconds, holder = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                session = self._find_session(manager)
                ok = False
                if session is not None:
                    ok = await self._apply(session, action, seconds)
            except Exception as exc:
                logger.debug("SMTC 控制命令失败 (%s): %s", action, exc)
                ok = False
            holder.set(ok)

    def _find_session(self, manager):
        """只控制最近一次上报给界面的那个会话。

        还没识别到会话时不回退到 ``GetCurrentSession``，避免“现在在放什么”
        还没有结果时，控制命令误打到视频或其它后台会话。
        """
        target = (self._current_source or "").lower()
        if not target:
            return None
        try:
            sessions = list(manager.get_sessions())
        except Exception:
            return None
        for session in sessions:
            source = str(getattr(session, "source_app_user_model_id", "") or "")
            if source.lower() == target:
                return session
        return None

    async def _apply(self, session, action: str, seconds: Optional[float]) -> bool:
        action = (action or "").strip().lower()
        if action in ("toggle", "play_pause", "playpause"):
            method = session.try_toggle_play_pause_async
        elif action == "play":
            method = session.try_play_async
        elif action == "pause":
            method = session.try_pause_async
        elif action in ("next", "skip", "skip_next"):
            method = session.try_skip_next_async
        elif action in ("previous", "prev", "back", "skip_previous"):
            method = session.try_skip_previous_async
        elif action == "stop":
            method = session.try_stop_async
        elif action == "seek":
            if seconds is None:
                return False
            result = await asyncio.wait_for(
                session.try_change_playback_position_async(
                    timedelta(seconds=max(0.0, float(seconds)))
                ),
                timeout=3.0,
            )
            return bool(result)
        else:
            logger.warning("未知的媒体控制动作: %s", action)
            return False
        try:
            result = await asyncio.wait_for(method(), timeout=3.0)
        except Exception:
            return False
        return bool(result)


class SmtcService:
    """SMTC 服务单例：启动/停止后台轮询，向上层提供快照和控制。"""

    def __init__(self) -> None:
        self._worker: Optional[_SmtcWorker] = None
        self._latest: Optional[NowPlaying] = None
        self._lock = threading.Lock()
        self._callback: Optional[Callable[[Optional[NowPlaying]], None]] = None

    @staticmethod
    def supported() -> bool:
        # 后台线程尚未尝试加载时先乐观返回 True（非 Windows 除外），
        # 避免启动早期 MCP 工具误报“平台不支持”；真正的可用性由
        # worker.available 在会话管理器就绪后给出。
        if not _WINDOWS:
            return False
        return _AVAILABLE or not _load_attempted

    @property
    def available(self) -> bool:
        return self._worker is not None and self._worker.available

    def start(self, on_update: Callable[[Optional[NowPlaying]], None]) -> bool:
        """启动后台轮询。``on_update`` 在后台线程被调用，调用方需自行切回主线程。"""
        if not _WINDOWS:
            logger.info("媒体会话功能不可用（非 Windows）")
            return False
        if _load_attempted and not _AVAILABLE:
            logger.info("媒体会话功能不可用（缺少 PyWinRT）")
            return False
        if self._worker is not None:
            return True
        self._callback = on_update

        def _update(snapshot: Optional[NowPlaying]) -> None:
            with self._lock:
                self._latest = snapshot
            if self._callback is not None:
                self._callback(snapshot)

        self._worker = _SmtcWorker(_update)
        self._worker.start()
        logger.info("媒体会话服务已启动")
        return True

    def stop(self) -> None:
        worker, self._worker = self._worker, None
        self._callback = None
        if worker is not None:
            worker.stop()
            logger.info("媒体会话服务已停止")
        with self._lock:
            self._latest = None

    def latest(self) -> Optional[NowPlaying]:
        with self._lock:
            return self._latest

    def control(self, action: str, seconds: Optional[float] = None, timeout: float = 3.0) -> bool:
        worker = self._worker
        if worker is None:
            return False
        # 服务层也坚持“只控制音乐”：视频/未知会话一律拒绝。
        latest = self.latest()
        if latest is None or latest.kind != KIND_MUSIC:
            return False
        return worker.control(action, seconds, timeout)


_service: Optional[SmtcService] = None
_service_lock = threading.Lock()


def get_smtc_service() -> SmtcService:
    global _service
    with _service_lock:
        if _service is None:
            _service = SmtcService()
        return _service
