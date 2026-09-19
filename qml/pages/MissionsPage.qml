import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ColumnLayout {
    id: missionsPage
    required property var appWindow
    required property real sidebarWidth
    readonly property var missions: cockpit.activeMissions || []
    readonly property var summary: cockpit.missionsSummary || ({})
    readonly property var goals: cockpit.communityGoals || []
    readonly property color cyan: appWindow.cyan
    readonly property color green: appWindow.green
    readonly property color orange: appWindow.orange
    readonly property color danger: appWindow.danger
    readonly property color textPrimary: appWindow.textPrimary
    readonly property color textSecondary: appWindow.textSecondary
    readonly property color muted: appWindow.muted
    readonly property color panelRaised: appWindow.panelRaised
    readonly property color borderTone: appWindow.borderTone
    readonly property color inputBackground: appWindow.inputBackground
    readonly property color dangerBackground: appWindow.errorBackground
    readonly property color successBackground: appWindow.successBackground
    readonly property color warningBackground: appWindow.warningBackground
    readonly property string monoFont: "Consolas"

    function isUrgent(expiry) {
        var parsed = Date.parse(expiry || "")
        if (isNaN(parsed))
            return false
        return (parsed - sessionClock) < 6 * 3600 * 1000
    }
    function missionKind(m) {
        if (m.killCount) return "combat"
        if (m.target) return "combat"
        if (m.donation !== null && m.donation !== undefined) return "donation"
        if (m.commodity && m.commodityCount) return "delivery"
        return "courier"
    }
    function missionKindIcon(kind) {
        return kind === "combat" ? "⚔"
             : kind === "donation" ? "¤"
             : kind === "delivery" ? "◧"
             : "✈"
    }
    function missionKindColor(kind) {
        return kind === "combat" ? danger
             : kind === "donation" ? orange
             : kind === "delivery" ? cyan
             : green
    }
    function missionKindBackground(kind) {
        return kind === "combat" ? dangerBackground
             : kind === "donation" ? warningBackground
             : kind === "delivery" ? Qt.rgba(cyan.r, cyan.g, cyan.b, 0.16)
             : successBackground
    }
    function missionKindLabel(kind) {
        return kind === "combat" ? appWindow.t("missions.tag_combat", "COMBAT")
             : kind === "donation" ? appWindow.t("missions.tag_donation", "DONATION")
             : kind === "delivery" ? appWindow.t("missions.tag_delivery", "DELIVERY")
             : appWindow.t("missions.tag_courier", "COURIER")
    }
    function missionDetailLine(m) {
        if (m.commodity && m.commodityCount)
            return m.commodityCount + "× " + m.commodity
                   + (m.destinationStation ? " → " + m.destinationStation : "")
        if (m.killCount)
            return appWindow.tf("missions.kill_target", "Kill %1× %2",
                                 [m.killCount, m.targetFaction || m.targetType || "?"])
        if (m.target)
            return appWindow.tf("missions.assassinate_target", "Target: %1", [m.target])
        if (m.donation !== null && m.donation !== undefined)
            return appWindow.t("missions.donation", "Donation")
        if (m.destinationStation)
            return m.destinationStation + (m.destinationSystem ? " · " + m.destinationSystem : "")
        return m.faction
    }
    function missionRewardText(m) {
        if (m.reward !== null && m.reward !== undefined)
            return Number(m.reward).toLocaleString(Qt.locale(), "f", 0)
        if (m.donation !== null && m.donation !== undefined)
            return Number(m.donation).toLocaleString(Qt.locale(), "f", 0)
        return "—"
    }
    function tierNumber(text) {
        var match = /(\d+)/.exec(text || "")
        return match ? parseInt(match[1], 10) : 0
    }

    objectName: "qa-page-missions"
    anchors.fill: parent
    anchors.leftMargin: sidebarWidth + (appWindow.compactSidebar ? 18 : 26)
    anchors.rightMargin: appWindow.compactSidebar ? 18 : 26
    anchors.topMargin: appWindow.compactSidebar ? 18 : 26
    anchors.bottomMargin: appWindow.compactSidebar ? 18 : 26
    spacing: 14

    WorkspaceHeader {
        appWindow: missionsPage.appWindow
        eyebrow: appWindow.t("missions.workspace", "STATION BOARD")
        title: appWindow.t("missions.title", "MISSIONS & COMMUNITY GOALS")
        subtitle: appWindow.t("missions.subtitle", "Read from your Journal — nothing here is sent back to the game")
        statusText: missions.length
                    ? appWindow.tf("missions.status_active", "%1 ACTIVE", [missions.length]) : ""
        statusTone: cyan
    }

    GridLayout {
        Layout.fillWidth: true
        columns: appWindow.narrowWorkspace ? 2 : 4
        columnSpacing: 10
        rowSpacing: 10
        Repeater {
            model: [
                {
                    "label": appWindow.t("missions.stat_active", "ACTIVE MISSIONS"),
                    "value": String(summary.activeCount || 0),
                    "unit": "",
                    "warn": false,
                },
                {
                    "label": appWindow.t("missions.stat_reward", "REWARD AT STAKE"),
                    "value": (summary.missionsWithKnownReward < summary.activeCount ? "≥" : "")
                             + Number(summary.totalReward || 0).toLocaleString(Qt.locale(), "f", 0),
                    "unit": "CR",
                    "warn": false,
                },
                {
                    "label": appWindow.t("missions.stat_deadline", "NEAREST DEADLINE"),
                    "value": summary.nearestExpiry ? appWindow.timeUntil(summary.nearestExpiry) : "—",
                    "unit": "",
                    "warn": !!summary.nearestExpiry && missionsPage.isUrgent(summary.nearestExpiry),
                },
                {
                    "label": appWindow.t("missions.stat_cgs", "COMMUNITY GOALS"),
                    "value": String(goals.length),
                    "unit": appWindow.t("missions.joined_unit", "joined"),
                    "warn": false,
                },
            ]
            delegate: ShadowCard {
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredHeight: 82
                accent: modelData.warn ? danger : cyan
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 5
                        Label {
                            text: modelData.value
                            color: modelData.warn ? danger : textPrimary
                            font.family: monoFont
                            font.pixelSize: 20
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Label {
                            visible: !!modelData.unit
                            Layout.alignment: Qt.AlignBaseline
                            text: modelData.unit
                            color: muted
                            font.pixelSize: 10
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Label { text: modelData.label; color: muted; font.pixelSize: 9; font.bold: true }
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: 14

        ShadowCard {
            Layout.fillWidth: true
            Layout.preferredWidth: 3
            Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: appWindow.t("missions.active_missions", "ACTIVE MISSIONS")
                        color: cyan; font.pixelSize: 11; font.bold: true
                    }
                    Label {
                        text: appWindow.tf("missions.tracked_count", "%1 TRACKED", [missions.length])
                        color: muted; font.family: monoFont; font.pixelSize: 10
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }

                EmptyState {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: missions.length === 0
                    symbol: "⚑"
                    title: appWindow.t("missions.none", "NO ACTIVE MISSIONS")
                    detail: appWindow.t("missions.none_help", "Accept a mission at any station's Mission Board and it will appear here within a couple of seconds.")
                    tone: cyan
                }

                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: missions.length > 0
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    ColumnLayout {
                        width: parent.width
                        spacing: 8
                        Repeater {
                            model: missions
                            delegate: Rectangle {
                                id: missionRow
                                required property var modelData
                                readonly property bool urgent: missionsPage.isUrgent(modelData.expiry)
                                readonly property string kind: missionsPage.missionKind(modelData)
                                readonly property color kindColor: missionsPage.missionKindColor(kind)
                                Layout.fillWidth: true
                                implicitHeight: missionColumn.implicitHeight + 22
                                radius: 10
                                clip: true
                                color: panelRaised
                                border.width: urgent ? 1.6 : 1
                                border.color: urgent ? danger : borderTone

                                Rectangle {
                                    visible: missionRow.urgent
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    height: 46
                                    gradient: Gradient {
                                        GradientStop { position: 0.0; color: missionsPage.dangerBackground }
                                        GradientStop { position: 1.0; color: "transparent" }
                                    }
                                }

                                ColumnLayout {
                                    id: missionColumn
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: 12
                                    spacing: 8

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 10
                                        Rectangle {
                                            Layout.preferredWidth: 32
                                            Layout.preferredHeight: 32
                                            radius: 8
                                            color: missionsPage.missionKindBackground(missionRow.kind)
                                            Label {
                                                anchors.centerIn: parent
                                                text: missionsPage.missionKindIcon(missionRow.kind)
                                                color: missionRow.kindColor
                                                font.pixelSize: 15
                                                font.bold: true
                                            }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 1
                                            Label {
                                                Layout.fillWidth: true
                                                text: missionRow.modelData.name
                                                color: textPrimary
                                                font.pixelSize: 13
                                                font.bold: true
                                                elide: Text.ElideRight
                                            }
                                            Label {
                                                text: missionRow.modelData.faction
                                                color: muted
                                                font.pixelSize: 10
                                            }
                                        }
                                        ColumnLayout {
                                            spacing: 0
                                            Label {
                                                Layout.alignment: Qt.AlignRight
                                                text: appWindow.t("missions.time_left", "TIME LEFT")
                                                color: muted
                                                font.pixelSize: 8
                                                font.bold: true
                                            }
                                            Label {
                                                Layout.alignment: Qt.AlignRight
                                                text: appWindow.timeUntil(missionRow.modelData.expiry)
                                                color: missionRow.urgent ? danger : textSecondary
                                                font.family: monoFont
                                                font.pixelSize: 13
                                                font.bold: true
                                            }
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        Layout.leftMargin: 42
                                        text: missionsPage.missionDetailLine(missionRow.modelData)
                                        color: textSecondary
                                        font.pixelSize: 11
                                        elide: Text.ElideRight
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Layout.leftMargin: 42
                                        visible: missionRow.modelData.progressKnown === true
                                        spacing: 8
                                        Rectangle {
                                            Layout.fillWidth: true
                                            height: 6; radius: 3; color: inputBackground
                                            Rectangle {
                                                height: parent.height; radius: parent.radius
                                                color: missionRow.modelData.progressDone >= missionRow.modelData.progressTotal
                                                       ? green : cyan
                                                width: parent.width * Math.max(0, Math.min(1,
                                                    missionRow.modelData.progressTotal > 0
                                                    ? missionRow.modelData.progressDone / missionRow.modelData.progressTotal : 0))
                                                Behavior on width { NumberAnimation { duration: 150 } }
                                            }
                                        }
                                        Label {
                                            text: missionRow.modelData.progressDone + " / " + missionRow.modelData.progressTotal
                                            color: muted
                                            font.family: monoFont
                                            font.pixelSize: 10
                                            font.bold: true
                                        }
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Layout.leftMargin: 42
                                        spacing: 6
                                        Label {
                                            text: missionsPage.missionRewardText(missionRow.modelData)
                                            color: orange
                                            font.family: monoFont
                                            font.pixelSize: 14
                                            font.bold: true
                                        }
                                        Label {
                                            visible: missionRow.modelData.reward !== null || missionRow.modelData.donation !== null
                                            text: appWindow.t("missions.currency_cr", "CR")
                                            color: muted
                                            font.pixelSize: 9
                                            font.bold: true
                                        }
                                        Item { Layout.fillWidth: true }
                                        Rectangle {
                                            radius: 5
                                            color: missionsPage.missionKindBackground(missionRow.kind)
                                            implicitWidth: kindTag.implicitWidth + 14
                                            implicitHeight: 18
                                            Label {
                                                id: kindTag
                                                anchors.centerIn: parent
                                                text: missionsPage.missionKindLabel(missionRow.kind)
                                                color: missionRow.kindColor
                                                font.pixelSize: 8
                                                font.bold: true
                                            }
                                        }
                                        Rectangle {
                                            visible: missionRow.modelData.wing === true
                                            radius: 5
                                            color: successBackground
                                            implicitWidth: wingTag.implicitWidth + 14
                                            implicitHeight: 18
                                            Label {
                                                id: wingTag
                                                anchors.centerIn: parent
                                                text: appWindow.t("missions.wing", "WING")
                                                color: green
                                                font.pixelSize: 8
                                                font.bold: true
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        ShadowCard {
            Layout.fillWidth: true
            Layout.preferredWidth: 2
            Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: appWindow.t("missions.community_goals", "COMMUNITY GOALS")
                        color: cyan; font.pixelSize: 11; font.bold: true
                    }
                    Label {
                        text: appWindow.tf("missions.joined_count", "%1 JOINED", [goals.length])
                        color: muted; font.family: monoFont; font.pixelSize: 10
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }

                EmptyState {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: goals.length === 0
                    symbol: "◈"
                    title: appWindow.t("missions.no_cgs", "NO OPEN COMMUNITY GOALS")
                    detail: appWindow.t("missions.no_cgs_help", "Contribute cargo, combat or exploration data toward a Community Goal and it appears here for as long as it stays open.")
                    tone: cyan
                }

                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: goals.length > 0
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    ColumnLayout {
                        width: parent.width
                        spacing: 8
                        Repeater {
                            model: goals
                            delegate: Rectangle {
                                id: cgRow
                                required property var modelData
                                readonly property int tierTotal: Math.max(
                                    missionsPage.tierNumber(modelData.topTierName),
                                    missionsPage.tierNumber(modelData.tierReached), 1)
                                readonly property int tierDone: missionsPage.tierNumber(modelData.tierReached)
                                Layout.fillWidth: true
                                implicitHeight: cgColumn.implicitHeight + 22
                                radius: 10
                                color: panelRaised
                                border.width: 1
                                border.color: borderTone

                                ColumnLayout {
                                    id: cgColumn
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: 12
                                    spacing: 7

                                    Label {
                                        Layout.fillWidth: true
                                        text: cgRow.modelData.title
                                        color: textPrimary
                                        font.pixelSize: 13
                                        font.bold: true
                                        wrapMode: Text.WordWrap
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: cgRow.modelData.system
                                              + (cgRow.modelData.expiry ? " · " + appWindow.t("missions.closes", "closes") + " " + appWindow.timeUntil(cgRow.modelData.expiry) : "")
                                        color: muted
                                        font.pixelSize: 10
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 3
                                        visible: cgRow.tierTotal > 1
                                        Repeater {
                                            model: cgRow.tierTotal
                                            delegate: Rectangle {
                                                required property int index
                                                Layout.fillWidth: true
                                                Layout.preferredHeight: 5
                                                radius: 3
                                                color: index < cgRow.tierDone ? cyan : borderTone
                                            }
                                        }
                                    }
                                    Label {
                                        visible: cgRow.tierTotal > 1
                                        text: cgRow.modelData.tierReached
                                              + (cgRow.modelData.topTierName ? " / " + cgRow.modelData.topTierName : "")
                                        color: cyan
                                        font.family: monoFont
                                        font.pixelSize: 10
                                        font.bold: true
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            text: appWindow.t("missions.your_contribution", "YOUR CONTRIBUTION")
                                            color: muted
                                            font.pixelSize: 9
                                            font.bold: true
                                        }
                                        Item { Layout.fillWidth: true }
                                        Label {
                                            text: Number(cgRow.modelData.playerContribution).toLocaleString(Qt.locale(), "f", 0)
                                            color: textSecondary
                                            font.family: monoFont
                                            font.pixelSize: 12
                                            font.bold: true
                                        }
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        visible: cgRow.modelData.percentileBandKnown === true
                                        height: 7; radius: 4; color: inputBackground
                                        Rectangle {
                                            height: parent.height; radius: parent.radius
                                            color: cyan
                                            width: parent.width * Math.max(0, Math.min(1,
                                                (100 - cgRow.modelData.percentileBand) / 100))
                                        }
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        visible: cgRow.modelData.percentileBandKnown === true
                                        Item { Layout.fillWidth: true }
                                        Rectangle {
                                            radius: 5
                                            color: successBackground
                                            implicitWidth: bandLabel.implicitWidth + 14
                                            implicitHeight: 18
                                            Label {
                                                id: bandLabel
                                                anchors.centerIn: parent
                                                text: appWindow.tf("missions.percentile_band", "TOP %1%", [cgRow.modelData.percentileBand])
                                                color: green
                                                font.pixelSize: 8
                                                font.bold: true
                                            }
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: appWindow.tf("missions.contributors", "%1 contributors · %2 total contribution",
                                              [Number(cgRow.modelData.numContributors).toLocaleString(Qt.locale(), "f", 0),
                                               Number(cgRow.modelData.currentTotal).toLocaleString(Qt.locale(), "f", 0)])
                                        color: muted
                                        font.pixelSize: 9
                                        wrapMode: Text.WordWrap
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
