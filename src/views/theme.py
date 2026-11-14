from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


FONT_FAMILY = "Microsoft YaHei UI"


SETTINGS_DIALOG_QSS = """
QDialog#SettingsWindow,
QDialog#ThemedDialog {
    background: #f9fafb;
    color: #111827;
}

QDialog#SettingsWindow QWidget,
QDialog#ThemedDialog QWidget {
    font-family: "Microsoft YaHei UI";
    font-size: 14px;
    color: #111827;
}

QLabel {
    color: #374151;
    background: transparent;
    font-size: 14px;
}

QLabel#SettingsPageTitle {
    color: #111827;
    font-size: 24px;
    font-weight: bold;
}

QLabel#SettingsPageSubtitle {
    color: #6b7280;
    font-size: 13px;
}

QLabel#SettingsSectionTitle {
    color: #111827;
    font-size: 18px;
    font-weight: bold;
    padding-bottom: 2px;
}

QLabel#SettingsStatusLabel {
    color: #6b7280;
    font-size: 13px;
}

QWidget#SettingsSidebar {
    background: transparent;
}

QFrame#SettingsContentCard {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
}

QFrame#SettingsSeparator {
    background: #f3f4f6;
    border: none;
}

QStackedWidget#SettingsStack {
    background: transparent;
}

QWidget[settingsPage="true"] {
    background: transparent;
}

QGroupBox {
    background: transparent;
    border: none;
    margin-top: 20px;
    padding: 6px 0 0 0;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0;
    padding: 0 0 6px 0;
    color: #111827;
    font-size: 15px;
    font-weight: bold;
    background: transparent;
}

/* 文件操作工具单独用灰色卡片区分 */
QGroupBox#FileToolsGroup {
    background: #f9fafb;
    border: 1px solid #d1d5db;
    border-radius: 10px;
    margin-top: 22px;
    padding: 12px;
}

QGroupBox#FileToolsGroup::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #111827;
    background: #f9fafb;
    font-size: 15px;
    font-weight: bold;
}

QLineEdit,
QTextEdit,
QPlainTextEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox,
QListWidget {
    background: #ffffff;
    color: #111827;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 14px;
    min-height: 22px;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}

QLineEdit:hover,
QTextEdit:hover,
QPlainTextEdit:hover,
QComboBox:hover,
QSpinBox:hover,
QDoubleSpinBox:hover,
QListWidget:hover {
    border-color: #9ca3af;
    background: #ffffff;
}

QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QListWidget:focus {
    border-color: #2563eb;
}

QLineEdit:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled,
QComboBox:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled {
    color: #9ca3af;
    background: #f3f4f6;
    border-color: #e5e7eb;
}

QComboBox {
    padding-right: 34px;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 30px;
    border: none;
    background: transparent;
}

QComboBox QAbstractItemView {
    background: #ffffff;
    color: #111827;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 6px;
    outline: none;
    selection-background-color: #eff6ff;
    selection-color: #1d4ed8;
}

QComboBox QAbstractItemView::item {
    padding: 8px 10px;
    border-radius: 6px;
    min-height: 22px;
}

QSpinBox::up-button,
QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid #e5e7eb;
    border-top-right-radius: 8px;
    background: #f9fafb;
}

QSpinBox::down-button,
QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 24px;
    border-left: 1px solid #e5e7eb;
    border-bottom-right-radius: 8px;
    background: #f9fafb;
}

QSpinBox::up-button:hover,
QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover {
    background: #f3f4f6;
}

QSpinBox::up-arrow,
QDoubleSpinBox::up-arrow {
    image: url("__SPIN_ARROW_UP__");
    width: 8px;
    height: 5px;
}

QSpinBox::down-arrow,
QDoubleSpinBox::down-arrow {
    image: url("__SPIN_ARROW_DOWN__");
    width: 8px;
    height: 5px;
}

QPushButton {
    min-height: 20px;
    padding: 9px 16px;
    border-radius: 8px;
    border: 1px solid #e5e7eb;
    background: #ffffff;
    color: #374151;
    font-size: 14px;
    font-weight: bold;
}

QPushButton:hover {
    background: #f9fafb;
    border-color: #d1d5db;
}

QPushButton:pressed {
    background: #f3f4f6;
}

QPushButton:disabled {
    color: #9ca3af;
    background: #f9fafb;
    border-color: #e5e7eb;
}

QDialogButtonBox QPushButton[text="OK"],
QDialogButtonBox QPushButton[text="确定"] {
    background: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
}

QDialogButtonBox QPushButton[text="OK"]:hover,
QDialogButtonBox QPushButton[text="确定"]:hover {
    background: #1d4ed8;
    border-color: #1d4ed8;
    color: #ffffff;
}

/* 页面操作按钮：默认统一白底，悬浮时分别显示蓝/红/绿 */
QPushButton#save_btn,
QPushButton#cancel_btn,
QPushButton#reset_btn {
    background: #ffffff;
    border-color: #e5e7eb;
    color: #374151;
}

QPushButton#save_btn:hover {
    background: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
}

QPushButton#save_btn:pressed {
    background: #1d4ed8;
    border-color: #1d4ed8;
    color: #ffffff;
}

QPushButton#cancel_btn:hover {
    background: #dc2626;
    border-color: #dc2626;
    color: #ffffff;
}

QPushButton#cancel_btn:pressed {
    background: #b91c1c;
    border-color: #b91c1c;
    color: #ffffff;
}

QPushButton#reset_btn:hover {
    background: #16a34a;
    border-color: #16a34a;
    color: #ffffff;
}

QPushButton#reset_btn:pressed {
    background: #15803d;
    border-color: #15803d;
    color: #ffffff;
}

QCheckBox {
    color: #374151;
    spacing: 8px;
    background: transparent;
    font-size: 14px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #d1d5db;
    background: #ffffff;
}

QCheckBox::indicator:hover {
    border-color: #9ca3af;
}

QCheckBox::indicator:checked {
    background: #2563eb;
    border-color: #2563eb;
}

QSlider {
    min-height: 24px;
}

QSlider::groove:horizontal {
    height: 6px;
    background: #e5e7eb;
    border-radius: 3px;
}

QSlider::sub-page:horizontal {
    background: #2563eb;
    border-radius: 3px;
}

QSlider::add-page:horizontal {
    background: #e5e7eb;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
    background: #ffffff;
    border: 1px solid #d1d5db;
}

QSlider::handle:horizontal:hover {
    border-color: #2563eb;
}

QSlider::handle:horizontal:pressed {
    background: #eff6ff;
    border-color: #2563eb;
}

QListWidget::item {
    padding: 8px 10px;
    border-radius: 6px;
    margin: 2px;
}

QListWidget::item:hover {
    background: #f3f4f6;
}

QListWidget::item:selected {
    background: #eff6ff;
    color: #1d4ed8;
}

QScrollArea {
    background: transparent;
    border: none;
}

QScrollArea > QWidget,
QScrollArea > QWidget > QWidget {
    background: transparent;
}

QScrollBar:vertical {
    background: transparent;
    border: none;
    width: 10px;
    margin: 2px 0 2px 0;
}

QScrollBar::handle:vertical {
    background: #d1d5db;
    border: none;
    border-radius: 5px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background: #9ca3af;
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
    border: none;
    height: 10px;
    margin: 0 2px 0 2px;
}

QScrollBar::handle:horizontal {
    background: #d1d5db;
    border: none;
    border-radius: 5px;
    min-width: 28px;
}

QScrollBar::handle:horizontal:hover {
    background: #9ca3af;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    background: transparent;
    border: none;
    width: 0;
}

QToolTip {
    background: #ffffff;
    color: #111827;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 8px 10px;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
}

QMessageBox {
    background: #ffffff;
}

QMessageBox QLabel {
    color: #111827;
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


def _asset_url(filename: str) -> str:
    """定位 QSS 使用的图片资源；返回绝对路径（正斜杠）."""
    try:
        from src.utils.resource_finder import resource_finder

        path = resource_finder.find_file(f"assets/image/{filename}")
        if path is not None:
            return path.resolve().as_posix()
    except Exception:
        pass
    return ""


def settings_dialog_qss() -> str:
    """返回设置对话框样式，并注入下拉/数值框箭头图片路径."""
    qss = SETTINGS_DIALOG_QSS
    tokens = {
        "__SPIN_ARROW_UP__": "spin_arrow_up.png",
        "__SPIN_ARROW_DOWN__": "spin_arrow_down.png",
    }
    for token, filename in tokens.items():
        url = _asset_url(filename)
        if url:
            qss = qss.replace(token, url)
        else:
            # 资源缺失时去掉 image 行，避免 QSS 去加载不存在的文件
            qss = qss.replace(f'    image: url("{token}");\n', "")
    return qss


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
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)


def apply_tray_menu_style(menu) -> None:
    menu.setStyleSheet(tray_menu_qss())
    menu.setFont(QFont(FONT_FAMILY, 10))


def apply_tray_popup_style(menu) -> None:
    menu.setStyleSheet(tray_popup_qss())
    menu.setFont(QFont(FONT_FAMILY, 10))
    menu.setAttribute(Qt.WA_StyledBackground, True)
