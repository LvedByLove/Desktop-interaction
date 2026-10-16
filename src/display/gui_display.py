# -*- coding: utf-8 -*-
"""
GUI 显示模块 - 使用 QML 实现.
"""

import asyncio
import ctypes
import os
import signal
import sys
import time
from abc import ABCMeta
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QEasingCurve,
    QMimeData,
    QObject,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    QUrl,
    pyqtProperty,
)
from PyQt5.QtGui import QDrag, QCursor, QFont
from PyQt5.QtQuickWidgets import QQuickWidget
from PyQt5.QtWidgets import QApplication, QMessageBox, QVBoxLayout, QWidget

from src.display.base_display import BaseDisplay
from src.display.file_tray_model import FileIconProvider, FileTrayModel
from src.display.gui_display_model import GuiDisplayModel
from src.utils.resource_finder import find_assets_dir


# 创建兼容的元类
class CombinedMeta(type(QObject), ABCMeta):
    pass


class _WindowsSessionEndFilter(QAbstractNativeEventFilter):
    """拦截 Windows 关机/注销消息，允许系统正常关机，避免程序阻止关机。"""

    WM_QUERYENDSESSION = 0x0011
    WM_ENDSESSION = 0x0016

    def __init__(self, on_session_end):
        super().__init__()
        self._on_session_end = on_session_end

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) != b"windows_generic_MSG":
                return False, 0
        except Exception:
            return False, 0

        try:
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
        except Exception:
            return False, 0

        if msg.message == self.WM_QUERYENDSESSION:
            # 返回 1 (TRUE) 允许 Windows 继续关机，同时提前置位标志
            self._on_session_end(query_only=True)
            return True, 1
        if msg.message == self.WM_ENDSESSION:
            self._on_session_end(query_only=False)
            return True, 0
        return False, 0


class GuiDisplay(BaseDisplay, QObject, metaclass=CombinedMeta):
    """GUI 显示类 - 基于 QML 的现代化胶囊状界面"""

    # 常量定义
    EMOTION_EXTENSIONS = (".gif", ".png", ".jpg", ".jpeg", ".webp")
    # 胶囊状界面的默认尺寸（圆形状态）
    CAPSULE_SIZE = (36, 36)
    # 隐藏模式与屏幕顶部的间距
    TOP_MARGIN = 5
    # 圆球默认位置与屏幕右边缘的间距
    MARGIN_RIGHT = 940
    # 展开后的尺寸
    EXPANDED_SIZE = (320, 280)
    DEFAULT_FONT_SIZE = 12
    QUIT_TIMEOUT_MS = 3000

    def __init__(self):
        super().__init__()
        QObject.__init__(self)

        # Qt 组件
        self.app = None
        self.root = None
        self.qml_widget = None
        self.system_tray = None

        # 数据模型
        self.display_model = GuiDisplayModel()

        # 文件中转站：数据源（路径列表）与系统文件图标供给器
        self._file_tray = FileTrayModel()
        self._file_icon_provider = FileIconProvider()
        self._file_tray.set_icon_provider(self._file_icon_provider)
        # 全局拖拽检测（主动唤醒圆球），延迟到 GUI 启动后再实例化
        self._drag_watcher = None
        # OLE 文件拖拽捕捉器（CF_HDROP 精确判定），延迟到 GUI 启动后再实例化
        self._ole_catcher = None
        # 文件中转站独立窗口（与主胶囊窗口解耦，可同时显示互不干扰）
        self.tray_root = None
        self.tray_qml = None
        self._tray_geometry_anim = None

        # 表情管理
        self._emotion_cache = {}
        self._last_emotion_name = None

        # 状态管理
        self.auto_mode = False
        self._running = True
        self.current_status = ""
        self.is_connected = True

        # 窗口拖动状态
        self._dragging = False
        self._drag_position = None
        self._music_indicator_dragging = False
        self._music_indicator_drag_position = None
        self._music_indicator_was_moved = False

        # TTS 一体化显示状态
        self._tts_hide_timer = None

        # 输入框激活状态
        self._input_active = False
        self._input_activation_timer = None

        # 回调函数映射
        self._callbacks = {
            "button_press": None,
            "button_release": None,
            "mode": None,
            "auto": None,
            "abort": None,
            "send_text": None,
        }

        # Windows 关机/注销状态
        self._system_shutdown = False
        self._session_end_filter = None

    # =========================================================================
    # 公共 API - 回调与更新
    # =========================================================================

    async def set_callbacks(
        self,
        press_callback: Optional[Callable] = None,
        release_callback: Optional[Callable] = None,
        mode_callback: Optional[Callable] = None,
        auto_callback: Optional[Callable] = None,
        abort_callback: Optional[Callable] = None,
        send_text_callback: Optional[Callable] = None,
    ):
        """
        设置回调函数.
        """
        self._callbacks.update(
            {
                "button_press": press_callback,
                "button_release": release_callback,
                "mode": mode_callback,
                "auto": auto_callback,
                "abort": abort_callback,
                "send_text": send_text_callback,
            }
        )

    async def update_status(self, status: str, connected: bool):
        """
        更新状态文本并处理相关逻辑.
        """
        self.display_model.update_status(status, connected)

        # 跟踪状态变化
        status_changed = status != self.current_status
        connected_changed = bool(connected) != self.is_connected

        if status_changed:
            self.current_status = status
        if connected_changed:
            self.is_connected = bool(connected)

        # 更新系统托盘
        if (status_changed or connected_changed) and self.system_tray:
            self.system_tray.update_status(status, self.is_connected)

    async def update_text(self, text: str):
        """
        更新 TTS 文本.
        """
        self.display_model.update_text(text)

    async def show_listening(self):
        """
        在按住语音对话快捷键时显示倾听状态.
        """
        if self._tts_hide_timer:
            self._tts_hide_timer.cancel()
            self._tts_hide_timer = None

        if self.root:
            self.root.show()
            self.root.raise_()

        root_object = self.qml_widget.rootObject() if self.qml_widget else None
        if root_object:
            root_object.showListening()

    async def hide_listening(self):
        """
        结束按住语音对话时退出倾听状态.
        """
        root_object = self.qml_widget.rootObject() if self.qml_widget else None
        if root_object:
            root_object.hideListening()

    async def show_tts(self, text: str = ""):
        """
        在主胶囊窗口内显示 TTS 状态.
        """
        if self._tts_hide_timer:
            self._tts_hide_timer.cancel()
            self._tts_hide_timer = None

        self.display_model.update_text(text or "语音播报中...")
        # 普通对话的 TTS 回复计入会话累计文本；音乐歌词不计入
        if not self.display_model.musicLyricsActive:
            self.display_model.begin_tts_reply(text)
        self.display_model.set_tts_visible(True)

        if self.root:
            self.root.show()
            self.root.raise_()

        root_object = self.qml_widget.rootObject() if self.qml_widget else None
        if root_object:
            root_object.showTts(text or "语音播报中...")

    async def hide_tts(self):
        """
        延迟隐藏主胶囊窗口内的 TTS 状态.
        """
        if self._tts_hide_timer:
            self._tts_hide_timer.cancel()
            self._tts_hide_timer = None

        loop = asyncio.get_event_loop()
        self._tts_hide_timer = loop.call_later(3, self._finish_hide_tts)

    def _finish_hide_tts(self):
        """
        实际退出 TTS 状态.
        """
        self._tts_hide_timer = None
        self.display_model.set_tts_visible(False)

        if getattr(self, "_music_indicator_was_moved", False):
            self.restore_default_top_position(self.CAPSULE_SIZE[0])

        root_object = self.qml_widget.rootObject() if self.qml_widget else None
        if root_object:
            root_object.hideTts()

    async def update_tts_text(self, text: str):
        """
        更新主胶囊 TTS 文本.
        """
        self.display_model.update_text(text)
        # 普通对话的流式文本覆盖累计文本的最后一条；音乐歌词不计入
        if not self.display_model.musicLyricsActive:
            self.display_model.update_tts_reply(text)

    def clear_tts_transcript(self):
        """
        清空本次会话累计的 TTS 文本（新会话开始时调用）.
        """
        self.display_model.clear_tts_transcript()

    def set_music_lyrics_active(self, active: bool):
        """
        设置当前 TTS 区域是否由音乐歌词占用.
        """
        self.display_model.set_music_lyrics_active(active)
        self._refresh_qml_current_mode_size()

    def set_music_lyrics_collapsed(self, collapsed: bool):
        """
        设置音乐歌词是否折叠为播放指示器.
        """
        self.display_model.set_music_lyrics_collapsed(collapsed)
        self._refresh_qml_current_mode_size()

    def _refresh_qml_current_mode_size(self):
        root_object = self.qml_widget.rootObject() if self.qml_widget else None
        if root_object and hasattr(root_object, "refreshCurrentModeSize"):
            try:
                root_object.refreshCurrentModeSize()
            except Exception as e:
                self.logger.debug(f"刷新歌词显示尺寸失败: {e}")

    async def update_emotion(self, emotion_name: str):
        """
        更新表情显示.
        """
        if emotion_name == self._last_emotion_name:
            return

        self._last_emotion_name = emotion_name
        asset_path = self._get_emotion_asset_path(emotion_name)

        # 将本地文件路径转换为 QML 可用的 URL（file:///...），
        # 非文件（如 emoji 字符）保持原样。
        def to_qml_url(p: str) -> str:
            if not p:
                return ""
            if p.startswith(("qrc:/", "file:")):
                return p
            # 仅当路径存在时才转换为 file URL，避免把 emoji 当作路径
            try:
                if os.path.exists(p):
                    return QUrl.fromLocalFile(p).toString()
            except Exception:
                pass
            return p

        url_or_text = to_qml_url(asset_path)
        self.display_model.update_emotion(url_or_text)

    async def update_button_status(self, text: str):
        """
        更新按钮状态.
        """
        if self.auto_mode:
            self.display_model.update_button_text(text)

    async def toggle_mode(self):
        """
        切换对话模式.
        """
        if self._callbacks["mode"]:
            self._on_mode_button_click()
            self.logger.debug("通过快捷键切换了对话模式")

    async def toggle_window_visibility(self):
        """
        切换胶囊内文字输入状态.
        """
        if self.root:
            self.root.show()
            self.root.raise_()
            self.root.activateWindow()

        root_object = self.qml_widget.rootObject() if self.qml_widget else None
        if root_object:
            self.logger.debug("通过快捷键切换胶囊内文字输入状态")
            root_object.toggleInput()
            if root_object.property("viewMode") == "input":
                self._activate_input_window()
            else:
                self._input_active = False

    def _activate_input_window(self):
        """
        激活胶囊输入框，让快捷键打开后可直接输入，并开始监听外部点击.
        """
        self._input_active = True
        if self._input_activation_timer:
            self._input_activation_timer.stop()

        self._focus_input_text()
        self._input_activation_timer = QTimer.singleShot(80, self._focus_input_text)
        QTimer.singleShot(260, self._focus_input_text)

    def _focus_input_text(self):
        """
        把系统焦点和 QML 焦点都交给输入框.
        """
        if not self.root:
            return

        try:
            if os.name == "nt":
                hwnd = int(self.root.winId())
                ctypes.windll.user32.ShowWindow(hwnd, 5)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

        self.root.show()
        self.root.raise_()
        self.root.activateWindow()

        if self.qml_widget:
            self.qml_widget.setFocus(Qt.ActiveWindowFocusReason)
            root_object = self.qml_widget.rootObject()
            if root_object and root_object.property("viewMode") == "input":
                root_object.focusInputText()

    def eventFilter(self, watched, event):
        """
        主胶囊输入时点击其它位置自动收起输入框.
        """
        try:
            root_object = self.qml_widget.rootObject() if self.qml_widget else None
        except RuntimeError:
            root_object = None
        if not root_object:
            return super().eventFilter(watched, event)

        if watched == self.root and event.type() == QEvent.WindowActivate:
            if root_object.property("viewMode") == "input":
                QTimer.singleShot(0, root_object.focusInputText)

        if watched == self.root and event.type() == QEvent.WindowDeactivate:
            if root_object.property("viewMode") == "input":
                QTimer.singleShot(0, root_object.hideInput)
            self._input_active = False

        if watched == self.app and event.type() == QEvent.ApplicationDeactivate:
            if root_object.property("viewMode") == "input":
                QTimer.singleShot(0, root_object.hideInput)
            self._input_active = False

        if watched == self.app and self._input_active and event.type() == QEvent.MouseButtonPress:
            if root_object.property("viewMode") == "input":
                pos = QCursor.pos()
                if self.root and not self.root.frameGeometry().contains(pos):
                    QTimer.singleShot(0, root_object.hideInput)
                    self._input_active = False

        return super().eventFilter(watched, event)

    async def close(self):
        """
        关闭窗口处理.
        """
        self._running = False
        # 清空中转站：让 QML 的 Repeater 在引擎仍健康时销毁大量 delegate，
        # 避免退出时模型对象先被回收、QML 绑定空引用导致卡顿与报错。
        # 放在停止拖拽监听之前，避免被 pynput stop() 阻塞主线程而延迟清理。
        t0 = time.perf_counter()
        self._file_tray.clear()
        t1 = time.perf_counter()
        self._stop_drag_watcher()
        t2 = time.perf_counter()
        if self._tts_hide_timer:
            self._tts_hide_timer.cancel()
            self._tts_hide_timer = None
        if self.app:
            self.app.removeEventFilter(self)
        if self.system_tray:
            self.system_tray.hide()
        if self.tray_root:
            self.tray_root.close()
        if self.root:
            self.root.close()
        t3 = time.perf_counter()
        self.logger.info(
            "GuiDisplay 关闭耗时: "
            f"clear={1000 * (t1 - t0):.1f}ms, "
            f"stop_drag_watcher={1000 * (t2 - t1):.1f}ms, "
            f"root.close={1000 * (t3 - t2):.1f}ms"
        )

    # =========================================================================
    # 启动流程
    # =========================================================================

    async def start(self):
        """
        启动 GUI.
        """
        try:
            self._configure_environment()
            self._create_main_window()
            self._load_qml()
            self._setup_interactions()
            await self._finalize_startup()
            self._start_drag_watcher()
        except Exception as e:
            self.logger.error(f"GUI启动失败: {e}", exc_info=True)
            raise

    def _configure_environment(self):
        """
        配置环境.
        """
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts.debug=false")

        self.app = QApplication.instance()
        if self.app is None:
            raise RuntimeError("QApplication 未找到，请确保在 qasync 环境中运行")

        self.app.setQuitOnLastWindowClosed(False)
        self.app.setFont(QFont("PingFang SC", self.DEFAULT_FONT_SIZE))

        self._setup_signal_handlers()
        self._setup_activation_handler()
        self._setup_session_end_filter()

    def _setup_session_end_filter(self):
        """
        监听 Windows 关机/注销消息，正常退出而不是阻止关机.
        """
        if sys.platform != "win32":
            return
        try:
            self._session_end_filter = _WindowsSessionEndFilter(
                self._on_windows_session_end
            )
            self.app.installNativeEventFilter(self._session_end_filter)
            self.logger.debug("已安装 Windows 会话结束监听")
        except Exception as e:
            self.logger.warning(f"安装会话结束监听失败: {e}")

    def _on_windows_session_end(self, query_only: bool = False):
        """
        处理 Windows 关机/注销事件.

        Args:
            query_only: True 表示 WM_QUERYENDSESSION（仅询问，先置位标志），
                False 表示 WM_ENDSESSION（正式结束，触发退出）。
        """
        if not self._system_shutdown:
            self.logger.info("检测到系统关机/注销请求")
        self._system_shutdown = True
        if not query_only:
            QTimer.singleShot(0, self._quit_application)

    def _create_main_window(self):
        """
        创建主窗口 - 胶囊状界面.
        """
        self.root = QWidget()
        self.root.setWindowTitle("")
        # 无边框 + 透明背景 + 保持在顶层 + 工具窗口（不显示在任务栏）
        self.root.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.root.setFocusPolicy(Qt.StrongFocus)
        # 设置窗口透明背景
        self.root.setAttribute(Qt.WA_TranslucentBackground)
        # 接受外部拖入（文件/文件夹），用于文件中转站
        self.root.setAcceptDrops(True)

        # 使用胶囊状尺寸（圆形状态）
        self.root.resize(*self.CAPSULE_SIZE)

        # 设置最小窗口尺寸为隐藏模式大小（允许变成细线）
        self.root.setMinimumSize(1, 1)

        # 保存是否全屏的状态，在 show 时使用
        self._is_fullscreen = False

        self.root.closeEvent = self._closeEvent
        self.root.installEventFilter(self)
        self.app.installEventFilter(self)

        # 设置初始位置到屏幕右上角（最上面）
        self._set_initial_position()

    def _set_initial_position(self):
        """
        设置窗口初始位置到屏幕右上角（最上面）
        """
        desktop = QApplication.desktop()
        screen_rect = desktop.availableGeometry()
        screen_width = screen_rect.width()
        screen_height = screen_rect.height()

        # 右上角位置：右边缘留出一些边距，顶部保留间距
        margin_right = self.MARGIN_RIGHT
        x = screen_width - self.CAPSULE_SIZE[0] - margin_right
        y = self.TOP_MARGIN

        self.root.move(x, y)

    def _calculate_window_size(self) -> tuple:
        """
        根据配置计算窗口大小，返回 (宽, 高, 是否全屏)
        """
        try:
            from src.utils.config_manager import ConfigManager

            config_manager = ConfigManager.get_instance()
            window_size_mode = config_manager.get_config(
                "SYSTEM_OPTIONS.WINDOW_SIZE_MODE", "default"
            )

            # 获取屏幕尺寸（可用区域，排除任务栏等）
            desktop = QApplication.desktop()
            screen_rect = desktop.availableGeometry()
            screen_width = screen_rect.width()
            screen_height = screen_rect.height()

            # 根据模式计算窗口大小
            if window_size_mode == "default":
                # 默认使用 50%
                width = int(screen_width * 0.5)
                height = int(screen_height * 0.5)
                is_fullscreen = False
            elif window_size_mode == "screen_75":
                width = int(screen_width * 0.75)
                height = int(screen_height * 0.75)
                is_fullscreen = False
            elif window_size_mode == "screen_100":
                # 100% 使用真正的全屏模式
                width = screen_width
                height = screen_height
                is_fullscreen = True
            else:
                # 未知模式使用 50%
                width = int(screen_width * 0.5)
                height = int(screen_height * 0.5)
                is_fullscreen = False

            return ((width, height), is_fullscreen)

        except Exception as e:
            self.logger.error(f"计算窗口大小失败: {e}", exc_info=True)
            # 错误时返回屏幕 50%
            try:
                desktop = QApplication.desktop()
                screen_rect = desktop.availableGeometry()
                return (
                    (int(screen_rect.width() * 0.5), int(screen_rect.height() * 0.5)),
                    False,
                )
            except Exception:
                return (self.DEFAULT_WINDOW_SIZE, False)

    def _load_qml(self):
        """
        加载 QML 界面 - 胶囊状界面.
        """
        self.qml_widget = QQuickWidget()
        self.qml_widget.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.qml_widget.setFocusPolicy(Qt.StrongFocus)
        # 接受外部拖入事件，配合 QML DropArea 接收文件/文件夹
        self.qml_widget.setAcceptDrops(True)
        # 设置透明背景，配合胶囊状界面
        self.qml_widget.setClearColor(Qt.transparent)

        # 注册数据模型到 QML 上下文
        qml_context = self.qml_widget.rootContext()
        qml_context.setContextProperty("displayModel", self.display_model)

        # 加载 QML 文件
        qml_file = Path(__file__).parent / "gui_display.qml"
        self.qml_widget.setSource(QUrl.fromLocalFile(str(qml_file)))

        # 设置为主窗口的中央 widget
        layout = QVBoxLayout(self.root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.qml_widget)
        self.root.setFocusProxy(self.qml_widget)

        # 创建独立的文件中转站窗口（与主胶囊窗口解耦，可同时显示互不干扰）
        self._create_tray_window()

    def _create_tray_window(self):
        """
        创建文件中转站独立窗口：拖拽唤醒薄条 + 中转站面板。

        与主胶囊窗口完全分离，因此 TTS 播报期间拖入文件也能正常唤醒中转站，
        不会因为共用 viewMode 状态机而互相打断。
        """
        self.tray_root = QWidget()
        self.tray_root.setWindowTitle("")
        # 无边框 + 顶层 + 工具窗口 + 不抢焦点（避免打断主胶囊输入/拖拽源焦点）
        self.tray_root.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.tray_root.setAttribute(Qt.WA_TranslucentBackground)
        self.tray_root.setAcceptDrops(True)
        self.tray_root.setMinimumSize(1, 1)
        self.tray_root.hide()

        self.tray_qml = QQuickWidget()
        self.tray_qml.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.tray_qml.setFocusPolicy(Qt.NoFocus)
        self.tray_qml.setAcceptDrops(True)
        self.tray_qml.setClearColor(Qt.transparent)

        qml_context = self.tray_qml.rootContext()
        qml_context.setContextProperty("fileTrayModel", self._file_tray)

        qml_file = Path(__file__).parent / "file_tray_window.qml"
        self.tray_qml.setSource(QUrl.fromLocalFile(str(qml_file)))

        layout = QVBoxLayout(self.tray_root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tray_qml)

    def _setup_interactions(self):
        """
        设置交互（信号、托盘）
        """
        self._connect_qml_signals()
        self._connect_tray_qml_signals()

    async def _finalize_startup(self):
        """
        完成启动流程.
        """
        await self.update_emotion("neutral")

        # 根据配置决定显示模式
        if getattr(self, "_is_fullscreen", False):
            self.root.showFullScreen()
        else:
            self.root.show()

        self._setup_system_tray()

    # =========================================================================
    # 信号连接
    # =========================================================================

    def _connect_qml_signals(self):
        """
        连接 QML 信号到 Python 槽.
        """
        root_object = self.qml_widget.rootObject()
        if not root_object:
            self.logger.warning("QML 根对象未找到，无法设置信号连接")
            return

        # 按钮事件信号映射
        button_signals = {
            "manualButtonPressed": self._on_manual_button_press,
            "manualButtonReleased": self._on_manual_button_release,
            "autoButtonClicked": self._on_auto_button_click,
            "abortButtonClicked": self._on_abort_button_click,
            "modeButtonClicked": self._on_mode_button_click,
            "sendButtonClicked": self._on_send_button_click,
        }

        # 标题栏控制信号映射
        titlebar_signals = {
            "titleMinimize": self._minimize_window,
            "titleClose": self._quit_application,
            "titleDragStart": self._on_title_drag_start,
            "titleDragMoveTo": self._on_title_drag_move,
            "titleDragEnd": self._on_title_drag_end,
        }

        # 窗口大小变化信号
        window_signals = {
            "windowResizeRequested": self._on_window_resize_requested,
        }

        # 系统控制信号
        system_signals = {
            "hiddenModeChanged": self._on_hidden_mode_changed,
            "musicIndicatorDragStart": self._on_music_indicator_drag_start,
            "musicIndicatorDragMove": self._on_music_indicator_drag_move,
            "musicIndicatorDragEnd": self._on_music_indicator_drag_end,
        }

        # 文件中转站：主胶囊上的打开入口（文件夹图标）→ 打开独立中转站窗口
        tray_signals = {
            "fileTrayOpenRequested": self._open_file_tray,
        }

        # 批量连接信号
        all_signals = {**button_signals, **titlebar_signals, **window_signals, **system_signals, **tray_signals}
        for signal_name, handler in all_signals.items():
            try:
                getattr(root_object, signal_name).connect(handler)
            except AttributeError:
                self.logger.debug(f"信号 {signal_name} 不存在（可能是可选功能）")

        self.logger.debug("QML 信号连接设置完成")

    def _connect_tray_qml_signals(self):
        """
        连接独立的文件中转站窗口信号到 Python 槽。
        """
        if not self.tray_qml:
            return
        root_object = self.tray_qml.rootObject()
        if not root_object:
            self.logger.warning("中转站 QML 根对象未找到，无法设置信号连接")
            return

        tray_signals = {
            "windowResizeRequested": self._on_tray_window_resize_requested,
            "filesDropped": self._on_files_dropped,
            "fileDragRequested": self._on_file_drag_requested,
            "fileRemoveRequested": self._on_file_remove_requested,
            "fileTrayClosed": self._on_file_tray_closed,
            "fadeOutDone": self._on_tray_fade_out_done,
            "selectIndex": self._on_tray_select_index,
            "marqueeSelection": self._on_tray_marquee_selection,
            "clearSelection": self._on_tray_clear_selection,
        }
        for signal_name, handler in tray_signals.items():
            try:
                getattr(root_object, signal_name).connect(handler)
            except AttributeError:
                self.logger.debug(f"中转站信号 {signal_name} 不存在（可能是可选功能）")

        self.logger.debug("文件中转站信号连接设置完成")

    def _show_tray_window(self):
        """显示中转站窗口（保持顶层，不抢焦点），首次显示时做淡入。"""
        if not self.tray_root:
            return
        was_visible = self.tray_root.isVisible()
        if not was_visible:
            root_object = self.tray_qml.rootObject() if self.tray_qml else None
            if root_object and hasattr(root_object, "appear"):
                try:
                    root_object.appear()
                except Exception as e:
                    self.logger.debug(f"中转站淡入失败: {e}")
        self.tray_root.show()
        self.tray_root.raise_()

    def _open_file_tray(self):
        """打开文件中转站面板（独立窗口）。"""
        root_object = self.tray_qml.rootObject() if self.tray_qml else None
        if root_object and hasattr(root_object, "openFileTray"):
            try:
                # 先按目标尺寸调整窗口，再显示，避免闪现默认尺寸
                root_object.openFileTray()
                self._show_tray_window()
            except Exception as e:
                self.logger.debug(f"打开中转站失败: {e}")

    def _fade_out_tray_window(self):
        """淡出中转站窗口；完成后由 fadeOutDone 信号触发隐藏。"""
        root_object = self.tray_qml.rootObject() if self.tray_qml else None
        if root_object and hasattr(root_object, "fadeOutNow"):
            try:
                root_object.fadeOutNow()
                return
            except Exception as e:
                self.logger.debug(f"中转站淡出失败: {e}")
        # 兜底：无法淡出时直接隐藏
        self._on_tray_fade_out_done()

    def _on_tray_fade_out_done(self):
        """淡出动画结束：真正隐藏中转站窗口。"""
        if self.tray_root:
            self.tray_root.hide()

    def _hide_tray_window_if_idle(self):
        """拖拽结束后，若中转站面板未打开且窗口当前可见，则淡出隐藏整个中转站窗口。"""
        root_object = self.tray_qml.rootObject() if self.tray_qml else None
        tray_visible = bool(root_object.property("trayVisible")) if root_object else False
        if not tray_visible and self.tray_root and self.tray_root.isVisible():
            self._fade_out_tray_window()

    def _on_tray_window_resize_requested(self, width: int, height: int):
        """中转站窗口尺寸变化：始终保持在主屏顶部居中，并做平滑几何过渡。"""
        if not self.tray_root:
            return
        desktop = QApplication.desktop()
        screen_rect = desktop.availableGeometry()
        new_x = screen_rect.left() + (screen_rect.width() - width) // 2
        new_y = screen_rect.top()
        target_rect = QRect(new_x, new_y, width, height)

        # 窗口尚未显示时直接定位，避免首帧闪现默认尺寸
        if not self.tray_root.isVisible():
            self.tray_root.setGeometry(target_rect)
            return

        try:
            if self._tray_geometry_anim is None:
                self._tray_geometry_anim = QPropertyAnimation(self.tray_root, b"geometry")
                self._tray_geometry_anim.setDuration(220)
                self._tray_geometry_anim.setEasingCurve(QEasingCurve.OutCubic)
            if self._tray_geometry_anim.state() == QPropertyAnimation.Running:
                self._tray_geometry_anim.stop()
            self._tray_geometry_anim.setStartValue(self.tray_root.geometry())
            self._tray_geometry_anim.setEndValue(target_rect)
            self._tray_geometry_anim.start()
        except Exception as e:
            self.logger.debug(f"中转站窗口动画失败，直接调整: {e}")
            self.tray_root.setGeometry(target_rect)

    # =========================================================================
    # 按钮事件处理
    # =========================================================================

    def _on_manual_button_press(self):
        """
        手动模式按钮按下.
        """
        self._dispatch_callback("button_press")

    def _on_manual_button_release(self):
        """
        手动模式按钮释放.
        """
        self._dispatch_callback("button_release")

    def _on_auto_button_click(self):
        """
        自动模式按钮点击.
        """
        self._dispatch_callback("auto")

    def _on_abort_button_click(self):
        """
        中止按钮点击.
        """
        self._dispatch_callback("abort")

    def _on_mode_button_click(self):
        """
        对话模式切换按钮点击.
        """
        if self._callbacks["mode"] and not self._callbacks["mode"]():
            return

        self.auto_mode = not self.auto_mode
        mode_text = "自动对话" if self.auto_mode else "手动对话"
        self.display_model.update_mode_text(mode_text)
        self.display_model.set_auto_mode(self.auto_mode)

    def _on_send_button_click(self, text: str):
        """
        处理发送文本按钮点击.
        """
        text = text.strip()
        if not text or not self._callbacks["send_text"]:
            return

        try:
            task = asyncio.create_task(self._callbacks["send_text"](text))
            task.add_done_callback(
                lambda t: t.cancelled()
                or not t.exception()
                or self.logger.error(
                    f"发送文本任务异常: {t.exception()}", exc_info=True
                )
            )
        except Exception as e:
            self.logger.error(f"发送文本时出错: {e}")

    def _on_settings_button_click(self):
        """
        处理设置请求.
        """
        try:
            from src.views.settings import SettingsWindow

            settings_window = SettingsWindow()
            settings_window.exec_()
        except Exception as e:
            self.logger.error(f"打开设置窗口失败: {e}", exc_info=True)

    def _on_hidden_mode_changed(self, is_hidden: bool):
        """
        处理隐藏模式变化信号.
        进入隐藏模式：窗口移动到屏幕顶部并保留顶部间距
        退出隐藏模式：窗口保持在当前隐藏位置，恢复正常大小
        """
        try:
            current_x = self.root.x()

            if is_hidden:
                self.logger.info("进入隐藏模式，窗口移动到屏幕顶部并保留间距")
                self.root.move(current_x, self.TOP_MARGIN)
            else:
                self.logger.info("退出隐藏模式，窗口恢复正常大小")
                self.root.move(current_x, self.TOP_MARGIN)
        except Exception as e:
            self.logger.error(f"处理隐藏模式变化失败: {e}", exc_info=True)

    def restore_default_top_position(self, width: int = None):
        """
        恢复到屏幕中间上方的默认位置.
        """
        if not self.root:
            return

        desktop = QApplication.desktop()
        screen_rect = desktop.availableGeometry()
        target_width = width or self.root.width()
        x = screen_rect.left() + (screen_rect.width() - target_width) // 2
        self.root.move(x, self.TOP_MARGIN)
        self._music_indicator_was_moved = False

    def _on_window_resize_requested(self, width: int, height: int):
        """
        处理 QML 请求的窗口大小变化.
        展开方式：向黑球左边、右边和下面共同展开，保持右边缘位置不变
        """
        if self.root:
            # 获取当前窗口位置
            current_x = self.root.x()
            current_y = self.root.y()
            current_width = self.root.width()
            current_height = self.root.height()

            # 计算扩展量：向左右两侧扩展
            expand_total = width - current_width
            expand_right = expand_total // 2
            expand_left = expand_total - expand_right
            
            # 新位置：向左移动 expand_left，顶部位置不变（向下扩展）
            new_x = current_x - expand_left
            new_y = current_y  # 保持顶部位置不变，向下扩展

            root_object = self.qml_widget.rootObject() if self.qml_widget else None
            view_mode = root_object.property("viewMode") if root_object else None
            is_music_indicator = (
                self.display_model.musicLyricsCollapsed
                and view_mode == "tts"
            )
            if view_mode == "hidden":
                new_y = self.TOP_MARGIN

            # 确保窗口不超出屏幕边界
            desktop = QApplication.desktop()
            screen_rect = desktop.availableGeometry()
            should_restore_music_position = (
                view_mode == "tts"
                and not self.display_model.musicLyricsCollapsed
            ) or (
                getattr(self, "_music_indicator_was_moved", False)
                and not is_music_indicator
            )
            if should_restore_music_position:
                new_x = screen_rect.left() + (screen_rect.width() - width) // 2
                new_y = self.TOP_MARGIN
                self._music_indicator_was_moved = False

            if new_x < screen_rect.left():
                new_x = screen_rect.left()
            if new_y < screen_rect.top():
                new_y = screen_rect.top()
            # 检查右边缘
            if new_x + width > screen_rect.right():
                new_x = screen_rect.right() - width
            # 检查底部边缘
            if new_y + height > screen_rect.bottom():
                # 如果底部超出，向上调整位置
                new_y = screen_rect.bottom() - height

            # 隐藏模式直接收起（不做缩放动画），避免关闭中转站/拖拽退出时闪现"小球"
            if view_mode == "hidden":
                if hasattr(self, "_window_animation") and self._window_animation is not None:
                    if self._window_animation.state() == QPropertyAnimation.Running:
                        self._window_animation.stop()
                self.root.setGeometry(new_x, new_y, width, height)
                self.logger.debug(f"窗口已隐藏: {width}x{height}，位置: ({new_x}, {new_y})")
                return

            # 使用 Qt 动画实现平滑收缩效果
            try:
                # 如果动画正在运行，先停止
                if hasattr(self, '_window_animation') and self._window_animation is not None:
                    if self._window_animation.state() == QPropertyAnimation.Running:
                        self._window_animation.stop()
                
                # 创建或重用动画对象
                if not hasattr(self, '_window_animation') or self._window_animation is None:
                    self._window_animation = QPropertyAnimation(self.root, b"geometry")
                    self._window_animation.setDuration(220)
                
                self._window_animation.setStartValue(self.root.geometry())
                self._window_animation.setEndValue(QRect(new_x, new_y, width, height))
                self._window_animation.start()
                
                self.logger.debug(f"窗口大小已调整为: {width}x{height}，位置: ({new_x}, {new_y})")
            except Exception as e:
                # 如果动画失败，直接调整窗口大小
                self.logger.error(f"窗口动画失败，使用直接调整: {e}")
                self.root.move(new_x, new_y)
                self.root.resize(width, height)

    def _dispatch_callback(self, callback_name: str, *args):
        """
        通用回调调度器.
        """
        callback = self._callbacks.get(callback_name)
        if callback:
            callback(*args)

    def _on_music_indicator_drag_start(self):
        """
        音乐隐藏小动画拖动开始.
        """
        if not self.root:
            return
        self._music_indicator_dragging = True
        self._music_indicator_drag_position = QCursor.pos() - self.root.pos()

    def _on_music_indicator_drag_move(self):
        """
        音乐隐藏小动画拖动移动.
        """
        if self._music_indicator_dragging and self._music_indicator_drag_position:
            self.root.move(QCursor.pos() - self._music_indicator_drag_position)

    def _on_music_indicator_drag_end(self, moved: bool):
        """
        音乐隐藏小动画拖动结束.
        """
        self._music_indicator_dragging = False
        self._music_indicator_drag_position = None
        if moved:
            self._music_indicator_was_moved = True

    # =========================================================================
    # 文件中转站
    # =========================================================================

    def _start_drag_watcher(self):
        """启动全局拖拽检测链路：pynput 粗检测 + OLE CF_HDROP 精确判定。

        两个组件各自独立失败域：一个初始化失败不影响另一个启动。
        """
        # 1) pynput 粗检测（把疑似拖拽的开始/结束转发给 OLE 捕捉窗口）
        try:
            from src.display.file_drag_watcher import FileDragWatcher

            self._drag_watcher = FileDragWatcher(self)
            self._drag_watcher.dragStarted.connect(self._on_global_drag_started)
            self._drag_watcher.dragEnded.connect(self._on_global_drag_ended)
            self._drag_watcher.start()
            self.logger.info("pynput 全局拖拽检测已启动")
        except Exception as e:
            self.logger.error(f"启动 pynput 拖拽检测失败: {e}", exc_info=True)

        # 2) OLE 捕捉器（CF_HDROP 精确判定后才唤醒圆球）
        try:
            from src.display.ole_drop_catcher import OleDropCatcher

            self._ole_catcher = OleDropCatcher(self)
            self._ole_catcher.fileDragEntered.connect(self._on_file_drag_entered)
            self._ole_catcher.start()
            self.logger.info("OLE 文件拖拽捕捉器已启动")
        except Exception as e:
            self.logger.error(f"启动 OLE 捕捉器失败: {e}", exc_info=True)

    def _stop_drag_watcher(self):
        """停止全局拖拽检测。"""
        if self._drag_watcher:
            try:
                self._drag_watcher.stop()
            except Exception:
                pass
            self._drag_watcher = None
        if self._ole_catcher:
            try:
                self._ole_catcher.stop()
            except Exception:
                pass
            self._ole_catcher = None

    def _on_global_drag_started(self):
        """疑似拖拽开始（按下左键并移动超阈值）：显示 OLE 隐形捕捉窗口。

        是否唤醒圆球由捕捉窗口的 CF_HDROP 判定决定（见 _on_file_drag_entered）。
        """
        self.logger.info("疑似拖拽开始：显示 OLE 捕捉窗口")
        if self._ole_catcher:
            try:
                self._ole_catcher.show()
            except Exception as e:
                self.logger.debug(f"显示捕捉窗口失败: {e}")

    def _on_global_drag_ended(self):
        """拖拽结束（松开左键）：隐藏捕捉窗口，唤醒薄条回落待命态。"""
        self.logger.info("拖拽结束：隐藏 OLE 捕捉窗口")
        if self._ole_catcher:
            try:
                self._ole_catcher.hide()
            except Exception as e:
                self.logger.debug(f"隐藏捕捉窗口失败: {e}")
        root_object = self.tray_qml.rootObject() if self.tray_qml else None
        if root_object and hasattr(root_object, "unwakeBall"):
            try:
                root_object.unwakeBall()
            except Exception as e:
                self.logger.debug(f"唤醒薄条回落失败: {e}")
        self._hide_tray_window_if_idle()

    def _on_file_drag_entered(self):
        """OLE 确认文件/文件夹拖拽经过顶部目标区：唤醒薄条作为可拖放目标。"""
        self.logger.info("OLE 命中文件拖拽：唤醒中转站")
        root_object = self.tray_qml.rootObject() if self.tray_qml else None
        if root_object and hasattr(root_object, "wakeBall"):
            try:
                # 先按唤醒薄条尺寸调整窗口，再显示，避免闪现默认尺寸
                root_object.wakeBall()
                self._show_tray_window()
            except Exception as e:
                self.logger.debug(f"唤醒中转站失败: {e}")

    def _on_files_dropped(self, urls):
        """
        处理外部文件/文件夹拖入中转站.

        QML DropArea 传入的是 file:/// 形式的 URL 列表，这里提取本地路径并去重加入。
        """
        try:
            # QML 信号传入的数组在 Python 侧是 QJSValue，需转成 Python 列表
            if hasattr(urls, "toVariant"):
                urls = urls.toVariant()
            local_paths = []
            for u in urls or []:
                # 单个元素也可能是 QJSValue，统一转字符串
                if hasattr(u, "toString"):
                    u = u.toString()
                if not u:
                    continue
                url = QUrl(u)
                if url.isLocalFile():
                    local_paths.append(url.toLocalFile())
            if local_paths:
                added = self._file_tray.add_paths(local_paths)
                # 提前生成图标并落盘缓存，避免 QML 渲染线程在 data() 里同步查询 shell 图标导致卡顿
                for p in added:
                    self._file_icon_provider.icon_url_for_path(p)
                self.logger.info(f"文件中转站新增 {len(added)} 项，共 {len(self._file_tray.paths)} 项")
        except Exception as e:
            self.logger.error(f"处理文件拖入失败: {e}", exc_info=True)

    def _on_file_drag_requested(self, index: int, is_copy: bool):
        """
        处理从托盘图标拖出文件：默认=复制，拖放过程中按修饰键切换复制/剪切.

        若拖动的条目正处于选中集，则批量拖出所有选中项；否则仅拖出该条目
        （并把选中收敛到该条目，符合资源管理器"拖动未选中项=先选中它"）。
        """
        try:
            paths = self._file_tray.paths
            if index < 0 or index >= len(paths):
                return

            selected = self._file_tray.selected_indexes()
            if index in selected and len(selected) > 1:
                drag_indices = selected
            else:
                drag_indices = [index]
                self._file_tray.select_only(index)

            drag_paths = [paths[i] for i in drag_indices if 0 <= i < len(paths)]
            if not drag_paths:
                return

            # 拖出前校验源位置：引用已失效的文件提醒用户并清理，不参与本次拖拽
            missing = [(i, paths[i]) for i in drag_indices
                       if 0 <= i < len(paths) and not os.path.exists(paths[i])]
            if missing:
                names = "\n".join(f"• {p}" for _, p in missing)
                QMessageBox.warning(
                    self.tray_root,
                    "文件源位置不存在",
                    f"以下文件/文件夹的原位置已不存在，已从中转站移除：\n\n{names}",
                )
                for i in sorted((i for i, _ in missing), reverse=True):
                    self._file_tray.remove_index(i)
                missing_paths = {p for _, p in missing}
                drag_paths = [p for p in drag_paths if p not in missing_paths]
                if not drag_paths:
                    return

            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(p) for p in drag_paths])

            drag = QDrag(self.tray_root)
            drag.setMimeData(mime)
            pixmap = self._file_icon_provider.drag_pixmap_for_path(drag_paths[0])
            if not pixmap.isNull():
                drag.setPixmap(pixmap)

            # 支持复制 + 移动，默认复制；拖放中按修饰键可动态切换
            drag.exec_(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
            # 拖出结束后按磁盘实际存在性清理：条目被"移动/剪切"到别处或拖入
            # 回收站后，源路径已不存在，此时从中转站移除其引用。复制到别处时
            # 源文件仍在，不会被误删。降序遍历避免删除时索引偏移。
            for i in sorted(drag_indices, reverse=True):
                if 0 <= i < len(paths) and not os.path.exists(paths[i]):
                    self._file_tray.remove_index(i)
        except Exception as e:
            self.logger.error(f"从托盘拖出文件失败: {e}", exc_info=True)

    def _on_file_remove_requested(self, index: int):
        """右键单击中转站某一项：将其从托盘移除（仅移除引用，不动磁盘原文件）。"""
        try:
            if self._file_tray.remove_index(index):
                self.logger.info(f"已从中转站移除第 {index} 项")
        except Exception as e:
            self.logger.error(f"从中转站移除文件失败: {e}", exc_info=True)

    def _on_tray_select_index(self, index: int, mode: int):
        """中转站点击选择：mode 0=单选替换 / 1=Ctrl切换 / 2=Shift连续。"""
        try:
            if mode == 1:
                self._file_tray.toggle_selected(index)
            elif mode == 2:
                self._file_tray.select_range_to(index)
            else:
                self._file_tray.select_only(index)
        except Exception as e:
            self.logger.debug(f"中转站选择失败: {e}")

    def _on_tray_marquee_selection(self, indices):
        """中转站空白框选：按命中的索引集合替换选中。"""
        try:
            if hasattr(indices, "toVariant"):
                indices = indices.toVariant()
            self._file_tray.select_indices(indices)
        except Exception as e:
            self.logger.debug(f"中转站框选失败: {e}")

    def _on_tray_clear_selection(self):
        """中转站空白点击：清空选中。"""
        try:
            self._file_tray.clear_selection()
        except Exception as e:
            self.logger.debug(f"中转站清空选中失败: {e}")

    def _on_file_tray_closed(self):
        """关闭中转站时清空所有暂存文件（仅移除引用，不动磁盘原文件），并淡出隐藏窗口。"""
        try:
            self._file_tray.clear()
            self.logger.info("中转站已关闭，已清空所有暂存文件")
        except Exception as e:
            self.logger.error(f"清空中转站失败: {e}", exc_info=True)
        self._fade_out_tray_window()

    # =========================================================================
    # 窗口拖动
    # =========================================================================

    def _on_title_drag_start(self, _x, _y):
        """
        标题栏拖动开始.
        """
        self._dragging = True
        self._drag_position = QCursor.pos() - self.root.pos()

    def _on_title_drag_move(self, _x, _y):
        """
        标题栏拖动移动.
        """
        if self._dragging and self._drag_position:
            self.root.move(QCursor.pos() - self._drag_position)

    def _on_title_drag_end(self):
        """
        标题栏拖动结束.
        """
        self._dragging = False
        self._drag_position = None

    # =========================================================================
    # 表情管理
    # =========================================================================

    def _get_emotion_asset_path(self, emotion_name: str) -> str:
        """
        获取表情资源文件路径，自动匹配常见后缀.
        """
        if emotion_name in self._emotion_cache:
            return self._emotion_cache[emotion_name]

        assets_dir = find_assets_dir()
        if not assets_dir:
            path = "😊"
        else:
            emotion_dir = assets_dir / "emojis"
            # 尝试查找表情文件，失败则回退到 neutral
            path = (
                str(self._find_emotion_file(emotion_dir, emotion_name))
                or str(self._find_emotion_file(emotion_dir, "neutral"))
                or "😊"
            )

        self._emotion_cache[emotion_name] = path
        return path

    def _find_emotion_file(self, emotion_dir: Path, name: str) -> Optional[Path]:
        """
        在指定目录查找表情文件.
        """
        for ext in self.EMOTION_EXTENSIONS:
            file_path = emotion_dir / f"{name}{ext}"
            if file_path.exists():
                return file_path
        return None

    # =========================================================================
    # 系统设置
    # =========================================================================

    def _setup_signal_handlers(self):
        """
        设置信号处理器（Ctrl+C）
        """
        try:
            signal.signal(
                signal.SIGINT,
                lambda *_: QTimer.singleShot(0, self._quit_application),
            )
        except Exception as e:
            self.logger.warning(f"设置信号处理器失败: {e}")

    def _setup_activation_handler(self):
        """
        设置应用激活处理器（macOS Dock 图标点击恢复窗口）
        """
        try:
            import platform

            if platform.system() != "Darwin":
                return

            self.app.applicationStateChanged.connect(self._on_application_state_changed)
            self.logger.debug("已设置应用激活处理器（macOS Dock 支持）")
        except Exception as e:
            self.logger.warning(f"设置应用激活处理器失败: {e}")

    def _on_application_state_changed(self, state):
        """
        应用状态变化处理（macOS Dock 点击时恢复窗口）
        """
        if state == Qt.ApplicationActive and self.root and not self.root.isVisible():
            QTimer.singleShot(0, self._show_main_window)

    def _setup_system_tray(self):
        """
        设置系统托盘.
        """
        if os.getenv("XIAOZHI_DISABLE_TRAY") == "1":
            self.logger.warning("已通过环境变量禁用系统托盘 (XIAOZHI_DISABLE_TRAY=1)")
            return

        try:
            from src.views.components.system_tray import SystemTray

            self.system_tray = SystemTray(self.root)

            # 连接托盘信号（使用 QTimer 确保主线程执行）
            tray_signals = {
                "show_window_requested": self._show_main_window,
                "settings_requested": self._on_settings_button_click,
                "quit_requested": self._quit_application,
            }

            for signal_name, handler in tray_signals.items():
                getattr(self.system_tray, signal_name).connect(
                    lambda h=handler: QTimer.singleShot(0, h)
                )

        except Exception as e:
            self.logger.error(f"初始化系统托盘组件失败: {e}", exc_info=True)

    # =========================================================================
    # 窗口控制
    # =========================================================================

    def _show_main_window(self):
        """
        显示主窗口.
        """
        if not self.root:
            return

        if self.root.isMinimized():
            self.root.showNormal()
        if not self.root.isVisible():
            self.root.show()
        self.root.activateWindow()
        self.root.raise_()

        # 如果处于完全隐藏模式（1x1 透明区域），退出隐藏，恢复为胶囊显示
        root_object = self.qml_widget.rootObject() if self.qml_widget else None
        if root_object and root_object.property("viewMode") == "hidden":
            try:
                root_object.exitHiddenMode()
            except Exception as e:
                self.logger.debug(f"退出隐藏模式失败: {e}")

    def _minimize_window(self):
        """
        最小化窗口.
        """
        if self.root:
            self.root.showMinimized()

    def _quit_application(self):
        """
        退出应用程序.
        """
        self.logger.info("开始退出应用程序...")
        self._running = False

        if self.system_tray:
            self.system_tray.hide()

        try:
            from src.application import Application

            app = Application.get_instance()
            if not app:
                QApplication.quit()
                return

            loop = asyncio.get_event_loop()
            if not loop.is_running():
                QApplication.quit()
                return

            # 创建关闭任务并设置超时
            shutdown_task = asyncio.create_task(app.shutdown())

            def on_shutdown_complete(task):
                if not task.cancelled() and task.exception():
                    self.logger.error(f"应用程序关闭异常: {task.exception()}")
                else:
                    self.logger.info("应用程序正常关闭")
                # 不在这里 quit：shutdown() 完成时 _shutdown_event 已置位，
                # Application.run() 会自行返回并让主函数正常退出。
                # 若此处立即 QApplication.quit()，会在 run() 收尾（finally await
                # shutdown）之前提前停掉 qasync 循环，触发
                # "Event loop stopped before Future completed"。
                # quit 仅在关闭超时（force_quit）或异常兜底时使用。

            def force_quit():
                if not shutdown_task.done():
                    self.logger.warning("关闭超时，强制退出")
                    shutdown_task.cancel()
                QApplication.quit()

            shutdown_task.add_done_callback(on_shutdown_complete)
            QTimer.singleShot(self.QUIT_TIMEOUT_MS, force_quit)

        except Exception as e:
            self.logger.error(f"关闭应用程序失败: {e}")
            QApplication.quit()

    def _closeEvent(self, event):
        """
        处理窗口关闭事件.
        """
        # 系统关机/注销时直接退出，不再最小化到托盘，避免阻止关机
        if self._system_shutdown:
            self.logger.info("系统关机中：关闭窗口")
            QTimer.singleShot(0, self._quit_application)
            event.accept()
            return

        # 如果系统托盘可用，最小化到托盘
        if self.system_tray and (
            getattr(self.system_tray, "is_available", lambda: False)()
            or getattr(self.system_tray, "is_visible", lambda: False)()
        ):
            self.logger.info("关闭窗口：最小化到托盘")
            QTimer.singleShot(0, self.root.hide)
            event.ignore()
        else:
            QTimer.singleShot(0, self._quit_application)
            event.accept()
