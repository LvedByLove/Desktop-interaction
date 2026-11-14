"""给 QLineEdit 增加「显示/隐藏密码」眼睛按钮."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLineEdit

_ICON_COLOR = QColor("#4e5969")


def _eye_icon(visible: bool) -> QIcon:
    """绘制眼睛图标；visible=False 时带一条斜线."""
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(_ICON_COLOR)
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        # 眼睛轮廓
        path = QPainterPath()
        path.moveTo(QPointF(2, 9))
        path.cubicTo(QPointF(4.5, 4.2), QPointF(13.5, 4.2), QPointF(16, 9))
        path.cubicTo(QPointF(13.5, 13.8), QPointF(4.5, 13.8), QPointF(2, 9))
        painter.drawPath(path)

        # 瞳孔
        painter.drawEllipse(QRectF(7, 6, 4, 6))

        # 隐藏状态：斜线
        if not visible:
            painter.drawLine(QPointF(3, 15), QPointF(15, 3))
    finally:
        painter.end()

    return QIcon(pixmap)


def attach_password_toggle(edit: QLineEdit) -> None:
    """把普通密码输入框升级为可切换明文/密文.

    幂等：重复调用不会重复添加按钮。
    """
    if edit is None or getattr(edit, "_password_toggle_attached", False):
        return

    edit._password_toggle_attached = True
    edit.setEchoMode(QLineEdit.Password)
    edit.setTextMargins(0, 0, 24, 0)

    action = edit.addAction(_eye_icon(False), QLineEdit.TrailingPosition)
    action.setCheckable(True)
    action.setToolTip("显示密码")

    def _toggle(checked: bool) -> None:
        edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        action.setIcon(_eye_icon(checked))
        action.setToolTip("隐藏密码" if checked else "显示密码")

    action.toggled.connect(_toggle)
