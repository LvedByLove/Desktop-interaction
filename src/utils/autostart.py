"""开机自启动管理（Windows 注册表 HKCU Run 键，免管理员权限）."""

import sys
from pathlib import Path

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# 注册表 Run 键与值名
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "DesktopInteraction"


def _is_frozen() -> bool:
    """判断是否为打包后的独立可执行文件（兼容 PyInstaller 与 Nuitka）.

    - PyInstaller: 设置 sys.frozen=True
    - Nuitka standalone: 不设置 sys.frozen；__compiled__ 仅在主模块 globals 中；
      sys.executable 指向 dist 下不存在的 stub python.exe（而非真实 app exe），
      因此唯一可靠信号是 sys.argv[0] 以 .exe 结尾（真实 exe 路径）。
    """
    return (
        getattr(sys, "frozen", False)
        or "__compiled__" in globals()
        or Path(sys.argv[0]).suffix.lower() == ".exe"
    )


def _launch_command() -> str:
    """构造自启动命令行（独立可执行文件路径，带引号）.

    使用 sys.argv[0]（真实 app exe）而非 sys.executable：PyInstaller 两者相同，
    但 Nuitka standalone 的 sys.executable 指向 dist 下不存在的 stub python.exe。
    """
    if not _is_frozen():
        return ""
    return f'"{Path(sys.argv[0]).resolve()}"'


def is_autostart_enabled() -> bool:
    """查询当前是否已注册开机自启动."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ
        ) as key:
            try:
                value, _ = winreg.QueryValueEx(key, _VALUE_NAME)
                return bool(value)
            except FileNotFoundError:
                return False
    except Exception as e:
        logger.debug(f"查询开机自启动状态失败: {e}")
        return False


def enable_autostart() -> bool:
    """注册开机自启动."""
    if sys.platform != "win32":
        return False
    command = _launch_command()
    if not command:
        logger.warning("非打包环境，跳过开机自启动注册")
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, command)
        logger.info(f"已注册开机自启动: {command}")
        return True
    except Exception as e:
        logger.error(f"注册开机自启动失败: {e}")
        return False


def disable_autostart() -> bool:
    """取消开机自启动."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass
        logger.info("已取消开机自启动")
        return True
    except Exception as e:
        logger.error(f"取消开机自启动失败: {e}")
        return False