"""音乐与缓存设置组件."""

import os
import shutil
from pathlib import Path

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger
from src.utils.resource_finder import (
    get_default_music_dir,
    get_project_root,
    get_user_cache_dir,
    get_user_data_dir,
)


class MusicCacheWidget(QWidget):
    """
    音乐与缓存设置组件.
    """

    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = get_logger(__name__)
        self.config_manager = ConfigManager.get_instance()
        self._default_music_dir = get_default_music_dir()
        self._default_music_dir_text = self._default_music_dir.as_posix()

        self.music_dir_edit = None

        self._setup_ui()
        self._connect_events()
        self._load_config_values()

    def _setup_ui(self):
        """初始化界面."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        # 音乐目录
        music_group = QGroupBox("本地音乐目录")
        music_layout = QVBoxLayout(music_group)
        music_layout.setSpacing(8)

        hint = QLabel(
            "把喜欢的音乐文件（mp3/flac/wav/m4a 等）放进下面这个目录，"
            "即可通过语音让助手播放。支持子文件夹。"
        )
        hint.setWordWrap(True)
        music_layout.addWidget(hint)

        path_row = QHBoxLayout()
        self.music_dir_edit = QLineEdit()
        self.music_dir_edit.setPlaceholderText(self._default_music_dir_text)
        browse_btn = QPushButton("浏览…")
        browse_btn.setObjectName("music_dir_browse_btn")
        path_row.addWidget(self.music_dir_edit)
        path_row.addWidget(browse_btn)
        music_layout.addLayout(path_row)

        self._default_hint_label = QLabel()
        self._default_hint_label.setStyleSheet("color: #86909c; font-size: 11px;")
        music_layout.addWidget(self._default_hint_label)

        layout.addWidget(music_group)

        # 缓存与日志清理
        clear_group = QGroupBox("缓存与日志")
        clear_layout = QFormLayout(clear_group)
        clear_layout.setSpacing(12)

        self._cache_btn = QPushButton("清空缓存")
        self._cache_btn.setObjectName("clear_cache_btn")
        clear_layout.addRow("缓存（临时解码文件等）:", self._cache_btn)

        self._log_btn = QPushButton("清空日志")
        self._log_btn.setObjectName("clear_log_btn")
        clear_layout.addRow("日志文件（app.log 及轮转文件）:", self._log_btn)

        layout.addWidget(clear_group)
        layout.addStretch()

    def _connect_events(self):
        """连接事件处理."""
        self.music_dir_edit.textChanged.connect(self._on_music_dir_changed)

        browse_btn = self.findChild(QPushButton, "music_dir_browse_btn")
        if browse_btn:
            browse_btn.clicked.connect(self._on_browse_music_dir)

        if self._cache_btn:
            self._cache_btn.clicked.connect(self._on_clear_cache)

        if self._log_btn:
            self._log_btn.clicked.connect(self._on_clear_logs)

    def _load_config_values(self):
        """从配置加载值到UI控件."""
        try:
            music_dir = self.config_manager.get_config("MUSIC.MUSIC_DIR")
            music_dir = str(music_dir).strip() if music_dir else ""

            self.music_dir_edit.blockSignals(True)
            try:
                if not music_dir or self._is_default_dir(music_dir):
                    # 默认目录也显示出来，但用灰色文字与自定义目录区分
                    self.music_dir_edit.setText(self._default_music_dir_text)
                else:
                    self.music_dir_edit.setText(music_dir)
            finally:
                self.music_dir_edit.blockSignals(False)

            self._update_default_hint()
        except Exception as e:
            self.logger.error(f"加载音乐配置失败: {e}", exc_info=True)

    def _on_music_dir_changed(self, _text: str):
        """音乐目录文字变化时更新样式并通知配置变更."""
        self._update_default_hint()
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

    def _update_default_hint(self):
        """更新默认/自定义目录的灰色提示和输入框文字颜色."""
        current = self.music_dir_edit.text().strip()
        is_default = self._is_default_dir(current)

        if is_default:
            self.music_dir_edit.setStyleSheet("QLineEdit { color: #86909c; }")
        else:
            self.music_dir_edit.setStyleSheet("")

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

    def _on_clear_cache(self):
        """清空缓存."""
        reply = QMessageBox.question(
            self,
            "清空缓存",
            "确定要清空缓存吗？\n将删除临时解码文件等缓存内容。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        cleared = 0
        try:
            targets = [
                get_project_root() / "cache",
                get_user_cache_dir(create=False),
            ]
            # 兼容旧版临时音乐缓存目录
            import tempfile

            targets.append(Path(tempfile.gettempdir()) / "xiaozhi_music_cache")

            for directory in targets:
                cleared += self._clear_dir_contents(directory)
            self.logger.info(f"已清空缓存，删除 {cleared} 项")
            QMessageBox.information(self, "完成", f"缓存已清空，共清理 {cleared} 项。")
        except Exception as e:
            self.logger.error(f"清空缓存失败: {e}", exc_info=True)
            QMessageBox.warning(self, "错误", f"清空缓存失败: {str(e)}")

    def _on_clear_logs(self):
        """清空日志文件."""
        reply = QMessageBox.question(
            self,
            "清空日志",
            "确定要清空日志吗？\n将清空 app.log 及其轮转文件。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        cleared = 0
        try:
            log_dirs = []
            try:
                log_dirs.append(get_user_data_dir(create=False) / "logs")
            except Exception:
                pass
            try:
                log_dirs.append(get_project_root() / "logs")
            except Exception:
                pass

            for directory in log_dirs:
                cleared += self._clear_log_files(directory)
            self.logger.info(f"已清空日志，清理 {cleared} 个文件")
            QMessageBox.information(self, "完成", f"日志已清空，共清理 {cleared} 个文件。")
        except Exception as e:
            self.logger.error(f"清空日志失败: {e}", exc_info=True)
            QMessageBox.warning(self, "错误", f"清空日志失败: {str(e)}")

    def _clear_dir_contents(self, directory: Path) -> int:
        """递归删除目录下所有内容（保留目录本身），返回删除条目数。"""
        if not directory or not directory.exists():
            return 0
        count = 0
        for path in directory.iterdir():
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink()
                count += 1
            except Exception as e:
                self.logger.debug(f"删除失败: {path}, {e}")
        return count

    def _clear_log_files(self, directory: Path) -> int:
        """清空/删除日志目录下的日志文件（当前 app.log 截断，其余删除）。"""
        if not directory or not directory.exists():
            return 0
        count = 0
        for path in directory.glob("*.log"):
            try:
                if path.name == "app.log":
                    # 活动日志：截断以保持文件句柄有效
                    with open(path, "w", encoding="utf-8"):
                        pass
                else:
                    path.unlink()
                count += 1
            except Exception as e:
                self.logger.debug(f"清空日志失败: {path}, {e}")
        return count

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
            config_data["MUSIC"] = existing
        except Exception as e:
            self.logger.error(f"获取音乐配置数据失败: {e}", exc_info=True)
        return config_data

    def reset_to_defaults(self):
        """重置为默认值."""
        try:
            self.music_dir_edit.setText(self._default_music_dir_text)
            self._update_default_hint()
            self.logger.info("音乐配置已重置为默认值")
        except Exception as e:
            self.logger.error(f"重置音乐配置失败: {e}", exc_info=True)