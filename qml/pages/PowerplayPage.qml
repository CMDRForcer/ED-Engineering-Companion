import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    id: powerplayPage
    required property var appWindow
    required property real sidebarWidth
    readonly property var overview: cockpit.powerplayOverview || ({})
    readonly property var location: overview.location || ({})
    readonly property color cyan: appWindow.cyan
    readonly property color green: appWindow.green
    readonly property color orange: appWindow.orange
    readonly property color textPrimary: appWindow.textPrimary
    readonly property color textSecondary: appWindow.textSecondary
    readonly property color muted: appWindow.muted
    readonly property color inputBackground: appWindow.inputBackground
    readonly property color divider: appWindow.divider

    objectName: "qa-page-powerplay"
    anchors.fill: parent
    anchors.leftMargin: sidebarWidth + (appWindow.compactSidebar ? 18 : 26)
    anchors.rightMargin: appWindow.compactSidebar ? 18 : 26
    anchors.topMargin: appWindow.compactSidebar ? 18 : 26
    anchors.bottomMargin: appWindow.compactSidebar ? 18 : 26
    spacing: 14

    WorkspaceHeader {
        appWindow: powerplayPage.appWindow
        eyebrow: appWindow.t("powerplay.workspace", "GALACTIC INFLUENCE")
        title: appWindow.t("powerplay.title", "POWERPLAY")
        subtitle: overview.pledged
                  ? appWindow.t("powerplay.subtitle_pledged", "Journal-derived Commander pledge and local system state")
                  : appWindow.t("powerplay.subtitle_none", "No active Power pledge found in this Commander's Journal")
        statusText: overview.pledged && overview.timePledgedKnown
                    ? appWindow.tf("powerplay.pledged", "PLEDGED FOR %1 H", [appWindow.powerplayPledgedHours(overview)])
                    : ""
        statusTone: cyan
    }

    EmptyState {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: !overview.pledged
        symbol: "⚑"
        title: appWindow.t("powerplay.none", "NO POWER AFFILIATION")
        detail: appWindow.t("powerplay.none_help", "EDEC will show Powerplay data after the Journal confirms a Commander pledge. No external account or CAPI query is used.")
        tone: cyan
    }

    ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: overview.pledged
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: parent.width
            spacing: 14

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: leaderPortraitColumn.implicitHeight
                ColumnLayout {
                    id: leaderPortraitColumn
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 10
                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: appWindow.narrowWorkspace ? 210 : 260
                        Layout.preferredHeight: width
                        radius: 16
                        color: inputBackground
                        border.width: 2
                        border.color: cyan
                        clip: true
                        Image {
                            anchors.fill: parent
                            anchors.margins: 2
                            source: appWindow.powerplayLeaderPortrait(overview.power)
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                            cache: true
                        }
                    }
                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: overview.power || ""
                        color: textPrimary
                        font.pixelSize: 28
                        font.bold: true
                    }
                }
            }

            Label {
                Layout.fillWidth: true
                Layout.maximumWidth: 1050
                Layout.alignment: Qt.AlignHCenter
                visible: text.length > 0
                text: appWindow.powerplayLeaderBiography(overview.power)
                color: textSecondary
                font.pixelSize: 12
                lineHeight: 1.25
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }

            GridLayout {
                Layout.fillWidth: true
                columns: appWindow.narrowWorkspace ? 1 : 3
                columnSpacing: 14
                rowSpacing: 14
                Repeater {
                    model: [
                        {"label": appWindow.t("powerplay.rank", "RANK"), "value": overview.rankKnown ? appWindow.t("powerplay.rank", "RANK") + " " + overview.rank : ""},
                        {"label": appWindow.t("powerplay.merits", "MERITS"), "value": overview.meritsKnown ? Number(overview.merits).toLocaleString(Qt.locale(), "f", 0) : ""},
                        {"label": appWindow.t("powerplay.pledged_for", "PLEDGED FOR"), "value": overview.timePledgedKnown ? appWindow.powerplayPledgedHours(overview) + " H" : ""}
                    ]
                    delegate: ShadowCard {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 118
                        visible: modelData.value.length > 0
                        accent: cyan
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 18
                            spacing: 8
                            Label { text: modelData.label; color: muted; font.pixelSize: 11; font.bold: true }
                            Label { Layout.fillWidth: true; text: modelData.value; color: textPrimary; font.pixelSize: 23; font.bold: true; elide: Text.ElideRight }
                            Item { Layout.fillHeight: true }
                        }
                    }
                }
            }

            ShadowCard {
                Layout.fillWidth: true
                Layout.preferredHeight: location.tugKnown ? 300 : 208
                visible: !!location.system
                accent: appWindow.powerplayStateColor(location.state)
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                    Label { text: appWindow.t("powerplay.current_system", "CURRENT SYSTEM"); color: textPrimary; font.pixelSize: 16; font.bold: true }
                            Label { Layout.fillWidth: true; text: location.system || ""; color: textSecondary; font.pixelSize: 14; elide: Text.ElideRight }
                            Label {
                                visible: !!location.controllingPower
                                text: appWindow.tf("powerplay.controlled", "CONTROLLED BY %1", [location.controllingPower])
                                color: muted
                                font.pixelSize: 9
                                font.bold: true
                            }
                        }
                        Rectangle {
                            visible: !!location.state
                            implicitWidth: stateBadgeText.implicitWidth + 28
                            implicitHeight: 34
                            radius: 17
                            color: Qt.rgba(
                                appWindow.powerplayStateColor(location.state).r,
                                appWindow.powerplayStateColor(location.state).g,
                                appWindow.powerplayStateColor(location.state).b, 0.22)
                            border.width: 1
                            border.color: appWindow.powerplayStateColor(location.state)
                            Label {
                                id: stateBadgeText
                                anchors.centerIn: parent
                                text: appWindow.localizedStatus(location.state)
                                color: appWindow.powerplayStateColor(location.state)
                                font.pixelSize: 11
                                font.bold: true
                            }
                        }
                    }
                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 54
                        Row {
                            id: powerplayZones
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            height: 14
                            spacing: 3
                            Repeater {
                                model: [
                                    ["Unoccupied", appWindow.powerplayUnoccupied],
                                    ["Exploited", appWindow.powerplayExploited],
                                    ["Fortified", appWindow.powerplayFortified],
                                    ["Stronghold", appWindow.powerplayStronghold]
                                ]
                                delegate: Rectangle {
                                    required property var modelData
                                    width: (powerplayZones.width - 9) / 4
                                    height: 14
                                    radius: 5
                                    color: modelData[1]
                                    opacity: String(location.state).toLowerCase()
                                             === String(modelData[0]).toLowerCase() ? 1.0 : 0.3
                                }
                            }
                        }
                        RowLayout {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            Repeater {
                                model: ["UNOCCUPIED", "EXPLOITED", "FORTIFIED", "STRONGHOLD"]
                                delegate: Label {
                                    required property string modelData
                                    required property int index
                                    Layout.fillWidth: true
                                    text: appWindow.localizedStatus(modelData)
                                    horizontalAlignment: index === 0 ? Text.AlignLeft
                                                         : index === 3 ? Text.AlignRight
                                                         : Text.AlignHCenter
                                    color: muted
                                    font.pixelSize: 9
                                }
                            }
                        }
                    }
                    Label {
                        visible: location.controlProgressKnown === true
                        text: appWindow.t("powerplay.control_progress", "CONTROL PROGRESS · ")
                              + Math.round(location.controlProgress * 100) + "%"
                        color: cyan
                        font.pixelSize: 10
                        font.bold: true
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 7
                        visible: location.tugKnown === true
                        Label { text: appWindow.t("powerplay.tug_of_war", "TUG OF WAR"); color: textPrimary; font.pixelSize: 13; font.bold: true }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Label {
                                text: Number(location.undermining).toLocaleString(Qt.locale(), "f", 0)
                                color: appWindow.powerplayUndermining
                                font.pixelSize: 13
                                font.bold: true
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                height: 14
                                radius: 7
                                color: inputBackground
                                Row {
                                    anchors.fill: parent
                                    property real total: Math.max(1, Number(location.undermining) + Number(location.reinforcement))
                                    Rectangle { width: parent.width * Number(location.undermining) / parent.total; height: parent.height; color: appWindow.powerplayUndermining; radius: 7 }
                                    Rectangle { width: parent.width * Number(location.reinforcement) / parent.total; height: parent.height; color: appWindow.powerplayReinforcement; radius: 7 }
                                }
                            }
                            Label {
                                text: Number(location.reinforcement).toLocaleString(Qt.locale(), "f", 0)
                                color: appWindow.powerplayReinforcement
                                font.pixelSize: 13
                                font.bold: true
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: appWindow.t("powerplay.undermining", "UNDERMINING"); color: muted; font.pixelSize: 9 }
                            Item { Layout.fillWidth: true }
                            Label { text: appWindow.t("powerplay.reinforcement", "REINFORCEMENT"); color: muted; font.pixelSize: 9 }
                        }
                    }
                }
            }

            ShadowCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 76
                visible: overview.salaryKnown === true
                accent: orange
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                        Label { text: appWindow.t("powerplay.last_salary", "LAST SALARY"); color: textPrimary; font.pixelSize: 13; font.bold: true }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: Number(overview.salary.amount).toLocaleString(Qt.locale(), "f", 0)
                              + " CR · " + appWindow.relativeJournalTime(overview.salary.timestamp)
                        color: textSecondary
                        font.pixelSize: 13
                    }
                }
            }

            ShadowCard {
                Layout.fillWidth: true
                Layout.preferredHeight: 64 + overview.cargoHistory.length * 48
                visible: overview.cargoHistory.length > 0
                accent: cyan
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 4
                        Label { text: appWindow.t("powerplay.recent_cargo", "RECENT CARGO ACTIVITY"); color: textPrimary; font.pixelSize: 14; font.bold: true }
                    Repeater {
                        model: overview.cargoHistory
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 3
                            Rectangle { Layout.fillWidth: true; height: 1; color: divider }
                            RowLayout {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 40
                                Label {
                                    Layout.fillWidth: true
                                    text: appWindow.t(
                                              modelData.direction === "DELIVER"
                                              ? "powerplay.deliver" : "powerplay.collect",
                                              modelData.direction) + " · " + modelData.type
                                    color: modelData.direction === "DELIVER" ? green : cyan
                                    font.pixelSize: 11
                                    font.bold: true
                                    elide: Text.ElideRight
                                }
                                Label {
                                    text: modelData.count + "×"
                                          + (modelData.system ? " · " + modelData.system : "")
                                    color: textSecondary
                                    font.pixelSize: 11
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
