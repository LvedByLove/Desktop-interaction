from typing import Any, Optional

from src.audio_codecs.audio_channels import OUTPUT_CHANNEL_TTS
from src.constants.constants import AbortReason, DeviceState
from src.plugins.base import Plugin


class UIPlugin(Plugin):
    """UI 插件 - 管理 CLI/GUI 显示"""

    name = "ui"
    priority = 60  # UI 需要在其他插件完成后初始化

    # 设备状态文本映射
    STATE_TEXT_MAP = {
        DeviceState.IDLE: "待命",
        DeviceState.LISTENING: "聆听中...",
        DeviceState.SPEAKING: "说话中...",
    }

    def __init__(self, mode: Optional[str] = None) -> None:
        super().__init__()
        self.app = None
        self.mode = (mode or "cli").lower()
        self.display = None
        self.tts_display = None  # TTS专用显示窗口
        self._is_gui = False
        self._tts_owner = None
        self.is_first = True

    async def setup(self, app: Any) -> None:
        """
        初始化 UI 插件.
        """
        self.app = app

        # 创建对应的 display 实例
        self.display = self._create_display()

        # 禁用应用内控制台输入
        if hasattr(app, "use_console_input"):
            app.use_console_input = False

    def _create_display(self):
        """
        根据模式创建 display 实例.
        """
        if self.mode == "gui":
            from src.display.gui_display import GuiDisplay

            self._is_gui = True
            return GuiDisplay()
        else:
            from src.display.cli_display import CliDisplay

            self._is_gui = False
            return CliDisplay()

    async def start(self) -> None:
        """
        启动 UI 显示.
        """
        if not self.display:
            return

        # 绑定回调
        await self._setup_callbacks()

        # 启动显示
        self.app.spawn(self.display.start(), name=f"ui:{self.mode}:start")

        # GUI 模式下 TTS 已集成到主胶囊窗口，不再启动独立 TTS 窗口

    async def _start_tts_display(self):
        """
        启动 TTS 专用显示窗口.
        """
        from src.utils.logging_config import get_logger
        logger = get_logger(__name__)
        
        try:
            logger.info("开始启动TTS显示窗口...")
            from src.display.tts_display import TtsDisplay

            self.tts_display = TtsDisplay()
            await self.tts_display.start()
            
            # 设置TTS窗口隐藏完成后的回调，用于恢复主窗口
            def on_tts_hidden():
                logger.info("TTS窗口已隐藏，恢复主窗口")
                if self.display and hasattr(self.display, 'root') and self.display.root:
                    self.display.root.show()
            
            self.tts_display.set_hide_callback(on_tts_hidden)
            logger.info("TTS显示窗口启动成功")
        except Exception as e:
            logger.error(f"启动TTS显示窗口失败: {e}", exc_info=True)

    async def _setup_callbacks(self) -> None:
        """
        设置 display 回调.
        """
        if self._is_gui:
            # GUI 需要调度到异步任务
            callbacks = {
                "press_callback": self._wrap_callback(self._press),
                "release_callback": self._wrap_callback(self._release),
                "auto_callback": self._wrap_callback(self._auto_toggle),
                "abort_callback": self._wrap_callback(self._abort),
                "send_text_callback": self._send_text,
            }
        else:
            # CLI 直接传递协程函数
            callbacks = {
                "auto_callback": self._auto_toggle,
                "abort_callback": self._abort,
                "send_text_callback": self._send_text,
            }

        await self.display.set_callbacks(**callbacks)

    def _wrap_callback(self, coro_func):
        """
        包装协程函数为可调度的 lambda.
        """
        return lambda: self.app.spawn(coro_func(), name="ui:callback")

    async def on_incoming_json(self, message: Any) -> None:
        """
        处理传入的 JSON 消息.
        """
        from src.utils.logging_config import get_logger
        logger = get_logger(__name__)
        
        if not isinstance(message, dict):
            return

        msg_type = message.get("type")
        logger.debug(f"UI插件收到JSON消息: type={msg_type}, message={message}")

        # tts/stt 都更新文本
        if msg_type in ("tts", "stt"):
            if text := message.get("text"):
                if self.display:
                    await self.display.update_text(text)

        # TTS 状态变化控制主胶囊窗口显示
        if msg_type == "tts":
            state = message.get("state")
            text = message.get("text")

            logger.debug(f"TTS消息状态: {state}, 文本: {text[:20] if text else None}")

            if state == "start":
                self._tts_owner = "tts"
                if self.display:
                    if hasattr(self.display, "set_music_lyrics_active"):
                        self.display.set_music_lyrics_active(False)
                    if hasattr(self.display, "set_music_lyrics_collapsed"):
                        self.display.set_music_lyrics_collapsed(False)
                if self.display and hasattr(self.display, "show_tts"):
                    display_text = text if text else "语音播报中..."
                    logger.info(f"TTS开始，主胶囊进入播报状态，文本: {display_text}")
                    await self.display.show_tts(display_text)
            elif state == "stop":
                if self._tts_owner == "tts":
                    self._tts_owner = None
                    if self.display and hasattr(self.display, "hide_tts"):
                        logger.info("TTS结束，主胶囊延迟退出播报状态")
                        await self.display.hide_tts()
                else:
                    logger.debug(f"忽略过期 TTS stop，当前 TTS owner={self._tts_owner}")
            elif text:
                if self.display and hasattr(self.display, "update_tts_text"):
                    logger.info(f"TTS文本更新: {text[:30]}")
                    await self.display.update_tts_text(text)

        # llm 更新表情
        elif msg_type == "llm":
            if emotion := message.get("emotion"):
                if self.display:
                    await self.display.update_emotion(emotion)

    async def show_music_lyrics(self, text: str) -> bool:
        """
        在 TTS 区域显示音乐歌词，不广播 TTS 事件，避免触发音乐暂停.
        """
        if self._tts_owner == "tts" or not self.display:
            return False
        if not hasattr(self.display, "show_tts"):
            await self.display.update_text(text)
            return True

        self._tts_owner = "music"
        if hasattr(self.display, "set_music_lyrics_active"):
            self.display.set_music_lyrics_active(True)
        if hasattr(self.display, "set_music_lyrics_collapsed"):
            self.display.set_music_lyrics_collapsed(False)
        await self.display.show_tts(text)
        return True

    async def update_music_lyrics(self, text: str) -> bool:
        """
        更新音乐歌词文本；真实 TTS 正在显示时不抢占.
        """
        if self._tts_owner == "tts" or not self.display:
            return False
        if self._tts_owner != "music":
            return await self.show_music_lyrics(text)

        if hasattr(self.display, "update_tts_text"):
            await self.display.update_tts_text(text)
        else:
            await self.display.update_text(text)
        return True

    async def hide_music_lyrics(self) -> None:
        """
        隐藏音乐歌词显示；只处理音乐自己打开的 TTS 区域.
        """
        if self._tts_owner != "music" or not self.display:
            return

        self._tts_owner = None
        if hasattr(self.display, "set_music_lyrics_active"):
            self.display.set_music_lyrics_active(False)
        if hasattr(self.display, "set_music_lyrics_collapsed"):
            self.display.set_music_lyrics_collapsed(False)
        if hasattr(self.display, "hide_tts"):
            await self.display.hide_tts()

    async def on_device_state_changed(self, state: Any) -> None:
        """
        设备状态变化处理.
        """
        if not self.display:
            return

        # 跳过首次调用
        if self.is_first:
            self.is_first = False
            return

        # 更新表情和状态
        await self.display.update_emotion("neutral")
        if status_text := self.STATE_TEXT_MAP.get(state):
            await self.display.update_status(status_text, True)

    async def shutdown(self) -> None:
        """
        清理 UI 资源，关闭窗口.
        """
        if self.display:
            await self.display.close()
            self.display = None

        # 关闭TTS显示窗口
        if self.tts_display:
            await self.tts_display.close()
            self.tts_display = None

    # ===== 回调函数 =====

    async def _send_text(self, text: str):
        """
        发送文本到服务端.
        """
        if self.app.device_state == DeviceState.SPEAKING:
            audio_plugin = self.app.plugins.get_plugin("audio")
            if audio_plugin and audio_plugin.codec:
                await audio_plugin.codec.clear_audio_queue(channel=OUTPUT_CHANNEL_TTS)
            await self.app.abort_speaking(None)
        if await self.app.connect_protocol():
            await self.app.protocol.send_wake_word_detected(text)

    async def _press(self):
        """
        手动模式：按下开始录音.
        """
        if self.display and hasattr(self.display, "show_listening"):
            await self.display.show_listening()
        await self.app.start_listening_manual()

    async def _release(self):
        """
        手动模式：释放停止录音.
        """
        await self.app.stop_listening_manual()
        if self.display and hasattr(self.display, "hide_listening"):
            await self.display.hide_listening()

    async def _auto_toggle(self):
        """
        自动模式切换.
        """
        await self.app.start_auto_conversation()

    async def _abort(self):
        """
        中断对话.
        """
        await self.app.abort_speaking(AbortReason.USER_INTERRUPTION)
