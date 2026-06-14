import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import aiohttp

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class MusicApiClient:
    """
    音乐 API 客户端.
    """

    BASE_URL = "https://music.cnmsb.xin"

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")

    async def search_music(self, query: str) -> List[Dict[str, Any]]:
        """
        模糊搜索音乐.
        """
        query = query.strip()
        if not query:
            return []

        data = await self._request_json(
            "POST",
            "/api/music/search",
            json={"query": query},
        )
        results = data.get("results") or []
        return [item for item in results if item]

    async def search_music_batch(self, items: List[Dict[str, str]]) -> List[Optional[Dict[str, Any]]]:
        """
        批量精确搜索音乐.
        """
        clean_items = []
        for item in items:
            title = (item.get("title") or "").strip()
            artist = (item.get("artist") or "").strip()
            if not title:
                continue
            clean_item = {"title": title}
            if artist:
                clean_item["artist"] = artist
            clean_items.append(clean_item)

        if not clean_items:
            return []

        data = await self._request_json(
            "POST",
            "/api/music/search",
            json={"items": clean_items},
        )
        return data.get("results") or []

    async def get_music_info(self, music_id: int) -> Dict[str, Any]:
        """
        获取音乐信息.
        """
        data = await self._request_json("GET", f"/api/music/info/{music_id}")
        return data.get("data") or {}

    async def get_lyrics(self, music_id: int) -> str:
        """
        获取歌词文本.
        """
        data = await self._request_json("GET", f"/api/music/lyrics/{music_id}")
        return data.get("data") or ""

    async def download_music(
        self,
        music_id: int,
        target_dir: Path,
        filename: Optional[str] = None,
        force: bool = False,
    ) -> Path:
        """
        下载音乐文件到本地缓存.
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_filename = self._safe_filename(filename or f"{music_id}.mp3")
        if not safe_filename.lower().endswith(".mp3"):
            safe_filename += ".mp3"
        target_path = target_dir / safe_filename

        if not force and self._is_valid_cached_file(target_path):
            return target_path

        if force and target_path.exists():
            target_path.unlink(missing_ok=True)

        url = self.build_file_url(music_id)
        last_error = None
        for attempt in range(2):
            temp_path = target_dir / f".{target_path.name}.{uuid.uuid4().hex}.download"
            try:
                expected_size = None
                downloaded_size = 0
                timeout = aiohttp.ClientTimeout(total=120)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        response.raise_for_status()
                        content_length = response.headers.get("Content-Length")
                        if content_length and content_length.isdigit():
                            expected_size = int(content_length)

                        with temp_path.open("wb") as file:
                            async for chunk in response.content.iter_chunked(1024 * 64):
                                if chunk:
                                    file.write(chunk)
                                    downloaded_size += len(chunk)

                if downloaded_size <= 0 or temp_path.stat().st_size <= 0:
                    raise RuntimeError("下载的音乐文件为空")
                if expected_size is not None and downloaded_size != expected_size:
                    raise RuntimeError(
                        f"音乐文件下载不完整: {downloaded_size}/{expected_size} 字节"
                    )

                temp_path.replace(target_path)
                return target_path
            except Exception as e:
                last_error = e
                temp_path.unlink(missing_ok=True)
                logger.warning(f"下载音乐失败，尝试 {attempt + 1}/2: {e}")

        raise RuntimeError(f"下载音乐失败: {last_error}")

    def _is_valid_cached_file(self, path: Path) -> bool:
        try:
            return path.exists() and path.stat().st_size > 0
        except Exception:
            return False

    def build_file_url(self, music_id: int) -> str:
        return urljoin(self.base_url + "/", f"api/music/file/{music_id}")

    def build_cover_url(self, music_id: int) -> str:
        return urljoin(self.base_url + "/", f"api/music/cover/{music_id}")

    async def _request_json(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, **kwargs) as response:
                response.raise_for_status()
                return await response.json(content_type=None)

    def _safe_filename(self, filename: str) -> str:
        filename = re.sub(r'[\\/:*?"<>|]+', "_", filename).strip()
        return filename or "music.mp3"
