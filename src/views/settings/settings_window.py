import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger
from src.utils.resource_finder import resource_finder
from src.views.components.sidebar_button import SidebarButton
from src.views.theme import apply_settings_dialog_style


class SettingsWindow(QDialog):
    """
    参数配置窗口.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = get_logger(__name__)
        self.config_manager = ConfigManager.get_instance()

        # 组件引用
        self.system_options_tab = None
        self.mcp_tools_tab = None
        self.wake_word_tab = None
        self.camera_tab = None
        self.audio_tab = None
        self.shortcuts_tab = None
        self.data_directory_tab = None
        self.music_cache_tab = None

        # UI控件
        self.ui_controls = {}

        # 懒加载页面状态
        self._tab_specs = []
        self._tab_built = []
        self._nav_buttons = []
        self._stack = None
        self._section_title = None
        self._status_label = None

        # 初始化UI
        self._setup_ui()
        self._connect_events()

    def _setup_ui(self):
        """
        设置UI界面（模板风格：左侧导航 + 右侧白色圆角卡片）.
        """
        try:
            self.setWindowFlags(
                Qt.Window
                | Qt.WindowMinimizeButtonHint
                | Qt.WindowMaximizeButtonHint
                | Qt.WindowCloseButtonHint
            )
            # 确保不保持在最上层（主窗口带 WindowStaysOnTopHint，避免被连带置顶）
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.setWindowTitle("Desktop-interaction")
            self._set_window_icon()
            self.setObjectName("SettingsWindow")

            self._build_layout()
            self._add_component_tabs()

            apply_settings_dialog_style(self)
            self.resize(920, 760)
            self.setMinimumSize(760, 580)
            self._center_on_screen()

        except Exception as e:
            self.logger.error(f"设置UI失败: {e}", exc_info=True)
            raise

    def _build_layout(self):
        """构建页面骨架：标题区 / 左侧导航 / 内容卡片 / 底部操作栏."""
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        # 页面标题
        header = QVBoxLayout()
        header.setSpacing(2)
        self._page_title = QLabel("参数设置")
        self._page_title.setObjectName("SettingsPageTitle")
        self._page_subtitle = QLabel("管理语音、设备、快捷方式与音乐")
        self._page_subtitle.setObjectName("SettingsPageSubtitle")
        header.addWidget(self._page_title)
        header.addWidget(self._page_subtitle)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(24)

        # 左侧导航
        self._sidebar = QWidget()
        self._sidebar.setObjectName("SettingsSidebar")
        self._sidebar.setFixedWidth(210)
        self._sidebar_layout = QVBoxLayout(self._sidebar)
        self._sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self._sidebar_layout.setSpacing(4)
        body.addWidget(self._sidebar)

        # 右侧内容卡片
        self._content_card = QFrame()
        self._content_card.setObjectName("SettingsContentCard")
        self._content_card.setAttribute(Qt.WA_StyledBackground, True)
        card_layout = QVBoxLayout(self._content_card)
        card_layout.setContentsMargins(24, 22, 24, 18)
        card_layout.setSpacing(14)

        self._section_title = QLabel("系统选项")
        self._section_title.setObjectName("SettingsSectionTitle")
        card_layout.addWidget(self._section_title)

        self._stack = QStackedWidget()
        self._stack.setObjectName("SettingsStack")
        card_layout.addWidget(self._stack, 1)

        separator = QFrame()
        separator.setObjectName("SettingsSeparator")
        separator.setFixedHeight(1)
        card_layout.addWidget(separator)

        # 底部操作栏
        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._status_label = QLabel("修改后点击「保存」生效")
        self._status_label.setObjectName("SettingsStatusLabel")
        footer.addWidget(self._status_label, 1)

        # 保存成功提示：显示几秒后自动恢复为默认提示，窗口保持打开
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._reset_status_label)

        self.ui_controls["reset_btn"] = QPushButton("重置")
        self.ui_controls["reset_btn"].setObjectName("reset_btn")
        self.ui_controls["cancel_btn"] = QPushButton("取消")
        self.ui_controls["cancel_btn"].setObjectName("cancel_btn")
        self.ui_controls["save_btn"] = QPushButton("保存")
        self.ui_controls["save_btn"].setObjectName("save_btn")
        for key in ("reset_btn", "cancel_btn", "save_btn"):
            self.ui_controls[key].setCursor(Qt.PointingHandCursor)
            self.ui_controls[key].setMinimumWidth(88)
        footer.addWidget(self.ui_controls["reset_btn"])
        footer.addWidget(self.ui_controls["cancel_btn"])
        footer.addWidget(self.ui_controls["save_btn"])
        card_layout.addLayout(footer)

        body.addWidget(self._content_card, 1)
        root.addLayout(body, 1)

    def _set_window_icon(self):
        """
        设置窗口图标.
        """
        icon_path = resource_finder.find_file("assets/image/Redstone_orange.png")
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            self.logger.warning(
                "未找到设置窗口图标资源: assets/image/Redstone_orange.png"
            )

    def _center_on_screen(self):
        """
        将窗口居中到屏幕正中间（首次打开时调用）.
        """
        try:
            screen = QApplication.primaryScreen()
            if screen:
                rect = screen.availableGeometry()
                x = rect.x() + (rect.width() - self.width()) // 2
                y = rect.y() + (rect.height() - self.height()) // 2
                self.move(x, y)
        except Exception as e:
            self.logger.debug(f"居中窗口失败: {e}")

    def _add_component_tabs(self):
        """
        添加左侧导航与内容页.

        只创建占位页；真正重的组件（导入 cv2/sounddevice、枚举设备等）
        等到用户切换到对应页时再懒加载，避免打开设置面板时明显卡顿。
        """
        try:
            self._tab_specs = [
                ("系统选项", self._build_system_options_tab, "sliders"),
                ("MCP 工具", self._build_mcp_tools_tab, "grid"),
                ("唤醒词", self._build_wake_word_tab, "mic"),
                ("摄像头", self._build_camera_tab, "camera"),
                ("音频设备", self._build_audio_tab, "speaker"),
                ("数据目录", self._build_data_directory_tab, "database"),
                ("音乐", self._build_music_cache_tab, "music"),
            ]
            self._tab_built = [False] * len(self._tab_specs)
            self._nav_buttons = []

            for index, (title, _, icon_kind) in enumerate(self._tab_specs):
                button = SidebarButton(title, icon_kind)
                button.clicked.connect(
                    lambda _checked=False, i=index: self._on_nav_clicked(i)
                )
                self._sidebar_layout.addWidget(button)
                self._nav_buttons.append(button)
                # 占位页，切换时再替换为真实组件
                self._stack.addWidget(QWidget())

            self._sidebar_layout.addStretch(1)

            if self._nav_buttons:
                self._nav_buttons[0].setChecked(True)
            self._on_nav_clicked(0)
            self.logger.debug("设置导航已建立（按需懒加载）")

        except Exception as e:
            self.logger.error(f"添加设置页失败: {e}", exc_info=True)

    def _on_nav_clicked(self, index: int):
        """左侧导航点击：懒加载并切换到对应页面."""
        if index < 0 or index >= len(self._tab_specs):
            return
        self._ensure_tab(index)
        self._stack.setCurrentIndex(index)
        if 0 <= index < len(self._nav_buttons):
            self._nav_buttons[index].setChecked(True)
            self._section_title.setText(self._tab_specs[index][0])

    def _ensure_tab(self, index: int):
        """切换到某个页面时才构建其真实内容."""
        try:
            if index < 0 or index >= len(self._tab_specs):
                return
            if self._tab_built[index]:
                return
            self._tab_built[index] = True

            title, builder, _ = self._tab_specs[index]
            placeholder = self._stack.widget(index)
            widget = builder()
            if widget is None:
                self._tab_built[index] = False
                return

            # 只让页面容器透明，避免 QSS 的宽泛规则影响页面内部的按钮/输入框
            widget.setProperty("settingsPage", True)
            self._stack.removeWidget(placeholder)
            self._stack.insertWidget(index, widget)
            try:
                widget.style().unpolish(widget)
                widget.style().polish(widget)
            except Exception:
                pass
            if placeholder is not None:
                placeholder.deleteLater()

            if hasattr(widget, "settings_changed"):
                widget.settings_changed.connect(self._on_settings_changed)

            # 模板使用「标签在上、输入框在下」的表单；音频页为节省高度保留左右布局，
            # 让底部设备信息日志框无需滚动即可见。
            compact_form = title == "音频设备"
            for form in widget.findChildren(QFormLayout):
                if compact_form:
                    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
                    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    form.setRowWrapPolicy(QFormLayout.WrapAllRows)
                    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                form.setVerticalSpacing(10)
                form.setHorizontalSpacing(12)

            self.logger.debug(f"设置页已加载: {title}")
        except Exception as e:
            # 允许用户再次切换该页时重试，而不是永久显示空白占位
            if 0 <= index < len(self._tab_built):
                self._tab_built[index] = False
            self.logger.error(f"加载设置页失败 (index={index}): {e}", exc_info=True)

    # ---- 各标签页构建器（内部 import，避免启动时加载重依赖）----

    def _build_system_options_tab(self):
        from src.views.settings.components.shortcuts_settings import (
            ShortcutsSettingsWidget,
        )
        from src.views.settings.components.system_options import SystemOptionsWidget

        self.system_options_tab = SystemOptionsWidget()

        # 快捷键不再是独立页签，作为「系统选项」页的一个区块
        self.shortcuts_tab = ShortcutsSettingsWidget(embedded=True)
        self.system_options_tab.add_section("快捷键", self.shortcuts_tab)
        try:
            self.shortcuts_tab.settings_changed.connect(
                self.system_options_tab._emit_settings_changed
            )
        except Exception:
            pass
        return self.system_options_tab

    def _build_mcp_tools_tab(self):
        from src.views.settings.components.mcp_tools import McpToolsWidget

        self.mcp_tools_tab = McpToolsWidget()
        return self.mcp_tools_tab

    def _build_wake_word_tab(self):
        from src.views.settings.components.wake_word import WakeWordWidget

        self.wake_word_tab = WakeWordWidget()
        return self.wake_word_tab

    def _build_camera_tab(self):
        from src.views.settings.components.camera import CameraWidget

        self.camera_tab = CameraWidget()
        return self.camera_tab

    def _build_audio_tab(self):
        from src.views.settings.components.audio import AudioWidget

        self.audio_tab = AudioWidget()
        return self.audio_tab

    def _build_data_directory_tab(self):
        from src.views.settings.components.data_directory import DataDirectoryWidget

        self.data_directory_tab = DataDirectoryWidget()
        return self.data_directory_tab

    def _build_music_cache_tab(self):
        from src.views.settings.components.music_cache import MusicCacheWidget

        self.music_cache_tab = MusicCacheWidget()
        return self.music_cache_tab

    def _on_settings_changed(self):
        """
        设置变更回调.
        """
        # 可以在此添加一些提示或者其他逻辑

    def _show_status(self, text: str, *, success: bool = True, duration: int = 3000):
        """在底部显示临时提示，超时后自动恢复默认文案."""
        if not hasattr(self, "_status_timer"):
            return
        color = "#16a34a" if success else "#dc2626"
        self._status_label.setText(text)
        self._status_label.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 600;"
        )
        self._status_timer.stop()
        self._status_timer.start(duration)

    def _reset_status_label(self):
        """恢复底部默认提示文案与颜色."""
        try:
            self._status_label.setText("修改后点击「保存」生效")
            self._status_label.setStyleSheet("")
        except Exception:
            pass

    def _get_ui_controls(self):
        """
        获取UI控件引用.
        """
        # 只需要获取主要的按钮控件
        self.ui_controls.update(
            {
                "save_btn": self.findChild(QPushButton, "save_btn"),
                "cancel_btn": self.findChild(QPushButton, "cancel_btn"),
                "reset_btn": self.findChild(QPushButton, "reset_btn"),
            }
        )

    def _connect_events(self):
        """
        连接事件处理.
        """
        if self.ui_controls["save_btn"]:
            self.ui_controls["save_btn"].clicked.connect(self._on_save_clicked)

        if self.ui_controls["cancel_btn"]:
            self.ui_controls["cancel_btn"].clicked.connect(self.reject)

        if self.ui_controls["reset_btn"]:
            self.ui_controls["reset_btn"].clicked.connect(self._on_reset_clicked)

    # 配置加载现在由各个组件自行处理，不需要在主窗口中处理

    # 移除了不再需要的控件操作方法，现在由各个组件处理

    def _on_save_clicked(self):
        """
        保存按钮点击事件.

        只有真正需要重启的配置变更才弹窗；音乐、快捷键、开机自启动等
        热更新项保存后直接生效并关闭窗口。
        """
        try:
            before = self.config_manager.snapshot()
            success = self._save_all_config()
            if not success:
                self._show_status("配置保存失败", success=False, duration=4000)
                QMessageBox.warning(self, "错误", "配置保存失败，请检查输入的值。")
                return

            after = self.config_manager.snapshot()
            changed = self._diff_config(before, after)

            # 快捷键可以运行时重载，不需要重启
            if any(path.startswith("SHORTCUTS.") for path in changed):
                self._reload_shortcuts_runtime()

            needs_restart = any(self._requires_restart(path) for path in changed)
            if needs_restart:
                reply = QMessageBox.question(
                    self,
                    "配置保存成功",
                    "部分设置需要重启才能生效。\n\n是否现在重启？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if reply == QMessageBox.Yes:
                    self._restart_application()
                    return
                self._show_status("配置已保存，部分设置重启后生效")
            else:
                # 热更新项：不弹窗、不关闭窗口，仅显示临时提示
                self._show_status("配置已保存并已生效")

        except Exception as e:
            self.logger.error(f"保存配置失败: {e}", exc_info=True)
            QMessageBox.critical(self, "错误", f"保存配置时发生错误: {str(e)}")

    @staticmethod
    def _diff_config(before, after, prefix: str = "") -> list:
        """递归比较两份配置，返回发生变化的点分路径列表."""
        if isinstance(before, dict) and isinstance(after, dict):
            changes = []
            for key in set(before) | set(after):
                path = f"{prefix}.{key}" if prefix else str(key)
                changes.extend(
                    SettingsWindow._diff_config(before.get(key), after.get(key), path)
                )
            return changes
        return [prefix] if before != after else []

    @staticmethod
    def _requires_restart(path: str) -> bool:
        """判断某个配置项是否需要重启才能生效."""
        # 热更新项：保存后立即生效
        if path == "SYSTEM_OPTIONS.AUTOSTART":
            return False
        if path.startswith("MUSIC.") or path.startswith("SHORTCUTS."):
            return False
        if path == "MCP_FILE_TOOLS.ROOT":
            return False
        if path.startswith("MCP_FILE_TOOLS."):
            # 启用/关闭文件工具需要重启后重新注册工具列表
            return True
        # 其余（网络/音频/摄像头/唤醒词/数据目录/MCP 等）需要重启
        return True

    def _reload_shortcuts_runtime(self):
        """保存快捷键后让运行中的快捷键插件立即重新加载配置."""
        try:
            from src.application import Application

            app = Application.get_instance()
            plugin = app.plugins.get_plugin("shortcuts") if app else None
            if plugin is None or not hasattr(plugin, "reload_from_config"):
                return

            if getattr(app, "_main_loop", None) and not app._main_loop.is_closed():
                app.spawn(plugin.reload_from_config(), name="settings:reload_shortcuts")
            else:
                import asyncio

                asyncio.get_running_loop().create_task(plugin.reload_from_config())
        except Exception as e:
            self.logger.warning(f"运行时重载快捷键失败: {e}")

    def _save_all_config(self) -> bool:
        """
        保存所有配置.
        """
        try:
            # 从各个组件收集配置数据
            all_config_data = {}

            # 系统选项配置
            if self.system_options_tab:
                system_config = self.system_options_tab.get_config_data()
                all_config_data.update(system_config)

            # MCP 工具开关
            if self.mcp_tools_tab:
                all_config_data.update(self.mcp_tools_tab.get_config_data())

            # 唤醒词配置
            if self.wake_word_tab:
                wake_word_config = self.wake_word_tab.get_config_data()
                all_config_data.update(wake_word_config)
                # 保存唤醒词文件
                self.wake_word_tab.save_keywords()

            # 摄像头配置
            if self.camera_tab:
                camera_config = self.camera_tab.get_config_data()
                all_config_data.update(camera_config)

            # 音频设备配置
            if self.audio_tab:
                audio_config = self.audio_tab.get_config_data()
                all_config_data.update(audio_config)

            # 快捷键配置
            if self.shortcuts_tab:
                # 快捷键组件有自己的保存方法
                self.shortcuts_tab.apply_settings()

            # 数据目录配置
            if self.data_directory_tab:
                all_config_data.update(self.data_directory_tab.get_config_data())

            # 音乐配置
            if self.music_cache_tab:
                music_cache_config = self.music_cache_tab.get_config_data()
                all_config_data.update(music_cache_config)

            # 批量更新配置
            for config_path, value in all_config_data.items():
                self.config_manager.update_config(config_path, value)

            # 应用开机自启动设置（写入/移除系统注册表）
            if self.system_options_tab:
                self.system_options_tab.apply_autostart()

            self.logger.info("配置保存成功")
            return True

        except Exception as e:
            self.logger.error(f"保存配置时出错: {e}", exc_info=True)
            return False

    def _on_reset_clicked(self):
        """
        重置按钮点击事件.
        """
        reply = QMessageBox.question(
            self,
            "确认重置",
            "确定要重置所有配置为默认值吗？\n这将清除当前的所有设置。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self._reset_to_defaults()

    def _reset_to_defaults(self):
        """
        重置为默认值.
        """
        try:
            # 让各个组件重置为默认值
            if self.system_options_tab:
                self.system_options_tab.reset_to_defaults()

            if self.mcp_tools_tab:
                self.mcp_tools_tab.reset_to_defaults()

            if self.wake_word_tab:
                self.wake_word_tab.reset_to_defaults()

            if self.camera_tab:
                self.camera_tab.reset_to_defaults()

            if self.audio_tab:
                self.audio_tab.reset_to_defaults()

            if self.shortcuts_tab:
                self.shortcuts_tab.reset_to_defaults()

            if self.data_directory_tab:
                self.data_directory_tab.reset_to_defaults()

            if self.music_cache_tab:
                self.music_cache_tab.reset_to_defaults()

            self.logger.info("所有组件配置已重置为默认值")

        except Exception as e:
            self.logger.error(f"重置配置失败: {e}", exc_info=True)
            QMessageBox.critical(self, "错误", f"重置配置时发生错误: {str(e)}")

    def _restart_application(self):
        """
        重启应用程序.
        """
        try:
            self.logger.info("用户选择重启应用程序")

            # 关闭设置窗口
            self.accept()

            # 直接重启程序
            self._direct_restart()

        except Exception as e:
            self.logger.error(f"重启应用程序失败: {e}", exc_info=True)
            QMessageBox.warning(
                self, "重启失败", "自动重启失败，请手动重启软件以使配置生效。"
            )

    def _direct_restart(self):
        """
        直接重启程序.
        """
        try:
            import subprocess

            from PySide6.QtWidgets import QApplication

            # 获取当前执行的程序路径和参数
            python = sys.executable
            script = sys.argv[0]
            script_path = Path(script).resolve()
            args = []
            skip_next = False
            executable_paths = {Path(sys.executable).resolve(), script_path}
            for arg in sys.argv[1:]:
                if skip_next:
                    skip_next = False
                    continue
                if arg == "--wait-parent-pid":
                    skip_next = True
                    continue
                try:
                    if Path(arg).resolve() in executable_paths:
                        continue
                except Exception:
                    pass
                args.append(arg)
            restart_args = ["--wait-parent-pid", str(os.getpid()), *args]

            # 启动新实例（使用subprocess避免路径空格问题）
            # 打包环境判定：
            #   - PyInstaller 会设置 sys.frozen，argv[0] 就是程序 exe；
            #   - Nuitka 不设置 sys.frozen（那是 PyInstaller 的做法），它的标志是
            #     编译后的模块里有 __compiled__（在 globals() 里，不在 sys.modules）；
            #   - 最后再兜一层“argv[0] 本身就是可执行文件”，不依赖任何框架内部行为。
            is_frozen = (
                getattr(sys, "frozen", False)
                or "__compiled__" in globals()
                or (script_path.suffix.lower() == ".exe" and script_path.exists())
            )

            if is_frozen:
                # 打包环境：直接用程序自身的 exe 启动。
                # 不能用 sys.executable——Nuitka 产物里它指向 <安装目录>\python.exe，
                # 而该文件并不存在，实测会抛 FileNotFoundError: [WinError 2]。
                launcher = script_path if script_path.exists() else Path(sys.executable)
                command = [str(launcher), *restart_args]
                cwd = str(launcher.parent)
            else:
                # 开发环境：python 解释器 + 脚本
                command = [python, script, *restart_args]
                cwd = None

            self.logger.info(f"重启命令: {' '.join(command)}")
            subprocess.Popen(command, cwd=cwd)

            # 关闭当前应用：走优雅关闭路径，不能直接 QApplication.quit()——
            # 那会提前停掉 qasync 循环，在 Application.run() 收尾之前触发
            # "Event loop stopped before Future completed"。
            import asyncio

            from PySide6.QtCore import QTimer

            from src.application import Application

            app = Application.get_instance()
            if not app:
                QApplication.quit()
                return

            loop = asyncio.get_event_loop()
            if not loop.is_running():
                QApplication.quit()
                return

            shutdown_task = asyncio.create_task(app.shutdown())

            def force_quit():
                if not shutdown_task.done():
                    self.logger.warning("重启关闭超时，强制退出")
                    shutdown_task.cancel()
                QApplication.quit()

            # 新实例等待旧进程最多10秒，兜底超时需短于该值
            QTimer.singleShot(3000, force_quit)

        except Exception as e:
            self.logger.error(f"直接重启失败: {e}", exc_info=True)

    def closeEvent(self, event):
        """
        窗口关闭事件.
        """
        self.logger.debug("设置窗口已关闭")
        super().closeEvent(event)
