"""关于页面组件."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget


class AboutWidget(QWidget):
    """
    关于页面组件.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """
        初始化界面.
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header = QLabel(
            "<div style='font-size:22px; font-weight:600;'>Desktop interaction</div>"
            "<div style='font-size:13px; color:#6b7280; margin-top:4px;'>"
            "一个基于小智生态的桌面语音交互客户端"
            "</div>"
        )
        self._configure_link_label(header)
        layout.addWidget(header)

        intro_group = QGroupBox("项目说明")
        intro_layout = QVBoxLayout(intro_group)
        intro_layout.setSpacing(10)

        description = QLabel(
            "<p style='line-height:1.7; margin:0;'>"
            "本程序基于 "
            "<a href='https://github.com/huangjunsen0406/py-xiaozhi'>py-xiaozhi</a> "
            "进行非官方的二次开发，依托于 "
            "<a href='https://xiaozhi.me/'>小智</a> "
            "服务器提供语音交互能力，本程序由 "
            "<a href='https://github.com/FantasyNetworkCN/NekoMusicDocs'>Neko云音乐</a> "
            "API 提供技术支持。"
            "</p>"
        )
        self._configure_link_label(description)
        intro_layout.addWidget(description)
        layout.addWidget(intro_group)

        links_group = QGroupBox("相关项目与服务")
        links_layout = QVBoxLayout(links_group)
        links_layout.setSpacing(12)

        links_layout.addWidget(
            self._create_link_card(
                "GitHub",
                "py-xiaozhi",
                "本程序的基础项目",
                "https://github.com/huangjunsen0406/py-xiaozhi",
            )
        )
        links_layout.addWidget(
            self._create_link_card(
                "服务",
                "小智服务器",
                "提供语音交互与智能对话服务",
                "https://xiaozhi.me/",
            )
        )
        links_layout.addWidget(
            self._create_link_card(
                "GitHub",
                "Neko云音乐",
                "音乐搜索与播放相关接口",
                "https://github.com/FantasyNetworkCN/NekoMusicDocs",
            )
        )

        layout.addWidget(links_group)
        layout.addStretch()

    def _create_link_card(
        self, badge: str, title: str, description: str, url: str
    ) -> QLabel:
        """
        创建链接卡片.
        """
        badge_color = "#24292f" if badge == "GitHub" else "#2563eb"
        label = QLabel(
            "<div style='line-height:1.6;'>"
            f"<span style='background-color:{badge_color}; color:white; "
            "font-size:11px; font-weight:600; padding:2px 6px;'>"
            f"{badge}"
            "</span> "
            f"<a href='{url}' style='font-size:15px; font-weight:600;'>{title}</a>"
            f"<div style='color:#6b7280; margin-top:2px;'>{description}</div>"
            f"<div style='color:#6b7280; font-size:12px;'>{url}</div>"
            "</div>"
        )
        self._configure_link_label(label)
        label.setStyleSheet(
            "QLabel {"
            "  padding: 10px;"
            "  border: 1px solid rgba(0, 0, 0, 32);"
            "  border-radius: 8px;"
            "  background: rgba(255, 255, 255, 150);"
            "}"
        )
        return label

    def _configure_link_label(self, label: QLabel):
        """
        配置可点击链接标签.
        """
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        label.setOpenExternalLinks(True)
