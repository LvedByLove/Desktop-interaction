# -*- coding: utf-8 -*-
"""播放器媒体会话插件.

同时管理两类播放源：

- **本程序自己的播放器**（本地文件 / 在线搜索播放）：每 0.5 秒同步标题、
  歌手、进度和控制能力到播放面板；
- 外部播放器（Windows SMTC）：读取歌名/歌手并驱动歌词。

歌词只对严格判定为音乐的会话生效；视频/未知会话不弹胶囊。播放面板由
``MediaControlModel`` 驱动，支持上一首/播放暂停/下一首和进度拖动。
"""

from __future__ import annotations

import asyncio
import bisect
import sys
from pathlib import Path
from typing import Optional

from src.mcp.media.lyrics_external import ExternalLyrics
from src.mcp.media.smtc import KIND_MUSIC, NowPlaying, get_smtc_service, source_label
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class MediaSessionPlugin(Plugin):
    """SMTC 会话 → 胶囊歌词的桥接插件。"""

    name = "media_session"
    priority = 45  # 日历(40)之后、IoT/UI 之前；UI 的 setup 早已完成

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self._ui = None
        self._music_player = None
        self._service = None
        self._control_model = None
        self._lyrics = ExternalLyrics()

        self._last_snapshot: Optional[NowPlaying] = None
        self._local_active = False
        self._local_task: Optional[asyncio.Task] = None
        # 本地播放“上一首/下一首”是否可用：只在切歌时重新计算，
        # 避免 0.5s 状态轮询里反复扫描音乐目录。
        self._local_nav_key: Optional[tuple[str, str]] = None
        self._local_can_nav = False

        self._stopping = False
        self._shown = False
        self._track: Optional[tuple[str, str, str]] = None
        self._generation = 0
        self._lines: list[tuple[float, str]] = []
        self._times: list[float] = []
        self._line_index = -1
        self._last_text = ""
        self._fetch_task: Optional[asyncio.Task] = None

    # ---- 生命周期 ----

    async def setup(self, app) -> None:
        self.app = app

    async def start(self) -> None:
        try:
            self._ui = self.app.plugins.get_plugin("ui")
        except Exception:
            self._ui = None
        try:
            from src.mcp.music.music_player import get_music_player_instance

            self._music_player = get_music_player_instance()
        except Exception as exc:
            logger.debug("获取本地音乐播放器失败（不影响外部歌词）: %s", exc)

        display = getattr(self._ui, "display", None)
        self._control_model = getattr(display, "media_control_model", None)
        if self._control_model is not None:
            try:
                self._control_model.set_local_controller(self._control_local)
            except Exception as exc:
                logger.debug("绑定本地播放器控制失败: %s", exc)

        # 外部播放器仅 Windows 可用；本程序播放器的面板控制不受影响。
        if sys.platform == "win32":
            self._service = get_smtc_service()
            if self._control_model is not None:
                try:
                    self._control_model.set_service(self._service)
                except Exception as exc:
                    logger.debug("绑定播放控制面板失败: %s", exc)
            if not self._service.start(self._on_snapshot_thread):
                logger.info("媒体会话服务未启用，外部播放器歌词功能关闭")
            else:
                logger.info("外部播放器歌词已接入：复用胶囊 TTS 区域")
        else:
            logger.info("非 Windows 平台，外部播放器接入已跳过")

        # 本程序播放器状态同步（本地 / 在线音乐的面板与进度）
        if self._control_model is not None and self._music_player is not None:
            self._local_task = self._spawn(
                self._local_state_loop(), "media_session:local_state"
            )
        logger.info("媒体会话插件已启动")

    async def stop(self) -> None:
        self._stopping = True
        task, self._local_task = self._local_task, None
        if task is not None and not task.done():
            task.cancel()
        self._local_active = False
        self._last_snapshot = None
        if self._control_model is not None:
            try:
                self._control_model.update_local(None)
                self._control_model.update(None)
                self._control_model.set_local_controller(None)
                self._control_model.set_service(None)
            except Exception:
                pass
            self._control_model = None
        self._cancel_fetch()
        self._invalidate()
        service, self._service = self._service, None
        if service is not None:
            try:
                await asyncio.to_thread(service.stop)
            except Exception as exc:
                logger.debug("停止媒体会话服务失败: %s", exc)
        logger.info("媒体会话插件已停止")

    async def shutdown(self) -> None:
        await self.stop()

    # ---- 后台线程 → 主线程 ----

    def _on_snapshot_thread(self, snapshot: Optional[NowPlaying]) -> None:
        """后台线程回调：把快照调度回主事件循环。"""
        if self._stopping or not self.app:
            return
        try:
            self.app.schedule_command_nowait(self._handle_snapshot, snapshot)
        except Exception as exc:
            logger.debug("调度媒体快照失败: %s", exc)

    def _handle_snapshot(self, snapshot: Optional[NowPlaying]) -> None:
        """主线程：更新播放控制面板，并驱动歌词显示。"""
        if self._stopping:
            return

        self._last_snapshot = snapshot
        # 本程序播放器在播时，面板由本地状态驱动，外部快照只缓存不覆盖。
        if self._control_model is not None and not self._local_active:
            try:
                self._control_model.update(snapshot)
            except Exception as exc:
                logger.debug("更新播放控制面板失败: %s", exc)

        # 本程序自己的播放器在播时，胶囊归它所有。
        if self._own_music_playing():
            if self._shown or self._track is not None:
                self._shown = False
                self._invalidate()
            return

        if snapshot is None or not snapshot.title:
            if self._shown:
                self._shown = False
                self._invalidate()
                self._spawn(self._hide_lyrics(), "media_session:hide")
            return

        # 严格策略：只对已确认的音乐生效。视频/无法确认的会话不弹胶囊。
        if snapshot.kind != KIND_MUSIC:
            if self._shown:
                self._shown = False
                self._invalidate()
                self._spawn(self._hide_lyrics(), "media_session:hide")
            return

        key = snapshot.track_key
        if key != self._track:
            self._track = key
            self._generation += 1
            self._shown = True
            self._lines = []
            self._times = []
            self._line_index = -1
            self._last_text = ""
            self._cancel_fetch()
            generation = self._generation
            self._spawn(
                self._show_track(snapshot, generation), "media_session:track"
            )
            self._fetch_task = self._spawn(
                self._load_lyrics(snapshot, generation), "media_session:lyrics"
            )
            return

        if not self._shown or not snapshot.playing or not self._lines:
            return

        index = self._line_index_for(snapshot.position_now())
        if index < 0 or index == self._line_index:
            return
        text = self._lines[index][1]
        if not text or text == self._last_text:
            return
        self._line_index = index
        self._last_text = text
        self._spawn(
            self._push_lyric(text, self._generation), "media_session:line"
        )

    # ---- 显示 ----

    @staticmethod
    def _track_text(snapshot: NowPlaying) -> str:
        label = source_label(snapshot.source)
        text = f"♪ {label} · {snapshot.title}"
        if snapshot.artist:
            text += f" - {snapshot.artist}"
        return text

    async def _show_track(self, snapshot: NowPlaying, generation: int) -> None:
        if not self._ui or generation != self._generation:
            return
        try:
            await self._ui.show_music_lyrics(self._track_text(snapshot))
        except Exception as exc:
            logger.debug("显示外部播放器歌名失败: %s", exc)

    async def _push_lyric(self, text: str, generation: int) -> None:
        if not self._ui or generation != self._generation:
            return
        try:
            await self._ui.update_music_lyrics(text)
        except Exception as exc:
            logger.debug("更新外部播放器歌词失败: %s", exc)

    async def _hide_lyrics(self) -> None:
        if not self._ui:
            return
        try:
            await self._ui.hide_music_lyrics()
        except Exception as exc:
            logger.debug("收起外部播放器歌词失败: %s", exc)

    async def _load_lyrics(self, snapshot: NowPlaying, generation: int) -> None:
        lines = await self._lyrics.fetch(
            snapshot.title, snapshot.artist, snapshot.duration
        )
        if (
            generation != self._generation
            or self._track != snapshot.track_key
            or self._own_music_playing()
        ):
            return

        self._lines = lines
        self._times = [item[0] for item in lines]
        if not lines:
            # 严格匹配失败：保留已经显示的歌名，不显示任何歌词。
            return

        index = self._line_index_for(snapshot.position_now())
        if index >= 0 and self._lines[index][1]:
            self._line_index = index
            self._last_text = self._lines[index][1]
            await self._push_lyric(self._last_text, generation)
        else:
            await self._push_lyric(self._track_text(snapshot), generation)

    # ---- 本程序播放器（本地 / 在线） ----

    async def _local_state_loop(self) -> None:
        """每 0.5 秒把本地/在线播放器的状态同步到面板。"""
        try:
            while not self._stopping and getattr(self.app, "running", False):
                await self._poll_local_state()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("本地播放器状态同步退出: %s", exc)

    async def _poll_local_state(self) -> None:
        player = self._music_player
        model = self._control_model
        if player is None or model is None:
            return

        active = bool(
            getattr(player, "is_playing", False)
            and getattr(player, "current_song", "")
        )
        if not active:
            if self._local_active:
                self._local_active = False
                self._local_nav_key = None
                self._local_can_nav = False
                model.update_local(None)
                # 本地停止后，如果外部播放器还在播，立即把面板交还给它。
                model.update(self._last_snapshot)
            return

        self._local_active = True
        try:
            position = float(await player.get_position())
        except Exception:
            position = float(getattr(player, "current_position", 0.0) or 0.0)
        duration = float(getattr(player, "total_duration", 0.0) or 0.0)
        source = str(getattr(player, "current_source", "") or "")
        title = str(
            getattr(player, "current_title", "")
            or getattr(player, "current_song", "")
        )
        artist = str(getattr(player, "current_artist", "") or "")

        # 在线单曲没有“下一首”；本地按目录顺序切歌（随机模式不接管）。
        # 曲库索引只在切歌时刷新一次，且目录遍历放在线程池里，
        # 不再随 0.5s 轮询每 3 秒扫一次目录、卡住主线程。
        can_nav = False
        if source == "local" and not getattr(player, "_shuffle_mode", False):
            nav_key = (
                str(getattr(player, "_current_file_path", "") or ""),
                str(getattr(player, "current_song", "") or ""),
            )
            if nav_key != self._local_nav_key:
                self._local_nav_key = nav_key
                try:
                    index = await player.scan_music_files_async()
                    can_nav = len(index) > 1
                    path = getattr(player, "_current_file_path", None)
                    music_dir = getattr(player, "music_dir", None)
                    if can_nav and path is not None and music_dir is not None:
                        can_nav = path.relative_to(music_dir).as_posix() in index
                except Exception:
                    can_nav = False
                self._local_can_nav = can_nav
            else:
                can_nav = self._local_can_nav
        else:
            self._local_nav_key = None
            self._local_can_nav = False

        model.update_local(
            {
                "title": title,
                "artist": artist,
                "source_label": (
                    "本地音乐"
                    if source == "local"
                    else "在线音乐"
                    if source == "online"
                    else "音乐"
                ),
                "playing": not bool(getattr(player, "paused", False)),
                "position": position,
                "duration": duration,
                "can_next": can_nav,
                "can_prev": can_nav,
                "can_toggle": True,
                "can_seek": duration > 0,
            }
        )

    def _control_local(self, action: str, seconds: Optional[float] = None) -> None:
        """播放面板 → 本程序播放器；在主线程调度异步任务。"""
        player = self._music_player
        if player is None:
            return
        if action == "toggle":
            coro = (
                player.resume()
                if getattr(player, "paused", False)
                else player.pause()
            )
        elif action == "resume":
            coro = player.resume()
        elif action == "pause":
            coro = player.pause()
        elif action == "seek":
            coro = player.seek(float(seconds or 0.0))
        elif action == "next":
            coro = self._play_local_neighbor(1)
        elif action == "prev":
            coro = self._play_local_neighbor(-1)
        else:
            return
        self._spawn(coro, f"media_session:local_{action}")

    async def _play_local_neighbor(self, direction: int) -> None:
        """按本地音乐目录顺序播放上/下一首；在线单曲没有相邻曲目。"""
        player = self._music_player
        if player is None or getattr(player, "current_source", "") != "local":
            return
        try:
            playlist = await player.get_local_playlist()
        except Exception as exc:
            logger.debug("获取本地音乐列表失败: %s", exc)
            return
        items = list(playlist.get("playlist") or [])
        if len(items) < 2:
            return

        current = None
        path = getattr(player, "_current_file_path", None)
        music_dir = getattr(player, "music_dir", None)
        if path is not None and music_dir is not None:
            try:
                current = Path(path).relative_to(music_dir).as_posix()
            except ValueError:
                current = None
        if current not in items:
            stem = str(getattr(player, "current_song", "") or "")
            matches = [item for item in items if Path(item).stem == stem]
            current = matches[0] if len(matches) == 1 else None
        if current is None:
            return

        index = items.index(current)
        target = items[(index + direction) % len(items)]
        await player.play_local_song_by_id(target)

    # ---- 工具 ----

    def _line_index_for(self, position: float) -> int:
        if not self._times:
            return -1
        return bisect.bisect_right(self._times, max(0.0, position)) - 1

    def _own_music_playing(self) -> bool:
        return bool(
            self._music_player is not None
            and getattr(self._music_player, "is_playing", False)
        )

    def _cancel_fetch(self) -> None:
        task, self._fetch_task = self._fetch_task, None
        if task is not None and not task.done():
            task.cancel()

    def _invalidate(self) -> None:
        """让进行中的取词结果失效，并清空外部歌词状态。"""
        self._generation += 1
        self._cancel_fetch()
        self._track = None
        self._lines = []
        self._times = []
        self._line_index = -1
        self._last_text = ""

    def _spawn(self, coro, name: str) -> Optional[asyncio.Task]:
        if not self.app or not getattr(self.app, "running", False):
            try:
                coro.close()
            except Exception:
                pass
            return None
        return self.app.spawn(coro, name)
