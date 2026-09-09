import QtQuick
import QtQuick.Controls

Rectangle {
    id: badge
    property string statusText: ""
    property color tone: hostWindow ? hostWindow.accentSecondary : "#36cfee"
    property bool compact: false
    readonly property var hostWindow: ApplicationWindow.window

    implicitWidth: badgeLabel.implicitWidth + (compact ? 18 : 24)
    implicitHeight: compact ? 26 : 32
    radius: height / 2
    color: Qt.rgba(tone.r, tone.g, tone.b, 0.12)
    border.width: 1
    border.color: Qt.rgba(tone.r, tone.g, tone.b, 0.48)

    Label {
        id: badgeLabel
        anchors.centerIn: parent
        text: badge.statusText
        color: badge.tone
        font.pixelSize: badge.compact ? 9 : 10
        font.weight: Font.DemiBold
        font.letterSpacing: 0.4
        elide: Text.ElideRight
    }
}
