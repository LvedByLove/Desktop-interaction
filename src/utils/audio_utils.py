import asyncio
import platform
import re
from typing import Any, Dict, List, Optional

import numpy as np
import sounddevice as sd

# 可选：屏蔽常见虚拟/聚合设备（默认不选它们）
_VIRTUAL_PATTERNS = [
    r"blackhole",
    r"aggregate",
    r"multi[-\s]?output",  # macOS
    r"monitor",
    r"echo[-\s]?cancel",  # Linux Pulse/PipeWire
    r"vb[-\s]?cable",
    r"voicemeeter",
    r"cable (input|output)",  # Windows
    r"loopback",
]


def _is_virtual(name: str) -> bool:
    n = name.casefold()
    return any(re.search(pat, n) for pat in _VIRTUAL_PATTERNS)


def downmix_to_mono(
    pcm: np.ndarray | bytes,
    *,
    keepdims: bool = True,
    dtype: np.dtype | str = np.int16,
    in_channels: int | None = None,
) -> np.ndarray | bytes:
    """将任意格式的音频下混为单声道.

    支持两种输入:
    1. np.ndarray: 形状 (N,) 或 (N, C) 的 PCM 数组
    2. bytes: PCM 字节流 (需指定 dtype 和 in_channels)

    Args:
        pcm: 输入音频数据 (ndarray 或 bytes)
        keepdims: True 返回 (N,1)，False 返回 (N,) (仅 ndarray 输入)
        dtype: PCM 数据类型 (仅 bytes 输入时使用)
        in_channels: 输入声道数 (仅 bytes 输入时必需)

    Returns:
        单声道音频数据 (与输入类型相同)

    Examples:
        >>> # ndarray 输入
        >>> stereo = np.random.randint(-32768, 32767, (1000, 2), dtype=np.int16)
        >>> mono = downmix_to_mono(stereo, keepdims=False)  # shape: (1000,)

        >>> # bytes 输入
        >>> stereo_bytes = b'...'  # 立体声 PCM 数据
        >>> mono_bytes = downmix_to_mono(stereo_bytes, dtype=np.int16, in_channels=2)
    """
    # bytes 输入: 转换 -> 处理 -> 转回 bytes
    if isinstance(pcm, bytes):
        if in_channels is None:
            raise ValueError("bytes 输入必须指定 in_channels 参数")
        arr = np.frombuffer(pcm, dtype=dtype).reshape(-1, in_channels)
        mono_arr = downmix_to_mono(arr, keepdims=False)  # bytes 输出不需要 keepdims
        return mono_arr.tobytes()

    # ndarray 输入: 直接处理
    x = np.asarray(pcm)
    if x.ndim == 1:
        return x[:, None] if keepdims else x

    # 已经是单声道
    if x.shape[1] == 1:
        return x if keepdims else x[:, 0]

    # 多声道下混
    if np.issubdtype(x.dtype, np.integer):
        # 先转浮点求平均，再四舍五入回原整数类型，避免溢出
        y = np.rint(x.astype(np.float32).mean(axis=1))
        info = np.iinfo(x.dtype)
        y = np.clip(y, info.min, info.max).astype(x.dtype)
    else:
        # 浮点：保持原 dtype（比如 float32），避免默认为 float64
        y = x.mean(axis=1, dtype=x.dtype)

    return y[:, None] if keepdims else y


def safe_queue_put(
    queue: asyncio.Queue, item: Any, replace_oldest: bool = True
) -> bool:
    """安全地将项目放入队列，队列满时可选择丢弃最旧数据.

    Args:
        queue: asyncio.Queue 对象
        item: 要入队的数据
        replace_oldest: True=队列满时丢弃最旧数据并放入新数据, False=直接丢弃新数据

    Returns:
        True=成功入队, False=队列满且未入队
    """
    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        if replace_oldest:
            try:
                queue.get_nowait()  # 丢弃最旧的
                queue.put_nowait(item)  # 放入新数据
                return True
            except asyncio.QueueEmpty:
                # 理论上不会发生,但保险起见
                queue.put_nowait(item)
                return True
        return False


def upmix_mono_to_channels(mono_data: np.ndarray, num_channels: int) -> np.ndarray:
    """将单声道音频上混到多声道（复制到所有声道）

    Args:
        mono_data: 单声道音频数据，形状 (N,)
        num_channels: 目标声道数

    Returns:
        多声道音频数据，形状 (N, num_channels)
    """
    if num_channels == 1:
        return mono_data.reshape(-1, 1)

    # 复制单声道到所有声道
    return np.tile(mono_data.reshape(-1, 1), (1, num_channels))


def _valid(devs: List[dict], idx: int, kind: str, include_virtual: bool) -> bool:
    if not isinstance(idx, int) or idx < 0 or idx >= len(devs):
        return False
    d = devs[idx]
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    if int(d.get(key, 0)) <= 0:
        return False
    if not include_virtual and _is_virtual(d.get("name", "")):
        return False
    return True


def select_audio_device(
    kind: str,
    *,
    include_virtual: bool = False,
    allow_name_hints: Optional[bool] = None,  # None=Linux 才启用；True/False 可强制
) -> Optional[Dict[str, Any]]:
    """
    选择音频设备：HostAPI 默认 →（可选：设备名 hints，仅 Linux）→ sounddevice 系统默认 → 第一个可用 返回：{index, name,
    sample_rate, channels} 或 None.
    """
    assert kind in ("input", "output")
    system = platform.system().lower()

    # HostAPI 优先表
    if system == "windows":
        host_order = ["wasapi", "wdm-ks", "directsound", "mme"]
    elif system == "darwin":
        host_order = ["core audio"]
    else:
        host_order = ["alsa", "jack", "oss"]  # 多数 Linux 的 PortAudio 只有 ALSA

    # Linux 才默认启用 name hints；其它平台默认关闭（可通过参数打开）
    if allow_name_hints is None:
        allow_name_hints = system == "linux"

    DEVICE_NAME_HINTS = {
        "input": ["default", "sysdefault", "pulse", "pipewire"],
        "output": ["default", "sysdefault", "dmix", "pulse", "pipewire"],
    }

    # 枚举
    try:
        hostapis = list(sd.query_hostapis())
        devices = list(sd.query_devices())
    except Exception:
        hostapis, devices = [], []

    key_host_default = (
        "default_input_device" if kind == "input" else "default_output_device"
    )
    key_channels = "max_input_channels" if kind == "input" else "max_output_channels"

    def pack(idx: int, base: Optional[dict] = None) -> Optional[Dict[str, Any]]:
        if base is None:
            if not _valid(devices, idx, kind, include_virtual):
                return None
            d = devices[idx]
        else:
            d = base
            if not include_virtual and _is_virtual(d.get("name", "")):
                return None
        sr = d.get("default_samplerate", None)
        return {
            "index": int(d.get("index", idx)),
            "name": d.get("name", "Unknown"),
            "sample_rate": int(sr) if isinstance(sr, (int, float)) else None,
            "channels": int(d.get(key_channels, 0)),
        }

    # 1) 按 HostAPI 名称匹配（包含、忽略大小写）→ 取该 HostAPI 的“默认设备”
    for token in host_order:
        t = token.casefold()
        for ha in hostapis:
            if t in str(ha.get("name", "")).casefold():
                idx = ha.get(key_host_default, -1)
                info = pack(idx)
                if info:
                    return info

    # 1.5) （可选）设备名 hints，仅当 allow_name_hints=True 时启用（默认 Linux）
    if allow_name_hints and devices:
        hints = [h.casefold() for h in DEVICE_NAME_HINTS[kind]]
        cands: List[int] = []
        for i, d in enumerate(devices):
            if not _valid(devices, i, kind, include_virtual):
                continue
            name_low = str(d.get("name", "")).casefold()
            if any(h in name_low for h in hints):
                cands.append(i)
        if cands:
            cands.sort()  # 稳定：索引小优先
            info = pack(cands[0])
            if info:
                return info

    # 2) sounddevice 的系统默认（已考虑平台默认路由）
    try:
        info = sd.query_devices(
            kind=kind
        )  # dict，含 index / default_samplerate / max_*_channels
        packed = pack(int(info.get("index")), base=info)
        if packed:
            return packed
    except Exception:
        pass

    # 3) 兜底：第一个可用（且非虚拟，除非允许）
    for i, d in enumerate(devices):
        if _valid(devices, i, kind, include_virtual):
            return pack(i)

    return None


# 惰性缓存：Windows Core Audio（COM）所需的 ctypes 类型与原型，仅创建一次。
# 之前这些定义写在 get_windows_default_output_name 内部，而该函数被
# _monitor_output_device 每 1.5s 轮询调用一次，导致每次调用都新建一批
# ctypes.Structure 类与 WINFUNCTYPE 原型并被 ctypes 内部缓存持有、无法回收，
# 长时间运行内存持续增长（泄漏）。提升到模块级并缓存后，仅定义一次。
_win_com = None


def _get_win_com():
    global _win_com
    if _win_com is not None:
        return _win_com

    import ctypes
    from ctypes import POINTER, byref, cast, c_void_p, c_ulong, c_uint32

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", c_uint32)]

    class _PROPVARIANT_UNION(ctypes.Union):
        _fields_ = [
            ("pwszVal", ctypes.c_wchar_p),
            ("bytes", ctypes.c_ubyte * 8),
        ]

    class PROPVARIANT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [
            ("vt", ctypes.c_uint16),
            ("wReserved1", ctypes.c_uint16),
            ("wReserved2", ctypes.c_uint16),
            ("wReserved3", ctypes.c_uint16),
            ("u", _PROPVARIANT_UNION),
        ]

    def _guid(d1, d2, d3, d4):
        g = GUID()
        g.Data1 = d1
        g.Data2 = d2
        g.Data3 = d3
        g.Data4 = (ctypes.c_ubyte * 8)(*d4)
        return g

    CLSID_MMDeviceEnumerator = _guid(
        0xBCDE0395, 0xE52F, 0x467C, (0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E)
    )
    IID_IMMDeviceEnumerator = _guid(
        0xA95664D2, 0x9614, 0x4F35, (0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6)
    )
    # PKEY_Device_FriendlyName
    PKEY_FRIENDLY_NAME = PROPERTYKEY()
    PKEY_FRIENDLY_NAME.fmtid = _guid(
        0xA45C254E, 0xDF1C, 0x4EFD, (0x80, 0x20, 0x67, 0xD1, 0x46, 0xA8, 0x50, 0xE0)
    )
    PKEY_FRIENDLY_NAME.pid = 14

    CLSCTX_ALL = 23
    VT_LPWSTR = 31

    # 使用私有 ole32 实例，避免修改 ctypes.windll 共享单例的 argtypes
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoCreateInstance.argtypes = [
        POINTER(GUID), c_void_p, c_uint32, POINTER(GUID), POINTER(c_void_p)
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoInitializeEx.argtypes = [c_void_p, c_uint32]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.PropVariantClear.argtypes = [POINTER(PROPVARIANT)]
    ole32.PropVariantClear.restype = ctypes.c_long

    def _call_vtbl(ptr, index, proto):
        vtbl = cast(ptr, POINTER(POINTER(c_void_p))).contents
        return proto(vtbl[index])

    proto_release = ctypes.WINFUNCTYPE(c_ulong, c_void_p)
    proto_enum = ctypes.WINFUNCTYPE(
        ctypes.c_long, c_void_p, ctypes.c_int, ctypes.c_int, POINTER(c_void_p)
    )
    proto_open = ctypes.WINFUNCTYPE(
        ctypes.c_long, c_void_p, c_uint32, POINTER(c_void_p)
    )
    proto_get = ctypes.WINFUNCTYPE(
        ctypes.c_long, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT)
    )

    def _release(ptr):
        if ptr and ptr.value:
            try:
                _call_vtbl(ptr, 2, proto_release)(ptr)
            except Exception:
                pass

    _win_com = {
        "ole32": ole32,
        "byref": byref,
        "c_void_p": c_void_p,
        "PROPVARIANT": PROPVARIANT,
        "CLSID_MMDeviceEnumerator": CLSID_MMDeviceEnumerator,
        "IID_IMMDeviceEnumerator": IID_IMMDeviceEnumerator,
        "PKEY_FRIENDLY_NAME": PKEY_FRIENDLY_NAME,
        "CLSCTX_ALL": CLSCTX_ALL,
        "VT_LPWSTR": VT_LPWSTR,
        "proto_enum": proto_enum,
        "proto_open": proto_open,
        "proto_get": proto_get,
        "_call_vtbl": _call_vtbl,
        "_release": _release,
    }
    return _win_com


def get_windows_default_output_name() -> Optional[str]:
    """实时查询 Windows 当前默认输出（渲染）设备的友好名称.

    PortAudio 在初始化时会缓存默认设备索引，Windows 运行时切换默认输出设备
    （例如插入耳机后系统自动切换）后，sd.query_devices(kind='output') 不会更新。
    此函数通过 Core Audio 的 IMMDeviceEnumerator::GetDefaultAudioEndpoint
    直接向系统读取当前默认渲染端点，用于检测运行时切换并跟随路由。
    """
    if platform.system().lower() != "windows":
        return None

    com = _get_win_com()
    byref = com["byref"]
    c_void_p = com["c_void_p"]
    ole32 = com["ole32"]

    # COM 需在线程上初始化（MMDevice API 在任意 apartment 下均可用）。
    # 只初始化一次并保持（不反初始化），避免反复创建/销毁 COM 套间。
    try:
        ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    except Exception:
        pass

    name = None
    p_device = c_void_p()
    p_store = c_void_p()
    device = c_void_p()
    try:
        hr = ole32.CoCreateInstance(
            byref(com["CLSID_MMDeviceEnumerator"]), None, com["CLSCTX_ALL"],
            byref(com["IID_IMMDeviceEnumerator"]), byref(p_device),
        )
        if hr != 0 or not p_device.value:
            return None

        # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender=0, eConsole=0)
        hr = com["_call_vtbl"](p_device, 4, com["proto_enum"])(
            p_device, 0, 0, byref(device)
        )
        if hr != 0 or not device.value:
            return None

        # IMMDevice::OpenPropertyStore(STGM_READ=0, &IPropertyStore)
        hr = com["_call_vtbl"](device, 4, com["proto_open"])(device, 0, byref(p_store))
        if hr != 0 or not p_store.value:
            return None

        # IPropertyStore::GetValue(&key, &PROPVARIANT)
        pv = com["PROPVARIANT"]()
        hr = com["_call_vtbl"](p_store, 5, com["proto_get"])(
            p_store, byref(com["PKEY_FRIENDLY_NAME"]), byref(pv)
        )
        if hr == 0 and pv.vt == com["VT_LPWSTR"] and pv.pwszVal:
            name = pv.pwszVal
        # 释放 PROPVARIANT 持有的字符串（CoTaskMemAlloc 分配），避免每次轮询泄漏
        try:
            ole32.PropVariantClear(byref(pv))
        except Exception:
            pass
    except Exception:
        name = None
    finally:
        com["_release"](p_store)
        com["_release"](device)
        com["_release"](p_device)

    # 注意：这里故意不调用 CoUninitialize()。
    # 该函数被 _monitor_output_device 每 1.5s 轮询一次，且始终运行在同一个
    # 事件循环线程上。若每次 CoInitializeEx(S_OK) 后立即 CoUninitialize，会在
    # 该线程上反复创建/销毁 COM 套间（apartment），长时间运行可能加剧卡顿；
    # 保持 COM 在该线程上持续初始化（后续调用返回 S_FALSE）即可消除此抖动。
    return name
