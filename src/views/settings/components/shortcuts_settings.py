from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger
from src.views.components.arrow_combo_box import ArrowComboBox
from src.views.components.toggle_switch import ToggleSwitch

logger = get_logger(__name__)


class ShortcutsSettingsWidget(QWidget):
    """
    快捷键设置组件.
    """

    # 信号定义
    settings_changed = Signal()

    def __init__(self, parent=None, embedded: bool = False):
        super().__init__(parent)
        self.embedded = embedded
        self.config = ConfigManager.get_instance()
        self.shortcuts_config = self.config.get_config("SHORTCUTS", {})
        self.init_ui()

    def init_ui(self):
        """
        初始化UI.

        embedded=True 时直接作为区块嵌入系统选项页，不再自带滚动区。
        """
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(10)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # 启用快捷键选项
        self.enable_checkbox = ToggleSwitch("启用全局快捷键")
        self.enable_checkbox.setChecked(self.shortcuts_config.get("ENABLED", True))
        self.enable_checkbox.toggled.connect(self.on_settings_changed)
        layout.addWidget(self.enable_checkbox)

        # 快捷键配置组（嵌入时用普通容器，避免出现两个“快捷键”标题）
        if self.embedded:
            shortcuts_group = QWidget()
            shortcuts_layout = QVBoxLayout(shortcuts_group)
        else:
            shortcuts_group = QGroupBox("快捷键配置")
            shortcuts_layout = QVBoxLayout()
        shortcuts_layout.setSpacing(10)

        # 创建各个快捷键配置控件
        self.shortcut_widgets = {}

        # 按住说话（两个修饰键组合，可区分左/右）
        self.shortcut_widgets["MANUAL_PRESS"] = self.create_dual_modifier_config(
            "按住说话", self.shortcuts_config.get("MANUAL_PRESS", {})
        )
        shortcuts_layout.addWidget(self.shortcut_widgets["MANUAL_PRESS"])

        # 中断对话
        self.shortcut_widgets["ABORT"] = self.create_shortcut_config(
            "中断对话", self.shortcuts_config.get("ABORT", {})
        )
        shortcuts_layout.addWidget(self.shortcut_widgets["ABORT"])

        # 窗口显示/隐藏
        self.shortcut_widgets["WINDOW_TOGGLE"] = self.create_shortcut_config(
            "窗口显示/隐藏", self.shortcuts_config.get("WINDOW_TOGGLE", {})
        )
        shortcuts_layout.addWidget(self.shortcut_widgets["WINDOW_TOGGLE"])

        # 显示/收起歌词（配置缺失时按默认 Alt+L 显示，避免回退成 Ctrl+J）
        lyrics_cfg = dict(
            self.shortcuts_config.get("MUSIC_LYRICS_TOGGLE", {}) or {}
        )
        lyrics_cfg.setdefault("modifier", "alt")
        lyrics_cfg.setdefault("key", "l")
        self.shortcut_widgets["MUSIC_LYRICS_TOGGLE"] = self.create_shortcut_config(
            "显示/收起歌词", lyrics_cfg
        )
        shortcuts_layout.addWidget(
            self.shortcut_widgets["MUSIC_LYRICS_TOGGLE"]
        )

        if not self.embedded:
            shortcuts_group.setLayout(shortcuts_layout)
        layout.addWidget(shortcuts_group)

        if self.embedded:
            outer_layout.addWidget(content, 1)
        else:
            layout.addStretch(1)
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setFrameShape(QFrame.NoFrame)
            scroll_area.setWidget(content)
            outer_layout.addWidget(scroll_area, 1)


    # 所有快捷键统一只允许这些修饰键：(显示文本, 存储值)
    MODIFIER_OPTIONS = [
        ("左 Ctrl", "ctrl_l"),
        ("右 Ctrl", "ctrl_r"),
        ("左 Alt", "alt_l"),
        ("右 Alt", "alt_r"),
    ]

    # 仅保留 26 个英文字母与空格
    KEY_OPTIONS = ["space"] + [chr(i) for i in range(ord("a"), ord("z") + 1)]

    # 旧配置中的「任意 Alt/Ctrl」映射到具体一侧；Shift 不再支持。
    _MODIFIER_ALIASES = {
        "ctrl": "ctrl_l",
        "control": "ctrl_l",
        "alt": "alt_l",
        "option": "alt_l",
    }
    _ALLOWED_MODIFIERS = {"ctrl_l", "ctrl_r", "alt_l", "alt_r"}

    @classmethod
    def _normalize_modifier(cls, value, default: str) -> str:
        """把旧配置的修饰键规范到左/右 Ctrl 或 Alt."""
        text = str(value or "").strip().lower()
        text = cls._MODIFIER_ALIASES.get(text, text)
        return text if text in cls._ALLOWED_MODIFIERS else default

    # 修饰键值 -> 其能匹配的具体键名集合，用于判断两个修饰键是否会落到同一物理键。
    _MODIFIER_KEYSETS = {
        "ctrl_l": {"ctrl_l"},
        "ctrl_r": {"ctrl_r"},
        "alt_l": {"alt_l"},
        "alt_r": {"alt_r"},
    }

    def _modifiers_overlap(self, a: str, b: str) -> bool:
        """两个修饰键是否会匹配到同一个物理键."""
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
        first_combo = ArrowComboBox()
        for text, value in self.MODIFIER_OPTIONS:
            first_combo.addItem(text, value)
        first_modifier = self._normalize_modifier(
            config.get("modifier", "alt_l"), "alt_l"
        )
        self._set_combo_by_data(first_combo, first_modifier)
        first_combo.currentIndexChanged.connect(self.on_settings_changed)
        layout.addWidget(first_combo)

        # 第二个修饰键
        second_combo = ArrowComboBox()
        for text, value in self.MODIFIER_OPTIONS:
            second_combo.addItem(text, value)
        second_modifier = self._normalize_modifier(
            config.get("key2", "ctrl_l"), "ctrl_l"
        )
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
    TITLES = [
        "按住说话",
        "中断对话",
        "窗口显示/隐藏",
        "显示/收起歌词",
    ]

    def _create_title_label(self, title):
        """创建行标题标签，宽度按最长标题统一，保证各行下拉框左对齐。"""
        label = QLabel(f"{title}:")
        metrics = label.fontMetrics()
        width = max(metrics.horizontalAdvance(f"{t}:") for t in self.TITLES)
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

        # 修饰键选择：仅左/右 Ctrl、左/右 Alt
        modifier_combo = ArrowComboBox()
        for text, value in self.MODIFIER_OPTIONS:
            modifier_combo.addItem(text, value)
        current_modifier = self._normalize_modifier(
            config.get("modifier", "ctrl_l"), "ctrl_l"
        )
        self._set_combo_by_data(modifier_combo, current_modifier)
        modifier_combo.currentTextChanged.connect(self.on_settings_changed)
        layout.addWidget(modifier_combo)

        # 按键选择：仅 26 个英文字母与空格
        key_combo = ArrowComboBox()
        current_key = str(config.get("key", "space")).lower()
        if current_key not in self.KEY_OPTIONS:
            current_key = "space"
        key_combo.addItems(self.KEY_OPTIONS)
        key_combo.setCurrentText(current_key)
        key_combo.currentTextChanged.connect(self.on_settings_changed)
        layout.addWidget(key_combo)

        widget.setLayout(layout)
        widget.modifier_combo = modifier_combo
        widget.key_combo = key_combo
        return widget

    def on_settings_changed(self, *_args):
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
                        from PySide6.QtWidgets import QMessageBox

                        QMessageBox.warning(
                            self,
                            "快捷键无效",
                            "“按住说话”的两个修饰键不能相同"
                            "（例如 左 Alt + 左 Ctrl）。",
                        )
                        continue
                    self.config.update_config(f"SHORTCUTS.{key}.modifier", first)
                    self.config.update_config(f"SHORTCUTS.{key}.key2", second)
                else:
                    modifier = widget.modifier_combo.currentData() or "ctrl_l"
                    key_value = widget.key_combo.currentText().lower()
                    if key_value not in self.KEY_OPTIONS:
                        key_value = "space"
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
            "ABORT": {"modifier": "alt_l", "key": "q"},
            "WINDOW_TOGGLE": {"modifier": "alt_l", "key": "w"},
            "MUSIC_LYRICS_TOGGLE": {"modifier": "alt_l", "key": "l"},
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
                self._set_combo_by_data(widget.modifier_combo, config["modifier"])
                widget.key_combo.setCurrentText(config["key"].lower())

        # 触发变更信号
        self.on_settings_changed()
