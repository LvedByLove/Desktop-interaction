# -*- coding: utf-8 -*-
"""
GUI 显示模块 - 使用 QML 实现.
"""

import asyncio
import ctypes
import os
import signal
from abc import ABCMeta
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QObject, Qt, QTimer, QUrl, QPropertyAnimation, QRect, pyqtProperty, QEvent
from PyQt5.QtGui import QCursor, QFont
from PyQt5.QtQuickWidgets import QQuickWidget
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget

from src.display.base_display import BaseDisplay
from src.display.gui_display_model import GuiDisplayModel
from src.utils.resource_finder import find_assets_dir


# 创建兼容的元类
class CombinedMeta(type(QObject), ABCMeta):
    pass


class GuiDisplay(BaseDisplay, QObject, metaclass=CombinedMeta):
    """GUI 显示类 - 基于 QML 的现代化胶囊状界面"""

    # 常量定义
    EMOTION_EXTENSIONS = (".gif", ".png", ".jpg", ".jpeg", ".webp")
    # 胶囊状界面的默认尺寸（圆形状态）
    CAPSULE_SIZE = (36, 36)
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

        # TTS 一体化显示状态
        self._tts_hide_timer = None
        self._fullscreen_check_timer = None
        self._auto_collapsed_for_fullscreen = False

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

        root_object = self.qml_widget.rootObject() if self.qml_widget else None
        if root_object:
            root_object.hideTts()

    async def update_tts_text(self, text: str):
        """
        更新主胶囊 TTS 文本.
        """
        self.display_model.update_text(text)

    def set_music_lyrics_active(self, active: bool):
        """
        设置当前 TTS 区域是否由音乐歌词占用.
        """
        self.display_model.set_music_lyrics_active(active)
        if not active:
            self._auto_collapsed_for_fullscreen = False
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
        if self._tts_hide_timer:
            self._tts_hide_timer.cancel()
            self._tts_hide_timer = None
        if self.app:
            self.app.removeEventFilter(self)
        if self.system_tray:
            self.system_tray.hide()
        if self.root:
            self.root.close()

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

        # 右上角位置：右边缘留出一些边距，顶部对齐
        margin_right = 940
        margin_top = 5
        x = screen_width - self.CAPSULE_SIZE[0] - margin_right
        y = margin_top

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

    def _setup_interactions(self):
        """
        设置交互（信号、托盘）
        """
        self._connect_qml_signals()

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
        self._start_fullscreen_check_timer()

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
        }

        # 批量连接信号
        all_signals = {**button_signals, **titlebar_signals, **window_signals, **system_signals}
        for signal_name, handler in all_signals.items():
            try:
                getattr(root_object, signal_name).connect(handler)
            except AttributeError:
                self.logger.debug(f"信号 {signal_name} 不存在（可能是可选功能）")

        self.logger.debug("QML 信号连接设置完成")

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

            settings_window = SettingsWindow(self.root)
            settings_window.exec_()
        except Exception as e:
            self.logger.error(f"打开设置窗口失败: {e}", exc_info=True)

    def _start_fullscreen_check_timer(self):
        if os.name != "nt" or self._fullscreen_check_timer:
            return
        self._fullscreen_check_timer = QTimer(self.root)
        self._fullscreen_check_timer.setInterval(1200)
        self._fullscreen_check_timer.timeout.connect(self._check_foreground_fullscreen)
        self._fullscreen_check_timer.start()

    def _check_foreground_fullscreen(self):
        if not self.display_model.musicLyricsActive:
            self._auto_collapsed_for_fullscreen = False
            return

        try:
            fullscreen = self._is_other_app_fullscreen()
            if fullscreen and not self.display_model.musicLyricsCollapsed:
                self._auto_collapsed_for_fullscreen = True
                self.set_music_lyrics_collapsed(True)
            elif (
                not fullscreen
                and self._auto_collapsed_for_fullscreen
                and self.display_model.musicLyricsCollapsed
            ):
                self._auto_collapsed_for_fullscreen = False
                self.set_music_lyrics_collapsed(False)
        except Exception as e:
            self.logger.debug(f"检测前台全屏应用失败: {e}")

    def _is_other_app_fullscreen(self) -> bool:
        if os.name != "nt" or not self.root:
            return False

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False

        try:
            own_hwnd = int(self.root.winId())
            if int(foreground) == own_hwnd:
                return False
        except Exception:
            pass

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong),
            ]

        user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
        user32.GetWindowRect.restype = ctypes.c_bool
        user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = ctypes.c_bool

        window_rect = RECT()
        if not user32.GetWindowRect(foreground, ctypes.byref(window_rect)):
            return False

        monitor = user32.MonitorFromWindow(foreground, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor:
            return False

        monitor_info = MONITORINFO()
        monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
            return False

        monitor_rect = monitor_info.rcMonitor
        tolerance = 2
        return (
            window_rect.left <= monitor_rect.left + tolerance
            and window_rect.top <= monitor_rect.top + tolerance
            and window_rect.right >= monitor_rect.right - tolerance
            and window_rect.bottom >= monitor_rect.bottom - tolerance
        )

    def _on_hidden_mode_changed(self, is_hidden: bool):
        """
        处理隐藏模式变化信号.
        进入隐藏模式：窗口移动到屏幕顶部
        退出隐藏模式：窗口保持在当前隐藏位置，恢复正常大小
        """
        try:
            if is_hidden:
                self.logger.info("进入隐藏模式，窗口移动到屏幕顶部")
                # 获取当前窗口位置（应该在右上角）
                current_x = self.root.x()
                current_y = self.root.y()
                # 移动到屏幕顶部（保持X坐标不变，Y变为0加上边距）
                margin_top = 0
                self.root.move(current_x, margin_top)
            else:
                self.logger.info("退出隐藏模式，窗口恢复正常大小")
                # 恢复正常大小后，窗口已经在顶部位置
                # 需要调整Y坐标回到原来的位置（屏幕顶部）
                margin_top = 5
                current_x = self.root.x()
                self.root.move(current_x, margin_top)
        except Exception as e:
            self.logger.error(f"处理隐藏模式变化失败: {e}", exc_info=True)

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
            if self.display_model.musicLyricsActive:
                new_y = 0 if self.display_model.musicLyricsCollapsed else 5

            # 确保窗口不超出屏幕边界
            desktop = QApplication.desktop()
            screen_rect = desktop.availableGeometry()
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
                QApplication.quit()

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
