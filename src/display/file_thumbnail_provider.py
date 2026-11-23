# -*- coding: utf-8 -*-
"""Windows Shell 内容缩略图 / 文件图标供给器.

复刻岛仔（xiaotang-daozai, MIT）的置物架取图链路，优先级从高到低：

1. 后台线程调用 ``IShellItemImageFactory::GetImage`` 取 96px **内容缩略图**
   （图片显示照片本身、视频显示首帧、PDF/Office 显示首页预览）；
   文件夹强制 ``SIIGBF_ICONONLY``，只要文件夹图标不要内容预览。
2. 失败回退 Qt ``QFileIconProvider`` 的系统图标。
3. 再失败（含文件已被移动/删除）绘制内置矢量占位图标。

与旧实现的区别：
- Shell 查询与 GDI 转换全部在 ``QThreadPool`` 后台线程执行，不阻塞 GUI；
- 线程内按 COM STA 初始化（``CoInitializeEx(COINIT_APARTMENTTHREADED)``）；
- 结果在 GUI 线程转成 QPixmap、落盘 PNG，再以 ``file:///`` URL 交给 QML；
- 额外提供 ``metadata``（文件夹 / 大小 / 已移动），供面板在文件名下方显示；
- 缓存带 LRU 上限，异步完成时通过 ``updated`` 信号驱动模型刷新。

非 Windows 平台自动退化为方案 2/3。
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from collections import OrderedDict

from PySide6.QtCore import (
    QFileInfo,
    QObject,
    QRunnable,
    QRectF,
    QThreadPool,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFileIconProvider

# ---------------------------------------------------------------------------
# Windows Shell / GDI interop
# ---------------------------------------------------------------------------

_SHELL_SUPPORTED = sys.platform == "win32"

if _SHELL_SUPPORTED:
    import ctypes
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _SIZE(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

    class _BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", wintypes.LONG),
            ("bmWidth", wintypes.LONG),
            ("bmHeight", wintypes.LONG),
            ("bmWidthBytes", wintypes.LONG),
            ("bmPlanes", wintypes.WORD),
            ("bmBitsPixel", wintypes.WORD),
            ("bmBits", ctypes.c_void_p),
        ]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITMAPINFOHEADER)]

    _IID_ISHELLITEMIMAGEFACTORY = _GUID(
        0xBCC18B79,
        0xBA16,
        0x442F,
        (ctypes.c_ubyte * 8)(0x80, 0xC4, 0x8A, 0x59, 0xC3, 0x0C, 0x46, 0x3B),
    )

    _SIIGBF_RESIZETOFIT = 0x00
    _SIIGBF_BIGGERSIZEOK = 0x01
    _SIIGBF_ICONONLY = 0x04
    _BI_RGB = 0
    _DIB_RGB_COLORS = 0
    _COINIT_APARTMENTTHREADED = 0x2

    _ole32 = ctypes.WinDLL("ole32")
    _shell32 = ctypes.WinDLL("shell32")
    _gdi32 = ctypes.WinDLL("gdi32")
    _user32 = ctypes.WinDLL("user32")

    _shell32.SHCreateItemFromParsingName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _shell32.SHCreateItemFromParsingName.restype = ctypes.c_long

    _GetImageProto = ctypes.WINFUNCTYPE(
        ctypes.c_long,
        ctypes.c_void_p,
        _SIZE,
        ctypes.c_int,
        ctypes.POINTER(wintypes.HBITMAP),
    )
    _ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)

    class _IShellItemImageFactoryVtbl(ctypes.Structure):
        _fields_ = [
            ("QueryInterface", ctypes.c_void_p),
            ("AddRef", ctypes.c_void_p),
            ("Release", _ReleaseProto),
            ("GetImage", _GetImageProto),
        ]

    class _IShellItemImageFactory(ctypes.Structure):
        _fields_ = [("lpVtbl", ctypes.POINTER(_IShellItemImageFactoryVtbl))]

    _gdi32.GetObjectW.argtypes = [wintypes.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
    _gdi32.GetObjectW.restype = ctypes.c_int
    _gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(_BITMAPINFO),
        wintypes.UINT,
    ]
    _gdi32.GetDIBits.restype = ctypes.c_int
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _gdi32.DeleteObject.restype = wintypes.BOOL
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.GetDC.restype = wintypes.HDC
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]


def _load_shell_image(path: str, size: int, is_dir: bool):
    """后台线程：取 Shell 内容缩略图/图标，返回 (rgba_bytes, width, height)。

    失败返回 None。RGBA 为直通字节序（R,G,B,A），供 QImage 的
    ``Format_RGBA8888_Premultiplied`` 使用。
    """
    if not _SHELL_SUPPORTED:
        return None

    com_initialized = False
    try:
        hr = _ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        if hr in (0, 1):  # S_OK / S_FALSE
            com_initialized = hr == 0
        # 其它 HRESULT（如 RPC_E_CHANGED_MODE）继续尝试，Shell 通常仍可用。
    except Exception:
        pass

    hbmp = wintypes.HBITMAP()
    factory_ptr = ctypes.c_void_p()
    try:
        hr = _shell32.SHCreateItemFromParsingName(
            path,
            None,
            ctypes.byref(_IID_ISHELLITEMIMAGEFACTORY),
            ctypes.byref(factory_ptr),
        )
        if hr < 0 or not factory_ptr:
            return None

        factory = ctypes.cast(
            factory_ptr, ctypes.POINTER(_IShellItemImageFactory)
        )
        flags = (
            _SIIGBF_ICONONLY
            if is_dir
            else (_SIIGBF_RESIZETOFIT | _SIIGBF_BIGGERSIZEOK)
        )
        hr = factory.contents.lpVtbl.contents.GetImage(
            factory_ptr, _SIZE(size, size), flags, ctypes.byref(hbmp)
        )
        factory.contents.lpVtbl.contents.Release(factory_ptr)
        factory_ptr = ctypes.c_void_p()
        if hr < 0 or not hbmp:
            return None
    except Exception:
        return None
    finally:
        if factory_ptr:
            try:
                factory = ctypes.cast(
                    factory_ptr, ctypes.POINTER(_IShellItemImageFactory)
                )
                factory.contents.lpVtbl.contents.Release(factory_ptr)
            except Exception:
                pass
        if com_initialized:
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass

    try:
        bitmap = _BITMAP()
        got = _gdi32.GetObjectW(
            ctypes.c_void_p(hbmp.value),
            ctypes.sizeof(_BITMAP),
            ctypes.byref(bitmap),
        )
        width, height = bitmap.bmWidth, abs(bitmap.bmHeight)
        if not got or width <= 0 or height <= 0 or width > 1024 or height > 1024:
            return None

        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = _BI_RGB

        buffer = (ctypes.c_ubyte * (width * height * 4))()
        dc = _user32.GetDC(None)
        try:
            lines = _gdi32.GetDIBits(
                dc,
                hbmp,
                0,
                height,
                buffer,
                ctypes.byref(info),
                _DIB_RGB_COLORS,
            )
        finally:
            _user32.ReleaseDC(None, dc)
        if not lines:
            return None

        # GDI 是 BGRA；QImage 的 RGBA8888 需要 RGBA。
        pixels = bytearray(buffer)
        pixels[0::4], pixels[2::4] = pixels[2::4], pixels[0::4]
        # JPEG 之类缩略图 alpha 全为 0，视为完全不透明（与岛仔一致）。
        if all(alpha == 0 for alpha in pixels[3::4]):
            pixels[3::4] = b"\xff" * (width * height)
        return bytes(pixels), width, height
    except Exception:
        return None
    finally:
        try:
            _gdi32.DeleteObject(ctypes.c_void_p(hbmp.value))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 元信息 / 回退图标
# ---------------------------------------------------------------------------

_IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "ico", "tif", "tiff", "heic", "avif"}
_VIDEO_EXT = {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "ts"}
_AUDIO_EXT = {"mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "ape"}
_DOC_EXT = {"pdf", "txt", "md", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "rtf"}
_ARCHIVE_EXT = {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso"}
_CODE_EXT = {"py", "js", "ts", "rs", "c", "h", "cpp", "hpp", "java", "go", "json", "yaml", "yml", "toml", "html", "css", "sh", "bat", "ps1"}

_EXT_COLORS = {
    "image": QColor("#2563eb"),
    "video": QColor("#7c3aed"),
    "audio": QColor("#059669"),
    "doc": QColor("#d97706"),
    "archive": QColor("#b45309"),
    "code": QColor("#0891b2"),
    "exe": QColor("#475569"),
    "file": QColor("#64748b"),
    "missing": QColor("#9ca3af"),
}


def _human_size(num_bytes: int) -> str:
    kb = 1024.0
    value = float(num_bytes)
    if value >= kb * kb * kb:
        return f"{value / (kb * kb * kb):.1f} GB"
    if value >= kb * kb:
        return f"{value / (kb * kb):.1f} MB"
    if value >= kb:
        return f"{value / kb:.1f} KB"
    return f"{num_bytes} B"


def _path_metadata(path: str) -> str:
    """文件夹 / 大小 / 已移动。"""
    try:
        st = os.stat(path)
    except OSError:
        return "已移动"
    if stat.S_ISDIR(st.st_mode):
        return "文件夹"
    return _human_size(st.st_size)


def _file_kind(ext: str) -> str:
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _VIDEO_EXT:
        return "video"
    if ext in _AUDIO_EXT:
        return "audio"
    if ext in _DOC_EXT:
        return "doc"
    if ext in _ARCHIVE_EXT:
        return "archive"
    if ext in _CODE_EXT:
        return "code"
    if ext in ("exe", "msi", "dll"):
        return "exe"
    return "file"


def _glyph_pixmap(size: int, ext: str, missing: bool) -> QPixmap:
    """内置矢量占位图标：文件页 + 扩展名 + 可选失效斜线。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        kind = "missing" if missing else _file_kind(ext)
        color = _EXT_COLORS[kind]

        left, top = size * 0.24, size * 0.13
        right, bottom = size * 0.76, size * 0.87
        fold = size * 0.20
        page = QPainterPath()
        page.moveTo(left, top)
        page.lineTo(right - fold, top)
        page.lineTo(right, top + fold)
        page.lineTo(right, bottom)
        page.lineTo(left, bottom)
        page.closeSubpath()

        painter.setBrush(QColor(255, 255, 255, 235))
        painter.setPen(QPen(color, max(1.2, size * 0.024)))
        painter.drawPath(page)

        corner = QPainterPath()
        corner.moveTo(right - fold, top)
        corner.lineTo(right - fold, top + fold)
        corner.lineTo(right, top + fold)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 40))
        painter.setPen(Qt.NoPen)
        painter.drawPath(corner)

        if missing:
            painter.setPen(QPen(color, max(1.6, size * 0.035)))
            painter.drawLine(
                int(size * 0.30), int(size * 0.72), int(size * 0.70), int(size * 0.28)
            )
        else:
            label = (ext or "FILE").upper()[:3]
            font = QFont("Segoe UI", max(6, int(size * 0.14)))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(
                QRectF(left, bottom - size * 0.30, right - left, size * 0.22),
                Qt.AlignCenter,
                label,
            )
    finally:
        painter.end()
    return pixmap


# ---------------------------------------------------------------------------
# 异步任务
# ---------------------------------------------------------------------------


class _ThumbnailSignals(QObject):
    """跨线程回传（信号自动排队到 GUI 线程）。"""

    loaded = Signal(str, object)


class _ThumbnailTask(QRunnable):
    def __init__(self, path: str, size: int, generation: int, signals: _ThumbnailSignals):
        super().__init__()
        self._path = path
        self._size = size
        self._generation = generation
        self._signals = signals
        self.setAutoDelete(True)

    def run(self):
        payload = {"generation": self._generation, "meta": ""}
        try:
            payload["meta"] = _path_metadata(self._path)
            if os.path.exists(self._path):
                image = _load_shell_image(
                    self._path, self._size, os.path.isdir(self._path)
                )
                if image is not None:
                    rgba, width, height = image
                    payload.update(rgba=rgba, width=width, height=height)
        except Exception:
            pass
        try:
            self._signals.loaded.emit(self._path, payload)
        except RuntimeError:
            # 供给器已随窗口销毁。
            pass


# ---------------------------------------------------------------------------
# 供给器
# ---------------------------------------------------------------------------


class FileIconProvider(QObject):
    """生成文件缩略图/图标，缓存为写盘 PNG 的 ``file:///`` URL。

    对外保持旧接口（``icon_url_for_path`` / ``pixmap_for_path`` /
    ``drag_pixmap_for_path``），新增 ``metadata_for_path`` 与 ``updated`` 信号。
    首次请求异步加载，完成时发出 ``updated(path)``，由模型触发 dataChanged。
    """

    updated = Signal(str)
    _MAX_CACHE = 128

    def __init__(self, icon_size: int = 96, parent=None):
        super().__init__(parent)
        self._icon_size = icon_size
        self._provider = None
        self._pixmap_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._url_cache: OrderedDict[str, str] = OrderedDict()
        self._meta_cache: dict[str, str] = {}
        self._pending: set[str] = set()
        self._generation = 0
        self._icon_dir = os.path.join(
            tempfile.gettempdir(), "xiaozhi_file_tray_thumbs"
        )
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._signals = _ThumbnailSignals(self)
        self._signals.loaded.connect(
            self._on_loaded, Qt.ConnectionType.QueuedConnection
        )

    # -- 内部 ---------------------------------------------------------------

    def _ensure_provider(self) -> QFileIconProvider:
        if self._provider is None:
            self._provider = QFileIconProvider()
        return self._provider

    def _queue(self, path: str):
        if not path or path in self._pending:
            return
        self._pending.add(path)
        self._pool.start(
            _ThumbnailTask(path, self._icon_size, self._generation, self._signals)
        )

    def _fallback_pixmap(self, path: str) -> QPixmap:
        """Shell 不可用时的回退：系统图标 → 内置矢量图标。"""
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        if not os.path.exists(path):
            return _glyph_pixmap(self._icon_size, ext, missing=True)
        try:
            icon = self._ensure_provider().icon(QFileInfo(path))
            if not icon.isNull():
                pixmap = icon.pixmap(self._icon_size, self._icon_size)
                if not pixmap.isNull():
                    return pixmap
        except Exception:
            pass
        return _glyph_pixmap(self._icon_size, ext, missing=False)

    def _sync_fallback_pixmap(self, path: str, size: int) -> QPixmap:
        """拖拽光标需要同步结果，直接走 Qt 系统图标。"""
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        try:
            icon = self._ensure_provider().icon(QFileInfo(path))
            if not icon.isNull():
                pixmap = icon.pixmap(size, size)
                if not pixmap.isNull():
                    return pixmap
        except Exception:
            pass
        return _glyph_pixmap(size, ext, missing=not os.path.exists(path))

    def _store(self, path: str, pixmap: QPixmap, meta: str):
        self._meta_cache[path] = meta
        if pixmap is None or pixmap.isNull():
            self._pixmap_cache[path] = QPixmap()
            self._url_cache[path] = ""
        else:
            self._pixmap_cache[path] = pixmap
            url = ""
            try:
                os.makedirs(self._icon_dir, exist_ok=True)
                name = hashlib.md5(path.encode("utf-8")).hexdigest() + ".png"
                png_path = os.path.join(self._icon_dir, name)
                if pixmap.save(png_path, "PNG"):
                    url = QUrl.fromLocalFile(png_path).toString()
            except Exception:
                url = ""
            self._url_cache[path] = url

        for cache in (self._pixmap_cache, self._url_cache):
            cache.move_to_end(path)
            while len(cache) > self._MAX_CACHE:
                cache.popitem(last=False)

    def _on_loaded(self, path: str, payload: object):
        self._pending.discard(path)
        if not isinstance(payload, dict):
            return
        if payload.get("generation") != self._generation:
            return

        meta = payload.get("meta", "")
        pixmap = None
        rgba = payload.get("rgba")
        if rgba:
            width = int(payload.get("width") or 0)
            height = int(payload.get("height") or 0)
            if width > 0 and height > 0:
                image = QImage(
                    rgba,
                    width,
                    height,
                    width * 4,
                    QImage.Format_RGBA8888_Premultiplied,
                )
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image.copy())
        if pixmap is None or pixmap.isNull():
            pixmap = self._fallback_pixmap(path)

        self._store(path, pixmap, meta)
        self.updated.emit(path)

    # -- 对外接口（保持旧签名） ---------------------------------------------

    def icon_url_for_path(self, path: str) -> str:
        """返回图标 PNG 的 file:/// URL；未就绪时返回空串并触发异步加载。"""
        url = self._url_cache.get(path)
        if url is not None:
            self._url_cache.move_to_end(path)
            return url
        self._queue(path)
        return ""

    def metadata_for_path(self, path: str) -> str:
        return self._meta_cache.get(path, "")

    def pixmap_for_path(self, path: str) -> QPixmap:
        pixmap = self._pixmap_cache.get(path)
        if pixmap is not None:
            self._pixmap_cache.move_to_end(path)
            return pixmap
        self._queue(path)
        return QPixmap()

    def drag_pixmap_for_path(self, path: str, size: int = 32) -> QPixmap:
        """拖拽光标用的小图标；缓存未命中时同步取系统图标。

        尺寸沿用旧行为（默认 32），避免拖拽图像明显大于资源管理器光标。
        """
        pixmap = self._pixmap_cache.get(path)
        if pixmap is None or pixmap.isNull():
            pixmap = self._sync_fallback_pixmap(path, max(size * 3, 64))
        if pixmap.isNull():
            return pixmap
        scaled = pixmap.scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        if scaled.width() == size and scaled.height() == size:
            return scaled
        # 缩略图可能是宽/高矩形；统一放到透明正方形画布，拖拽光标的
        # 落点和大小与旧版系统图标保持一致。
        canvas = QPixmap(size, size)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        try:
            painter.drawPixmap(
                (size - scaled.width()) // 2,
                (size - scaled.height()) // 2,
                scaled,
            )
        finally:
            painter.end()
        return canvas

    def clear(self):
        """清空缓存，并让尚未完成的加载结果失效。"""
        self._generation += 1
        self._pending.clear()
        self._pixmap_cache.clear()
        self._url_cache.clear()
        self._meta_cache.clear()

    def wait_for_done(self, timeout_ms: int = 3000) -> bool:
        """退出前等待后台任务结束（可选）。"""
        return self._pool.waitForDone(timeout_ms)
