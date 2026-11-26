import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from src.constants.constants import AudioConfig
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger
from src.utils.resource_finder import resource_finder

logger = get_logger(__name__)

# 停止信号：唤醒 stop() 中阻塞等待的检测循环
_STOP_SENTINEL = object()


class WakeWordDetector:

    def __init__(self):
        # 基本属性
        self.audio_codec = None
        self.is_running_flag = False
        self.paused = False
        # 对话期间外部静音检测（避免 TTS/回声再次触发唤醒）
        self.muted = False
        self.detection_task = None

        # 内部状态
        self._stopping = False
        # 保护 sherpa-onnx 原生对象的访问（启动/停止/推理可能跨任务）
        self._onnx_lock = threading.Lock()

        # 音频数据队列（在 start() 中创建，确保绑定当前事件循环）
        self._audio_queue: Optional[asyncio.Queue] = None
        # 回调线程 → 事件循环的桥接：入队走 call_soon_threadsafe，
        # 检测循环用 await queue.get() 阻塞等待，不再 5ms 空转轮询。
        self._loop: asyncio.AbstractEventLoop | None = None
        # ONNX 推理放在单线程 executor，避免阻塞事件循环且保持帧顺序。
        self._executor: Optional[ThreadPoolExecutor] = None
        # paused/muted 时让检测循环挂起等待，不消耗 CPU。
        self._run_event: Optional[asyncio.Event] = None

        # 防重复触发机制
        self.last_detection_time = 0
        self.detection_cooldown = 1.5  # 1.5秒冷却时间

        # 回调函数
        self.on_detected_callback: Optional[Callable] = None
        self.on_error: Optional[Callable] = None

        # 配置检查
        config = ConfigManager.get_instance()
        if not config.get_config("WAKE_WORD_OPTIONS.USE_WAKE_WORD", False):
            logger.info("唤醒词功能已禁用")
            self.enabled = False
            return

        # 基本参数初始化
        self.enabled = True
        self.sample_rate = AudioConfig.INPUT_SAMPLE_RATE

        # Sherpa-ONNX KWS组件
        self.keyword_spotter = None
        self.stream = None

        # 初始化配置
        self._load_config(config)
        self._init_kws_model()
        self._validate_config()

    def _load_config(self, config):
        """
        加载配置参数.
        """
        # 模型路径配置
        model_path = config.get_config("WAKE_WORD_OPTIONS.MODEL_PATH", "models")
        self.model_dir = resource_finder.find_directory(model_path)

        if self.model_dir is None:
            # 兜底方案：尝试直接使用路径
            self.model_dir = Path(model_path)
            logger.warning(
                f"ResourceFinder未找到模型目录，使用原始路径: {self.model_dir}"
            )

        # KWS参数配置 - 优化速度
        self.num_threads = config.get_config(
            "WAKE_WORD_OPTIONS.NUM_THREADS", 2
        )  # 增加线程数
        self.provider = config.get_config("WAKE_WORD_OPTIONS.PROVIDER", "cpu")
        self.max_active_paths = config.get_config(
            "WAKE_WORD_OPTIONS.MAX_ACTIVE_PATHS", 2
        )  # 减少搜索路径
        self.keywords_score = config.get_config(
            "WAKE_WORD_OPTIONS.KEYWORDS_SCORE", 1.8
        )  # 降低分数提升速度
        self.keywords_threshold = config.get_config(
            "WAKE_WORD_OPTIONS.KEYWORDS_THRESHOLD", 0.2
        )  # 降低阈值提高灵敏度
        self.num_trailing_blanks = config.get_config(
            "WAKE_WORD_OPTIONS.NUM_TRAILING_BLANKS", 1
        )

        logger.info(
            f"KWS配置加载完成 - 阈值: {self.keywords_threshold}, 分数: {self.keywords_score}"
        )

    def _init_kws_model(self):
        """
        初始化Sherpa-ONNX KeywordSpotter模型.
        """
        try:
            # 延迟导入：sherpa_onnx 是大型原生扩展，默认关闭唤醒词时无需加载。
            import sherpa_onnx

            # 检查模型文件
            encoder_path = self.model_dir / "encoder.onnx"
            decoder_path = self.model_dir / "decoder.onnx"
            joiner_path = self.model_dir / "joiner.onnx"
            tokens_path = self.model_dir / "tokens.txt"

            # 优先使用「唤醒词目录」中的用户自定义关键词；不存在时回退模型目录。
            # 这样设置页的数据目录覆盖真正生效，且兼容旧版本。
            keywords_path = self.model_dir / "keywords.txt"
            try:
                from src.utils.resource_finder import get_keywords_dir

                user_keywords = get_keywords_dir(create=False) / "keywords.txt"
                if user_keywords.exists():
                    keywords_path = user_keywords
                    logger.info(f"使用唤醒词目录中的关键词文件: {keywords_path}")
            except Exception:
                pass

            required_files = [
                encoder_path,
                decoder_path,
                joiner_path,
                tokens_path,
                keywords_path,
            ]
            for file_path in required_files:
                if not file_path.exists():
                    raise FileNotFoundError(f"模型文件不存在: {file_path}")

            logger.info(f"加载Sherpa-ONNX KeywordSpotter模型: {self.model_dir}")

            # 创建KeywordSpotter
            self.keyword_spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(tokens_path),
                encoder=str(encoder_path),
                decoder=str(decoder_path),
                joiner=str(joiner_path),
                keywords_file=str(keywords_path),
                num_threads=self.num_threads,
                sample_rate=self.sample_rate,
                feature_dim=80,
                max_active_paths=self.max_active_paths,
                keywords_score=self.keywords_score,
                keywords_threshold=self.keywords_threshold,
                num_trailing_blanks=self.num_trailing_blanks,
                provider=self.provider,
            )

            logger.info("Sherpa-ONNX KeywordSpotter模型加载成功")

        except Exception as e:
            logger.error(f"Sherpa-ONNX KeywordSpotter初始化失败: {e}", exc_info=True)
            self.enabled = False

    def on_detected(self, callback: Callable):
        """
        设置检测到唤醒词的回调函数.
        """
        self.on_detected_callback = callback

    def _update_run_event(self) -> None:
        """根据 paused/muted 更新检测循环的等待事件."""
        event = self._run_event
        if event is None:
            return
        if self.paused or self.muted:
            event.clear()
        else:
            event.set()

    def set_muted(self, muted: bool) -> None:
        """对话期间临时静音检测（麦克风采集仍在继续）."""
        self.muted = bool(muted)
        # 静音/恢复时都丢弃积压帧：避免恢复后先喂入对话期间的 TTS 回声。
        queue = self._audio_queue
        if queue is not None:
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self._update_run_event()

    def _enqueue_audio(self, audio_data: np.ndarray) -> None:
        """事件循环线程：把回调线程送来的音频放入队列（满时丢最旧）."""
        queue = self._audio_queue
        if queue is None or self._stopping:
            return
        try:
            queue.put_nowait(audio_data)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(audio_data)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def on_audio_data(self, audio_data: np.ndarray):
        """PortAudio 回调线程：只做一次 copy + 线程安全调度，不碰 asyncio 队列."""
        if not self.enabled or not self.is_running_flag or self.paused or self.muted:
            return

        loop = self._loop
        if loop is None or loop.is_closed():
            return

        try:
            # 缓冲由 AudioCodec 复用，必须 copy 后才能跨线程交给检测循环
            loop.call_soon_threadsafe(self._enqueue_audio, audio_data.copy())
        except RuntimeError:
            # 事件循环已关闭（关闭收尾阶段），直接丢弃
            pass
        except Exception as e:
            logger.debug(f"音频数据入队失败: {type(e).__name__}: {e}")

    async def start(self, audio_codec) -> bool:
        if self.is_running_flag:
            return True

        if not self.enabled:
            logger.warning("唤醒词功能未启用")
            return False

        if not self.keyword_spotter:
            logger.error("KeywordSpotter未初始化")
            return False

        try:
            self.audio_codec = audio_codec
            self.is_running_flag = True
            self.paused = False
            self.muted = False
            self._stopping = False

            # 队列/事件/executor 创建在事件循环内，避免构造时未绑定循环
            self._loop = asyncio.get_running_loop()
            self._run_event = asyncio.Event()
            self._update_run_event()
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="kws-infer"
            )
            self._audio_queue = asyncio.Queue(maxsize=100)

            # 创建检测流
            with self._onnx_lock:
                self.stream = self.keyword_spotter.create_stream()

            # 注册为音频监听器（观察者模式）
            self.audio_codec.add_audio_listener(self)

            # 启动检测任务
            self.detection_task = asyncio.create_task(
                self._detection_loop(), name="wake_word:detect"
            )

            logger.info("Sherpa-ONNX KeywordSpotter检测器启动成功（观察者模式）")
            return True
        except Exception as e:
            logger.error(f"启动KeywordSpotter检测器失败: {e}", exc_info=True)
            self.enabled = False
            await self.stop()
            return False

    def _detach_audio(self):
        """移除音频监听器（可能被检测循环/stop 重复调用）."""
        codec = self.audio_codec
        if codec is not None:
            try:
                codec.remove_audio_listener(self)
            except Exception as e:
                logger.debug(f"移除音频监听器失败: {e}")
            finally:
                self.audio_codec = None

    async def _detection_loop(self):
        """
        检测循环.
        """
        error_count = 0
        MAX_ERRORS = 5

        try:
            while self.is_running_flag and not self._stopping:
                try:
                    if self.paused or self.muted:
                        # 暂停/静音期间完全挂起，不再 5ms 空转
                        event = self._run_event
                        if event is not None:
                            await event.wait()
                        else:
                            await asyncio.sleep(0.05)
                        continue

                    queue = self._audio_queue
                    if queue is None:
                        break
                    audio_data = await queue.get()
                    try:
                        if (
                            audio_data is _STOP_SENTINEL
                            or audio_data is None
                            or self._stopping
                        ):
                            break
                        # ONNX 推理放单线程 executor：不阻塞事件循环，且保持帧顺序
                        executor = self._executor
                        if executor is not None:
                            result = await asyncio.get_running_loop().run_in_executor(
                                executor, self._process_frame, audio_data
                            )
                        else:
                            result = self._process_frame(audio_data)
                        if result:
                            await self._handle_detection_result(result)
                        error_count = 0
                    finally:
                        queue.task_done()

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    error_count += 1
                    logger.error(
                        f"KWS检测循环错误({error_count}/{MAX_ERRORS}): {e}",
                        exc_info=True,
                    )

                    # 调用错误回调
                    if self.on_error:
                        try:
                            if asyncio.iscoroutinefunction(self.on_error):
                                await self.on_error(e)
                            else:
                                self.on_error(e)
                        except Exception as callback_error:
                            logger.error(f"执行错误回调时失败: {callback_error}")

                    if error_count >= MAX_ERRORS:
                        logger.critical("达到最大错误次数，停止KWS检测")
                        break
                    await asyncio.sleep(1)
        finally:
            # 任务无论正常结束还是被取消，都确保脱离音频回调
            self._detach_audio()
            if self.detection_task is asyncio.current_task():
                self.detection_task = None

    def _process_frame(self, audio_data) -> str:
        """在 executor 线程把一帧送入 KWS，返回检测到的关键词（无则空串）."""
        if audio_data is None or len(audio_data) == 0 or self._stopping:
            return ""

        with self._onnx_lock:
            if self._stopping or self.stream is None or self.keyword_spotter is None:
                return ""

            # 转换音频格式为 float32
            if audio_data.dtype == np.int16:
                samples = audio_data.astype(np.float32) / 32768.0
            else:
                samples = audio_data.astype(np.float32)

            self.stream.accept_waveform(sample_rate=self.sample_rate, waveform=samples)

            result = ""
            if self.keyword_spotter.is_ready(self.stream):
                self.keyword_spotter.decode_stream(self.stream)
                result = self.keyword_spotter.get_result(self.stream)
                if result:
                    self.keyword_spotter.reset_stream(self.stream)

        return result or ""

    async def _handle_detection_result(self, result):
        """
        处理检测结果.
        """
        # 防重复触发检查
        current_time = time.time()
        if current_time - self.last_detection_time < self.detection_cooldown:
            return

        self.last_detection_time = current_time
        logger.info(f"[唤醒词] 检测到: {result!r}")

        # 短暂暂停检测，避免唤醒流程中的 TTS/回声再次触发
        self.paused = True
        self._update_run_event()
        try:
            # 触发回调
            if self.on_detected_callback:
                try:
                    if asyncio.iscoroutinefunction(self.on_detected_callback):
                        await self.on_detected_callback(result, result)
                    else:
                        self.on_detected_callback(result, result)
                except Exception as e:
                    logger.error(f"唤醒词回调执行失败: {e}", exc_info=True)
        finally:
            if not self._stopping:
                await asyncio.sleep(0.3)
                # 丢弃回调期间积压的音频，避免重复检测
                queue = self._audio_queue
                if queue is not None:
                    while True:
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
            self.paused = False
            self._update_run_event()

    async def stop(self):
        """
        停止检测器（有界等待，绝不允许卡死关闭流程）.
        """
        if self._stopping and self.detection_task is None:
            return

        self._stopping = True
        self.is_running_flag = False
        # 若检测循环正挂起在 paused/muted 等待事件，先唤醒它
        event = self._run_event
        if event is not None:
            event.set()

        # 先从 AudioCodec 移除监听器，并释放强引用，避免关闭后 codec/soxr 泄漏
        self._detach_audio()

        queue = self._audio_queue
        task = self.detection_task
        try:
            if queue is not None:
                try:
                    queue.put_nowait(_STOP_SENTINEL)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                        queue.put_nowait(_STOP_SENTINEL)
                    except (asyncio.QueueEmpty, asyncio.QueueFull):
                        pass

            if task is not None and task is not asyncio.current_task():
                try:
                    # 给检测循环 1 秒正常退出（吃到 STOP 哨兵）
                    await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                except Exception as e:
                    logger.debug(f"检测任务结束异常: {e}")
                finally:
                    if not task.done():
                        task.cancel()
                        try:
                            # 取消后再给 1 秒；仍不退出就放弃等待，绝不阻塞关停
                            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            logger.warning("唤醒词检测任务未能及时退出，已放弃等待")
                        except Exception as e:
                            logger.debug(f"取消检测任务时异常: {e}")
        finally:
            if self.detection_task is task:
                self.detection_task = None
            self._audio_queue = None
            self._loop = None
            self._run_event = None
            executor, self._executor = self._executor, None
            if executor is not None:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
            # 注意：不要在这里释放 self.stream。sherpa-onnx 的 KeywordSpotter
            # 在 C++ 层拥有 stream 的所有权，先释放 stream 再释放 spotter 会
            # 导致析构时 double free；检测器对象本身随应用退出一起回收即可。
            self.paused = False
            self._stopping = False

        logger.info("Sherpa-ONNX KeywordSpotter检测器已停止")

    def _validate_config(self):
        """
        验证配置参数.
        """
        if not self.enabled:
            return

        # 验证阈值参数
        if not 0.1 <= self.keywords_threshold <= 1.0:
            logger.warning(f"关键词阈值 {self.keywords_threshold} 超出范围，重置为0.25")
            self.keywords_threshold = 0.25

        if not 0.1 <= self.keywords_score <= 10.0:
            logger.warning(f"关键词分数 {self.keywords_score} 超出范围，重置为2.0")
            self.keywords_score = 2.0

        logger.info(
            f"KWS配置验证完成 - 阈值: {self.keywords_threshold}, 分数: {self.keywords_score}"
        )
