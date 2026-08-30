import QtQuick
import QtQuick.Controls

Item {
    id: shell
    default property alias content: contentHost.data
    property color accent: "transparent"
    property real radius: 14
    property bool elevated: true
    readonly property var hostWindow: ApplicationWindow.window

    Rectangle {
        visible: shell.elevated
        anchors.fill: parent
        anchors.leftMargin: 2
        anchors.rightMargin: 0
        anchors.topMargin: 4
        radius: shell.radius + 1
        color: shell.hostWindow ? shell.hostWindow.shadow : "#80000000"
        opacity: 0.42
    }
    Rectangle {
        visible: !!shell.hostWindow
                 && shell.hostWindow.enhancedVisuals
                 && shell.accent !== "transparent"
        anchors.fill: parent
        anchors.margins: -1
        radius: shell.radius + 1
        color: "transparent"
        border.width: 1
        border.color: Qt.rgba(shell.accent.r, shell.accent.g, shell.accent.b, 0.16)
    }
    Rectangle {
        anchors.fill: parent
        anchors.rightMargin: 2
        anchors.bottomMargin: 4
        radius: shell.radius
        color: shell.hostWindow ? shell.hostWindow.panel : "#102033"
        border.width: 1
        border.color: shell.hostWindow ? shell.hostWindow.borderTone : "#294560"
        Behavior on border.color {
            enabled: !!shell.hostWindow && !shell.hostWindow.reducedMotion
            ColorAnimation { duration: 180 }
        }
    }
    Rectangle {
        visible: shell.accent !== "transparent"
        anchors.left: parent.left
        anchors.leftMargin: 1
        anchors.verticalCenter: parent.verticalCenter
        width: 3
        height: Math.min(42, parent.height - 24)
        radius: 2
        color: shell.accent
    }
    Item {
        id: contentHost
        anchors.fill: parent
        anchors.rightMargin: 2
        anchors.bottomMargin: 4
    }
}
