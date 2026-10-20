import asyncio
import re
import time
from pathlib import Path
from typing import Optional

from src.audio_codecs.audio_channels import OUTPUT_CHANNEL_MUSIC
from src.audio_codecs.music_decoder import MusicDecodeComplete, MusicDecodeError, MusicDecoder
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# FFmpeg 可解码的本地音频扩展名
AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wma",
    ".ape",
}


class MusicPlayer:
    def __init__(self):
        self._music_queue: Optional[asyncio.Queue] = None
        self._playback_task: Optional[asyncio.Task] = None
        self._lyric_task: Optional[asyncio.Task] = None
        self._decoder: Optional[MusicDecoder] = None
        self._decode_recovery_attempts = 0
        self._max_decode_recovery_attempts = 1
        self._decode_start_position = 0.0

        self.music_dir = self._resolve_music_dir()
        self._music_index_cache: Optional[dict] = None

        self.current_song = ""
        self.total_duration = 0
        self.is_playing = False
        self.paused = False
        self.current_position = 0
        self.start_play_time = 0
        self._pause_source: Optional[str] = None
        self._deferred_start_path: Optional[str] = None
        self._deferred_start_position: float = 0.0
        self._current_file_path: Optional[Path] = None

        self.lyrics = []
        self.current_lyric_index = -1
        self._last_lyric_text = ""

        self.app = None
        self.audio_codec = None
        self._initialize_app_reference()

        logger.info("音乐播放器已启用本地播放模式")

    def _initialize_app_reference(self):
        """
        初始化应用程序引用和 AudioCodec.
        """
        try:
            from src.application import Application

            self.app = Application.get_instance()
            self.audio_codec = getattr(self.app, "audio_codec", None)

            if not self.audio_codec:
                logger.warning("AudioCodec 未初始化，音乐播放可能不可用")

        except Exception as e:
            logger.warning(f"获取Application实例失败: {e}")
            self.app = None

    def _ensure_audio_codec(self):
        if self.audio_codec:
            return self.audio_codec
        self._initialize_app_reference()
        return self.audio_codec

    async def _run_on_app_loop(self, coro_factory):
        """
        确保音乐播放相关异步对象都在 Application 主事件循环中创建和使用.
        """
        if not self.app:
            self._initialize_app_reference()

        main_loop = getattr(self.app, "_main_loop", None) if self.app else None
        if not main_loop or main_loop.is_closed():
            return await coro_factory()

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is main_loop:
            return await coro_factory()

        future = asyncio.run_coroutine_threadsafe(coro_factory(), main_loop)
        return await asyncio.wrap_future(future)

    def _create_task(self, coro, name: str) -> asyncio.Task:
        if self.app and getattr(self.app, "running", False):
            try:
                task = self.app.spawn(coro, name=name)
                if task:
                    return task
            except Exception as e:
                logger.debug(f"使用应用任务池创建任务失败: {e}")

        return asyncio.create_task(coro, name=name)

    def _resolve_music_dir(self) -> Path:
        """
        确定本地音乐目录：优先读取配置，为空或无效时回退到项目根下 music/ 目录。
        """
        try:
            from src.utils.config_manager import ConfigManager
            from src.utils.resource_finder import get_project_root

            custom = ConfigManager.get_instance().get_config("MUSIC.MUSIC_DIR")
            path = Path(custom).expanduser() if custom else (get_project_root() / "music")
            path.mkdir(parents=True, exist_ok=True)
            return path
        except Exception as e:
            logger.warning(f"解析音乐目录失败，回退到项目根: {e}")
            try:
                from src.utils.resource_finder import get_project_root

                path = get_project_root() / "music"
                path.mkdir(parents=True, exist_ok=True)
                return path
            except Exception:
                from pathlib import Path

                return Path.cwd() / "music"

    def _refresh_music_dir_if_changed(self):
        """检测配置中的音乐目录是否变化，变化则更新目录并清空索引缓存."""
        try:
            from src.utils.config_manager import ConfigManager
            from src.utils.resource_finder import get_project_root

            custom = ConfigManager.get_instance().get_config("MUSIC.MUSIC_DIR")
            new_dir = Path(custom).expanduser() if custom else (get_project_root() / "music")
            if new_dir != self.music_dir:
                self.music_dir = new_dir
                self._music_index_cache = None
                logger.info(f"音乐目录已更新: {new_dir}")
        except Exception as e:
            logger.debug(f"刷新音乐目录失败: {e}")


    def _scan_music_files(self) -> dict:
        """
        递归扫描本地音乐目录，返回 {文件相对路径: 绝对路径} 索引（带缓存）。
        """
        self._refresh_music_dir_if_changed()
        if self._music_index_cache is not None:
            return self._music_index_cache

        index = {}
        try:
            for path in sorted(self.music_dir.rglob("*")):
                if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                    rel = path.relative_to(self.music_dir).as_posix()
                    index[rel] = path
        except Exception as e:
            logger.error(f"扫描本地音乐目录失败: {e}", exc_info=True)

        self._music_index_cache = index
        return index

    def _resolve_song_path(self, file_id: str) -> Optional[Path]:
        """
        根据歌曲 id（相对路径）解析到绝对路径；支持精确匹配、顶层文件名、去扩展名匹配。
        """
        file_id = (file_id or "").strip()
        if not file_id:
            return None

        index = self._scan_music_files()
        if file_id in index:
            return index[file_id]

        target = file_id.replace("\\", "/")
        for rel, path in index.items():
            if rel == target or rel == target + ".mp3":
                return path
            if Path(rel).stem.lower() == Path(file_id).stem.lower():
                return path
        return None

    async def get_local_playlist(self, force_refresh: bool = False) -> dict:
        """
        获取本地音乐列表.
        """
        if force_refresh:
            self._music_index_cache = None
        try:
            playlist = sorted(self._scan_music_files().keys())
            return {
                "status": "success",
                "message": f"本地音乐目录中有 {len(playlist)} 首音乐",
                "playlist": playlist,
                "total_count": len(playlist),
            }
        except Exception as e:
            logger.error(f"获取本地音乐列表失败: {e}", exc_info=True)
            return {"status": "error", "message": f"获取本地音乐列表失败: {e}", "playlist": []}

    async def search_local_music(self, query: str) -> dict:
        """
        搜索本地音乐.
        """
        query = query.strip().lower()
        results = [name for name in sorted(self._scan_music_files().keys()) if query in name.lower()]
        return {
            "status": "success" if results else "info",
            "message": "\n".join(results) if results else f"本地音乐目录中没有找到“{query}”",
            "results": results,
            "found_count": len(results),
        }

    async def play_local_song_by_id(self, file_id: str) -> dict:
        """
        根据歌曲 id（相对路径）播放本地音乐.
        """
        return await self._run_on_app_loop(lambda: self._play_local_song_by_id_impl(file_id))

    async def _play_local_song_by_id_impl(self, file_id: str) -> dict:
        file_path = self._resolve_song_path(file_id)
        if not file_path:
            return {"status": "error", "message": f"本地歌曲不存在: {file_id}"}
        await self._stop_impl(clear_queue=True)
        self.current_song = file_path.stem
        self.total_duration = 0
        self.lyrics = self._load_lyrics(file_path)
        self._decode_recovery_attempts = 0
        await self._start_playback(file_path)
        return {"status": "success", "message": f"开始播放: {self.current_song}"}

    async def get_position(self):
        if not self.is_playing or self.paused:
            return self.current_position

        current_pos = min(self.total_duration, time.time() - self.start_play_time) if self.total_duration else time.time() - self.start_play_time
        return current_pos

    async def get_progress(self):
        """
        获取播放进度百分比.
        """
        if self.total_duration <= 0:
            return 0
        position = await self.get_position()
        return round(position * 100 / self.total_duration, 1)

    async def _handle_playback_finished(self):
        """
        处理播放完成.
        """
        self.is_playing = False
        self.paused = False
        self.current_position = self.total_duration
        self._pause_source = None
        await self._stop_lyric_task(hide=False)
        if self.lyrics:
            await self._update_music_lyrics(f"播放完成: {self.current_song}")
            await asyncio.sleep(1.0)
            await self._hide_music_lyrics()
        else:
            await self._safe_update_ui(f"播放完成: {self.current_song}")
            await self._hide_music_lyrics()

    async def _start_playback(self, file_path, start_position: float = 0.0) -> bool:
        """
        启动本地音乐播放.
        """
        codec = self._ensure_audio_codec()
        if not codec:
            logger.error("AudioCodec 未初始化，无法播放音乐")
            return False

        path = Path(file_path)
        if not path.exists():
            logger.error(f"音乐文件不存在: {path}")
            return False

        await self._stop_playback(reset_state=False)
        await codec.clear_audio_queue(channel=OUTPUT_CHANNEL_MUSIC)

        self._music_queue = asyncio.Queue(maxsize=300)
        self._decoder = MusicDecoder()
        self._current_file_path = path
        self.current_position = max(0.0, float(start_position))
        self._decode_start_position = self.current_position
        self.start_play_time = time.time() - self.current_position
        self.is_playing = True
        self.paused = False
        self._pause_source = None

        started = await self._decoder.start_decode(path, self._music_queue, self.current_position)
        if not started:
            self.is_playing = False
            return False

        self._playback_task = self._create_task(self._playback_loop(), "music:playback")
        await self._start_lyric_task()
        return True

    async def _start_lyric_task(self):
        await self._stop_lyric_task(hide=False)
        self.current_lyric_index = -1
        self._last_lyric_text = ""

        # 无论是否有歌词，都显示音乐播放窗口；无歌词时只显示歌名并保持常驻
        await self._show_music_lyrics(f"♪ 正在播放：{self.current_song}")
        if self.lyrics:
            self._lyric_task = self._create_task(self._lyric_loop(), "music:lyrics")
        else:
            self._lyric_task = self._create_task(self._song_name_keepalive_loop(), "music:keepalive")

    async def _song_name_keepalive_loop(self):
        """
        无歌词时保持播放窗口常驻：周期性重新占用 TTS 区域显示歌名，
        避免被 TTS 播报结束的 hide_tts 定时器隐藏。
        """
        text = f"♪ 正在播放：{self.current_song}"
        try:
            while self.is_playing:
                await self._update_music_lyrics(text)
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"无歌词保持显示失败: {e}", exc_info=True)

    async def _stop_lyric_task(self, hide: bool = True):
        current_task = asyncio.current_task()
        if self._lyric_task and self._lyric_task is not current_task and not self._lyric_task.done():
            self._lyric_task.cancel()
            try:
                await self._lyric_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if self._lyric_task is not current_task:
            self._lyric_task = None
        self.current_lyric_index = -1
        self._last_lyric_text = ""
        if hide:
            await self._hide_music_lyrics()

    async def _lyric_loop(self):
        try:
            while self.is_playing and self.lyrics:
                if self.paused:
                    await asyncio.sleep(0.2)
                    continue

                position = await self.get_position()
                index = self._find_lyric_index(position)
                if index != -1 and index != self.current_lyric_index:
                    self.current_lyric_index = index
                    text = self.lyrics[index][1]
                    if text and text != self._last_lyric_text:
                        updated = await self._update_music_lyrics(text)
                        if updated:
                            self._last_lyric_text = text
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"歌词同步失败: {e}", exc_info=True)

    def _find_lyric_index(self, position: float) -> int:
        index = -1
        for i, (time_sec, _) in enumerate(self.lyrics):
            if time_sec <= position:
                index = i
            else:
                break
        return index

    async def _playback_loop(self):
        loop_decoder = self._decoder
        try:
            while self.is_playing and self._music_queue:
                pcm_data = await self._music_queue.get()
                if pcm_data is None:
                    await self._handle_playback_finished()
                    break
                if isinstance(pcm_data, MusicDecodeComplete):
                    await self._handle_decode_complete(pcm_data)
                    break
                if isinstance(pcm_data, MusicDecodeError):
                    await self._handle_decode_error(pcm_data)
                    break

                while self.is_playing and self.paused:
                    await asyncio.sleep(0.05)

                if not self.is_playing:
                    break

                codec = self._ensure_audio_codec()
                if not codec:
                    break
                await codec.write_pcm_direct(pcm_data, channel=OUTPUT_CHANNEL_MUSIC)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"音乐播放循环失败: {e}", exc_info=True)
            self.is_playing = False
            self.paused = False
            await self._stop_lyric_task(hide=True)
        finally:
            if loop_decoder and loop_decoder is self._decoder:
                await loop_decoder.stop()

    async def _handle_decode_complete(self, complete: MusicDecodeComplete):
        if self.total_duration <= 0:
            await self._handle_playback_finished()
            return

        expected_remaining = max(0.0, self.total_duration - self._decode_start_position)
        tolerance = max(5.0, min(15.0, self.total_duration * 0.03))
        if complete.decoded_duration + tolerance >= expected_remaining:
            await self._handle_playback_finished()
            return

        position = await self.get_position()
        reason = (
            f"音乐过早结束: 总时长={self.total_duration:.1f}s, "
            f"解码起点={self._decode_start_position:.1f}s, "
            f"期望剩余={expected_remaining:.1f}s, "
            f"实际解码={complete.decoded_duration:.1f}s, 容差={tolerance:.1f}s"
        )
        logger.warning(reason)
        await self._finish_decode_failure(position, "音乐文件时长异常或解码失败，已停止播放")

    async def _handle_decode_error(self, error: MusicDecodeError):
        position = await self.get_position()
        reason = f"音乐解码失败，准备处理恢复: {error.message}"
        logger.error(reason)
        await self._finish_decode_failure(position, "音乐文件解码失败，已停止播放")

    async def _finish_decode_failure(self, position: float, message: str):
        self.is_playing = False
        self.paused = False
        self._pause_source = None
        self.current_position = position
        await self._stop_lyric_task(hide=True)
        await self._safe_update_ui(message)

    async def _stop_playback(self, reset_state: bool = True):
        await self._stop_lyric_task(hide=reset_state)

        if self._decoder:
            await self._decoder.stop()
            self._decoder = None

        current_task = asyncio.current_task()
        if self._playback_task and self._playback_task is not current_task and not self._playback_task.done():
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if self._playback_task is not current_task:
            self._playback_task = None
        self._music_queue = None

        if reset_state:
            self.is_playing = False
            self.paused = False
            self._pause_source = None
            self.current_position = 0

    async def stop(self) -> dict:
        """
        停止播放.
        """
        return await self._run_on_app_loop(lambda: self._stop_impl(clear_queue=True))

    async def _stop_impl(self, clear_queue: bool = True) -> dict:
        try:
            await self._stop_playback(reset_state=True)
            codec = self._ensure_audio_codec()
            if clear_queue and codec:
                await codec.clear_audio_queue(channel=OUTPUT_CHANNEL_MUSIC)
            return {"status": "success", "message": "已停止播放音乐"}
        except Exception as e:
            logger.error(f"停止音乐失败: {e}", exc_info=True)
            return {"status": "error", "message": f"停止音乐失败: {e}"}

    async def pause(self, source: str = "manual") -> dict:
        """
        暂停播放.
        """
        return await self._run_on_app_loop(lambda: self._pause_impl(source))

    async def _pause_impl(self, source: str = "manual") -> dict:
        if not self.is_playing:
            return {"status": "info", "message": "当前没有正在播放的音乐"}
        if self.paused:
            return {"status": "success", "message": "音乐已暂停"}

        pause_position = await self.get_position()
        self.current_position = pause_position
        self.paused = True
        self._pause_source = source
        await self._stop_playback(reset_state=False)
        self.current_position = pause_position
        self.paused = True
        self._pause_source = source
        logger.info(f"音乐已暂停在 {pause_position:.1f}s，已停止解码器避免队列堆积")
        return {"status": "success", "message": "已暂停音乐"}

    async def resume(self) -> dict:
        """
        恢复播放.
        """
        return await self._run_on_app_loop(lambda: self._resume_impl())

    async def _resume_impl(self) -> dict:
        if not self.is_playing or not self._current_file_path:
            return {"status": "info", "message": "当前没有可恢复的音乐"}
        if not self.paused:
            return {"status": "success", "message": "音乐正在播放"}

        resume_position = self.current_position
        self.paused = False
        self._pause_source = None
        logger.info(f"从 {resume_position:.1f}s 恢复音乐播放，重新启动解码器")
        ok = await self._start_playback(self._current_file_path, resume_position)
        if not ok:
            return {"status": "error", "message": "恢复播放失败"}
        return {"status": "success", "message": "已恢复播放音乐"}

    async def seek(self, position: float) -> dict:
        """
        跳转到指定位置.
        """
        return await self._run_on_app_loop(lambda: self._seek_impl(position))

    async def _seek_impl(self, position: float) -> dict:
        if not self._current_file_path or not self._current_file_path.exists():
            return {"status": "info", "message": "当前没有正在播放的音乐"}

        position = max(0.0, float(position))
        if self.total_duration > 0:
            position = min(position, self.total_duration)

        was_paused = self.paused
        pause_source = self._pause_source
        ok = await self._start_playback(self._current_file_path, position)
        if not ok:
            return {"status": "error", "message": "跳转失败"}

        self.current_lyric_index = -1
        if was_paused:
            self.paused = True
            self._pause_source = pause_source
            self.current_position = position

        return {"status": "success", "message": f"已跳转到 {self._format_time(position)}"}

    async def get_lyrics(self) -> dict:
        """
        获取当前歌曲歌词.
        """
        if not self.lyrics:
            return {"status": "info", "message": "当前歌曲没有歌词", "lyrics": []}

        lyrics_text = []
        for time_sec, text in self.lyrics:
            time_str = self._format_time(time_sec)
            lyrics_text.append(f"[{time_str}] {text}" if time_sec > 0 else text)

        return {
            "status": "success",
            "message": f"获取到 {len(self.lyrics)} 行歌词",
            "lyrics": lyrics_text,
        }

    async def get_status(self) -> dict:
        """获取播放器状态."""
        position = await self.get_position()
        progress = await self.get_progress()

        if not self.is_playing:
            playing_state = "未播放"
        elif self.paused and self._pause_source == "manual":
            playing_state = "已暂停"
        elif self.is_playing:
            playing_state = "播放中"
        else:
            playing_state = "未知"

        duration_str = self._format_time(self.total_duration)
        position_str = self._format_time(position)

        return {
            "status": "success",
            "message": (
                f"当前歌曲: {self.current_song or '无'}\n"
                f"播放状态: {playing_state}\n"
                f"暂停来源状态: {self._pause_source or '无'}（TTS 播放时使用 ducking 降低音乐音量）\n"
                f"播放时长: {duration_str}\n"
                f"当前位置: {position_str}\n"
                f"播放进度: {progress}%\n"
                f"歌词可用: {'是' if len(self.lyrics) > 0 else '否'}"
            ),
        }

    def _load_lyrics(self, file_path: Path) -> list:
        """
        从同名 .lrc 侧车文件加载歌词（支持 .lrc/.LRC，UTF-8 优先，GBK 兜底）。
        无歌词文件或解析失败返回空列表。
        """
        try:
            lrc_path = file_path.with_suffix(".lrc")
            if not lrc_path.exists():
                alt = file_path.with_suffix(".LRC")
                if alt.exists():
                    lrc_path = alt
                else:
                    return []

            raw = lrc_path.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="ignore")
            return self._parse_lyrics(text)
        except Exception as e:
            logger.debug(f"加载歌词失败: {file_path.name}, {e}")
            return []

    def _parse_lyrics(self, lyrics_text: str):
        timed_lines = []
        plain_lines = []
        time_pattern = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]")
        meta_pattern = re.compile(r"^\[(ar|ti|al|by|offset|length|re|ve):.*\]$", re.IGNORECASE)

        for raw_line in (lyrics_text or "").splitlines():
            line = raw_line.strip()
            if not line or meta_pattern.match(line):
                continue

            matches = list(time_pattern.finditer(line))
            text = time_pattern.sub("", line).strip()
            if not text:
                continue
            if matches:
                for match in matches:
                    minutes = int(match.group(1))
                    seconds = int(match.group(2))
                    millis = int((match.group(3) or "0").ljust(3, "0"))
                    timed_lines.append((minutes * 60 + seconds + millis / 1000, text))
            else:
                plain_lines.append((0, text))

        lines = timed_lines if timed_lines else plain_lines
        return sorted(lines, key=lambda item: item[0])

    def _format_time(self, seconds: float) -> str:
        """
        将秒数格式化为 mm:ss 格式.
        """
        minutes = int(seconds) // 60
        seconds = int(seconds) % 60
        return f"{minutes:02d}:{seconds:02d}"

    def _get_ui_plugin(self):
        if not self.app or not getattr(self.app, "plugins", None):
            return None
        try:
            return self.app.plugins.get_plugin("ui")
        except Exception:
            return None

    async def _show_music_lyrics(self, text: str) -> bool:
        ui_plugin = self._get_ui_plugin()
        if not ui_plugin or not hasattr(ui_plugin, "show_music_lyrics"):
            return False
        try:
            return await ui_plugin.show_music_lyrics(text)
        except Exception as e:
            logger.debug(f"显示音乐歌词失败: {e}")
            return False

    async def _update_music_lyrics(self, text: str) -> bool:
        ui_plugin = self._get_ui_plugin()
        if not ui_plugin or not hasattr(ui_plugin, "update_music_lyrics"):
            return False
        try:
            return await ui_plugin.update_music_lyrics(text)
        except Exception as e:
            logger.debug(f"更新音乐歌词失败: {e}")
            return False

    async def _hide_music_lyrics(self):
        ui_plugin = self._get_ui_plugin()
        if not ui_plugin or not hasattr(ui_plugin, "hide_music_lyrics"):
            return
        try:
            await ui_plugin.hide_music_lyrics()
        except Exception as e:
            logger.debug(f"隐藏音乐歌词失败: {e}")

    async def _safe_update_ui(self, message: str):
        """
        安全地更新UI.
        """
        if not self.app or not hasattr(self.app, "set_chat_message"):
            return

        try:
            self.app.set_chat_message("assistant", message)
        except Exception as e:
            logger.error(f"更新UI失败: {e}")

    def __del__(self):
        """
        清理资源.
        """
        pass


# 全局音乐播放器实例
_music_player_instance = None


def get_music_player_instance() -> MusicPlayer:
    """
    获取音乐播放器单例.
    """
    global _music_player_instance
    if _music_player_instance is None:
        _music_player_instance = MusicPlayer()
        logger.info("[MusicPlayer] 创建音乐播放器单例实例")
    return _music_player_instance
