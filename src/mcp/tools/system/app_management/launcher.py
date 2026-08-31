"""统一的应用程序启动器.

根据系统自动选择对应的启动器实现
"""

import asyncio
import platform
import re
from typing import Any, Dict, Optional, Tuple

from src.utils.logging_config import get_logger

from .utils import find_best_matching_app

logger = get_logger(__name__)


async def launch_application(args: Dict[str, Any]) -> bool:
    """启动应用程序.

    Args:
        args: 包含应用程序名称的参数字典
            - app_name: 应用程序名称

    Returns:
        bool: 启动是否成功
    """
    try:
        app_name = args["app_name"]
        app_query, launch_target = _split_app_name_and_url(app_name)
        logger.info(f"[AppLauncher] 尝试启动应用程序: {app_name}")
        if launch_target:
            logger.info(
                f"[AppLauncher] 检测到启动目标: app_query={app_query or '<default>'}, target={launch_target}"
            )

        if launch_target and not app_query:
            # 只有 URL 时直接交给系统默认浏览器，避免把 tv.qq.com 误匹配成 QQ 等应用。
            success = await _launch_by_name(launch_target)
        else:
            # 首先尝试通过扫描找到精确匹配的应用程序
            matched_app = await _find_matching_application(app_query)
            if matched_app:
                logger.info(
                    f"[AppLauncher] 找到匹配的应用程序: {matched_app.get('display_name', matched_app.get('name', ''))}"
                )
                # 根据应用程序类型使用不同的启动方法
                success = await _launch_matched_app(matched_app, app_query, launch_target)
            else:
                # 如果没有找到匹配，使用原来的方法
                logger.info(f"[AppLauncher] 未找到精确匹配，使用原始名称: {app_name}")
                success = await _launch_by_name(app_name)

        if success:
            logger.info(f"[AppLauncher] 成功启动应用程序: {app_name}")
        else:
            logger.warning(f"[AppLauncher] 启动应用程序失败: {app_name}")

        return success

    except KeyError:
        logger.error("[AppLauncher] 缺少app_name参数")
        return False
    except Exception as e:
        logger.error(f"[AppLauncher] 启动应用程序失败: {e}", exc_info=True)
        return False


def _split_app_name_and_url(app_name: str) -> Tuple[str, Optional[str]]:
    """拆分“应用名 + URL/域名”的启动请求.

    例如："Microsoft Edge https://www.bilibili.com" 会拆成
    ("Microsoft Edge", "https://www.bilibili.com")；
    "Google Chrome bilibili.com" 会拆成
    ("Google Chrome", "https://bilibili.com")。
    """
    parts = app_name.split()
    for index, part in enumerate(parts):
        target = _normalize_launch_target(part)
        if target:
            app_query = " ".join(parts[:index] + parts[index + 1 :]).strip()
            return app_query, target

    return app_name, None


def _normalize_launch_target(value: str) -> Optional[str]:
    """识别 URL 或裸域名，并转换为可打开的 URL."""
    target = value.strip().strip('"\'“”‘’，。！？；;')
    if not target:
        return None

    if re.match(r"^https?://\S+$", target, re.IGNORECASE):
        return target

    lower_target = target.lower()
    if lower_target.endswith((".exe", ".lnk", ".bat", ".cmd", ".ps1")):
        return None

    if re.match(
        r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:[:/][^\s]*)?$",
        target,
        re.IGNORECASE,
    ):
        return f"https://{target}"

    return None


async def _find_matching_application(app_name: str) -> Optional[Dict[str, Any]]:
    """通过扫描找到匹配的应用程序.

    Args:
        app_name: 要查找的应用程序名称

    Returns:
        匹配的应用程序信息，如果没找到则返回None
    """
    try:
        # 使用统一的匹配逻辑
        matched_app = await find_best_matching_app(app_name, "installed")

        if matched_app:
            logger.info(
                f"[AppLauncher] 通过统一匹配找到应用: {matched_app.get('display_name', matched_app.get('name', ''))}"
            )

        return matched_app

    except Exception as e:
        logger.warning(f"[AppLauncher] 查找匹配应用程序时出错: {e}")
        return None


async def _launch_matched_app(
    matched_app: Dict[str, Any], original_name: str, launch_target: Optional[str] = None
) -> bool:
    """启动匹配到的应用程序.

    Args:
        matched_app: 匹配的应用程序信息
        original_name: 原始应用程序名称
        launch_target: 需要传给应用程序的启动目标，例如 URL

    Returns:
        bool: 启动是否成功
    """
    try:
        app_type = matched_app.get("type", "unknown")
        app_path = matched_app.get("path", matched_app.get("name", original_name))

        system = platform.system()

        if system == "Windows":
            if launch_target:
                from .windows.launcher import launch_application_with_args

                logger.info(
                    f"[AppLauncher] 使用匹配应用打开目标: app_path={app_path}, target={launch_target}"
                )
                return await asyncio.to_thread(
                    launch_application_with_args, app_path, [launch_target]
                )

            # Windows系统特殊处理
            if app_type == "uwp":
                # UWP应用使用特殊的启动方法
                from .windows.launcher import launch_uwp_app_by_path

                return await asyncio.to_thread(launch_uwp_app_by_path, app_path)
            elif app_type == "shortcut" and app_path.endswith(".lnk"):
                # 快捷方式文件
                from .windows.launcher import launch_shortcut

                return await asyncio.to_thread(launch_shortcut, app_path)

        # 常规应用程序启动
        return await _launch_by_name(app_path)

    except Exception as e:
        logger.error(f"[AppLauncher] 启动匹配应用失败: {e}")
        return False


async def _launch_by_name(app_name: str) -> bool:
    """根据名称启动应用程序.

    Args:
        app_name: 应用程序名称或路径

    Returns:
        bool: 启动是否成功
    """
    try:
        system = platform.system()

        if system == "Windows":
            from .windows.launcher import launch_application

            return await asyncio.to_thread(launch_application, app_name)
        elif system == "Darwin":  # macOS
            from .mac.launcher import launch_application

            return await asyncio.to_thread(launch_application, app_name)
        elif system == "Linux":
            from .linux.launcher import launch_application

            return await asyncio.to_thread(launch_application, app_name)
        else:
            logger.error(f"[AppLauncher] 不支持的操作系统: {system}")
            return False

    except Exception as e:
        logger.error(f"[AppLauncher] 启动应用程序失败: {e}")
        return False


def get_system_launcher():
    """根据当前系统获取对应的启动器模块.

    Returns:
        对应系统的启动器模块
    """
    system = platform.system()

    if system == "Darwin":  # macOS
        from .mac import launcher

        return launcher
    elif system == "Windows":  # Windows
        from .windows import launcher

        return launcher
    elif system == "Linux":  # Linux
        from .linux import launcher

        return launcher
    else:
        logger.warning(f"[AppLauncher] 不支持的系统: {system}")
        return None
