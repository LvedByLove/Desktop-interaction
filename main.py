import argparse
import asyncio
import signal
import sys

# 单实例运行检测
def check_single_instance() -> bool:
    """
    检查是否已有实例在运行.
    
    Returns:
        bool: 如果是第一个实例返回 True，否则返回 False
    """
    if sys.platform == "win32":
        # Windows 使用命名互斥锁
        import win32event
        import win32api
        import winerror
        
        mutex_name = "小智Ai客户端_单实例锁"
        try:
            # 创建互斥锁，False 表示如果已存在则返回已有的句柄
            mutex = win32event.CreateMutex(None, False, mutex_name)
            # 检查是否成功创建（ERROR_ALREADY_EXISTS 表示已存在）
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                # 已有实例运行
                return False
            return True
        except Exception:
            # 如果无法创建互斥锁（如缺少 pywin32），允许运行（降级处理）
            return True
    else:
        # Linux/macOS 使用文件锁
        import fcntl
        import os
        
        lock_file_path = "/tmp/xiaozhi_ai_client.lock"
        try:
            lock_file = open(lock_file_path, "w")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            # 已有实例运行
            return False
        except Exception:
            # 如果无法创建文件锁，允许运行（降级处理）
            return True


from src.application import Application
from src.utils.logging_config import get_logger, setup_logging

logger = get_logger(__name__)


def parse_args():
    """
    解析命令行参数.
    """
    parser = argparse.ArgumentParser(description="小智Ai客户端")
    parser.add_argument(
        "--mode",
        choices=["gui", "cli"],
        default="gui",
        help="运行模式：gui(图形界面) 或 cli(命令行)",
    )
    parser.add_argument(
        "--protocol",
        choices=["mqtt", "websocket"],
        default="websocket",
        help="通信协议：mqtt 或 websocket",
    )
    parser.add_argument(
        "--skip-activation",
        action="store_true",
        help="跳过激活流程，直接启动应用（仅用于调试）",
    )
    return parser.parse_args()


async def handle_activation(mode: str) -> bool:
    """处理设备激活流程，依赖已有事件循环.

    Args:
        mode: 运行模式，"gui"或"cli"

    Returns:
        bool: 激活是否成功
    """
    try:
        from src.core.system_initializer import SystemInitializer

        logger.info("开始设备激活流程检查...")

        system_initializer = SystemInitializer()
        # 统一使用 SystemInitializer 内的激活处理，GUI/CLI 自适应
        result = await system_initializer.handle_activation_process(mode=mode)
        success = bool(result.get("is_activated", False))
        logger.info(f"激活流程完成，结果: {success}")
        return success
    except Exception as e:
        logger.error(f"激活流程异常: {e}", exc_info=True)
        return False


async def start_app(mode: str, protocol: str, skip_activation: bool) -> int:
    """
    启动应用的统一入口（在已有事件循环中执行）.
    """
    logger.info("启动小智AI客户端")

    # 处理激活流程
    if not skip_activation:
        activation_success = await handle_activation(mode)
        if not activation_success:
            logger.error("设备激活失败，程序退出")
            return 1
    else:
        logger.warning("跳过激活流程（调试模式）")

    # 创建并启动应用程序
    app = Application.get_instance()
    return await app.run(mode=mode, protocol=protocol)


if __name__ == "__main__":
    exit_code = 1
    try:
        args = parse_args()
        setup_logging()

        # 检测Wayland环境并设置Qt平台插件配置
        import os

        is_wayland = (
            os.environ.get("WAYLAND_DISPLAY")
            or os.environ.get("XDG_SESSION_TYPE") == "wayland"
        )

        if args.mode == "gui" and is_wayland:
            # 在Wayland环境下，确保Qt使用正确的平台插件
            if "QT_QPA_PLATFORM" not in os.environ:
                # 优先使用wayland插件，失败则回退到xcb（X11兼容层）
                os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
                logger.info("Wayland环境：设置QT_QPA_PLATFORM=wayland;xcb")

            # 禁用一些在Wayland下不稳定的Qt特性
            os.environ.setdefault("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
            logger.info("Wayland环境检测完成，已应用兼容性配置")

        # 统一设置信号处理：忽略 macOS 上可能出现的 SIGTRAP，避免“trace trap”导致进程退出
        try:
            if hasattr(signal, "SIGINT"):
                # 交由 qasync/Qt 处理 Ctrl+C；保持默认或后续由 GUI 层处理
                pass
            if hasattr(signal, "SIGTERM"):
                # 允许进程收到终止信号时走正常关闭路径
                pass
            if hasattr(signal, "SIGTRAP"):
                signal.signal(signal.SIGTRAP, signal.SIG_IGN)
        except Exception:
            # 某些平台/环境不支持设置这些信号，忽略即可
            pass

        if args.mode == "gui":
            # 在GUI模式下，由main统一创建 QApplication 与 qasync 事件循环
            try:
                import qasync
                from PyQt5.QtWidgets import QApplication
            except ImportError as e:
                logger.error(f"GUI模式需要qasync和PyQt5库: {e}")
                sys.exit(1)

            qt_app = QApplication.instance() or QApplication(sys.argv)

            loop = qasync.QEventLoop(qt_app)
            asyncio.set_event_loop(loop)
            logger.info("已在main中创建qasync事件循环")

            # 确保关闭最后一个窗口不会自动退出应用，避免事件环提前停止
            try:
                qt_app.setQuitOnLastWindowClosed(False)
            except Exception:
                pass

            with loop:
                exit_code = loop.run_until_complete(
                    start_app(args.mode, args.protocol, args.skip_activation)
                )
        else:
            # CLI模式使用标准asyncio事件循环
            exit_code = asyncio.run(
                start_app(args.mode, args.protocol, args.skip_activation)
            )

    except KeyboardInterrupt:
        logger.info("程序被用户中断")
        exit_code = 0
    except Exception as e:
        logger.error(f"程序异常退出: {e}", exc_info=True)
        exit_code = 1
    finally:
        sys.exit(exit_code)
