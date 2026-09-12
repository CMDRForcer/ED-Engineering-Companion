import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    id: exobiologyPage
    required property var appWindow
    required property real sidebarWidth

    readonly property color cyan: appWindow.cyan
    readonly property color green: appWindow.green
    readonly property color orange: appWindow.orange
    readonly property color textPrimary: appWindow.textPrimary
    readonly property color textSecondary: appWindow.textSecondary
    readonly property color muted: appWindow.muted
    readonly property color panelRaised: appWindow.panelRaised
    readonly property color borderTone: appWindow.borderTone

    property bool showAllSystems: false

    readonly property var summary: cockpit.exobiologySummary || ({})
    readonly property var sessionSummary: cockpit.exobiologySessionSummary || ({})
    readonly property var carriedSummary: cockpit.exobiologyCarriedSummary || ({})
    readonly property var landingTargets: cockpit.exobiologyLandingTargets || []
    readonly property var currentSystemTargets: exobiologyPage.landingTargets.filter(function(target) {
        return target.inCurrentSystem
    })
    readonly property var otherSystemsCount: exobiologyPage.landingTargets.length - exobiologyPage.currentSystemTargets.length
    readonly property var displayedTargets: exobiologyPage.showAllSystems
                                             ? exobiologyPage.landingTargets
                                             : exobiologyPage.currentSystemTargets

    function formatCr(value) {
        return Number(value || 0).toLocaleString(Qt.locale(), "f", 0) + " CR"
    }

    objectName: "qa-page-exobiology"
    anchors.fill: parent
    anchors.leftMargin: sidebarWidth + (appWindow.compactSidebar ? 18 : 26)
    anchors.rightMargin: appWindow.compactSidebar ? 18 : 26
    anchors.topMargin: appWindow.compactSidebar ? 18 : 26
    anchors.bottomMargin: appWindow.compactSidebar ? 18 : 26
    spacing: 14

    WorkspaceHeader {
        appWindow: exobiologyPage.appWindow
        eyebrow: appWindow.t("exobiology.workspace", "BIOLOGICAL SURVEY")
        title: appWindow.t("exobiology.title", "EXOBIOLOGY")
        subtitle: appWindow.tf(
            "exobiology.subtitle", "%1 tracked species · Journal-derived scan progress",
            [exobiologyPage.summary.totalSpecies || 0])
        statusText: cockpit.journalAuto
                    ? appWindow.t("common.live", "LIVE")
                    : appWindow.t("common.paused", "PAUSED")
        statusTone: cockpit.journalAuto ? green : orange
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 12
        Repeater {
            model: [
                {"label": appWindow.t("exobiology.species_found", "SPECIES FOUND"), "value": exobiologyPage.summary.totalSpecies || 0, "tone": cyan},
                {"label": appWindow.t("exobiology.complete", "COMPLETE"), "value": exobiologyPage.summary.completeSpecies || 0, "tone": green},
                {"label": appWindow.t("exobiology.in_progress", "IN PROGRESS"), "value": exobiologyPage.summary.inProgressSpecies || 0, "tone": orange},
                {"label": appWindow.t("exobiology.this_session", "THIS SESSION"), "value": appWindow.tf(
                     "exobiology.session_value", "%1 · %2",
                     [exobiologyPage.sessionSummary.speciesCount || 0, exobiologyPage.formatCr(exobiologyPage.sessionSummary.totalValue)]), "tone": green},
                {"label": appWindow.t("exobiology.carried_unsold", "CARRIED · UNSOLD"), "value": appWindow.tf(
                     "exobiology.carried_value", "%1 · %2",
                     [exobiologyPage.carriedSummary.speciesCount || 0, exobiologyPage.formatCr(exobiologyPage.carriedSummary.totalValue)]), "tone": orange}
            ]
            delegate: ShadowCard {
                required property var modelData
                required property int index
                objectName: index === 0 ? "qa-card-exobiology" : ""
                Layout.fillWidth: true
                Layout.preferredHeight: 88
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 15
                    Label { text: modelData.label; color: muted; font.pixelSize: 10; font.bold: true }
                    Label {
                        text: modelData.value; color: modelData.tone
                        font.pixelSize: 18; font.bold: true
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }
            }
        }
    }

    ShadowCard {
        Layout.fillWidth: true
        Layout.fillHeight: true
        accent: orange
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    text: exobiologyPage.showAllSystems
                          ? appWindow.t("exobiology.survey_targets_all", "SURVEY TARGETS · ALL SYSTEMS")
                          : appWindow.t("exobiology.survey_targets_current", "SURVEY TARGETS · THIS SYSTEM")
                    color: orange; font.pixelSize: 15; font.bold: true
                }
                Item { Layout.fillWidth: true }
                Label {
                    visible: exobiologyPage.displayedTargets.length > 0
                    text: appWindow.tf(
                        "exobiology.survey_targets_count", "%1 BODIES WORTH CHECKING",
                        [exobiologyPage.displayedTargets.length])
                    color: muted; font.pixelSize: 11; font.bold: true
                }
            }
            Label {
                Layout.fillWidth: true
                text: appWindow.t(
                    "exobiology.survey_targets_help",
                    "From confirmed FSS/DSS signals · genus confirmed by a Detailed Surface Scan is shown before a species guessed from planetary conditions alone")
                color: muted; font.pixelSize: 11
                wrapMode: Text.WordWrap
            }
            ListView {
                id: targetList
                Layout.fillWidth: true
                Layout.fillHeight: true
                orientation: ListView.Horizontal
                spacing: 14
                clip: true
                visible: exobiologyPage.displayedTargets.length > 0
                model: exobiologyPage.displayedTargets
                ScrollBar.horizontal: CockpitScrollBar {}
                delegate: Rectangle {
                    required property var modelData
                    width: 300
                    height: targetList.height
                    radius: 13
                    color: panelRaised
                    border.width: modelData.inCurrentSystem ? 2 : 1
                    border.color: modelData.inCurrentSystem ? orange : borderTone
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: modelData.bodyName
                                color: textPrimary; font.pixelSize: 16; font.bold: true
                                Layout.fillWidth: true; elide: Text.ElideRight
                            }
                            StatusBadge {
                                statusText: modelData.confidence === "confirmed_genus"
                                            ? appWindow.t("exobiology.confirmed", "CONFIRMED")
                                            : appWindow.t("exobiology.predicted", "PREDICTED")
                                tone: modelData.confidence === "confirmed_genus" ? green : cyan
                            }
                        }
                        Label {
                            text: modelData.inCurrentSystem
                                  ? appWindow.tf(
                                        "exobiology.body_detail", "%1 · %2 LS",
                                        [modelData.planetClass, Number(modelData.distanceLs).toLocaleString(Qt.locale(), "f", 0)])
                                  : modelData.starSystem
                            color: modelData.inCurrentSystem ? orange : muted
                            font.pixelSize: 13; font.bold: modelData.inCurrentSystem
                            Layout.fillWidth: true; elide: Text.ElideRight
                        }
                        Label {
                            text: appWindow.tf(
                                "exobiology.signal_count", "%1 BIOLOGICAL SIGNAL(S)",
                                [modelData.signalCount])
                            color: cyan; font.pixelSize: 13; font.bold: true
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                        Repeater {
                            model: modelData.candidates.slice(0, 3)
                            delegate: Label {
                                required property var modelData
                                Layout.fillWidth: true
                                text: modelData.name + " · " + exobiologyPage.formatCr(modelData.value)
                                color: textSecondary; font.pixelSize: 13
                                elide: Text.ElideRight
                            }
                        }
                        Item { Layout.fillHeight: true }
                        Label {
                            Layout.fillWidth: true
                            text: appWindow.tf(
                                "exobiology.best_value", "UP TO %1",
                                [exobiologyPage.formatCr(modelData.bestValue)])
                            color: green; font.pixelSize: 17; font.bold: true
                        }
                    }
                }
            }
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: exobiologyPage.displayedTargets.length === 0
                EmptyState {
                    anchors.centerIn: parent
                    symbol: "◎"
                    title: appWindow.t("exobiology.no_current_targets", "NOTHING LEFT TO CHECK HERE YET")
                    detail: appWindow.t(
                        "exobiology.no_current_targets_help",
                        "Honk the system with the FSS, then head for a body showing biological signals — targets appear here as soon as the Journal reports them.")
                    tone: orange
                }
            }
            RowLayout {
                Layout.fillWidth: true
                visible: exobiologyPage.showAllSystems || exobiologyPage.otherSystemsCount > 0
                Item { Layout.fillWidth: true }
                Label {
                    text: exobiologyPage.showAllSystems
                          ? appWindow.t("exobiology.show_current_system", "SHOW CURRENT SYSTEM ONLY")
                          : appWindow.tf(
                                "exobiology.show_all_systems", "SHOW %1 MORE ACROSS OTHER SYSTEMS",
                                [exobiologyPage.otherSystemsCount])
                    color: cyan; font.pixelSize: 11; font.bold: true
                    MouseArea {
                        anchors.fill: parent
                        anchors.margins: -4
                        cursorShape: Qt.PointingHandCursor
                        onClicked: exobiologyPage.showAllSystems = !exobiologyPage.showAllSystems
                    }
                }
            }
        }
    }
}
