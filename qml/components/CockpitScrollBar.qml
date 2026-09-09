import QtQuick
import QtQuick.Controls

ScrollBar {
    id: themedScrollBar
    readonly property var hostWindow: ApplicationWindow.window
    implicitWidth: orientation === Qt.Vertical ? 8 : 100
    implicitHeight: orientation === Qt.Horizontal ? 8 : 100
    contentItem: Rectangle {
        implicitWidth: 5
        implicitHeight: 5
        radius: 2.5
        color: themedScrollBar.hovered || themedScrollBar.pressed
               ? (themedScrollBar.hostWindow ? themedScrollBar.hostWindow.accent : "#3bdcff")
               : (themedScrollBar.hostWindow ? themedScrollBar.hostWindow.textDisabled : "#587086")
        opacity: themedScrollBar.active ? 0.92 : 0.42
        Behavior on opacity { NumberAnimation { duration: 140 } }
    }
    background: Rectangle {
        color: themedScrollBar.hostWindow ? themedScrollBar.hostWindow.inputBackground : "#0d1b2b"
        radius: 4
        opacity: themedScrollBar.active ? 0.46 : 0.16
        Behavior on opacity { NumberAnimation { duration: 140 } }
    }
}
