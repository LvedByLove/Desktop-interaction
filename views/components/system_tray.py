"""
系统托盘组件模块 提供系统托盘图标、菜单和状态指示功能.
"""

from typing import Optional

from PyQt5.QtCore import QObject, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QIcon, QPainter, QPainterPath
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from src.utils.logging_config import get_logger
from src.utils.resource_finder import resource_finder
from src.views.theme import apply_tray_popup_style


class TrayPopupMenu(QWidget):
    """
    自绘圆角托盘菜单.
    """

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)

        painter.fillPath(path, QColor("#ffffff"))
        painter.setPen(QColor("#e5e6eb"))
        painter.drawPath(path)


class SystemTray(QObject):
    """
    系统托盘组件.
    """

    # 定义信号
    show_window_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self.logger = get_logger("SystemTray")
        self.parent_widget = parent

        # 托盘相关组件
        self.tray_icon = None
        self.tray_menu = None

        # 状态相关
        self.current_status = ""
        self.is_connected = True

        # 初始化托盘
        self._setup_tray()

    def _setup_tray(self):
        """
        设置系统托盘图标.
        """
        try:
            # 检查系统是否支持系统托盘
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self.logger.warning("系统不支持系统托盘功能")
                return

            # 创建托盘菜单
            self._create_tray_menu()

            # 创建系统托盘图标（不绑定 QWidget 作为父对象，避免窗口生命周期影响托盘图标，防止 macOS 下隐藏/关闭时崩溃）
            self.tray_icon = QSystemTrayIcon()

            self.tray_icon.setIcon(self._create_tray_icon())

            # 连接托盘图标的事件
            self.tray_icon.activated.connect(self._on_tray_activated)

            self.update_status("待命", connected=True)

            # 显示系统托盘图标
            self.tray_icon.show()
            self.logger.info("系统托盘图标已初始化")

        except Exception as e:
            self.logger.error(f"初始化系统托盘图标失败: {e}", exc_info=True)

    def _create_tray_icon(self):
        """
        创建固定托盘图标.
        """
        icon_path = resource_finder.find_file("assets/image/Redstone.png")
        if icon_path:
            return QIcon(str(icon_path))
        self.logger.warning("未找到系统托盘图标资源: assets/image/Redstone.png")
        return QIcon()

    def _create_tray_menu(self):
        """
        创建托盘右键菜单.
        """
        self.tray_menu = self._create_popup_menu()

    def _create_popup_menu(self):
        """
        创建自定义圆角托盘菜单.
        """
        menu = TrayPopupMenu()
        menu.setObjectName("TrayPopupMenu")
        menu.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
        apply_tray_popup_style(menu)

        layout = QVBoxLayout(menu)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(4)

        settings_btn = QPushButton("参数配置", menu)
        settings_btn.setObjectName("TrayPopupButton")
        settings_btn.clicked.connect(self._on_settings_from_popup)
        layout.addWidget(settings_btn)

        separator = QFrame(menu)
        separator.setObjectName("TrayPopupSeparator")
        separator.setFrameShape(QFrame.HLine)
        layout.addWidget(separator)

        quit_btn = QPushButton("退出程序", menu)
        quit_btn.setObjectName("TrayPopupQuitButton")
        quit_btn.clicked.connect(self._on_quit_from_popup)
        layout.addWidget(quit_btn)

        menu.setFixedWidth(150)
        menu.adjustSize()
        return menu

    def _show_popup_menu(self):
        """
        显示自定义圆角托盘菜单.
        """
        if not self.tray_menu:
            return
        self.tray_menu.adjustSize()
        pos = QCursor.pos()
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        size = self.tray_menu.sizeHint()
        x = pos.x() - size.width() + 12
        y = pos.y() - size.height() - 8
        if screen:
            geometry = screen.availableGeometry()
            x = max(geometry.left(), min(x, geometry.right() - size.width()))
            y = max(geometry.top(), min(y, geometry.bottom() - size.height()))
        self.tray_menu.move(x, y)
        self.tray_menu.show()
        self.tray_menu.raise_()
        self.tray_menu.activateWindow()

    def _on_tray_activated(self, reason):
        """
        处理托盘图标点击事件.
        """
        if reason == QSystemTrayIcon.Context:  # 右键
            self._show_popup_menu()
        elif reason == QSystemTrayIcon.DoubleClick:  # 双击打开设置
            self.settings_requested.emit()
        elif reason == QSystemTrayIcon.Trigger:  # 单击
            self.show_window_requested.emit()

    def _on_settings_from_popup(self):
        """
        处理设置菜单项点击.
        """
        if self.tray_menu:
            self.tray_menu.hide()
        self.settings_requested.emit()

    def _on_quit_from_popup(self):
        """
        处理退出菜单项点击.
        """
        if self.tray_menu:
            self.tray_menu.hide()
        self.quit_requested.emit()

    def update_status(self, status: str, connected: bool = True):
        """更新托盘图标状态.

        Args:
            status: 状态文本
            connected: 连接状态
        """
        if not self.tray_icon:
            return

        self.current_status = status
        self.is_connected = connected

        try:
            tooltip = f"小智AI助手 - {status}"
            self.tray_icon.setToolTip(tooltip)

        except Exception as e:
            self.logger.error(f"更新系统托盘状态失败: {e}")

    def show_message(
        self,
        title: str,
        message: str,
        icon_type=QSystemTrayIcon.Information,
        duration: int = 2000,
    ):
        """显示托盘通知消息.

        Args:
            title: 通知标题
            message: 通知内容
            icon_type: 图标类型
            duration: 显示时间(毫秒)
        """
        if self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.showMessage(title, message, icon_type, duration)

    def hide(self):
        """
        隐藏托盘图标.
        """
        if self.tray_icon:
            self.tray_icon.hide()

    def is_visible(self) -> bool:
        """
        检查托盘图标是否可见.
        """
        return self.tray_icon and self.tray_icon.isVisible()

    def is_available(self) -> bool:
        """
        检查系统托盘是否可用.
        """
        return QSystemTrayIcon.isSystemTrayAvailable()
