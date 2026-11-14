import io
import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

_RESET = "\033[0m"
_DIM = "\033[2m"
_BLUE = "\033[34m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;37;41m",
}


def _shorten_name(name: str, max_length: int = 25) -> str:
    """缩写 logger 名称，例如 src.ui.shared.bridge.event_bridge."""
    if len(name) <= max_length:
        return name
    parts = name.split(".")
    if len(parts) == 1:
        return name[:max_length]
    result = []
    for index, part in enumerate(parts[:-1]):
        result.append(part[:3] if index == 0 else part[0])
    result.append(parts[-1])
    return ".".join(result)[:max_length]


def _supports_color(stream) -> bool:
    """判断当前终端是否支持 ANSI 颜色."""
    try:
        if not hasattr(stream, "isatty") or not stream.isatty():
            return False
    except Exception:
        return False

    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # -11=STD_OUTPUT_HANDLE, -12=STD_ERROR_HANDLE；日志走 stderr，两个都开启 VT
            for handle in (-11, -12):
                kernel32.SetConsoleMode(kernel32.GetStdHandle(handle), 7)
        except Exception:
            return False
    return True


class _ProjectFormatter(logging.Formatter):
    """统一日志格式：时间 级别 [缩写模块] 消息（+ 非主线程名）."""

    def __init__(self, use_colors: bool = False):
        super().__init__()
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]
        level = record.levelname.ljust(8)
        name = _shorten_name(record.name)
        message = record.getMessage()

        if self.use_colors:
            timestamp = f"{_DIM}{timestamp}{_RESET}"
            level = f"{_LEVEL_COLORS.get(record.levelno, '')}{level}{_RESET}"
            name = f"{_BLUE}[{name}]{_RESET}"
            if record.levelno >= logging.ERROR:
                message = f"{_LEVEL_COLORS[logging.ERROR]}{message}{_RESET}"
        else:
            name = f"[{name}]"

        if record.threadName != "MainThread":
            if self.use_colors:
                message = f"{message} {_DIM}({record.threadName}){_RESET}"
            else:
                message = f"{message} ({record.threadName})"

        line = f"{timestamp} {level} {name} {message}"
        if record.exc_info:
            exception_text = self.formatException(record.exc_info)
            if self.use_colors:
                exception_text = f"{_LEVEL_COLORS[logging.ERROR]}{exception_text}{_RESET}"
            line = f"{line}\n{exception_text}"
        return line


def _resolve_log_dir():
    """确定可写的日志目录.

    优先使用用户数据目录（%LOCALAPPDATA%\\<App>\\logs），该目录在安装到
    Program Files 等只读位置时仍可写；失败时回退到项目根目录的 logs。
    返回 None 表示两者均不可用（此时仅使用控制台日志）。
    """
    from .resource_finder import get_log_dir, get_project_root

    # 首选：用户数据目录（已应用 PATHS.LOG_DIR / XIAOZHI_LOG_DIR 覆盖）
    try:
        log_dir = get_log_dir(create=True)
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
    # 先创建配置单例：它会应用 PATHS.LOG_DIR 等目录覆盖，
    # 这样本次启动的日志就能写到用户自定义目录。
    try:
        from .config_manager import ConfigManager

        ConfigManager.get_instance()
    except Exception:
        # 配置损坏时不应阻塞日志初始化，退回默认目录
        pass

    # 创建根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)  # 设置根日志级别

    # 清除已有的处理器（避免重复添加）
    if root_logger.handlers:
        root_logger.handlers.clear()

    # 控制台处理器（windowed 模式下可能为 None）
    console_handler = _make_console_handler()
    if console_handler is not None:
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(
            _ProjectFormatter(use_colors=_supports_color(console_handler.stream))
        )
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
            file_handler.setFormatter(_ProjectFormatter(use_colors=False))
            root_logger.addHandler(file_handler)
        except Exception as e:
            # 文件日志初始化失败不应导致程序崩溃（此时控制台日志仍可用）
            log_file = None
            if sys.stderr is not None:
                try:
                    sys.stderr.write(f"文件日志初始化失败，仅使用控制台日志: {e}\n")
                except Exception:
                    pass

    # 第三方库日志保持安静，避免刷屏
    for name in (
        "urllib3",
        "websockets",
        "asyncio",
        "qasync",
        "paho",
        "PIL",
        "sounddevice",
        "matplotlib",
    ):
        try:
            logging.getLogger(name).setLevel(logging.WARNING)
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
