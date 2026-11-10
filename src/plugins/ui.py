import re
from typing import Any, Optional

from src.audio_codecs.audio_channels import OUTPUT_CHANNEL_TTS
from src.constants.constants import AbortReason, DeviceState
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


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

    # 服务端工具调用的展示文本形如 "% play_music..." / "% play_song..."；
    # 这里只匹配顶层的音乐播放工具，避免把本地 MCP 的
    # "% music_player.play_local_song..." 也当成后端音乐。
    _REMOTE_MUSIC_MARKER = re.compile(
        r"^\s*%\s*(?:play_music|play_song)\b", re.IGNORECASE
    )

    def __init__(self, mode: Optional[str] = None) -> None:
        super().__init__()
        self.app = None
        self.mode = (mode or "cli").lower()
        self.display = None
        self._is_gui = False
        self._tts_owner = None
        self.is_first = True

        # 音乐歌词状态（与聆听/播报界面解耦）：
        #   _music_lyrics_active：音乐是否正在播放/暂停（歌词数据有效）
        #   _music_lyrics_user_visible：用户是否希望展开歌词（默认展开）
        #   _music_lyrics_last_text：最近一帧歌词，用于重新展开时恢复
        self._music_lyrics_active = False
        self._music_lyrics_user_visible = True
        self._music_lyrics_last_text = ""
        # 后端播放音乐：歌词与音频都经由 TTS 通道下发，没有独立的音乐状态。
        # 收到服务端的播放工具标记（如 "% play_music..."）后，后续 TTS 文本
        # 都按歌词处理，直到 TTS stop / 打断。
        self._remote_music_lyrics = False

    async def setup(self, app: Any) -> None:
        """
        初始化 UI 插件.
        """
        self.app = app

        # 创建对应的 display 实例
        self.display = self._create_display()
        self._connect_display_signals()

        # 禁用应用内控制台输入
        if hasattr(app, "use_console_input"):
            app.use_console_input = False

    def _connect_display_signals(self) -> None:
        """
        连接显示层的胶囊释放/歌词手势信号.

        聆听/播报胶囊收起、歌词上甩收起都由显示层通知，
        再由 UI 插件按"用户是否展开歌词"决定是否恢复，互不干扰。
        """
        if not self.display:
            return
        try:
            if hasattr(self.display, "listeningFinished"):
                self.display.listeningFinished.connect(
                    self._on_display_capsule_released
                )
            if hasattr(self.display, "ttsHidden"):
                self.display.ttsHidden.connect(self._on_display_tts_hidden)
        except Exception:
            pass

    def _on_display_capsule_released(self) -> None:
        """聆听界面收缩完成：必要时恢复展开状态的歌词."""
        self._schedule_music_lyrics_restore()

    def _on_display_tts_hidden(self) -> None:
        """播报胶囊真正收起：释放播报归属，并按用户意愿恢复歌词."""
        # 只处理"播报"胶囊的收起；音乐歌词自己的收起/中断不在这里恢复，
        # 避免中断快捷键把歌词收起后又立刻弹回来。
        if self._tts_owner != "tts":
            return
        self._tts_owner = None
        self._schedule_music_lyrics_restore()

    def _schedule_music_lyrics_restore(self) -> None:
        if self.app:
            self.app.spawn(
                self._restore_music_lyrics_after_ui(),
                "ui:music_lyrics_restore",
            )

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

        # 启动显示（TTS 已集成在主胶囊窗口内，没有独立 TTS 窗口）
        self.app.spawn(self.display.start(), name=f"ui:{self.mode}:start")

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
                # 工具调用标记只用于识别音乐，不显示给用户
                if self.display and not self._is_remote_music_marker(text):
                    await self.display.update_text(text)

        # TTS 状态变化控制主胶囊窗口显示
        if msg_type == "tts":
            state = message.get("state")
            text = message.get("text")

            logger.debug(f"TTS消息状态: {state}, 文本: {text[:20] if text else None}")

            if state == "start":
                # 新的播报开始：上一轮后端音乐（如果有）已经结束
                self._reset_remote_music_state()
                self._tts_owner = "tts"
                if self.display:
                    if hasattr(self.display, "set_music_lyrics_active"):
                        self.display.set_music_lyrics_active(False)
                    # 保留用户"收起/展开歌词"的状态，播报结束后据此恢复，
                    # 不要因为 TTS 开始就把收起状态清掉。
                    self._sync_music_lyrics_collapsed()
                if self.display and hasattr(self.display, "show_tts"):
                    display_text = text if text else "语音播报中..."
                    logger.info(f"TTS开始，主胶囊进入播报状态，文本: {display_text}")
                    await self.display.show_tts(display_text)
                if text and self._is_remote_music_marker(text):
                    await self._begin_remote_music_lyrics()
            elif state == "stop":
                if self._remote_music_lyrics:
                    # 后端音乐结束：清掉歌词状态，让胶囊按普通 TTS 收起
                    logger.info("后端音乐结束，清理歌词状态")
                    await self.hide_music_lyrics()
                elif self._tts_owner == "tts":
                    if self.display and hasattr(self.display, "hide_tts"):
                        # 保持播报归属，直到胶囊真正收起（ttsHidden 信号），
                        # 期间歌词更新不会抢回胶囊。
                        logger.info("TTS结束，主胶囊延迟退出播报状态")
                        await self.display.hide_tts()
                    else:
                        self._tts_owner = None
                else:
                    logger.debug(f"忽略过期 TTS stop，当前 TTS owner={self._tts_owner}")
            elif text:
                if self._is_remote_music_marker(text):
                    await self._begin_remote_music_lyrics()
                elif self._remote_music_lyrics:
                    # 后端音乐期间的所有文本（歌名、歌词）都按歌词更新
                    logger.info(f"歌词更新: {text[:30]}")
                    await self.update_music_lyrics(text)
                elif self.display and hasattr(self.display, "update_tts_text"):
                    logger.info(f"TTS文本更新: {text[:30]}")
                    await self.display.update_tts_text(text)

        # llm 更新表情
        elif msg_type == "llm":
            if emotion := message.get("emotion"):
                if self.display:
                    await self.display.update_emotion(emotion)

    # -------------------------
    # 音乐歌词显示
    # -------------------------
    def _is_listening_view(self) -> bool:
        """当前胶囊是否处于聆听声纹形态."""
        try:
            return bool(
                getattr(self.display, "is_listening_view", lambda: False)()
            )
        except Exception:
            return False

    def _display_view_mode(self) -> str:
        """当前胶囊形态（hidden/input/listening/tts/transcript）."""
        get_mode = getattr(self.display, "get_view_mode", None)
        if not callable(get_mode):
            return ""
        try:
            return str(get_mode() or "")
        except Exception:
            return ""

    def _sync_music_lyrics_collapsed(self) -> None:
        """同步"音乐在播且用户已收起"状态，供短横线下拉展开判断."""
        if not self.display:
            return
        setter = getattr(self.display, "set_music_lyrics_collapsed", None)
        if not callable(setter):
            return
        try:
            setter(
                bool(
                    self._music_lyrics_active
                    and not self._music_lyrics_user_visible
                )
            )
        except Exception:
            pass

    @classmethod
    def _is_remote_music_marker(cls, text: str) -> bool:
        """服务端音乐播放工具标记（如 "% play_music..."）."""
        return bool(text) and bool(cls._REMOTE_MUSIC_MARKER.match(text))

    async def _begin_remote_music_lyrics(self) -> None:
        """后端开始播放音乐：把当前 TTS 流切换为歌词流."""
        if self._remote_music_lyrics:
            return

        self._remote_music_lyrics = True
        self._music_lyrics_active = True
        # 下一帧文本（歌名/歌词）到达前，展开时也有内容可显示
        self._music_lyrics_last_text = "♪ 音乐播放中"
        logger.info("识别到后端音乐播放，歌词切换已启用")

        if not self.display:
            return

        self._tts_owner = "music"
        if hasattr(self.display, "set_music_lyrics_active"):
            self.display.set_music_lyrics_active(True)
        self._sync_music_lyrics_collapsed()

        # 用户此前选择收起时，保持收起，不把歌词弹出来
        if not self._music_lyrics_user_visible:
            await self.collapse_music_lyrics()

    def _reset_remote_music_state(self) -> None:
        """清理后端音乐歌词状态（不主动隐藏胶囊）."""
        if not self._remote_music_lyrics:
            return
        self._remote_music_lyrics = False
        self._music_lyrics_active = False
        self._music_lyrics_last_text = ""
        if self.display and hasattr(self.display, "set_music_lyrics_active"):
            self.display.set_music_lyrics_active(False)
        self._sync_music_lyrics_collapsed()

    async def _show_music_lyrics_capsule(self, text: str) -> None:
        """真正显示歌词胶囊（调用方已校验用户展开意愿与界面状态）."""
        if not self.display:
            return
        self._tts_owner = "music"
        if hasattr(self.display, "set_music_lyrics_active"):
            self.display.set_music_lyrics_active(True)
        self._sync_music_lyrics_collapsed()
        if hasattr(self.display, "show_tts"):
            await self.display.show_tts(text)
        else:
            await self.display.update_text(text)

    async def show_music_lyrics(self, text: str) -> bool:
        """
        音乐开始播放/切歌时显示歌词.

        即使当前被聆听/播报界面占用，也会记录歌词状态；等它们收起后
        再按用户是否展开决定是否恢复。默认展开。
        """
        self._music_lyrics_active = True
        self._music_lyrics_last_text = text
        self._sync_music_lyrics_collapsed()

        if not self.display or self._tts_owner == "tts":
            return False
        if self._is_listening_view():
            return False
        if not self._music_lyrics_user_visible:
            return False

        await self._show_music_lyrics_capsule(text)
        return True

    async def update_music_lyrics(self, text: str) -> bool:
        """
        更新歌词文本；真实 TTS 正在显示时不抢占.

        胶囊被聆听/播报临时占用后已收起时，若用户处于展开状态则恢复
        歌词；已收起时只更新缓存文本，绝不自动弹出。
        """
        self._music_lyrics_active = True
        self._music_lyrics_last_text = text
        self._sync_music_lyrics_collapsed()

        if not self.display or self._tts_owner == "tts":
            return False
        if self._is_listening_view():
            return False
        if not self._music_lyrics_user_visible:
            return False

        if self._tts_owner != "music":
            # 胶囊当前没有显示歌词：仅当已回到待机短横线时恢复，
            # 避免抢占输入框/对话记录等其它形态。
            mode = self._display_view_mode()
            if mode and mode != "hidden":
                return False
            await self._show_music_lyrics_capsule(text)
            return True

        if self._display_view_mode() != "hidden":
            if hasattr(self.display, "update_tts_text"):
                await self.display.update_tts_text(text)
            return True

        # 歌词归属仍在，但胶囊已被收起（或临时让出）：重新显示
        await self._show_music_lyrics_capsule(text)
        return True

    async def toggle_music_lyrics(self) -> bool:
        """快捷键：显示/收起歌词（默认展开）."""
        if not self._music_lyrics_active or not self.display:
            logger.info("歌词切换忽略：当前没有音乐在播放")
            return False
        logger.info(
            "歌词切换：%s", "收起" if self._music_lyrics_user_visible else "展开"
        )
        if self._music_lyrics_user_visible:
            await self.collapse_music_lyrics()
        else:
            await self.expand_music_lyrics()
        return True

    async def expand_music_lyrics(self) -> bool:
        """展开歌词：仅影响歌词胶囊，不打断聆听/播报界面."""
        if not self._music_lyrics_active or not self.display:
            return False
        self._music_lyrics_user_visible = True
        self._sync_music_lyrics_collapsed()
        await self._restore_music_lyrics_after_ui()
        return True

    async def collapse_music_lyrics(self) -> bool:
        """收起歌词：只影响歌词胶囊，不打断聆听/播报界面."""
        if not self._music_lyrics_active:
            return False

        self._music_lyrics_user_visible = False
        if not self.display:
            return True

        # 同步"已收起"状态：短横线下拉可重新展开（即使胶囊正被
        # 聆听/播报界面临时占用）。
        self._sync_music_lyrics_collapsed()

        # 聆听/播报界面正在占用胶囊时不碰它们，只记住收起状态。
        if self._tts_owner == "tts" or self._is_listening_view():
            return True

        if self._tts_owner == "music":
            self._tts_owner = None
            if hasattr(self.display, "set_music_lyrics_active"):
                self.display.set_music_lyrics_active(False)
            if hasattr(self.display, "hide_tts"):
                await self.display.hide_tts(delay=0.0)
        return True

    async def _restore_music_lyrics_after_ui(self) -> None:
        """
        聆听/播报胶囊真正收起后，按用户意愿恢复歌词.

        用户已收起歌词时不弹出；展开时才恢复，且仅在胶囊回到待机
        短横线、没有其它会话界面占用时恢复。
        """
        if not self.display or not self._music_lyrics_active:
            return
        if not self._music_lyrics_user_visible or not self._music_lyrics_last_text:
            return
        if self._tts_owner == "tts":
            return
        # 后端音乐的音频本身就是 TTS 流，is_tts_active() 全程为真；
        # 这类歌词由我们自己管理，不能被该判断拦住。
        if not self._remote_music_lyrics:
            try:
                if hasattr(self.app, "is_tts_active") and self.app.is_tts_active():
                    return
            except Exception:
                pass
        if self._is_listening_view():
            return
        mode = self._display_view_mode()
        if mode and mode != "hidden":
            return
        await self._show_music_lyrics_capsule(self._music_lyrics_last_text)

    def on_tts_interrupted(self) -> None:
        """
        播报被打断（按住说话 / 中断快捷键）时释放播报归属.

        播报胶囊已由显示层带收回动画关闭；这里只清理归属，
        避免服务端不再回传 TTS stop 时旧状态阻塞后续显示。
        """
        self.on_tts_aborted()
        self._tts_owner = None

    def on_tts_aborted(self) -> None:
        """
        播报被中止：后端音乐的歌词随之失效.

        本地音乐不受影响（音乐仍在播放，歌词应保留）。
        """
        if not self._remote_music_lyrics:
            return
        self._reset_remote_music_state()
        self._tts_owner = None

    async def hide_music_lyrics(self) -> None:
        """音乐停止：清理歌词状态，并在歌词占用胶囊时收起."""
        self._remote_music_lyrics = False
        self._music_lyrics_active = False
        self._music_lyrics_last_text = ""

        if not self.display:
            self._tts_owner = None
            return

        if hasattr(self.display, "set_music_lyrics_active"):
            self.display.set_music_lyrics_active(False)
        self._sync_music_lyrics_collapsed()

        if self._tts_owner != "music":
            # 已被收起或被聆听/播报占用：清状态即可
            return

        if hasattr(self.display, "restore_default_top_position"):
            self.display.restore_default_top_position()

        self._tts_owner = None
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

    # ===== 回调函数 =====

    def _clear_transcript(self):
        """
        新会话开始时清空累计的 TTS 文本.
        """
        if self.display and hasattr(self.display, "clear_tts_transcript"):
            self.display.clear_tts_transcript()

    async def _send_text(self, text: str):
        """
        发送文本到服务端.
        """
        self._clear_transcript()
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
        self._clear_transcript()
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
        self._clear_transcript()
        await self.app.start_auto_conversation()

    async def _abort(self):
        """
        中断对话.
        """
        self._clear_transcript()
        await self.app.abort_speaking(AbortReason.USER_INTERRUPTION)
        # 打断播报后立即收起播报胶囊（带收回动画）
        if self.display and hasattr(self.display, "hide_tts"):
            await self.display.hide_tts(delay=0.0)
