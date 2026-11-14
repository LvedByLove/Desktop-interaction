"""仿 py-xiaozhi XSwitch 的胶囊开关.

继承 QCheckBox，保留 isChecked/setChecked/stateChanged/toggled 等全部常用 API，
因此可以直接替换原 QCheckBox，无需改业务代码。
"""

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Property,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QCheckBox, QSizePolicy, QWidget

# 与 py-xiaozhi Theme 保持一致
_TRACK_CHECKED = QColor("#2563eb")
_TRACK_UNCHECKED = QColor("#e5e7eb")
_TRACK_CHECKED_DISABLED = QColor("#93c5fd")
_TRACK_UNCHECKED_DISABLED = QColor("#f3f4f6")
_THUMB_COLOR = QColor("#ffffff")
_SHADOW_COLOR = QColor(0, 0, 0, 28)
_TEXT_COLOR = QColor("#111827")
_TEXT_DISABLED = QColor("#9ca3af")


class ToggleSwitch(QCheckBox):
    """胶囊开关：轨道 44×24，滑块 20，切换时 200ms 动画."""

    TRACK_WIDTH = 44
    TRACK_HEIGHT = 24
    THUMB_SIZE = 20
    THUMB_MARGIN = 2
    TEXT_SPACING = 8

    def __init__(self, text: str = "", parent=None):
        # uic 加载自定义控件时只会传 parent，兼容 ToggleSwitch(parent)
        if isinstance(text, QWidget):
            parent = text
            text = ""
        super().__init__(text, parent)
        self._thumb_position = 1.0 if self.isChecked() else 0.0
        self._hovered = False

        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self._anim = QPropertyAnimation(self, b"thumbPosition", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate_thumb)

    # ---- 滑块位置（供 QPropertyAnimation 使用）----
    def _get_thumb_position(self) -> float:
        return self._thumb_position

    def _set_thumb_position(self, value: float) -> None:
        self._thumb_position = max(0.0, min(1.0, float(value)))
        self.update()

    thumbPosition = Property(float, _get_thumb_position, _set_thumb_position)

    def _animate_thumb(self, checked: bool) -> None:
        if not self.isVisible():
            # 未显示时直接到位，避免首次打开设置面板时播放动画
            self._set_thumb_position(1.0 if checked else 0.0)
            return
        self._anim.stop()
        self._anim.setStartValue(self._thumb_position)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    # ---- 尺寸 ----
    def sizeHint(self) -> QSize:
        text_width = (
            self.fontMetrics().horizontalAdvance(self.text()) if self.text() else 0
        )
        spacing = self.TEXT_SPACING if text_width else 0
        width = 4 + self.TRACK_WIDTH + spacing + text_width
        height = max(self.TRACK_HEIGHT, self.fontMetrics().height()) + 4
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        # 点击文字区域也能切换
        return self.rect().contains(pos)

    # ---- 交互状态 ----
    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.update()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.update()

    def changeEvent(self, event):
        super().changeEvent(event)
        self.update()

    # ---- 绘制 ----
    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)

            track_x = 2.0
            track_y = (self.height() - self.TRACK_HEIGHT) / 2.0
            track_rect = QRectF(
                track_x, track_y, self.TRACK_WIDTH, self.TRACK_HEIGHT
            )

            checked = self.isChecked()
            enabled = self.isEnabled()

            if not enabled:
                track_color = (
                    _TRACK_CHECKED_DISABLED if checked else _TRACK_UNCHECKED_DISABLED
                )
            else:
                track_color = _TRACK_CHECKED if checked else _TRACK_UNCHECKED

            # 轨道
            painter.setPen(Qt.NoPen)
            painter.setBrush(track_color)
            painter.drawRoundedRect(
                track_rect, self.TRACK_HEIGHT / 2, self.TRACK_HEIGHT / 2
            )

            # 滑块
            thumb_size = self.THUMB_SIZE
            travel = self.TRACK_WIDTH - thumb_size - self.THUMB_MARGIN * 2
            thumb_x = track_x + self.THUMB_MARGIN + travel * self._thumb_position

            scale = 1.0
            if enabled and self.isDown():
                scale = 0.9
            elif enabled and self._hovered:
                scale = 1.05

            radius = (thumb_size * scale) / 2.0
            center_x = thumb_x + thumb_size / 2.0
            center_y = track_y + self.TRACK_HEIGHT / 2.0

            # 轻微阴影
            painter.setBrush(_SHADOW_COLOR)
            painter.drawEllipse(
                QRectF(
                    center_x - radius - 1,
                    center_y - radius - 1,
                    radius * 2 + 2,
                    radius * 2 + 2,
                )
            )

            painter.setBrush(_THUMB_COLOR)
            painter.drawEllipse(
                QRectF(
                    center_x - radius,
                    center_y - radius,
                    radius * 2,
                    radius * 2,
                )
            )

            # 文字
            text = self.text()
            if text:
                painter.setPen(_TEXT_COLOR if enabled else _TEXT_DISABLED)
                metrics = self.fontMetrics()
                text_x = track_x + self.TRACK_WIDTH + self.TEXT_SPACING
                baseline = (self.height() - metrics.height()) / 2.0 + metrics.ascent()
                painter.drawText(QPointF(text_x, baseline), text)
        finally:
            painter.end()
