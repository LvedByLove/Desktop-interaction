# -*- coding: utf-8 -*-
"""
输入状态显示窗口 - 独立的顶层窗口，用于显示输入区域.
"""

import asyncio
import os
from pathlib import Path

from PyQt5.QtCore import Qt, QUrl, pyqtSignal, pyqtSlot, QObject
from PyQt5.QtQuickWidgets import QQuickWidget
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget

from src.display.gui_display_model import GuiDisplayModel


class InputDisplay(QObject):
    """
    输入状态显示窗口 - 独立的顶层窗口.
    """

    WINDOW_SIZE = (520, 54)

    # 发送文本信号
    send_text_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.app = None
        self.window = None
        self.qml_widget = None
        self.display_model = GuiDisplayModel()
        self._visible = False

    async def start(self):
        """
        启动输入窗口.
        """
        self._configure_environment()
        self._create_window()
        self._load_qml()
        self._setup_signals()

    def _configure_environment(self):
        """
        配置环境.
        """
        self.app = QApplication.instance()
        if self.app is None:
            raise RuntimeError("QApplication 未找到，请确保在 qasync 环境中运行")

    def _create_window(self):
        """
        创建完全独立的顶层输入显示窗口.
        """
        self.window = QWidget()
        self.window.setWindowTitle("输入状态显示")

        # 设置窗口标志：无边框、独立窗口
        self.window.setWindowFlags(
            Qt.FramelessWindowHint |  # 无边框
            Qt.WindowStaysOnTopHint |  # 保持在顶层，确保触发后可见
            Qt.Tool  # 工具窗口，不显示在任务栏
        )

        # 设置透明背景
        self.window.setAttribute(Qt.WA_TranslucentBackground)

        # 设置窗口尺寸
        self.window.resize(*self.WINDOW_SIZE)

        # 设置初始位置（屏幕底部居中）
        self._set_initial_position()

    def _set_initial_position(self):
        """
        设置初始位置 - 屏幕底部居中.
        """
        desktop = QApplication.desktop()
        screen_rect = desktop.availableGeometry()

        # 底部居中，距离底部 20 像素
        x = (screen_rect.width() - self.WINDOW_SIZE[0]) // 2
        y = screen_rect.height() - self.WINDOW_SIZE[1] - 20

        self.window.move(x, y)

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
        qml_file = Path(__file__).parent / "input_display.qml"
        if not qml_file.exists():
            raise FileNotFoundError(f"QML文件不存在: {qml_file}")

        self.qml_widget.setSource(QUrl.fromLocalFile(str(qml_file)))

        # 设置为窗口的中央 widget
        layout = QVBoxLayout(self.window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.qml_widget)

    def _setup_signals(self):
        """
        设置信号连接.
        """
        root_object = self.qml_widget.rootObject()
        if root_object:
            root_object.sendButtonClicked.connect(self._on_send_button_clicked)
            root_object.hideWindow.connect(self.hide)

    @pyqtSlot(str)
    def _on_send_button_clicked(self, text):
        """
        发送按钮点击处理.
        """
        self.send_text_signal.emit(text)

    def show(self):
        """
        显示窗口.
        """
        if self.window:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
            
            # 让 QML 中的 textInput 获得焦点
            if self.qml_widget:
                root_object = self.qml_widget.rootObject()
                if root_object:
                    # 通过 findChild 查找 textInput
                    text_input = root_object.findChild(QObject, "textInput")
                    if text_input:
                        text_input.setFocus(True)
            
            self._visible = True

    def hide(self):
        """
        隐藏窗口.
        """
        if self.window:
            self.window.hide()
            self._visible = False

    def toggle_visibility(self):
        """
        切换窗口可见性.
        """
        if self._visible:
            self.hide()
        else:
            self.show()

    def set_position(self, x, y):
        """
        设置窗口位置.
        """
        if self.window:
            self.window.move(x, y)

    def is_visible(self):
        """
        获取窗口可见状态.
        """
        return self._visible