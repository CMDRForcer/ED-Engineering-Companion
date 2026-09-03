import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Window {
    id: overlayWindow
    objectName: "engineering-overlay-window"
    width: 420
    height: 230
    minimumWidth: 300
    minimumHeight: 170
    visible: overlaySettings.visible
    color: "transparent"
    opacity: overlaySettings.opacity
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
           | (overlaySettings.clickThrough ? Qt.WindowTransparentForInput : 0)
    title: t("overlay.title", "EDEC Engineering Overlay")
    function t(key, fallback) {
        var activeLanguage = cockpit.interfaceLanguage
        return cockpit.translate(key, fallback)
    }

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: "#e6101822"
        border.color: cockpit.materialStatus === "READY" ? "#42d888" : "#ec3d50"
        border.width: 1

        MouseArea {
            anchors.fill: parent
            enabled: !overlaySettings.locked && !overlaySettings.clickThrough
            onPressed: overlayWindow.startSystemMove()
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16 * overlaySettings.scale
            spacing: 8 * overlaySettings.scale

            Label {
                text: t("overlay.engineering", "ENGINEERING") + " · "
                      + (cockpit.activeShip || t("overlay.no_ship", "NO SHIP"))
                color: "#8fa4ba"
                font.pixelSize: 11 * overlaySettings.scale
                font.bold: true
            }
            Label {
                objectName: "overlay-next-action"
                Layout.fillWidth: true
                text: cockpit.operationAction.title || cockpit.nextAction
                color: "#f4f8ff"
                font.pixelSize: 18 * overlaySettings.scale
                font.bold: true
                wrapMode: Text.Wrap
                maximumLineCount: 2
            }
            Label {
                Layout.fillWidth: true
                text: cockpit.operationAction.detail || cockpit.operationAction.subtitle || ""
                color: "#d5e3f2"
                font.pixelSize: 12 * overlaySettings.scale
                wrapMode: Text.Wrap
                maximumLineCount: 2
            }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    objectName: "overlay-material-readiness"
                    text: cockpit.materialStatus + " · " + cockpit.covered + " / " + cockpit.required
                    color: cockpit.materialStatus === "READY" ? "#42d888" : "#f2ae4b"
                    font.bold: true
                    font.pixelSize: 13 * overlaySettings.scale
                }
                Item { Layout.fillWidth: true }
                Label {
                    text: overlaySettings.locked
                          ? t("overlay.locked", "LOCKED")
                          : t("overlay.move", "MOVE")
                    color: "#8fa4ba"
                    font.pixelSize: 10 * overlaySettings.scale
                }
            }
            Label {
                Layout.fillWidth: true
                text: t("overlay.fullscreen_warning", "Exclusive fullscreen may cover overlays; use Borderless Windowed.")
                color: "#687b8f"
                font.pixelSize: 9 * overlaySettings.scale
                visible: !overlaySettings.clickThrough
            }
            RowLayout {
                visible: !overlaySettings.locked && !overlaySettings.clickThrough
                spacing: 6
                Button {
                    text: t("overlay.opacity_down", "− OPACITY")
                    onClicked: overlaySettings.opacity -= 0.05
                }
                Button {
                    text: t("overlay.opacity_up", "+ OPACITY")
                    onClicked: overlaySettings.opacity += 0.05
                }
                Button {
                    text: t("overlay.scale_down", "− SCALE")
                    onClicked: overlaySettings.scale -= 0.05
                }
                Button {
                    text: t("overlay.scale_up", "+ SCALE")
                    onClicked: overlaySettings.scale += 0.05
                }
                Button {
                    text: t("overlay.click_through", "CLICK-THROUGH")
                    onClicked: overlaySettings.toggleClickThrough()
                }
            }
        }
    }
}
