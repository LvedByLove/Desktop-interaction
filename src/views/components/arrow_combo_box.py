"""QComboBox：自绘向下三角形箭头，不依赖图片资源."""

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox


class ArrowComboBox(QComboBox):
    """在 QComboBox 绘制完成后叠加绘制箭头.

    QSS 不支持用 border 拼三角形，而图片箭头又有资源和 DPI 问题。
    这里直接用 QPainter 画矢量三角形，风格与 py-xiaozhi 的 QML ▼ 一致。
    """

    ARROW_WIDTH = 9.0
    ARROW_HEIGHT = 5.0
    ARROW_COLOR = QColor("#4e5969")
    ARROW_DISABLED_COLOR = QColor("#c9cdd4")

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            rect = self._arrow_rect()
            if rect is None:
                return

            enabled = self.isEnabled()
            painter.setPen(Qt.NoPen)
            painter.setBrush(
                self.ARROW_COLOR if enabled else self.ARROW_DISABLED_COLOR
            )

            center_x = rect.center().x() + 0.5
            center_y = rect.center().y() + 0.5
            half_w = self.ARROW_WIDTH / 2.0
            half_h = self.ARROW_HEIGHT / 2.0

            popup_open = False
            try:
                popup_open = bool(self.view().isVisible())
            except Exception:
                pass

            if popup_open:
                points = [
                    QPointF(center_x - half_w, center_y + half_h),
                    QPointF(center_x + half_w, center_y + half_h),
                    QPointF(center_x, center_y - half_h),
                ]
            else:
                points = [
                    QPointF(center_x - half_w, center_y - half_h),
                    QPointF(center_x + half_w, center_y - half_h),
                    QPointF(center_x, center_y + half_h),
                ]
            painter.drawPolygon(QPolygonF(points))
        finally:
            painter.end()

    def _arrow_rect(self) -> QRect | None:
        try:
            option = QStyleOptionComboBox()
            self.initStyleOption(option)
            rect = self.style().subControlRect(
                QStyle.CC_ComboBox,
                option,
                QStyle.SC_ComboBoxArrow,
                self,
            )
            if rect is not None and rect.width() > 0 and rect.height() > 0:
                return rect
        except Exception:
            pass

        if self.width() <= 0 or self.height() <= 0:
            return None
        return QRect(max(0, self.width() - 30), 0, 30, self.height())
