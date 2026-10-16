import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// 输入状态显示窗口 - 独立的顶层窗口
Rectangle {
    id: root
    width: 520
    height: 54
    color: Qt.rgba(8/255, 8/255, 8/255, 0.96)
    radius: height / 2
    border.color: textInput.activeFocus ? "#3a3a3a" : "#1f1f1f"
    border.width: 1
    focus: true

    Keys.onEscapePressed: root.hideWindow()

    // 信号定义
    signal sendButtonClicked(string text)
    signal hideWindow()

    function sendText() {
        var message = textInput.text.trim()
        if (message.length <= 0) return
        root.sendButtonClicked(message)
        textInput.text = ""
        textInput.forceActiveFocus()
    }

    // 使用RowLayout布局
    RowLayout {
        id: mainLayout
        anchors.fill: parent
        anchors.leftMargin: 18
        anchors.rightMargin: 8
        anchors.topMargin: 7
        anchors.bottomMargin: 7
        spacing: 10

        // 输入区域
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            TextInput {
                id: textInput
                objectName: "textInput"
                anchors.fill: parent
                verticalAlignment: TextInput.AlignVCenter
                font.family: "Segoe UI, Microsoft YaHei UI"
                font.pixelSize: 15
                color: "white"
                selectedTextColor: "white"
                selectionColor: "#165dff"
                selectByMouse: true
                clip: true

                Keys.onReturnPressed: root.sendText()
                Keys.onEnterPressed: root.sendText()
            }

            // 占位符文字
            Text {
                anchors.fill: parent
                text: "输入文字和小智对话..."
                font.family: "Segoe UI, Microsoft YaHei UI"
                font.pixelSize: 15
                color: Qt.rgba(255/255, 255/255, 255/255, 0.42)
                verticalAlignment: Text.AlignVCenter
                visible: textInput.text.length === 0
            }
        }

        // 发送按钮
        Button {
            id: sendBtn
            Layout.preferredWidth: 72
            Layout.preferredHeight: 40
            text: "发送"
            enabled: textInput.text.trim().length > 0
            background: Rectangle {
                color: !sendBtn.enabled ? "#242424" : (sendBtn.pressed ? "#0e42d2" : "#165dff")
                radius: height / 2
            }
            contentItem: Text {
                text: sendBtn.text
                font.family: "Segoe UI, Microsoft YaHei UI"
                font.pixelSize: 14
                font.weight: Font.Medium
                color: sendBtn.enabled ? "white" : Qt.rgba(255/255, 255/255, 255/255, 0.35)
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            onClicked: root.sendText()
        }
    }
}