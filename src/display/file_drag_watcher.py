# -*- coding: utf-8 -*-
"""
全局拖拽检测 - 用于文件中转站的"主动唤醒"。

通过 pynput 监听鼠标左键按下 + 移动，估算一次可能的拖拽的开始/结束。
本模块只做"疑似拖拽"的粗检测：拖拽开始 → 显示 OLE 隐形捕捉窗口，
拖拽结束 → 隐藏之。无法在鼠标层面区分拖的是文件还是文字，
最终是否唤醒圆球由 ole_drop_catcher 的 CF_HDROP 判断（精确关卡）决定。
"""

from PyQt5.QtCore import QObject, pyqtSignal

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# 判定为拖拽的最小累计位移（曼哈顿距离，像素），避免普通点击误触发
DRAG_THRESHOLD = 8


class FileDragWatcher(QObject):
    """监听全局鼠标左键按下 + 拖动，估算疑似拖拽的开始/结束。

    dragStarted 不再直接唤醒圆球，只用于显示 OLE 捕捉窗口；
    显示隐形窗口零代价（拖拽期间鼠标被拖放源捕获），误判无害。
    """

    dragStarted = pyqtSignal()
    dragEnded = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener = None
        self._running = False
        # 状态机：Idle / Pressed / Dragging
        self._pressed = False
        self._dragging = False
        self._start_x = 0
        self._start_y = 0

    def start(self):
        """启动监听（在后台线程中运行）。"""
        if self._running:
            return
        try:
            from pynput import mouse
        except Exception as e:
            logger.error(f"未安装 pynput 库，无法启用拖拽检测: {e}")
            return

        self._running = True
        self._pressed = False
        self._dragging = False
        self._listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click
        )
        try:
            self._listener.start()
            logger.debug("全局拖拽检测已启动")
        except Exception as e:
            self._running = False
            self._listener = None
            logger.error(f"启动拖拽监听失败: {e}")

    def stop(self):
        """停止监听并复位状态机。"""
        self._running = False
        self._pressed = False
        self._dragging = False
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    # --- pynput 回调（后台线程） ---

    def _on_click(self, x, y, button, pressed):
        from pynput.mouse import Button

        if button != Button.left:
            return
        if pressed:
            self._pressed = True
            self._dragging = False
            self._start_x = x
            self._start_y = y
        else:
            was_dragging = self._dragging
            self._pressed = False
            self._dragging = False
            if was_dragging:
                # 跨线程发射：Qt 会自动以队列方式投递到主线程
                self.dragEnded.emit()

    def _on_move(self, x, y):
        if not self._pressed or self._dragging:
            return
        if abs(x - self._start_x) + abs(y - self._start_y) < DRAG_THRESHOLD:
            return
        self._dragging = True
        self.dragStarted.emit()