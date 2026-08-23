import QtQuick
import QtQuick.Controls

ScrollBar {
    id: themedScrollBar
    readonly property var hostWindow: ApplicationWindow.window
    implicitWidth: orientation === Qt.Vertical ? 10 : 100
    implicitHeight: orientation === Qt.Horizontal ? 10 : 100
    contentItem: Rectangle {
        implicitWidth: 6
        implicitHeight: 6
        radius: 3
        color: themedScrollBar.hovered || themedScrollBar.pressed
               ? (themedScrollBar.hostWindow ? themedScrollBar.hostWindow.accent : "#3bdcff")
               : (themedScrollBar.hostWindow ? themedScrollBar.hostWindow.textDisabled : "#587086")
        opacity: themedScrollBar.active ? 0.95 : 0.62
    }
    background: Rectangle {
        color: themedScrollBar.hostWindow ? themedScrollBar.hostWindow.inputBackground : "#0d1b2b"
        radius: 4
        opacity: 0.72
    }
}

