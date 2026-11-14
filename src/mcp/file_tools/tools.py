"""文件操作 MCP 工具实现.

所有路径都被限制在 MCP_FILE_TOOLS.ROOT（默认用户主目录）内。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_MAX_BYTES = 1024 * 1024


def get_file_tool_root() -> Path:
    """文件工具允许访问的根目录."""
    config = ConfigManager.get_instance()
    raw = config.get_config("MCP_FILE_TOOLS.ROOT", None)
    root = Path(str(raw)).expanduser() if raw else Path.home()
    try:
        return root.resolve()
    except Exception:
        return root.absolute()


def _max_bytes() -> int:
    try:
        value = ConfigManager.get_instance().get_config(
            "MCP_FILE_TOOLS.MAX_BYTES", _DEFAULT_MAX_BYTES
        )
        return max(1, int(value))
    except Exception:
        return _DEFAULT_MAX_BYTES


def _backup_enabled() -> bool:
    try:
        return bool(
            ConfigManager.get_instance().get_config(
                "MCP_FILE_TOOLS.BACKUP_ON_OVERWRITE", True
            )
        )
    except Exception:
        return True


def _is_within(target: Path, root: Path) -> bool:
    """Windows 下忽略大小写的目录包含判断."""
    try:
        target_text = os.path.normcase(str(target))
        root_text = os.path.normcase(str(root)).rstrip("\\/")
        return target_text == root_text or target_text.startswith(
            root_text + os.sep
        )
    except Exception:
        return False


def _resolve_path(raw: Any, root: Path) -> Path:
    """把用户路径解析为绝对路径，并校验不超出根目录."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        target = root
    else:
        candidate = Path(text).expanduser()
        target = candidate if candidate.is_absolute() else root / candidate

    try:
        target = target.resolve(strict=False)
    except Exception:
        target = Path(os.path.abspath(str(target)))

    if not _is_within(target, root):
        raise PermissionError(f"路径超出允许范围，当前根目录: {root}")
    return target


def _result(success: bool, message: str, **extra) -> str:
    return json.dumps(
        {"success": success, "message": message, **extra},
        ensure_ascii=False,
        indent=2,
    )


async def create_or_write(args: dict[str, Any]) -> str:
    """创建或覆盖一个文本文件."""
    root = get_file_tool_root()
    max_bytes = _max_bytes()
    path = args.get("path")
    content = args.get("content", "")
    overwrite = bool(args.get("overwrite", False))

    if not str(path or "").strip():
        return _result(False, "path 不能为空")

    data = "" if content is None else str(content)
    size = len(data.encode("utf-8"))
    if size > max_bytes:
        return _result(False, f"内容超过大小限制（{size} > {max_bytes} bytes）")

    def work():
        target = _resolve_path(path, root)
        if target.exists() and target.is_dir():
            raise IsADirectoryError(f"目标是目录，无法写文件: {target}")
        existed = target.exists()
        if existed and not overwrite:
            raise FileExistsError(
                f"文件已存在；如需覆盖请传 overwrite=true: {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)

        backup_path = None
        if existed and _backup_enabled():
            backup_path = target.with_name(target.name + ".bak")
            try:
                shutil.copy2(target, backup_path)
            except Exception as e:
                logger.warning(f"覆盖前备份失败，继续写入: {target}, {e}")
                backup_path = None

        target.write_text(data, encoding="utf-8")
        return target, existed, backup_path, size

    try:
        target, existed, backup_path, size = await asyncio.to_thread(work)
    except Exception as e:
        logger.error(f"写入文件失败: {e}")
        return _result(False, f"写入失败: {e}")

    logger.info(
        "文件工具写入: %s（%s bytes, overwrite=%s）", target, size, existed
    )
    return _result(
        True,
        f"{'已覆盖' if existed else '已创建'}文件: {target}",
        path=str(target),
        bytes=size,
        overwritten=existed,
        backup=str(backup_path) if backup_path else None,
    )


async def append_file(args: dict[str, Any]) -> str:
    """在文件末尾追加文本；文件不存在时创建."""
    root = get_file_tool_root()
    max_bytes = _max_bytes()
    path = args.get("path")
    content = args.get("content", "")

    if not str(path or "").strip():
        return _result(False, "path 不能为空")

    data = "" if content is None else str(content)
    size = len(data.encode("utf-8"))
    if size > max_bytes:
        return _result(False, f"内容超过大小限制（{size} > {max_bytes} bytes）")

    def work():
        target = _resolve_path(path, root)
        if target.exists() and target.is_dir():
            raise IsADirectoryError(f"目标是目录，无法追加: {target}")
        existed = target.exists()
        if existed:
            current_size = target.stat().st_size
            if current_size + size > max_bytes:
                raise ValueError(
                    f"追加后文件超过大小限制（{current_size + size} > {max_bytes} bytes）"
                )
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as file:
            file.write(data)
        return target, existed, size

    try:
        target, existed, size = await asyncio.to_thread(work)
    except Exception as e:
        logger.error(f"追加文件失败: {e}")
        return _result(False, f"追加失败: {e}")

    logger.info("文件工具追加: %s（%s bytes）", target, size)
    return _result(
        True,
        f"{'已追加内容' if existed else '已创建并写入'}: {target}",
        path=str(target),
        bytes=size,
        created=not existed,
    )


async def read_file(args: dict[str, Any]) -> str:
    """读取文本文件."""
    root = get_file_tool_root()
    max_bytes = _max_bytes()
    path = args.get("path")

    if not str(path or "").strip():
        return _result(False, "path 不能为空")

    def work():
        target = _resolve_path(path, root)
        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {target}")
        if target.is_dir():
            raise IsADirectoryError(f"目标是目录: {target}")
        size = target.stat().st_size
        if size > max_bytes:
            raise ValueError(f"文件过大（{size} > {max_bytes} bytes）")
        text = target.read_text(encoding="utf-8", errors="replace")
        return target, text, size

    try:
        target, text, size = await asyncio.to_thread(work)
    except Exception as e:
        logger.error(f"读取文件失败: {e}")
        return _result(False, f"读取失败: {e}")

    return _result(
        True,
        f"已读取文件: {target}",
        path=str(target),
        bytes=size,
        content=text,
    )


async def list_directory(args: dict[str, Any]) -> str:
    """列出目录内容."""
    root = get_file_tool_root()
    path = args.get("path", "")
    try:
        limit = max(1, min(int(args.get("limit", 200) or 200), 1000))
    except (TypeError, ValueError):
        limit = 200

    def work():
        target = _resolve_path(path, root)
        if not target.exists():
            raise FileNotFoundError(f"目录不存在: {target}")
        if not target.is_dir():
            raise NotADirectoryError(f"不是目录: {target}")

        entries = []
        for item in sorted(
            target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        )[:limit]:
            try:
                size = item.stat().st_size if item.is_file() else None
            except Exception:
                size = None
            entries.append(
                {
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": size,
                }
            )
        return target, entries

    try:
        target, entries = await asyncio.to_thread(work)
    except Exception as e:
        logger.error(f"列出目录失败: {e}")
        return _result(False, f"列出目录失败: {e}")

    return _result(
        True,
        f"目录内容: {target}（{len(entries)} 项）",
        path=str(target),
        entries=entries,
    )


async def create_directory(args: dict[str, Any]) -> str:
    """创建目录（已存在时直接返回成功）."""
    root = get_file_tool_root()
    path = args.get("path")

    if not str(path or "").strip():
        return _result(False, "path 不能为空")

    def work():
        target = _resolve_path(path, root)
        existed = target.exists()
        if existed and not target.is_dir():
            raise FileExistsError(f"同名文件已存在: {target}")
        target.mkdir(parents=True, exist_ok=True)
        return target, existed

    try:
        target, existed = await asyncio.to_thread(work)
    except Exception as e:
        logger.error(f"创建目录失败: {e}")
        return _result(False, f"创建目录失败: {e}")

    return _result(
        True,
        f"{'目录已存在' if existed else '已创建目录'}: {target}",
        path=str(target),
        existed=existed,
    )
