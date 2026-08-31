"""Subprocess helpers."""

import platform
import subprocess
from typing import Any, Dict


def _default_text_encoding() -> str:
    """获取 text 模式下子进程输出的解码编码.

    Windows 中文系统上 tasklist/taskkill/wmic 等使用 OEM 代码页（如 cp936），
    PowerShell 也可能随系统配置输出 GBK 或 UTF-8。这里动态取 OEM 代码页，
    并配合 errors="replace" 保证中文应用名/窗口标题不会因解码失败而乱码或抛异常。
    """
    if platform.system() == "Windows":
        try:
            import ctypes

            oem_cp = ctypes.windll.kernel32.GetOEMCP()
            if oem_cp:
                return f"cp{oem_cp}"
        except Exception:
            pass
    # 非 Windows 或获取失败时退回 locale 首选编码
    return locale_getpreferredencoding()


def locale_getpreferredencoding() -> str:
    import locale

    try:
        return locale.getpreferredencoding(False)
    except Exception:
        return "utf-8"


def get_hidden_subprocess_kwargs() -> Dict[str, Any]:
    """
    获取隐藏控制台窗口的subprocess参数.
    """
    if platform.system() != "Windows":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE

    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }


def _apply_text_encoding(kwargs: Dict[str, Any]) -> None:
    """文本模式下为子进程输出注入默认编码与容错策略（原地修改 kwargs）.

    解决中文 Windows 上 tasklist/PowerShell 等输出解码乱码或抛
    UnicodeDecodeError 的问题。调用方显式指定了 encoding 时不覆盖。
    """
    is_text = kwargs.get("text", False) or kwargs.get("universal_newlines", False)
    if not is_text:
        return
    if kwargs.get("encoding"):
        # 调用方已显式指定编码，仅确保有 errors 容错
        kwargs.setdefault("errors", "replace")
        return
    kwargs["encoding"] = _default_text_encoding()
    kwargs.setdefault("errors", "replace")


def run_hidden(*popenargs, **kwargs) -> subprocess.CompletedProcess:
    """
    执行后台命令，Windows下不显示控制台窗口.
    """
    kwargs.update(get_hidden_subprocess_kwargs())
    _apply_text_encoding(kwargs)
    return subprocess.run(*popenargs, **kwargs)


def popen_hidden(*popenargs, **kwargs) -> subprocess.Popen:
    """
    启动后台进程，Windows下不显示控制台窗口.

    仅用于辅助命令，不要用于真正需要显示窗口的目标应用。
    """
    kwargs.update(get_hidden_subprocess_kwargs())
    _apply_text_encoding(kwargs)
    return subprocess.Popen(*popenargs, **kwargs)
