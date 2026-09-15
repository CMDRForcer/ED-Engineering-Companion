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
    readonly property color accentColor: appWindow.accent
    readonly property color textPrimary: appWindow.textPrimary
    readonly property color textSecondary: appWindow.textSecondary
    readonly property color muted: appWindow.muted
    readonly property color panelRaised: appWindow.panelRaised
    readonly property color borderTone: appWindow.borderTone

    property bool showAllSystems: false
    property bool showMissingGenera: false

    readonly property var summary: cockpit.exobiologySummary || ({})
    readonly property var sessionSummary: cockpit.exobiologySessionSummary || ({})
    readonly property var carriedSummary: cockpit.exobiologyCarriedSummary || ({})
    readonly property var bestFind: cockpit.exobiologyBestFind || ({})
    readonly property var remainingOnBody: cockpit.exobiologyRemainingOnBody || ({})
    readonly property var genusCompletion: cockpit.exobiologyGenusCompletion || ({})
    readonly property var missingGenusNames: exobiologyPage.genusCompletion.missingGenusNames || []
    readonly property var landingTargets: cockpit.exobiologyLandingTargets || []
    readonly property var currentSystemTargets: exobiologyPage.landingTargets.filter(function(target) {
        return target.inCurrentSystem
    })
    readonly property var otherSystemsCount: exobiologyPage.landingTargets.length - exobiologyPage.currentSystemTargets.length
    readonly property var displayedTargets: exobiologyPage.showAllSystems
                                             ? exobiologyPage.landingTargets
                                             : exobiologyPage.currentSystemTargets
    readonly property var distanceCheck: cockpit.exobiologyDistanceCheck || ({})

    function formatCr(value) {
        return Number(value || 0).toLocaleString(Qt.locale(), "f", 0) + " CR"
    }
    function stepLabel(step) {
        return step === "Analyse"
               ? appWindow.t("exobiology.step_analyse", "ANALYSE")
               : appWindow.t("exobiology.step_sample", "SAMPLE")
    }
    // The Survey Target card has a fixed height. A body only ever holds as
    // many organisms as it has detected biological signals - 8 covers every
    // body seen in practice (matches the cap already used on the plain
    // "candidates" list) with room to spare below the checklist, so this
    // cap is a safety net for a rare, unusually crowded body rather than
    // something normally reached. genusSignals is already sorted open-first,
    // so a straight cap keeps every still-missing organism visible and only
    // trims the already-found tail.
    function cardChecklist(target) {
        var signals = target.genusSignals || []
        if (signals.length > 0) {
            var shownSignals = signals.slice(0, 8)
            return { items: shownSignals, extra: signals.length - shownSignals.length }
        }
        var candidates = target.candidates || []
        var shownCandidates = candidates.slice(0, 3)
        return { items: shownCandidates, extra: 0 }
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

    ShadowCard {
        Layout.fillWidth: true
        Layout.preferredHeight: 68
        accent: exobiologyPage.distanceCheck.ready ? green : orange
        visible: Object.keys(exobiologyPage.distanceCheck).length > 0
        RowLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 4
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                Label {
                    text: appWindow.tf(
                        "exobiology.distance_check_title", "DISTANCE TO NEXT SAMPLE · %1",
                        [exobiologyPage.distanceCheck.displayName || ""])
                    color: muted; font.pixelSize: 10; font.bold: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Label {
                    text: exobiologyPage.distanceCheck.ready
                          ? appWindow.tf(
                                "exobiology.distance_check_ready", "READY TO %1 · %2 M / %3 M",
                                [exobiologyPage.stepLabel(exobiologyPage.distanceCheck.nextStep),
                                 exobiologyPage.distanceCheck.distanceM,
                                 exobiologyPage.distanceCheck.requiredM])
                          : appWindow.tf(
                                "exobiology.distance_check_not_ready", "KEEP MOVING BEFORE THE NEXT %1 · %2 M / %3 M",
                                [exobiologyPage.stepLabel(exobiologyPage.distanceCheck.nextStep),
                                 exobiologyPage.distanceCheck.distanceM,
                                 exobiologyPage.distanceCheck.requiredM])
                    color: exobiologyPage.distanceCheck.ready ? green : orange
                    font.pixelSize: 15; font.bold: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 12
        Repeater {
            model: [
                {"label": appWindow.t("exobiology.species_found", "SPECIES FOUND"), "value": String(exobiologyPage.summary.totalSpecies || 0), "detail": "", "tone": cyan},
                {"label": appWindow.t("exobiology.lifetime_earned", "LIFETIME EARNED"), "value": exobiologyPage.formatCr(cockpit.exobiologyLifetimeEarned),
                 "detail": Object.keys(exobiologyPage.bestFind).length > 0
                           ? appWindow.tf("exobiology.best_find_detail", "BEST · %1 · %2", [exobiologyPage.bestFind.displayName, exobiologyPage.formatCr(exobiologyPage.bestFind.value)])
                           : "", "tone": green},
                {"label": appWindow.t("exobiology.remaining_on_body", "REMAINING ON THIS PLANET"),
                 "value": Object.keys(exobiologyPage.remainingOnBody).length > 0 ? String(exobiologyPage.remainingOnBody.remaining) : "—",
                 "detail": "",
                 "tone": Object.keys(exobiologyPage.remainingOnBody).length === 0
                         ? muted
                         : (exobiologyPage.remainingOnBody.remaining === 0 ? green : orange)},
                {"label": appWindow.t("exobiology.this_session", "THIS SESSION"), "value": appWindow.tf(
                     "exobiology.session_value", "%1 · %2",
                     [exobiologyPage.sessionSummary.speciesCount || 0, exobiologyPage.formatCr(exobiologyPage.sessionSummary.totalValue)]), "detail": "", "tone": green},
                {"label": appWindow.t("exobiology.carried_unsold", "CARRIED · UNSOLD"), "value": appWindow.tf(
                     "exobiology.carried_value", "%1 · %2",
                     [exobiologyPage.carriedSummary.speciesCount || 0, exobiologyPage.formatCr(exobiologyPage.carriedSummary.totalValue)]), "detail": "", "tone": orange}
            ]
            delegate: ShadowCard {
                required property var modelData
                required property int index
                objectName: index === 0 ? "qa-card-exobiology" : ""
                Layout.fillWidth: true
                Layout.preferredHeight: 92
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 15
                    spacing: 2
                    Label { text: modelData.label; color: muted; font.pixelSize: 10; font.bold: true }
                    Label {
                        text: modelData.value; color: modelData.tone
                        font.pixelSize: 18; font.bold: true
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    Label {
                        visible: modelData.detail.length > 0
                        text: modelData.detail
                        color: muted; font.pixelSize: 9
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }
            }
        }
    }

    ShadowCard {
        Layout.fillWidth: true
        Layout.preferredHeight: genusCompletionContent.implicitHeight + 28
        accent: cyan
        visible: (exobiologyPage.genusCompletion.totalGenera || 0) > 0
        ColumnLayout {
            id: genusCompletionContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 14
            spacing: 6
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    text: appWindow.t("exobiology.genus_progress", "GENUS PROGRESS")
                    color: cyan; font.pixelSize: 11; font.bold: true
                }
                Item { Layout.fillWidth: true }
                Label {
                    text: appWindow.tf(
                        "exobiology.genus_progress_count", "%1 / %2 FOUND",
                        [exobiologyPage.genusCompletion.foundGenera || 0, exobiologyPage.genusCompletion.totalGenera || 0])
                    color: exobiologyPage.missingGenusNames.length === 0 ? green : textPrimary
                    font.pixelSize: 13; font.bold: true
                }
                Label {
                    visible: exobiologyPage.missingGenusNames.length > 0
                    text: exobiologyPage.showMissingGenera
                          ? appWindow.t("exobiology.hide_missing_genera", "HIDE")
                          : appWindow.t("exobiology.show_missing_genera", "SHOW MISSING")
                    color: cyan; font.pixelSize: 11; font.bold: true
                    MouseArea {
                        anchors.fill: parent
                        anchors.margins: -4
                        cursorShape: Qt.PointingHandCursor
                        onClicked: exobiologyPage.showMissingGenera = !exobiologyPage.showMissingGenera
                    }
                }
            }
            Label {
                visible: exobiologyPage.showMissingGenera && exobiologyPage.missingGenusNames.length > 0
                Layout.fillWidth: true
                text: exobiologyPage.missingGenusNames.join(" · ")
                color: muted; font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            Label {
                visible: exobiologyPage.showMissingGenera && exobiologyPage.missingGenusNames.length === 0
                text: appWindow.t("exobiology.genus_all_found", "ALL KNOWN GENERA FOUND")
                color: green; font.pixelSize: 12; font.bold: true
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
                    readonly property var checklist: exobiologyPage.cardChecklist(modelData)
                    width: 300
                    height: targetList.height
                    radius: 13
                    color: panelRaised
                    border.width: modelData.inCurrentSystem ? 2 : 1
                    border.color: modelData.inCurrentSystem ? orange : borderTone
                    // Layouts do not shrink a Text-based item below its own
                    // unelided width unless Layout.minimumWidth is capped
                    // explicitly - without it, elide never actually
                    // triggers and long text spills past this card's edge
                    // into the next one. clip guards the same edge as a
                    // last resort for anything that still overflows.
                    clip: true
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Label {
                                text: modelData.bodyName
                                color: textPrimary; font.pixelSize: 16; font.bold: true
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                Layout.alignment: Qt.AlignTop
                                wrapMode: Text.WordWrap
                                maximumLineCount: 2
                                elide: Text.ElideRight
                            }
                            StatusBadge {
                                Layout.alignment: Qt.AlignTop
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
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            elide: Text.ElideRight
                        }
                        Label {
                            text: appWindow.tf(
                                "exobiology.signal_count", "%1 BIOLOGICAL SIGNAL(S)",
                                [modelData.signalCount])
                            color: cyan; font.pixelSize: 13; font.bold: true
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            elide: Text.ElideRight
                        }
                        StatusBadge {
                            visible: modelData.firstFootfallPossible
                            statusText: appWindow.t("exobiology.footfall_possible", "FOOTFALL BONUS POSSIBLE")
                            tone: accentColor
                            compact: true
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                        Repeater {
                            // Once a DSS has confirmed the genus list, show
                            // every detected organism as a checklist - found
                            // ones ticked off and dimmed, so progress on this
                            // body is visible at a glance, not just what is
                            // still missing. Capped by cardChecklist() to the
                            // same row budget the old plain candidate list
                            // used, so a body with many confirmed genera
                            // can't blow out this card's fixed height.
                            model: checklist.items
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 6
                                Label {
                                    visible: typeof modelData.found !== "undefined"
                                    text: modelData.found ? "✓" : "○"
                                    color: modelData.found ? green : muted
                                    font.pixelSize: 13; font.bold: true
                                }
                                Label {
                                    Layout.fillWidth: true; Layout.minimumWidth: 0
                                    text: modelData.name + (modelData.found ? "" : " · " + exobiologyPage.formatCr(modelData.value))
                                    color: modelData.found ? muted : textSecondary
                                    font.pixelSize: 13
                                    font.strikeout: !!modelData.found
                                    elide: Text.ElideRight
                                }
                            }
                        }
                        Label {
                            visible: checklist.extra > 0
                            Layout.fillWidth: true
                            text: appWindow.tf("exobiology.checklist_more", "+%1 MORE", [checklist.extra])
                            color: muted; font.pixelSize: 11
                        }
                        Item { Layout.fillHeight: true }
                        Label {
                            Layout.fillWidth: true
                            text: modelData.firstFootfallPossible
                                  ? appWindow.tf(
                                        "exobiology.best_value_base", "BASE UP TO %1",
                                        [exobiologyPage.formatCr(modelData.bestValue)])
                                  : appWindow.tf(
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
