"""Subprocess helpers."""

import platform
import subprocess
from typing import Any, Dict


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


def run_hidden(*popenargs, **kwargs) -> subprocess.CompletedProcess:
    """
    执行后台命令，Windows下不显示控制台窗口.
    """
    kwargs.update(get_hidden_subprocess_kwargs())
    return subprocess.run(*popenargs, **kwargs)


def popen_hidden(*popenargs, **kwargs) -> subprocess.Popen:
    """
    启动后台进程，Windows下不显示控制台窗口.

    仅用于辅助命令，不要用于真正需要显示窗口的目标应用。
    """
    kwargs.update(get_hidden_subprocess_kwargs())
    return subprocess.Popen(*popenargs, **kwargs)
