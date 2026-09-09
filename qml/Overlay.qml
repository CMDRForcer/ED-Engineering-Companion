import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Window {
    id: overlayWindow
    objectName: "engineering-overlay-window"
    width: 480
    height: 286
    minimumWidth: 360
    minimumHeight: 220
    visible: overlaySettings.visible
    color: "transparent"
    opacity: overlaySettings.opacity
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
           | (overlaySettings.clickThrough ? Qt.WindowTransparentForInput : 0)
    title: t("overlay.title", "EDEC Engineering Overlay")

    property var action: cockpit.operationAction || ({})
    property string materialStatus: action.materialStatus || cockpit.materialStatus
    property int materialCovered: action.materialCovered !== undefined ? action.materialCovered : cockpit.covered
    property int materialRequired: action.materialRequired !== undefined ? action.materialRequired : cockpit.required
    property bool materialReady: materialStatus === "READY"

    Label {
        objectName: "overlay-next-action"
        visible: false
        text: cockpit.operationAction.title || cockpit.nextAction
    }
    Label {
        objectName: "overlay-material-readiness"
        visible: false
        text: cockpit.materialStatus + " · " + cockpit.covered + " / " + cockpit.required
    }

    function t(key, fallback) {
        var activeLanguage = cockpit.interfaceLanguage
        return cockpit.translate(key, fallback)
    }
    function effectSummary() {
        var effects = action.experimentalEffects || []
        var parts = []
        for (var index = 0; index < effects.length; index++)
            parts.push(String(effects[index].property || "Effect").toUpperCase()
                       + " " + String(effects[index].effect || ""))
        return parts.join(" · ")
    }

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: "#f2071119"
        border.color: "#ec3d50"
        border.width: 1

        MouseArea {
            anchors.fill: parent
            enabled: !overlaySettings.locked && !overlaySettings.clickThrough
            onPressed: overlayWindow.startSystemMove()
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 10 * overlaySettings.scale
            spacing: 6 * overlaySettings.scale

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 42 * overlaySettings.scale
                radius: 7
                color: "#6b1725"
                border.width: 1
                border.color: "#ec3d50"
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 10
                    spacing: 9
                    Label {
                        text: t("overlay.nba_icon", "»")
                        rotation: 90
                        color: "#ff5063"
                        font.pixelSize: 24 * overlaySettings.scale
                        font.bold: true
                    }
                    Label {
                        text: t("operations.next_action", "NEXT BEST ACTION")
                        color: "#ffffff"
                        font.pixelSize: 16 * overlaySettings.scale
                        font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: overlaySettings.locked ? t("overlay.locked", "LOCKED") : t("overlay.move", "MOVE")
                        color: "#ff9eaa"
                        font.pixelSize: 9 * overlaySettings.scale
                        font.bold: true
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Rectangle {
                    Layout.preferredWidth: 4
                    Layout.fillHeight: true
                    radius: 2
                    color: materialReady ? "#42d888" : "#f2ae4b"
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Label {
                        text: action.priority
                              ? t("operations.priority", "PRIORITY") + " · " + String(action.moduleName || "").toUpperCase()
                              : t("overlay.engineering", "ENGINEERING") + " · " + (cockpit.activeShip || t("overlay.no_ship", "NO SHIP"))
                        color: action.priority ? "#ffb13b" : "#39c9ef"
                        font.pixelSize: 10 * overlaySettings.scale
                        font.bold: true
                    }
                    Label {
                        Layout.fillWidth: true
                        text: action.shortTitle || action.title || cockpit.nextAction
                        color: "#ffffff"
                        font.pixelSize: 17 * overlaySettings.scale
                        font.bold: true
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                    }
                    Label {
                        Layout.fillWidth: true
                        text: [action.blueprintName || "",
                               (action.actionGrade || action.targetGrade) > 0 ? "G" + (action.actionGrade || action.targetGrade) : ""]
                              .filter(function(value) { return !!value }).join(" · ")
                        color: "#42d888"
                        font.pixelSize: 11 * overlaySettings.scale
                        font.bold: true
                        elide: Text.ElideRight
                    }
                    Label {
                        visible: !!action.experimentalName
                        Layout.fillWidth: true
                        text: String(action.experimentalName || "").toUpperCase()
                              + (effectSummary() ? " · " + effectSummary() : "")
                        color: "#f2ae4b"
                        font.pixelSize: 11 * overlaySettings.scale
                        font.bold: true
                        elide: Text.ElideRight
                    }
                    Label {
                        Layout.fillWidth: true
                        text: [action.engineerName || "", action.station || "", action.system || ""]
                              .filter(function(value) { return !!value }).join(" · ")
                        color: "#a9bdd0"
                        font.pixelSize: 10 * overlaySettings.scale
                        elide: Text.ElideRight
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 38 * overlaySettings.scale
                radius: 7
                color: materialReady ? "#193e32" : "#49321c"
                border.width: 1
                border.color: materialReady ? "#42d888" : "#f2ae4b"
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 11
                    anchors.rightMargin: 11
                    spacing: 8
                    Label {
                        text: materialReady ? "✓" : "!"
                        color: materialReady ? "#42d888" : "#f2ae4b"
                        font.pixelSize: 16 * overlaySettings.scale
                        font.bold: true
                    }
                    Label {
                        text: materialReady ? t("overlay.materials_ready", "MATERIALS READY")
                                            : t("overlay.materials_required", "MATERIALS REQUIRED")
                        color: materialReady ? "#42d888" : "#f2ae4b"
                        font.pixelSize: 11 * overlaySettings.scale
                        font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: materialCovered + " / " + materialRequired
                        color: "#ffffff"
                        font.pixelSize: 12 * overlaySettings.scale
                        font.bold: true
                    }
                }
            }

            Label {
                Layout.fillWidth: true
                text: t("overlay.fullscreen_warning", "Exclusive fullscreen may cover overlays; use Borderless Windowed.")
                color: "#687b8f"
                font.pixelSize: 8 * overlaySettings.scale
                visible: !overlaySettings.clickThrough
            }
            RowLayout {
                visible: !overlaySettings.locked && !overlaySettings.clickThrough
                spacing: 5
                Button { text: t("overlay.opacity_down", "− OPACITY"); onClicked: overlaySettings.opacity -= 0.05 }
                Button { text: t("overlay.opacity_up", "+ OPACITY"); onClicked: overlaySettings.opacity += 0.05 }
                Button { text: t("overlay.scale_down", "− SCALE"); onClicked: overlaySettings.scale -= 0.05 }
                Button { text: t("overlay.scale_up", "+ SCALE"); onClicked: overlaySettings.scale += 0.05 }
                Button { text: t("overlay.click_through", "CLICK-THROUGH"); onClicked: overlaySettings.toggleClickThrough() }
            }
        }
    }
}
