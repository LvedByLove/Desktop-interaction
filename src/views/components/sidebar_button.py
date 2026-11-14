"""左侧导航按钮（仿 Tailwind 设置页的 sidebar item）."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy

_BG_ACTIVE = QColor("#ffffff")
_BG_HOVER = QColor("#f3f4f6")
_BORDER_ACTIVE = QColor("#e5e7eb")
_TEXT_ACTIVE = QColor("#111827")
_TEXT_INACTIVE = QColor("#6b7280")
_TEXT_HOVER = QColor("#374151")

_ICON_ACTIVE = QColor("#111827")
_ICON_INACTIVE = QColor("#6b7280")
_ICON_HOVER = QColor("#374151")


def _draw_icon(painter: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    """用线条绘制 18x18 导航图标."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()

    def p(px: float, py: float) -> QPointF:
        return QPointF(x + px * w / 18.0, y + py * h / 18.0)

    def r(px: float, py: float, pw: float, ph: float) -> QRectF:
        return QRectF(
            x + px * w / 18.0,
            y + py * h / 18.0,
            pw * w / 18.0,
            ph * h / 18.0,
        )

    if kind == "sliders":
        for line_y, knob_x in ((4.5, 6.0), (9.0, 12.0), (13.5, 8.0)):
            painter.drawLine(p(3, line_y), p(15, line_y))
            painter.setBrush(color)
            painter.drawEllipse(p(knob_x, line_y), 1.8, 1.8)
            painter.setBrush(Qt.NoBrush)
    elif kind == "grid":
        for gx, gy in ((2.5, 2.5), (10, 2.5), (2.5, 10), (10, 10)):
            painter.drawRoundedRect(r(gx, gy, 5.5, 5.5), 1.5, 1.5)
    elif kind == "mic":
        painter.drawRoundedRect(
            r(6.5, 2, 5, 9), 2.5, 2.5
        )
        path = QPainterPath()
        path.moveTo(p(4.5, 8.5))
        path.cubicTo(p(4.5, 14), p(13.5, 14), p(13.5, 8.5))
        painter.drawPath(path)
        painter.drawLine(p(9, 14), p(9, 16.5))
    elif kind == "camera":
        painter.drawRoundedRect(
            r(2, 5, 14, 10), 2.5, 2.5
        )
        painter.drawEllipse(p(9, 10), 3.0, 3.0)
        painter.drawLine(p(6, 5), p(7, 2.5))
        painter.drawLine(p(7, 2.5), p(11, 2.5))
        painter.drawLine(p(11, 2.5), p(12, 5))
    elif kind == "speaker":
        path = QPainterPath()
        path.moveTo(p(3, 7))
        path.lineTo(p(7, 7))
        path.lineTo(p(11.5, 3))
        path.lineTo(p(11.5, 15))
        path.lineTo(p(7, 11))
        path.lineTo(p(3, 11))
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawArc(r(11, 5, 5, 8), -60 * 16, 120 * 16)
    elif kind == "keyboard":
        painter.drawRoundedRect(
            r(1.5, 5, 15, 9), 2.0, 2.0
        )
        for kx, ky in ((5, 8), (9, 8), (13, 8), (7, 11), (11, 11)):
            painter.setBrush(color)
            painter.drawEllipse(p(kx, ky), 0.8, 0.8)
            painter.setBrush(Qt.NoBrush)
    elif kind == "music":
        painter.drawLine(p(7, 12), p(7, 3.5))
        painter.drawLine(p(7, 3.5), p(14, 2))
        painter.drawLine(p(14, 2), p(14, 10.5))
        painter.setBrush(color)
        painter.drawEllipse(p(5.2, 12.5), 2.0, 1.6)
        painter.drawEllipse(p(12.2, 11), 2.0, 1.6)
        painter.setBrush(Qt.NoBrush)
    elif kind == "database":
        painter.drawEllipse(r(3, 3, 12, 4))
        painter.drawLine(p(3, 5), p(3, 13))
        painter.drawLine(p(15, 5), p(15, 13))
        painter.drawArc(r(3, 9.5, 12, 7), 180 * 16, 180 * 16)
        painter.drawArc(r(3, 7.2, 12, 7), 180 * 16, 180 * 16)
    else:
        painter.drawEllipse(p(9, 9), 6.5, 6.5)

    painter.restore()


class SidebarButton(QAbstractButton):
    """40px 高、图标 + 文本的左侧导航项."""

    def __init__(self, text: str, icon_kind: str, parent=None):
        super().__init__(parent)
        self._icon_kind = icon_kind
        self._hovered = False
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(14)
        self.setFont(font)

    def sizeHint(self) -> QSize:
        return QSize(210, 40)

    def minimumSizeHint(self) -> QSize:
        return QSize(120, 40)

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

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)

            active = self.isChecked()
            if active:
                bg = _BG_ACTIVE
                text_color = _TEXT_ACTIVE
                icon_color = _ICON_ACTIVE
            elif self._hovered:
                bg = _BG_HOVER
                text_color = _TEXT_HOVER
                icon_color = _ICON_HOVER
            else:
                bg = None
                text_color = _TEXT_INACTIVE
                icon_color = _ICON_INACTIVE

            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            if bg is not None:
                painter.setPen(QPen(_BORDER_ACTIVE) if active else Qt.NoPen)
                painter.setBrush(bg)
                painter.drawRoundedRect(rect, 8, 8)

            icon_rect = QRectF(12, (self.height() - 18) / 2, 18, 18)
            _draw_icon(painter, self._icon_kind, icon_rect, icon_color)

            font = self.font()
            font.setWeight(QFont.Weight.Medium if active else QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(text_color)
            text_x = 42
            available = max(0, self.width() - text_x - 8)
            metrics = QFontMetrics(font)
            text = metrics.elidedText(self.text(), Qt.ElideRight, available)
            painter.drawText(
                QRectF(text_x, 0, available, self.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                text,
            )
        finally:
            painter.end()
