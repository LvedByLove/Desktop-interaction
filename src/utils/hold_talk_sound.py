"""按住说话（Ctrl+Alt）触发/结束音效播放器.

音效文件: assets/music/cut_0sec.mp3（按下触发）, assets/music/cut_1sec.mp3（松开结束）.
解码一次后缓存 PCM 帧，播放时直接推入 AudioCodec 的 music 通道，
与本地音乐播放（MusicPlayer）走同一路径，不额外占用音频设备。
"""

import asyncio
from pathlib import Path

from src.audio_codecs.audio_channels import OUTPUT_CHANNEL_MUSIC
from src.audio_codecs.music_decoder import (
    MusicDecodeComplete,
    MusicDecodeError,
    MusicDecoder,
)
from src.utils.logging_config import get_logger
from src.utils.resource_finder import ResourceFinder

logger = get_logger(__name__)

_EFFECT_FILES = {
    "start": "assets/music/cut_0sec.mp3",
    "stop": "assets/music/cut_1sec.mp3",
}


class HoldTalkSound:
    """按住说话音效播放器（单例，惰性解码缓存）."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initd", False):
            return
        self._initd = True
        self._chunks: dict = {}  # kind -> [np.ndarray, ...]（PCM 帧）
        self._codec = None

    def _get_codec(self):
        if self._codec is None:
            try:
                from src.application import Application

                self._codec = getattr(Application.get_instance(), "audio_codec", None)
            except Exception:
                self._codec = None
        return self._codec

    @staticmethod
    def _find_effect(kind: str) -> Path | None:
        rel = _EFFECT_FILES[kind]
        try:
            found = ResourceFinder().find_file(rel)
            if found:
                return found
        except Exception:
            pass
        # 兜底：按源码根目录定位
        return Path(__file__).resolve().parents[2] / rel

    async def play(self, kind: str) -> None:
        """播放音效；文件缺失/解码失败/编解码器未就绪时静默降级（不打断热键流程）."""
        if kind not in _EFFECT_FILES:
            return
        try:
            codec = self._get_codec()
            if codec is None:
                return
            chunks = self._chunks.get(kind)
            if chunks is None:
                path = self._find_effect(kind)
                chunks = (
                    await self._decode_file(path)
                    if path and path.exists()
                    else []
                )
                self._chunks[kind] = chunks
                if not chunks:
                    logger.debug(f"按住说话音效解码结果为空: {path}")
            if not chunks:
                return
            # 按住说话音效是即时反馈音：播放前清空 music 通道积压（背景音乐/上次音效残余），
            # 让音效帧立即排到队首发声，避免被背景音乐顶到队尾造成"按下后迟迟才响"
            await codec.clear_audio_queue(channel=OUTPUT_CHANNEL_MUSIC)
            for chunk in chunks:
                await codec.write_pcm_direct(chunk, channel=OUTPUT_CHANNEL_MUSIC)
        except Exception as e:
            logger.debug(f"播放按住说话{kind}音效失败: {e}")

    async def preload(self) -> None:
        """预解码并缓存两个音效文件（后台预热）.

        避免首次按住说话时 FFmpeg 启动+解码带来的延迟；解码结果缓存至 _chunks，
        后续 play() 直接命中缓存推帧。文件缺失/解码失败静默降级，不打断启动流程。
        """
        for kind in _EFFECT_FILES:
            if self._chunks.get(kind):
                continue
            try:
                path = self._find_effect(kind)
                chunks = (
                    await self._decode_file(path)
                    if path and path.exists()
                    else []
                )
                self._chunks[kind] = chunks
                if chunks:
                    logger.debug(f"已预加载按住说话{kind}音效: {len(chunks)} 帧")
            except Exception as e:
                logger.debug(f"预加载按住说话{kind}音效失败: {e}")

    async def _decode_file(self, path: Path) -> list:
        """用 FFmpeg 解码 mp3 为 24kHz 单声道 PCM 帧列表（与 MusicPlayer 同参数）."""
        queue = asyncio.Queue(maxsize=200)
        decoder = MusicDecoder()
        try:
            if not await decoder.start_decode(path, queue, 0.0):
                return []
            chunks = []
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    logger.debug(f"音效解码超时: {path.name}")
                    break
                if isinstance(item, (MusicDecodeComplete, MusicDecodeError)):
                    break
                chunks.append(item)
            return chunks
        finally:
            await decoder.stop()
