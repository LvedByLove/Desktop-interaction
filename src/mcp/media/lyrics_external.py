# -*- coding: utf-8 -*-
"""外部播放器的歌词匹配与缓存.

SMTC 只提供歌名/歌手/时长，不提供歌词。这里沿用项目已有音乐模块的取词链路：

1. 酷我搜索拿到 song_id，再取同步歌词（中文歌覆盖较好）；
2. 酷我失败时回退 LRCLIB（按歌名/歌手/时长精确匹配）。

结果按 ``歌名|歌手|时长`` 落盘缓存，避免每次切歌重复请求不稳定的在线接口。

LRCLIB 的中文歌词常是用户上传的繁体版本，因此所有来源在解析时统一转成
简体（Windows ``LCMapStringW``），缓存中的旧繁体歌词也会在读缓存时转换。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from src.mcp.music.online import (
    DEFAULT_LYRICS_URL,
    _parse_lrc_text,
    _sync_fetch_kuwo_lyrics,
    load_music_config,
    search_song,
)
from src.utils.logging_config import get_logger
from src.utils.resource_finder import get_user_cache_dir

logger = get_logger(__name__)

# 确认没有歌词后，内存里记住 10 分钟；短暂网络抖动不至于长时间没有歌词。
_NEGATIVE_TTL = 10 * 60
_KUWO_TIMEOUT = 15.0
_LRCLIB_TIMEOUT = 15.0
_SEARCH_TIMEOUT = 12.0
# 歌词匹配适当放宽：时长误差 10 秒内、歌名/歌手相似度略降、至少 3 行。
# 视频/未知会话仍然完全不弹（由 smtc.classify_media 拦截），这里的放宽只
# 影响“已确认是音乐”的会话。
_DURATION_TOLERANCE = 10.0
_MIN_LINES = 3
_LRCLIB_GET_URL = "https://lrclib.net/api/get"
_LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
_LRCLIB_HEADERS = {
    "User-Agent": "Desktop-interaction/1.0 (https://github.com/LvedByLove/Desktop-interaction)"
}

_NORMALIZE_RE = re.compile(r"[\s\-_()（）\[\]【】·.,，。!！?？'\"“”‘’/\\]+")
# 「词：周杰伦」「合声编写：…」「录音助理：…」这类制作人员行不是歌词。
_CREDIT_RE = re.compile(r"^\s*([^：:]{1,12})[:：]")
_CREDIT_KEYWORDS = (
    "作词", "作曲", "词曲", "编曲", "制作", "演唱", "原唱", "翻唱",
    "吉他", "贝斯", "鼓", "键盘", "弦乐", "合声", "和声", "合唱",
    "混音", "母带", "录音", "监制", "出品", "发行", "演奏", "助理",
    "工程", "编写",
)


def _is_credit(text: str) -> bool:
    match = _CREDIT_RE.match(text or "")
    if not match:
        return False
    prefix = match.group(1).strip()
    if prefix in ("词", "曲"):
        return True
    upper = prefix.upper()
    # OP/SP 是版权标识，按完整前缀判断，避免把 "stop：..." 当成制作信息。
    if upper in ("OP", "SP") or upper.startswith(("OP/", "SP/", "OP ", "SP ")):
        return True
    return any(keyword.upper() in upper for keyword in _CREDIT_KEYWORDS)

# Windows 自带的繁→简映射（逐字/常用词级），避免为这一项额外引入 OpenCC 依赖。
# 酷我/网易云以简体为主，但 LRCLIB 的中文歌词常是用户上传的繁体版本。
_SIMPLIFIED_SUPPORTED = sys.platform == "win32"
if _SIMPLIFIED_SUPPORTED:
    import ctypes

    _LCMAP_SIMPLIFIED_CHINESE = 0x02000000
    _LOCALE_SYSTEM_DEFAULT = 0x0800
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LCMapStringW.argtypes = [
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    _kernel32.LCMapStringW.restype = ctypes.c_int


def to_simplified(text: str) -> str:
    """把繁体歌词转成简体；非 Windows 或转换失败时原样返回。

    含日文假名或韩文的行保持原样，避免把日文汉字/韩文歌词误转成简体。
    """
    if not text or not _SIMPLIFIED_SUPPORTED:
        return text
    if any("぀" <= ch <= "ヿ" or "가" <= ch <= "힣" for ch in text):
        return text
    try:
        size = len(text) + 1
        buffer = ctypes.create_unicode_buffer(size)
        written = _kernel32.LCMapStringW(
            _LOCALE_SYSTEM_DEFAULT,
            _LCMAP_SIMPLIFIED_CHINESE,
            text,
            -1,
            buffer,
            size,
        )
        return buffer.value if written else text
    except Exception:
        return text


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub("", (text or "").lower())


_TITLE_SPLIT_RE = re.compile(r"\s*[-–—|｜/]\s*")
_BRACKET_RE = re.compile(r"[（(【\[].*?[）)】\]]")


def _title_similar(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right)) >= 0.5
    return SequenceMatcher(None, left, right).ratio() >= 0.7


def _title_variants(text: str):
    """歌名可能带「- 歌手」「(Live)」「【4K】」等修饰，拆出可比较的候选。"""
    text = text or ""
    yield text
    stripped = _BRACKET_RE.sub(" ", text)
    if stripped != text:
        yield stripped
    for part in _TITLE_SPLIT_RE.split(text):
        yield part
        yield _BRACKET_RE.sub(" ", part)


def _title_matches(played: str, candidate: str) -> bool:
    """歌名必须足够接近；允许剥离括号说明和「歌名 - 歌手」后缀。"""
    candidates = [_normalize(item) for item in _title_variants(candidate)]
    for played_variant in _title_variants(played):
        normalized = _normalize(played_variant)
        if any(_title_similar(normalized, item) for item in candidates):
            return True
    return False


def _artist_matches(played: str, candidate: str) -> bool:
    """歌手对不上就拒绝；一方缺失时交给歌名+时长判断。"""
    a, b = _normalize(played), _normalize(candidate)
    if not a or not b:
        return True
    return a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.6


def _duration_matches(played: float, candidate: float) -> bool:
    if played <= 0 or candidate <= 0:
        return True
    return abs(played - candidate) <= _DURATION_TOLERANCE


def _strip_artist(title: str, artist: str) -> str:
    """部分播放器把歌手直接拼进歌名（如「晴天周杰伦」），去掉后再比较。"""
    text = (title or "").strip()
    artist = (artist or "").strip()
    if text and artist and artist.lower() in text.lower():
        text = re.sub(re.escape(artist), " ", text, flags=re.IGNORECASE).strip()
    return text


def _candidate_matches(
    played_title: str,
    played_artist: str,
    played_duration: float,
    candidate_title: str,
    candidate_artist: str,
    candidate_duration: float,
) -> bool:
    title_ok = (
        _title_matches(played_title, candidate_title)
        or _title_matches(
            _strip_artist(played_title, played_artist), candidate_title
        )
        or _title_matches(
            played_title, _strip_artist(candidate_title, candidate_artist)
        )
    )
    return (
        title_ok
        and _artist_matches(played_artist, candidate_artist)
        and _duration_matches(played_duration, candidate_duration)
    )


def _clean_lines(lines: Any) -> list[tuple[float, str]]:
    """规范化歌词行：丢弃非法时间、按时间排序、去重。"""
    result: list[tuple[float, str]] = []
    if not isinstance(lines, (list, tuple)):
        return result
    seen: set[tuple[float, str]] = set()
    for item in lines:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            at = float(item[0])
        except (TypeError, ValueError):
            continue
        text = to_simplified(str(item[1] or "").strip())
        if at < 0 or not text:
            continue
        if _is_credit(text):
            continue
        key = (round(at, 2), text)
        if key in seen:
            continue
        seen.add(key)
        result.append((at, text))
    result.sort(key=lambda line: line[0])
    return result


class ExternalLyrics:
    """外部歌曲歌词：网络匹配 + 文件缓存。"""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else (
            get_user_cache_dir() / "external_lyrics"
        )
        self._negative: dict[str, float] = {}
        self._config: Optional[dict[str, Any]] = None

    # ---- 缓存 ----

    @staticmethod
    def cache_key(title: str, artist: str, duration: float) -> str:
        return f"{_normalize(title)}|{_normalize(artist)}|{int(max(0.0, duration))}"

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{digest}.json"

    def _read_cache(self, key: str) -> Optional[list[tuple[float, str]]]:
        path = self._cache_path(key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            lines = _clean_lines(data.get("lines"))
            return lines if lines else None
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.debug("读取歌词缓存失败 %s: %s", path.name, exc)
            return None

    def _write_cache(self, key: str, lines: list[tuple[float, str]]) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "key": key,
                "saved_at": int(time.time()),
                "lines": [[round(t, 2), text] for t, text in lines],
            }
            self._cache_path(key).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug("写入歌词缓存失败: %s", exc)

    # ---- 取词 ----

    def _cfg(self) -> dict[str, Any]:
        if self._config is None:
            try:
                self._config = load_music_config()
            except Exception as exc:
                logger.debug("读取音乐配置失败，使用默认值: %s", exc)
                self._config = {}
        return self._config

    async def fetch(
        self, title: str, artist: str, duration: float
    ) -> list[tuple[float, str]]:
        """返回带时间轴的歌词行；没有歌词返回空列表（不抛异常）。"""
        title = (title or "").strip()
        artist = (artist or "").strip()
        if not title:
            return []
        key = self.cache_key(title, artist, duration)

        # 负缓存先判断：已知无歌词的曲目不再每次读盘。
        if self._negative.get(key, 0.0) > time.time():
            return []

        # 缓存读写 + 逐行繁简转换（ctypes）都放线程，避免阻塞事件循环。
        cached = await asyncio.to_thread(self._read_cache, key)
        if cached:
            logger.debug("外部歌词命中缓存: %s - %s", artist, title)
            return cached

        lines: list[tuple[float, str]] = []
        try:
            lines = await self._fetch_online(title, artist, duration)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("外部歌词获取失败 %s - %s: %s", artist, title, exc)

        if lines:
            await asyncio.to_thread(self._write_cache, key, lines)
            logger.info("外部歌词获取成功: %s - %s（%s 行）", artist, title, len(lines))
        else:
            self._negative[key] = time.time() + _NEGATIVE_TTL
            logger.info("外部歌词未找到: %s - %s", artist, title)
        return lines

    async def _fetch_online(
        self, title: str, artist: str, duration: float
    ) -> list[tuple[float, str]]:
        config = self._cfg()

        # 1) 酷我：搜索 song_id 后取同步歌词。
        query = f"{title} {artist}".strip()
        try:
            hit = await asyncio.wait_for(
                search_song(query, config), timeout=_SEARCH_TIMEOUT
            )
        except Exception as exc:
            logger.debug("酷我搜索失败 %s: %s", query, exc)
            hit = None

        if hit is not None:
            candidate_title = getattr(hit, "title", "") or hit.display_name
            candidate_artist = getattr(hit, "artist", "") or ""
            try:
                candidate_duration = float(getattr(hit, "duration", 0) or 0)
            except (TypeError, ValueError):
                candidate_duration = 0.0

            if _candidate_matches(
                title,
                artist,
                duration,
                candidate_title,
                candidate_artist,
                candidate_duration,
            ):
                try:
                    lines, reason = await asyncio.wait_for(
                        asyncio.to_thread(
                            _sync_fetch_kuwo_lyrics,
                            hit.song_id,
                            config.get("LYRICS_URL") or DEFAULT_LYRICS_URL,
                            config.get("HEADERS") or {},
                        ),
                        timeout=_KUWO_TIMEOUT,
                    )
                    cleaned = _clean_lines(lines)
                    if len(cleaned) >= _MIN_LINES:
                        return cleaned
                    logger.debug("酷我歌词为空或过少: %s（%s）", hit.display_name, reason)
                except Exception as exc:
                    logger.debug("酷我歌词请求失败: %s", exc)
            else:
                logger.debug(
                    "酷我搜索结果不匹配，跳过: %s / %s (%ss vs %ss)",
                    hit.display_name,
                    title,
                    candidate_duration,
                    duration,
                )

        # 2) LRCLIB 兜底：同样按歌名/歌手/时长严格验证，宁缺毋滥。
        try:
            lines = await asyncio.wait_for(
                asyncio.to_thread(
                    self._fetch_lrclib_strict, title, artist, duration
                ),
                timeout=_LRCLIB_TIMEOUT,
            )
            return lines
        except Exception as exc:
            logger.debug("LRCLIB 歌词请求失败: %s", exc)
            return []

    @staticmethod
    def _fetch_lrclib_strict(
        title: str, artist: str, duration: float
    ) -> list[tuple[float, str]]:
        """LRCLIB 取词并按元数据严格验证，返回至少 ``_MIN_LINES`` 行才算命中。"""
        # 延迟导入：避免启动时加载 requests/urllib3（约 200ms）。
        import requests

        candidates: list[Any] = []
        last_error: Optional[Exception] = None

        def usable(item: Any) -> bool:
            if not isinstance(item, dict) or not item.get("syncedLyrics"):
                return False
            try:
                item_duration = float(item.get("duration") or 0)
            except (TypeError, ValueError):
                item_duration = 0.0
            return _candidate_matches(
                title,
                artist,
                duration,
                str(item.get("trackName") or ""),
                str(item.get("artistName") or ""),
                item_duration,
            )

        # LRCLIB 偶发连接超时；重试一次，避免正常歌曲被一次网络抖动误判为无歌词。
        for attempt in range(2):
            try:
                params: dict[str, Any] = {"track_name": title}
                if artist:
                    params["artist_name"] = artist
                if duration > 0:
                    params["duration"] = int(round(duration))
                response = requests.get(
                    _LRCLIB_GET_URL,
                    params=params,
                    headers=_LRCLIB_HEADERS,
                    timeout=(5, 10),
                )
                if response.status_code == 200:
                    candidates.append(response.json())

                # 精确接口返回的版本如果时长/歌名对不上，也要走搜索再找一遍。
                if not any(usable(item) for item in candidates):
                    search_params: dict[str, Any] = {"track_name": title}
                    if artist:
                        search_params["artist_name"] = artist
                    response = requests.get(
                        _LRCLIB_SEARCH_URL,
                        params=search_params,
                        headers=_LRCLIB_HEADERS,
                        timeout=(5, 10),
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if isinstance(data, list):
                            candidates.extend(data)
                break
            except Exception as exc:
                last_error = exc
                candidates.clear()
                if attempt == 0:
                    time.sleep(0.4)
        else:
            logger.debug("LRCLIB 请求失败: %s", last_error)
            return []

        matched: list[tuple[float, str]] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            candidate_title = str(item.get("trackName") or "")
            candidate_artist = str(item.get("artistName") or "")
            try:
                candidate_duration = float(item.get("duration") or 0)
            except (TypeError, ValueError):
                candidate_duration = 0.0
            if not _candidate_matches(
                title,
                artist,
                duration,
                candidate_title,
                candidate_artist,
                candidate_duration,
            ):
                continue
            synced = item.get("syncedLyrics")
            if not isinstance(synced, str) or not synced.strip():
                continue
            distance = (
                abs(candidate_duration - duration)
                if duration > 0 and candidate_duration > 0
                else 9999.0
            )
            matched.append((distance, synced))

        matched.sort(key=lambda item: item[0])
        for _, synced in matched:
            lines = _clean_lines(_parse_lrc_text(synced))
            if len(lines) >= _MIN_LINES:
                return lines
        return []
