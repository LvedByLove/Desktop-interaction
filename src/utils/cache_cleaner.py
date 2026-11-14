"""缓存与日志清理工具."""

import shutil
import tempfile
from pathlib import Path

from src.utils.logging_config import get_logger
from src.utils.resource_finder import (
    get_music_cache_dir,
    get_project_root,
    get_user_cache_dir,
    get_user_log_dir,
)

logger = get_logger(__name__)


def _clear_dir_contents(directory: Path) -> int:
    """递归删除目录下所有内容（保留目录本身），返回删除条目数."""
    if not directory or not directory.exists():
        return 0
    count = 0
    for path in directory.iterdir():
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
            count += 1
        except Exception as e:
            logger.debug(f"删除失败: {path}, {e}")
    return count


def _clear_log_files(directory: Path) -> int:
    """清理日志目录：当前 app.log 截断，其余 *.log 删除."""
    if not directory or not directory.exists():
        return 0
    count = 0
    for path in directory.glob("*.log"):
        try:
            if path.name == "app.log":
                with open(path, "w", encoding="utf-8"):
                    pass
            else:
                path.unlink()
            count += 1
        except Exception as e:
            logger.debug(f"清空日志失败: {path}, {e}")
    return count


def clear_cache_dirs() -> int:
    """清空临时缓存、音乐缓存等，返回清理条目数."""
    targets = [
        get_project_root() / "cache",
        get_user_cache_dir(create=False),
        get_music_cache_dir(create=False),
        Path(tempfile.gettempdir()) / "xiaozhi_music_cache",
    ]
    cleared = 0
    for directory in targets:
        try:
            cleared += _clear_dir_contents(directory)
        except Exception as e:
            logger.debug(f"清空缓存目录失败: {directory}, {e}")
    logger.info(f"已清空缓存，删除 {cleared} 项")
    return cleared


def clear_log_files() -> int:
    """清空用户日志目录和项目日志目录，返回清理文件数."""
    directories = []
    try:
        directories.append(get_user_log_dir(create=False))
    except Exception:
        pass
    try:
        directories.append(get_project_root() / "logs")
    except Exception:
        pass

    cleared = 0
    for directory in directories:
        cleared += _clear_log_files(directory)
    logger.info(f"已清空日志，清理 {cleared} 个文件")
    return cleared
