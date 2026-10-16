import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root
    color: Qt.rgba(20/255, 20/255, 20/255, 0.94)
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

    Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    Behavior on radius { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

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

    // 窗口出现：从透明 + 略微缩小状态放大淡入（带轻微回弹）
    function appear() {
        fadeOut.stop()
        root.opacity = 0
        root.scale = 0.95
        fadeIn.restart()
    }

    // 窗口退出：淡出并轻微收缩，完成后由 fadeOutDone 通知 Python 隐藏
    function fadeOutNow() {
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
        wakeVisible = true
        wakeReady = true
        refreshSize()
    }

    function unwakeBall() {
        wakeReady = false
        if (trayVisible) return
        wakeVisible = false
    }

    function openFileTray() {
        wakeVisible = false
        wakeReady = false
        trayVisible = true
        refreshSize()
    }

    function closeFileTray() {
        if (!trayVisible) return
        trayVisible = false
        wakeVisible = false
        wakeReady = false
        root.fileTrayClosed()
    }

    // 拖拽中途离开空的中转站：回到唤醒薄条，允许同一趟拖拽再次拖回
    function returnToWake() {
        if (!trayVisible) return
        trayVisible = false
        wakeVisible = true
        wakeReady = true
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

        Text {
            anchors.centerIn: parent
            text: "文件 / 文件夹拖到此处"
            font.family: "Segoe UI, Microsoft YaHei UI"
            font.pixelSize: 10
            color: Qt.rgba(255/255, 255/255, 255/255, 0.75)
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
        scale: trayVisible ? 1 : 0.92
        transformOrigin: Item.Center

        Behavior on opacity { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }

        ColumnLayout {
            anchors.fill: parent
            spacing: 6

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 22
                spacing: 8

                Text {
                    text: "文件中转站"
                    font.family: "Segoe UI, Microsoft YaHei UI"
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    color: Qt.rgba(255/255, 255/255, 255/255, 0.9)
                }

                Item { Layout.fillWidth: true }

                Text {
                    text: "注意：不要删除源路径上的源文件"
                    font.family: "Segoe UI, Microsoft YaHei UI"
                    font.pixelSize: 9
                    color: Qt.rgba(255/255, 255/255, 255/255, 0.4)
                    Layout.alignment: Qt.AlignVCenter
                }

                Rectangle {
                    Layout.preferredWidth: 22
                    Layout.preferredHeight: 22
                    radius: 11
                    color: trayCloseArea.pressed ? "#2a2a2a" : "transparent"
                    Layout.alignment: Qt.AlignVCenter

                    Text {
                        anchors.centerIn: parent
                        text: "×"
                        font.family: "Segoe UI, Microsoft YaHei UI"
                        font.pixelSize: 16
                        color: Qt.rgba(255/255, 255/255, 255/255, 0.7)
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
                font.family: "Segoe UI, Microsoft YaHei UI"
                font.pixelSize: 12
                color: Qt.rgba(255/255, 255/255, 255/255, 0.4)
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

                    onPressed: {
                        if (mouse.button === Qt.LeftButton) {
                            pressed = true
                            marqueeing = false
                            startX = mouse.x
                            startY = mouse.y
                        }
                    }

                    onPositionChanged: {
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
                        Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

                        // 逐个错峰淡入，让中转站填充更有层次
                        Timer {
                            interval: Math.min(index * 25, 250)
                            repeat: false
                            running: true
                            onTriggered: trayItem.opacity = 1
                        }

                        Column {
                            anchors.centerIn: parent
                            spacing: 3

                            Rectangle {
                                width: 44
                                height: 44
                                radius: 10
                                anchors.horizontalCenter: parent.horizontalCenter
                                color: model.selected ? Qt.rgba(0, 120/255, 215/255, 0.35) : "transparent"
                                border.color: model.selected ? Qt.rgba(0, 170/255, 255/255, 0.95) : Qt.rgba(255/255, 255/255, 255/255, 0.08)
                                border.width: model.selected ? 2 : 1

                                Image {
                                    anchors.centerIn: parent
                                    width: 30
                                    height: 30
                                    source: model.icon
                                    fillMode: Image.PreserveAspectFit
                                    smooth: true
                                }
                            }

                            Text {
                                width: cellSize
                                text: root.fileBasename(model.path)
                                font.family: "Segoe UI, Microsoft YaHei UI"
                                font.pixelSize: 8
                                color: Qt.rgba(255/255, 255/255, 255/255, 0.6)
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideMiddle
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
                            ToolTip.delay: 300

                            onPressed: {
                                pressButton = mouse.button
                                pressX = mouse.x
                                pressY = mouse.y
                                dragStarted = false
                            }

                            onPositionChanged: {
                                if (dragStarted || pressButton !== Qt.LeftButton) return
                                if (Math.abs(mouse.x - pressX) + Math.abs(mouse.y - pressY) < 8) return
                                dragStarted = true
                                root.fileDragRequested(index, true)
                            }

                            onReleased: {
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
                    color: Qt.rgba(0, 140/255, 220/255, 0.15)
                    border.color: Qt.rgba(0, 170/255, 255/255, 0.7)
                    border.width: 1
                }
            }
        }
    }

    // 拖入热区：文件拖到唤醒薄条上立即展开中转站；拖离后若为空则回到薄条
    DropArea {
        id: fileDropArea
        anchors.fill: parent
        keys: ["text/uri-list"]

        onEntered: {
            if (!drag.hasUrls) return
            root.wakeReady = false
            if (!trayVisible) root.openFileTray()
        }

        onExited: {
            if (trayVisible && fileTrayRepeater.count === 0) {
                root.returnToWake()
            }
        }

        onDropped: {
            if (!drop.hasUrls) return
            drop.accept(Qt.CopyAction)
            var urls = []
            for (var i = 0; i < drop.urls.length; i++) {
                urls.push(String(drop.urls[i]))
            }
            root.filesDropped(urls)
            if (!trayVisible) root.openFileTray()
        }
    }

    // 中转站清空 1s 后自动关闭（避免空面板一直停留）
    Timer {
        id: trayEmptyAutoCloseTimer
        interval: 1000
        repeat: false
        onTriggered: {
            if (trayVisible && fileTrayRepeater.count === 0) {
                root.closeFileTray()
            }
        }
    }

    Component.onCompleted: refreshSize()
}