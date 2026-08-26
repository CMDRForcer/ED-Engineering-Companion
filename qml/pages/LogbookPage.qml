import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    id: logbookPage
    required property var appWindow
    required property real sidebarWidth
    signal entryRequested(var entryId)

    readonly property color cyan: appWindow.cyan
    readonly property color green: appWindow.green
    readonly property color orange: appWindow.orange
    readonly property color textPrimary: appWindow.textPrimary
    readonly property color textSecondary: appWindow.textSecondary
    readonly property color muted: appWindow.muted
    readonly property color active: appWindow.active
    readonly property color panelRaised: appWindow.panelRaised
    readonly property color backgroundPrimary: appWindow.backgroundPrimary
    readonly property color borderTone: appWindow.borderTone
    readonly property color hover: appWindow.hover

    objectName: "qa-page-logbook"
    anchors.fill: parent
    anchors.leftMargin: sidebarWidth + (appWindow.compactSidebar ? 18 : 26)
    anchors.rightMargin: appWindow.compactSidebar ? 18 : 26
    anchors.topMargin: appWindow.compactSidebar ? 18 : 26
    anchors.bottomMargin: appWindow.compactSidebar ? 18 : 26
    spacing: 14

    RowLayout {
        Layout.fillWidth: true
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Label { text: appWindow.t("logbook.title", "COMMANDER LOGBOOK"); color: textPrimary; font.pixelSize: 24; font.bold: true }
            Label { text: appWindow.t("logbook.subtitle", "Profile-isolated Journal highlights · newest first"); color: muted; font.pixelSize: 11 }
        }
        Label {
                text: cockpit.journalAuto
                      ? appWindow.t("common.live", "● LIVE")
                      : appWindow.t("common.paused", "Ⅱ PAUSED")
            color: cockpit.journalAuto ? green : orange
            font.pixelSize: 11
            font.bold: true
        }
    }
    ShadowCard {
        Layout.fillWidth: true
        Layout.preferredHeight: 184
        accent: green
        RowLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 18
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: appWindow.t("logbook.current_session", "CURRENT SESSION"); color: green; font.pixelSize: 12; font.bold: true }
                    Item { Layout.fillWidth: true }
                    Label {
                        visible: !!cockpit.currentSession.active
                        text: appWindow.formatSessionDuration(
                                  appWindow.currentSessionSeconds(cockpit.currentSession))
                        color: textPrimary
                        font.pixelSize: 18
                        font.bold: true
                    }
                }
                GridLayout {
                    Layout.fillWidth: true
                    visible: !!cockpit.currentSession.active
                    columns: 4
                    rowSpacing: 8
                    columnSpacing: 8
                    Repeater {
                        model: [
                            ["FSD JUMPS", cockpit.currentSession.fsdJumps || 0],
                            ["DISTANCE", Number(cockpit.currentSession.distanceLy || 0).toFixed(1) + " ly"],
                            ["DOCKINGS", cockpit.currentSession.dockings || 0],
                            ["CRAFTS", cockpit.currentSession.engineerCrafts || 0],
                            ["EXPERIMENTALS", cockpit.currentSession.experimentalCrafts || 0],
                            ["TRADES", cockpit.currentSession.materialTrades || 0],
                            ["SYSTEMS", cockpit.currentSession.visitedSystems || 0]
                        ]
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 48
                            radius: 9
                            color: active
                            border.width: 1
                            border.color: borderTone
                            ColumnLayout {
                                anchors.centerIn: parent
                                spacing: 1
                                Label { text: modelData[0]; color: muted; font.pixelSize: 8; font.bold: true }
                                Label { text: modelData[1]; color: textPrimary; font.pixelSize: 12; font.bold: true }
                            }
                        }
                    }
                }
                EmptyState {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: !cockpit.currentSession.active
                    symbol: "◷"
                    title: appWindow.t("logbook.no_active", "NO ACTIVE SESSION")
                    detail: appWindow.t("logbook.no_active_help", "A session starts when the active Commander writes a LoadGame event.")
                    tone: green
                }
            }
            Rectangle { Layout.fillHeight: true; width: 1; color: borderTone }
            ColumnLayout {
                Layout.preferredWidth: 320
                Layout.fillHeight: true
                spacing: 6
                    Label { text: appWindow.t("logbook.recent_sessions", "RECENT SESSIONS"); color: cyan; font.pixelSize: 12; font.bold: true }
                ListView {
                    id: recentSessionList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: cockpit.recentSessions.slice(0, 3)
                    spacing: 5
                    clip: true
                    delegate: Rectangle {
                        required property var modelData
                        width: recentSessionList.width
                        height: 42
                        radius: 8
                        color: panelRaised
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 8
                            Label {
                                text: (modelData.start || "").slice(0, 10)
                                color: orange
                                font.pixelSize: 9
                                font.bold: true
                            }
                            Label {
                                text: appWindow.formatSessionDuration(modelData.durationSeconds)
                                      + " · " + modelData.fsdJumps + " jumps"
                                      + " · " + Number(modelData.distanceLy || 0).toFixed(1) + " ly"
                                color: textSecondary
                                font.pixelSize: 9
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: parent.count === 0
                        text: appWindow.t("logbook.no_history", "No completed sessions stored yet.")
                        color: muted
                        font.pixelSize: 10
                    }
                }
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: 10
        TextField {
            Layout.fillWidth: true
            placeholderText: appWindow.t("logbook.search", "Search system, station, blueprint, material or ship…")
            onTextChanged: cockpit.setLogbookQuery(text)
        }
        ComboBox {
            Layout.preferredWidth: 210
            model: cockpit.logbookFilters
            onActivated: cockpit.setLogbookFilter(currentText)
        }
        Label {
                text: appWindow.tf("logbook.entry_count", "%1 ENTRIES", [cockpit.logbookEntries.length])
            color: cyan
            font.pixelSize: 10
            font.bold: true
        }
    }
    ShadowCard {
        Layout.fillWidth: true
        Layout.fillHeight: true
        accent: cyan
        ListView {
            id: logbookList
            anchors.fill: parent
            anchors.margins: 16
            model: cockpit.logbookEntries
            spacing: 8
            clip: true
            ScrollBar.vertical: CockpitScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: logbookList.width - 10
                height: modelData.note ? 102 : 82
                radius: 11
                color: logbookMouse.containsMouse ? hover : backgroundPrimary
                border.width: 1
                border.color: borderTone
                activeFocusOnTab: true
                Accessible.name: appWindow.tf("logbook.open_entry", "Open Logbook entry: %1", [modelData.title])
                Accessible.role: Accessible.Button
                Keys.onReturnPressed: logbookPage.entryRequested(modelData.id)
                MouseArea {
                    id: logbookMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: logbookPage.entryRequested(modelData.id)
                }
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 12
                    Rectangle {
                        width: 118
                        height: 48
                        radius: 9
                        color: active
                        ColumnLayout {
                            anchors.centerIn: parent
                            spacing: 1
                            Label { text: modelData.category; color: cyan; font.pixelSize: 8; font.bold: true }
                            Label { text: modelData.event; color: textPrimary; font.pixelSize: 9; font.bold: true }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Label { text: modelData.title; color: textPrimary; font.pixelSize: 13; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                        Label { text: modelData.summary || appWindow.t("logbook.journal_event", "Journal event"); color: muted; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                        Label {
                            text: [modelData.system, modelData.station, modelData.ship].filter(function(value) { return !!value }).join(" · ")
                            color: green
                            font.pixelSize: 9
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        Label {
                            visible: !!modelData.note
                            text: appWindow.tf("logbook.note_label", "NOTE · %1", [modelData.note || ""])
                            color: orange
                            font.pixelSize: 9
                            font.bold: true
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                    }
                    ColumnLayout {
                        Layout.preferredWidth: 92
                        spacing: 2
                        Label { text: modelData.date; color: muted; font.pixelSize: 9 }
                        Label { text: modelData.time + " UTC"; color: orange; font.pixelSize: 9; font.bold: true }
                    }
                }
            }
            EmptyState {
                anchors.centerIn: parent
                visible: parent.count === 0
                symbol: "≣"
                title: cockpit.commanderKnown
                       ? appWindow.t("logbook.no_matches", "NO MATCHING LOGBOOK ENTRIES")
                       : appWindow.t("logbook.no_journal", "NO COMMANDER JOURNAL DETECTED")
                detail: cockpit.commanderKnown
                        ? "Adjust the filter or search text."
                        : "Start Elite Dangerous or configure the Journal path in Settings."
                tone: cyan
            }
        }
    }
}
