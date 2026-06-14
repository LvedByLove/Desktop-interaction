import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15

Rectangle {
    id: root
    color: Qt.rgba(20/255, 20/255, 20/255, 1)
    radius: normalHeight / 2

    // 信号定义 - 与 Python 回调对接
    signal sendButtonClicked(string text)
    signal windowResizeRequested(int width, int height)
    signal hiddenModeChanged(bool isHidden)

    // 状态机：dot / hidden / controls / input / tts
    property string viewMode: "dot"
    property bool isHovered: false
    property bool isLineMode: false
    property bool isHidden: false
    property bool isExpanded: false
    property bool canExitHiddenByHover: false

    // 窗口尺寸状态
    property int normalWidth: 36
    property int normalHeight: 36
    property int hiddenWidth: 100
    property int hiddenHeight: 4
    property int expandedWidth: 220
    property int expandedHeight: 46
    property int listeningWidth: 220
    property int listeningHeight: 42
    property int inputWidth: 520
    property int inputHeight: 54
    property int ttsWidth: 320
    property int ttsHeight: 42
    property int musicIndicatorWidth: 56
    property int musicIndicatorHeight: 42

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
        ColorAnimation { duration: 180; easing.type: Easing.OutCubic }
    }

    function modeWidth(mode) {
        if (mode === "hidden") return hiddenWidth
        if (mode === "controls") return expandedWidth
        if (mode === "input") return inputWidth
        if (mode === "listening") return listeningWidth
        if (mode === "tts") return isMusicLyricsCollapsed() ? musicIndicatorWidth : ttsWidth
        return normalWidth
    }

    function modeHeight(mode) {
        if (mode === "hidden") return hiddenHeight
        if (mode === "controls") return expandedHeight
        if (mode === "input") return inputHeight
        if (mode === "listening") return listeningHeight
        if (mode === "tts") return isMusicLyricsCollapsed() ? musicIndicatorHeight : ttsHeight
        return normalHeight
    }

    function modeRadius(mode) {
        if (mode === "hidden") return 2
        if (mode === "controls") return 18
        if (mode === "input") return inputHeight / 2
        if (mode === "listening") return 18
        if (mode === "tts") return 18
        return normalHeight / 2
    }

    function isMusicLyricsCollapsed() {
        return displayModel && displayModel.musicLyricsActive && displayModel.musicLyricsCollapsed
    }

    function applyMode(mode) {
        var wasHidden = isHidden
        viewMode = mode
        isHidden = mode === "hidden"
        isExpanded = mode === "controls" || mode === "input"
        isHovered = mode !== "hidden"

        var targetWidth = modeWidth(mode)
        var targetHeight = modeHeight(mode)
        root.radius = modeRadius(mode)
        root.color = (mode === "controls" || mode === "input" || mode === "listening") ? Qt.rgba(20/255, 20/255, 20/255, 0.94) : Qt.rgba(20/255, 20/255, 20/255, 1)
        root.windowResizeRequested(targetWidth, targetHeight)

        if (wasHidden !== isHidden) {
            root.hiddenModeChanged(isHidden)
        }

        if (mode === "tts" && !isMusicLyricsCollapsed()) {
            restartTtsScroll()
        } else {
            stopTtsScroll()
        }
        listeningPulse.running = mode === "listening"
        listeningWave1.running = mode === "listening"
        listeningWave2.running = mode === "listening"
        listeningWave3.running = mode === "listening"

        if (mode === "input") {
            inputFocusTimer.restart()
        }
    }

    function focusInputText() {
        if (viewMode !== "input") return
        inputText.forceActiveFocus()
        inputText.cursorPosition = inputText.text.length
    }

    function setViewMode(mode) {
        if (viewMode === "tts" && mode !== "tts" && displayModel && displayModel.ttsVisible) {
            return
        }
        applyMode(mode)
    }

    function enterHiddenMode(allowHoverExit) {
        if (viewMode === "tts" || viewMode === "listening" || viewMode === "input") return
        canExitHiddenByHover = false
        hideClickArea.visible = false
        applyMode("hidden")
        if (allowHoverExit === true) {
            hoverEnableTimer.restart()
        }
    }

    function exitHiddenMode() {
        if (viewMode === "tts") return
        hoverEnableTimer.stop()
        canExitHiddenByHover = false
        hideClickArea.visible = false
        applyMode("dot")
        hideTimer.restart()
    }

    function canShowInput() {
        if (viewMode === "listening") return false
        if (viewMode === "tts" && displayModel && displayModel.ttsVisible) return false
        return true
    }

    function canClickShowInput() {
        return (viewMode === "dot" || viewMode === "controls" || viewMode === "tts") && canShowInput()
    }

    function showInput() {
        if (!canShowInput()) return
        shrinkTimer.stop()
        hideTimer.stop()
        idleInputTimer.stop()
        hideClickArea.visible = false
        canExitHiddenByHover = false
        applyMode("input")
        idleInputTimer.restart()
    }

    function hideInput() {
        if (viewMode !== "input") return
        idleInputTimer.stop()
        inputText.text = ""
        inputText.focus = false
        hideClickArea.visible = false
        canExitHiddenByHover = false
        applyMode("dot")
        hideTimer.restart()
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

    function showListening() {
        if (viewMode === "tts") return
        shrinkTimer.stop()
        hideTimer.stop()
        hideClickArea.visible = false
        canExitHiddenByHover = false
        applyMode("listening")
    }

    function hideListening() {
        if (viewMode !== "listening") return
        hideClickArea.visible = false
        canExitHiddenByHover = false
        applyMode("dot")
        hideTimer.restart()
    }

    function showTts(text) {
        shrinkTimer.stop()
        hideTimer.stop()
        hideClickArea.visible = false
        applyMode("tts")
    }

    function hideTts() {
        hideClickArea.visible = false
        canExitHiddenByHover = false
        applyMode("dot")
        hideTimer.restart()
    }

    function restartTtsScroll() {
        ttsText.x = 0
        scrollTimer.stop()
        scrollBackTimer.stop()
        showTimer.restart()
    }

    function stopTtsScroll() {
        showTimer.stop()
        scrollTimer.stop()
        scrollBackTimer.stop()
        ttsText.x = 0
    }

    function refreshCurrentModeSize() {
        if (viewMode !== "tts") return
        root.windowResizeRequested(modeWidth(viewMode), modeHeight(viewMode))
        if (isMusicLyricsCollapsed()) {
            stopTtsScroll()
        } else {
            restartTtsScroll()
        }
    }

    function toggleMusicLyricsCollapsed() {
        if (viewMode !== "tts" || !displayModel || !displayModel.musicLyricsActive) return false
        displayModel.musicLyricsCollapsed = !displayModel.musicLyricsCollapsed
        return true
    }

    Connections {
        target: displayModel
        function onTtsTextChanged() {
            if (root.viewMode === "tts" && !root.isMusicLyricsCollapsed()) {
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
    }

    MouseArea {
        id: hoverArea
        anchors.fill: parent
        hoverEnabled: true
        propagateComposedEvents: true

        onEntered: {
            if (viewMode === "tts" || viewMode === "listening" || viewMode === "input") return
            shrinkTimer.stop()
            hideTimer.stop()
            hideClickArea.visible = false
            applyMode("controls")
        }

        onExited: {
            if (viewMode === "tts" || viewMode === "listening" || viewMode === "input") return
            shrinkTimer.start()
            hideTimer.restart()
        }

        onClicked: function(mouse) {
            if (mouse.button !== Qt.LeftButton) return
            if (root.toggleMusicLyricsCollapsed()) return
            if (!canClickShowInput()) return
            singleClickTimer.restart()
        }

        onDoubleClicked: {
            singleClickTimer.stop()
        }
    }

    Timer {
        id: singleClickTimer
        interval: (Qt.styleHints && Qt.styleHints.mouseDoubleClickInterval) ? Qt.styleHints.mouseDoubleClickInterval : 300
        repeat: false
        onTriggered: {
            if (canClickShowInput()) {
                root.showInput()
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
        id: shrinkTimer
        interval: 180
        repeat: false
        onTriggered: {
            if (!hoverArea.containsMouse && viewMode === "controls") {
                applyMode("dot")
                hideTimer.restart()
            }
        }
    }

    Timer {
        id: hideTimer
        interval: 2000
        repeat: false
        onTriggered: {
            if (!hoverArea.containsMouse && viewMode !== "hidden" && viewMode !== "tts" && viewMode !== "input") {
                enterHiddenMode(true)
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
        id: hoverEnableTimer
        interval: 400
        repeat: false
        onTriggered: {
            if (viewMode === "hidden") {
                canExitHiddenByHover = true
                hideClickArea.visible = true
            }
        }
    }

    MouseArea {
        id: hideClickArea
        anchors.fill: parent
        visible: false
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onEntered: {
            if (canExitHiddenByHover && !hoverEnableTimer.running) {
                exitHiddenMode()
            }
        }
        onClicked: exitHiddenMode()
    }

    Item {
        id: dotContent
        anchors.fill: parent
        opacity: viewMode === "dot" ? 1 : 0
        enabled: viewMode === "dot"
        Behavior on opacity { NumberAnimation { duration: 140 } }

        AnimatedImage {
            anchors.centerIn: parent
            width: 70
            height: 70
            source: "../../assets/emojis/huang2.gif"
            fillMode: Image.PreserveAspectFit
            cache: false
            playing: true
        }
    }

    RowLayout {
        id: controlsContent
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 14
        anchors.topMargin: 6
        anchors.bottomMargin: 6
        spacing: 10
        opacity: viewMode === "controls" ? 1 : 0
        enabled: viewMode === "controls"
        Behavior on opacity { NumberAnimation { duration: 180 } }

        AnimatedImage {
            Layout.preferredWidth: 42
            Layout.preferredHeight: 42
            Layout.alignment: Qt.AlignVCenter
            source: "../../assets/emojis/huang2.gif"
            fillMode: Image.PreserveAspectFit
            cache: false
            playing: true
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 1

            Text {
                Layout.fillWidth: true
                text: "小智 AI 助手"
                font.family: "Segoe UI, Microsoft YaHei UI"
                font.pixelSize: 13
                font.weight: Font.DemiBold
                color: Qt.rgba(255/255, 255/255, 255/255, 0.94)
                elide: Text.ElideRight
            }

            Text {
                Layout.fillWidth: true
                text: displayModel ? displayModel.statusText.replace("状态: ", "") : "待命"
                font.family: "Segoe UI, Microsoft YaHei UI"
                font.pixelSize: 11
                color: Qt.rgba(255/255, 255/255, 255/255, 0.58)
                elide: Text.ElideRight
            }
        }
    }

    RowLayout {
        id: listeningContent
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        anchors.topMargin: 5
        anchors.bottomMargin: 5
        spacing: 10
        opacity: viewMode === "listening" ? 1 : 0
        enabled: viewMode === "listening"
        Behavior on opacity { NumberAnimation { duration: 160 } }

        Item {
            Layout.preferredWidth: 32
            Layout.preferredHeight: 32
            Layout.alignment: Qt.AlignVCenter

            Rectangle {
                id: listeningGlow
                anchors.centerIn: parent
                width: 30
                height: 30
                radius: 15
                color: "transparent"
                border.color: "#52b7ff"
                border.width: 1
                opacity: 0.25

                SequentialAnimation on opacity {
                    id: listeningPulse
                    running: false
                    loops: Animation.Infinite
                    NumberAnimation { from: 0.2; to: 0.7; duration: 520; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: 0.7; to: 0.2; duration: 520; easing.type: Easing.InOutQuad }
                }
            }

            AnimatedImage {
                anchors.centerIn: parent
                width: 54
                height: 54
                source: "../../assets/emojis/huang2.gif"
                fillMode: Image.PreserveAspectFit
                cache: false
                playing: true
            }
        }

        Text {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            text: "正在倾听..."
            font.family: "Segoe UI, Microsoft YaHei UI"
            font.pixelSize: 14
            font.weight: Font.Medium
            color: "white"
            elide: Text.ElideRight
        }

        Row {
            spacing: 4
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 28

            Rectangle {
                width: 4
                height: 10
                radius: 2
                color: "#52b7ff"
                SequentialAnimation on height {
                    id: listeningWave1
                    running: false
                    loops: Animation.Infinite
                    NumberAnimation { from: 8; to: 18; duration: 360; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: 18; to: 8; duration: 360; easing.type: Easing.InOutQuad }
                }
            }
            Rectangle {
                width: 4
                height: 14
                radius: 2
                color: "#52b7ff"
                SequentialAnimation on height {
                    id: listeningWave2
                    running: false
                    loops: Animation.Infinite
                    PauseAnimation { duration: 120 }
                    NumberAnimation { from: 8; to: 20; duration: 360; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: 20; to: 8; duration: 360; easing.type: Easing.InOutQuad }
                }
            }
            Rectangle {
                width: 4
                height: 10
                radius: 2
                color: "#52b7ff"
                SequentialAnimation on height {
                    id: listeningWave3
                    running: false
                    loops: Animation.Infinite
                    PauseAnimation { duration: 240 }
                    NumberAnimation { from: 8; to: 18; duration: 360; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: 18; to: 8; duration: 360; easing.type: Easing.InOutQuad }
                }
            }
        }
    }

    RowLayout {
        id: ttsContent
        property bool collapsed: root.isMusicLyricsCollapsed()
        anchors.fill: parent
        anchors.leftMargin: collapsed ? 16 : 14
        anchors.rightMargin: collapsed ? 16 : 14
        anchors.topMargin: 4
        anchors.bottomMargin: 4
        spacing: collapsed ? 0 : 10
        opacity: viewMode === "tts" ? 1 : 0
        enabled: viewMode === "tts"
        Behavior on opacity { NumberAnimation { duration: 180 } }

        Item {
            width: ttsContent.collapsed ? 0 : 32
            height: 32
            visible: !ttsContent.collapsed
            Layout.preferredWidth: ttsContent.collapsed ? 0 : 32
            Layout.preferredHeight: 32
            Layout.alignment: Qt.AlignVCenter

            Rectangle {
                anchors.centerIn: parent
                width: 34
                height: 34
                radius: 17
                color: "transparent"
                border.color: "#52b7ff"
                border.width: 0.5
                opacity: 0.28
            }

            AnimatedImage {
                anchors.centerIn: parent
                width: 58
                height: 58
                source: "../../assets/emojis/huang1.gif"
                fillMode: Image.PreserveAspectFit
                cache: false
                playing: true
            }
        }

        Item {
            id: textContainer
            visible: !ttsContent.collapsed
            Layout.fillWidth: !ttsContent.collapsed
            Layout.preferredWidth: ttsContent.collapsed ? 0 : 180
            Layout.alignment: Qt.AlignVCenter
            height: 28
            clip: true

            Text {
                id: ttsText
                x: 0
                y: (parent.height - height) / 2
                text: displayModel ? displayModel.ttsText : "语音播报中..."
                font.family: "Segoe UI, Microsoft YaHei UI"
                font.pixelSize: 14
                font.weight: Font.Medium
                color: "white"
                verticalAlignment: Text.AlignVCenter
                height: 20
                wrapMode: Text.NoWrap
            }
        }

        Timer {
            id: showTimer
            interval: 1200
            repeat: false
            onTriggered: {
                if (viewMode !== "tts" || root.isMusicLyricsCollapsed()) return
                if (ttsText.width > textContainer.width) {
                    scrollTimer.start()
                }
            }
        }

        Timer {
            id: scrollTimer
            interval: 80
            repeat: true
            onTriggered: {
                var textWidth = ttsText.width
                var containerWidth = textContainer.width
                if (ttsText.x > containerWidth - textWidth - 12) {
                    ttsText.x -= 2
                } else {
                    scrollTimer.stop()
                    scrollBackTimer.start()
                }
            }
        }

        Timer {
            id: scrollBackTimer
            interval: 900
            repeat: false
            onTriggered: {
                ttsText.x = 0
                if (viewMode === "tts" && !root.isMusicLyricsCollapsed() && ttsText.width > textContainer.width) {
                    showTimer.restart()
                }
            }
        }

        Row {
            spacing: 4
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 24

            Rectangle {
                width: 4
                height: 16
                radius: 2
                color: "#52b7ff"
                SequentialAnimation on height {
                    loops: Animation.Infinite
                    NumberAnimation { from: 8; to: 16; duration: 500; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: 16; to: 8; duration: 500; easing.type: Easing.InOutQuad }
                }
            }
            Rectangle {
                width: 4
                height: 16
                radius: 2
                color: "#52b7ff"
                SequentialAnimation on height {
                    loops: Animation.Infinite
                    PauseAnimation { duration: 150 }
                    NumberAnimation { from: 8; to: 16; duration: 500; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: 16; to: 8; duration: 500; easing.type: Easing.InOutQuad }
                }
            }
            Rectangle {
                width: 4
                height: 16
                radius: 2
                color: "#52b7ff"
                SequentialAnimation on height {
                    loops: Animation.Infinite
                    PauseAnimation { duration: 300 }
                    NumberAnimation { from: 8; to: 16; duration: 500; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: 16; to: 8; duration: 500; easing.type: Easing.InOutQuad }
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
                font.family: "Segoe UI, Microsoft YaHei UI"
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
                font.family: "Segoe UI, Microsoft YaHei UI"
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
                font.family: "Segoe UI, Microsoft YaHei UI"
                font.pixelSize: 14
                font.weight: Font.Medium
                color: inputSendBtn.enabled ? "white" : Qt.rgba(255/255, 255/255, 255/255, 0.35)
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            onClicked: root.sendInputText()
        }
    }

    Component.onCompleted: {
        applyMode("dot")
        hideTimer.start()
    }
}
