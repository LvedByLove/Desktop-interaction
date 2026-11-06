import io
import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from colorlog import ColoredFormatter


def _resolve_log_dir():
    """确定可写的日志目录.

    优先使用用户数据目录（%LOCALAPPDATA%\\<App>\\logs），该目录在安装到
    Program Files 等只读位置时仍可写；失败时回退到项目根目录的 logs。
    返回 None 表示两者均不可用（此时仅使用控制台日志）。
    """
    from .resource_finder import get_project_root, get_user_data_dir

    # 首选：用户数据目录
    try:
        data_dir = get_user_data_dir(create=True)
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir
    except Exception:
        pass

    # 回退：项目根目录（开发环境或可写安装目录）
    try:
        project_root = get_project_root()
        log_dir = project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir
    except Exception:
        return None


def _make_console_handler():
    """创建控制台处理器；windowed 模式下 sys.stderr 为 None 时返回 None."""
    stream = sys.stderr
    if stream is None:
        # PyInstaller windowed（无控制台）模式下没有 stderr，不添加控制台处理器
        return None

    # 用 UTF-8 + replace 包装，避免中文/emoji 在 cp936 控制台上抛 UnicodeEncodeError
    try:
        if hasattr(stream, "buffer"):
            stream = io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    return logging.StreamHandler(stream)


def setup_logging():
    """
    配置日志系统.
    """
    # 创建根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)  # 设置根日志级别

    # 清除已有的处理器（避免重复添加）
    if root_logger.handlers:
        root_logger.handlers.clear()

    # 创建格式化器
    formatter = logging.Formatter(
        "%(asctime)s[%(name)s] - %(levelname)s - %(message)s - %(threadName)s"
    )

    # 控制台颜色格式化器
    color_formatter = ColoredFormatter(
        "%(green)s%(asctime)s%(reset)s[%(blue)s%(name)s%(reset)s] - "
        "%(log_color)s%(levelname)s%(reset)s - %(green)s%(message)s%(reset)s - "
        "%(cyan)s%(threadName)s%(reset)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "white",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "red,bg_white",
        },
        secondary_log_colors={"asctime": {"green": "green"}, "name": {"blue": "blue"}},
    )

    # 控制台处理器（windowed 模式下可能为 None）
    console_handler = _make_console_handler()
    if console_handler is not None:
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(color_formatter)
        root_logger.addHandler(console_handler)

    # 文件处理器：目录不可写时降级为仅控制台日志，不阻塞程序启动
    log_file = None
    log_dir = _resolve_log_dir()
    if log_dir is not None:
        try:
            log_file = log_dir / "app.log"
            file_handler = TimedRotatingFileHandler(
                log_file,
                when="midnight",  # 每天午夜切割
                interval=1,  # 每1天
                backupCount=30,  # 保留30天的日志
                encoding="utf-8",
            )
            file_handler.setLevel(logging.INFO)
            file_handler.suffix = "%Y-%m-%d.log"  # 日志文件后缀格式
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            # 文件日志初始化失败不应导致程序崩溃（此时控制台日志仍可用）
            log_file = None
            if sys.stderr is not None:
                try:
                    sys.stderr.write(f"文件日志初始化失败，仅使用控制台日志: {e}\n")
                except Exception:
                    pass

    # 输出日志配置信息
    if log_file is not None:
        logging.info("日志系统已初始化，日志文件: %s", log_file)
    else:
        logging.warning("日志系统已初始化（仅控制台输出，文件日志不可用）")

    return log_file


def get_logger(name):
    """获取统一配置的日志记录器.

    Args:
        name: 日志记录器名称，通常是模块名

    Returns:
        logging.Logger: 配置好的日志记录器

    示例:
        logger = get_logger(__name__)
        logger.info("这是一条信息")
        logger.error("出错了: %s", error_msg)
    """
    logger = logging.getLogger(name)

    # 添加一些辅助方法
    def log_error_with_exc(msg, *args, **kwargs):
        """
        记录错误并自动包含异常堆栈.
        """
        kwargs["exc_info"] = True
        logger.error(msg, *args, **kwargs)

    # 添加到日志记录器
    logger.error_exc = log_error_with_exc

    return logger
