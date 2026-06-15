import platform
import shutil
import subprocess
import re
from functools import wraps
from typing import Any, Callable, List, Optional

from src.utils.logging_config import get_logger
from src.utils.subprocess_utils import run_hidden


class BrightnessController:
    """
    跨平台亮度控制器.
    """

    # 默认亮度常量
    DEFAULT_BRIGHTNESS = 50

    # 平台特定的方法映射
    PLATFORM_INIT = {
        "Windows": "_init_windows",
        "Darwin": "_init_macos",
        "Linux": "_init_linux",
    }

    BRIGHTNESS_METHODS = {
        "Windows": ("_get_windows_brightness", "_set_windows_brightness"),
        "Darwin": ("_get_macos_brightness", "_set_macos_brightness"),
        "Linux": ("_get_linux_brightness", "_set_linux_brightness"),
    }

    LINUX_BRIGHTNESS_METHODS = {
        "brightnessctl": ("_get_brightnessctl_brightness", "_set_brightnessctl_brightness"),
        "xbacklight": ("_get_xbacklight_brightness", "_set_xbacklight_brightness"),
    }

    # 平台特定的模块依赖
    PLATFORM_MODULES = {
        "Windows": {
            "wmi": "wmi",
        },
        "Darwin": {
            "applescript": "applescript",
        },
        "Linux": {},
    }

    def __init__(self):
        """
        初始化亮度控制器.
        """
        self.logger = get_logger("BrightnessController")
        self.system = platform.system()
        self.linux_tool = None
        self._module_cache = {}  # 模块缓存

        # 初始化特定平台的控制器
        init_method_name = self.PLATFORM_INIT.get(self.system)
        if init_method_name:
            init_method = getattr(self, init_method_name)
            init_method()
        else:
            self.logger.warning(f"不支持的操作系统: {self.system}")
            raise NotImplementedError(f"不支持的操作系统: {self.system}")

    def _lazy_import(self, module_name: str, attr: str = None) -> Any:
        """懒加载模块，支持缓存和属性导入.

        Args:
            module_name: 模块名称
            attr: 可选，模块中的属性名

        Returns:
            导入的模块或属性
        """
        if module_name in self._module_cache:
            module = self._module_cache[module_name]
        else:
            try:
                module = __import__(
                    module_name, fromlist=["*"] if "." in module_name else []
                )
                self._module_cache[module_name] = module
            except ImportError as e:
                self.logger.warning(f"导入模块 {module_name} 失败: {e}")
                raise

        if attr:
            return getattr(module, attr)
        return module

    def _safe_execute(self, func_name: str, default_return: Any = None) -> Callable:
        """
        安全执行函数的装饰器.
        """

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    self.logger.warning(f"{func_name}失败: {e}")
                    return default_return

            return wrapper

        return decorator

    def _run_command(
        self, cmd: List[str], check: bool = False
    ) -> Optional[subprocess.CompletedProcess]:
        """
        通用命令执行方法.
        """
        try:
            return run_hidden(cmd, capture_output=True, text=True, check=check)
        except Exception as e:
            self.logger.debug(f"执行命令失败 {' '.join(cmd)}: {e}")
            return None

    def _init_windows(self) -> None:
        """
        初始化Windows亮度控制.
        """
        self.windows_backend = None

        try:
            # 优先使用screen_brightness_control，兼容更多Windows显示器
            self._lazy_import("screen_brightness_control")
            self.windows_backend = "screen_brightness_control"
            self.logger.debug("Windows亮度控制初始化成功，使用screen_brightness_control")
            return
        except Exception as e:
            self.logger.debug(f"screen_brightness_control初始化失败，尝试WMI: {e}")

        try:
            # 使用懒加载导入所需模块
            wmi = self._lazy_import("wmi")
            self.wmi_c = wmi.WMI(namespace="wmi")
            self.windows_backend = "wmi"
            self.logger.debug("Windows亮度控制初始化成功，使用WMI")
        except Exception as e:
            self.logger.error(f"Windows亮度控制初始化失败: {e}")
            raise

    def _init_macos(self) -> None:
        """
        初始化macOS亮度控制.
        """
        try:
            applescript = self._lazy_import("applescript")

            # 测试是否可以访问亮度控制
            result = applescript.run("get brightness")
            if result and result.code != 0:
                # 尝试另一种方式
                result = applescript.run("tell application \"System Events\" to get brightness of display 1")
            self.logger.debug("macOS亮度控制初始化成功")
        except Exception as e:
            self.logger.error(f"macOS亮度控制初始化失败: {e}")
            raise

    def _init_linux(self) -> None:
        """
        初始化Linux亮度控制.
        """
        # 按优先级检查工具
        linux_tools = ["brightnessctl", "xbacklight"]
        for tool in linux_tools:
            if shutil.which(tool):
                self.linux_tool = tool
                break

        if not self.linux_tool:
            self.logger.warning("未找到可用的Linux亮度控制工具 (brightnessctl/xbacklight)")

        self.logger.debug(f"Linux亮度控制初始化成功，使用: {self.linux_tool or 'None'}")

    def get_brightness(self) -> int:
        """
        获取当前亮度 (0-100)
        """
        if not self.linux_tool and self.system == "Linux":
            return self.DEFAULT_BRIGHTNESS

        get_method_name, _ = self.BRIGHTNESS_METHODS.get(self.system, (None, None))
        if not get_method_name:
            return self.DEFAULT_BRIGHTNESS

        get_method = getattr(self, get_method_name)
        return get_method()

    def set_brightness(self, brightness: int) -> None:
        """
        设置亮度 (0-100)
        """
        if not self.linux_tool and self.system == "Linux":
            self.logger.warning("Linux亮度控制工具不可用")
            return

        # 确保亮度在有效范围内
        brightness = max(0, min(100, brightness))

        _, set_method_name = self.BRIGHTNESS_METHODS.get(self.system, (None, None))
        if set_method_name:
            set_method = getattr(self, set_method_name)
            set_method(brightness)

    @property
    def _get_windows_brightness(self) -> Callable[[], int]:
        @self._safe_execute("获取Windows亮度", self.DEFAULT_BRIGHTNESS)
        def get_brightness():
            if self.windows_backend == "screen_brightness_control":
                sbc = self._lazy_import("screen_brightness_control")
                brightness_values = sbc.get_brightness()
                if brightness_values:
                    return int(brightness_values[0])
                return self.DEFAULT_BRIGHTNESS

            for monitor in self.wmi_c.WmiMonitorBrightness():
                return monitor.CurrentBrightness
            return self.DEFAULT_BRIGHTNESS

        return get_brightness

    @property
    def _set_windows_brightness(self) -> Callable[[int], None]:
        @self._safe_execute("设置Windows亮度")
        def set_brightness(brightness):
            if self.windows_backend == "screen_brightness_control":
                sbc = self._lazy_import("screen_brightness_control")
                sbc.set_brightness(brightness)
                self.logger.debug(f"screen_brightness_control设置亮度成功: {brightness}%")
                return

            # 获取所有显示器名称，用于过滤虚拟显示器
            monitor_names = []
            try:
                for monitor in self.wmi_c.WmiMonitorBasicDisplayParams():
                    if hasattr(monitor, "InstanceName"):
                        monitor_names.append(monitor.InstanceName)
            except Exception as e:
                self.logger.debug(f"获取显示器名称失败: {e}")

            success_count = 0
            for idx, monitor in enumerate(self.wmi_c.WmiMonitorBrightnessMethods()):
                # 获取当前显示器名称
                monitor_name = (
                    monitor_names[idx] if idx < len(monitor_names) else f"Monitor-{idx}"
                )

                # 跳过虚拟显示器（如Dummy、Microsoft Remote Display Adapter等）
                virtual_keywords = [
                    "Dummy",
                    "Remote",
                    "VNC",
                    "VMware",
                    "VirtualBox",
                    "Hyper-V",
                ]
                if any(keyword in monitor_name for keyword in virtual_keywords):
                    self.logger.debug(f"跳过虚拟显示器: {monitor_name}")
                    continue

                try:
                    monitor.WmiSetBrightness(brightness, 0)
                    success_count += 1
                    self.logger.debug(
                        f"成功设置显示器 {monitor_name} 亮度为 {brightness}%"
                    )
                except Exception as e:
                    self.logger.warning(f"设置显示器 {monitor_name} 亮度失败: {e}")

            if success_count == 0:
                self.logger.warning("没有成功设置任何Windows显示器亮度")

        return set_brightness

    @property
    def _get_macos_brightness(self) -> Callable[[], int]:
        @self._safe_execute("获取macOS亮度", self.DEFAULT_BRIGHTNESS)
        def get_brightness():
            applescript = self._lazy_import("applescript")
            # 尝试多种方式获取亮度
            scripts = [
                "get brightness",
                "tell application \"System Events\" to get brightness of display 1",
                "tell application \"System Preferences\" to get brightness",
            ]
            for script in scripts:
                try:
                    result = applescript.run(script)
                    if result and result.out:
                        value = float(result.out.strip())
                        return int(value * 100)
                except Exception:
                    continue
            return self.DEFAULT_BRIGHTNESS

        return get_brightness

    @property
    def _set_macos_brightness(self) -> Callable[[int], None]:
        @self._safe_execute("设置macOS亮度")
        def set_brightness(brightness):
            applescript = self._lazy_import("applescript")
            brightness_value = brightness / 100.0
            applescript.run(f"set brightness to {brightness_value}")

        return set_brightness

    def _get_linux_brightness(self) -> int:
        """
        获取Linux亮度.
        """
        if not self.linux_tool:
            return self.DEFAULT_BRIGHTNESS

        get_method_name, _ = self.LINUX_BRIGHTNESS_METHODS.get(
            self.linux_tool, (None, None)
        )
        if not get_method_name:
            return self.DEFAULT_BRIGHTNESS

        get_method = getattr(self, get_method_name)
        return get_method()

    def _set_linux_brightness(self, brightness: int) -> None:
        """
        设置Linux亮度.
        """
        if not self.linux_tool:
            return

        _, set_method_name = self.LINUX_BRIGHTNESS_METHODS.get(
            self.linux_tool, (None, None)
        )
        if set_method_name:
            set_method = getattr(self, set_method_name)
            set_method(brightness)

    @property
    def _get_brightnessctl_brightness(self) -> Callable[[], int]:
        @self._safe_execute("通过brightnessctl获取亮度", self.DEFAULT_BRIGHTNESS)
        def get_brightness():
            result = self._run_command(["brightnessctl", "get"])
            if result and result.returncode == 0:
                current = int(result.stdout.strip())
                result_max = self._run_command(["brightnessctl", "max"])
                if result_max and result_max.returncode == 0:
                    max_val = int(result_max.stdout.strip())
                    return int((current / max_val) * 100)
            return self.DEFAULT_BRIGHTNESS

        return get_brightness

    @property
    def _set_brightnessctl_brightness(self) -> Callable[[int], None]:
        @self._safe_execute("通过brightnessctl设置亮度")
        def set_brightness(brightness):
            result = self._run_command(["brightnessctl", "set", f"{brightness}%"])
            if result and result.returncode == 0:
                self.logger.debug(f"brightnessctl设置亮度成功: {brightness}%")
            else:
                self.logger.warning(
                    f"brightnessctl设置亮度失败: {result.returncode if result else 'None'}"
                )

        return set_brightness

    @property
    def _get_xbacklight_brightness(self) -> Callable[[], int]:
        @self._safe_execute("通过xbacklight获取亮度", self.DEFAULT_BRIGHTNESS)
        def get_brightness():
            result = self._run_command(["xbacklight", "-get"])
            if result and result.returncode == 0:
                return int(float(result.stdout.strip()))
            return self.DEFAULT_BRIGHTNESS

        return get_brightness

    @property
    def _set_xbacklight_brightness(self) -> Callable[[int], None]:
        @self._safe_execute("通过xbacklight设置亮度")
        def set_brightness(brightness):
            result = self._run_command(["xbacklight", "-set", str(brightness)])
            if result and result.returncode == 0:
                self.logger.debug(f"xbacklight设置亮度成功: {brightness}%")
            else:
                self.logger.warning(
                    f"xbacklight设置亮度失败: {result.returncode if result else 'None'}"
                )

        return set_brightness

    @staticmethod
    def check_dependencies() -> bool:
        """
        检查并报告缺少的依赖.
        """
        system = platform.system()
        missing = []

        # 检查Python模块依赖
        BrightnessController._check_python_modules(system, missing)

        # 检查Linux工具依赖
        if system == "Linux":
            BrightnessController._check_linux_tools(missing)

        # 报告缺少的依赖
        return BrightnessController._report_missing_dependencies(system, missing)

    @staticmethod
    def _check_python_modules(system: str, missing: List[str]) -> None:
        """
        检查Python模块依赖.
        """
        if system == "Windows":
            has_supported_module = False
            for module in ["screen_brightness_control", "wmi"]:
                try:
                    __import__(module)
                    has_supported_module = True
                    break
                except ImportError:
                    continue
            if not has_supported_module:
                missing.append("screen_brightness_control 或 wmi")
        elif system == "Darwin":  # macOS
            try:
                __import__("applescript")
            except ImportError:
                missing.append("applescript")

    @staticmethod
    def _check_linux_tools(missing: List[str]) -> None:
        """
        检查Linux工具依赖.
        """
        tools = ["brightnessctl", "xbacklight"]
        found = any(shutil.which(tool) for tool in tools)
        if not found:
            missing.append("brightnessctl 或 xbacklight")

    @staticmethod
    def _report_missing_dependencies(system: str, missing: List[str]) -> bool:
        """
        报告缺少的依赖.
        """
        if missing:
            print(f"警告: 亮度控制需要以下依赖，但未找到: {', '.join(missing)}")
            print("请使用以下命令安装缺少的依赖:")
            if system in ["Windows", "Darwin"]:
                print("pip install " + " ".join(missing))
            elif system == "Linux":
                print("sudo apt-get install " + " ".join(missing))
            return False
        return True
