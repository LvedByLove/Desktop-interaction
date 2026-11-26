import asyncio
import gc
import threading
import time
from collections import deque
from typing import Callable, List, Optional, Protocol

import numpy as np
import opuslib
import sounddevice as sd
import soxr

from src.audio_codecs.aec_processor import AECProcessor
from src.audio_codecs.audio_channels import (
    OUTPUT_CHANNEL_MUSIC,
    OUTPUT_CHANNEL_TTS,
    OUTPUT_CHANNELS,
)
from src.constants.constants import AudioConfig
from src.utils.audio_utils import (
    downmix_to_mono,
    get_windows_default_output_name,
    select_audio_device,
)
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# 每个输出通道最多缓存约 10 秒音频（20ms/帧 × 500 帧）。
_MAX_OUTPUT_FRAMES = 500
# 音频回调状态异常的最小告警间隔（秒）：避免 xrun 时每帧同步写日志。
_STREAM_STATUS_LOG_INTERVAL = 5.0

# 输出流回调块的最小长度（毫秒）。
# 设备原生帧长通常是 20ms，Python 回调只要被 GIL 拖住超过一个帧长就会
# 触发 PortAudio underflow。启动阶段插件初始化、拖放时缩略图/文件处理
# 都会短暂占用主线程，因此把输出回调块放宽到至少 40ms 留出余量；服务端
# 协议帧仍是 AudioConfig.FRAME_DURATION，不影响 TTS / 音乐的帧对齐。
_OUTPUT_BLOCKSIZE_MIN_MS = 40

# 输出流启动后的预热期（秒）：这段时间内插件初始化、资源加载会短暂占用
# 主线程，首个音频回调偶发错过 deadline 属于正常现象；此时还没有播放任何
# 音频，underflow 不影响听感，因此只记 DEBUG，不刷 WARNING。
_STREAM_STATUS_STARTUP_GRACE = 2.0


class _SampleBuffer:
    """实时重采样缓冲：按 ndarray 块累积，避免逐样本 popleft。

    PortAudio 回调线程写入/取帧，主线程可能 clear()，因此用锁保护。
    取帧只做块级切片/一次 concatenate，绝不对样本数做 Python 循环。
    """

    __slots__ = ("_blocks", "_size", "_lock")

    def __init__(self):
        self._blocks: deque = deque()
        self._size = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def extend(self, samples) -> None:
        if samples is None:
            return
        arr = np.asarray(samples, dtype=np.float32)
        n = len(arr)
        if n == 0:
            return
        with self._lock:
            self._blocks.append(arr)
            self._size += n

    def clear(self) -> int:
        with self._lock:
            count = self._size
            self._blocks.clear()
            self._size = 0
            return count

    def take(self, n: int):
        """取出恰好 n 个样本；数据不足时返回 None。"""
        if n <= 0:
            return np.empty(0, dtype=np.float32)
        with self._lock:
            if self._size < n:
                return None
            parts = []
            need = n
            while need > 0:
                block = self._blocks[0]
                if len(block) <= need:
                    parts.append(block)
                    self._blocks.popleft()
                    need -= len(block)
                else:
                    parts.append(block[:need])
                    self._blocks[0] = block[need:]
                    need = 0
            self._size -= n
        if len(parts) == 1:
            return parts[0]
        return np.concatenate(parts)


class AudioListener(Protocol):
    """
    音频监听器协议（用于唤醒词检测等）

    注意：``audio_data`` 是回调线程复用的缓冲区视图，监听器如需保留
    必须自行 ``copy()``，不得原地修改。
    """

    def on_audio_data(self, audio_data: np.ndarray) -> None:
        """
        接收音频数据的回调.
        """
        ...


class AudioCodec:
    """
    音频编解码器 - 音频格式适配器 + 编解码通道

    核心职责：
    1. 设备管理：设备选择、流创建、错误恢复
    2. 格式转换：采样率转换、声道转换、帧重组
    3. 编解码：PCM ↔ Opus
    4. 流控缓冲：防溢出、防卡顿

    设计原则：
    - 设备层：完全按照设备原生能力创建（采样率、声道数）
    - 转换层：自动适配设备格式与服务端协议
    - 解耦原则：通过回调和观察者模式解耦外部依赖

    转换流程：
    - 输入：设备原生 → 下混+重采样 → 16kHz单声道 → Opus编码 → 回调发送
    - 输出：接收Opus → 解码24kHz单声道 → 重采样+上混 → 设备原生播放
    """

    def __init__(self, audio_processor: Optional[AECProcessor] = None):
        """初始化音频编解码器.

        Args:
            audio_processor: 可选的音频处理器（AEC等），通过依赖注入解耦
        """
        # 获取配置管理器
        self.config = ConfigManager.get_instance()

        # Opus编解码器
        self.opus_encoder = None
        self.opus_decoder = None

        # 设备原生信息
        self.device_input_sample_rate = None
        self.device_output_sample_rate = None
        self.input_channels = None
        self.output_channels = None
        self.mic_device_id = None
        self.speaker_device_id = None

        # 重采样器（按需创建）
        self.input_resampler = None
        self._output_resamplers = {channel: None for channel in OUTPUT_CHANNELS}

        # 重采样缓冲区（按块累积，避免实时线程逐样本 popleft）
        self._resample_input_buffer = _SampleBuffer()
        self._resample_output_buffers = {
            channel: _SampleBuffer() for channel in OUTPUT_CHANNELS
        }

        # 输出混音/重采样 scratch（仅在输出回调线程使用，回调内复用）
        self._render_scratch = {
            channel: np.zeros(0, dtype=np.float32) for channel in OUTPUT_CHANNELS
        }
        self._resample_input_scratch = {
            channel: np.zeros(0, dtype=np.float32) for channel in OUTPUT_CHANNELS
        }
        self._mix_scratch = np.zeros(0, dtype=np.float32)
        self._music_mix_scratch = np.zeros(0, dtype=np.float32)
        # 输入回调转 int16 的复用缓冲（回调线程专用）
        self._input_int16_scratch = np.zeros(
            AudioConfig.INPUT_FRAME_SIZE, dtype=np.int16
        )

        # 转换标记
        self._need_input_downmix = False
        self._need_output_upmix = False

        # 设备帧大小
        self._device_input_frame_size = None
        self._device_output_frame_size = None

        # 音频流对象
        self.input_stream = None
        self.output_stream = None

        # 播放队列：PortAudio 回调线程消费、事件循环线程生产。
        # 用 deque 而不是 asyncio.Queue：deque 的 append/popleft 是线程安全的，
        # 且不会像 asyncio.Queue 那样在跨线程 get_nowait 时操作 loop Future。
        self._output_buffers = {channel: deque() for channel in OUTPUT_CHANNELS}
        self._last_status_log_at = 0.0
        self._output_stream_started_at = 0.0
        self._output_drop_count = 0
        self._output_drop_log_at = 0.0

        # 通道音量和音乐 ducking
        self._channel_volumes = {
            OUTPUT_CHANNEL_TTS: 1.0,
            OUTPUT_CHANNEL_MUSIC: 1.0,
        }
        self._music_duck_volume = 0.25
        self._music_gain_current = 1.0
        self._music_gain_target = 1.0

        # 回调和监听器（解耦外部依赖）
        self._encoded_callback: Optional[Callable] = None
        self._audio_listeners: List[AudioListener] = []
        # 监听器在 PortAudio 回调线程里遍历，启动/停止时在主线程增删，
        # 必须加锁，否则关闭瞬间可能触发 "list changed size during iteration"
        self._listeners_lock = threading.Lock()

        # 音频处理器（可选注入）
        self.audio_processor = audio_processor
        self._aec_enabled = False

        # 状态标记
        self._is_closing = False

        # 输入/输出流 finished 回调完成信号（PortAudio 后台线程触发），
        # 用于在 close() 中等待流彻底释放，避免进程退出时 Pa_Terminate 与
        # 尚未结束的流回调线程发生竞态导致卡顿/挂起
        self._input_finished_evt = threading.Event()
        self._output_finished_evt = threading.Event()

        # 输出设备跟随系统默认：记录输出流当前绑定的默认设备名称，用于检测热插拔切换
        self._bound_output_name = None
        self._output_monitor_task = None

    async def initialize(self):
        """初始化音频设备和编解码器.

        策略：
        1. 首次运行：自动选择最佳设备，保存配置
        2. 后续运行：从配置加载设备信息
        3. 按设备原生能力创建流（采样率、声道数）
        4. 自动创建转换器（采样率、声道）
        """
        try:
            # 加载或初始化设备配置
            await self._load_device_config()

            # 创建Opus编解码器
            await self._create_opus_codecs()

            # 创建重采样器和转换标记
            await self._create_resamplers()

            # 创建音频流（使用设备原生格式）
            await self._create_streams()

            # 启动输出设备热插拔监控（跟随系统默认输出设备切换，如插入耳机）
            self._output_monitor_task = asyncio.create_task(
                self._monitor_output_device()
            )

            # 初始化AEC处理器（如果提供）
            if self.audio_processor:
                try:
                    await self.audio_processor.initialize()
                    self._aec_enabled = self.audio_processor._is_initialized
                    logger.info(
                        f"AEC处理器已初始化: {'启用' if self._aec_enabled else '禁用'}"
                    )
                except Exception as e:
                    logger.warning(f"AEC处理器初始化失败: {e}")
                    self._aec_enabled = False

            logger.info("AudioCodec 初始化完成")

        except Exception as e:
            logger.error(f"初始化音频设备失败: {e}")
            await self.close()
            raise

    async def _load_device_config(self):
        """
        加载或初始化设备配置.
        """
        audio_config = self.config.get_config("AUDIO_DEVICES", {}) or {}

        input_device_id = audio_config.get("input_device_id")
        output_device_id = audio_config.get("output_device_id")
        output_pinned = bool(audio_config.get("output_device_pinned", False))

        # 首次运行：自动选择设备
        if input_device_id is None:
            logger.info("首次运行，自动选择音频设备...")
            await self._auto_detect_devices()
            return

        # 从配置加载
        self.mic_device_id = input_device_id
        self.device_input_sample_rate = audio_config.get(
            "input_sample_rate", AudioConfig.INPUT_SAMPLE_RATE
        )
        self.input_channels = audio_config.get("input_channels", 1)

        # 输出设备：未固定时跟随系统默认（耳机插入后自动切换路由）
        if output_pinned and output_device_id is not None:
            self.speaker_device_id = output_device_id
            self.device_output_sample_rate = audio_config.get(
                "output_sample_rate", AudioConfig.OUTPUT_SAMPLE_RATE
            )
            self.output_channels = audio_config.get("output_channels", 1)
        else:
            self.speaker_device_id = None
            # 设备枚举是同步 PortAudio 调用，放线程里避免阻塞事件循环
            default_info = await asyncio.to_thread(self._resolve_default_output)
            if default_info:
                _, self.device_output_sample_rate, self.output_channels = default_info
            else:
                # 解析默认设备失败时回退到已存配置（可能已失效，但至少可尝试）
                self.device_output_sample_rate = audio_config.get(
                    "output_sample_rate", AudioConfig.OUTPUT_SAMPLE_RATE
                )
                self.output_channels = audio_config.get("output_channels", 1)
            logger.info("输出设备跟随系统默认输出")

        # 计算设备帧大小
        self._device_input_frame_size = int(
            self.device_input_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
        )
        self._device_output_frame_size = int(
            self.device_output_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
        )

        logger.info(
            f"加载设备配置 | 输入: {self.device_input_sample_rate}Hz {self.input_channels}ch | "
            f"输出: {self.device_output_sample_rate}Hz {self.output_channels}ch"
        )

    def _resolve_default_output(self):
        """
        解析当前系统默认输出设备的 (device, sample_rate, channels)。
        device 固定返回 None，表示让 sounddevice 使用系统默认设备。
        """
        try:
            info = sd.query_devices(kind="output")
            sample_rate = int(info.get("default_samplerate") or AudioConfig.OUTPUT_SAMPLE_RATE)
            channels = min(
                int(info.get("max_output_channels") or 1),
                AudioConfig.MAX_OUTPUT_CHANNELS,
            )
            return None, sample_rate, channels
        except Exception as e:
            logger.debug(f"解析默认输出设备失败: {e}")
            return None

    def _get_live_default_output_name(self):
        """
        实时获取 Windows 当前默认输出设备的友好名称（不依赖 PortAudio 缓存）.
        """
        return get_windows_default_output_name()

    # 输入设备可用的 HostAPI 优先级（越靠前越优先）
    _INPUT_HOSTAPI_PRIORITY = ("wasapi", "wdm-ks", "directsound", "mme")

    @staticmethod
    def _query_device_info(index) -> Optional[dict]:
        """查询设备信息；索引无效时返回 None（不抛异常）。"""
        if index is None:
            return None
        try:
            return sd.query_devices(index)
        except Exception:
            return None

    def _is_input_capable(self, index) -> bool:
        """索引是否指向一个具备输入声道的设备。

        Windows 下设备索引会随蓝牙/USB 设备热插拔整体移位，配置里保存的
        整数索引可能已经指向一个纯输出设备（max_input_channels == 0），
        直接打开会报 PaErrorCode -9998 (Invalid number of channels)。
        """
        info = self._query_device_info(index)
        return bool(info and int(info.get("max_input_channels") or 0) > 0)

    @staticmethod
    def _normalize_device_name(name) -> str:
        return str(name or "").strip().casefold()

    def _find_input_device_by_name(
        self, name: str, prefer_hostapi: Optional[int] = None
    ) -> Optional[int]:
        """按设备名称反查输入设备索引（名称匹配，忽略大小写与首尾空格）。

        同名设备通常会出现在多个 HostAPI 下（MME/DirectSound/WASAPI...），
        优先沿用旧设备所在 HostAPI，其次按 _INPUT_HOSTAPI_PRIORITY 选择。
        """
        target = self._normalize_device_name(name)
        if not target:
            return None
        try:
            devices = list(sd.query_devices())
            hostapis = list(sd.query_hostapis())
        except Exception:
            return None

        candidates = [
            i
            for i, d in enumerate(devices)
            if int(d.get("max_input_channels") or 0) > 0
            and self._normalize_device_name(d.get("name")) == target
        ]
        if not candidates:
            return None

        def priority(index: int) -> tuple[int, int]:
            hostapi = devices[index].get("hostapi")
            if prefer_hostapi is not None and hostapi == prefer_hostapi:
                return (-1, index)
            api_name = ""
            try:
                api_name = str(hostapis[int(hostapi)].get("name", "")).casefold()
            except Exception:
                pass
            for rank, token in enumerate(self._INPUT_HOSTAPI_PRIORITY):
                if token in api_name:
                    return (rank, index)
            return (len(self._INPUT_HOSTAPI_PRIORITY), index)

        return min(candidates, key=priority)

    def _probe_input_params(self, index: int) -> Optional[tuple[int, int]]:
        """探测设备实际支持的 (采样率, 声道数)。

        优先沿用配置中的采样率与声道数，失败时退回设备默认采样率，
        最后退化为单声道。返回 None 表示设备无法作为输入使用。
        """
        info = self._query_device_info(index)
        if not info:
            return None
        max_channels = int(info.get("max_input_channels") or 0)
        if max_channels <= 0:
            return None

        desired_channels = max(1, min(int(self.input_channels or 1), max_channels))

        rates: List[int] = []
        for candidate in (
            self.device_input_sample_rate,
            info.get("default_samplerate"),
            AudioConfig.INPUT_SAMPLE_RATE,
            48000,
            44100,
            16000,
        ):
            try:
                rate = int(round(float(candidate)))
            except (TypeError, ValueError):
                continue
            if rate > 0 and rate not in rates:
                rates.append(rate)

        for channels in (desired_channels, 1):
            for rate in rates:
                try:
                    sd.check_input_settings(
                        device=index,
                        samplerate=rate,
                        channels=channels,
                        dtype=np.float32,
                    )
                    return rate, channels
                except Exception:
                    continue
        return None

    async def _rebuild_input_resampler(self, rate: int) -> None:
        """按新的设备采样率重建输入重采样器并丢弃残留缓冲。"""
        if self.input_resampler is not None:
            await self._cleanup_resampler(self.input_resampler, "输入")
            self.input_resampler = None
        if self._resample_input_buffer:
            self._resample_input_buffer.clear()
        if rate != AudioConfig.INPUT_SAMPLE_RATE:
            self.input_resampler = soxr.ResampleStream(
                rate,
                AudioConfig.INPUT_SAMPLE_RATE,
                num_channels=1,  # 重采样器处理单声道（下混后）
                dtype="float32",
                quality="QQ",  # 快速质量（低延迟）
            )
            logger.info(f"输入重采样: {rate}Hz → 16kHz")

    async def _apply_input_params(
        self, index: int, rate: int, channels: int, *, persist: bool = False
    ) -> None:
        """应用已校验的输入设备参数（必要时重建重采样器）。

        persist=True 时把修正后的设备信息写回配置；仅当设备按名称明确
        匹配成功时才写回，避免把“设备临时不可用时的兜底选择”覆盖掉
        用户原本的偏好（例如蓝牙耳机暂时断开时退回的本机麦克风）。
        """
        info = self._query_device_info(index) or {}
        old_index = self.mic_device_id
        old_rate = self.device_input_sample_rate
        old_channels = self.input_channels

        if index != old_index:
            logger.warning(
                f"输入设备已重新定位: {old_index} -> {index} "
                f"({info.get('name', 'Unknown')})"
            )
        if old_rate != rate:
            logger.warning(f"输入采样率已调整: {old_rate} -> {rate}")
        if old_channels != channels:
            logger.warning(f"输入声道数已调整: {old_channels} -> {channels}")

        self.mic_device_id = index
        if rate != old_rate or (
            rate != AudioConfig.INPUT_SAMPLE_RATE and self.input_resampler is None
        ):
            await self._rebuild_input_resampler(rate)
        elif index != old_index and self._resample_input_buffer:
            # 仅切换设备时同样丢弃旧设备的残留采样
            self._resample_input_buffer.clear()
        self.device_input_sample_rate = rate
        self.input_channels = channels
        self._need_input_downmix = channels > 1
        self._device_input_frame_size = int(
            rate * (AudioConfig.FRAME_DURATION / 1000)
        )

        if persist and (index, rate, channels) != (
            old_index,
            old_rate,
            old_channels,
        ):
            try:
                self.config.update_config("AUDIO_DEVICES.input_device_id", index)
                self.config.update_config(
                    "AUDIO_DEVICES.input_device_name", info.get("name")
                )
                self.config.update_config("AUDIO_DEVICES.input_sample_rate", rate)
                self.config.update_config("AUDIO_DEVICES.input_channels", channels)
            except Exception as e:
                logger.debug(f"保存修正后的输入设备配置失败: {e}")

    async def _ensure_input_device(self) -> bool:
        """确保输入设备索引与参数可用；必要时自动修正。

        返回 False 表示当前没有可用的麦克风输入设备。
        """
        try:
            name = self.config.get_config("AUDIO_DEVICES", {}).get(
                "input_device_name"
            )
        except Exception:
            name = None

        # 仅当旧索引仍是一个有效输入设备时，才把它所在 HostAPI 作为偏好；
        # 旧索引若已移位成纯输出设备，其 HostAPI 对麦克风没有参考意义。
        prefer_hostapi = None
        current = self._query_device_info(self.mic_device_id)
        if current is not None and self._is_input_capable(self.mic_device_id):
            prefer_hostapi = current.get("hostapi")

        # 1) 名称匹配：索引会随热插拔移位，名称稳定，可安全写回配置
        index = self._find_input_device_by_name(name, prefer_hostapi)
        persist = index is not None

        # 2) 旧配置没有名称时，旧索引仍指向输入设备则复用
        if index is None and self._is_input_capable(self.mic_device_id):
            index = self.mic_device_id

        # 3) 兜底：系统默认/最佳输入设备（不覆盖配置中的设备偏好）
        if index is None:
            fallback = select_audio_device("input", include_virtual=False)
            if fallback:
                index = int(fallback["index"])
        if index is None:
            logger.error("未找到可用的麦克风输入设备")
            return False

        params = self._probe_input_params(index)
        if params is None:
            fallback = select_audio_device("input", include_virtual=False)
            if fallback and int(fallback["index"]) != index:
                index = int(fallback["index"])
                persist = False
                params = self._probe_input_params(index)
        if params is None:
            logger.error(f"麦克风输入设备不可用: index={index}")
            return False

        rate, channels = params
        await self._apply_input_params(index, rate, channels, persist=persist)
        return True

    async def _fallback_input_device(self) -> bool:
        """打开输入流失败时，强制退回系统默认输入设备。"""
        info = select_audio_device("input", include_virtual=False)
        if not info:
            return False
        index = int(info["index"])
        params = self._probe_input_params(index)
        if params is None:
            return False
        rate, channels = params
        await self._apply_input_params(index, rate, channels, persist=False)
        return True

    async def _auto_detect_devices(self):
        """
        自动检测并选择最佳设备.
        """
        # 输入设备：智能选择（PortAudio 枚举放线程，避免启动卡事件循环）
        in_info = await asyncio.to_thread(
            select_audio_device, "input", include_virtual=False
        )
        if not in_info:
            raise RuntimeError("无法找到可用的音频输入设备")

        # 输出设备：跟随系统默认（插入耳机后由系统自动切换路由），解析默认输出能力
        self.speaker_device_id = None
        default_output = await asyncio.to_thread(self._resolve_default_output)
        if not default_output:
            raise RuntimeError("无法找到可用的音频输出设备")
        _, self.device_output_sample_rate, self.output_channels = default_output

        # 获取默认输出设备名称（用于 UI 显示与日志）
        try:
            default_output_name = await asyncio.to_thread(
                lambda: sd.query_devices(kind="output").get("name")
            )
        except Exception:
            default_output_name = None

        # 限制输入声道数（避免100+声道设备性能浪费）
        raw_input_channels = in_info["channels"]
        self.input_channels = min(raw_input_channels, AudioConfig.MAX_INPUT_CHANNELS)

        self.mic_device_id = in_info["index"]
        self.device_input_sample_rate = in_info["sample_rate"] or AudioConfig.INPUT_SAMPLE_RATE

        # 计算帧大小
        self._device_input_frame_size = int(
            self.device_input_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
        )
        self._device_output_frame_size = int(
            self.device_output_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
        )

        if raw_input_channels > AudioConfig.MAX_INPUT_CHANNELS:
            logger.info(
                f"输入设备支持 {raw_input_channels} 声道，限制使用前 {self.input_channels} 声道"
            )

        logger.info(
            f"选择输入设备: {in_info['name']} ({self.device_input_sample_rate}Hz, {self.input_channels}ch)"
        )
        logger.info(
            f"输出跟随系统默认: {default_output_name} "
            f"({self.device_output_sample_rate}Hz, {self.output_channels}ch)"
        )

        # 保存配置（首次运行时保存）
        self.config.update_config("AUDIO_DEVICES.input_device_id", self.mic_device_id)
        self.config.update_config("AUDIO_DEVICES.input_device_name", in_info["name"])
        self.config.update_config(
            "AUDIO_DEVICES.input_sample_rate", self.device_input_sample_rate
        )
        self.config.update_config("AUDIO_DEVICES.input_channels", self.input_channels)

        self.config.update_config(
            "AUDIO_DEVICES.output_device_id", None
        )
        self.config.update_config("AUDIO_DEVICES.output_device_name", default_output_name)
        self.config.update_config("AUDIO_DEVICES.output_device_pinned", False)
        self.config.update_config(
            "AUDIO_DEVICES.output_sample_rate", self.device_output_sample_rate
        )
        self.config.update_config("AUDIO_DEVICES.output_channels", self.output_channels)

    async def _create_opus_codecs(self):
        """
        创建Opus编解码器.
        """
        try:
            # 输入编码器：16kHz单声道
            self.opus_encoder = opuslib.Encoder(
                AudioConfig.INPUT_SAMPLE_RATE,
                AudioConfig.CHANNELS,
                opuslib.APPLICATION_VOIP,
            )

            # 输出解码器：24kHz单声道
            self.opus_decoder = opuslib.Decoder(
                AudioConfig.OUTPUT_SAMPLE_RATE, AudioConfig.CHANNELS
            )

            logger.info("Opus编解码器创建成功")
        except Exception as e:
            logger.error(f"创建Opus编解码器失败: {e}")
            raise

    async def _create_resamplers(self):
        """
        根据设备与服务端的差异，按需创建重采样器和转换标记.
        """
        # 输入转换器配置
        # 1. 声道下混标记
        self._need_input_downmix = self.input_channels > 1
        if self._need_input_downmix:
            logger.info(f"输入声道下混: {self.input_channels}ch → 1ch")

        # 2. 采样率重采样器
        if self.device_input_sample_rate != AudioConfig.INPUT_SAMPLE_RATE:
            self.input_resampler = soxr.ResampleStream(
                self.device_input_sample_rate,
                AudioConfig.INPUT_SAMPLE_RATE,
                num_channels=1,  # 重采样器处理单声道（下混后）
                dtype="float32",
                quality="QQ",  # 快速质量（低延迟）
            )
            logger.info(f"输入重采样: {self.device_input_sample_rate}Hz → 16kHz")

        # 输出转换器配置
        # 1. 采样率重采样器
        if self.device_output_sample_rate != AudioConfig.OUTPUT_SAMPLE_RATE:
            for channel in OUTPUT_CHANNELS:
                self._output_resamplers[channel] = soxr.ResampleStream(
                    AudioConfig.OUTPUT_SAMPLE_RATE,
                    self.device_output_sample_rate,
                    num_channels=1,  # 重采样器处理单声道（上混前）
                    dtype="float32",
                    quality="QQ",
                )
            logger.info(
                f"输出重采样: {AudioConfig.OUTPUT_SAMPLE_RATE}Hz → "
                f"{self.device_output_sample_rate}Hz"
            )

        # 2. 声道上混标记
        self._need_output_upmix = self.output_channels > 1
        if self._need_output_upmix:
            logger.info(f"输出声道上混: 1ch → {self.output_channels}ch")

    def _output_blocksize(self) -> int:
        """输出流回调块大小（帧）：在设备原生帧长基础上放宽到至少 40ms.

        协议帧仍按 20ms 生产/消费，这里只是 PortAudio 一次回调取多少帧，
        不影响 TTS / 音乐的时间线，仅增加约一个块的输出延迟。
        """
        native = int(self._device_output_frame_size or 0)
        sample_rate = int(self.device_output_sample_rate or 0)
        relaxed = int(sample_rate * (_OUTPUT_BLOCKSIZE_MIN_MS / 1000.0))
        return max(native, relaxed, 1)

    async def _create_streams(self):
        """
        创建音频流（完全使用设备原生格式）

        输入流不在初始化时创建：待命 / 播报状态下无需占用麦克风，统一由
        start_input_stream() 在进入 LISTENING（或启用唤醒词时由
        WakeWordPlugin 启动检测）按需打开，避免每次启动都短暂打开麦克风。
        """
        try:
            # 输入流：按需创建，此处保持为空
            self.input_stream = None

            # 输出流：使用设备原生采样率和声道数（常驻，用于 TTS / 音乐播放）
            self.output_stream = sd.OutputStream(
                device=self.speaker_device_id,
                samplerate=self.device_output_sample_rate,  # 设备原生采样率
                channels=self.output_channels,  # 设备原生声道数
                dtype=np.float32,
                blocksize=self._output_blocksize(),  # 至少 40ms，容忍 GIL 争用
                callback=self._output_callback,
                finished_callback=self._output_finished_callback,
                latency="low",
            )

            self.output_stream.start()
            self._output_stream_started_at = time.monotonic()

            # 设备名探测走 COM，放到线程里，避免阻塞事件循环
            self._bound_output_name = await asyncio.to_thread(
                self._get_live_default_output_name
            )

            logger.info(
                f"输出流已启动 | 输出: {self.device_output_sample_rate}Hz {self.output_channels}ch"
                f"（麦克风输入流在聆听/唤醒词时按需启动）"
            )

        except Exception as e:
            logger.error(f"创建音频流失败: {e}")
            raise

    def _input_callback(self, indata, frames, time_info, status):
        """
        输入回调：设备原生格式 → 服务端协议格式 转换流程：多声道/高采样率 → 下混+重采样 → 16kHz单声道 → Opus编码.
        """
        if status:
            self._log_stream_status(status, "输入")

        if self._is_closing:
            return

        try:
            # 步骤1: 声道下混（立体声/多声道 → 单声道）
            if self._need_input_downmix:
                # indata shape: (frames, channels)
                audio_data = downmix_to_mono(indata, keepdims=False)
            else:
                # 单声道：取列视图，避免 flatten() 的整帧复制
                audio_data = indata[:, 0] if indata.ndim > 1 else indata

            # 步骤2: 采样率转换（设备采样率 → 16kHz）
            if self.input_resampler is not None:
                audio_data = self._process_input_resampling(audio_data)
                if audio_data is None:  # 数据不足，等待下一帧
                    return

            # 步骤3: 验证帧大小
            if len(audio_data) != AudioConfig.INPUT_FRAME_SIZE:
                return

            # 步骤4: 转换为 int16 供 Opus 编码和 AEC 处理（复用 scratch，避免分配）
            int16_scratch = self._input_int16_scratch
            if len(int16_scratch) != len(audio_data):
                int16_scratch = np.empty(len(audio_data), dtype=np.int16)
                self._input_int16_scratch = int16_scratch
            np.multiply(audio_data, 32768.0, out=int16_scratch, casting="unsafe")
            audio_data_int16 = int16_scratch

            # 步骤5: AEC处理（如果启用）
            if self._aec_enabled and self.audio_processor._is_macos:
                try:
                    audio_data_int16 = self.audio_processor.process_audio(audio_data_int16)
                except Exception as e:
                    logger.warning(f"AEC处理失败，使用原始音频: {e}")

            # 步骤6: Opus编码并实时发送
            if self._encoded_callback:
                try:
                    pcm_data = audio_data_int16.tobytes()
                    encoded_data = self.opus_encoder.encode(
                        pcm_data, AudioConfig.INPUT_FRAME_SIZE
                    )
                    if encoded_data:
                        self._encoded_callback(encoded_data)
                except Exception as e:
                    logger.warning(f"实时录音编码失败: {e}")

            # 步骤7: 通知音频监听器（解耦唤醒词检测）
            # 传入复用缓冲的视图；需要保留数据的监听器（如唤醒词）自行在
            # 入队时 copy 一次，避免这里每帧再复制一份。
            with self._listeners_lock:
                listeners = tuple(self._audio_listeners)
            for listener in listeners:
                try:
                    listener.on_audio_data(audio_data_int16)
                except Exception as e:
                    logger.warning(f"音频监听器处理失败: {e}")

        except Exception as e:
            logger.error(f"输入回调错误: {e}")

    def _process_input_resampling(self, audio_data):
        """
        输入重采样处理：设备采样率 → 16kHz 使用缓冲区累积数据，凑够一帧再返回.
        """
        try:
            resampled_data = self.input_resampler.resample_chunk(audio_data, last=False)
            if len(resampled_data) > 0:
                self._resample_input_buffer.extend(resampled_data)
            return self._resample_input_buffer.take(AudioConfig.INPUT_FRAME_SIZE)
        except Exception as e:
            logger.error(f"输入重采样失败: {e}")
            return None

    def _output_callback(self, outdata, frames, time_info, status):
        """
        输出回调：服务端协议格式 → 设备原生格式 转换流程：24kHz单声道 → 重采样+上混 → 多声道/高采样率.
        """
        if status:
            if (
                time.monotonic() - self._output_stream_started_at
                < _STREAM_STATUS_STARTUP_GRACE
            ):
                # 启动预热期内的 xrun：尚未开始播放，不打扰用户
                logger.debug("输出流启动预热期状态: %s", status)
            else:
                self._log_stream_status(status, "输出")

        # 关闭流程中直接回填静音并返回，避免在 stop/close 与置空输出流、清理
        # 重采样器之间存在 TOCTOU 竞态窗口：PortAudio 可能在流停止后仍触发一次
        # 回调，此时访问已置空的流/重采样器可能导致原生崩溃（表现为退出异常）。
        if self._is_closing:
            outdata.fill(0)
            return

        try:
            if self._has_output_resampler():
                self._output_callback_with_resample(outdata, frames)
            else:
                self._output_callback_direct(outdata, frames)

        except Exception as e:
            logger.error(f"输出回调错误: {e}")
            outdata.fill(0)

    def _has_output_resampler(self) -> bool:
        return any(resampler is not None for resampler in self._output_resamplers.values())

    def _log_stream_status(self, status, kind: str) -> None:
        """回调线程状态异常限流：最多每 5 秒写一次日志，避免 xrun 时刷盘."""
        now = time.monotonic()
        if now - self._last_status_log_at < _STREAM_STATUS_LOG_INTERVAL:
            return
        self._last_status_log_at = now
        logger.warning("%s流状态异常: %s", kind, status)

    def _note_output_drop(self, channel: str) -> None:
        """输出队列拥塞丢帧计数（限流日志）."""
        self._output_drop_count += 1
        now = time.monotonic()
        if now - self._output_drop_log_at < _STREAM_STATUS_LOG_INTERVAL:
            return
        self._output_drop_log_at = now
        logger.warning(
            "%s 输出队列拥塞，近 5 秒丢弃 %s 帧音频",
            channel,
            self._output_drop_count,
        )
        self._output_drop_count = 0

    def _enqueue_output(
        self, channel: str, samples: np.ndarray, *, replace_oldest: bool
    ) -> bool:
        """线程安全入队：满时按策略丢最旧或拒绝新帧，不再阻塞事件循环."""
        buffer = self._output_buffers[channel]
        full = len(buffer) >= _MAX_OUTPUT_FRAMES
        if full:
            if not replace_oldest:
                self._note_output_drop(channel)
                return False
            try:
                buffer.popleft()
            except IndexError:
                pass
            self._note_output_drop(channel)
        buffer.append(samples)
        return True

    def _output_callback_direct(self, outdata, frames):
        """直接播放（设备支持协议输出采样率时）"""
        mono_data = self._mix_output_channels(frames, resampled=False)
        self._write_mono_to_outdata(outdata, mono_data)

    def _output_callback_with_resample(self, outdata, frames):
        """重采样播放（协议输出采样率 → 设备采样率）"""
        mono_data = self._mix_output_channels(frames, resampled=True)
        self._write_mono_to_outdata(outdata, mono_data)

    def _render_scratch_for(self, channel: str, frames: int) -> np.ndarray:
        """返回该通道至少 frames 长的 float32 scratch（输出回调线程专用）."""
        scratch = self._render_scratch[channel]
        if len(scratch) < frames:
            scratch = np.zeros(frames, dtype=np.float32)
            self._render_scratch[channel] = scratch
        return scratch

    def _ensure_scratch(self, attr: str, frames: int) -> np.ndarray:
        scratch = getattr(self, attr)
        if len(scratch) < frames:
            scratch = np.zeros(frames, dtype=np.float32)
            setattr(self, attr, scratch)
        return scratch

    def _render_channel_direct(self, channel: str, frames: int) -> np.ndarray:
        scratch = self._render_scratch_for(channel, frames)
        try:
            audio_data = self._output_buffers[channel].popleft()
        except IndexError:
            audio_data = None

        if audio_data is None or len(audio_data) == 0:
            scratch[:frames].fill(0.0)
            return scratch[:frames]

        n = min(len(audio_data), frames)
        np.copyto(scratch[:n], audio_data[:n], casting="unsafe")
        np.multiply(scratch[:n], 1.0 / 32768.0, out=scratch[:n])
        if n < frames:
            scratch[n:frames].fill(0.0)
        return scratch[:frames]

    def _render_channel_resampled(self, channel: str, frames: int) -> np.ndarray:
        resampler = self._output_resamplers.get(channel)
        if resampler is None:
            return self._render_channel_direct(channel, frames)

        buffer = self._resample_output_buffers[channel]
        source_queue = self._output_buffers[channel]
        input_scratch = self._resample_input_scratch[channel]

        while len(buffer) < frames:
            try:
                audio_data = source_queue.popleft()
            except IndexError:
                break

            n = len(audio_data)
            if n == 0:
                continue
            if len(input_scratch) < n:
                input_scratch = np.zeros(n, dtype=np.float32)
                self._resample_input_scratch[channel] = input_scratch
            np.copyto(input_scratch[:n], audio_data, casting="unsafe")
            np.multiply(input_scratch[:n], 1.0 / 32768.0, out=input_scratch[:n])
            resampled_data = resampler.resample_chunk(input_scratch[:n], last=False)
            if len(resampled_data) > 0:
                buffer.extend(resampled_data)

        data = buffer.take(frames)
        if data is not None:
            return data

        scratch = self._render_scratch_for(channel, frames)
        available = len(buffer)
        if available > 0:
            tail = buffer.take(available)
            if tail is not None:
                np.copyto(scratch[:available], tail, casting="unsafe")
        scratch[available:frames].fill(0.0)
        return scratch[:frames]

    def _mix_output_channels(self, frames: int, resampled: bool) -> np.ndarray:
        render = self._render_channel_resampled if resampled else self._render_channel_direct
        tts_data = render(OUTPUT_CHANNEL_TTS, frames)
        music_data = render(OUTPUT_CHANNEL_MUSIC, frames)

        tts_volume = self._channel_volumes[OUTPUT_CHANNEL_TTS]
        music_volume = self._channel_volumes[OUTPUT_CHANNEL_MUSIC]
        music_gain = self._update_music_duck_gain()

        mixed = self._ensure_scratch("_mix_scratch", frames)
        music_scaled = self._ensure_scratch("_music_mix_scratch", frames)
        np.multiply(tts_data, tts_volume, out=mixed[:frames])
        np.multiply(music_data, music_volume * music_gain, out=music_scaled[:frames])
        np.add(mixed[:frames], music_scaled[:frames], out=mixed[:frames])
        np.clip(mixed[:frames], -1.0, 1.0, out=mixed[:frames])
        return mixed[:frames]

    def _update_music_duck_gain(self) -> float:
        target = self._music_gain_target
        current = self._music_gain_current
        if abs(target - current) < 0.01:
            self._music_gain_current = target
        else:
            self._music_gain_current = current + (target - current) * 0.25
        return self._music_gain_current

    def _write_mono_to_outdata(self, outdata, mono_data: np.ndarray):
        if self._need_output_upmix:
            # 广播写入，避免 np.tile 每帧生成整块多声道临时数组
            outdata[:] = mono_data[:, None]
        else:
            outdata[:, 0] = mono_data

    def _input_finished_callback(self):
        """
        输入流结束回调.
        """
        logger.info("输入流已结束")
        try:
            self._input_finished_evt.set()
        except Exception:
            pass

    def _output_finished_callback(self):
        """
        输出流结束回调.
        """
        logger.info("输出流已结束")
        try:
            self._output_finished_evt.set()
        except Exception:
            pass

    # ============= 对外接口方法 =============

    def set_encoded_callback(self, callback: Callable[[bytes], None]):
        """设置编码音频回调（解耦网络层）

        Args:
            callback: 编码后数据的回调函数，接收 bytes 类型的 Opus 数据

        示例:
            def network_send(opus_data: bytes):
                await websocket.send(opus_data)

            audio_codec.set_encoded_callback(network_send)
        """
        self._encoded_callback = callback

        if callback:
            logger.info("已设置编码音频回调")
        else:
            logger.info("已清除编码音频回调")

    def add_audio_listener(self, listener: AudioListener):
        """添加音频监听器（解耦唤醒词检测等功能）

        Args:
            listener: 实现 AudioListener 协议的监听器对象

        示例:
            wake_word_detector = WakeWordDetector()  # 实现了 on_audio_data 方法
            audio_codec.add_audio_listener(wake_word_detector)
        """
        with self._listeners_lock:
            if listener not in self._audio_listeners:
                self._audio_listeners.append(listener)
                logger.info(f"已添加音频监听器: {listener.__class__.__name__}")

    def remove_audio_listener(self, listener: AudioListener):
        """移除音频监听器.

        Args:
            listener: 要移除的监听器对象
        """
        with self._listeners_lock:
            if listener in self._audio_listeners:
                self._audio_listeners.remove(listener)
                logger.info(f"已移除音频监听器: {listener.__class__.__name__}")

    def _validate_output_channel(self, channel: str) -> str:
        if channel not in self._output_buffers:
            raise ValueError(f"未知输出通道: {channel}")
        return channel

    def has_pending_audio(self, channel: str = OUTPUT_CHANNEL_TTS) -> bool:
        """该输出通道是否还有未播完的数据（含重采样缓冲）."""
        if self._output_buffers.get(channel):
            return True
        buffer = self._resample_output_buffers.get(channel)
        return bool(buffer)

    def set_channel_volume(self, channel: str, volume: float) -> None:
        """设置逻辑输出通道音量."""
        channel = self._validate_output_channel(channel)
        self._channel_volumes[channel] = max(0.0, min(1.0, float(volume)))

    def set_music_ducking(self, active: bool, duck_volume: float | None = None) -> None:
        """设置 TTS 播放期间的音乐压低音量状态."""
        if duck_volume is not None:
            self._music_duck_volume = max(0.0, min(1.0, float(duck_volume)))
        self._music_gain_target = self._music_duck_volume if active else 1.0

    async def write_audio(self, opus_data: bytes, channel: str = OUTPUT_CHANNEL_TTS):
        """解码并播放音频（服务端 Opus 数据 → 扬声器）

        Args:
            opus_data: 服务端返回的 Opus 编码数据

        流程:
            Opus解码 → 24kHz单声道PCM → 播放队列 → 输出回调处理
        """
        try:
            channel = self._validate_output_channel(channel)

            # Opus解码为24kHz PCM数据
            pcm_data = self.opus_decoder.decode(
                opus_data, AudioConfig.OUTPUT_FRAME_SIZE
            )

            audio_array = np.frombuffer(pcm_data, dtype=np.int16)

            expected_length = AudioConfig.OUTPUT_FRAME_SIZE * AudioConfig.CHANNELS
            if len(audio_array) != expected_length:
                logger.warning(
                    f"解码音频长度异常: {len(audio_array)}, 期望: {expected_length}"
                )
                return

            # 放入指定逻辑通道；满时丢最旧帧，保持低延迟
            self._enqueue_output(channel, audio_array, replace_oldest=True)

        except opuslib.OpusError as e:
            logger.warning(f"Opus解码失败，丢弃此帧: {e}")
        except Exception as e:
            logger.warning(f"音频写入失败，丢弃此帧: {e}")

    async def write_pcm_direct(
        self, pcm_data: np.ndarray, channel: str = OUTPUT_CHANNEL_MUSIC
    ):
        """直接写入 PCM 数据到播放队列（供 MusicPlayer 使用）

        Args:
            pcm_data: 24kHz 单声道 PCM 数据 (np.int16)

        说明:
            此方法绕过 Opus 解码，直接将 PCM 数据写入播放队列。
            主要用于本地音乐播放，数据已由 FFmpeg 解码为目标格式。
        """
        try:
            channel = self._validate_output_channel(channel)

            # 验证数据格式
            expected_length = AudioConfig.OUTPUT_FRAME_SIZE * AudioConfig.CHANNELS

            # 如果数据长度不匹配，进行填充或截断
            if len(pcm_data) != expected_length:
                if len(pcm_data) < expected_length:
                    # 填充静音
                    original_len = len(pcm_data)
                    padded = np.zeros(expected_length, dtype=np.int16)
                    padded[:original_len] = pcm_data
                    pcm_data = padded
                    logger.debug(
                        "PCM 数据不足，填充静音: %s → %s",
                        original_len,
                        expected_length,
                    )
                else:
                    # 截断多余数据
                    original_len = len(pcm_data)
                    pcm_data = pcm_data[:expected_length]
                    logger.debug(
                        "PCM 数据过长，截断: %s → %s",
                        original_len,
                        expected_length,
                    )

            # 满时丢弃新帧（不回压、不阻塞事件循环）；解码端已按实时速率限速
            self._enqueue_output(channel, pcm_data, replace_oldest=False)

        except Exception as e:
            logger.warning(f"写入 PCM 数据失败: {e}")

    async def reinitialize_stream(self, is_input: bool = True):
        """重建音频流（处理设备错误/断开）

        Args:
            is_input: True=重建输入流, False=重建输出流

        使用场景:
            - 设备热插拔
            - 驱动错误恢复
            - 系统休眠唤醒
        """
        if self._is_closing:
            return False if is_input else None

        try:
            if is_input:
                if self.input_stream:
                    self.input_stream.stop()
                    self.input_stream.close()
                    self.input_stream = None

                if not await self._ensure_input_device():
                    return False

                self.input_stream = sd.InputStream(
                    device=self.mic_device_id,
                    samplerate=self.device_input_sample_rate,
                    channels=self.input_channels,
                    dtype=np.float32,
                    blocksize=self._device_input_frame_size,
                    callback=self._input_callback,
                    finished_callback=self._input_finished_callback,
                    latency="low",
                )
                self.input_stream.start()
                logger.info("输入流重新初始化成功")
                return True
            else:
                if self.output_stream:
                    self.output_stream.stop()
                    self.output_stream.close()

                self.output_stream = sd.OutputStream(
                    device=self.speaker_device_id,
                    samplerate=self.device_output_sample_rate,
                    channels=self.output_channels,
                    dtype=np.float32,
                    blocksize=self._output_blocksize(),
                    callback=self._output_callback,
                    finished_callback=self._output_finished_callback,
                    latency="low",
                )
                self.output_stream.start()
                self._output_stream_started_at = time.monotonic()
                self._bound_output_name = await asyncio.to_thread(
                    self._get_live_default_output_name
                )
                logger.info("输出流重新初始化成功")
                return None
        except Exception as e:
            stream_type = "输入" if is_input else "输出"
            logger.error(f"{stream_type}流重建失败: {e}")
            if is_input:
                return False
            else:
                raise

    async def start_input_stream(self) -> bool:
        """启动（或重建）麦克风输入流。已运行时直接返回 True。

        用于从“已释放”状态恢复麦克风采集。唤醒词等监听器会被自动重新拾取，
        因为监听器列表在释放输入流时不会被清空。
        """
        if self._is_closing:
            return False

        # 已运行则直接返回
        if self.input_stream is not None:
            try:
                if self.input_stream.active:
                    return True
            except Exception:
                pass
            # 残留但未运行的流先释放，避免重复创建时泄漏
            self._stop_stream_sync(self.input_stream, "输入")
            self.input_stream = None

        # 校验配置中的设备索引/声道/采样率：Windows 设备索引会随蓝牙、
        # USB 设备热插拔整体移位，直接使用旧索引可能指向纯输出设备，
        # 打开时报 -9998 (Invalid number of channels)。
        if not await self._ensure_input_device():
            return False

        last_error: Optional[Exception] = None
        for attempt in (0, 1):
            try:
                self.input_stream = sd.InputStream(
                    device=self.mic_device_id,
                    samplerate=self.device_input_sample_rate,
                    channels=self.input_channels,
                    dtype=np.float32,
                    blocksize=self._device_input_frame_size,
                    callback=self._input_callback,
                    finished_callback=self._input_finished_callback,
                    latency="low",
                )
                self.input_stream.start()
                logger.info(
                    f"输入流已启动 | 输入: {self.device_input_sample_rate}Hz "
                    f"{self.input_channels}ch"
                )
                return True
            except Exception as e:
                last_error = e
                if self.input_stream is not None:
                    self._stop_stream_sync(self.input_stream, "输入")
                    self.input_stream = None
                if attempt == 0:
                    logger.warning(
                        f"启动输入流失败，改为系统默认输入设备后重试: {e}"
                    )
                    if not await self._fallback_input_device():
                        break

        logger.error(f"启动输入流失败: {last_error}")
        return False

    async def stop_input_stream(self) -> None:
        """停止并关闭麦克风输入流，释放麦克风设备。

        关闭后可随时通过 start_input_stream 重建恢复。
        """
        if self.input_stream is None:
            return
        self._stop_stream_sync(self.input_stream, "输入")
        self.input_stream = None
        # 清空输入重采样缓冲，避免恢复后混入释放前的残留音频
        if self._resample_input_buffer:
            self._resample_input_buffer.clear()
        logger.info("输入流已释放")

    async def _follow_default_output(self):
        """
        检测到系统默认输出设备变化后，重新初始化音频层以跟随新默认设备。

        PortAudio 在初始化时缓存设备列表与默认设备，运行时切换系统默认（如插入
        耳机）不会自动更新；直接按索引重建可能触发 MME "device ID out of range"
        （设备热插拔导致缓存列表陈旧）。因此这里关闭所有流后重新初始化 PortAudio
        （等价于音频层"重启"，已被验证有效），再用 device=None 重新打开，使输出
        真正跟随新的系统默认设备。
        """
        new_name = await asyncio.to_thread(self._get_live_default_output_name)

        # 记录输入流在重建前是否处于运行状态，避免待命下热插拔重新打开麦克风
        input_was_running = self.input_stream is not None and getattr(
            self.input_stream, "active", False
        )

        # 1. 关闭现有流（_terminate 会释放 PortAudio 内的所有流）
        for attr in ("output_stream", "input_stream"):
            stream = getattr(self, attr)
            if stream:
                try:
                    if stream.active:
                        stream.stop()
                    stream.close()
                except Exception as e:
                    logger.warning(f"关闭{attr}失败: {e}")
                setattr(self, attr, None)

        # 2. 重新初始化 PortAudio，刷新设备列表与默认设备（放线程，避免卡事件循环）
        def _reinit_portaudio():
            sd._terminate()
            sd._initialize()

        try:
            await asyncio.to_thread(_reinit_portaudio)
        except Exception as e:
            logger.warning(f"重新初始化 PortAudio 失败: {e}")

        # 3. 重新定位输入设备（重枚举后索引可能变化，按名称匹配）
        await self._ensure_input_device()

        # 4. 重新解析默认输出（此时已实时）
        self.speaker_device_id = None
        default_info = await asyncio.to_thread(self._resolve_default_output)
        if default_info:
            _, self.device_output_sample_rate, self.output_channels = default_info
        else:
            logger.warning("重新解析默认输出失败，保持原采样率/声道")

        # 5. 重建输出端重采样器并清空残留缓冲（避免旧采样率的脏数据）
        for channel in OUTPUT_CHANNELS:
            resampler = self._output_resamplers.get(channel)
            if resampler is not None:
                await self._cleanup_resampler(resampler, f"输出-{channel}")
            self._output_resamplers[channel] = None

            buffer = self._resample_output_buffers.get(channel)
            if buffer:
                buffer.clear()

        if self.device_output_sample_rate != AudioConfig.OUTPUT_SAMPLE_RATE:
            for channel in OUTPUT_CHANNELS:
                self._output_resamplers[channel] = soxr.ResampleStream(
                    AudioConfig.OUTPUT_SAMPLE_RATE,
                    self.device_output_sample_rate,
                    num_channels=1,  # 重采样器处理单声道（上混前）
                    dtype="float32",
                    quality="QQ",
                )
        self._need_output_upmix = self.output_channels > 1

        # 6. 重新计算帧大小并重开两条流（device=None 跟随新默认设备）
        self._device_input_frame_size = int(
            self.device_input_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
        )
        self._device_output_frame_size = int(
            self.device_output_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
        )

        try:
            if input_was_running:
                self.input_stream = sd.InputStream(
                    device=self.mic_device_id,
                    samplerate=self.device_input_sample_rate,
                    channels=self.input_channels,
                    dtype=np.float32,
                    blocksize=self._device_input_frame_size,
                    callback=self._input_callback,
                    finished_callback=self._input_finished_callback,
                    latency="low",
                )
            self.output_stream = sd.OutputStream(
                device=self.speaker_device_id,
                samplerate=self.device_output_sample_rate,
                channels=self.output_channels,
                dtype=np.float32,
                blocksize=self._output_blocksize(),
                callback=self._output_callback,
                finished_callback=self._output_finished_callback,
                latency="low",
            )
            if self.input_stream is not None:
                self.input_stream.start()
            self.output_stream.start()
            self._output_stream_started_at = time.monotonic()
        except Exception as e:
            logger.error(f"重新初始化音频流失败: {e}")

        # 7. 记录新绑定的默认设备名称
        self._bound_output_name = new_name
        logger.info(
            f"输出已切换至系统默认设备 ({new_name!r}, "
            f"{self.device_output_sample_rate}Hz, {self.output_channels}ch)"
        )

    async def _monitor_output_device(self):
        """
        后台监控系统默认输出设备变化，发现热插拔（如插入耳机）时自动切换输出路由。
        """
        while not self._is_closing:
            try:
                # COM 探测放线程；间隔放宽到 3 秒，减少常驻 CPU/主循环抖动
                current = await asyncio.to_thread(
                    self._get_live_default_output_name
                )
                if (
                    current is not None
                    and self._bound_output_name is not None
                    and current != self._bound_output_name
                ):
                    logger.info(
                        "检测到系统默认输出设备变化: %r -> %r",
                        self._bound_output_name,
                        current,
                    )
                    await self._follow_default_output()
            except Exception as e:
                logger.warning(f"监控输出设备变化失败: {e}")
            await asyncio.sleep(3.0)

    async def clear_audio_queue(self, channel: str | None = None):
        """清空音频队列.

        使用场景:
            - 用户中断播放
            - 唤醒词触发时打断旧音频
            - 错误恢复时清空脏数据
        """
        cleared_count = 0
        channels = OUTPUT_CHANNELS if channel is None else (self._validate_output_channel(channel),)

        for output_channel in channels:
            queue = self._output_buffers[output_channel]
            cleared_count += len(queue)
            queue.clear()

            cleared_count += self._resample_output_buffers[output_channel].clear()

        # 全量清空时保留旧行为：同时清理输入重采样缓存
        if channel is None:
            cleared_count += self._resample_input_buffer.clear()

        if cleared_count > 0:
            target = "全部" if channel is None else channel
            logger.info(f"清空{target}音频队列，丢弃 {cleared_count} 帧音频数据")

    # ============= AEC 控制方法 =============

    async def _cleanup_resampler(self, resampler, name: str):
        """
        清理重采样器资源.
        """
        if not resampler:
            return

        try:
            # 刷新缓冲区
            if hasattr(resampler, "resample_chunk"):
                empty_array = np.array([], dtype=np.float32)
                resampler.resample_chunk(empty_array, last=True)
        except Exception as e:
            logger.debug(f"刷新{name}重采样器缓冲区失败: {e}")

        try:
            # 尝试显式关闭
            if hasattr(resampler, "close"):
                resampler.close()
                logger.debug(f"{name}重采样器已关闭")
        except Exception as e:
            logger.debug(f"关闭{name}重采样器失败: {e}")

    def _stop_stream_sync(self, stream, name: str):
        """
        同步停止单个音频流.
        """
        if not stream:
            return
        try:
            # 无条件先 stop 再 close：PortAudio 对仍在运行的流直接 close 可能挂起，
            # 而 stop() 在已停止的流上会返回 paStreamIsNotStopped 并被忽略，安全。
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception as e:
            logger.warning(f"关闭{name}流失败: {e}")

    async def _wait_stream_finished(self, wait_input: bool, wait_output: bool) -> None:
        """
        等待活跃音频流的 finished 回调在 PortAudio 后台线程上执行完毕。

        使用线程池等待 threading.Event，避免阻塞事件循环；设超时兜底，
        PortAudio 异常时不至于卡死关闭流程。
        """
        if not (wait_input or wait_output):
            return
        loop = asyncio.get_running_loop()
        waits = []
        if wait_input:
            waits.append(loop.run_in_executor(None, self._input_finished_evt.wait, 1.0))
        if wait_output:
            waits.append(loop.run_in_executor(None, self._output_finished_evt.wait, 1.0))
        try:
            await asyncio.wait_for(asyncio.gather(*waits), timeout=2.0)
        except Exception as e:
            logger.debug(f"等待音频流 finished 回调结束异常: {e}")

    async def close(self):
        """关闭音频编解码器并释放所有资源.

        清理顺序:
        1. 设置关闭标志，停止音频流
        2. 清空回调和监听器引用
        3. 清空队列和缓冲区
        4. 关闭AEC处理器
        5. 清理重采样器
        6. 释放编解码器
        7. 执行垃圾回收
        """
        if self._is_closing:
            return

        self._is_closing = True
        logger.info("开始关闭音频编解码器...")

        # 取消输出设备热插拔监控任务
        if self._output_monitor_task is not None:
            self._output_monitor_task.cancel()
            self._output_monitor_task = None

        try:
            # 1. 停止音频流（记录停止前是否活跃，用于等待 PortAudio 后台线程的
            #    finished 回调完成，避免进程退出时与尚未结束的流回调线程竞态导致卡顿/挂起）
            input_was_active = self.input_stream is not None and getattr(
                self.input_stream, "active", False
            )
            output_was_active = self.output_stream is not None and getattr(
                self.output_stream, "active", False
            )

            if input_was_active:
                self._input_finished_evt.clear()
            if output_was_active:
                self._output_finished_evt.clear()

            self._stop_stream_sync(self.input_stream, "输入")
            self._stop_stream_sync(self.output_stream, "输出")
            self.input_stream = None
            self.output_stream = None

            # 等待活跃流的 finished 回调真正执行完毕（用线程池等待，不阻塞事件循环）
            await self._wait_stream_finished(input_was_active, output_was_active)

            # 2. 清空回调和监听器
            self._encoded_callback = None
            with self._listeners_lock:
                self._audio_listeners.clear()

            # 3. 清空队列和缓冲区
            await self.clear_audio_queue()

            # 4. 关闭AEC处理器
            if self.audio_processor:
                try:
                    await self.audio_processor.close()
                except Exception as e:
                    logger.warning(f"关闭AEC处理器失败: {e}")
                finally:
                    self.audio_processor = None

            # 5. 清理重采样器
            await self._cleanup_resampler(self.input_resampler, "输入")
            for channel, resampler in self._output_resamplers.items():
                await self._cleanup_resampler(resampler, f"输出-{channel}")
                self._output_resamplers[channel] = None
            self.input_resampler = None

            # 6. 释放编解码器
            self.opus_encoder = None
            self.opus_decoder = None

            # 7. 垃圾回收
            gc.collect()

            logger.info("音频资源已完全释放")
        except Exception as e:
            logger.error(f"关闭音频编解码器过程中发生错误: {e}", exc_info=True)
        finally:
            self._is_closing = True

    def __del__(self):
        """析构函数 - 检查资源是否正确释放"""
        if not self._is_closing:
            logger.warning("AudioCodec未正确关闭，请调用 close() 方法")
