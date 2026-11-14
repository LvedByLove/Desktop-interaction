import argparse
import asyncio
import os
import signal
import sys

# OpenBLAS（numpy 自带）会按 CPU 线程数预分配 32MB 工作缓冲区。
# 本机 20 线程时会在导入 numpy 时提交 20×32MB=640MB 内存（且可能全部驻留），
# 而本项目只用 numpy 做小规模音频数组运算，完全不需要 BLAS 多线程。
# 必须在导入 numpy 之前设置；main.py 下方的 src.application 会间接导入 numpy。
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

# qasync 按 QT_API 选择绑定；本应用已迁移为 PySide6，直接强制指定，
# 避免外部环境残留 QT_API=pyqt5 时选错绑定。
os.environ["QT_API"] = "pyside6"

# Qt6 默认启用高 DPI 自动缩放，会把整套界面在 125% 缩放的屏幕上放大 25%，
# 与旧 PyQt5 版本（DPR=1.0）的观感不一致。Windows 下关闭自动缩放以保持
# 原有尺寸和字体基线；macOS/Linux 保留 Qt6 的原生高 DPI 行为。
if sys.platform == "win32":
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

# QML 里的 Button/TextField 等使用自定义 background/contentItem，
# 原生 Quick Controls 样式不支持这种定制，会打印 warning 且外观失效。
# 统一使用可定制的 Basic 样式。
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"

_single_instance_lock = None

# 单实例运行检测
def check_single_instance() -> bool:
    """
    检查是否已有实例在运行.
    
    Returns:
        bool: 如果是第一个实例返回 True，否则返回 False
    """
    global _single_instance_lock

    if sys.platform == "win32":
        # Windows 使用命名互斥锁，句柄必须保持引用，否则会被释放导致检测失效。
        import win32api
        import win32event
        import winerror

        mutex_name = "Local\\xiaozhi_ai_client_single_instance"
        try:
            mutex = win32event.CreateMutex(None, False, mutex_name)
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                win32api.CloseHandle(mutex)
                return False

            _single_instance_lock = mutex
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
            _single_instance_lock = lock_file
            return True
        except BlockingIOError:
            # 已有实例运行
            return False
        except Exception:
            # 如果无法创建文件锁，允许运行（降级处理）
            return True


from src.application import Application
from src.utils.logging_config import get_logger, setup_logging


def wait_for_parent_exit(parent_pid: int):
    """
    自重启时等待旧进程退出，避免被单实例互斥锁拦截.
    """
    if parent_pid <= 0:
        return

    try:
        import os
        import time

        if parent_pid == os.getpid():
            return

        if sys.platform == "win32":
            import win32api
            import win32con
            import win32event

            handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, parent_pid)
            try:
                win32event.WaitForSingleObject(handle, 10000)
            finally:
                win32api.CloseHandle(handle)
        else:
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    os.kill(parent_pid, 0)
                except OSError:
                    break
                time.sleep(0.1)
    except Exception as e:
        logger.debug(f"等待旧实例退出失败，继续启动: {e}")

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
    parser.add_argument(
        "--wait-parent-pid",
        type=int,
        default=0,
        help=argparse.SUPPRESS,
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

    # 内存采样诊断（默认关闭，仅当 XIAOZHI_MEM_PROFILE=1 时启用）
    from src.utils import memory_profiler

    memory_profiler.start()

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

        # 先等旧实例退出：setup_logging 会创建 ConfigManager 并执行
        # PATHS 目录迁移，必须避免与旧进程同时读写日志/缓存目录。
        wait_for_parent_exit(args.wait_parent_pid)
        setup_logging()

        if args.mode == "gui" and not check_single_instance():
            logger.info("检测到已有实例运行，当前实例退出")
            sys.exit(0)

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
            # Windows：设置显式的 AppUserModelID，使任务栏正确分组并使用程序图标
            # （未设置时 Win11 会把 pythonw/安装包当作默认图标分组）
            if sys.platform == "win32":
                try:
                    import ctypes

                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                        "xiaozhi.desktop-interaction.client"
                    )
                except Exception as e:
                    logger.debug(f"设置 AppUserModelID 失败: {e}")

            # 在GUI模式下，由main统一创建 QApplication 与 qasync 事件循环
            try:
                import qasync
                from PySide6.QtGui import QIcon
                from PySide6.QtWidgets import QApplication
            except ImportError as e:
                logger.error(f"GUI模式需要qasync和PySide6库: {e}")
                sys.exit(1)

            qt_app = QApplication.instance() or QApplication(sys.argv)

            # 恢复 Qt5 时代的原生 Windows 样式，避免 Qt6 windows11 样式
            # 与现有 QSS/控件尺寸产生差异。
            if sys.platform == "win32":
                try:
                    qt_app.setStyle("windowsvista")
                except Exception as e:
                    logger.debug(f"设置 windowsvista 样式失败: {e}")

            # 设置应用级窗口图标（任务栏/窗口标题栏），资源随包打包
            try:
                from src.utils import resource_finder

                icon_path = resource_finder.find_file("assets/image/Redstone.png")
                if icon_path:
                    qt_app.setWindowIcon(QIcon(str(icon_path)))
            except Exception as e:
                logger.debug(f"设置应用图标失败: {e}")

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
