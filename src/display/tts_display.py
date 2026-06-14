# -*- coding: utf-8 -*-
"""
TTS 显示窗口 - 专门显示语音播报状态
语音播报期间显示，其他时间隐藏
这是一个完全独立的顶层窗口，与主窗口分开
"""

import asyncio
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, Qt, QUrl, pyqtSlot
from PyQt5.QtQuickWidgets import QQuickWidget
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget

from src.display.gui_display_model import GuiDisplayModel
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class TtsDisplay(QObject):
    """TTS 显示窗口类 - 语音播报期间显示，其他时间隐藏"""

    # 窗口尺寸
    WINDOW_SIZE = (320, 60)

    def __init__(self):
        super().__init__()

        # Qt 组件
        self.app = None
        self.window = None
        self.qml_widget = None

        # 数据模型
        self.display_model = GuiDisplayModel()

        # 状态管理
        self._is_visible = False
        self._running = True
        
        # 延迟隐藏定时器（TTS结束后延迟3秒隐藏）
        self._delay_hide_timer = None
        
        # 隐藏完成回调
        self._hide_callback = None

    async def start(self):
        """
        启动 TTS 显示窗口.
        """
        try:
            self._configure_environment()
            self._create_window()
            self._load_qml()
            await self._finalize_startup()
        except Exception as e:
            logger.error(f"TTS显示窗口启动失败: {e}", exc_info=True)
            raise

    def _configure_environment(self):
        """
        配置环境.
        """
        self.app = QApplication.instance()
        if self.app is None:
            raise RuntimeError("QApplication 未找到，请确保在 qasync 环境中运行")

    def _create_window(self):
        """
        创建完全独立的顶层 TTS 显示窗口.
        """
        self.window = QWidget()
        self.window.setWindowTitle("TTS状态显示")
        
        # 设置窗口标志：无边框、保持在顶层、独立窗口
        self.window.setWindowFlags(
            Qt.FramelessWindowHint |          # 无边框
            Qt.WindowStaysOnTopHint |        # 保持在顶层
            Qt.Tool |                        # 工具窗口样式
            Qt.X11BypassWindowManagerHint    # 绕过窗口管理器（确保始终可见）
        )
        
        # 设置透明背景
        self.window.setAttribute(Qt.WA_TranslucentBackground)

        # 设置窗口尺寸
        self.window.resize(*self.WINDOW_SIZE)

        # 设置初始位置（屏幕底部居中）
        self._set_initial_position()

        # 设置窗口关闭事件
        self.window.closeEvent = self._closeEvent

        logger.info(f"TTS窗口创建完成，位置: ({self.window.x()}, {self.window.y()}), 大小: {self.WINDOW_SIZE}")

    def _set_initial_position(self):
        """
        设置窗口初始位置到屏幕顶部居中
        """
        desktop = QApplication.desktop()
        screen_rect = desktop.availableGeometry()
        screen_width = screen_rect.width()
        screen_height = screen_rect.height()

        # 顶部居中位置，留出一些边距
        margin_top = 5
        x = (screen_width - self.WINDOW_SIZE[0]) // 2
        y = margin_top

        self.window.move(x, y)
        logger.debug(f"TTS窗口位置设置为: ({x}, {y})")

    def _load_qml(self):
        """
        加载 QML 界面.
        """
        self.qml_widget = QQuickWidget()
        self.qml_widget.setResizeMode(QQuickWidget.SizeRootObjectToView)
        # 设置透明背景
        self.qml_widget.setClearColor(Qt.transparent)

        # 注册数据模型到 QML 上下文
        qml_context = self.qml_widget.rootContext()
        qml_context.setContextProperty("displayModel", self.display_model)

        # 加载 QML 文件
        qml_file = Path(__file__).parent / "tts_display.qml"
        if not qml_file.exists():
            raise FileNotFoundError(f"QML文件不存在: {qml_file}")
        
        self.qml_widget.setSource(QUrl.fromLocalFile(str(qml_file)))
        
        # 检查QML加载是否成功
        if self.qml_widget.status() != QQuickWidget.Ready:
            logger.error(f"QML加载失败，状态: {self.qml_widget.status()}")
        else:
            logger.info("QML加载成功")

        # 设置为窗口的中央 widget
        layout = QVBoxLayout(self.window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.qml_widget)

    async def _finalize_startup(self):
        """
        完成启动流程.
        """
        # 默认隐藏窗口
        logger.info("TTS显示窗口启动，默认隐藏")
        self._is_visible = False
        self.window.hide()

    async def show(self, text: str = ""):
        """
        显示 TTS 窗口.
        
        Args:
            text: 要显示的 TTS 文本
        """
        # 取消延迟隐藏定时器（如果存在）
        if self._delay_hide_timer:
            self._delay_hide_timer.cancel()
            self._delay_hide_timer = None
            logger.debug("延迟隐藏定时器已取消")
        
        if text:
            await self.update_text(text)
            logger.info(f"TTS显示窗口即将显示，文本: {text}")
        
        self._is_visible = True
        
        if self.window:
            # 确保窗口在最前面
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
            logger.info(f"TTS显示窗口已显示，位置: ({self.window.x()}, {self.window.y()})")
        else:
            logger.error("TTS窗口对象未创建")

    async def hide(self):
        """
        隐藏 TTS 窗口（延迟3秒后隐藏）.
        """
        self._is_visible = False
        
        # 取消之前的延迟隐藏定时器（如果存在）
        if self._delay_hide_timer:
            self._delay_hide_timer.cancel()
            self._delay_hide_timer = None
        
        # 延迟3秒后隐藏窗口
        loop = asyncio.get_event_loop()
        self._delay_hide_timer = loop.call_later(3, self._do_hide)
        logger.info("TTS显示窗口将在3秒后隐藏")
    
    def _do_hide(self):
        """
        实际执行隐藏操作.
        """
        self._delay_hide_timer = None
        
        if self.window:
            self.window.hide()
            logger.info("TTS显示窗口已隐藏")
        
        # 调用隐藏完成回调
        if self._hide_callback:
            try:
                self._hide_callback()
            except Exception as e:
                logger.error(f"隐藏回调执行失败: {e}")
    
    def set_hide_callback(self, callback):
        """
        设置隐藏完成后的回调函数.
        """
        self._hide_callback = callback

    async def update_text(self, text: str):
        """
        更新 TTS 文本.
        """
        self.display_model.update_text(text)
        logger.debug(f"TTS文本已更新: {text}")

    async def close(self):
        """
        关闭窗口处理.
        """
        self._running = False
        if self.window:
            self.window.close()

    def _closeEvent(self, event):
        """
        窗口关闭事件.
        """
        self._running = False
        event.accept()
        logger.info("TTS显示窗口已关闭")