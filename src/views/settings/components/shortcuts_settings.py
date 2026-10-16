from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class ShortcutsSettingsWidget(QWidget):
    """
    快捷键设置组件.
    """

    # 信号定义
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = ConfigManager.get_instance()
        self.shortcuts_config = self.config.get_config("SHORTCUTS", {})
        self.init_ui()

    def init_ui(self):
        """
        初始化UI.
        """
        layout = QVBoxLayout()

        # 启用快捷键选项
        self.enable_checkbox = QCheckBox("启用全局快捷键")
        self.enable_checkbox.setChecked(self.shortcuts_config.get("ENABLED", True))
        self.enable_checkbox.toggled.connect(self.on_settings_changed)
        layout.addWidget(self.enable_checkbox)

        # 快捷键配置组
        shortcuts_group = QGroupBox("快捷键配置")
        shortcuts_layout = QVBoxLayout()

        # 创建各个快捷键配置控件
        self.shortcut_widgets = {}

        # 按住说话（两个修饰键组合，可区分左/右）
        self.shortcut_widgets["MANUAL_PRESS"] = self.create_dual_modifier_config(
            "按住说话", self.shortcuts_config.get("MANUAL_PRESS", {})
        )
        shortcuts_layout.addWidget(self.shortcut_widgets["MANUAL_PRESS"])

        # 自动对话
        self.shortcut_widgets["AUTO_TOGGLE"] = self.create_shortcut_config(
            "自动对话", self.shortcuts_config.get("AUTO_TOGGLE", {})
        )
        shortcuts_layout.addWidget(self.shortcut_widgets["AUTO_TOGGLE"])

        # 中断对话
        self.shortcut_widgets["ABORT"] = self.create_shortcut_config(
            "中断对话", self.shortcuts_config.get("ABORT", {})
        )
        shortcuts_layout.addWidget(self.shortcut_widgets["ABORT"])

        # 模式切换
        self.shortcut_widgets["MODE_TOGGLE"] = self.create_shortcut_config(
            "模式切换", self.shortcuts_config.get("MODE_TOGGLE", {})
        )
        shortcuts_layout.addWidget(self.shortcut_widgets["MODE_TOGGLE"])

        # 窗口显示/隐藏
        self.shortcut_widgets["WINDOW_TOGGLE"] = self.create_shortcut_config(
            "输入窗口显示/隐藏", self.shortcuts_config.get("WINDOW_TOGGLE", {})
        )
        shortcuts_layout.addWidget(self.shortcut_widgets["WINDOW_TOGGLE"])

        shortcuts_group.setLayout(shortcuts_layout)
        layout.addWidget(shortcuts_group)

        # 按钮区域
        btn_layout = QHBoxLayout()
        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.clicked.connect(self.reset_to_defaults)
        btn_layout.addWidget(self.reset_btn)

        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self.apply_settings)
        btn_layout.addWidget(self.apply_btn)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

    # 按住说话使用的修饰键选项：(显示文本, 存储值)
    # 存储值 ctrl_l/ctrl_r 等表示区分左右；ctrl/alt/shift 表示任意一侧
    MODIFIER_OPTIONS = [
        ("左 Ctrl", "ctrl_l"),
        ("右 Ctrl", "ctrl_r"),
        ("Ctrl（任意）", "ctrl"),
        ("左 Alt", "alt_l"),
        ("右 Alt", "alt_r"),
        ("Alt（任意）", "alt"),
        ("左 Shift", "shift_l"),
        ("右 Shift", "shift_r"),
        ("Shift（任意）", "shift"),
    ]

    # 修饰键值 -> 其能匹配的具体键名集合，用于判断两个修饰键是否会落到同一物理键。
    # 与 src/plugins/shortcuts.py 的 _MODIFIER_ALIASES 保持一致。
    _MODIFIER_KEYSETS = {
        "ctrl": {"ctrl_l", "ctrl_r", "control"},
        "ctrl_l": {"ctrl_l"},
        "ctrl_r": {"ctrl_r"},
        "alt": {"alt_l", "alt_r", "option"},
        "alt_l": {"alt_l"},
        "alt_r": {"alt_r"},
        "shift": {"shift_l", "shift_r"},
        "shift_l": {"shift_l"},
        "shift_r": {"shift_r"},
    }

    def _modifiers_overlap(self, a: str, b: str) -> bool:
        """两个修饰键是否会匹配到同一个物理键（如 Ctrl(任意) 与 左 Ctrl）。"""
        ka = self._MODIFIER_KEYSETS.get(a, {a})
        kb = self._MODIFIER_KEYSETS.get(b, {b})
        return bool(ka & kb)

    def create_dual_modifier_config(self, title, config):
        """
        创建“双修饰键”配置控件（用于按住说话）。
        两个按键均为修饰键，可分别选择左/右/任意。
        配置存储为 modifier / key2 两个字段。
        """
        widget = QWidget()
        layout = QHBoxLayout()

        layout.addWidget(self._create_title_label(title))

        # 第一个修饰键
        first_combo = QComboBox()
        for text, value in self.MODIFIER_OPTIONS:
            first_combo.addItem(text, value)
        first_modifier = str(config.get("modifier", "alt_l")).lower()
        self._set_combo_by_data(first_combo, first_modifier)
        first_combo.currentIndexChanged.connect(self.on_settings_changed)
        layout.addWidget(first_combo)

        # 第二个修饰键
        second_combo = QComboBox()
        for text, value in self.MODIFIER_OPTIONS:
            second_combo.addItem(text, value)
        second_modifier = str(config.get("key2", "ctrl_l")).lower()
        self._set_combo_by_data(second_combo, second_modifier)
        second_combo.currentIndexChanged.connect(self.on_settings_changed)
        layout.addWidget(second_combo)

        widget.setLayout(layout)
        widget.first_combo = first_combo
        widget.second_combo = second_combo
        # 标记为双修饰键类型，便于 apply 时区分
        widget.dual_modifier = True
        return widget

    @staticmethod
    def _set_combo_by_data(combo, data):
        """按 itemData 设置下拉框当前项，找不到时回退到第一项。"""
        idx = combo.findData(data)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    # 各行标题文本，用于统一标签宽度使下拉框左对齐
    TITLES = ["按住说话", "自动对话", "中断对话", "模式切换", "输入窗口显示/隐藏"]

    def _create_title_label(self, title):
        """创建行标题标签，宽度按最长标题统一，保证各行下拉框左对齐。"""
        label = QLabel(f"{title}:")
        metrics = label.fontMetrics()
        width = max(metrics.width(f"{t}:") for t in self.TITLES)
        label.setFixedWidth(width)
        return label

    def create_shortcut_config(self, title, config):
        """
        创建单个快捷键配置控件.
        """
        widget = QWidget()
        layout = QHBoxLayout()

        # 标题
        layout.addWidget(self._create_title_label(title))

        # 修饰键选择
        modifier_combo = QComboBox()
        modifier_combo.addItems(["Ctrl", "Alt", "Shift"])
        current_modifier = config.get("modifier", "ctrl").title()
        modifier_combo.setCurrentText(current_modifier)
        modifier_combo.currentTextChanged.connect(self.on_settings_changed)
        layout.addWidget(modifier_combo)

        # 按键选择
        key_combo = QComboBox()
        key_combo.addItems(["space"] + [chr(i) for i in range(ord("a"), ord("z") + 1)])
        current_key = config.get("key", "j").lower()
        key_combo.setCurrentText(current_key)
        key_combo.currentTextChanged.connect(self.on_settings_changed)
        layout.addWidget(key_combo)

        widget.setLayout(layout)
        widget.modifier_combo = modifier_combo
        widget.key_combo = key_combo
        return widget

    def on_settings_changed(self):
        """
        设置变更回调.
        """
        self.settings_changed.emit()

    def apply_settings(self):
        """
        应用设置.
        """
        try:
            # 更新启用状态
            self.config.update_config(
                "SHORTCUTS.ENABLED", self.enable_checkbox.isChecked()
            )

            # 更新各个快捷键配置
            for key, widget in self.shortcut_widgets.items():
                if getattr(widget, "dual_modifier", False):
                    # 按住说话：两个修饰键
                    first = widget.first_combo.currentData()
                    second = widget.second_combo.currentData()
                    # 两个修饰键不能落到同一物理键（如 Ctrl(任意)+左 Ctrl），
                    # 否则单按一个键就会误触发。
                    if self._modifiers_overlap(first, second):
                        from PyQt5.QtWidgets import QMessageBox

                        QMessageBox.warning(
                            self,
                            "快捷键无效",
                            "“按住说话”的两个修饰键必须是不同的键"
                            "（例如 左 Alt + 左 Ctrl）。\n"
                            "请不要选择同一类修饰键的“任意一侧”与具体某一侧组合。",
                        )
                        continue
                    self.config.update_config(f"SHORTCUTS.{key}.modifier", first)
                    self.config.update_config(f"SHORTCUTS.{key}.key2", second)
                else:
                    modifier = widget.modifier_combo.currentText().lower()
                    key_value = widget.key_combo.currentText().lower()
                    self.config.update_config(f"SHORTCUTS.{key}.modifier", modifier)
                    self.config.update_config(f"SHORTCUTS.{key}.key", key_value)

            # 重新加载配置
            self.config.reload_config()
            self.shortcuts_config = self.config.get_config("SHORTCUTS", {})

            logger.info("快捷键设置已保存")

        except Exception as e:
            logger.error(f"保存快捷键设置失败: {e}")

    def reset_to_defaults(self):
        """
        恢复默认设置.
        """
        # 默认配置
        defaults = {
            "ENABLED": True,
            "MANUAL_PRESS": {"modifier": "alt_l", "key2": "ctrl_l"},
            "AUTO_TOGGLE": {"modifier": "ctrl", "key": "k"},
            "ABORT": {"modifier": "ctrl", "key": "q"},
            "MODE_TOGGLE": {"modifier": "ctrl", "key": "m"},
            "WINDOW_TOGGLE": {"modifier": "ctrl", "key": "w"},
        }

        # 更新UI
        self.enable_checkbox.setChecked(defaults["ENABLED"])

        for key, config in defaults.items():
            if key == "ENABLED":
                continue

            widget = self.shortcut_widgets.get(key)
            if not widget:
                continue

            if getattr(widget, "dual_modifier", False):
                self._set_combo_by_data(
                    widget.first_combo, config.get("modifier", "alt_l")
                )
                self._set_combo_by_data(
                    widget.second_combo, config.get("key2", "ctrl_l")
                )
            else:
                widget.modifier_combo.setCurrentText(config["modifier"].title())
                widget.key_combo.setCurrentText(config["key"].lower())

        # 触发变更信号
        self.on_settings_changed()
