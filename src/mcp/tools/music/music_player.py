import asyncio
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

from src.audio_codecs.audio_channels import OUTPUT_CHANNEL_MUSIC
from src.audio_codecs.music_decoder import MusicDecodeComplete, MusicDecodeError, MusicDecoder
from src.mcp.tools.music.music_api import MusicApiClient
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class MusicPlayer:
    def __init__(self):
        self._music_queue: Optional[asyncio.Queue] = None
        self._playback_task: Optional[asyncio.Task] = None
        self._lyric_task: Optional[asyncio.Task] = None
        self._decoder: Optional[MusicDecoder] = None
        self._decode_recovery_attempts = 0
        self._max_decode_recovery_attempts = 1
        self._decode_start_position = 0.0
        self._api = MusicApiClient()

        self.cache_dir = Path(tempfile.gettempdir()) / "xiaozhi_music_cache"
        self.temp_cache_dir = self.cache_dir / "temp"
        self._init_cache_dirs()

        self.current_song = ""
        self.current_url = ""
        self.song_id = ""
        self.total_duration = 0
        self.is_playing = False
        self.paused = False
        self.current_position = 0
        self.start_play_time = 0
        self._pause_source: Optional[str] = None
        self._deferred_start_path: Optional[str] = None
        self._deferred_start_position: float = 0.0
        self._current_file_path: Optional[Path] = None
        self._pending_search_play_task: Optional[asyncio.Task] = None
        self._pending_search_play_name = ""

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

    def _init_cache_dirs(self):
        """
        初始化缓存目录.
        """
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.temp_cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"音乐缓存目录初始化完成: {self.cache_dir}")
        except Exception as e:
            logger.error(f"创建缓存目录失败: {e}")

    def _clean_temp_cache(self):
        """
        清理临时缓存文件.
        """
        try:
            if not self.temp_cache_dir.exists():
                return
            for file_path in self.temp_cache_dir.glob("*"):
                try:
                    if file_path.is_file():
                        file_path.unlink()
                except Exception as e:
                    logger.debug(f"删除临时缓存文件失败: {file_path.name}, {e}")
        except Exception as e:
            logger.debug(f"清理临时音乐缓存失败: {e}")

    async def search_music(self, query: str) -> dict:
        """
        搜索在线音乐.
        """
        try:
            results = await self._api.search_music(query)
            if not results:
                return {
                    "status": "info",
                    "message": f"没有找到与“{query}”匹配的歌曲",
                    "results": [],
                    "found_count": 0,
                }

            lines = []
            for index, item in enumerate(results[:10], 1):
                duration = self._format_time(item.get("duration") or 0)
                lines.append(
                    f"{index}. {item.get('title', '未知歌曲')} - {item.get('artist', '未知歌手')} [{duration}]"
                )

            return {
                "status": "success",
                "message": "搜索结果:\n" + "\n".join(lines),
                "results": results,
                "found_count": len(results),
            }
        except Exception as e:
            logger.error(f"搜索音乐失败: {e}", exc_info=True)
            return {"status": "error", "message": f"搜索音乐失败: {e}", "results": []}

    async def get_local_playlist(self, force_refresh: bool = False) -> dict:
        """
        获取本地音乐缓存列表.
        """
        try:
            files = sorted(self.cache_dir.glob("*.mp3"))
            playlist = [file.stem for file in files]
            return {
                "status": "success",
                "message": f"本地缓存中有 {len(playlist)} 首音乐",
                "playlist": playlist,
                "total_count": len(playlist),
            }
        except Exception as e:
            logger.error(f"获取本地音乐缓存失败: {e}", exc_info=True)
            return {"status": "error", "message": f"获取本地音乐缓存失败: {e}", "playlist": []}

    async def search_local_music(self, query: str) -> dict:
        """
        搜索本地音乐缓存.
        """
        query = query.strip().lower()
        playlist = await self.get_local_playlist()
        results = [name for name in playlist.get("playlist", []) if query in name.lower()]
        return {
            "status": "success" if results else "info",
            "message": "\n".join(results) if results else f"本地缓存中没有找到“{query}”",
            "results": results,
            "found_count": len(results),
        }

    async def play_local_song_by_id(self, file_id: str) -> dict:
        """
        根据本地缓存文件名播放歌曲.
        """
        return await self._run_on_app_loop(lambda: self._play_local_song_by_id_impl(file_id))

    async def _play_local_song_by_id_impl(self, file_id: str) -> dict:
        file_path = self.cache_dir / f"{file_id}.mp3"
        if not file_path.exists():
            return {"status": "error", "message": f"本地歌曲不存在: {file_id}"}
        await self._stop_impl(clear_queue=True)
        self.current_song = file_path.stem
        self.total_duration = 0
        self.lyrics = []
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

    async def search_and_play(self, song_name: str) -> dict:
        """
        搜索并播放歌曲.
        """
        return await self._run_on_app_loop(lambda: self._search_and_play_with_fast_response(song_name))

    async def _search_and_play_with_fast_response(self, song_name: str) -> dict:
        song_name = song_name.strip()
        if not song_name:
            return {"status": "error", "message": "请告诉我要播放的歌曲名称"}

        if self._pending_search_play_task and not self._pending_search_play_task.done():
            if self._pending_search_play_name == song_name:
                return {
                    "status": "success",
                    "message": f"《{song_name}》正在缓存，准备好后会自动播放",
                }
            self._pending_search_play_task.cancel()
            try:
                await self._pending_search_play_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        task = self._create_task(
            self._search_and_play_impl(song_name),
            f"music:search_and_play:{song_name}",
        )
        if not task:
            return await self._search_and_play_impl(song_name)

        def _clear_pending(done_task: asyncio.Task):
            if self._pending_search_play_task is done_task:
                self._pending_search_play_task = None
                self._pending_search_play_name = ""
                try:
                    result = done_task.result()
                    if isinstance(result, dict) and result.get("status") != "success":
                        self._create_task(
                            self._safe_update_ui(result.get("message", "音乐播放失败")),
                            "music:pending_status",
                        )
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    self._create_task(
                        self._safe_update_ui(f"音乐播放失败: {e}"),
                        "music:pending_error",
                    )

        task.add_done_callback(_clear_pending)
        self._pending_search_play_task = task
        self._pending_search_play_name = song_name
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
            return result
        except asyncio.TimeoutError:
            return {
                "status": "success",
                "message": f"正在搜索并缓存《{song_name}》，准备好后会自动播放",
            }

    async def _search_and_play_impl(self, song_name: str) -> dict:
        song_name = song_name.strip()
        if not song_name:
            return {"status": "error", "message": "请告诉我要播放的歌曲名称"}

        try:
            search_result = await self.search_music(song_name)
            results = search_result.get("results") or []
            if not results:
                return search_result

            song = results[0]
            music_id = int(song["id"])
            title = song.get("title") or song_name
            artist = song.get("artist") or "未知歌手"
            duration = int(song.get("duration") or 0)

            filename = f"{music_id}_{title}_{artist}.mp3"
            file_path = await self._api.download_music(music_id, self.cache_dir, filename)

            lyrics_text = ""
            try:
                lyrics_text = await self._api.get_lyrics(music_id)
            except Exception as e:
                logger.debug(f"获取歌词失败: {e}")

            await self._stop_impl(clear_queue=True)

            self.current_song = f"{title} - {artist}"
            self.current_url = self._api.build_file_url(music_id)
            self.song_id = str(music_id)
            self.total_duration = duration
            self.current_position = 0
            self.lyrics = self._parse_lyrics(lyrics_text)
            self._decode_recovery_attempts = 0

            ok = await self._start_playback(file_path)
            if not ok:
                return {"status": "error", "message": f"播放失败: {self.current_song}"}

            if asyncio.current_task() is self._pending_search_play_task:
                await self._safe_update_ui(f"正在播放: {self.current_song}")

            return {
                "status": "success",
                "message": f"正在播放: {self.current_song}",
            }
        except Exception as e:
            logger.error(f"搜索并播放歌曲失败: {e}", exc_info=True)
            return {"status": "error", "message": f"搜索并播放歌曲失败: {e}"}

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
        if not self.lyrics:
            await self._safe_update_ui(f"正在播放: {self.current_song}")
        return True

    async def _start_lyric_task(self):
        await self._stop_lyric_task(hide=False)
        self.current_lyric_index = -1
        self._last_lyric_text = ""
        if not self.lyrics:
            return

        await self._show_music_lyrics(f"♪ 正在播放：{self.current_song}")
        self._lyric_task = self._create_task(self._lyric_loop(), "music:lyrics")

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
        recovered = await self._recover_from_decode_failure(reason, position)
        if not recovered:
            await self._finish_decode_failure(position, "音乐文件时长异常或解码失败，已停止播放")

    async def _handle_decode_error(self, error: MusicDecodeError):
        position = await self.get_position()
        reason = f"音乐解码失败，准备处理恢复: {error.message}"
        logger.error(reason)
        recovered = await self._recover_from_decode_failure(reason, position)
        if not recovered:
            await self._finish_decode_failure(position, "音乐文件解码失败，已停止播放")

    async def _recover_from_decode_failure(self, reason: str, position: float) -> bool:
        if not (
            self.song_id
            and self._current_file_path
            and self._decode_recovery_attempts < self._max_decode_recovery_attempts
        ):
            logger.warning(f"音乐恢复条件不满足: {reason}")
            return False

        self._decode_recovery_attempts += 1
        resume_position = max(0.0, position - 2.0)
        current_path = self._current_file_path
        filename = current_path.name
        try:
            logger.info(
                f"音乐缓存可能损坏，强制重新下载后从 {resume_position:.1f}s 恢复播放"
            )
            current_path.unlink(missing_ok=True)
            new_path = await self._api.download_music(
                int(self.song_id), self.cache_dir, filename, force=True
            )
            self.current_position = resume_position
            await self._start_playback(new_path, resume_position)
            return True
        except Exception as e:
            logger.error(f"音乐恢复播放失败: {e}", exc_info=True)
            return False

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
        try:
            self._clean_temp_cache()
        except Exception:
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
