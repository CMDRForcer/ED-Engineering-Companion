import QtQuick
import QtQuick.Controls

Item {
    id: shell
    default property alias content: contentHost.data
    property color accent: "transparent"
    property real radius: 18
    readonly property var hostWindow: ApplicationWindow.window

    Rectangle {
        anchors.fill: parent
        anchors.leftMargin: 7
        anchors.topMargin: 9
        radius: shell.radius
        color: shell.hostWindow ? shell.hostWindow.shadow : "#80000000"
    }
    Rectangle {
        visible: !!shell.hostWindow
                 && shell.hostWindow.enhancedVisuals
                 && shell.accent !== "transparent"
        anchors.fill: parent
        anchors.margins: -2
        anchors.topMargin: 3
        radius: shell.radius + 2
        color: "transparent"
        border.width: 1
        border.color: Qt.rgba(shell.accent.r, shell.accent.g, shell.accent.b, 0.20)
    }
    Rectangle {
        anchors.fill: parent
        anchors.rightMargin: 7
        anchors.bottomMargin: 9
        radius: shell.radius
        color: shell.hostWindow ? shell.hostWindow.panel : "#102033"
        border.width: 1
        border.color: shell.accent === "transparent"
                      ? (shell.hostWindow ? shell.hostWindow.borderTone : "#294560")
                      : shell.accent
        Behavior on border.color {
            enabled: !!shell.hostWindow && !shell.hostWindow.reducedMotion
            ColorAnimation { duration: 180 }
        }
    }
    Rectangle {
        visible: !!shell.hostWindow && shell.hostWindow.enhancedVisuals
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: 20
        anchors.rightMargin: 26
        height: 1
        opacity: 0.28
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop {
                position: 0.5
                color: shell.accent === "transparent"
                       ? (shell.hostWindow ? shell.hostWindow.accentSecondary : "#769cc2")
                       : shell.accent
            }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }
    Item {
        id: contentHost
        anchors.fill: parent
        anchors.rightMargin: 7
        anchors.bottomMargin: 9
    }
}

