# -*- coding: utf-8 -*-
"""
GUI 显示窗口数据模型 - 用于 QML 数据绑定.
"""

from PyQt5.QtCore import QObject, pyqtProperty, pyqtSignal


class GuiDisplayModel(QObject):
    """
    GUI 主窗口的数据模型，用于 Python 和 QML 之间的数据绑定.
    """

    # 属性变化信号
    statusTextChanged = pyqtSignal()
    emotionPathChanged = pyqtSignal()
    ttsTextChanged = pyqtSignal()
    ttsVisibleChanged = pyqtSignal()
    musicLyricsActiveChanged = pyqtSignal()
    musicLyricsCollapsedChanged = pyqtSignal()
    buttonTextChanged = pyqtSignal()
    modeTextChanged = pyqtSignal()
    autoModeChanged = pyqtSignal()

    # 用户操作信号
    manualButtonPressed = pyqtSignal()
    manualButtonReleased = pyqtSignal()
    autoButtonClicked = pyqtSignal()
    abortButtonClicked = pyqtSignal()
    modeButtonClicked = pyqtSignal()
    sendButtonClicked = pyqtSignal(str)  # 携带输入的文本

    def __init__(self, parent=None):
        super().__init__(parent)

        # 私有属性
        self._status_text = "状态: 未连接"
        self._emotion_path = ""  # 表情资源路径（GIF/图片）或 emoji 字符
        self._tts_text = "待命"
        self._tts_visible = False  # TTS窗口可见性
        self._music_lyrics_active = False  # 当前 TTS 区域是否显示音乐歌词
        self._music_lyrics_collapsed = False  # 音乐歌词是否折叠为播放指示器
        self._button_text = "开始对话"  # 自动模式按钮文本
        self._mode_text = "手动对话"  # 模式切换按钮文本
        self._auto_mode = False  # 是否自动模式
        self._is_connected = False

    # 状态文本属性
    @pyqtProperty(str, notify=statusTextChanged)
    def statusText(self):
        return self._status_text

    @statusText.setter
    def statusText(self, value):
        if self._status_text != value:
            self._status_text = value
            self.statusTextChanged.emit()

    # 表情路径属性
    @pyqtProperty(str, notify=emotionPathChanged)
    def emotionPath(self):
        return self._emotion_path

    @emotionPath.setter
    def emotionPath(self, value):
        if self._emotion_path != value:
            self._emotion_path = value
            self.emotionPathChanged.emit()

    # TTS 文本属性
    @pyqtProperty(str, notify=ttsTextChanged)
    def ttsText(self):
        return self._tts_text

    @ttsText.setter
    def ttsText(self, value):
        if self._tts_text != value:
            self._tts_text = value
            self.ttsTextChanged.emit()

    # TTS 可见性属性
    @pyqtProperty(bool, notify=ttsVisibleChanged)
    def ttsVisible(self):
        return self._tts_visible

    @ttsVisible.setter
    def ttsVisible(self, value):
        if self._tts_visible != value:
            self._tts_visible = value
            self.ttsVisibleChanged.emit()

    # 音乐歌词状态属性
    @pyqtProperty(bool, notify=musicLyricsActiveChanged)
    def musicLyricsActive(self):
        return self._music_lyrics_active

    @musicLyricsActive.setter
    def musicLyricsActive(self, value):
        value = bool(value)
        if self._music_lyrics_active != value:
            self._music_lyrics_active = value
            self.musicLyricsActiveChanged.emit()

    @pyqtProperty(bool, notify=musicLyricsCollapsedChanged)
    def musicLyricsCollapsed(self):
        return self._music_lyrics_collapsed

    @musicLyricsCollapsed.setter
    def musicLyricsCollapsed(self, value):
        value = bool(value)
        if self._music_lyrics_collapsed != value:
            self._music_lyrics_collapsed = value
            self.musicLyricsCollapsedChanged.emit()

    # 自动模式按钮文本属性
    @pyqtProperty(str, notify=buttonTextChanged)
    def buttonText(self):
        return self._button_text

    @buttonText.setter
    def buttonText(self, value):
        if self._button_text != value:
            self._button_text = value
            self.buttonTextChanged.emit()

    # 模式切换按钮文本属性
    @pyqtProperty(str, notify=modeTextChanged)
    def modeText(self):
        return self._mode_text

    @modeText.setter
    def modeText(self, value):
        if self._mode_text != value:
            self._mode_text = value
            self.modeTextChanged.emit()

    # 自动模式标志属性
    @pyqtProperty(bool, notify=autoModeChanged)
    def autoMode(self):
        return self._auto_mode

    @autoMode.setter
    def autoMode(self, value):
        if self._auto_mode != value:
            self._auto_mode = value
            self.autoModeChanged.emit()

    # 便捷方法
    def update_status(self, status: str, connected: bool):
        """
        更新状态文本和连接状态.
        """
        self.statusText = f"状态: {status}"
        self._is_connected = connected

    def update_text(self, text: str):
        """
        更新 TTS 文本.
        """
        self.ttsText = text

    def set_tts_visible(self, visible: bool):
        """
        设置 TTS 窗口可见性.
        """
        self.ttsVisible = visible

    def set_music_lyrics_active(self, active: bool):
        """
        设置音乐歌词显示状态.
        """
        self.musicLyricsActive = active

    def set_music_lyrics_collapsed(self, collapsed: bool):
        """
        设置音乐歌词折叠状态.
        """
        self.musicLyricsCollapsed = collapsed

    def update_emotion(self, emotion_path: str):
        """
        更新表情路径.
        """
        self.emotionPath = emotion_path

    def update_button_text(self, text: str):
        """
        更新自动模式按钮文本.
        """
        self.buttonText = text

    def update_mode_text(self, text: str):
        """
        更新模式按钮文本.
        """
        self.modeText = text

    def set_auto_mode(self, is_auto: bool):
        """
        设置自动模式.
        """
        self.autoMode = is_auto
        if is_auto:
            self.modeText = "自动对话"
        else:
            self.modeText = "手动对话"
