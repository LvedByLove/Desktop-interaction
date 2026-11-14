"""数据目录设置页：存储目录 + 缓存与日志清理."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.utils.cache_cleaner import clear_cache_dirs, clear_log_files
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger
from src.utils.resource_finder import (
    get_keywords_dir,
    get_user_cache_dir,
    get_user_log_dir,
)


class DataDirectoryWidget(QWidget):
    """存储目录 + 缓存/日志清理."""

    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = get_logger(__name__)
        self.config_manager = ConfigManager.get_instance()
        self._path_edits = {}

        self._setup_ui()
        self._load_config_values()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # 存储目录
        storage_group = QGroupBox("存储目录")
        form = QFormLayout(storage_group)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        hint = QLabel(
            "配置文件位置固定，不随此处迁移。留空 = 系统默认路径；"
            "保存后下次启动会把旧目录内容复制到新路径（不删除源文件）。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280; font-size: 12px;")
        form.addRow(hint)

        specs = (
            ("cache", "缓存目录", "选择缓存目录"),
            ("log", "日志目录", "选择日志目录"),
            ("music", "音乐缓存", "选择音乐缓存目录"),
            ("keywords", "唤醒词目录", "选择唤醒词目录"),
        )
        for key, label, title in specs:
            edit = QLineEdit()
            edit.textChanged.connect(self._on_path_text_changed)
            browse = QPushButton("选择")
            browse.clicked.connect(
                lambda _=False, k=key, t=title: self._browse_path(k, t)
            )
            reset = QPushButton("默认")
            reset.clicked.connect(lambda _=False, k=key: self._clear_path(k))
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(edit, 1)
            row.addWidget(browse)
            row.addWidget(reset)
            form.addRow(QLabel(label), row)
            self._path_edits[key] = edit

        layout.addWidget(storage_group)

        # 缓存与日志清理
        clear_group = QGroupBox("缓存与日志")
        clear_layout = QVBoxLayout(clear_group)
        clear_layout.setSpacing(10)

        cache_row = QHBoxLayout()
        cache_label = QLabel("缓存（临时解码文件、音乐缓存等）")
        cache_label.setWordWrap(True)
        self._cache_btn = QPushButton("清空缓存")
        self._cache_btn.setObjectName("clear_cache_btn")
        self._cache_btn.clicked.connect(self._on_clear_cache)
        cache_row.addWidget(cache_label, 1)
        cache_row.addWidget(self._cache_btn)
        clear_layout.addLayout(cache_row)

        log_row = QHBoxLayout()
        log_label = QLabel("日志文件（app.log 及轮转文件）")
        log_label.setWordWrap(True)
        self._log_btn = QPushButton("清空日志")
        self._log_btn.setObjectName("clear_log_btn")
        self._log_btn.clicked.connect(self._on_clear_logs)
        log_row.addWidget(log_label, 1)
        log_row.addWidget(self._log_btn)
        clear_layout.addLayout(log_row)

        layout.addWidget(clear_group)

        layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._update_path_placeholders()

    # ---- 路径 ----

    def _on_path_text_changed(self, _text: str):
        self._update_path_placeholders()
        self.settings_changed.emit()

    def _default_paths(self) -> dict:
        """当前输入框状态下的默认路径（音乐缓存默认跟随缓存目录）."""
        cache_text = ""
        if "cache" in self._path_edits:
            cache_text = self._path_edits["cache"].text().strip()
        try:
            cache_dir = (
                Path(cache_text).expanduser()
                if cache_text
                else get_user_cache_dir(create=False)
            )
        except Exception:
            cache_dir = get_user_cache_dir(create=False)

        try:
            log_dir = get_user_log_dir(create=False)
        except Exception:
            log_dir = Path("")
        try:
            keywords_dir = get_keywords_dir(create=False)
        except Exception:
            keywords_dir = Path("")

        return {
            "cache": str(cache_dir),
            "log": str(log_dir),
            "music": str(cache_dir / "music"),
            "keywords": str(keywords_dir),
        }

    def _update_path_placeholders(self):
        defaults = self._default_paths()
        for key, edit in self._path_edits.items():
            edit.setPlaceholderText(defaults.get(key, ""))

    def _browse_path(self, key: str, title: str):
        edit = self._path_edits.get(key)
        if edit is None:
            return
        start = edit.text().strip() or edit.placeholderText()
        chosen = QFileDialog.getExistingDirectory(self, title, start)
        if chosen:
            edit.setText(chosen)

    def _clear_path(self, key: str):
        edit = self._path_edits.get(key)
        if edit is not None:
            edit.clear()

    def _load_config_values(self):
        try:
            paths = self.config_manager.get_config("PATHS", {}) or {}
            for key, edit in self._path_edits.items():
                value = paths.get(key.upper() + "_DIR")
                if key == "music":
                    value = paths.get("MUSIC_CACHE_DIR")
                edit.setText(str(value).strip() if value else "")
            self._update_path_placeholders()
        except Exception as e:
            self.logger.error(f"加载数据目录配置失败: {e}", exc_info=True)

    # ---- 清理 ----

    def _on_clear_cache(self):
        reply = QMessageBox.question(
            self,
            "清空缓存",
            "确定要清空缓存吗？\n将删除临时解码文件、音乐缓存等缓存内容。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            cleared = clear_cache_dirs()
            QMessageBox.information(self, "完成", f"缓存已清空，共清理 {cleared} 项。")
        except Exception as e:
            self.logger.error(f"清空缓存失败: {e}", exc_info=True)
            QMessageBox.warning(self, "错误", f"清空缓存失败: {e}")

    def _on_clear_logs(self):
        reply = QMessageBox.question(
            self,
            "清空日志",
            "确定要清空日志吗？\n将清空 app.log 及其轮转文件。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            cleared = clear_log_files()
            QMessageBox.information(
                self, "完成", f"日志已清空，共清理 {cleared} 个文件。"
            )
        except Exception as e:
            self.logger.error(f"清空日志失败: {e}", exc_info=True)
            QMessageBox.warning(self, "错误", f"清空日志失败: {e}")

    # ---- 配置读写 ----

    def get_config_data(self) -> dict:
        path_keys = {
            "cache": "PATHS.CACHE_DIR",
            "log": "PATHS.LOG_DIR",
            "music": "PATHS.MUSIC_CACHE_DIR",
            "keywords": "PATHS.KEYWORDS_DIR",
        }
        config_data = {}
        for key, config_key in path_keys.items():
            edit = self._path_edits.get(key)
            if edit is not None:
                config_data[config_key] = edit.text().strip() or None
        return config_data

    def reset_to_defaults(self):
        for key in list(self._path_edits):
            self._clear_path(key)
        self._update_path_placeholders()
