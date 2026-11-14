"""
区域截图覆盖层：全屏遮罩 + 鼠标拖拽选区（仿 QQ/微信截图交互）。

流程：先抓取虚拟屏幕原图作为背景，再弹出无边框置顶全屏遮罩窗口；
用户拖拽划出选区（Esc / 右键取消），返回选区矩形，由调用方裁剪并交给 AI 分析。
"""

import ctypes

from PIL import Image, ImageGrab
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class RegionCaptureOverlay(QWidget):
    """
    全屏无边框半透明选区覆盖层.
    """

    # 选区完成（rect 为 None 表示取消）
    _selection_done = Signal(object)

    def __init__(self, background: Image.Image):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._background = background
        self._background_pixmap = self._pil_to_pixmap(background)
        self._start_pos: QPoint | None = None
        self._current_rect = QRect()
        self._result_rect = None

        # 覆盖整个虚拟屏幕（含多显示器）
        user32 = ctypes.windll.user32
        self._virtual_left = user32.GetSystemMetrics(76)
        self._virtual_top = user32.GetSystemMetrics(77)
        self._virtual_width = user32.GetSystemMetrics(78)
        self._virtual_height = user32.GetSystemMetrics(79)
        self.setGeometry(
            self._virtual_left,
            self._virtual_top,
            self._virtual_width,
            self._virtual_height,
        )

        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    @staticmethod
    def _pil_to_pixmap(image: Image.Image) -> QPixmap:
        """PIL Image 转 QPixmap（RGB）。"""
        if image.mode != "RGB":
            image = image.convert("RGB")
        data = image.tobytes()
        qimage = QImage(data, image.width, image.height, image.width * 3, QImage.Format_RGB888)
        return QPixmap.fromImage(qimage)

    # ---------------- 事件处理 ----------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._start_pos = event.pos()
            self._current_rect = QRect(self._start_pos, self._start_pos)
            self.update()
        elif event.button() == Qt.RightButton:
            self._finish(None)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start_pos is None:
            return
        # 归一化矩形（支持任意方向拖拽）
        self._current_rect = QRect(self._start_pos, event.pos()).normalized()
        self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._start_pos is not None:
            rect = QRect(self._start_pos, event.pos()).normalized()
            self._start_pos = None
            if rect.width() < 2 or rect.height() < 2:
                self._finish(None)
            else:
                self._finish(rect)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._finish(None)
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._background_pixmap)

        # 选中区域外的四块遮罩
        mask_color = QColor(0, 0, 0, 140)
        if self._current_rect.isEmpty():
            painter.fillRect(self.rect(), mask_color)
        else:
            r = self._current_rect
            painter.fillRect(self.rect().x(), self.rect().y(), self.rect().width(), r.y(), mask_color)
            painter.fillRect(
                self.rect().x(), r.bottom(), self.rect().width(),
                self.rect().height() - (r.bottom() - self.rect().y()), mask_color,
            )
            painter.fillRect(self.rect().x(), r.y(), r.x(), r.height(), mask_color)
            painter.fillRect(r.right(), r.y(), self.rect().right() - r.right() + 1, r.height(), mask_color)

            # 选区边框 + 尺寸提示
            painter.setPen(QPen(QColor(0, 150, 255), 1, Qt.SolidLine))
            painter.drawRect(r)
            painter.setPen(QColor(255, 255, 255))
            size_text = f"{r.width()} x {r.height()}"
            painter.drawText(r.bottomLeft() + QPoint(2, 14), size_text)

        painter.end()
        super().paintEvent(event)

    # ---------------- 结果 ----------------
    def _finish(self, rect):
        if self._result_rect is not None:
            return
        self._result_rect = rect
        self._selection_done.emit(rect)

    # ---------------- 供调用方使用的快捷方法 ----------------
    @staticmethod
    def grab_virtual_screen() -> Image.Image:
        """抓取整个虚拟屏幕的原图（与覆盖层几何对齐）。"""
        return ImageGrab.grab(all_screens=True)
