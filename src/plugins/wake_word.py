from typing import Any

from src.audio_codecs.audio_channels import OUTPUT_CHANNEL_TTS
from src.constants.constants import AbortReason
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class WakeWordPlugin(Plugin):
    name = "wake_word"
    priority = 30  # 依赖 AudioPlugin

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self.detector = None

    async def setup(self, app: Any) -> None:
        self.app = app
        try:
            from src.audio_processing.wake_word_detect import WakeWordDetector

            self.detector = WakeWordDetector()
            if not getattr(self.detector, "enabled", False):
                self.detector = None
                return

            # 绑定回调
            self.detector.on_detected(self._on_detected)
            self.detector.on_error = self._on_error
        except ImportError as e:
            logger.error(f"无法导入唤醒词检测器: {e}")
            self.detector = None
        except Exception as e:
            logger.error(f"唤醒词插件初始化失败: {e}", exc_info=True)
            self.detector = None

    async def start(self) -> None:
        if not self.detector:
            return
        try:
            # 需要音频编码器以提供原始PCM数据
            audio_codec = getattr(self.app, "audio_codec", None)
            if audio_codec is None:
                logger.warning("未找到audio_codec，无法启动唤醒词检测")
                return

            # 输入流默认按需创建；启用唤醒词时必须持续监听麦克风，
            # 因此在启动检测器前显式打开输入流。
            if not await audio_codec.start_input_stream():
                logger.warning("打开麦克风输入流失败，唤醒词可能无法检测")

            await self.detector.start(audio_codec)
        except Exception as e:
            logger.error(f"启动唤醒词检测器失败: {e}", exc_info=True)

    async def stop(self) -> None:
        if self.detector:
            try:
                await self.detector.stop()
            except Exception as e:
                logger.warning(f"停止唤醒词检测器失败: {e}")

    async def shutdown(self) -> None:
        if self.detector:
            try:
                await self.detector.stop()
            except Exception as e:
                logger.warning(f"关闭唤醒词检测器失败: {e}")

    def set_detection_muted(self, muted: bool) -> None:
        """对话期间临时静音唤醒检测，避免 TTS/回声重复触发."""
        detector = self.detector
        if detector is not None:
            try:
                detector.set_muted(muted)
            except Exception as e:
                logger.debug(f"设置唤醒检测静音失败: {e}")

    async def _on_detected(self, wake_word, full_text):
        # 检测到唤醒词：开始一次唤醒会话
        logger.info(f"[唤醒词] 触发对话: {full_text}")
        try:
            if not hasattr(self.app, "start_wake_conversation"):
                return

            # 已在唤醒会话中（理论上已被静音，不会走到这里）直接忽略
            if getattr(self.app, "wake_session_active", False):
                logger.info("[唤醒词] 当前已在对话中，忽略重复唤醒")
                return

            # 若正在播报，先打断，再进入聆听
            if self.app.is_speaking():
                await self.app.abort_speaking(AbortReason.WAKE_WORD_DETECTED)
                audio_plugin = self.app.plugins.get_plugin("audio")
                if audio_plugin and audio_plugin.codec:
                    await audio_plugin.codec.clear_audio_queue(
                        channel=OUTPUT_CHANNEL_TTS
                    )

            await self.app.start_wake_conversation()
            if not self.app.is_audio_channel_opened():
                logger.warning(
                    "唤醒后协议通道未打开，请检查网络或服务端配置"
                )
        except Exception as e:
            logger.error(f"处理唤醒词检测失败: {e}", exc_info=True)

    def _on_error(self, error):
        try:
            logger.error(f"唤醒词检测错误: {error}")
            if hasattr(self.app, "set_chat_message"):
                self.app.set_chat_message("assistant", f"[唤醒词错误] {error}")
        except Exception as e:
            logger.error(f"处理唤醒词错误回调失败: {e}")
