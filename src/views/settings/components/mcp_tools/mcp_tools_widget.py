"""MCP 工具开关设置组件."""

import html
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger
from src.views.components.toggle_switch import ToggleSwitch
from src.views.theme import apply_settings_dialog_style

# 工具名称 -> (显示名称, 简短说明)
_TOOL_META = {
    # 应用程序
    "self.application.launch": ("启动应用", "按名称启动本机应用程序。"),
    "self.application.kill": ("关闭应用", "按名称关闭正在运行的应用程序。"),
    "self.application.list_running": ("查看运行中的应用", "列出当前运行的应用和进程。"),
    "self.application.scan_installed": ("扫描已安装应用", "扫描并列出已安装的应用程序。"),
    # 音量
    "self.audio_speaker.get_volume": ("查询系统音量", "读取当前系统音量。"),
    "self.audio_speaker.set_volume": ("设置系统音量", "设置系统音量（0–100）。"),
    # 屏幕亮度
    "self.display.get_brightness": ("查询屏幕亮度", "读取当前屏幕亮度。"),
    "self.display.set_brightness": ("设置屏幕亮度", "设置屏幕亮度（0–100）。"),
    # 浏览器
    "self.browser.open_url": ("打开网页", "用默认浏览器打开指定网址。"),
    # 日程
    "self.calendar.create_event": ("创建日程", "新建日程，并自动检测时间冲突。"),
    "self.calendar.get_events": ("查询日程", "按时间范围查询日程安排。"),
    "self.calendar.get_upcoming_events": ("即将到来的日程", "查看未来一段时间内的日程。"),
    "self.calendar.update_event": ("修改日程", "修改已有日程的标题、时间等信息。"),
    "self.calendar.delete_event": ("删除日程", "永久删除指定日程。"),
    "self.calendar.delete_events_batch": (
        "批量删除日程",
        "按条件批量删除日程，或清空全部日程。",
    ),
    "self.calendar.get_categories": ("日程分类", "获取可用的日程分类列表。"),
    # 倒计时
    "timer.start_countdown": ("启动倒计时", "倒计时结束后执行指定操作。"),
    "timer.cancel_countdown": ("取消倒计时", "按 ID 取消正在运行的倒计时。"),
    "timer.get_active_timers": ("查看倒计时", "列出当前活跃的倒计时。"),
    # 音乐
    "music_player.search_and_play": (
        "在线搜索并播放",
        "按歌名在线搜索，下载后自动播放。",
    ),
    "music_player.play_local_song_by_id": (
        "播放本地歌曲",
        "播放本地音乐目录中的指定歌曲。",
    ),
    "music_player.search_local_music": (
        "搜索本地音乐",
        "按关键词在本地音乐目录中查找。",
    ),
    "music_player.get_local_playlist": (
        "获取本地歌单",
        "列出本地音乐目录中的所有歌曲。",
    ),
    "music_player.play_random_local_song": (
        "随机播放一首",
        "随机播放一首本地歌曲，播完停止。",
    ),
    "music_player.start_shuffle_play": (
        "随机连续播放",
        "随机连续播放本地歌曲，自动切歌。",
    ),
    "music_player.pause": ("暂停音乐", "暂停当前歌曲，保留播放位置。"),
    "music_player.resume": ("恢复音乐", "从暂停位置继续播放。"),
    "music_player.stop": ("停止音乐", "停止播放并回到歌曲开头。"),
    "music_player.seek": ("跳转播放进度", "跳到指定时间或播放百分比。"),
    "music_player.get_lyrics": ("获取歌词", "获取当前歌曲的歌词。"),
    # 拍照 / 截图
    "take_photo": ("拍照识图", "拍照并分析画面内容，回答相关问题。"),
    "take_screenshot": ("桌面截图分析", "截取屏幕并分析画面或提取文字。"),
    # 文件操作
    "file.create_or_write": (
        "新建 / 覆盖文件",
        "在允许目录内创建或覆盖文本文件。",
    ),
    "file.append": ("追加文件内容", "向文件末尾追加文本，不存在则创建。"),
    "file.read": ("读取文件", "读取允许目录内的文本文件。"),
    "file.list_dir": ("列出目录", "列出目录内的文件和文件夹。"),
    "file.create_dir": ("新建文件夹", "在允许目录内创建文件夹。"),
}

_GROUP_LABELS = {
    "self.application": "应用程序",
    "self.audio_speaker": "音量控制",
    "self.display": "屏幕亮度",
    "self.browser": "浏览器",
    "self.calendar": "日程管理",
    "timer": "倒计时",
    "music_player": "音乐播放",
    "take_photo": "拍照识图",
    "take_screenshot": "桌面截图",
    "file": "文件操作",
}

_GROUP_ORDER = [
    "self.application",
    "self.audio_speaker",
    "self.display",
    "self.browser",
    "self.calendar",
    "timer",
    "music_player",
    "take_photo",
    "take_screenshot",
    "file",
]


def _group_key(name: str) -> str:
    parts = name.split(".")
    if parts[0] == "self" and len(parts) > 1:
        return "self." + parts[1]
    if len(parts) > 1:
        return parts[0]
    return name


def _fallback_description(raw: str) -> str:
    """从模型的工具说明中截取一段适合人看的短句."""
    text = " ".join((raw or "").split())
    if not text:
        return "暂无说明"
    for sep in ("。", "；", "！", "？", "!", "?"):
        index = text.find(sep)
        if 0 < index <= 80:
            return text[:index]
    for sep in (". ",):
        index = text.find(sep)
        if 0 < index <= 80:
            return text[:index]
    return text[:80] + ("…" if len(text) > 80 else "")


class McpToolsWidget(QWidget):
    """按分组列出所有 MCP 工具，允许禁用（下次启动生效）."""

    settings_changed = Signal()

    def _emit_settings_changed(self, *_args):
        """兼容带参数的控件信号（PySide6 不允许把多余参数传给零参信号）."""
        self.settings_changed.emit()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = get_logger(__name__)
        self.config_manager = ConfigManager.get_instance()
        self._checks = {}
        self._tool_catalog = {}
        self.file_root_edit = None
        self.file_enabled_switch = None
        self._file_root_controls = []
        self._file_tool_checks = []
        self._file_switch_guard = False
        self._file_dialog_pending = False

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        hint = QLabel(
            "取消勾选后，该工具不会注册给模型。保存并重启程序后生效；"
            "禁用不常用的工具可以减少模型选错工具的情况。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(hint)

        toolbar = QHBoxLayout()
        select_all = QPushButton("全选")
        select_none = QPushButton("全不选")
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none.clicked.connect(lambda: self._set_all(False))
        toolbar.addWidget(select_all)
        toolbar.addWidget(select_none)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self._groups_layout = QVBoxLayout(container)
        self._groups_layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout.setSpacing(12)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        self._build_tool_groups()

    def _load_catalog(self) -> dict:
        """从 McpServer 获取工具目录（含被禁用的）."""
        catalog = {}
        try:
            from src.mcp.mcp_server import McpServer

            server = McpServer.get_instance()
            catalog.update(getattr(server, "tool_catalog", {}) or {})
            for tool in getattr(server, "tools", []) or []:
                catalog.setdefault(tool.name, tool.description or "")
        except Exception as e:
            self.logger.warning(f"读取 MCP 工具目录失败: {e}")
        return catalog

    def _build_tool_groups(self):
        self._tool_catalog = self._load_catalog()
        disabled = set(
            self.config_manager.get_config("MCP_TOOLS.DISABLED", []) or []
        )

        if not self._tool_catalog:
            empty = QLabel("当前没有可配置的 MCP 工具。")
            empty.setStyleSheet("color: #6b7280;")
            self._groups_layout.addWidget(empty)
            return

        grouped = {}
        for name in self._tool_catalog:
            grouped.setdefault(_group_key(name), []).append(name)

        ordered_keys = [key for key in _GROUP_ORDER if key in grouped]
        ordered_keys += sorted(key for key in grouped if key not in _GROUP_ORDER)

        for group_key in ordered_keys:
            names = sorted(
                grouped[group_key],
                key=lambda name: _TOOL_META.get(name, (name, ""))[0],
            )
            box = QGroupBox(_GROUP_LABELS.get(group_key, group_key))
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 6, 0, 2)
            box_layout.setSpacing(8)

            if group_key == "file":
                box.setObjectName("FileToolsGroup")
                box_layout.setContentsMargins(4, 10, 4, 4)

                disclaimer = QLabel(
                    "<b>使用声明与免责声明</b><br>"
                    "文件操作工具默认为关闭状态。启用后，AI 可以在允许目录内新建、读取、"
                    "覆盖或追加文本文件。请仅授权你信任的专用工作目录，不要选择系统盘根目录、"
                    "Windows、Program Files 等敏感位置。⚠️启用则代表已熟知风险，因 AI 生成内容、误操作或文件覆盖等工具造成的"
                    "任何损失由使用者自行承担。"
                )
                disclaimer.setTextFormat(Qt.RichText)
                disclaimer.setWordWrap(True)
                disclaimer.setStyleSheet(
                    "background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px;"
                    "color: #92400e; padding: 10px; font-size: 12px;"
                )
                box_layout.addWidget(disclaimer)

                master_row = QHBoxLayout()
                master_label = QLabel("启用文件操作工具（⚠️测试版本，有风险）")
                master_label.setStyleSheet(
                    "font-size: 13px; color: #111827; font-weight: 600;"
                )
                self.file_enabled_switch = ToggleSwitch("")
                self.file_enabled_switch.setFixedWidth(48)
                try:
                    file_enabled = bool(
                        self.config_manager.get_config(
                            "MCP_FILE_TOOLS.ENABLED", False
                        )
                    )
                except Exception:
                    file_enabled = False
                self.file_enabled_switch.setChecked(file_enabled)
                self.file_enabled_switch.stateChanged.connect(
                    self._on_file_enabled_changed
                )
                master_row.addWidget(master_label, 1)
                master_row.addWidget(self.file_enabled_switch)
                box_layout.addLayout(master_row)

                restart_hint = QLabel(
                    "启用或关闭文件工具后，需要重启程序才会重新注册工具列表。"
                )
                restart_hint.setStyleSheet("color: #6b7280; font-size: 12px;")
                restart_hint.setWordWrap(True)
                box_layout.addWidget(restart_hint)

                root_label = QLabel("允许操作目录")
                root_label.setStyleSheet(
                    "font-size: 13px; color: #374151; font-weight: 600;"
                )
                box_layout.addWidget(root_label)

                self.file_root_edit = QLineEdit()
                self.file_root_edit.setPlaceholderText(str(Path.home()))
                try:
                    saved_root = self.config_manager.get_config(
                        "MCP_FILE_TOOLS.ROOT", None
                    )
                except Exception:
                    saved_root = None
                self.file_root_edit.setText(
                    str(saved_root).strip() if saved_root else ""
                )
                self.file_root_edit.textChanged.connect(
                    self._emit_settings_changed
                )

                root_browse = QPushButton("选择")
                root_browse.clicked.connect(self._browse_file_root)
                root_default = QPushButton("默认")
                root_default.clicked.connect(self.file_root_edit.clear)

                root_row = QHBoxLayout()
                root_row.setSpacing(8)
                root_row.addWidget(self.file_root_edit, 1)
                root_row.addWidget(root_browse)
                root_row.addWidget(root_default)
                box_layout.addLayout(root_row)

                root_hint = QLabel(
                    "留空 = 当前用户主目录；文件工具只能读写此目录内的文件。"
                )
                root_hint.setStyleSheet("color: #6b7280; font-size: 12px;")
                root_hint.setWordWrap(True)
                box_layout.addWidget(root_hint)

                warning_text = (
                    "警告：AI 可在此目录内创建、覆盖或追加文件。"
                    "请只选择专用工作目录；覆盖前会尝试生成 .bak 备份，"
                    "但不保证一定成功。"
                )
                root_warning = QLabel(warning_text)
                root_warning.setStyleSheet(
                    "color: #dc2626; font-size: 12px; font-weight: 600;"
                )
                root_warning.setWordWrap(True)
                box_layout.addWidget(root_warning)
                self.file_root_edit.setToolTip(warning_text)

                self._file_root_controls = [
                    root_label,
                    self.file_root_edit,
                    root_browse,
                    root_default,
                ]

            file_checks = []
            for name in names:
                check = self._add_tool_row(
                    box_layout, name, name not in disabled
                )
                if group_key == "file":
                    file_checks.append(check)
            if group_key == "file":
                self._file_tool_checks = file_checks
                self._set_file_controls_enabled(
                    self.file_enabled_switch.isChecked()
                )
            self._groups_layout.addWidget(box)

        self._groups_layout.addStretch(1)

    def _browse_file_root(self):
        """选择文件工具允许操作的根目录."""
        if self.file_root_edit is None:
            return
        start = (
            self.file_root_edit.text().strip()
            or self.file_root_edit.placeholderText()
            or str(Path.home())
        )
        chosen = QFileDialog.getExistingDirectory(
            self, "选择文件工具允许目录", start
        )
        if chosen:
            self.file_root_edit.setText(chosen)

    def _on_file_enabled_changed(self, checked):
        """文件工具总开关：启用前弹出风险确认，确认按钮 5 秒后才可用."""
        if self._file_switch_guard:
            return

        if checked:
            # 不要在开关的鼠标事件中同步打开模态框：QAbstractButton 会在
            # 取消后恢复 setChecked(False) 时丢失后续的 stateChanged 信号。
            # 延迟到当前事件处理结束后再弹窗。
            self._file_dialog_pending = True
            QTimer.singleShot(0, self._prompt_file_enable)
            return

        self._set_file_controls_enabled(False)
        self._emit_settings_changed()

    def _prompt_file_enable(self):
        """鼠标事件结束后显示风险确认框."""
        if not self._file_dialog_pending:
            return
        self._file_dialog_pending = False

        if self.file_enabled_switch is None or not self.file_enabled_switch.isChecked():
            return

        if not self._confirm_enable_file_tools():
            # 用户取消：恢复关闭状态
            self._file_switch_guard = True
            try:
                self.file_enabled_switch.setChecked(False)
            finally:
                self._file_switch_guard = False
            self._set_file_controls_enabled(False)
            return

        self._set_file_controls_enabled(True)
        self._emit_settings_changed()

    def _confirm_enable_file_tools(self) -> bool:
        """启用文件工具前的风险确认对话框."""
        dialog = QDialog(self)
        dialog.setObjectName("ThemedDialog")
        dialog.setWindowTitle("启用文件操作工具")
        dialog.setModal(True)
        dialog.setMinimumWidth(560)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(14)

        title = QLabel("启用文件操作工具前请确认")
        title.setStyleSheet(
            "font-size: 18px; font-weight: 600; color: #111827;"
        )
        layout.addWidget(title)

        root_text = ""
        if self.file_root_edit is not None:
            root_text = (
                self.file_root_edit.text().strip()
                or self.file_root_edit.placeholderText()
            )
        if not root_text:
            root_text = str(Path.home())

        body = QLabel(
            "<b>启用后，AI 可以在以下目录内创建、读取、覆盖或追加文本文件：</b><br>"
            f"{html.escape(root_text)}<br><br>"
            "<b>请确认：</b><br>"
            "• 该目录是你信任的专用工作目录；<br>"
            "• 不要选择系统盘根目录、Windows、Program Files 等敏感位置；<br>"
            "• 因 AI 生成内容、误操作或文件覆盖等所有因工具造成的损失由你自行承担；<br>"
            "• 温馨提示：请提前备份重要文件。<br><br>"
            "保存设置并重启程序后，文件工具才会真正注册给模型。"
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setStyleSheet(
            "background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px;"
            "color: #92400e; padding: 12px; font-size: 13px;"
        )
        layout.addWidget(body)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dialog.reject)
        button_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("我已了解风险，确认启用（5）")
        confirm_btn.setEnabled(False)
        confirm_btn.setMinimumWidth(220)
        confirm_btn.setStyleSheet(
            "QPushButton { background: #2563eb; border: 1px solid #2563eb;"
            " border-radius: 8px; color: #ffffff; padding: 9px 16px;"
            " font-weight: 600; }"
            "QPushButton:hover { background: #1d4ed8; border-color: #1d4ed8; }"
            "QPushButton:disabled { background: #e5e7eb; border-color: #e5e7eb;"
            " color: #9ca3af; }"
        )
        button_row.addWidget(confirm_btn)
        layout.addLayout(button_row)

        apply_settings_dialog_style(dialog)

        remaining = {"seconds": 5}
        timer = QTimer(dialog)
        timer.setInterval(1000)

        def tick():
            remaining["seconds"] -= 1
            if remaining["seconds"] > 0:
                confirm_btn.setText(
                    f"我已了解风险，确认启用（{remaining['seconds']}）"
                )
            else:
                timer.stop()
                confirm_btn.setText("我已了解风险，确认启用")
                confirm_btn.setEnabled(True)

        timer.timeout.connect(tick)
        timer.start()

        confirm_btn.clicked.connect(dialog.accept)
        result = dialog.exec()
        timer.stop()
        return result == QDialog.Accepted

    def _set_file_controls_enabled(self, enabled: bool):
        """总开关关闭时禁用目录选择和文件工具单项开关."""
        for widget in self._file_root_controls:
            try:
                widget.setEnabled(enabled)
            except Exception:
                pass
        for check in self._file_tool_checks:
            try:
                check.setEnabled(enabled)
            except Exception:
                pass

    def _add_tool_row(self, parent_layout, name: str, enabled: bool):
        display_name, short_desc = _TOOL_META.get(name, (None, None))
        raw_desc = self._tool_catalog.get(name) or ""
        if not display_name:
            display_name = name.rsplit(".", 1)[-1].replace("_", " ")
        if not short_desc:
            short_desc = _fallback_description(raw_desc)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 5, 2, 5)
        row_layout.setSpacing(12)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)

        # 只展示简短说明，避免把整段模型提示词塞进 tooltip 形成大块浮层
        tooltip = f"{display_name}\n{name}\n{short_desc}"

        title = QLabel(display_name)
        title.setStyleSheet("font-size: 14px; font-weight: 600; color: #111827;")
        title.setToolTip(tooltip)

        desc = QLabel(short_desc)
        desc.setStyleSheet("font-size: 12px; color: #6b7280;")
        desc.setWordWrap(True)
        desc.setToolTip(tooltip)

        text_column.addWidget(title)
        text_column.addWidget(desc)
        row_layout.addLayout(text_column, 1)

        check = ToggleSwitch("")
        check.setFixedWidth(48)
        check.setChecked(enabled)
        check.setToolTip(tooltip)
        check.stateChanged.connect(self._emit_settings_changed)
        row_layout.addWidget(check, 0, Qt.AlignVCenter)

        parent_layout.addWidget(row)
        self._checks[name] = check
        return check

    def _set_all(self, checked: bool):
        for check in self._checks.values():
            check.setChecked(checked)

    def get_config_data(self) -> dict:
        disabled = [
            name for name, check in self._checks.items() if not check.isChecked()
        ]
        config_data = {"MCP_TOOLS.DISABLED": disabled}
        if self.file_enabled_switch is not None:
            config_data["MCP_FILE_TOOLS.ENABLED"] = (
                self.file_enabled_switch.isChecked()
            )
        if self.file_root_edit is not None:
            config_data["MCP_FILE_TOOLS.ROOT"] = (
                self.file_root_edit.text().strip() or None
            )
        return config_data

    def reset_to_defaults(self):
        self._set_all(True)
        if self.file_enabled_switch is not None:
            self.file_enabled_switch.setChecked(False)
            self._set_file_controls_enabled(False)
        if self.file_root_edit is not None:
            self.file_root_edit.clear()
