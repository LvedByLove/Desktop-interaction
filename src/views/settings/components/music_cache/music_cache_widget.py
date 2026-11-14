"""音乐与缓存设置组件."""

import os
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
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
from src.utils.resource_finder import (
    get_default_music_dir,
)
from src.views.components.arrow_combo_box import ArrowComboBox
from src.views.components.password_toggle import attach_password_toggle


class MusicCacheWidget(QWidget):
    """
    音乐与缓存设置组件.
    """

    settings_changed = Signal()

    def _emit_settings_changed(self, *_args):
        """兼容带参数的控件信号（PySide6 不允许把多余参数传给零参信号）."""
        self.settings_changed.emit()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = get_logger(__name__)
        self.config_manager = ConfigManager.get_instance()
        self._default_music_dir = get_default_music_dir()
        self._default_music_dir_text = self._default_music_dir.as_posix()

        self.music_dir_edit = None
        self.music_search_edit = None
        self.music_api_edit = None
        self.music_api_key_edit = None
        self.music_quality_combo = None

        self._setup_ui()
        self._connect_events()
        self._load_config_values()

    def _setup_ui(self):
        """初始化界面."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # 音乐目录（与数据目录页的「存储目录」保持一致）
        music_group = QGroupBox("本地音乐目录")
        music_layout = QFormLayout(music_group)
        music_layout.setSpacing(10)
        music_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        hint = QLabel(
            "把喜欢的音乐文件（mp3/flac/wav/m4a 等）放进下面这个目录，"
            "即可通过语音让助手播放。支持子文件夹。留空 = 默认目录。"
        )
        hint.setWordWrap(True)
        music_layout.addRow(hint)

        self.music_dir_edit = QLineEdit()
        self.music_dir_edit.setPlaceholderText(self._default_music_dir_text)

        browse_btn = QPushButton("选择")
        browse_btn.setObjectName("music_dir_browse_btn")
        default_btn = QPushButton("默认")
        default_btn.setObjectName("music_dir_default_btn")

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(self.music_dir_edit, 1)
        path_row.addWidget(browse_btn)
        path_row.addWidget(default_btn)
        music_layout.addRow(QLabel("目录路径"), path_row)

        layout.addWidget(music_group)

        # 在线音乐 API
        api_group = QGroupBox("音乐 API")
        api_layout = QFormLayout(api_group)
        api_layout.setSpacing(10)

        api_hint = QLabel(
            "留空使用内置默认接口。搜索 API 使用酷我搜索，"
            "直链 API 用于解析播放地址（需配合 API Key）。"
        )
        api_hint.setWordWrap(True)
        api_hint.setStyleSheet("color: #6b7280; font-size: 12px;")
        api_layout.addRow(api_hint)

        self.music_search_edit = QLineEdit()
        self.music_search_edit.setPlaceholderText("留空使用默认酷我搜索 API")
        api_layout.addRow("搜索 API:", self.music_search_edit)

        self.music_api_edit = QLineEdit()
        self.music_api_edit.setPlaceholderText("留空使用默认 lx-music-api")
        api_layout.addRow("直链 API:", self.music_api_edit)

        self.music_api_key_edit = QLineEdit()
        self.music_api_key_edit.setPlaceholderText("留空使用默认 API Key")
        attach_password_toggle(self.music_api_key_edit)
        api_layout.addRow("API Key:", self.music_api_key_edit)

        self.music_quality_combo = ArrowComboBox()
        self.music_quality_combo.addItems(["320k", "128k"])
        api_layout.addRow("默认音质:", self.music_quality_combo)

        layout.addWidget(api_group)

        layout.addStretch()

    def _connect_events(self):
        """连接事件处理."""
        self.music_dir_edit.textChanged.connect(self._on_music_dir_changed)

        for edit in (
            self.music_search_edit,
            self.music_api_edit,
            self.music_api_key_edit,
        ):
            edit.textChanged.connect(self._emit_settings_changed)
        self.music_quality_combo.currentTextChanged.connect(self._emit_settings_changed)

        browse_btn = self.findChild(QPushButton, "music_dir_browse_btn")
        if browse_btn:
            browse_btn.clicked.connect(self._on_browse_music_dir)

        default_btn = self.findChild(QPushButton, "music_dir_default_btn")
        if default_btn:
            default_btn.clicked.connect(self.music_dir_edit.clear)

    def _load_config_values(self):
        """从配置加载值到UI控件."""
        try:
            music_dir = self.config_manager.get_config("MUSIC.MUSIC_DIR")
            music_dir = str(music_dir).strip() if music_dir else ""

            self.music_dir_edit.blockSignals(True)
            try:
                # 默认目录留空，由占位符展示实际默认路径
                if not music_dir or self._is_default_dir(music_dir):
                    self.music_dir_edit.clear()
                else:
                    self.music_dir_edit.setText(music_dir)
            finally:
                self.music_dir_edit.blockSignals(False)

            music_cfg = self.config_manager.get_config("MUSIC", {}) or {}
            self.music_search_edit.setText(str(music_cfg.get("SEARCH_URL") or ""))
            self.music_api_edit.setText(str(music_cfg.get("URL_API") or ""))
            self.music_api_key_edit.setText(str(music_cfg.get("URL_API_KEY") or ""))
            quality = str(music_cfg.get("DEFAULT_QUALITY") or "320k")
            index = self.music_quality_combo.findText(quality)
            self.music_quality_combo.setCurrentIndex(index if index >= 0 else 0)

        except Exception as e:
            self.logger.error(f"加载音乐配置失败: {e}", exc_info=True)

    def _on_music_dir_changed(self, _text: str):
        """音乐目录文字变化时更新样式并通知配置变更."""
        self.settings_changed.emit()

    def _normalize_dir(self, value) -> str:
        """规范化目录路径，便于在 Windows 上忽略大小写和分隔符差异."""
        value = str(value).strip().strip('"')
        if not value:
            return ""
        try:
            path = os.path.abspath(os.path.expanduser(value))
        except Exception:
            path = value
        return os.path.normcase(os.path.normpath(path))

    def _is_default_dir(self, value) -> bool:
        """判断是否为默认音乐目录（含空值回退默认目录的情况）."""
        if not str(value).strip():
            return True
        return self._normalize_dir(value) == self._normalize_dir(
            self._default_music_dir
        )

    def _on_browse_music_dir(self):
        """浏览选择音乐目录."""
        current = self.music_dir_edit.text().strip()
        start = (
            current
            if current and Path(current).is_dir()
            else self._default_music_dir_text
        )
        chosen = QFileDialog.getExistingDirectory(self, "选择本地音乐目录", start)
        if chosen:
            self.music_dir_edit.setText(chosen)

    def get_config_data(self) -> dict:
        """获取当前配置数据."""
        config_data = {}
        try:
            existing = dict(self.config_manager.get_config("MUSIC", {}) or {})
            music_dir = self.music_dir_edit.text().strip()
            # 默认目录保存为空值，确保下次启动仍按默认设置处理
            existing["MUSIC_DIR"] = (
                None if self._is_default_dir(music_dir) else music_dir
            )
            existing["SEARCH_URL"] = self.music_search_edit.text().strip() or None
            existing["URL_API"] = self.music_api_edit.text().strip() or None
            existing["URL_API_KEY"] = (
                self.music_api_key_edit.text().strip() or None
            )
            existing["DEFAULT_QUALITY"] = (
                self.music_quality_combo.currentText() or "320k"
            )
            config_data["MUSIC"] = existing
        except Exception as e:
            self.logger.error(f"获取音乐配置数据失败: {e}", exc_info=True)
        return config_data

    def reset_to_defaults(self):
        """重置为默认值."""
        try:
            self.music_dir_edit.clear()
            self.music_search_edit.clear()
            self.music_api_edit.clear()
            self.music_api_key_edit.clear()
            index = self.music_quality_combo.findText("320k")
            self.music_quality_combo.setCurrentIndex(index if index >= 0 else 0)
            self.logger.info("音乐配置已重置为默认值")
        except Exception as e:
            self.logger.error(f"重置音乐配置失败: {e}", exc_info=True)