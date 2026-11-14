"""Qt6（PySide6）兼容辅助."""

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication


def primary_available_geometry() -> QRect:
    """主屏可用区域（替代 Qt5 的 QApplication.desktop().availableGeometry()）."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QRect(0, 0, 1024, 768)
    return screen.availableGeometry()


def ensure_quick_controls_style() -> None:
    """在加载 QML 前切换到可定制的 Basic 样式.

    Qt6 的原生 Quick Controls 样式不允许 QML 覆盖 background/contentItem，
    会打印 warning 并忽略自定义外观。
    """
    try:
        from PySide6.QtQuickControls2 import QQuickStyle

        QQuickStyle.setStyle("Basic")
    except Exception:
        pass
