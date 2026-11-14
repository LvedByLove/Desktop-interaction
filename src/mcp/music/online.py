"""在线音乐：搜索、直链解析、缓存下载、歌词.

从 py-xiaozhi 上游移植并精简，只依赖 requests，不引入额外依赖。
下载到「音乐缓存目录」（可在设置 → 数据目录中修改）。
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse

import requests

from src.utils.logging_config import get_logger
from src.utils.resource_finder import get_music_cache_dir

logger = get_logger(__name__)

DEFAULT_SEARCH_URL = "http://search.kuwo.cn/r.s"
DEFAULT_URL_API = "https://lxmusicapi.onrender.com"
DEFAULT_URL_API_KEY = "share-v3"
DEFAULT_LYRICS_URL = "https://m.kuwo.cn/newh5/singles/songinfoandlrc"

_KUWO_PLAYURL = "https://wapi.kuwo.cn/api/v1/www/music/playUrl"
_LRCLIB_GET_URL = "https://lrclib.net/api/get"
_LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
_LRCLIB_HEADERS = {
    "User-Agent": "Desktop-interaction/1.0 (https://github.com/LvedByLove/Desktop-interaction)"
}
# 酷我歌词接口会随机返回 status=301「音乐查询失败」，冷启动时尤其明显，
# 单次请求失败率较高，需要重试；仍失败时再回退到 LRCLIB。
_KUWO_LYRICS_RETRIES = 3
_QUALITY_FALLBACKS = ("320k", "128k")
_MUSIC_EXTS = (".mp3", ".m4a", ".flac", ".wav", ".ogg")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}

_METADATA_PREFIXES = ("作词", "作曲", "编曲", "制作", "演唱", "原唱", "翻唱")


@dataclass
class SearchHit:
    song_id: str
    display_name: str
    duration: float
    api_url: str
    # 原始歌名/歌手，供歌词备用源（LRCLIB）按曲目精确匹配
    title: str = ""
    artist: str = ""


def _cfg_str(cm, path: str, default: str) -> str:
    value = cm.get_config(path, default)
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def load_music_config() -> dict[str, Any]:
    """读取音乐相关配置；留空自动回落到内置默认值."""
    from src.utils.config_manager import ConfigManager

    cm = ConfigManager.get_instance()
    return {
        "SEARCH_URL": _cfg_str(cm, "MUSIC.SEARCH_URL", DEFAULT_SEARCH_URL),
        "URL_API": _cfg_str(cm, "MUSIC.URL_API", DEFAULT_URL_API),
        "URL_API_KEY": _cfg_str(cm, "MUSIC.URL_API_KEY", DEFAULT_URL_API_KEY),
        "LYRICS_URL": _cfg_str(cm, "MUSIC.LYRICS_URL", DEFAULT_LYRICS_URL),
        "DEFAULT_BR": _cfg_str(cm, "MUSIC.DEFAULT_QUALITY", "320k"),
        "SEARCH_LIMIT": 20,
        "HEADERS": dict(_HEADERS),
    }


async def _fetch_json(
    url: str, headers: dict[str, str], *, timeout: int = 15
) -> Optional[Any]:
    try:
        response = await asyncio.to_thread(
            requests.get, url, headers=headers, timeout=timeout
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.warning("请求失败 %s: %s", urlparse(url).netloc, exc)
        return None


async def search_song(song_name: str, config: dict[str, Any]) -> Optional[SearchHit]:
    """按歌名搜索（酷我），返回第一首的结果."""
    try:
        base = (config.get("SEARCH_URL") or DEFAULT_SEARCH_URL).strip()
        if "://" not in base:
            logger.warning("搜索 API 无效 (%r)，改用默认地址", base)
            base = DEFAULT_SEARCH_URL

        limit = int(config.get("SEARCH_LIMIT") or 20)
        search_url = (
            f"{base}?client=kt&all={quote(song_name)}&pn=0&rn={limit}"
            "&uid=794762570&ver=kwplayer_ar_9.2.2.1&vipver=1"
            "&show_copyright_off=1&newver=1&ft=music&cluster=0"
            "&strategy=2012&encoding=utf8&rformat=json&vermerge=1"
            "&mobi=1&issubtitle=1"
        )
        logger.info("搜索歌曲: %s", song_name)

        data = None
        for attempt in range(3):
            data = await _fetch_json(search_url, config.get("HEADERS") or {}, timeout=10)
            if data is not None:
                break
            if attempt < 2:
                logger.warning("搜索失败，重试 (%s/2)", attempt + 1)

        if not isinstance(data, dict):
            return None

        results = data.get("abslist") or []
        if not results:
            logger.warning("未找到歌曲: %s", song_name)
            return None

        first = results[0]
        music_rid = str(first.get("MUSICRID", ""))
        song_id = music_rid.replace("MUSIC_", "") if music_rid else ""
        if not song_id:
            logger.error("搜索结果中没有歌曲 ID")
            return None

        title = first.get("SONGNAME") or song_name
        artist = first.get("ARTIST") or ""
        album = first.get("ALBUM") or ""
        display_name = str(title)
        if artist:
            display_name = f"{title} - {artist}"
            if album:
                display_name += f" ({album})"

        duration = 0.0
        try:
            duration = float(first.get("DURATION", 0) or 0)
        except (TypeError, ValueError):
            pass

        quality = config.get("DEFAULT_BR") or "320k"
        url_api = (config.get("URL_API") or DEFAULT_URL_API).rstrip("/")
        api_url = f"{url_api}/url/kw/{song_id}/{quality}"

        logger.info("找到歌曲: %s (ID=%s)", display_name, song_id)
        return SearchHit(
            song_id=song_id,
            display_name=display_name,
            duration=duration,
            api_url=api_url,
            title=str(title),
            artist=str(artist),
        )
    except Exception as exc:
        logger.error("搜索歌曲失败: %s", exc, exc_info=True)
        return None


def _extract_url(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    url = data.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    inner = data.get("data")
    if isinstance(inner, dict):
        nested = inner.get("url")
        if isinstance(nested, str) and nested.startswith("http"):
            return nested
    elif isinstance(inner, str) and inner.startswith("http"):
        return inner
    return None


def _describe_failure(data: Any) -> str:
    if not isinstance(data, dict):
        return "直链 API 返回无法解析的数据"
    code = data.get("code")
    msg = str(data.get("msg") or data.get("message") or "").strip()
    if code == 1 or "禁止批量下载" in msg or "block ip" in msg.lower():
        return "直链 API 已封禁当前 IP，可更换网络或在设置中更换直链 API"
    if code == 5 or "too many" in msg.lower():
        return "直链 API 请求过于频繁，请稍后再试"
    if code == 2:
        return "直链 API 获取播放地址失败（曲库无源或解析失败）"
    if code == 4:
        return "直链 API 内部错误"
    if code == 6:
        return "直链 API 参数错误"
    return f"直链 API 失败: {msg}" if msg else f"直链 API 未返回 URL: {data}"


def _candidate_api_urls(api_url: str) -> list[str]:
    urls = [api_url]
    match = re.search(r"/url/[^/]+/[^/]+/([^/?#]+)", api_url)
    if not match:
        return urls
    current = match.group(1)
    for quality in _QUALITY_FALLBACKS:
        if quality == current:
            continue
        alt = re.sub(r"(/url/[^/]+/[^/]+/)[^/?#]+", rf"\g<1>{quality}", api_url, count=1)
        if alt not in urls:
            urls.append(alt)
    return urls


async def _resolve_via_lx_api(
    api_url: str, config: dict[str, Any]
) -> tuple[Optional[str], Optional[str]]:
    headers = {
        "X-Request-Key": config.get("URL_API_KEY") or DEFAULT_URL_API_KEY,
        "User-Agent": "lx-music-request",
        "Content-Type": "application/json",
    }
    reason = None
    for candidate in _candidate_api_urls(api_url):
        data = await _fetch_json(candidate, headers)
        if data is None:
            reason = "直链 API 网络请求失败"
            continue
        url = _extract_url(data)
        if url:
            logger.info("直链解析成功: %s...", url[:80])
            return url, None
        reason = _describe_failure(data)
        logger.warning("直链 API 未返回 URL: %s", reason)
        if "封禁" in (reason or ""):
            break
    return None, reason


async def _resolve_via_kuwo_official(
    song_id: str, config: dict[str, Any]
) -> tuple[Optional[str], Optional[str]]:
    if not song_id or song_id == "unknown":
        return None, "缺少歌曲 ID，无法回退官方接口"

    headers = dict(config.get("HEADERS") or {})
    headers.setdefault("Referer", "https://www.kuwo.cn/")
    reason = None
    for br in ("320kmp3", "128kmp3"):
        url = f"{_KUWO_PLAYURL}?mid={song_id}&type=music&httpsStatus=1&br={br}"
        data = await _fetch_json(url, headers)
        if data is None:
            reason = "酷我官方接口网络请求失败"
            continue
        real_url = _extract_url(data)
        if real_url:
            logger.info("酷我官方接口解析成功: %s...", real_url[:80])
            return real_url, None
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("msg") or data.get("message") or "").strip()
        if "付费" in msg:
            reason = f"该歌曲为付费内容，官方接口无法试听（{msg}）"
            break
        reason = msg or f"酷我官方接口未返回 URL: {data}"
    return None, reason


async def resolve_play_url(
    api_url: str, song_id: str, config: dict[str, Any]
) -> tuple[Optional[str], Optional[str]]:
    """直链 API → 降音质 → 酷我官方接口."""
    reasons: list[str] = []
    try:
        url, reason = await _resolve_via_lx_api(api_url, config)
        if url:
            return url, None
        if reason:
            reasons.append(reason)

        url, reason = await _resolve_via_kuwo_official(song_id, config)
        if url:
            return url, None
        if reason:
            reasons.append(reason)

        error = "；".join(reasons) if reasons else "未能解析播放地址"
        logger.error("未能解析播放 URL: %s", error)
        return None, error
    except Exception as exc:
        logger.error("解析播放地址异常: %s", exc, exc_info=True)
        return None, f"解析播放地址异常: {exc}"


def _media_headers(media_url: str, config: dict[str, Any]) -> dict[str, str]:
    host = (urlparse(media_url).hostname or "").lower()
    headers = dict(config.get("HEADERS") or _HEADERS)
    headers["Accept"] = "*/*"
    if "kuwo" in host or "sycdn" in host or "bd-lv" in host:
        headers["Referer"] = "https://www.kuwo.cn/"
        headers["Origin"] = "https://www.kuwo.cn"
    return headers


def find_cached_song(song_id: str) -> Optional[Path]:
    """在音乐缓存目录中查找已下载的歌曲."""
    root = get_music_cache_dir(create=False)
    for ext in _MUSIC_EXTS:
        path = root / f"{song_id}{ext}"
        if path.exists():
            return path
    return None


def _sync_download(
    media_url: str, headers: dict, temp_path: Path, cache_path: Path
) -> Path:
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            if temp_path.exists():
                temp_path.unlink()
            with requests.get(
                media_url,
                headers=headers,
                stream=True,
                timeout=45,
                allow_redirects=True,
            ) as response:
                response.raise_for_status()
                with open(temp_path, "wb") as file:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            file.write(chunk)
            if temp_path.stat().st_size <= 0:
                raise OSError("下载文件为空")
            shutil.move(str(temp_path), str(cache_path))
            return cache_path
        except (requests.RequestException, OSError) as exc:
            last_error = exc
            logger.warning("下载重试 %s/3 失败: %s", attempt + 1, exc)
    assert last_error is not None
    raise last_error


async def download_song(
    hit: SearchHit, config: dict[str, Any]
) -> tuple[Optional[Path], Optional[str]]:
    """解析并下载到音乐缓存目录；命中缓存直接返回."""
    cached = find_cached_song(hit.song_id)
    if cached is not None:
        logger.info("使用音乐缓存: %s", cached)
        return cached, None

    media_url, error = await resolve_play_url(hit.api_url, hit.song_id, config)
    if not media_url:
        return None, error or "未能解析播放地址"

    root = get_music_cache_dir(create=True)
    temp_dir = root / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    cache_path = root / f"{hit.song_id}.mp3"
    temp_path = temp_dir / f"{uuid.uuid4().hex}.part"
    headers = _media_headers(media_url, config)

    try:
        logger.info("开始下载: %s", hit.display_name)
        result = await asyncio.to_thread(
            _sync_download, media_url, headers, temp_path, cache_path
        )
        logger.info("下载完成: %s", result)
        return result, None
    except Exception as exc:
        logger.error("音乐下载失败: %s", exc, exc_info=True)
        return None, f"下载失败: {exc}"
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _format_lrc_time(seconds: float) -> str:
    minutes = int(seconds) // 60
    secs = seconds - minutes * 60
    return f"{minutes:02d}:{secs:05.2f}"


_LRC_TIME_PATTERN = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def _parse_lrc_text(text: str) -> list[tuple[float, str]]:
    """解析标准 LRC 文本（支持一行多个时间戳和百分秒/毫秒）."""
    lyrics: list[tuple[float, str]] = []
    for raw_line in (text or "").splitlines():
        matches = list(_LRC_TIME_PATTERN.finditer(raw_line))
        content = _LRC_TIME_PATTERN.sub("", raw_line).strip()
        if not matches or not content:
            continue
        if content.startswith(_METADATA_PREFIXES):
            continue
        for match in matches:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            fraction = (match.group(3) or "0").ljust(3, "0")[:3]
            timestamp = minutes * 60 + seconds + int(fraction) / 1000
            lyrics.append((timestamp, content))
    return sorted(lyrics, key=lambda item: item[0])


def _parse_kuwo_lrc_list(raw_lines: list[Any]) -> list[tuple[float, str]]:
    """把酷我接口的 lrclist 转成 (时间秒, 歌词) 列表."""
    lyrics: list[tuple[float, str]] = []
    for item in raw_lines:
        if not isinstance(item, dict):
            continue
        text = str(item.get("lineLyric") or "").strip()
        if not text or text.startswith(_METADATA_PREFIXES):
            continue
        try:
            timestamp = float(item.get("time", 0))
        except (TypeError, ValueError):
            continue
        lyrics.append((timestamp, text))
    return sorted(lyrics, key=lambda item: item[0])


def _sync_fetch_kuwo_lyrics(
    song_id: str, lyrics_url: str, headers: dict[str, str]
) -> tuple[list[tuple[float, str]], str]:
    """请求酷我歌词接口，返回 (歌词, 失败原因).

    酷我移动端接口需要先访问站点首页拿到会话 Cookie，否则容易返回
    status=301「音乐查询失败」；即使拿到 Cookie 也可能随机失败，因此重试几次。
    """
    session = requests.Session()
    session.headers.update(dict(headers or _HEADERS))
    try:
        parsed = urlparse(lyrics_url)
        if parsed.scheme and parsed.netloc:
            session.get(f"{parsed.scheme}://{parsed.netloc}/", timeout=(3, 5))
    except Exception as exc:
        logger.debug("酷我歌词接口预热失败: %s", exc)

    last_reason = "未知原因"
    for attempt in range(_KUWO_LYRICS_RETRIES):
        try:
            response = session.get(
                lyrics_url, params={"musicId": song_id}, timeout=(3, 6)
            )
            response.raise_for_status()
            data = response.json()
            status = data.get("status")
            if status == 200:
                raw_lines = (data.get("data") or {}).get("lrclist") or []
                lyrics = _parse_kuwo_lrc_list(raw_lines)
                if lyrics:
                    return lyrics, ""
                last_reason = "接口返回空歌词"
            else:
                last_reason = (
                    f"status={status}, msg={data.get('msg') or ''}"
                )
        except Exception as exc:
            last_reason = f"请求异常: {exc}"

        if attempt < _KUWO_LYRICS_RETRIES - 1:
            logger.debug(
                "酷我歌词获取失败（%s），重试 %s/%s",
                last_reason,
                attempt + 1,
                _KUWO_LYRICS_RETRIES - 1,
            )
            time.sleep(0.4)

    return [], last_reason


def _sync_fetch_lrclib_lyrics(
    title: str, artist: str, duration: float
) -> list[tuple[float, str]]:
    """从 LRCLIB 获取带时间轴的歌词（酷我接口失败时的备用源）."""
    if not title:
        return []

    try:
        candidates: list[Any] = []
        params: dict[str, Any] = {"track_name": title}
        if artist:
            params["artist_name"] = artist
        if duration > 0:
            params["duration"] = int(round(duration))

        response = requests.get(
            _LRCLIB_GET_URL, params=params, headers=_LRCLIB_HEADERS, timeout=(3, 6)
        )
        if response.status_code == 200:
            candidates.append(response.json())

        if not any(
            isinstance(item, dict) and item.get("syncedLyrics")
            for item in candidates
        ):
            search_params: dict[str, Any] = {"track_name": title}
            if artist:
                search_params["artist_name"] = artist
            response = requests.get(
                _LRCLIB_SEARCH_URL,
                params=search_params,
                headers=_LRCLIB_HEADERS,
                timeout=(3, 6),
            )
            if response.status_code == 200:
                results = response.json()
                if isinstance(results, list):
                    candidates.extend(results)

        def duration_distance(item: Any) -> float:
            if not isinstance(item, dict):
                return 9999.0
            try:
                item_duration = float(item.get("duration") or 0)
            except (TypeError, ValueError):
                item_duration = 0.0
            if duration <= 0 or item_duration <= 0:
                return 9999.0
            return abs(item_duration - duration)

        candidates.sort(key=duration_distance)
        for item in candidates:
            if not isinstance(item, dict):
                continue
            synced = item.get("syncedLyrics")
            if not isinstance(synced, str) or not synced.strip():
                continue
            lyrics = _parse_lrc_text(synced)
            if lyrics:
                return lyrics
    except Exception as exc:
        logger.debug("LRCLIB 歌词请求失败: %s", exc)
    return []


def _fetch_kuwo_or_fallback_lyrics(
    song_id: str,
    lyrics_url: str,
    headers: dict[str, str],
    title: str,
    artist: str,
    duration: float,
) -> list[tuple[float, str]]:
    """优先酷我歌词，失败后用 LRCLIB 兜底."""
    lyrics, reason = _sync_fetch_kuwo_lyrics(song_id, lyrics_url, headers)
    if lyrics:
        logger.info("酷我歌词获取成功: %s（%s 行）", song_id, len(lyrics))
        return lyrics

    logger.warning("酷我歌词获取失败: %s（%s），尝试备用歌词源", song_id, reason)
    lyrics = _sync_fetch_lrclib_lyrics(title, artist, duration)
    if lyrics:
        logger.info(
            "备用歌词源获取成功: %s - %s（%s 行）", artist, title, len(lyrics)
        )
    else:
        logger.warning("未获取到歌词: %s - %s", title or song_id, artist)
    return lyrics


async def fetch_lyrics_and_save(
    song_id: str,
    audio_path: Path,
    config: dict[str, Any],
    *,
    title: str = "",
    artist: str = "",
    duration: float = 0.0,
) -> list[tuple[float, str]]:
    """拉取歌词并写成同名 .lrc；失败返回空列表."""
    lrc_path = audio_path.with_suffix(".lrc")

    # 已经成功缓存过歌词时直接复用，避免重复请求不稳定的在线接口
    try:
        if lrc_path.exists():
            cached = _parse_lrc_text(
                lrc_path.read_text(encoding="utf-8", errors="ignore")
            )
            if cached:
                logger.debug("使用已缓存歌词: %s（%s 行）", lrc_path, len(cached))
                return cached
    except OSError as exc:
        logger.debug("读取缓存歌词失败: %s", exc)

    try:
        # 歌词只是增强显示，不能让网络异常长时间阻塞音乐起播
        lyrics = await asyncio.wait_for(
            asyncio.to_thread(
                _fetch_kuwo_or_fallback_lyrics,
                song_id,
                config.get("LYRICS_URL") or DEFAULT_LYRICS_URL,
                config.get("HEADERS") or _HEADERS,
                title,
                artist,
                float(duration or 0),
            ),
            timeout=15,
        )
        if not lyrics:
            return []

        with open(lrc_path, "w", encoding="utf-8") as file:
            for timestamp, text in lyrics:
                file.write(f"[{_format_lrc_time(timestamp)}]{text}\n")
        logger.info("歌词已保存: %s（%s 行）", lrc_path, len(lyrics))
        return lyrics
    except asyncio.TimeoutError:
        logger.warning("获取歌词超时，本次跳过: %s", song_id)
        return []
    except Exception as exc:
        logger.warning("获取歌词失败: %s", exc)
        return []
