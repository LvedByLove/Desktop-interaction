# -*- coding: utf-8 -*-
"""
OLE 拖放捕捉器 —— 只用 Win32 原生拖放协议精准识别"文件/文件夹"拖拽。

背景：pynput 只能感知鼠标动作，无法区分"拖文件"、"拖动选中文字"与"桌面空拖"。
Windows 原生 OLE 拖放（DoDragDrop）会向注册的 drop target 发送 DragEnter，
其 IDataObject 对文件拖拽必带 CF_HDROP 格式，可据此 100% 精确区分。

设计（PoC 已验证）：
  - 独立线程 + Win32 消息循环，与 Qt 主线程隔离。
  - 一个不可见的窄带窗口（主屏顶部居中 800x200，TOPMOST/NOACTIVATE/Layerd alpha=1）。
  - 平时 SW_HIDE：隐藏窗口不参与 OLE 命中测试，不挡任何点击。
  - pynput 检测到"疑似拖拽开始"→ 显示窗口；拖拽期间鼠标被拖放源捕获，
    窗口挡不到任何点击，误显示零代价。
  - DragEnter 时 QueryGetData(CF_HDROP)：
      * 命中 → 发射 fileDragEntered 唤醒中转站界面，并保持窗口可见，
        由 Drop 回调原生解析 CF_HDROP 取出路径（不依赖 Qt 窗口接收拖放）；
      * 未命中 → 隐藏自身并返回 DROPEFFECT_NONE，把非文件拖拽放行给下面的窗口。
  - 松开左键（pynput）→ 隐藏窗口。

为什么 Drop 要原生处理：
  拖放最终落在"光标下最上层的窗口"上。若依赖 QML DropArea，则会受 Qt 窗口
  置顶顺序、模态对话框（Qt 会禁用应用内其它窗口，拖放直接返回 NONE）等影响。
  本窗口是独立线程里用原生 API 创建的 TOPMOST 窗口，不在 Qt 的窗口管理里，
  因此始终能收到拖放，是最稳定的一层兜底。

注意：窗口不能带 WS_EX_TRANSPARENT —— 穿透窗口会被 OLE 命中测试整体跳过（PoC 实测），
因此采用"仅拖拽期间显示"的方式规避挡点击问题。
"""

import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ---------- 常量 ----------
CF_HDROP = 15
DVASPECT_CONTENT = 1
TYMED_HGLOBAL = 1
TYMED_ISTREAM = 4
DROPEFFECT_NONE = 0
DROPEFFECT_COPY = 1

S_OK = 0
S_FALSE = 1
E_NOINTERFACE = -2147467262  # 0x80004002

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x00000002

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

WM_APP = 0x8000
WM_APP_SHOW = WM_APP + 1
WM_APP_HIDE = WM_APP + 2
WM_APP_QUIT = WM_APP + 3


# ---------- 结构体 ----------
class MSG(ctypes.Structure):
    # 注意：wParam/lParam 在 64 位下为 8 字节，不能用 wintypes.WPARAM（32 位）
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", wintypes.UINT),
        ("wParam", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
        ("time", wintypes.DWORD),
        ("pt", (ctypes.c_long * 2)),
    ]


class POINTL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class FORMATETC(ctypes.Structure):
    _fields_ = [
        ("cfFormat", ctypes.c_ushort),
        ("ptd", ctypes.c_void_p),
        ("dwAspect", wintypes.DWORD),
        ("lindex", ctypes.c_long),
        ("tymed", wintypes.DWORD),
    ]


class STGMEDIUM(ctypes.Structure):
    """IDataObject::GetData 的返回介质；CF_HDROP 使用 TYMED_HGLOBAL。

    DWORD 后紧跟指针，ctypes 会自动补齐 64 位对齐所需的填充。
    """

    _fields_ = [
        ("tymed", wintypes.DWORD),
        ("hGlobal", ctypes.c_void_p),
        ("pUnkForRelease", ctypes.c_void_p),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class IDropTargetVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", ctypes.c_void_p),
        ("AddRef", ctypes.c_void_p),
        ("Release", ctypes.c_void_p),
        ("DragEnter", ctypes.c_void_p),
        ("DragOver", ctypes.c_void_p),
        ("DragLeave", ctypes.c_void_p),
        ("Drop", ctypes.c_void_p),
    ]


class DropTarget(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(IDropTargetVtbl)), ("ref", ctypes.c_ulong)]


# ---------- 函数指针类型（stdcall） ----------
QI_FN = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
ADDREF_FN = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
RELEASE_FN = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
DRAGENTER_FN = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, POINTL,
    ctypes.POINTER(wintypes.DWORD),
)
DRAGOVER_FN = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, wintypes.DWORD, POINTL, ctypes.POINTER(wintypes.DWORD),
)
DRAGLEAVE_FN = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
DROP_FN = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, POINTL,
    ctypes.POINTER(wintypes.DWORD),
)
QUERYGETDATA_FN = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(FORMATETC))
GETDATA_FN = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.POINTER(FORMATETC),
    ctypes.POINTER(STGMEDIUM),
)
WNDPROC_FN = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p
)


# ---------- 私有 DLL 实例 ----------
# 签名只设置在这几个私有实例上，绝不修改 ctypes.windll 共享单例：
# pynput 等第三方库的消息泵依赖 ctypes.windll.user32/kernel32 的默认行为，
# 篡改其 argtypes（如 GetMessageW 的 MSG 结构）会导致它们的监听线程崩溃
# （曾导致全局快捷键整体失效）。
_user32 = ctypes.WinDLL("user32")
_kernel32 = ctypes.WinDLL("kernel32")
_ole32 = ctypes.WinDLL("ole32")

# CF_HDROP 解析要在任意调用时机都安全：默认 restype 为 c_int 会把 64 位
# HGLOBAL/内存指针截断，必须在模块加载时就声明。只作用于上面的私有实例。
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_ole32.ReleaseStgMedium.argtypes = [ctypes.POINTER(STGMEDIUM)]


# ---------- Win32 函数签名（避免 64 位句柄截断） ----------
def _declare_win32_signatures():
    user32 = _user32
    kernel32 = _kernel32
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetLastError.restype = wintypes.DWORD
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [
        ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
    ]
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetLayeredWindowAttributes.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD,
    ]
    user32.PostMessageW.argtypes = [
        ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
    ]
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
    ]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]


# 注意：签名声明不在此处执行，延后到 _run() 线程内调用；
# 且只作用于上面的私有 DLL 实例（_user32/_kernel32），
# 不影响 pynput 等第三方库依赖的 ctypes.windll 共享单例。
# _declare_win32_signatures()


# ---------- COM 回调 ----------
# ctypes 回调必须是模块级函数，通过该全局引用访问活跃的捕捉器实例
_ACTIVE_CATCHER = None


@QI_FN
def _qi(this, riid, ppv):
    return E_NOINTERFACE


@ADDREF_FN
def _addref(this):
    return 1


@RELEASE_FN
def _release(this):
    return 1


def _pdataobj_has_format(pdataobj, cf, tymed):
    """通过 IDataObject::QueryGetData（vtbl 索引 5）判断是否提供某剪贴板格式。"""
    if not pdataobj:
        return False
    vtbl = ctypes.cast(pdataobj, ctypes.POINTER(ctypes.c_void_p))[0]
    qgd = QUERYGETDATA_FN(ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[5])
    fmt = FORMATETC()
    fmt.cfFormat = cf
    fmt.ptd = None
    fmt.dwAspect = DVASPECT_CONTENT
    fmt.lindex = -1
    fmt.tymed = tymed
    return qgd(pdataobj, ctypes.byref(fmt)) == S_OK


def _read_string_list(addr, wide):
    """读取 DROPFILES 之后以双 NUL 结尾的路径串列表。"""
    paths = []
    offset = 0
    limit = 1 << 20  # 防御性上限：1MB
    while offset < limit:
        if wide:
            s = ctypes.wstring_at(addr + offset)
            if not s:
                break
            paths.append(s)
            offset += (len(s) + 1) * 2
        else:
            raw = ctypes.string_at(addr + offset)
            if not raw:
                break
            paths.append(raw.decode("mbcs", "replace"))
            offset += len(raw) + 1
    return paths


def _parse_dropfiles(hglobal):
    """解析 HGLOBAL 中的 DROPFILES 结构，返回文件/文件夹路径列表。

    DROPFILES: pFiles(DWORD,偏移) + pt(POINT) + fNC(BOOL) + fWide(BOOL)，
    fWide 位于偏移 16；路径串从 pFiles 偏移处开始。
    """
    handle = ctypes.c_void_p(hglobal)
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        return []
    try:
        p_files = ctypes.c_uint32.from_address(ptr).value
        f_wide = ctypes.c_int.from_address(ptr + 16).value
        return _read_string_list(ptr + p_files, bool(f_wide))
    except Exception as e:
        logger.warning(f"解析 CF_HDROP 数据失败: {e}")
        return []
    finally:
        _kernel32.GlobalUnlock(handle)


def _get_hdrop_paths(pdataobj):
    """通过 IDataObject::GetData（vtbl 索引 3）取出 CF_HDROP 路径列表。"""
    if not pdataobj:
        return []
    try:
        vtbl = ctypes.cast(pdataobj, ctypes.POINTER(ctypes.c_void_p))[0]
        get_data = GETDATA_FN(ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[3])
        fmt = FORMATETC()
        fmt.cfFormat = CF_HDROP
        fmt.ptd = None
        fmt.dwAspect = DVASPECT_CONTENT
        fmt.lindex = -1
        fmt.tymed = TYMED_HGLOBAL
        medium = STGMEDIUM()
        hr = get_data(pdataobj, ctypes.byref(fmt), ctypes.byref(medium))
        if hr != S_OK or not medium.hGlobal:
            return []
        try:
            return _parse_dropfiles(medium.hGlobal)
        finally:
            try:
                _ole32.ReleaseStgMedium(ctypes.byref(medium))
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"读取拖放文件列表失败: {e}")
        return []


@DRAGENTER_FN
def _drag_enter(this, pdataobj, grf_key_state, pt, pdw_effect):
    catcher = _ACTIVE_CATCHER
    is_file = (
        _pdataobj_has_format(pdataobj, CF_HDROP, TYMED_HGLOBAL)
        or _pdataobj_has_format(pdataobj, CF_HDROP, TYMED_ISTREAM)
    )
    if is_file and catcher is not None:
        logger.info(f"OLE 检测到文件拖拽进入目标区 位置=({pt.x},{pt.y})")
        # 不隐藏：保持窗口在最上层，由本窗口的 Drop 原生接收文件。
        # 这样即使中转站窗口被模态对话框禁用、或被其它置顶窗口压住，
        # 拖放也仍然落在本窗口上，不会被 Qt 拒绝。
        catcher._drop_enabled = True
        # 跨线程发射：Qt 自动队列投递到主线程槽
        catcher.fileDragEntered.emit()
        pdw_effect[0] = DROPEFFECT_COPY
        return S_OK
    # 非文件拖拽：隐藏自身，把拖拽放行给下面的窗口
    if catcher is not None:
        catcher._drop_enabled = False
        _user32.ShowWindow(catcher._hwnd, SW_HIDE)
    pdw_effect[0] = DROPEFFECT_NONE
    return S_FALSE


@DRAGOVER_FN
def _drag_over(this, grf_key_state, pt, pdw_effect):
    catcher = _ACTIVE_CATCHER
    if catcher is not None and catcher._drop_enabled and not catcher._suppress_drops:
        pdw_effect[0] = DROPEFFECT_COPY
    else:
        pdw_effect[0] = DROPEFFECT_NONE
    return S_OK


@DRAGLEAVE_FN
def _drag_leave(this):
    catcher = _ACTIVE_CATCHER
    if catcher is not None:
        catcher._drop_enabled = False
    return S_OK


@DROP_FN
def _drop(this, pdataobj, grf_key_state, pt, pdw_effect):
    logger.info("OLE Drop 回调触发")
    catcher = _ACTIVE_CATCHER
    if catcher is None or catcher._suppress_drops:
        # 拖出中转站时自己发起的拖拽（或异常状态）：不接管，避免文件被加回
        logger.debug(f"OLE Drop 被抑制 (catcher={catcher is not None}, suppress={getattr(catcher, '_suppress_drops', None)})")
        pdw_effect[0] = DROPEFFECT_NONE
        return S_OK

    catcher._drop_enabled = False
    paths = _get_hdrop_paths(pdataobj)
    if not paths:
        logger.info("OLE Drop 未解析到文件路径，忽略本次拖放")
        pdw_effect[0] = DROPEFFECT_NONE
        return S_OK

    logger.info(f"OLE 原生接收 {len(paths)} 个文件/文件夹")
    # 跨线程发射：主线程加入中转站模型并展开面板
    catcher.filesDropped.emit(paths)
    # 本次拖拽已结束，立即隐藏，避免继续遮挡下面的窗口
    _user32.ShowWindow(catcher._hwnd, SW_HIDE)
    pdw_effect[0] = DROPEFFECT_COPY
    return S_OK


@WNDPROC_FN
def _wnd_proc(hwnd, msg, wparam, lparam):
    catcher = _ACTIVE_CATCHER
    user32 = _user32
    if msg == WM_APP_SHOW:
        # 提到 TOPMOST 顶层并显示（窗口平时隐藏，仅拖拽期间显示）
        user32.SetWindowPos(
            hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        return 0
    if msg == WM_APP_HIDE:
        user32.ShowWindow(hwnd, SW_HIDE)
        return 0
    if msg == WM_APP_QUIT:
        # 通知消息循环退出；窗口销毁留在循环后（RevokeDragDrop 需要窗口存活）
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _fn_addr(fn):
    return ctypes.cast(fn, ctypes.c_void_p).value


class OleDropCatcher(QObject):
    """OLE 文件拖拽捕捉器：仅在文件拖拽经过顶部目标区时通知主界面。"""

    # 目标区：主屏顶部居中（与拖拽唤醒胶囊出现位置一致）
    ZONE_WIDTH = 800
    ZONE_HEIGHT = 100

    # 文件拖拽进入目标区（在后台线程发射，Qt 队列投递到主线程）
    fileDragEntered = Signal()
    # 原生 Drop 收到文件路径列表（在后台线程发射，Qt 队列投递到主线程）
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._ready = threading.Event()
        self._hwnd = 0
        # 当前拖拽是否携带文件（DragEnter 判定，DragOver/Drop 依据它决定是否接管）
        self._drop_enabled = False
        # 拖出中转站时自己发起的拖拽：忽略落在捕捉带上的 Drop
        self._suppress_drops = False
        # 保持 ctypes 回调与 COM 对象的引用，防止被 GC 回收导致崩溃
        self._keepalive = []

    # ------------------------------------------------------------------
    # 公共 API（可从任意线程调用）
    # ------------------------------------------------------------------

    def start(self):
        """启动后台线程：创建隐形窗口并注册 drop target。"""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="ole-drop-catcher", daemon=True
        )
        self._thread.start()

    def show(self):
        """显示隐形捕捉窗口（拖拽期间调用）。"""
        if not self._ready.wait(2.0):
            logger.debug("OLE 捕捉窗口未就绪，跳过显示")
            return
        try:
            _user32.PostMessageW(self._hwnd, WM_APP_SHOW, 0, 0)
        except Exception as e:
            logger.debug(f"显示 OLE 捕捉窗口失败: {e}")

    def hide(self):
        """隐藏隐形捕捉窗口。"""
        if not self._thread or not self._thread.is_alive():
            return
        if not self._ready.wait(2.0):
            return
        try:
            _user32.PostMessageW(self._hwnd, WM_APP_HIDE, 0, 0)
        except Exception as e:
            logger.debug(f"隐藏 OLE 捕捉窗口失败: {e}")

    def raise_above(self):
        """把捕捉窗口重新提到置顶层。

        中转站窗口 show()/raise_() 后会压到捕捉窗口上面；此时若指针正落在
        薄条/面板区域，拖放会重新交给 Qt 窗口处理（模态时会被拒绝）。
        唤醒中转站后再调用一次，确保整条捕捉带始终由原生窗口接管。
        """
        self.show()

    def set_suppress_drops(self, suppress: bool):
        """拖出中转站期间抑制原生 Drop，避免文件落到顶部带里被又加回中转站。"""
        self._suppress_drops = bool(suppress)

    def stop(self):
        """退出后台线程（daemon 线程，join 超时后随进程结束，不会阻塞关闭）。"""
        if not self._thread or not self._thread.is_alive():
            return
        if self._ready.wait(2.0):
            try:
                _user32.PostMessageW(self._hwnd, WM_APP_QUIT, 0, 0)
            except Exception:
                pass
        self._thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # 后台线程
    # ------------------------------------------------------------------

    def _run(self):
        global _ACTIVE_CATCHER
        _ACTIVE_CATCHER = self
        # 线程内声明 Win32 签名（只作用于私有 DLL 实例）
        _declare_win32_signatures()
        user32 = _user32
        kernel32 = _kernel32
        ole32 = _ole32

        hr = ole32.OleInitialize(None)
        if hr not in (S_OK, S_FALSE):
            logger.error(f"OLE 捕捉器初始化失败 0x{ctypes.c_ulong(hr).value:08X}")
            self._ready.set()
            return

        try:
            # 注册窗口类
            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = _fn_addr(_wnd_proc)
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.lpszClassName = "XiaoZhiOleDropCatcher"
            if not user32.RegisterClassW(ctypes.byref(wc)):
                err = kernel32.GetLastError()
                if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                    logger.error(f"注册捕捉窗口类失败，错误码 {err}")
                    return

            # 目标区：主屏顶部居中（主屏原点即虚拟屏幕原点 (0,0)）
            screen_w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            zone_x = (screen_w - self.ZONE_WIDTH) // 2
            zone_y = 0

            ex_style = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            hwnd = user32.CreateWindowExW(
                ex_style, "XiaoZhiOleDropCatcher", "", WS_POPUP,
                zone_x, zone_y, self.ZONE_WIDTH, self.ZONE_HEIGHT,
                0, 0, wc.hInstance, None,
            )
            if not hwnd:
                logger.error("创建 OLE 捕捉窗口失败")
                return

            # 全透明但可命中：alpha 用 1 而非 0（Win8+ 对 alpha=0 可能跳过命中测试）
            user32.SetLayeredWindowAttributes(hwnd, 0, 1, LWA_ALPHA)

            # 构建并注册 drop target（保持引用防止被 GC）
            vtbl = IDropTargetVtbl()
            vtbl.QueryInterface = _fn_addr(_qi)
            vtbl.AddRef = _fn_addr(_addref)
            vtbl.Release = _fn_addr(_release)
            vtbl.DragEnter = _fn_addr(_drag_enter)
            vtbl.DragOver = _fn_addr(_drag_over)
            vtbl.DragLeave = _fn_addr(_drag_leave)
            vtbl.Drop = _fn_addr(_drop)
            drop_target = DropTarget()
            drop_target.lpVtbl = ctypes.pointer(vtbl)
            drop_target.ref = 1
            self._keepalive = [vtbl, drop_target]

            hrr = ole32.RegisterDragDrop(
                hwnd, ctypes.cast(ctypes.pointer(drop_target), ctypes.c_void_p)
            )
            if hrr != S_OK:
                logger.error(f"RegisterDragDrop 失败 0x{ctypes.c_ulong(hrr).value:08X}")
                user32.DestroyWindow(hwnd)
                return

            self._hwnd = hwnd
            self._ready.set()
            logger.debug("OLE 文件拖拽捕捉器已就绪（窗口保持隐藏）")

            # 消息循环
            msg = MSG()
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
                if ret <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            ole32.RevokeDragDrop(hwnd)
            user32.DestroyWindow(hwnd)
        finally:
            _ACTIVE_CATCHER = None
            self._ready.set()
            ole32.OleUninitialize()
            logger.debug("OLE 文件拖拽捕捉器已退出")