import platform
import subprocess
import time
from typing import Optional

from src.utils.com_utils import com_initialized
from src.utils.logging_config import get_logger
from src.utils.subprocess_utils import run_hidden


class VolumeController:
    """
    跨平台音量控制器 - 使用系统API方式.
    """

    # 默认音量常量
    DEFAULT_VOLUME = 70

    # Windows 虚拟键码
    VK_VOLUME_MUTE = 0xAD
    VK_VOLUME_UP = 0xAF
    VK_VOLUME_DOWN = 0xAE
    KEYEVENTF_KEYDOWN = 0x0
    KEYEVENTF_KEYUP = 0x2

    def __init__(self):
        """
        初始化音量控制器.
        """
        self.logger = get_logger("VolumeController")
        self.system = platform.system()
        self._user32 = None
        if self.system == "Windows":
            self._init_windows_user32()

    def _init_windows_user32(self):
        """
        初始化 Windows user32 库.
        """
        try:
            import ctypes
            self._user32 = ctypes.WinDLL('user32', use_last_error=True)
            self.logger.debug("成功加载 user32 库")
        except Exception as e:
            self.logger.warning(f"加载 user32 库失败: {e}")

    def _send_key(self, vk_code: int):
        """
        使用 keybd_event 模拟按键.
        """
        if not self._user32:
            return False

        try:
            # 按下按键
            self._user32.keybd_event(vk_code, 0, self.KEYEVENTF_KEYDOWN, 0)
            time.sleep(0.05)
            # 释放按键
            self._user32.keybd_event(vk_code, 0, self.KEYEVENTF_KEYUP, 0)
            return True
        except Exception as e:
            self.logger.warning(f"发送按键失败: {e}")
            return False

    def set_volume(self, volume: int) -> None:
        """
        设置音量 (0-100).
        """
        volume = max(0, min(100, volume))

        if self.system == "Windows":
            self._set_windows_volume(volume)
        elif self.system == "Darwin":
            self._set_macos_volume(volume)
        elif self.system == "Linux":
            self._set_linux_volume(volume)
        else:
            self.logger.warning(f"不支持的操作系统: {self.system}")

    def get_volume(self) -> int:
        """
        获取当前音量 (0-100).
        """
        if self.system == "Windows":
            return self._get_windows_volume()
        elif self.system == "Darwin":
            return self._get_macos_volume()
        elif self.system == "Linux":
            return self._get_linux_volume()
        else:
            return self.DEFAULT_VOLUME

    def mute(self):
        """
        静音.
        """
        if self.system == "Windows":
            if self._set_windows_mute(True):
                return
            # 降级方案：模拟静音键（切换功能，仅在无法使用 Core Audio 时使用）
            if self._send_key(self.VK_VOLUME_MUTE):
                self.logger.debug("使用 keybd_event 模拟静音")
                return
            self._run_command(["powershell", "-NoProfile", "-Command", "(new-object -com wscript.shell).SendKeys([char]173)"])
        elif self.system == "Darwin":
            self._run_command(["osascript", "-e", "set volume with output muted"])
        elif self.system == "Linux":
            self._run_command(["amixer", "-D", "pulse", "sset", "Master", "mute"])

    def unmute(self):
        """
        取消静音.
        """
        if self.system == "Windows":
            if self._set_windows_mute(False):
                return
            # 静音键是切换功能，无法保证当前状态，故仅在 Core Audio 不可用且
            # 已知处于静音时才用按键降级；这里无法获知状态，记录警告。
            self.logger.warning("无法通过 Core Audio 取消静音，且按键方式无法可靠取消静音")
        elif self.system == "Darwin":
            self._run_command(["osascript", "-e", "set volume without output muted"])
        elif self.system == "Linux":
            self._run_command(["amixer", "-D", "pulse", "sset", "Master", "unmute"])

    def _set_windows_mute(self, muted: bool) -> bool:
        """
        通过 Core Audio(pycaw) 显式设置静音状态.

        Returns:
            bool: 是否成功
        """
        with com_initialized():
            try:
                endpoint_volume = self._get_windows_endpoint_volume()
                if endpoint_volume:
                    endpoint_volume.SetMute(1 if muted else 0, None)
                    self.logger.debug(f"使用pycaw设置静音状态: {muted}")
                    return True
            except Exception as e:
                self.logger.warning(f"使用pycaw设置静音失败: {e}")
        return False

    def _set_windows_volume(self, volume: int):
        """
        设置Windows音量.优先使用 pycaw（毫秒级），失败再降级到 PowerShell.
        """
        # 首选：pycaw + Core Audio，快速且可靠
        with com_initialized():
            try:
                endpoint_volume = self._get_windows_endpoint_volume()
                if endpoint_volume:
                    endpoint_volume.SetMasterVolumeLevelScalar(volume / 100.0, None)
                    if volume > 0:
                        endpoint_volume.SetMute(0, None)
                    else:
                        endpoint_volume.SetMute(1, None)
                    self.logger.info(f"使用pycaw设置音量到 {volume}%")
                    return
            except Exception as e:
                self.logger.warning(f"使用pycaw设置音量失败: {e}")

        # 降级：PowerShell Core Audio 脚本
        if self._run_windows_volume_script("SetVolume", volume) is not None:
            self.logger.info(f"使用Windows Core Audio脚本设置音量到 {volume}%")
            return

        # 最后降级：按键模拟（精度受系统步进影响）
        try:
            if volume == 0:
                if self._send_key(self.VK_VOLUME_MUTE):
                    return
            # 先按一次静音键确保未静音，再按音量加键
            self._send_key(self.VK_VOLUME_MUTE)
            for _ in range(max(1, int(volume / 2))):
                self._send_key(self.VK_VOLUME_UP)
            self.logger.info(f"使用按键模拟设置音量到 {volume}%")
        except Exception as e:
            self.logger.error(f"设置Windows音量失败: {e}")

    def _get_windows_volume(self) -> int:
        """
        获取Windows音量.优先使用 pycaw，失败再降级到 PowerShell.
        """
        with com_initialized():
            try:
                endpoint_volume = self._get_windows_endpoint_volume()
                if endpoint_volume:
                    return int(round(endpoint_volume.GetMasterVolumeLevelScalar() * 100))
            except Exception as e:
                self.logger.warning(f"使用pycaw获取音量失败: {e}")

        volume = self._run_windows_volume_script("GetVolume")
        if volume is not None:
            return volume

        return self.DEFAULT_VOLUME

    def _run_windows_volume_script(self, action: str, volume: int = 0) -> Optional[int]:
        """
        通过 Windows Core Audio 读取或设置默认播放设备音量.
        """
        ps_command = f"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class AudioEndpointVolume {{
    [ComImport]
    [Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    private class MMDeviceEnumerator {{}}
    private enum EDataFlow {{ eRender, eCapture, eAll }}
    private enum ERole {{ eConsole, eMultimedia, eCommunications }}
    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceEnumerator {{
        int NotImpl1();
        int GetDefaultAudioEndpoint(EDataFlow dataFlow, ERole role, out IMMDevice ppDevice);
    }}
    [Guid("D666063F-1587-4E43-81F1-B948E807363F")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDevice {{
        int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, out IAudioEndpointVolume ppInterface);
    }}
    [Guid("5CDF2C82-841E-4546-9722-0CF74078229A")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IAudioEndpointVolume {{
        int NotImpl1();
        int NotImpl2();
        int GetChannelCount(out uint channelCount);
        int SetMasterVolumeLevel(float levelDB, Guid eventContext);
        int SetMasterVolumeLevelScalar(float level, Guid eventContext);
        int GetMasterVolumeLevel(out float levelDB);
        int GetMasterVolumeLevelScalar(out float level);
        int SetChannelVolumeLevel(uint channelNumber, float levelDB, Guid eventContext);
        int SetChannelVolumeLevelScalar(uint channelNumber, float level, Guid eventContext);
        int GetChannelVolumeLevel(uint channelNumber, out float levelDB);
        int GetChannelVolumeLevelScalar(uint channelNumber, out float level);
        int SetMute(bool isMuted, Guid eventContext);
    }}
    private static IAudioEndpointVolume GetEndpointVolume() {{
        IMMDeviceEnumerator enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
        IMMDevice device;
        Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender, ERole.eMultimedia, out device));
        Guid iid = typeof(IAudioEndpointVolume).GUID;
        IAudioEndpointVolume endpointVolume;
        Marshal.ThrowExceptionForHR(device.Activate(ref iid, 23, IntPtr.Zero, out endpointVolume));
        return endpointVolume;
    }}
    public static int GetVolume() {{
        float level;
        Marshal.ThrowExceptionForHR(GetEndpointVolume().GetMasterVolumeLevelScalar(out level));
        return (int)Math.Round(level * 100);
    }}
    public static int SetVolume(int volume) {{
        volume = Math.Max(0, Math.Min(100, volume));
        IAudioEndpointVolume endpointVolume = GetEndpointVolume();
        Marshal.ThrowExceptionForHR(endpointVolume.SetMasterVolumeLevelScalar(volume / 100.0f, Guid.Empty));
        if (volume > 0) {{
            Marshal.ThrowExceptionForHR(endpointVolume.SetMute(false, Guid.Empty));
        }}
        return volume;
    }}
}}
'@
$action = "{action}"
if ($action -eq "GetVolume") {{
    [AudioEndpointVolume]::GetVolume()
}} else {{
    [AudioEndpointVolume]::SetVolume({volume})
}}
"""
        try:
            result = self._run_command(["powershell", "-NoProfile", "-Command", ps_command])
            if result and result.returncode == 0:
                return int(result.stdout.strip())
            if result:
                self.logger.warning(f"PowerShell音量脚本执行失败: {result.stderr.strip()}")
        except Exception as e:
            self.logger.warning(f"PowerShell音量脚本执行失败: {e}")
        return None

    def _get_windows_endpoint_volume(self):
        """
        获取Windows默认播放设备音量接口.
        """
        try:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            speakers = AudioUtilities.GetSpeakers()
            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return interface.QueryInterface(IAudioEndpointVolume)
        except Exception as e:
            self.logger.warning(f"获取Windows默认播放设备音量接口失败: {e}")
            return None

    def _set_macos_volume(self, volume: int):
        """
        设置macOS音量.
        """
        try:
            self._run_command(["osascript", "-e", f"set volume output volume {volume}"])
            self.logger.info(f"使用AppleScript设置音量到 {volume}%")
        except Exception as e:
            self.logger.error(f"设置macOS音量失败: {e}")

    def _get_macos_volume(self) -> int:
        """
        获取macOS音量.
        """
        try:
            result = self._run_command(["osascript", "-e", "output volume of (get volume settings)"])
            if result and result.returncode == 0:
                return int(result.stdout.strip())
        except Exception as e:
            self.logger.warning(f"获取macOS音量失败: {e}")
        return self.DEFAULT_VOLUME

    def _set_linux_volume(self, volume: int):
        """
        设置Linux音量.
        """
        try:
            # 尝试 pactl（PulseAudio）
            result = self._run_command(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"])
            if result and result.returncode == 0:
                self.logger.info(f"使用pactl设置音量到 {volume}%")
                return
        except Exception:
            pass

        try:
            # 尝试 wpctl（WirePlumber）
            result = self._run_command(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{volume / 100.0:.2f}"])
            if result and result.returncode == 0:
                self.logger.info(f"使用wpctl设置音量到 {volume}%")
                return
        except Exception:
            pass

        try:
            # 尝试 amixer（ALSA）
            result = self._run_command(["amixer", "-D", "pulse", "sset", "Master", f"{volume}%"])
            if result and result.returncode == 0:
                self.logger.info(f"使用amixer设置音量到 {volume}%")
                return
        except Exception as e:
            self.logger.error(f"设置Linux音量失败: {e}")

    def _get_linux_volume(self) -> int:
        """
        获取Linux音量.
        """
        import re

        # 尝试 pactl
        try:
            result = self._run_command(["pactl", "list", "sinks"])
            if result and result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "Volume:" in line:
                        match = re.search(r"(\d+)%", line)
                        if match:
                            return int(match.group(1))
        except Exception:
            pass

        # 尝试 wpctl
        try:
            result = self._run_command(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if result and result.returncode == 0:
                match = re.search(r"(\d+\.?\d*)", result.stdout)
                if match:
                    return int(float(match.group(1)) * 100)
        except Exception:
            pass

        # 尝试 amixer
        try:
            result = self._run_command(["amixer", "get", "Master"])
            if result and result.returncode == 0:
                match = re.search(r"\[(\d+)%\]", result.stdout)
                if match:
                    return int(match.group(1))
        except Exception:
            pass

        return self.DEFAULT_VOLUME

    def _run_command(self, cmd: list) -> Optional[subprocess.CompletedProcess]:
        """
        执行系统命令.
        """
        try:
            return run_hidden(cmd, capture_output=True, text=True)
        except Exception as e:
            self.logger.debug(f"执行命令失败 {' '.join(cmd)}: {e}")
            return None

    @staticmethod
    def check_dependencies() -> bool:
        """
        检查依赖.
        """
        system = platform.system()
        
        # Windows 使用内置的 ctypes 和 PowerShell
        if system == "Windows":
            return True
        
        # macOS 不需要额外依赖（AppleScript内置）
        if system == "Darwin":
            return True
        
        # Linux 需要检查音量控制工具
        if system == "Linux":
            import shutil
            tools = ["pactl", "wpctl", "amixer"]
            if any(shutil.which(tool) for tool in tools):
                return True
            print("警告: Linux系统未找到音量控制工具 (pactl/wpctl/amixer)")
            return False
        
        return False
