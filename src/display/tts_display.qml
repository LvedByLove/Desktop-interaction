import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// TTS状态显示窗口 - 独立的顶层窗口，语音播报期间显示
Rectangle {
    id: root
    width: 260
    height: 30
    color: "#2a2a2a"
    // 圆角设为高度的一半，让两边像球一样圆润
    radius: 30
    border.color: "#52b7ff"
    border.width: 2

    // 使用RowLayout布局
    RowLayout {
        id: mainLayout
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.margins: 0
        spacing: 12

        // AI动画球体 - 使用AI.gif显示
        Item {
            id: aiBall
            width: 28
            height: 28
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 28
            Layout.preferredHeight: 28

            // 外发光效果
            Rectangle {
                anchors.centerIn: parent
                width: 36
                height: 36
                radius: 18
                color: "transparent"
                border.color: "#52b7ff"
                border.width: 0.5
                opacity: 0.3
            }

            // AI.gif动画图片 - 支持GIF动态显示
            AnimatedImage {
                anchors.centerIn: parent
                width: 80
                height: 80
                source: "../../assets/emojis/huang1.gif"
                fillMode: Image.PreserveAspectFit
                cache: false
                playing: true
            }
        }

        // TTS文本 - 支持滚动显示
        Item {
            id: textContainer
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            Layout.minimumWidth: 50
            height: parent.height
            clip: true

            // 可滚动的文本
            Text {
                id: ttsText
                x: 0
                anchors.verticalCenter: parent.verticalCenter
                text: displayModel ? displayModel.ttsText : "语音播报中..."
                font.family: "Segoe UI, Microsoft YaHei UI"
                font.pixelSize: 14
                font.weight: Font.Medium
                color: "white"
                wrapMode: Text.NoWrap

                onTextChanged: {
                    x = 0
                    scrollTimer.stop()
                    scrollBackTimer.stop()
                    showTimer.restart()
                }
            }

            // 文本先完整显示一段时间
            Timer {
                id: showTimer
                interval: 2000
                repeat: false
                
                onTriggered: {
                    var textWidth = ttsText.width
                    var containerWidth = textContainer.width
                    
                    console.log("Text width:", textWidth, "Container width:", containerWidth)
                    
                    if (textWidth > containerWidth) {
                        scrollTimer.start()
                        console.log("Starting scroll after display")
                    }
                }
            }

            // 滚动定时器 - 每80毫秒移动2像素（更快一点）
            Timer {
                id: scrollTimer
                interval: 80
                repeat: true
                
                onTriggered: {
                    var textWidth = ttsText.width
                    var containerWidth = textContainer.width
                    
                    if (ttsText.x > containerWidth - textWidth) {
                        ttsText.x -= 2
                    } else {
                        scrollTimer.stop()
                        scrollBackTimer.start()
                    }
                }
            }

            // 滚动到末尾后延迟回到起点
            Timer {
                id: scrollBackTimer
                interval: 1000
                repeat: false
                
                onTriggered: {
                    ttsText.x = 0
                    var textWidth = ttsText.width
                    var containerWidth = textContainer.width
                    if (textWidth > containerWidth) {
                        scrollTimer.start()
                    }
                }
            }
        }

        // 播放指示器 - 使用SequentialAnimation实现延迟
        Row {
            spacing: 4
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 24

            Rectangle {
                width: 4
                height: 16
                radius: 2
                color: "#52b7ff"
                property int baseHeight: 16

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

    Component.onCompleted: {
        console.log("TTS Display QML loaded successfully")
    }
}