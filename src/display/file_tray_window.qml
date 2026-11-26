import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    // 灰白面板配色：变形只改变几何尺寸和文字显隐，不做颜色过渡
    readonly property color panelBackgroundColor: "#f6f7f9"
    readonly property color primaryTextColor: "#111827"
    readonly property color secondaryTextColor: "#6b7280"
    readonly property color surfaceHoverColor: "#e8ebef"
    readonly property color surfacePressedColor: "#d9dee5"
    readonly property color accentColor: "#2563eb"

    color: panelBackgroundColor
    border.color: "#d8dde5"
    border.width: 1
    radius: 20

    // 信号定义 - 与 Python 回调对接
    signal windowResizeRequested(int width, int height)
    signal filesDropped(var urls)
    signal fileDragRequested(int index, bool copy)
    signal fileRemoveRequested(int index)
    signal fileTrayClosed()
    // 窗口级淡出动画完成，通知 Python 隐藏窗口
    signal fadeOutDone()
    // 多选：mode 0=单选替换 / 1=Ctrl切换 / 2=Shift连续
    signal selectIndex(int index, int mode)
    // 空白框选：命中的索引列表
    signal marqueeSelection(var indices)
    // 空白点击：清空选中
    signal clearSelection()

    // 尺寸
    property int wakeWidth: 570
    property int wakeHeight: 16
    property int trayWidth: 600
    property int cellSize: 56
    property int fileTrayColumns: 9

    // 两种显示形态独立可见性（与主胶囊 viewMode 解耦，二者可同时显示互不干扰）
    property bool wakeVisible: false   // 拖拽唤醒胶囊（薄条）
    property bool trayVisible: false   // 中转站面板
    property bool wakeReady: false     // 全局拖拽中：就绪等待拖入
    property bool fileDropArmed: false // 已通过拖放条激活，面板内可继续放置
    // 最近一次打开面板的时间：用于忽略 Drop 同一瞬间触发的 DropArea.onExited
    property real trayShownAt: 0
    // 超量拖入等警告：短暂显示在面板底部
    property string warningText: ""

    // 窗口几何由 Python 侧 QPropertyAnimation 驱动，QML 侧不再重复动画 width/height
    Behavior on radius { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

    // 唤醒薄条文字的显隐进度：从待机短横线变形时，文字后于几何展开淡入
    property real wakeTextOpacity: 0

    // 变形展开：暂停一下再让文字浮现，避免与条形拉伸同时出现显得拥挤
    SequentialAnimation {
        id: wakeTextReveal

        PauseAnimation { duration: 120 }
        NumberAnimation {
            target: root
            property: "wakeTextOpacity"
            to: 1
            duration: 220
            easing.type: Easing.OutCubic
        }
    }

    // 收起：文字先于条形收缩淡出
    NumberAnimation {
        id: wakeTextHide

        target: root
        property: "wakeTextOpacity"
        to: 0
        duration: 140
        easing.type: Easing.InCubic
    }

    opacity: 1
    scale: 1
    transformOrigin: Item.Center

    // 窗口级淡入/淡出动画：透明度 + 缩放叠加，出现时轻柔回弹、退出时收缩消失
    ParallelAnimation {
        id: fadeIn

        NumberAnimation {
            target: root
            property: "opacity"
            to: 1
            duration: 260
            easing.type: Easing.OutCubic
        }
        NumberAnimation {
            target: root
            property: "scale"
            to: 1
            duration: 360
            easing.type: Easing.OutBack
        }
    }

    ParallelAnimation {
        id: fadeOut

        NumberAnimation {
            target: root
            property: "opacity"
            to: 0
            duration: 240
            easing.type: Easing.InCubic
        }
        NumberAnimation {
            target: root
            property: "scale"
            to: 0.94
            duration: 280
            easing.type: Easing.InBack
        }
        onStopped: {
            if (root.opacity <= 0) {
                root.fadeOutDone()
            }
        }
    }

    // 窗口出现：从一条短横线横向展开并淡入（带轻微回弹）
    function appear() {
        trayCloseTextTimer.stop()
        fadeOut.stop()
        fadeIn.stop()
        wakeTextReveal.stop()
        wakeTextHide.stop()
        root.wakeTextOpacity = 1
        root.opacity = 0
        root.scale = 0.35
        fadeIn.restart()
    }

    // 窗口已显示但正在淡出（例如刚关闭面板又立刻拖入文件）：
    // 不重置透明度，直接淡回可见，避免闪一下再出现
    function reappear() {
        trayCloseTextTimer.stop()
        fadeOut.stop()
        wakeTextReveal.stop()
        wakeTextHide.stop()
        root.wakeTextOpacity = 1
        fadeIn.restart()
    }

    // 从主胶囊待机短横线变形为唤醒薄条：
    // 窗口几何由 Python 侧从短横线尺寸动画到薄条尺寸，这里同步让文字延迟淡入，
    // 让「文件 / 文件夹拖到此处」像是横条长出来的。
    function beginLineMorph() {
        trayCloseTextTimer.stop()
        fadeIn.stop()
        fadeOut.stop()
        trayVisible = false
        wakeVisible = true
        wakeReady = true
        root.opacity = 1
        root.scale = 1
        wakeTextReveal.stop()
        wakeTextHide.stop()
        root.wakeTextOpacity = 0
        wakeTextReveal.start()
    }

    // 唤醒薄条收回到待机短横线：几何由 Python 侧同步收缩，
    // 最后窗口隐藏时与横线衔接。
    function endLineMorph() {
        trayCloseTextTimer.stop()
        fadeIn.stop()
        fadeOut.stop()
        wakeReady = false
        wakeVisible = false
        wakeTextReveal.stop()
        wakeTextHide.start()
    }

    // 关闭中转站：面板内容切换为拖放条，几何和文字同步连续收拢。
    function beginTrayCollapse() {
        beginLineMorph()
        wakeReady = false
        fileDropArmed = false
        warningText = ""
        wakeTextReveal.stop()
        wakeTextHide.stop()
        root.wakeTextOpacity = 1
        trayCloseTextTimer.restart()
    }

    // 主界面不在待机态时无法衔接横条，回到原有淡出流程。
    function prepareTrayFadeOut() {
        trayCloseTextTimer.stop()
        trayVisible = false
        wakeVisible = false
        wakeReady = false
        fileDropArmed = false
        warningText = ""
    }

    // 窗口退出：淡出并轻微收缩，完成后由 fadeOutDone 通知 Python 隐藏
    function fadeOutNow() {
        trayCloseTextTimer.stop()
        fadeIn.stop()
        fadeOut.restart()
    }

    function count() {
        return fileTrayRepeater ? fileTrayRepeater.count : 0
    }

    function trayHeight() {
        var c = count()
        if (c === 0) return 72
        var rows = Math.ceil(c / fileTrayColumns)
        return 40 + rows * cellSize + (rows - 1) * 8 + 14
    }

    function fileBasename(path) {
        var s = String(path)
        var i = Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\"))
        return i >= 0 ? s.slice(i + 1) : s
    }

    function refreshSize() {
        if (trayVisible) {
            root.radius = 20
            root.windowResizeRequested(trayWidth, trayHeight())
        } else if (wakeVisible) {
            root.radius = wakeHeight / 2
            root.windowResizeRequested(wakeWidth, wakeHeight)
        }
    }

    function wakeBall() {
        if (trayVisible) return
        trayCloseTextTimer.stop()
        wakeVisible = true
        wakeReady = true
        wakeTextReveal.stop()
        wakeTextHide.stop()
        root.wakeTextOpacity = 1
        refreshSize()
    }

    function armFileDrop() {
        fileDropArmed = true
    }

    function unwakeBall() {
        trayCloseTextTimer.stop()
        wakeReady = false
        if (trayVisible) return
        fileDropArmed = false
        wakeTextReveal.stop()
        wakeTextHide.start()
        wakeVisible = false
    }

    function openFileTray() {
        trayCloseTextTimer.stop()
        wakeVisible = false
        wakeReady = false
        wakeTextReveal.stop()
        wakeTextHide.stop()
        root.wakeTextOpacity = 0
        trayVisible = true
        fileDropArmed = true
        trayShownAt = Date.now()
        refreshSize()
    }

    // 超量拖入警告：显示 3.5 秒；若面板未展开则先展开
    function showWarning(message) {
        warningText = String(message || "")
        if (warningText.length === 0) return
        warningClearTimer.restart()
        if (!trayVisible) openFileTray()
    }

    function closeFileTray() {
        if (!trayVisible) return
        root.fileTrayClosed()
    }

    // 拖拽中途离开空的中转站：回到唤醒薄条，允许同一趟拖拽再次拖回
    function returnToWake() {
        if (!trayVisible) return
        trayCloseTextTimer.stop()
        trayVisible = false
        wakeVisible = true
        wakeReady = true
        fileDropArmed = false
        warningText = ""
        wakeTextReveal.stop()
        wakeTextHide.stop()
        root.wakeTextOpacity = 1
        refreshSize()
    }

    Connections {
        target: fileTrayModel
        function onChanged() {
            root.refreshSize()
            if (fileTrayRepeater.count === 0 && trayVisible) {
                trayEmptyAutoCloseTimer.restart()
            } else {
                trayEmptyAutoCloseTimer.stop()
            }
        }
    }

    // 拖拽唤醒胶囊：检测到拖拽文件/文件夹时显示的薄条
    Item {
        id: wakeContent
        anchors.fill: parent
        visible: opacity > 0
        opacity: wakeVisible && !trayVisible ? 1 : 0
        scale: wakeVisible && !trayVisible ? 1 : 0.85
        transformOrigin: Item.Center

        Behavior on opacity { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 300; easing.type: Easing.OutBack } }

        Item {
            id: wakeTextWrap

            anchors.centerIn: parent
            width: wakeLabel.implicitWidth
            height: wakeLabel.implicitHeight
            // 从待机短横线变形时由 wakeTextOpacity 控制延迟淡入；
            // 普通唤醒 / 收起同样走这个进度，避免文字比条形先出现或后消失
            opacity: trayVisible ? 0 : root.wakeTextOpacity

            Text {
                id: wakeLabel

                anchors.centerIn: parent
                text: "文件 / 文件夹拖到此处"
                font.family: "Microsoft YaHei UI"
                font.pixelSize: 10
                color: root.secondaryTextColor
                opacity: 1.0

                // 等待拖入时轻微呼吸，让唤醒胶囊显得"有生命"
                SequentialAnimation on opacity {
                    loops: Animation.Infinite
                    running: wakeContent.visible
                    NumberAnimation { from: 1.0; to: 0.5; duration: 900; easing.type: Easing.InOutSine }
                    NumberAnimation { from: 0.5; to: 1.0; duration: 900; easing.type: Easing.InOutSine }
                }
            }
        }
    }

    // 中转站面板
    Item {
        id: trayContent
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        anchors.topMargin: 7
        anchors.bottomMargin: 7
        visible: opacity > 0
        opacity: trayVisible ? 1 : 0

        // 纵向从顶部"一条线"展开成完整面板，与隐藏态短横线的展开语言保持一致
        property real revealY: trayVisible ? 1 : 0.05
        transform: Scale {
            origin.x: trayContent.width / 2
            origin.y: 0
            yScale: trayContent.revealY
        }

        Behavior on opacity { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        Behavior on revealY { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }

        ColumnLayout {
            anchors.fill: parent
            spacing: 6

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 22
                spacing: 8

                Text {
                    text: "文件中转站"
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    color: root.primaryTextColor
                }

                Item { Layout.fillWidth: true }

                Text {
                    text: "注意：不要删除源路径上的源文件"
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 9
                    color: root.secondaryTextColor
                    Layout.alignment: Qt.AlignVCenter
                }

                Rectangle {
                    Layout.preferredWidth: 22
                    Layout.preferredHeight: 22
                    radius: 11
                    color: trayCloseArea.pressed
                        ? root.surfacePressedColor
                        : trayCloseArea.containsMouse
                            ? root.surfaceHoverColor
                            : "transparent"
                    Layout.alignment: Qt.AlignVCenter

                    Text {
                        anchors.centerIn: parent
                        text: "×"
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: 16
                        color: trayCloseArea.containsMouse
                            ? root.primaryTextColor
                            : root.secondaryTextColor
                    }

                    MouseArea {
                        id: trayCloseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.closeFileTray()
                    }
                }
            }

            Text {
                id: trayEmptyHint
                visible: fileTrayRepeater.count === 0
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: "拖动文件 / 文件夹到这里"
                font.family: "Microsoft YaHei UI"
                font.pixelSize: 12
                color: root.secondaryTextColor
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            Item {
                id: trayGridContainer
                visible: fileTrayRepeater.count > 0
                Layout.fillWidth: true
                Layout.fillHeight: true

                // 空白框选层（绘制在 Grid 之下，仅接收落在空白处/图标间隙上的按下）
                MouseArea {
                    id: gridBackgroundArea
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    hoverEnabled: true
                    property bool pressed: false
                    property bool marqueeing: false
                    property real startX: 0
                    property real startY: 0

                    onPressed: function (mouse) {
                        if (mouse.button === Qt.LeftButton) {
                            pressed = true
                            marqueeing = false
                            startX = mouse.x
                            startY = mouse.y
                        }
                    }

                    onPositionChanged: function (mouse) {
                        if (!pressed) return
                        if (!marqueeing && Math.abs(mouse.x - startX) + Math.abs(mouse.y - startY) < 4) return
                        marqueeing = true
                        var x = Math.min(startX, mouse.x)
                        var y = Math.min(startY, mouse.y)
                        var w = Math.abs(mouse.x - startX)
                        var h = Math.abs(mouse.y - startY)
                        selectionRect.x = x
                        selectionRect.y = y
                        selectionRect.width = w
                        selectionRect.height = h
                        selectionRect.visible = true
                        root.marqueeSelection(marqueeIndexes(x, y, w, h))
                    }

                    onReleased: {
                        if (pressed) {
                            if (!marqueeing) {
                                root.clearSelection()
                            }
                            pressed = false
                            marqueeing = false
                            selectionRect.visible = false
                        }
                    }

                    function marqueeIndexes(x, y, w, h) {
                        var cols = fileTrayColumns
                        var rows = Math.ceil(root.count() / cols)
                        if (rows < 1) rows = 1
                        var cw = cols * cellSize + (cols - 1) * 8
                        var ch = rows * cellSize + (rows - 1) * 8
                        var ox = Math.max(0, (trayGrid.width - cw) / 2)
                        var oy = Math.max(0, (trayGrid.height - ch) / 2)
                        var x0 = x - ox
                        var y0 = y - oy
                        var n = root.count()
                        var idx = []
                        for (var i = 0; i < n; i++) {
                            var row = Math.floor(i / cols)
                            var col = i % cols
                            var ix = col * (cellSize + 8)
                            var iy = row * (cellSize + 8)
                            if (x0 < ix + cellSize && x0 + w > ix && y0 < iy + cellSize && y0 + h > iy)
                                idx.push(i)
                        }
                        return idx
                    }
                }

                Grid {
                    id: trayGrid
                    anchors.fill: parent
                    columns: fileTrayColumns
                    horizontalItemAlignment: Grid.AlignHCenter
                    verticalItemAlignment: Grid.AlignVCenter
                    spacing: 8

                Repeater {
                    id: fileTrayRepeater
                    model: fileTrayModel

                    delegate: Item {
                        id: trayItem
                        width: cellSize
                        height: cellSize
                        opacity: 0

                        // 逐个错峰淡入，让中转站填充更有层次。
                        // 用声明式动画替代每个 delegate 一个 QTimer。
                        SequentialAnimation on opacity {
                            running: true
                            // delegate 销毁/模型重置时 index 可能短暂为 -1
                            PauseAnimation { duration: Math.max(0, Math.min(index * 25, 250)) }
                            NumberAnimation {
                                to: 1
                                duration: 200
                                easing.type: Easing.OutCubic
                            }
                        }

                        Column {
                            anchors.centerIn: parent
                            spacing: 2

                            // 无边框：选中时仅以淡色底作为反馈，图标本身占据整个区域
                            Rectangle {
                                width: 40
                                height: 40
                                radius: 10
                                anchors.horizontalCenter: parent.horizontalCenter
                                color: model.selected
                                    ? Qt.rgba(37/255, 99/255, 235/255, 0.12)
                                    : "transparent"
                                border.width: 0

                                Image {
                                    anchors.fill: parent
                                    source: model.icon
                                    fillMode: Image.PreserveAspectFit
                                    asynchronous: true
                                    // 以最终显示尺寸解码，避免先解 96px 再缩到 40px
                                    sourceSize: Qt.size(40, 40)
                                }
                            }

                            Text {
                                width: cellSize
                                text: root.fileBasename(model.path)
                                font.family: "Microsoft YaHei UI"
                                font.pixelSize: 8
                                color: root.secondaryTextColor
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideMiddle
                            }

                            // 文件夹 / 文件大小 / 已移动（Shell 缩略图异步加载后填充）
                            Text {
                                width: cellSize
                                text: model.meta ? model.meta : ""
                                visible: text.length > 0
                                font.family: "Microsoft YaHei UI"
                                font.pixelSize: 7
                                color: root.secondaryTextColor
                                opacity: 0.75
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                            }
                        }

                        MouseArea {
                            id: trayIconArea
                            anchors.fill: parent
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            hoverEnabled: true
                            cursorShape: Qt.OpenHandCursor
                            property bool dragStarted: false
                            property int pressButton: Qt.NoButton
                            property real pressX: 0
                            property real pressY: 0

                            ToolTip.visible: containsMouse && !dragStarted
                            ToolTip.text: root.fileBasename(model.path)
                                + (model.meta ? "\n" + model.meta : "")
                            ToolTip.delay: 300

                            onPressed: function (mouse) {
                                pressButton = mouse.button
                                pressX = mouse.x
                                pressY = mouse.y
                                dragStarted = false
                            }

                            onPositionChanged: function (mouse) {
                                if (dragStarted || pressButton !== Qt.LeftButton) return
                                if (Math.abs(mouse.x - pressX) + Math.abs(mouse.y - pressY) < 8) return
                                dragStarted = true
                                root.fileDragRequested(index, true)
                            }

                            onReleased: function (mouse) {
                                var wasDrag = dragStarted
                                var btn = pressButton
                                var mods = mouse.modifiers
                                dragStarted = false
                                pressButton = Qt.NoButton
                                if (btn === Qt.RightButton && !wasDrag) {
                                    root.fileRemoveRequested(index)
                                } else if (btn === Qt.LeftButton && !wasDrag) {
                                    if (mods & Qt.ControlModifier) {
                                        root.selectIndex(index, 1)   // Ctrl：切换
                                    } else if (mods & Qt.ShiftModifier) {
                                        root.selectIndex(index, 2)   // Shift：连续
                                    } else {
                                        root.selectIndex(index, 0)   // 单选
                                    }
                                }
                            }
                        }
                    }
                }
                }

                Rectangle {
                    id: selectionRect
                    visible: false
                    color: Qt.rgba(37/255, 99/255, 235/255, 0.10)
                    border.color: Qt.rgba(37/255, 99/255, 235/255, 0.65)
                    border.width: 1
                }
            }
        }
    }

    // 拖入热区：首次必须命中顶部拖放条；激活并展开后，面板任意位置均可放置。
    DropArea {
        id: fileDropArea
        anchors.fill: parent
        keys: ["text/uri-list"]
        enabled: root.wakeVisible || root.trayVisible || root.fileDropArmed

        onEntered: function (drag) {
            if (!drag.hasUrls) return
            if (!root.fileDropArmed && !root.wakeVisible && !root.trayVisible) {
                drag.ignore()
                return
            }
            root.fileDropArmed = true
            root.wakeReady = false
            drag.accept(Qt.CopyAction)
        }

        onExited: {
            // Drop 与 onExited 的先后顺序不稳定；面板刚展开的 800ms 内
            // 不把它收回短横线，避免“文件已加入但界面消失”。
            if (Date.now() - root.trayShownAt < 800) return
            if (root.trayVisible && fileTrayRepeater.count === 0) {
                root.returnToWake()
            }
        }

        onDropped: function (drop) {
            if (!drop.hasUrls) return
            if (!root.fileDropArmed) {
                return
            }
            drop.accept(Qt.CopyAction)
            var urls = []
            for (var i = 0; i < drop.urls.length; i++) {
                urls.push(String(drop.urls[i]))
            }
            root.filesDropped(urls)
            if (!root.trayVisible) root.openFileTray()
        }
    }

    // 中转站清空 1s 后自动关闭（避免空面板一直停留）
    Timer {
        id: trayCloseTextTimer
        interval: 150
        repeat: false
        onTriggered: root.endLineMorph()
    }

    Timer {
        id: trayEmptyAutoCloseTimer
        interval: 1000
        repeat: false
        onTriggered: {
            if (root.trayVisible && fileTrayRepeater.count === 0) {
                root.closeFileTray()
            }
        }
    }

    Timer {
        id: warningClearTimer
        interval: 3500
        repeat: false
        onTriggered: {
            root.warningText = ""
            // 空面板上的拒绝提示结束后，再走一次空态自动收起
            if (root.trayVisible && fileTrayRepeater.count === 0) {
                trayEmptyAutoCloseTimer.restart()
            }
        }
    }

    // 超量拖入警告：覆盖在面板底部，不参与布局、不改变面板高度
    Rectangle {
        id: warningBanner
        z: 200
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 10
        width: Math.max(200, Math.min(parent.width - 32, 520))
        height: warningLabel.implicitHeight + 16
        radius: 10
        visible: root.warningText.length > 0 && root.trayVisible
        color: "#fdecec"
        border.color: "#f0b4b4"
        border.width: 1

        Text {
            id: warningLabel
            anchors.centerIn: parent
            width: Math.max(0, parent.width - 24)
            text: root.warningText
            font.family: "Microsoft YaHei UI"
            font.pixelSize: 11
            color: "#b42323"
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
        }
    }

    Component.onCompleted: refreshSize()
}
