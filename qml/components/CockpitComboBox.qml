import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    property var appWindow: ApplicationWindow.window

    implicitHeight: 42
    leftPadding: 14
    rightPadding: 42
    topPadding: 0
    bottomPadding: 0
    font.family: appWindow ? appWindow.font.family : "Segoe UI Variable Text"
    font.pixelSize: 11
    font.bold: true
    hoverEnabled: true
    wheelEnabled: false
    implicitContentWidthPolicy: ComboBox.WidestTextWhenCompleted

    contentItem: Text {
        leftPadding: 0
        rightPadding: 0
        text: control.displayText
        font: control.font
        color: control.enabled
               ? (control.appWindow ? control.appWindow.textPrimary : "#f4f8ff")
               : (control.appWindow ? control.appWindow.textDisabled : "#587086")
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        ToolTip.visible: control.hovered && implicitWidth > width
        ToolTip.text: text
    }

    indicator: Item {
        width: 38
        height: control.height
        x: control.width - width
        Label {
            anchors.centerIn: parent
            text: "⌄"
            color: control.enabled
                   ? (control.appWindow ? control.appWindow.accent : "#3bdcff")
                   : (control.appWindow ? control.appWindow.textDisabled : "#587086")
            font.pixelSize: 18
            font.bold: true
            rotation: control.popup.visible ? 180 : 0
            Behavior on rotation { NumberAnimation { duration: 120 } }
        }
    }

    background: Rectangle {
        radius: 8
        color: control.down || control.popup.visible
               ? (control.appWindow ? control.appWindow.active : "#214563")
               : control.hovered
                 ? (control.appWindow ? control.appWindow.hover : "#1c3650")
                 : (control.appWindow ? control.appWindow.inputBackground : "#0d1b2b")
        border.width: control.activeFocus || control.popup.visible ? 2 : 1
        border.color: control.activeFocus || control.popup.visible
                      ? (control.appWindow ? control.appWindow.accent : "#3bdcff")
                      : (control.appWindow ? control.appWindow.borderTone : "#294560")
        Behavior on color { ColorAnimation { duration: 90 } }
    }

    delegate: ItemDelegate {
        id: option
        required property int index
        width: ListView.view ? ListView.view.width : control.width
        implicitWidth: optionText.implicitWidth + 28
        height: 40
        highlighted: control.highlightedIndex === index
        hoverEnabled: true
        contentItem: Text {
            id: optionText
            text: control.textAt(option.index)
            color: option.highlighted
                   ? (control.appWindow ? control.appWindow.textPrimary : "#f4f8ff")
                   : (control.appWindow ? control.appWindow.textSecondary : "#d5e3f2")
            font: control.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            ToolTip.visible: option.hovered && implicitWidth > width
            ToolTip.text: text
        }
        background: Rectangle {
            radius: 6
            color: option.highlighted
                   ? (control.appWindow ? control.appWindow.active : "#214563")
                   : "transparent"
            border.width: option.highlighted ? 1 : 0
            border.color: control.appWindow ? control.appWindow.accent : "#3bdcff"
        }
    }

    popup: Popup {
        y: control.height + 4
        width: Math.min(460, Math.max(control.width, 340))
        x: width > control.width ? control.width - width : 0
        implicitHeight: Math.min(contentItem.implicitHeight + 12, 340)
        padding: 6
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.delegateModel
            currentIndex: control.highlightedIndex
            highlightMoveDuration: 80
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle {
            radius: 9
            color: control.appWindow ? control.appWindow.cardRaised : "#172b42"
            border.width: 1
            border.color: control.appWindow ? control.appWindow.accent : "#3bdcff"
        }
    }

    Accessible.name: displayText
    Accessible.role: Accessible.ComboBox
}
