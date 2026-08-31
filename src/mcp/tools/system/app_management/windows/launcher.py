"""Windows系统应用程序启动器.

提供Windows平台下的应用程序启动功能
"""

import os
import subprocess
import webbrowser
from typing import List, Optional

from src.utils.com_utils import com_initialized
from src.utils.logging_config import get_logger
from src.utils.subprocess_utils import run_hidden

logger = get_logger(__name__)

# 不允许出现在应用名中的 shell 元字符（用于命令行/PowerShell 调用前的校验）
_SHELL_FORBIDDEN_CHARS = set('&|<>^%`\n\r;')


def _is_shell_safe(value: str) -> bool:
    """检查字符串是否不含 shell 元字符，可安全用于命令行调用."""
    if not value:
        return False
    return not any(ch in _SHELL_FORBIDDEN_CHARS for ch in value)


def launch_application(app_name: str) -> bool:
    """在Windows上启动应用程序.

    Args:
        app_name: 应用程序名称

    Returns:
        bool: 启动是否成功
    """
    try:
        logger.info(f"[WindowsLauncher] 启动应用程序: {app_name}")

        if _is_url(app_name):
            webbrowser.open(app_name)
            logger.info(f"[WindowsLauncher] 默认浏览器打开URL成功: {app_name}")
            return True

        # 按优先级尝试不同的启动方法
        launch_methods = [
            ("os.startfile", _try_os_startfile),
            ("PowerShell Start-Process", _try_powershell_start),
            ("注册表查找", _try_registry_launch),
            ("常见路径", _try_common_paths),
            ("where命令", _try_where_command),
            ("UWP应用", _try_uwp_launch),
        ]

        for method_name, method_func in launch_methods:
            try:
                if method_func(app_name):
                    logger.info(f"[WindowsLauncher] {method_name}成功启动: {app_name}")
                    return True
                else:
                    logger.debug(f"[WindowsLauncher] {method_name}启动失败: {app_name}")
            except Exception as e:
                logger.debug(f"[WindowsLauncher] {method_name}异常: {e}")

        logger.warning(f"[WindowsLauncher] 所有Windows启动方法都失败了: {app_name}")
        return False

    except Exception as e:
        logger.error(f"[WindowsLauncher] Windows启动异常: {e}", exc_info=True)
        return False


def launch_application_with_args(app_path: str, args: Optional[List[str]] = None) -> bool:
    """启动应用程序并传入参数（例如浏览器 URL）.

    Args:
        app_path: 应用程序路径或命令
        args: 传给应用程序的参数

    Returns:
        bool: 启动是否成功
    """
    try:
        args = args or []
        logger.info(f"[WindowsLauncher] 启动应用程序并传参: {app_path} {args}")

        if app_path.lower().endswith(".lnk"):
            return launch_shortcut(app_path, args)

        if os.path.exists(app_path):
            subprocess.Popen([app_path, *args])
            return True

        subprocess.Popen([app_path, *args], shell=False)
        return True
    except Exception as e:
        logger.error(f"[WindowsLauncher] 启动应用程序并传参失败: {e}")
        return False


def launch_uwp_app_by_path(uwp_path: str) -> bool:
    """通过UWP路径启动应用程序.

    Args:
        uwp_path: UWP应用程序路径（shell:AppsFolder\\...格式）

    Returns:
        bool: 启动是否成功
    """
    try:
        if uwp_path.startswith("shell:AppsFolder\\"):
            # 使用explorer启动UWP应用
            subprocess.Popen(["explorer.exe", uwp_path])
            logger.info(f"[WindowsLauncher] UWP应用启动成功: {uwp_path}")
            return True
        else:
            return False
    except Exception as e:
        logger.error(f"[WindowsLauncher] UWP应用启动失败: {e}")
        return False


def launch_shortcut(shortcut_path: str, args: Optional[List[str]] = None) -> bool:
    """启动快捷方式文件.

    Args:
        shortcut_path: 快捷方式文件路径
        args: 传给快捷方式目标程序的参数

    Returns:
        bool: 启动是否成功
    """
    try:
        if args:
            target_path = _resolve_shortcut_target(shortcut_path)
            if target_path:
                subprocess.Popen([target_path, *args])
            else:
                os.startfile(shortcut_path)
        else:
            os.startfile(shortcut_path)
        logger.info(f"[WindowsLauncher] 快捷方式启动成功: {shortcut_path}")
        return True
    except Exception as e:
        logger.error(f"[WindowsLauncher] 快捷方式启动失败: {e}")
        return False


def _is_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _resolve_shortcut_target(shortcut_path: str) -> Optional[str]:
    """解析 Windows 快捷方式目标路径."""
    try:
        with com_initialized():
            import win32com.client

            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(shortcut_path)
            target_path = shortcut.Targetpath
            if target_path and os.path.exists(target_path):
                return target_path
    except Exception as e:
        logger.debug(f"[WindowsLauncher] 解析快捷方式失败: {e}")
    return None


def _try_powershell_start(app_name: str) -> bool:
    """
    尝试使用PowerShell Start-Process启动应用程序.

    目标名称通过环境变量传入，避免字符串拼接导致的命令注入。
    """
    if not _is_shell_safe(app_name):
        return False
    try:
        ps_script = (
            "$ErrorActionPreference='Stop';"
            "try { Start-Process -FilePath $env:XZ_LAUNCH_TARGET; Write-Output 'OK' }"
            "catch { exit 1 }"
        )
        env = dict(os.environ)
        env["XZ_LAUNCH_TARGET"] = app_name
        result = run_hidden(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        return result.returncode == 0 and "OK" in (result.stdout or "")
    except Exception as e:
        logger.debug(f"[WindowsLauncher] PowerShell启动异常: {e}")
        return False


def _try_os_startfile(app_name: str) -> bool:
    """
    尝试使用os.startfile启动应用程序.
    """
    try:
        os.startfile(app_name)
        return True
    except OSError:
        return False


def _try_registry_launch(app_name: str) -> bool:
    """
    尝试通过注册表查找并启动应用程序.
    """
    try:
        executable_path = _find_executable_in_registry(app_name)
        if executable_path:
            subprocess.Popen([executable_path])
            return True
    except Exception as e:
        logger.debug(f"[WindowsLauncher] 注册表启动异常: {e}")
    return False


def _try_common_paths(app_name: str) -> bool:
    """
    尝试常见的应用程序路径（使用环境变量，避免硬编码盘符和用户名）.
    """
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_app_data = os.environ.get("LOCALAPPDATA")
    app_data = os.environ.get("APPDATA")

    common_paths = [
        os.path.join(program_files, app_name, f"{app_name}.exe"),
        os.path.join(program_files_x86, app_name, f"{app_name}.exe"),
    ]
    if local_app_data:
        common_paths.extend([
            os.path.join(local_app_data, "Programs", app_name, f"{app_name}.exe"),
            os.path.join(local_app_data, app_name, f"{app_name}.exe"),
        ])
    if app_data:
        common_paths.append(os.path.join(app_data, app_name, f"{app_name}.exe"))

    for path in common_paths:
        if os.path.exists(path):
            try:
                subprocess.Popen([path])
                return True
            except Exception:
                continue
    return False


def _try_where_command(app_name: str) -> bool:
    """
    尝试使用where命令查找并启动应用程序.
    """
    if not _is_shell_safe(app_name):
        return False
    try:
        result = run_hidden(
            ["where", app_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            exe_path = result.stdout.strip().split("\n")[0]  # 取第一个结果
            if exe_path and os.path.exists(exe_path):
                subprocess.Popen([exe_path])
                return True
    except Exception as e:
        logger.debug(f"[WindowsLauncher] where命令异常: {e}")
    return False


def _try_uwp_launch(app_name: str) -> bool:
    """
    尝试启动UWP应用程序.
    """
    try:
        return _launch_uwp_app(app_name)
    except Exception as e:
        logger.debug(f"[WindowsLauncher] UWP启动异常: {e}")
        return False


def _find_executable_in_registry(app_name: str) -> Optional[str]:
    """通过注册表查找应用程序的可执行文件路径.

    同时检查 HKLM（64位、32位WOW6432Node）与 HKCU（用户级安装）三个位置。

    Args:
        app_name: 应用程序名称

    Returns:
        应用程序路径，如果没找到则返回None
    """
    try:
        import winreg

        # (注册表根, 子路径) —— 覆盖系统级与用户级安装
        uninstall_subpath = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
        registry_roots = [
            (winreg.HKEY_LOCAL_MACHINE, uninstall_subpath),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, uninstall_subpath),
        ]

        for hive, registry_path in registry_roots:
            try:
                with winreg.OpenKey(hive, registry_path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                try:
                                    display_name = winreg.QueryValueEx(
                                        subkey, "DisplayName"
                                    )[0]
                                    if app_name.lower() in str(display_name).lower():
                                        exe_path = _resolve_exe_from_uninstall_key(
                                            subkey, app_name
                                        )
                                        if exe_path:
                                            return exe_path
                                except FileNotFoundError:
                                    continue
                        except Exception:
                            continue
            except FileNotFoundError:
                # 该根/路径不存在（如某些系统无 WOW6432Node），继续下一个
                continue
            except Exception:
                continue

        return None

    except ImportError:
        logger.debug("[WindowsLauncher] winreg模块不可用，跳过注册表查找")
        return None
    except Exception as e:
        logger.debug(f"[WindowsLauncher] 注册表查找失败: {e}")
        return None


def _resolve_exe_from_uninstall_key(subkey, app_name: str) -> Optional[str]:
    """从卸载项子键中解析可执行文件路径（DisplayIcon 优先，InstallLocation 次之）."""
    import winreg

    # 优先使用 DisplayIcon（形如 "C:\\...\\app.exe" 或 "C:\\...\\app.exe,0"）
    try:
        display_icon = winreg.QueryValueEx(subkey, "DisplayIcon")[0]
        if display_icon:
            icon_path = str(display_icon).split(",")[0].strip().strip('"')
            if icon_path.lower().endswith(".exe") and os.path.exists(icon_path):
                return icon_path
    except (FileNotFoundError, OSError):
        pass

    # 其次在 InstallLocation 下浅层查找匹配的 exe（限制遍历深度，避免扫超大目录）
    try:
        install_location = winreg.QueryValueEx(subkey, "InstallLocation")[0]
        if install_location and os.path.isdir(install_location):
            base_depth = install_location.rstrip("\\/").count(os.sep)
            for root, dirs, files in os.walk(install_location):
                # 仅向下查找 2 层
                if root.count(os.sep) - base_depth >= 2:
                    dirs[:] = []
                for file in files:
                    if file.lower().endswith(".exe") and app_name.lower() in file.lower():
                        return os.path.join(root, file)
    except (FileNotFoundError, OSError):
        pass

    return None


def _launch_uwp_app(app_name: str) -> bool:
    """尝试启动UWP（Windows Store）应用程序.

    Args:
        app_name: 应用程序名称

    Returns:
        bool: 启动是否成功
    """
    try:
        # 关键字通过环境变量传入，避免注入
        powershell_script = """
        $keyword = $env:XZ_UWP_KEYWORD
        $app = Get-AppxPackage | Where-Object {$_.Name -like "*$keyword*" -or $_.PackageFullName -like "*$keyword*"} | Select-Object -First 1
        if ($app) {
            $manifest = Get-AppxPackageManifest $app.PackageFullName
            $appId = $manifest.Package.Applications.Application.Id
            if ($appId) {
                Start-Process "shell:AppsFolder\\$($app.PackageFullName)!$appId"
                Write-Output "Success"
            }
        }
        """
        env = dict(os.environ)
        env["XZ_UWP_KEYWORD"] = app_name

        result = run_hidden(
            ["powershell", "-NoProfile", "-Command", powershell_script],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )

        if result.returncode == 0 and "Success" in (result.stdout or ""):
            return True

    except Exception as e:
        logger.debug(f"[WindowsLauncher] UWP启动异常: {e}")

    return False
