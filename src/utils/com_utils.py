"""COM 线程初始化辅助工具.

Windows 上的 COM 调用（pycaw 音量、wmi 亮度、WScript.Shell 快捷方式解析、
SAPI 语音等）要求调用线程先初始化 COM。在 asyncio.to_thread 使用的共享线程池
线程中 COM 默认未初始化，会导致间歇性失败（CO_E_NOTINITIALIZED / 0x800401F0）
或跨 apartment 访问问题。

使用方式：
    with com_initialized():
        # 在此创建并使用 COM 对象
        ...
"""

import sys
from contextlib import contextmanager

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@contextmanager
def com_initialized():
    """在当前线程初始化 COM（STA），退出时反初始化.

    非 Windows 平台或缺少 pywin32 时为空操作。COM 对象的创建与使用必须在
    同一个 with 块（同一个线程、同一个 apartment）内完成。
    """
    pythoncom = None
    if sys.platform == "win32":
        try:
            import pythoncom
        except Exception as e:  # pywin32 缺失时降级，不阻塞功能
            logger.debug(f"pythoncom 不可用，跳过 COM 初始化: {e}")

    if pythoncom is not None:
        # CoInitialize 使用 STA；重复调用返回 S_FALSE 且仍需配对 CoUninitialize
        pythoncom.CoInitialize()
        try:
            yield
        finally:
            pythoncom.CoUninitialize()
    else:
        yield
