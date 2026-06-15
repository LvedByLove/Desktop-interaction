import asyncio
import re
import sys
import threading
from pathlib import Path
from typing import Any, Awaitable

# 允许作为脚本直接运行：把项目根目录加入 sys.path（src 的上一级）
try:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
except Exception:
    pass

from src.constants.constants import DeviceState, ListeningMode
from src.plugins.calendar import CalendarPlugin
from src.plugins.iot import IoTPlugin
from src.plugins.manager import PluginManager
from src.plugins.mcp import McpPlugin
from src.plugins.shortcuts import ShortcutsPlugin
from src.plugins.ui import UIPlugin
from src.plugins.wake_word import WakeWordPlugin
from src.protocols.mqtt_protocol import MqttProtocol
from src.protocols.websocket_protocol import WebsocketProtocol
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger
from src.utils.opus_loader import setup_opus

logger = get_logger(__name__)
setup_opus()


class Application:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = Application()
        return cls._instance

    def __init__(self):
        if Application._instance is not None:
            logger.error("尝试创建Application的多个实例")
            raise Exception("Application是单例类，请使用get_instance()获取实例")
        Application._instance = self

        logger.debug("初始化Application实例")

        # 配置
        self.config = ConfigManager.get_instance()

        # 状态
        self.running = False
        self.protocol = None

        # 设备状态（仅主程序改写，插件只读）
        self.device_state = DeviceState.IDLE
        try:
            aec_enabled_cfg = bool(self.config.get_config("AEC_OPTIONS.ENABLED", True))
        except Exception:
            aec_enabled_cfg = True
        self.aec_enabled = aec_enabled_cfg
        self.listening_mode = (
            ListeningMode.REALTIME if self.aec_enabled else ListeningMode.AUTO_STOP
        )
        self.keep_listening = False

        # 统一任务池（替代 _main_tasks/_bg_tasks）
        self._tasks: set[asyncio.Task] = set()

        # 关停事件
        self._shutdown_event: asyncio.Event | None = None

        # 事件循环
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # 并发控制
        self._state_lock: asyncio.Lock | None = None
        self._connect_lock: asyncio.Lock | None = None

        # 插件
        self.plugins = PluginManager()

    # -------------------------
    # 生命周期
    # -------------------------
    async def run(self, *, protocol: str = "websocket", mode: str = "gui") -> int:
        logger.info("启动Application，protocol=%s", protocol)
        try:
            self.running = True
            self._main_loop = asyncio.get_running_loop()
            self._initialize_async_objects()
            self._set_protocol(protocol)
            self._setup_protocol_callbacks()
            # 插件：setup（延迟导入AudioPlugin，确保上面setup_opus已执行）
            from src.plugins.audio import AudioPlugin

            # 注册音频、UI、MCP、IoT、唤醒词、快捷键与日程插件（UI模式从run参数传入）
            # 插件会自动按 priority 排序：
            # AudioPlugin(10) -> McpPlugin(20) -> WakeWordPlugin(30) -> CalendarPlugin(40)
            # -> IoTPlugin(50) -> UIPlugin(60) -> ShortcutsPlugin(70)
            self.plugins.register(
                McpPlugin(),
                IoTPlugin(),
                AudioPlugin(),
                WakeWordPlugin(),
                CalendarPlugin(),
                UIPlugin(mode=mode),
                ShortcutsPlugin(),
            )
            await self.plugins.setup_all(self)
            # 启动后广播初始状态，确保 UI 就绪时能看到“待命”
            try:
                await self.plugins.notify_device_state_changed(self.device_state)
            except Exception:
                pass
            # await self.connect_protocol()
            # 插件：start
            await self.plugins.start_all()
            # 等待关停
            await self._wait_shutdown()
            return 0

        except Exception as e:
            logger.error(f"应用运行失败: {e}", exc_info=True)
            return 1
        finally:
            try:
                await self.shutdown()
            except Exception as e:
                logger.error(f"关闭应用时出错: {e}")

    async def connect_protocol(self):
        """
        确保协议通道打开并广播一次协议就绪。返回是否已打开。
        """
        # 已打开直接返回
        try:
            if self.is_audio_channel_opened():
                return True
            if not self._connect_lock:
                # 未初始化锁时，直接尝试一次
                opened = await asyncio.wait_for(
                    self.protocol.open_audio_channel(), timeout=12.0
                )
                if not opened:
                    logger.error("协议连接失败")
                    return False
                logger.info("协议连接已建立，按Ctrl+C退出")
                await self.plugins.notify_protocol_connected(self.protocol)
                return True

            async with self._connect_lock:
                if self.is_audio_channel_opened():
                    return True
                opened = await asyncio.wait_for(
                    self.protocol.open_audio_channel(), timeout=12.0
                )
                if not opened:
                    logger.error("协议连接失败")
                    return False
                logger.info("协议连接已建立，按Ctrl+C退出")
                await self.plugins.notify_protocol_connected(self.protocol)
                return True
        except asyncio.TimeoutError:
            logger.error("协议连接超时")
            return False

    def _initialize_async_objects(self) -> None:
        logger.debug("初始化异步对象")
        self._shutdown_event = asyncio.Event()
        self._state_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()

    def _set_protocol(self, protocol_type: str) -> None:
        logger.debug("设置协议类型: %s", protocol_type)
        if protocol_type == "mqtt":
            self.protocol = MqttProtocol(asyncio.get_running_loop())
        else:
            self.protocol = WebsocketProtocol()

    # -------------------------
    # 手动聆听（按住说话）
    # -------------------------
    async def start_listening_manual(self) -> None:
        try:
            ok = await self.connect_protocol()
            if not ok:
                return
            self.keep_listening = False

            # 如果说话中发送打断
            if self.device_state == DeviceState.SPEAKING:
                logger.info("说话中发送打断")
                await self.protocol.send_abort_speaking(None)
                await self.set_device_state(DeviceState.IDLE)
            await self.protocol.send_start_listening(ListeningMode.MANUAL)
            await self.set_device_state(DeviceState.LISTENING)
        except Exception:
            pass

    async def stop_listening_manual(self) -> None:
        try:
            await self.protocol.send_stop_listening()
            await self.set_device_state(DeviceState.IDLE)
        except Exception:
            pass

    # -------------------------
    # 自动/实时对话：根据 AEC 与当前配置选择模式，开启保持会话
    # -------------------------
    async def start_auto_conversation(self) -> None:
        try:
            ok = await self.connect_protocol()
            if not ok:
                return

            mode = (
                ListeningMode.REALTIME if self.aec_enabled else ListeningMode.AUTO_STOP
            )
            self.listening_mode = mode
            self.keep_listening = True
            await self.protocol.send_start_listening(mode)
            await self.set_device_state(DeviceState.LISTENING)
        except Exception:
            pass

    def _setup_protocol_callbacks(self) -> None:
        self.protocol.on_network_error(self._on_network_error)
        self.protocol.on_incoming_json(self._on_incoming_json)
        self.protocol.on_incoming_audio(self._on_incoming_audio)
        self.protocol.on_audio_channel_opened(self._on_audio_channel_opened)
        self.protocol.on_audio_channel_closed(self._on_audio_channel_closed)

    async def _wait_shutdown(self) -> None:
        await self._shutdown_event.wait()

    # -------------------------
    # 统一任务管理（精简）
    # -------------------------
    def spawn(self, coro: Awaitable[Any], name: str) -> asyncio.Task:
        """
        创建任务并登记，关停时统一取消。
        """
        if not self.running or (self._shutdown_event and self._shutdown_event.is_set()):
            logger.debug(f"跳过任务创建（应用正在关闭）: {name}")
            return None
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)

        def _done(t: asyncio.Task):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.error(f"任务 {name} 异常结束: {t.exception()}", exc_info=True)

        task.add_done_callback(_done)
        return task

    def schedule_command_nowait(self, fn, *args, **kwargs) -> None:
        if not self._main_loop or self._main_loop.is_closed():
            logger.warning("主事件循环未就绪，拒绝调度")
            return

        def _runner():
            try:
                res = fn(*args, **kwargs)
                if asyncio.iscoroutine(res):
                    self.spawn(res, name=f"call:{getattr(fn, '__name__', 'anon')}")
            except Exception as e:
                logger.error(f"调度的可调用执行失败: {e}", exc_info=True)

        # 确保在事件循环线程里执行
        self._main_loop.call_soon_threadsafe(_runner)

    # -------------------------
    # 协议回调
    # -------------------------
    def _on_network_error(self, error_message=None):
        if error_message:
            logger.error(error_message)

        self.keep_listening = False
        # 出错即请求关闭
        # if self._shutdown_event and not self._shutdown_event.is_set():
        #     self._shutdown_event.set()

    def _on_incoming_audio(self, data: bytes):
        logger.debug(f"收到二进制消息，长度: {len(data)}")
        # 转发给插件
        self.spawn(self.plugins.notify_incoming_audio(data), "plugin:on_audio")

    def _on_incoming_json(self, json_data):
        try:
            msg_type = json_data.get("type") if isinstance(json_data, dict) else None
            logger.info(f"收到JSON消息: type={msg_type}")
            # 将 TTS start/stop 映射为设备状态（支持自动/实时，且不污染手动模式）
            if msg_type == "stt":
                self._handle_local_music_command(json_data)

            if msg_type == "tts":
                state = json_data.get("state")
                if state == "start":
                    # 仅当保持会话且实时模式时，TTS开始期间保持LISTENING；否则显示SPEAKING
                    if (
                        self.keep_listening
                        and self.listening_mode == ListeningMode.REALTIME
                    ):
                        self.spawn(
                            self.set_device_state(DeviceState.LISTENING),
                            "state:tts_start_rt",
                        )
                    else:
                        self.spawn(
                            self.set_device_state(DeviceState.SPEAKING),
                            "state:tts_start_speaking",
                        )
                elif state == "stop":
                    if self.keep_listening:
                        # 继续对话：根据当前模式重启监听
                        async def _restart_listening():
                            try:
                                # 先设置状态为 LISTENING，触发音频队列清空和硬件停止等待
                                await self.set_device_state(DeviceState.LISTENING)

                                # 等待音频硬件完全停止后，再发送监听指令
                                # REALTIME 且已在 LISTENING 时无需重复发送
                                if not (
                                    self.listening_mode == ListeningMode.REALTIME
                                    and self.device_state == DeviceState.LISTENING
                                ):
                                    await self.protocol.send_start_listening(
                                        self.listening_mode
                                    )
                            except Exception:
                                pass

                        self.spawn(_restart_listening(), "state:tts_stop_restart")
                    else:
                        self.spawn(
                            self.set_device_state(DeviceState.IDLE),
                            "state:tts_stop_idle",
                        )
            # 转发给插件
            self.spawn(self.plugins.notify_incoming_json(json_data), "plugin:on_json")
        except Exception:
            logger.info("收到JSON消息")

    def _handle_local_music_command(self, json_data: dict) -> None:
        text = str(json_data.get("text") or "").strip()
        command = self._parse_local_music_command(text)
        if not command:
            return

        try:
            from src.mcp.tools.music.music_player import get_music_player_instance

            player = get_music_player_instance()
            action, value = command
            if action == "stop":
                if not getattr(player, "is_playing", False):
                    return
                logger.info(f"本地识别到音乐停止指令，直接结束播放: {text}")
                self.spawn(player.stop(), "music:local_stop_command")
            elif action == "pause":
                if not getattr(player, "is_playing", False) or getattr(player, "paused", False):
                    return
                logger.info(f"本地识别到音乐暂停指令，直接执行暂停: {text}")
                self.spawn(player.pause(source="manual"), "music:local_pause_command")
            elif action == "resume":
                if not getattr(player, "is_playing", False) or not getattr(player, "paused", False):
                    return
                logger.info(f"本地识别到音乐恢复指令，直接执行恢复: {text}")
                self.spawn(player.resume(), "music:local_resume_command")
            elif action == "play" and value:
                logger.info(f"本地识别到音乐播放指令，直接播放《{value}》: {text}")
                self.set_chat_message("assistant", f"正在为你播放《{value}》...")
                self.spawn(player.search_and_play(value), "music:local_play_command")
        except Exception as e:
            logger.error(f"执行本地音乐指令失败: {e}", exc_info=True)

    def _parse_local_music_command(self, text: str) -> tuple[str, str] | None:
        if not text:
            return None
        if self._is_local_music_stop_command(text):
            return ("stop", "")
        if self._is_local_music_pause_command(text):
            return ("pause", "")
        if self._is_local_music_resume_command(text):
            return ("resume", "")
        song_name = self._extract_local_music_play_name(text)
        if song_name:
            return ("play", song_name)
        return None

    def _is_local_music_stop_command(self, text: str) -> bool:
        normalized = re.sub(r"[\s，。！？、,.!?~～]+", "", text.lower())
        if not normalized:
            return False

        if "音乐" not in normalized and "歌" not in normalized and "播放" not in normalized:
            return False

        stop_patterns = (
            "停止音乐",
            "音乐停止",
            "停止播放",
            "播放停止",
            "结束音乐",
            "音乐结束",
            "结束播放",
            "播放结束",
            "关闭音乐",
            "音乐关闭",
            "关掉音乐",
            "关了音乐",
            "把音乐关掉",
            "把歌关掉",
            "不要放音乐了",
            "别放音乐了",
            "别播放了",
            "不播放了",
            "别唱了",
            "停掉音乐",
            "音乐停掉",
            "停止这首歌",
            "结束这首歌",
            "这首歌停掉",
            "这首歌结束",
        )
        if any(pattern in normalized for pattern in stop_patterns):
            return True

        return bool(
            re.search(r"(音乐|歌|播放).{0,4}(停止|结束|关闭|关掉|停掉)", normalized)
            or re.search(r"(停止|结束|关闭|关掉|停掉).{0,4}(音乐|歌|播放)", normalized)
        )

    def _is_local_music_pause_command(self, text: str) -> bool:
        normalized = re.sub(r"[\s，。！？、,.!?~～]+", "", text.lower())
        if not normalized:
            return False

        if "音乐" not in normalized and "歌" not in normalized and "播放" not in normalized:
            return False

        pause_patterns = (
            "暂停音乐",
            "音乐暂停",
            "暂停播放",
            "播放暂停",
            "暂停这首歌",
            "这首歌暂停",
            "歌暂停",
            "暂停一下音乐",
            "音乐暂停一下",
            "先暂停音乐",
            "音乐先暂停",
            "把音乐暂停",
            "把歌暂停",
            "音乐停一下",
            "歌停一下",
            "先停一下音乐",
            "先停一下歌",
            "停一下音乐",
            "停一下歌",
        )
        if any(pattern in normalized for pattern in pause_patterns):
            return True

        return bool(
            re.search(r"(音乐|歌|播放).{0,4}(暂停|停一下)", normalized)
            or re.search(r"(暂停|停一下).{0,4}(音乐|歌|播放)", normalized)
        )

    def _is_local_music_resume_command(self, text: str) -> bool:
        normalized = re.sub(r"[\s，。！？、,.!?~～]+", "", text.lower())
        if not normalized:
            return False

        resume_patterns = (
            "继续播放",
            "恢复播放",
            "播放继续",
            "音乐继续",
            "继续音乐",
            "恢复音乐",
            "音乐恢复",
            "继续放歌",
            "继续放音乐",
            "接着播放",
            "接着放",
            "接着听",
            "继续听歌",
            "继续听音乐",
            "放起来",
            "能放起来",
        )
        if any(pattern in normalized for pattern in resume_patterns):
            return True

        return bool(
            re.search(r"(继续|恢复|接着).{0,4}(播放|音乐|歌|听)", normalized)
            or re.search(r"(播放|音乐|歌).{0,4}(继续|恢复)", normalized)
        )

    def _extract_local_music_play_name(self, text: str) -> str:
        cleaned = re.sub(r"[，。！？、,.!?~～]+", " ", text).strip()
        if not cleaned:
            return ""

        negative_markers = ("暂停", "停止", "停一下", "继续", "恢复", "接着")
        if any(marker in cleaned for marker in negative_markers):
            return ""

        patterns = (
            r"(?:播放|放一下|放一首|放首|听一下|听一首|听首|我要听|想听)(?:音乐|歌曲|歌)?\s*[《\"“']?([^》\"”'，。！？、,.!?]+)[》\"”']?",
            r"(?:来一首|来首)\s*[《\"“']?([^》\"”'，。！？、,.!?]+)[》\"”']?",
        )
        for pattern in patterns:
            match = re.search(pattern, cleaned)
            if not match:
                continue
            song_name = self._normalize_local_music_search_name(match.group(1))
            non_song_markers = (
                "设置",
                "列表",
                "功能",
                "按钮",
                "页面",
                "在哪里",
                "在哪",
                "哪里",
                "怎么",
                "如何",
            )
            if any(marker in song_name for marker in non_song_markers):
                continue
            if song_name and 1 <= len(song_name) <= 40:
                return song_name
        return ""

    def _normalize_local_music_search_name(self, song_name: str) -> str:
        song_name = (song_name or "").strip()
        song_name = re.sub(r"^(音乐|歌曲|歌)", "", song_name).strip()
        song_name = re.sub(r"(给我听|给我放|吧|一下|一首)$", "", song_name).strip()
        song_name = re.sub(r"\s+", " ", song_name).strip()

        match = re.match(r"^([一-龥A-Za-z0-9 .·]{1,20})的([^的]{1,30})$", song_name)
        if match:
            artist = match.group(1).strip()
            title = match.group(2).strip()
            if artist and title:
                return f"{artist} {title}"

        return song_name

    async def _on_audio_channel_opened(self):
        logger.info("协议通道已打开")
        # 通道打开后进入 LISTENING（：简化为直读直写）
        await self.set_device_state(DeviceState.LISTENING)

    async def _on_audio_channel_closed(self):
        logger.info("协议通道已关闭")
        # 通道关闭回到 IDLE
        await self.set_device_state(DeviceState.IDLE)
        try:
            audio_plugin = self.plugins.get_plugin("audio")
            if audio_plugin and hasattr(audio_plugin, "recover_music_after_tts_interruption"):
                await audio_plugin.recover_music_after_tts_interruption()
        except Exception as e:
            logger.error(f"协议关闭后恢复音乐失败: {e}", exc_info=True)

    async def set_device_state(self, state: DeviceState):
        """
        仅供主程序内部调用：设置设备状态。插件请只读获取。
        """
        # print(f"set_device_state: {state}")
        if not self._state_lock:
            self.device_state = state
            try:
                await self.plugins.notify_device_state_changed(state)
            except Exception:
                pass
            return
        async with self._state_lock:
            if self.device_state == state:
                return
            logger.info(f"设置设备状态: {state}")
            self.device_state = state
        # 锁外广播，避免插件回调引起潜在的长耗时阻塞
        try:
            await self.plugins.notify_device_state_changed(state)
            if state == DeviceState.LISTENING:
                await asyncio.sleep(0.5)
                self.aborted = False
        except Exception:
            pass

    # -------------------------
    # 只读访问器（提供给插件使用）
    # -------------------------
    def get_device_state(self):
        return self.device_state

    def is_idle(self) -> bool:
        return self.device_state == DeviceState.IDLE

    def is_listening(self) -> bool:
        return self.device_state == DeviceState.LISTENING

    def is_speaking(self) -> bool:
        return self.device_state == DeviceState.SPEAKING

    def get_listening_mode(self):
        return self.listening_mode

    def is_keep_listening(self) -> bool:
        return bool(self.keep_listening)

    def is_audio_channel_opened(self) -> bool:
        try:
            return bool(self.protocol and self.protocol.is_audio_channel_opened())
        except Exception:
            return False

    def should_capture_audio(self) -> bool:
        try:
            if self.device_state == DeviceState.LISTENING and not self.aborted:
                return True

            return (
                self.device_state == DeviceState.SPEAKING
                and self.aec_enabled
                and self.keep_listening
                and self.listening_mode == ListeningMode.REALTIME
            )
        except Exception:
            return False

    def get_state_snapshot(self) -> dict:
        return {
            "device_state": self.device_state,
            "listening_mode": self.listening_mode,
            "keep_listening": bool(self.keep_listening),
            "audio_opened": self.is_audio_channel_opened(),
        }

    async def abort_speaking(self, reason):
        """
        中止语音输出.
        """

        if self.aborted:
            logger.debug(f"已经中止，忽略重复的中止请求: {reason}")
            return

        logger.info(f"中止语音输出，原因: {reason}")
        self.aborted = True
        await self.protocol.send_abort_speaking(reason)
        await self.set_device_state(DeviceState.IDLE)

    # -------------------------
    # UI 辅助：供插件或工具直接调用
    # -------------------------
    def set_chat_message(self, role, message: str) -> None:
        """将文本更新转发为 UI 可识别的 JSON 消息（复用 UIPlugin 的 on_incoming_json）。
        role: "assistant" | "user" 影响消息类型映射。
        """
        try:
            msg_type = "tts" if str(role).lower() == "assistant" else "stt"
        except Exception:
            msg_type = "tts"
        payload = {"type": msg_type, "text": message}
        # 通过插件事件总线异步派发
        self.spawn(self.plugins.notify_incoming_json(payload), "ui:text_update")

    def set_emotion(self, emotion: str) -> None:
        """
        设置情绪表情：通过 UIPlugin 的 on_incoming_json 路由。
        """
        payload = {"type": "llm", "emotion": emotion}
        self.spawn(self.plugins.notify_incoming_json(payload), "ui:emotion_update")

    # -------------------------
    # 关停
    # -------------------------
    async def shutdown(self):
        if not self.running:
            return
        logger.info("正在关闭Application...")
        self.running = False

        if self._shutdown_event is not None:
            self._shutdown_event.set()

        try:
            # 取消所有登记任务
            if self._tasks:
                for t in list(self._tasks):
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)
                self._tasks.clear()

            # 关闭协议（限时，避免阻塞退出）
            if self.protocol:
                try:
                    try:
                        self._main_loop.create_task(self.protocol.close_audio_channel())
                    except asyncio.TimeoutError:
                        logger.warning("关闭协议超时，跳过等待")
                except Exception as e:
                    logger.error(f"关闭协议失败: {e}")

            # 插件：stop/shutdown
            try:
                await self.plugins.stop_all()
            except Exception:
                pass
            try:
                await self.plugins.shutdown_all()
            except Exception:
                pass

            logger.info("Application 关闭完成")
        except Exception as e:
            logger.error(f"关闭应用时出错: {e}", exc_info=True)
