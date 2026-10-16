from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


FONT_FAMILY = "Microsoft YaHei UI"


SETTINGS_DIALOG_QSS = """
QDialog#SettingsWindow,
QDialog#ThemedDialog {
    background: #f7f8fa;
    color: #1d2129;
    border: 1px solid #e5e6eb;
    border-radius: 18px;
}

QDialog#SettingsWindow QWidget,
QDialog#ThemedDialog QWidget {
    background: #f7f8fa;
    color: #1d2129;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}

QTabWidget::pane {
    border: 1px solid #e5e6eb;
    border-radius: 14px;
    background: #ffffff;
    top: -1px;
}

QTabBar::tab {
    background: #f2f3f5;
    color: #4e5969;
    border: 1px solid #e5e6eb;
    border-bottom: none;
    padding: 9px 18px;
    margin-right: 6px;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
}

QTabBar::tab:hover {
    background: #e8f3ff;
    color: #165dff;
}

QTabBar::tab:selected {
    background: #165dff;
    color: #ffffff;
    border-color: #165dff;
}

QScrollArea,
QScrollArea > QWidget,
QScrollArea > QWidget > QWidget {
    background: #ffffff;
    border: none;
}

QGroupBox {
    background: #ffffff;
    border: 1px solid #e5e6eb;
    border-radius: 14px;
    margin-top: 18px;
    padding: 14px 12px 12px 12px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 8px;
    color: #165dff;
    background: #ffffff;
    font-weight: 600;
}

QLabel {
    color: #1d2129;
    background: transparent;
}

QTabWidget,
QTabWidget QWidget,
QStackedWidget,
QStackedWidget QWidget {
    background: #ffffff;
}

QLabel#dialogTitleLabel {
    color: #1d2129;
    font-size: 15px;
    font-weight: 600;
    padding-bottom: 6px;
}

QWidget#SettingsTitleBar {
    background: #ffffff;
    border-top-left-radius: 18px;
    border-top-right-radius: 18px;
    border-bottom: 1px solid #e5e6eb;
}

QLabel#SettingsTitleLabel {
    color: #1d2129;
    font-size: 14px;
    font-weight: 600;
    padding-left: 2px;
}

QPushButton#SettingsCloseButton {
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    padding: 0;
    border: none;
    border-radius: 16px;
    background: transparent;
    color: #4e5969;
    font-size: 18px;
    font-weight: 400;
}

QPushButton#SettingsCloseButton:hover {
    background: #f53f3f;
    color: #ffffff;
}

QPushButton#SettingsCloseButton:pressed {
    background: #c93636;
}

QLineEdit,
QTextEdit,
QPlainTextEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox,
QListWidget {
    background: #ffffff;
    color: #1d2129;
    border: 1px solid #dcdfe6;
    border-radius: 9px;
    padding: 7px 10px;
    selection-background-color: #165dff;
    selection-color: #ffffff;
}

QLineEdit:hover,
QTextEdit:hover,
QPlainTextEdit:hover,
QComboBox:hover,
QSpinBox:hover,
QDoubleSpinBox:hover,
QListWidget:hover {
    border-color: #a9c7ff;
    background: #fbfdff;
}

QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QListWidget:focus {
    border-color: #165dff;
}

QLineEdit:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled,
QComboBox:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled {
    color: #86909c;
    background: #f2f3f5;
    border-color: #e5e6eb;
}

QComboBox {
    padding-right: 28px;
}

QComboBox::drop-down {
    width: 26px;
    border: none;
    border-left: 1px solid #e5e6eb;
    border-top-right-radius: 9px;
    border-bottom-right-radius: 9px;
    background: #f7f8fa;
}

QComboBox::down-arrow {
    width: 0;
    height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #4e5969;
}

QComboBox QAbstractItemView {
    background: #ffffff;
    color: #1d2129;
    border: 1px solid #e5e6eb;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: #165dff;
    selection-color: #ffffff;
    outline: none;
}

QListWidget::item {
    padding: 8px 10px;
    border-radius: 7px;
    margin: 2px;
}

QListWidget::item:hover {
    background: #e8f3ff;
}

QListWidget::item:selected {
    background: #165dff;
    color: #ffffff;
}

QCheckBox {
    color: #1d2129;
    spacing: 8px;
    background: transparent;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid #c9cdd4;
    background: #ffffff;
}

QCheckBox::indicator:hover {
    border-color: #165dff;
}

QCheckBox::indicator:checked {
    background: #165dff;
    border-color: #165dff;
}

QPushButton {
    min-height: 32px;
    padding: 7px 16px;
    border-radius: 16px;
    border: 1px solid #dcdfe6;
    background: #ffffff;
    color: #1d2129;
    font-weight: 500;
}

QPushButton:hover {
    background: #e8f3ff;
    border-color: #165dff;
    color: #165dff;
}

QPushButton:pressed {
    background: #d6e8ff;
    border-color: #0e42d2;
    color: #0e42d2;
}

QPushButton:disabled {
    color: #c9cdd4;
    background: #f2f3f5;
    border-color: #e5e6eb;
}

QPushButton#save_btn,
QDialogButtonBox QPushButton[text="OK"],
QDialogButtonBox QPushButton[text="确定"] {
    background: #165dff;
    border-color: #165dff;
    color: #ffffff;
}

QPushButton#save_btn:hover,
QDialogButtonBox QPushButton[text="OK"]:hover,
QDialogButtonBox QPushButton[text="确定"]:hover {
    background: #4080ff;
    border-color: #4080ff;
    color: #ffffff;
}

QPushButton#reset_btn {
    color: #ff7d00;
    border-color: #ffd8a8;
    background: #fff7e8;
}

QPushButton#reset_btn:hover {
    color: #d25f00;
    border-color: #ffb65d;
    background: #fff1d6;
}

QPushButton#cancel_btn {
    color: #4e5969;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 2px;
}

QScrollBar::handle:vertical {
    background: #c9cdd4;
    border-radius: 5px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background: #a9aeb8;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: transparent;
    border: none;
    height: 0;
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px 4px 2px 4px;
}

QScrollBar::handle:horizontal {
    background: #c9cdd4;
    border-radius: 5px;
    min-width: 28px;
}

QScrollBar::handle:horizontal:hover {
    background: #a9aeb8;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    background: transparent;
    border: none;
    width: 0;
}

QMessageBox {
    background: #ffffff;
}

QMessageBox QLabel {
    color: #1d2129;
}
"""


TRAY_MENU_QSS = """
QMenu {
    background: #101114;
    color: #ffffff;
    border: 1px solid #2a2f3a;
    border-radius: 10px;
    padding: 8px;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}

QMenu::item {
    padding: 9px 28px 9px 14px;
    border-radius: 8px;
    color: #d7dbe4;
    background: transparent;
}

QMenu::item:selected {
    background: #165dff;
    color: #ffffff;
}

QMenu::separator {
    height: 1px;
    background: #2a2f3a;
    margin: 7px 8px;
}

QMenu::icon {
    padding-left: 8px;
}
"""


TRAY_POPUP_QSS = """
QWidget#TrayPopupMenu {
    background: transparent;
    border: none;
}

QPushButton#TrayPopupButton,
QPushButton#TrayPopupQuitButton {
    min-height: 34px;
    padding: 8px 18px;
    border: none;
    border-radius: 9px;
    background: transparent;
    color: #1d2129;
    text-align: left;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}

QPushButton#TrayPopupButton:hover {
    background: #e8f3ff;
    color: #165dff;
}

QPushButton#TrayPopupQuitButton:hover {
    background: #fff1f0;
    color: #f53f3f;
}

QFrame#TrayPopupSeparator {
    background: #e5e6eb;
    min-height: 1px;
    max-height: 1px;
    border: none;
}
"""


def settings_dialog_qss() -> str:
    return SETTINGS_DIALOG_QSS


def tray_menu_qss() -> str:
    return TRAY_MENU_QSS


def tray_popup_qss() -> str:
    return TRAY_POPUP_QSS


def apply_settings_dialog_style(widget) -> None:
    widget.setStyleSheet(settings_dialog_qss())
    widget.setFont(QFont(FONT_FAMILY, 10))
    widget.setAttribute(Qt.WA_StyledBackground, True)

    layout = widget.layout()
    if layout:
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)


def apply_tray_menu_style(menu) -> None:
    menu.setStyleSheet(tray_menu_qss())
    menu.setFont(QFont(FONT_FAMILY, 10))


def apply_tray_popup_style(menu) -> None:
    menu.setStyleSheet(tray_popup_qss())
    menu.setFont(QFont(FONT_FAMILY, 10))
    menu.setAttribute(Qt.WA_StyledBackground, True)
