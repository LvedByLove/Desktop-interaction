import asyncio
import os
from typing import Any

import numpy as np

from src.audio_codecs.audio_channels import OUTPUT_CHANNEL_TTS
from src.audio_codecs.audio_codec import AudioCodec
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# 待发送音频队列上限：64 帧 × 20ms ≈ 1.28 秒。网络慢时丢最旧帧，
# 保持低延迟，避免每帧新建 Task 无界堆积。
_AUDIO_SEND_QUEUE_MAX = 64

# 唤醒会话期间判定"用户正在说话"的麦克风峰值（int16）
_VOICE_ACTIVITY_PEAK = 2000


class AudioPlugin(Plugin):
    name = "audio"
    priority = 10  # 最高优先级，其他插件依赖 audio_codec

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self.codec: AudioCodec | None = None
        self._main_loop = None
        self._audio_consumer_task = None
        self._send_queue: asyncio.Queue[bytes] | None = None
        self._send_task: asyncio.Task | None = None
        self._in_silence_period = False  # 静默期标志，用于防止TTS尾音被捕获

    async def setup(self, app: Any) -> None:
        self.app = app
        self._main_loop = app._main_loop

        if os.getenv("XIAOZHI_DISABLE_AUDIO") == "1":
            return

        try:
            self.codec = AudioCodec()
            await self.codec.initialize()

            # 常驻发送协程 + 有界队列：回调线程只投递，网络慢时有界丢帧，
            # 不再每帧创建一个 asyncio Task。
            self._send_queue = asyncio.Queue(maxsize=_AUDIO_SEND_QUEUE_MAX)
            self._send_task = asyncio.create_task(
                self._send_loop(), name="audio:send"
            )
            self.codec.set_encoded_callback(self._on_encoded_audio)

            # 暴露给应用，便于唤醒词插件使用
            self.app.audio_codec = self.codec

            # 注册麦克风活动监听：唤醒会话期间用于判定"用户正在说话"，
            # 避免用户话说得久、服务端又迟迟没有消息时被超时误关闭。
            self.codec.add_audio_listener(self)

            # 后台预热按住说话音效，避免首次触发时 FFmpeg 解码延迟
            self.app.spawn(
                self._preload_hold_talk_sound(), name="audio:preload_effects"
            )
        except Exception as e:
            logger.error(f"音频插件初始化失败: {e}", exc_info=True)
            self.codec = None

    async def _preload_hold_talk_sound(self) -> None:
        """后台预加载按住说话音效（不阻塞启动）."""
        try:
            from src.utils.hold_talk_sound import HoldTalkSound

            await HoldTalkSound().preload()
        except Exception:
            pass

    def on_audio_data(self, audio_data: np.ndarray) -> None:
        """音频监听回调（PortAudio 线程）：唤醒会话期间上报用户说话活动.

        只做一次 min/max 扫描，不分配 int32 临时数组。
        """
        app = self.app
        if app is None or not getattr(app, "wake_session_active", False):
            return
        try:
            if len(audio_data) == 0:
                return
            # 用 int 比较，避免 np.abs(-32768) 的 int16 溢出
            peak = max(int(audio_data.max()), -int(audio_data.min()))
            if peak >= _VOICE_ACTIVITY_PEAK:
                app.notify_wake_voice_activity()
        except Exception:
            pass

    async def on_device_state_changed(self, state):
        """设备状态变化时清空音频队列，并按需释放/恢复输入流.

        特别处理：进入 LISTENING 状态时，等待音频硬件输出完全停止， 避免 TTS 尾音被麦克风捕获导致误触发。
        """
        if not self.codec:
            return

        from src.constants.constants import DeviceState

        # 如果进入监听状态，确保输入流运行，清空队列并等待硬件输出完全停止
        if state == DeviceState.LISTENING:
            await self.codec.start_input_stream()
            # 设置静默期标志，阻止麦克风音频发送
            self._in_silence_period = True
            try:
                # 等待硬件 DAC 输出完成（50-100ms）+ 声波传播（20ms）+ 安全余量
                await asyncio.sleep(0.2)
            finally:
                # 清空和等待完成后，解除静默期
                self._in_silence_period = False
        elif state == DeviceState.IDLE:
            # 待命状态下，若未启用唤醒词则释放输入流，避免持续占用麦克风
            if not self._wake_word_enabled():
                await self.codec.stop_input_stream()

    def _wake_word_enabled(self) -> bool:
        """判断唤醒词检测是否启用（启用时需持续占用输入流以监听唤醒词）。"""
        try:
            plugin = self.app.plugins.get_plugin("wake_word") if self.app else None
            if not plugin:
                return False
            detector = getattr(plugin, "detector", None)
            return bool(detector is not None and getattr(detector, "enabled", False))
        except Exception:
            return False

    async def on_incoming_json(self, message: Any) -> None:
        """处理 TTS 事件，控制音乐播放.

        Args:
            message: JSON消息，包含 type 和 state 字段
        """
        if not isinstance(message, dict):
            return

        try:
            # 监听 TTS 状态变化，控制音乐播放
            if message.get("type") == "tts":
                state = message.get("state")
                if state == "start":
                    # TTS 开始：只清空 TTS 通道，并压低音乐音量
                    await self._start_tts_ducking()
                elif state == "stop":
                    # TTS 结束：只清空 TTS 尾音，并恢复音乐音量
                    await self._stop_tts_ducking()
        except Exception as e:
            logger.error(f"处理 TTS 事件失败: {e}", exc_info=True)

    async def on_incoming_audio(self, data: bytes) -> None:
        """接收服务端返回的音频数据并播放.

        Args:
            data: 服务端返回的Opus编码音频数据
        """
        if self.codec:
            # 打断后服务端可能还有少量尾包在途，直接丢弃，避免和用户抢话；
            # 该标记与麦克风采集抑制无关，下一次 tts start 会自动解除。
            if self.app is not None and getattr(
                self.app, "drop_incoming_tts", False
            ):
                return
            try:
                await self.codec.write_audio(data)
            except Exception as e:
                logger.debug(f"写入音频数据失败: {e}")

    async def _start_tts_ducking(self):
        """
        TTS 开始时：只清空 TTS 通道，并压低音乐音量.
        """
        try:
            if self.codec:
                await self.codec.clear_audio_queue(channel=OUTPUT_CHANNEL_TTS)

                # AEC_OPTIONS.MUSIC_PARALLEL=True：TTS 与音乐并行（压低音量）
                # False：TTS 期间音乐静音
                parallel = True
                try:
                    from src.utils.config_manager import ConfigManager

                    parallel = bool(
                        ConfigManager.get_instance().get_config(
                            "AEC_OPTIONS.MUSIC_PARALLEL", True
                        )
                    )
                except Exception:
                    pass
                self.codec.set_music_ducking(True, 0.25 if parallel else 0.0)
                logger.debug(
                    "TTS 开始，已清空 TTS 通道并%s音乐",
                    "压低" if parallel else "静音",
                )
        except Exception as e:
            logger.error(f"TTS 开始处理失败: {e}", exc_info=True)

    async def _stop_tts_ducking(self):
        """
        TTS 结束后：只清空 TTS 尾音，并恢复音乐音量.
        """
        try:
            if self.codec:
                await self.codec.clear_audio_queue(channel=OUTPUT_CHANNEL_TTS)
                self.codec.set_music_ducking(False)
                logger.debug("TTS 结束，已清空 TTS 通道并恢复音乐音量")
        except Exception as e:
            logger.error(f"恢复音乐音量失败: {e}", exc_info=True)

    async def recover_music_after_tts_interruption(self) -> None:
        """
        协议异常关闭时，结束 TTS ducking 并清理 TTS 通道.
        """
        try:
            if self.codec:
                await self.codec.clear_audio_queue(channel=OUTPUT_CHANNEL_TTS)
                self.codec.set_music_ducking(False)
                logger.info("协议通道关闭，已恢复音乐音量")
        except Exception as e:
            logger.error(f"恢复 TTS 中断后的音乐音量失败: {e}", exc_info=True)

    async def shutdown(self) -> None:
        """
        完全关闭并释放音频资源.
        """
        logger.info("正在关闭音频插件...")

        # 停止常驻发送协程（先取消，再断开队列引用）
        send_task, self._send_task = self._send_task, None
        if send_task is not None and not send_task.done():
            send_task.cancel()
            try:
                await send_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._send_queue = None

        # 停止音频消费者任务
        if self._audio_consumer_task and not self._audio_consumer_task.done():
            self._audio_consumer_task.cancel()
            try:
                await self._audio_consumer_task
            except asyncio.CancelledError:
                pass

        if self.codec:
            try:
                await self.codec.close()
                logger.info("音频编解码器已关闭")
            except Exception as e:
                logger.error(f"关闭音频编解码器失败: {e}", exc_info=True)
            finally:
                self.codec = None

        # 清空应用引用
        if self.app:
            self.app.audio_codec = None

        logger.info("音频插件已关闭")

    # -------------------------
    # 内部：直接发送录音音频（不走队列）
    # -------------------------
    def _on_encoded_audio(self, encoded_data: bytes) -> None:
        """PortAudio 回调线程：只投递到事件循环，不创建 Task."""
        try:
            loop = self._main_loop
            if loop is None or loop.is_closed():
                return
            loop.call_soon_threadsafe(self._enqueue_send_audio, encoded_data)
        except Exception:
            pass

    def _enqueue_send_audio(self, encoded_data: bytes) -> None:
        """事件循环线程：有界入队，满时丢最旧帧（低延迟优先）."""
        queue = self._send_queue
        if queue is None:
            return
        try:
            queue.put_nowait(encoded_data)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(encoded_data)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def _send_loop(self) -> None:
        """常驻发送协程：串行发送、天然背压，不再每帧建 Task."""
        queue = self._send_queue
        if queue is None:
            return
        while True:
            encoded_data = await queue.get()
            try:
                protocol = self.app.protocol if self.app else None
                if (
                    self.app is not None
                    and self.app.running
                    and protocol is not None
                    and protocol.is_audio_channel_opened()
                    and self._should_send_microphone_audio()
                ):
                    await protocol.send_audio(encoded_data)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                queue.task_done()

    def _should_send_microphone_audio(self) -> bool:
        """
        委托给应用的统一状态机规则，并检查静默期标志.
        """
        try:
            # 静默期内禁止发送音频（防止TTS尾音被捕获）
            if self._in_silence_period:
                return False
            return self.app and self.app.should_capture_audio()
        except Exception:
            return False
