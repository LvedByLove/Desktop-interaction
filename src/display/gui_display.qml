import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    color: "transparent"
    radius: hiddenHeight / 2

    // 信号定义 - 与 Python 回调对接
    signal sendButtonClicked(string text)
    signal windowResizeRequested(int width, int height)
    signal hiddenModeChanged(bool isHidden)
    // 聆听声纹停留结束、已回到待机短横线
    signal listeningSettled()

    // 状态机：hidden（顶部短横线，待机）/ input / listening / tts / transcript
    property string viewMode: "hidden"
    property bool isHidden: false

    // 窗口尺寸状态
    // 待机形态：屏幕正上方的短横线（类似滚动条滑块）
    property int hiddenWidth: 88
    property int hiddenHeight: 14
    property int hiddenBarWidth: 64
    property int hiddenBarHeight: 5
    property bool hiddenBarHovered: false
    property int inputWidth: 520
    property int inputHeight: 54
    property int ttsWidth: 320
    property int ttsHeight: 42
    property int transcriptWidth: 460
    property int transcriptHeight: 360

    Behavior on width {
        NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
    }
    Behavior on height {
        NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
    }
    Behavior on radius {
        NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
    }
    Behavior on x {
        NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
    }
    Behavior on y {
        NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
    }
    Behavior on color {
        // 略长于窗口收缩，让深色胶囊在收回过程中保持可见，而不是提前淡没
        ColorAnimation { duration: 260; easing.type: Easing.InOutCubic }
    }

    function modeWidth(mode) {
        if (mode === "input") return inputWidth
        if (mode === "tts") return ttsWidth
        if (mode === "transcript") return transcriptWidth
        return hiddenWidth
    }

    function modeHeight(mode) {
        if (mode === "input") return inputHeight
        if (mode === "tts") return ttsHeight
        if (mode === "transcript") return transcriptHeight
        return hiddenHeight
    }

    function modeRadius(mode) {
        if (mode === "input") return inputHeight / 2
        if (mode === "tts") return 18
        if (mode === "transcript") return 20
        return hiddenHeight / 2
    }

    function applyMode(mode) {
        var wasHidden = isHidden
        viewMode = mode
        isHidden = mode === "hidden"

        // 离开待机态时先清掉悬浮高亮：短横线隐藏后 MouseArea 不再收到
        // onExited，否则下次回到待机态会残留"悬浮高亮"。
        if (mode !== "hidden") {
            hiddenBarHovered = false
        }

        var targetWidth = modeWidth(mode)
        var targetHeight = modeHeight(mode)
        root.radius = modeRadius(mode)
        // 待机短横线和声纹聆听态都没有胶囊背景
        if (mode === "hidden" || mode === "listening") {
            root.color = "transparent"
        } else if (mode === "input") {
            root.color = Qt.rgba(20/255, 20/255, 20/255, 0.94)
        } else {
            root.color = Qt.rgba(20/255, 20/255, 20/255, 1)
        }
        root.windowResizeRequested(targetWidth, targetHeight)

        // 从其它形态收回到短横线时，横线弹入
        if (!wasHidden && mode === "hidden") {
            hiddenBarEnterAnim.restart()
        }

        if (wasHidden !== isHidden) {
            root.hiddenModeChanged(isHidden)
        }

        if (mode === "tts") {
            restartTtsScroll()
        } else {
            stopTtsScroll()
        }

        if (mode === "input") {
            inputFocusTimer.restart()
        }
    }

    function focusInputText() {
        if (viewMode !== "input") return
        inputText.forceActiveFocus()
        inputText.cursorPosition = inputText.text.length
    }

    // 回到待机短横线
    function showLine() {
        listeningSettleTimer.stop()
        if (viewMode === "hidden") return
        var wasListening = viewMode === "listening"
        hiddenBarHovered = false
        applyMode("hidden")
        // 通知 UI 插件：聆听界面已完全收起，可按用户意愿恢复歌词
        if (wasListening) root.listeningSettled()
    }

    // 短横线左键：直接展开输入框
    function exitHiddenMode() {
        if (viewMode !== "hidden") return
        hiddenBarHovered = false
        showInput()
    }

    function canShowInput() {
        if (viewMode === "listening") return false
        if (viewMode === "transcript") return false
        if (viewMode === "tts" && displayModel && displayModel.ttsVisible) return false
        return true
    }

    function showInput() {
        if (!canShowInput()) return
        idleInputTimer.stop()
        applyMode("input")
        idleInputTimer.restart()
    }

    function hideInput() {
        if (viewMode !== "input") return
        idleInputTimer.stop()
        inputText.text = ""
        inputText.focus = false
        showLine()
    }

    function toggleInput() {
        if (viewMode === "input") {
            hideInput()
        } else {
            showInput()
        }
    }

    function sendInputText() {
        var message = inputText.text.trim()
        if (message.length <= 0) return
        root.sendButtonClicked(message)
        hideInput()
    }

    // 按住说话进入聆听态。
    // 若正在播报/显示歌词，则临时接管胶囊：内容回缩淡出、声纹弹入；
    // 聆听结束后是否恢复歌词由 UI 插件按用户收起状态决定。
    function showListening() {
        listeningSettleTimer.stop()
        applyMode("listening")
        listeningEnterAnim.restart()
    }

    function hideListening() {
        if (viewMode !== "listening") return
        // 松开后先保留一小段声纹作为"处理中"过渡；
        // 若这段时间内收到播报，showTts 会取消计时器，直接从声纹展开成胶囊。
        listeningSettleTimer.restart()
    }

    // 窗口几何动画结束后，由 Python 侧按真实指针位置校正短横线悬浮状态。
    // 收缩过程中 MouseArea 可能先判定为"鼠标进入"，而窗口缩走后收不到
    // "离开"事件，会残留悬浮高亮；这里以指针是否仍在窗口内为准覆盖一次。
    function syncHover(inside) {
        hiddenBarHovered = !!inside
    }

    function showTts(text) {
        listeningSettleTimer.stop()
        applyMode("tts")
    }

    function hideTts() {
        // 若用户正在查看对话记录，不打断（保持大胶囊显示）
        if (viewMode === "transcript") return
        showLine()
    }

    // 文本能完整放进胶囊时水平居中；超出时才从左侧开始滚动。
    function syncTtsTextPosition() {
        if (!ttsText || !textContainer) return
        // 滚动过程中不要覆盖滚动动画设置的 x
        if (scrollTimer.running) return
        var containerWidth = textContainer.width
        var textWidth = ttsText.width
        if (textWidth <= 0 || containerWidth <= 0) return
        ttsText.x = textWidth < containerWidth ? Math.round((containerWidth - textWidth) / 2) : 0
    }

    function restartTtsScroll() {
        scrollTimer.stop()
        scrollBackTimer.stop()
        syncTtsTextPosition()
        showTimer.restart()
    }

    function stopTtsScroll() {
        showTimer.stop()
        scrollTimer.stop()
        scrollBackTimer.stop()
        syncTtsTextPosition()
    }

    function refreshCurrentModeSize() {
        if (viewMode !== "tts") return
        root.radius = modeRadius(viewMode)
        root.windowResizeRequested(modeWidth(viewMode), modeHeight(viewMode))
        restartTtsScroll()
    }

    function showTranscript() {
        if (viewMode === "transcript") return
        hiddenBarHovered = false
        applyMode("transcript")
        transcriptScrollTimer.restart()
    }

    function hideTranscript() {
        if (viewMode !== "transcript") return
        // 收起历史记录后：若仍在播报则回到播报胶囊，否则回到短横线
        if (displayModel && displayModel.ttsVisible) {
            applyMode("tts")
        } else {
            showLine()
        }
    }

    function transcriptScrollToBottom() {
        if (!transcriptFlickable || !transcriptText) return
        transcriptFlickable.contentY = Math.max(0, transcriptText.height - transcriptFlickable.height)
    }

    Connections {
        target: displayModel
        function onTtsTextChanged() {
            if (root.viewMode === "tts") {
                root.restartTtsScroll()
            }
        }
        function onTtsVisibleChanged() {
            if (!displayModel) return
            if (displayModel.ttsVisible) {
                root.showTts(displayModel.ttsText)
            } else if (root.viewMode === "tts") {
                root.hideTts()
            }
        }
        function onMusicLyricsActiveChanged() {
            root.refreshCurrentModeSize()
        }
        function onMusicLyricsCollapsedChanged() {
            root.refreshCurrentModeSize()
        }
        function onTtsTranscriptChanged() {
            if (root.viewMode === "transcript") {
                transcriptScrollToBottom()
            }
        }
    }

    Timer {
        id: idleInputTimer
        interval: 5000
        repeat: false
        onTriggered: {
            if (viewMode === "input" && inputText.text.length === 0 && !inputText.activeFocus) {
                root.hideInput()
            }
        }
    }

    Timer {
        id: inputFocusTimer
        interval: 240
        repeat: false
        onTriggered: root.focusInputText()
    }

    Timer {
        id: transcriptScrollTimer
        interval: 60
        repeat: false
        onTriggered: transcriptScrollToBottom()
    }

    // 松开语音键后声纹的停留时间；期间若开始播报，会取消该计时器直接声纹→胶囊
    Timer {
        id: listeningSettleTimer
        interval: 900
        repeat: false
        onTriggered: root.showLine()
    }

    // 待机态：屏幕正上方的短横线（类似滚动条滑块）
    // 左键展开输入框，右键打开历史记录
    Item {
        id: hiddenBarItem
        anchors.fill: parent
        visible: opacity > 0
        opacity: viewMode === "hidden" ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }

        // 悬停时的柔和光晕
        Rectangle {
            anchors.centerIn: parent
            width: hiddenBar.width + 8
            height: 10
            radius: height / 2
            color: "#52b7ff"
            opacity: hiddenBarHovered ? 0.18 : 0
            Behavior on opacity { NumberAnimation { duration: 180 } }
        }

        Rectangle {
            id: hiddenBar
            anchors.centerIn: parent
            width: hiddenBarHovered ? 78 : hiddenBarWidth
            height: hiddenBarHovered ? 6 : hiddenBarHeight
            radius: height / 2
            // 中性灰蓝：浅色/深色桌面上都能看清，悬停时提亮。
            // 中转站唤醒条从这条短横线的位置展开
            // （见 file_tray_window.qml 与 gui_display.py 的线形变形逻辑）。
            color: hiddenBarHovered ? "#eef7ff"
                                      : Qt.rgba(158/255, 170/255, 184/255, 0.62)
            Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            Behavior on color { ColorAnimation { duration: 160 } }
        }

        MouseArea {
            id: hiddenBarArea
            anchors.fill: parent
            hoverEnabled: true
            enabled: viewMode === "hidden"
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: Qt.PointingHandCursor
            onEntered: hiddenBarHovered = true
            onExited: hiddenBarHovered = false
            onClicked: function(mouse) {
                if (mouse.button === Qt.RightButton) {
                    root.showTranscript()
                } else {
                    root.exitHiddenMode()
                }
            }
        }
    }

    // 聆听态入场：光晕向外扩散并淡出（打断播报 / 按下说话都会触发一次）
    ParallelAnimation {
        id: listeningEnterAnim

        NumberAnimation {
            target: listeningRipple
            property: "scale"
            from: 0.3
            to: 1.0
            duration: 380
            easing.type: Easing.OutCubic
        }

        NumberAnimation {
            target: listeningRipple
            property: "opacity"
            from: 0.9
            to: 0
            duration: 420
            easing.type: Easing.OutCubic
        }
    }

    SequentialAnimation {
        id: hiddenBarEnterAnim
        running: false
        NumberAnimation {
            target: hiddenBar
            property: "scale"
            from: 0.3
            to: 1.08
            duration: 180
            easing.type: Easing.OutCubic
        }
        NumberAnimation {
            target: hiddenBar
            property: "scale"
            to: 1.0
            duration: 100
            easing.type: Easing.OutCubic
        }
    }

    // 聆听态：短横线断开成一排长短不一、持续跳动的声纹小段（无胶囊背景）
    Item {
        id: listeningWaveItem
        anchors.fill: parent
        visible: opacity > 0
        opacity: viewMode === "listening" ? 1 : 0
        scale: viewMode === "listening" ? 1 : 0.7
        Behavior on opacity { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }

        // 进入聆听态时扩散一次的光晕：按下快捷键 / 打断播报时给一点"已听到"的反馈
        Rectangle {
            id: listeningRipple
            anchors.centerIn: parent
            width: root.hiddenWidth
            height: root.hiddenHeight
            radius: height / 2
            color: Qt.rgba(82/255, 183/255, 255/255, 0.16)
            border.width: 1
            border.color: Qt.rgba(82/255, 183/255, 255/255, 0.5)
            opacity: 0
            scale: 0.3
        }

        Row {
            id: listeningBarsRow
            anchors.centerIn: parent
            spacing: 6

            Repeater {
                model: 7

                Item {
                    width: 6
                    height: root.hiddenHeight

                    Rectangle {
                        anchors.centerIn: parent
                        width: 6
                        height: 5
                        radius: width / 2
                        color: "#52b7ff"

                        // 进入聆听态时每根声纹错峰"弹"起来，避免一排线段同时闪出
                        SequentialAnimation on scale {
                            running: root.viewMode === "listening"
                            PauseAnimation { duration: index * 28 }
                            NumberAnimation {
                                from: 0.25
                                to: 1.0
                                duration: 300
                                easing.type: Easing.OutBack
                            }
                        }

                        SequentialAnimation on height {
                            loops: Animation.Infinite
                            running: root.viewMode === "listening"
                            PauseAnimation { duration: (index % 4) * 55 }
                            NumberAnimation {
                                from: 4 + (index % 2) * 2
                                to: 9 + (index % 3)
                                duration: 260 + (index % 4) * 45
                                easing.type: Easing.InOutSine
                            }
                            NumberAnimation {
                                from: 9 + (index % 3)
                                to: 4 + (index % 2) * 2
                                duration: 260 + (index % 4) * 45
                                easing.type: Easing.InOutSine
                            }
                        }
                    }
                }
            }
        }
    }

    // 播报态：只是一个胶囊窗口，文本单行滚动
    RowLayout {
        id: ttsContent
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.topMargin: 7
        anchors.bottomMargin: 7
        opacity: viewMode === "tts" ? 1 : 0
        enabled: viewMode === "tts"
        // 收起时文字/内容向中心缩放淡出，配合窗口几何收缩形成"收回"效果
        scale: viewMode === "tts" ? 1 : 0.55
        transformOrigin: Item.Center
        Behavior on opacity { NumberAnimation { duration: 240; easing.type: Easing.InOutCubic } }
        Behavior on scale { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        // 收起时内容向上聚拢，展开时从短横线向下展开
        transform: Translate {
            y: viewMode === "tts" ? 0 : -10
            Behavior on y { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        }

        Item {
            id: textContainer
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            // 胶囊宽度变化（窗口缩放/歌词态切换）后重新计算居中位置
            onWidthChanged: root.syncTtsTextPosition()

            Text {
                id: ttsText
                // 显式让宽度跟随文本内容宽度，否则 Text 默认 width 为 0，
                // 会导致滚动判断 (ttsText.width > containerWidth) 永远为 false，
                // 长文本无法滚动而被裁剪。
                width: implicitWidth
                height: parent.height
                // 布局测量完成后按容器宽度决定居中还是左对齐
                onWidthChanged: root.syncTtsTextPosition()
                text: displayModel ? displayModel.ttsText : "语音播报中..."
                font.family: "Microsoft YaHei UI"
                font.pixelSize: 14
                font.weight: Font.Medium
                color: "white"
                verticalAlignment: Text.AlignVCenter
                wrapMode: Text.NoWrap
            }
        }

        Timer {
            id: showTimer
            interval: 800
            repeat: false
            onTriggered: {
                if (viewMode !== "tts") return
                var textWidth = ttsText.width
                // 文本尚未完成布局测量时，稍后再试一次
                if (textWidth <= 0) {
                    showTimer.restart()
                    return
                }
                if (textWidth > textContainer.width) {
                    scrollTimer.start()
                }
            }
        }

        Timer {
            id: scrollTimer
            interval: 40
            repeat: true
            onTriggered: {
                var textWidth = ttsText.width
                var containerWidth = textContainer.width
                if (ttsText.x > containerWidth - textWidth - 12) {
                    ttsText.x -= 3
                } else {
                    scrollTimer.stop()
                    scrollBackTimer.start()
                }
            }
        }

        Timer {
            id: scrollBackTimer
            interval: 700
            repeat: false
            onTriggered: {
                root.syncTtsTextPosition()
                if (viewMode === "tts" && ttsText.width > textContainer.width) {
                    showTimer.restart()
                }
            }
        }
    }

    RowLayout {
        id: inputContent
        anchors.fill: parent
        anchors.leftMargin: 18
        anchors.rightMargin: 8
        anchors.topMargin: 7
        anchors.bottomMargin: 7
        spacing: 10
        opacity: viewMode === "input" ? 1 : 0
        enabled: viewMode === "input"
        Behavior on opacity { NumberAnimation { duration: 180 } }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            TextInput {
                id: inputText
                anchors.fill: parent
                verticalAlignment: TextInput.AlignVCenter
                font.family: "Microsoft YaHei UI"
                font.pixelSize: 15
                color: "white"
                selectedTextColor: "white"
                selectionColor: "#165dff"
                selectByMouse: true
                clip: true
                onTextChanged: idleInputTimer.restart()
                onActiveFocusChanged: {
                    if (activeFocus) {
                        idleInputTimer.stop()
                    } else if (viewMode === "input") {
                        idleInputTimer.restart()
                    }
                }

                Keys.onReturnPressed: root.sendInputText()
                Keys.onEnterPressed: root.sendInputText()
                Keys.onEscapePressed: root.hideInput()
            }

            Text {
                anchors.fill: parent
                text: "请输入简短字句，长句请用语音..."
                font.family: "Microsoft YaHei UI"
                font.pixelSize: 15
                color: Qt.rgba(255/255, 255/255, 255/255, 0.42)
                verticalAlignment: Text.AlignVCenter
                visible: inputText.text.length === 0
            }
        }

        Button {
            id: inputSendBtn
            Layout.preferredWidth: 72
            Layout.preferredHeight: 40
            text: "发送"
            enabled: inputText.text.trim().length > 0
            background: Rectangle {
                color: !inputSendBtn.enabled ? "#242424" : (inputSendBtn.pressed ? "#0e42d2" : "#165dff")
                radius: height / 2
            }
            contentItem: Text {
                text: inputSendBtn.text
                font.family: "Microsoft YaHei UI"
                font.pixelSize: 14
                font.weight: Font.Medium
                color: inputSendBtn.enabled ? "white" : Qt.rgba(255/255, 255/255, 255/255, 0.35)
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            onClicked: root.sendInputText()
        }
    }

    Item {
        id: transcriptContent
        anchors.fill: parent
        opacity: viewMode === "transcript" ? 1 : 0
        enabled: viewMode === "transcript"
        Behavior on opacity { NumberAnimation { duration: 180 } }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 28

                Text {
                    Layout.fillWidth: true
                    text: "对话记录"
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    color: "white"
                }

                Rectangle {
                    id: transcriptCloseBtn
                    Layout.preferredWidth: 26
                    Layout.preferredHeight: 26
                    radius: 13
                    color: transcriptCloseArea.pressed ? "#3a3a3a" : "#262626"
                    border.color: Qt.rgba(255/255, 255/255, 255/255, 0.18)
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: "×"
                        color: Qt.rgba(255/255, 255/255, 255/255, 0.85)
                        font.pixelSize: 16
                    }

                    MouseArea {
                        id: transcriptCloseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.hideTranscript()
                    }
                }
            }

            Flickable {
                id: transcriptFlickable
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: width
                contentHeight: transcriptText.height

                Text {
                    id: transcriptText
                    width: transcriptFlickable.width
                    text: (displayModel && displayModel.ttsTranscript) ? displayModel.ttsTranscript : "暂无对话记录"
                    color: (displayModel && displayModel.ttsTranscript) ? Qt.rgba(255/255, 255/255, 255/255, 0.92) : Qt.rgba(255/255, 255/255, 255/255, 0.4)
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 14
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }

                ScrollBar.vertical: ScrollBar {
                    policy: ScrollBar.AsNeeded
                }
            }
        }
    }

    Component.onCompleted: {
        applyMode("hidden")
    }
}