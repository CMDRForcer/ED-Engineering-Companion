import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "qml/components"
import "qml/pages"

ApplicationWindow {
    id: window
    width: 1480
    height: 900
    minimumWidth: 1120
    minimumHeight: 700
    visible: true
    title: "ED Engineering Companion (EDEC) · " + cockpit.appVersion
    property int previewEngineersMode: 0

    // The single app-wide source of color truth. Every palette defines the
    // same semantic roles so a theme switch updates every binding at once.
    readonly property var themeSets: ({
        "arctic_alloy": {
            backgroundPrimary: "#171f22", backgroundSecondary: "#222c30",
            backgroundTertiary: "#303c40", card: "#3d484b", cardRaised: "#4b5658",
            inputBackground: "#2b3538", textPrimary: "#f4faf9",
            textSecondary: "#d8e4e2", textMuted: "#aab8b8", textDisabled: "#748891",
            accent: "#c1d1cf", accentSecondary: "#748891",
            success: "#9fd6c2", warning: "#e5bd7d", error: "#ee7d86",
            border: "#666b64", divider: "#536064", hover: "#566164",
            active: "#636467", successBackground: "#304b43",
            warningBackground: "#51442f", errorBackground: "#52363b",
            overlay: "#ba171f22", shadow: "#80000000"
        },
        "navy": {
            backgroundPrimary: "#07111d", backgroundSecondary: "#071827",
            backgroundTertiary: "#101a2b", card: "#102033", cardRaised: "#172b42",
            inputBackground: "#0d1b2b", textPrimary: "#f4f8ff",
            textSecondary: "#d5e3f2", textMuted: "#8fa4ba", textDisabled: "#587086",
            accent: "#3bdcff", accentSecondary: "#769cc2",
            success: "#4de2ac", warning: "#ffb65c", error: "#ff6c82",
            border: "#294560", divider: "#233c55", hover: "#1c3650",
            active: "#214563", successBackground: "#15392f",
            warningBackground: "#3a2c1b", errorBackground: "#3a202a",
            overlay: "#b0050b14", shadow: "#70000000"
        },
        "neon_vector": {
            backgroundPrimary: "#09061a", backgroundSecondary: "#110a27",
            backgroundTertiary: "#1b1035", card: "#201440", cardRaised: "#2b1952",
            inputBackground: "#170e31", textPrimary: "#fbf7ff",
            textSecondary: "#ded8f0", textMuted: "#9b91bb", textDisabled: "#61577c",
            accent: "#00e8f0", accentSecondary: "#7b35ff",
            success: "#00f59b", warning: "#ffbf38", error: "#ff267f",
            border: "#473078", divider: "#392563", hover: "#34205f",
            active: "#4d1e8a", successBackground: "#103d35",
            warningBackground: "#49351b", errorBackground: "#4b1738",
            overlay: "#c509061a", shadow: "#90000000"
        },
        "orbital_dawn": {
            backgroundPrimary: "#171219", backgroundSecondary: "#1d111b",
            backgroundTertiary: "#292238", card: "#302a3a", cardRaised: "#40394a",
            inputBackground: "#241d2c", textPrimary: "#f3f1e6",
            textSecondary: "#d9d8cf", textMuted: "#9d97a4", textDisabled: "#665f6d",
            accent: "#5c9c9c", accentSecondary: "#315d72",
            success: "#a5d1c7", warning: "#e8b36f", error: "#d94c62",
            border: "#554e5f", divider: "#443d4c", hover: "#493e50",
            active: "#315d72", successBackground: "#294845",
            warningBackground: "#4a3829", errorBackground: "#512634",
            overlay: "#c0171219", shadow: "#88000000"
        },
        "crimson_dark": {
            backgroundPrimary: "#07080a", backgroundSecondary: "#0d0f12",
            backgroundTertiary: "#13161a", card: "#171a1f", cardRaised: "#20242a",
            inputBackground: "#111419", textPrimary: "#f5f6f8",
            textSecondary: "#d3d6dc", textMuted: "#9298a2", textDisabled: "#5c626c",
            accent: "#e23b4d", accentSecondary: "#a94a55",
            success: "#60d394", warning: "#e9a84d", error: "#ff6372",
            border: "#45343a", divider: "#332b30", hover: "#302126",
            active: "#48252d", successBackground: "#193329",
            warningBackground: "#3a2b1b", errorBackground: "#421f27",
            overlay: "#c8000000", shadow: "#85000000"
        },
        "crimson_light": {
            backgroundPrimary: "#eceff2", backgroundSecondary: "#e1e5e9",
            backgroundTertiary: "#d5dbe0", card: "#f8f9fb", cardRaised: "#ffffff",
            inputBackground: "#eef1f4", textPrimary: "#17191d",
            textSecondary: "#343941", textMuted: "#68707b", textDisabled: "#9aa1aa",
            accent: "#c6283d", accentSecondary: "#8f4650",
            success: "#267a50", warning: "#a86612", error: "#b91f35",
            border: "#b8bec6", divider: "#c8cdd3", hover: "#f1dce0",
            active: "#e7c1c8", successBackground: "#d9eee3",
            warningBackground: "#f5e7cf", errorBackground: "#f4d7dc",
            overlay: "#99000000", shadow: "#30000000"
        }
    })
    readonly property var themeColors: themeSets[cockpit.theme] || themeSets.navy
    property color backgroundPrimary: themeColors.backgroundPrimary
    property color backgroundSecondary: themeColors.backgroundSecondary
    property color backgroundTertiary: themeColors.backgroundTertiary
    property color card: themeColors.card
    property color cardRaised: themeColors.cardRaised
    property color inputBackground: themeColors.inputBackground
    property color textPrimary: themeColors.textPrimary
    property color textSecondary: themeColors.textSecondary
    property color textMuted: themeColors.textMuted
    property color textDisabled: themeColors.textDisabled
    property color accent: themeColors.accent
    property color accentSecondary: themeColors.accentSecondary
    property color success: themeColors.success
    property color warning: themeColors.warning
    property color error: themeColors.error
    property color borderTone: themeColors.border
    property color divider: themeColors.divider
    property color hover: themeColors.hover
    property color active: themeColors.active
    property color successBackground: themeColors.successBackground
    property color warningBackground: themeColors.warningBackground
    property color errorBackground: themeColors.errorBackground
    property color overlay: themeColors.overlay
    property color shadow: themeColors.shadow
    color: backgroundPrimary

    // Compatibility aliases keep existing component APIs semantic while all
    // values now come from the active palette.
    property color cyan: accent
    property color green: success
    property color orange: warning
    property color panel: card
    property color panelRaised: cardRaised
    property color muted: textMuted
    palette.window: backgroundPrimary
    palette.windowText: textPrimary
    palette.base: inputBackground
    palette.alternateBase: cardRaised
    palette.text: textPrimary
    palette.button: cardRaised
    palette.buttonText: textPrimary
    palette.highlight: accent
    palette.highlightedText: backgroundPrimary
    palette.placeholderText: textMuted
    palette.mid: borderTone
    palette.dark: divider
    property bool reducedMotion: cockpit ? cockpit.reducedMotion : true
    property bool enhancedVisuals: cockpit ? cockpit.enhancedVisuals : false
    property int currentPage: cockpit.lastPage
    property var globalResults: []
    property int connectionPreviewMode: 0
    // Cross-page navigation requests live at window scope because inactive
    // pages are unloaded to avoid evaluating their models in the background.
    property bool materialFarmMissingRequested: false
    property var guardianPageRequest: ({})
    // Preserve transient page controls while Loader releases an inactive page.
    property bool materialsNeededOnlyState: false
    property bool materialsFarmMissingState: false
    property string materialsStatusFilterState: "all"
    property string materialsSearchState: ""
    property string engineeringCategoryState: "Core Internals"
    property string engineeringModuleState: "Frame Shift Drive"
    property string engineeringSearchState: ""
    property string engineersSearchState: ""
    property string engineersStatusState: "ALL"
    property string engineersBrokerState: "ALL"
    property bool engineersUnlockState: previewEngineersMode === 1
    property bool engineersGuardianState: false
    property string selectedEngineerState: ""
    property string selectedGuardianState: ""
    property string hgeMaterialState: "ALL HGE MATERIALS"
    property string hgeFindTypeState: "ALL FIND TYPES"
    property string hgeStatusState: "ALL STATES"
    property string hgeAllegianceState: "ALL ALLEGIANCES"
    property string hgeEvidenceState: "ALL EVIDENCE"
    property bool hgeAdvancedState: false
    property int hgeNearbyRadiusState: 0
    property int hgeVisibleLimitState: 250
    property bool settingsAdvancedState: false
    function requestMaterialFarmMissing(openPage) {
        materialFarmMissingRequested = true
        if (openPage)
            currentPage = 2
    }
    function requestGuardianPage(row) {
        guardianPageRequest = {
            "brokerFilter": row.brokerSubtype === "GUARDIAN" ? "GUARDIAN" : "HUMAN",
            "selectedGuardianName": row.id || row.name || ""
        }
        currentPage = 4
    }
    property bool compactSidebar: width < 1300
    property bool narrowWorkspace: (width / Math.max(1.0, cockpit.uiScale)) < 1350
    property var liveHgeTargets: cockpit.hgeTargets
    property double sessionClock: Date.now()
    readonly property color powerplayUnoccupied: "#74777c"
    readonly property color powerplayExploited: "#d3424b"
    readonly property color powerplayFortified: "#35b96b"
    readonly property color powerplayStronghold: "#8a63df"
    readonly property color powerplayUndermining: "#d3424b"
    readonly property color powerplayReinforcement: "#347bd4"
    function t(key, fallback) {
        // Reading the property keeps every binding reactive when the language
        // changes, while the controller supplies the English/source fallback.
        var activeLanguage = cockpit.interfaceLanguage
        return cockpit.translate(key, fallback)
    }
    function tf(key, fallback, values) {
        var result = t(key, fallback)
        for (var index = 0; index < values.length; ++index)
            result = result.replace("%" + (index + 1), String(values[index]))
        return result
    }
    function countLabel(value, singular, plural) {
        var count = Math.max(0, Number(value) || 0)
        return count + " " + (count === 1 ? singular : plural)
    }
    function localizedStatus(value) {
        var source = String(value === undefined || value === null ? "" : value)
        var key = source.toUpperCase().replace(/[^A-Z0-9]+/g, "_")
        var known = {
            "ACTIVE": "status.value.active", "COMPLETE": "status.value.complete",
            "IN_PROGRESS": "status.value.in_progress", "INVITED": "status.value.invited",
            "KNOWN": "status.value.known", "LOCKED": "status.value.locked",
            "MISSING": "status.value.missing", "NONE": "status.value.none",
            "NOT_NEEDED": "status.value.not_needed", "NOT_STARTED": "status.value.not_started",
            "PENDING": "status.value.pending", "READY": "status.value.ready",
            "STOPPED": "status.value.stopped", "SURPLUS": "status.value.surplus",
            "TRADEABLE": "status.value.tradeable", "UNLOCKED": "status.value.unlocked",
            "UNRELIABLE": "status.value.unreliable", "UNKNOWN": "status.value.unknown",
            "UNOCCUPIED": "powerplay.state.unoccupied", "EXPLOITED": "powerplay.state.exploited",
            "FORTIFIED": "powerplay.state.fortified", "STRONGHOLD": "powerplay.state.stronghold",
            "HOMESYSTEM": "powerplay.state.home_system",
            "LIVE": "status.value.live", "RECENT": "status.value.recent",
            "STALE": "status.value.stale", "EXACT_MATCH": "status.value.exact_match",
            "FAMILY_MATCH": "status.value.family_match", "UNRESOLVED": "status.unresolved",
            "EDDN_LIVE": "status.value.eddn_live", "EDDN_SENT": "status.value.eddn_sent",
            "EDDN_FAILED": "status.value.eddn_failed", "EDDN_QUEUED": "status.value.eddn_queued",
            "EDDN_RETRY": "status.value.eddn_retry", "EDDN_SENDING": "status.value.eddn_sending",
            "PARTIAL": "import.partial", "WARNING": "status.value.warning",
            "SENT": "status.value.sent", "FAILED": "status.value.failed",
            "QUEUED": "status.value.queued", "SENDING": "status.sending"
        }
        return known[key] ? t(known[key], source) : source
    }
    function materialDisplay(status, value, reliable) {
        var trusted = reliable === true
        return {
            "status": localizedStatus(trusted ? status : "UNRELIABLE"),
            "completion": trusted && status === "READY" ? 1.0 : value
        }
    }
    function formatSessionDuration(seconds) {
        var value = Math.max(0, Math.floor(Number(seconds) || 0))
        var hours = Math.floor(value / 3600)
        var minutes = Math.floor((value % 3600) / 60)
        return hours + "h " + String(minutes).padStart(2, "0") + "m"
    }
    function currentSessionSeconds(session) {
        if (!session || !session.start)
            return 0
        if (!session.active)
            return session.durationSeconds || 0
        var started = Date.parse(session.start)
        return isNaN(started)
               ? (session.durationSeconds || 0)
               : Math.max(session.durationSeconds || 0,
                          Math.floor((sessionClock - started) / 1000))
    }
    function relativeJournalTime(timestamp) {
        var parsed = Date.parse(timestamp || "")
        if (isNaN(parsed))
            return ""
        var seconds = Math.max(0, Math.floor((sessionClock - parsed) / 1000))
        if (seconds < 60)
            return t("logbook.just_now", "just now")
        if (seconds < 3600)
            return tf("logbook.minutes_ago", "%1 min ago", [Math.floor(seconds / 60)])
        if (seconds < 86400)
            return tf("logbook.hours_ago", "%1 h ago", [Math.floor(seconds / 3600)])
        var days = Math.floor(seconds / 86400)
        return tf(days === 1 ? "logbook.day_ago" : "logbook.days_ago",
                  days === 1 ? "%1 day ago" : "%1 days ago", [days])
    }
    function powerplayPledgedHours(overview) {
        if (!overview || !overview.timePledgedKnown)
            return 0
        var seconds = Math.max(0, Number(overview.timePledgedSeconds || 0))
        var observedAt = Date.parse(overview.timePledgedObservedAt || "")
        if (!isNaN(observedAt))
            seconds += Math.max(0, Math.floor((sessionClock - observedAt) / 1000))
        return Math.floor(seconds / 3600)
    }
    function powerplayStateColor(state) {
        var key = String(state || "").toLowerCase()
        if (key === "exploited") return powerplayExploited
        if (key === "fortified") return powerplayFortified
        if (key === "stronghold" || key === "homesystem") return powerplayStronghold
        return powerplayUnoccupied
    }
    function powerplayLeaderPortrait(powerName) {
        var key = String(powerName || "").toLowerCase().replace(/[^a-z0-9]/g, "")
        var portraits = {
            "aislingduval": "assets/powerplay/aisling_duval.png",
            "arissalavignyduval": "assets/powerplay/arissa_lavigny_duval.png",
            "alavignyduval": "assets/powerplay/arissa_lavigny_duval.png",
            "dentonpatreus": "assets/powerplay/denton_patreus.png",
            "zeminatorval": "assets/powerplay/zemina_torval.png",
            "feliciawinters": "assets/powerplay/felicia_winters.png",
            "jeromearcher": "assets/powerplay/jerome_archer.png",
            "edmundmahon": "assets/powerplay/edmund_mahon.png",
            "nakatokaine": "assets/powerplay/nakato_kaine.png",
            "pranavantal": "assets/powerplay/pranav_antal.png",
            "archondelaine": "assets/powerplay/archon_delaine.png",
            "yurigrom": "assets/powerplay/yuri_grom.png",
            "liyongrui": "assets/powerplay/li_yong_rui.png"
        }
        return portraits[key] ? Qt.resolvedUrl(portraits[key]) : ""
    }
    function powerplayLeaderBiography(powerName) {
        var key = String(powerName || "").toLowerCase().replace(/[^a-z0-9]/g, "")
        if (key === "alavignyduval")
            key = "arissalavignyduval"
        var biographies = {
            "aislingduval": "Known as the People's Princess, Aisling Duval combines Imperial celebrity with a reformist political programme. The granddaughter of former Emperor Hengist Duval, she has built broad popular support despite being born outside marriage. From her headquarters in Cubeo, she campaigns against Imperial slavery and narcotics while promoting social welfare and a more modern Empire. She also represents the Empire within the anti-xeno organisation Aegis. Her signature module is the Prismatic Shield Generator, prioritised early in her Powerplay 2.0 progression rather than exclusive to her supporters.",
            "arissalavignyduval": "Arissa Lavigny-Duval is the reigning Emperor and the central figure of traditional Imperial authority. The previously unacknowledged daughter of Hengist Duval rose through the succession crisis by presenting herself as the defender of stability, justice and the rule of law. Her power is based in Kamadhenu, and her supporters expose corruption, reinforce Imperial garrisons and punish criminals. Her signature module is the Imperial Hammer railgun, prioritised early in her Powerplay 2.0 progression rather than exclusive to her supporters.",
            "alavignyduval": "Arissa Lavigny-Duval is the reigning Emperor and the central figure of traditional Imperial authority. The previously unacknowledged daughter of Hengist Duval rose through the succession crisis by presenting herself as the defender of stability, justice and the rule of law. Her power is based in Kamadhenu, and her supporters expose corruption, reinforce Imperial garrisons and punish criminals. Her signature module is the Imperial Hammer railgun, prioritised early in her Powerplay 2.0 progression rather than exclusive to her supporters.",
            "dentonpatreus": "Senator Denton Patreus is an Imperial financier, military commander and Admiral of the Fleet. He gained influence by financing governments and using debt, diplomacy and armed intervention to advance Imperial interests. Operating from Eotienses, Patreus favours decisive military action and has led major campaigns against threats such as Emperor's Dawn and Nova Imperium. His signature module is the Advanced Plasma Accelerator, prioritised early in his Powerplay 2.0 progression rather than exclusive to his supporters.",
            "zeminatorval": "Senator Zemina Torval is one of the Empire's most experienced and uncompromising political operators. A powerful industrialist with extensive mining interests, she represents the traditionalist wing of Imperial society and openly defends its hierarchical institutions. From Synteini, she expands her influence through commerce, patronage and resource control rather than public popularity. Her signature module is the Mining Lance, prioritised early in her Powerplay 2.0 progression rather than exclusive to her supporters.",
            "feliciawinters": "Felicia Winters is the liberal President of the Federation and a long-standing advocate of civil rights, social investment and accountable government. Her political network is centred on Rhea, where aid programmes and community development are used to build support for Federal democratic values. Winters has repeatedly opposed authoritarian surveillance and hard-line security policies while remaining committed to the Federation. Her signature module is the Pulse Disruptor, prioritised early in her Powerplay 2.0 progression rather than exclusive to her supporters.",
            "jeromearcher": "Jerome Archer is the Federation's security-focused Vice President and the leading figure of its Republican power bloc. Emerging as Zachary Hudson's political successor, he promotes military strength, domestic security and direct action against threats to Federal authority. His organisation operates from Nanomam and uses armed intervention to expand and defend its influence. His signature module is the Pacifier Frag-Cannon, prioritised early in his Powerplay 2.0 progression rather than exclusive to his supporters.",
            "edmundmahon": "Edmund Mahon is the Prime Minister of the Alliance and one of the galaxy's most influential economic strategists. Based in Gateway, he strengthens the Alliance through trade agreements, commercial networks and cooperation between independent member systems. His pragmatic leadership has delivered long periods of growth, although critics such as Nakato Kaine challenge his reliance on large external corporations. His signature module is the Retributor beam laser, prioritised early in his Powerplay 2.0 progression rather than exclusive to his supporters.",
            "nakatokaine": "Councillor Nakato Kaine is a prominent Alliance reformer and Edmund Mahon's principal political rival. Based in Tionisla, she argues that the Alliance should protect the sovereignty of its member systems, favour locally founded businesses and reduce dependence on the other superpowers and megacorporations. Her campaigns combine public advocacy with covert and economic pressure. Introduced as a power with Powerplay 2.0, she has no legacy signature module; her progression instead emphasises economic benefits, including bonuses for trade and mined commodities.",
            "pranavantal": "Simguru Pranav Antal leads Utopia, a transhumanist community devoted to scientific progress and the long-term improvement of humanity. From Polevnic, Utopia develops advanced simulation, medical and social technologies while attempting to remain above conventional superpower rivalries. Antal presents himself as a philosopher and visionary, but guards Utopia's discoveries carefully against exploitation. His signature module is the Enforcer Cannon, prioritised early in his Powerplay 2.0 progression rather than exclusive to his supporters.",
            "archondelaine": "Archon Delaine is the ruthless leader of the Kumo Crew and ruler of a criminal domain centred on Harma. He transformed a pirate organisation into a territorial power through violence, intimidation and control of black markets. Delaine demands recognition as a sovereign leader, while most governments still regard his Kumo Council as an organised crime syndicate. His signature module is the Cytoscrambler burst laser, prioritised early in his Powerplay 2.0 progression rather than exclusive to his supporters.",
            "yurigrom": "Yuri Grom is the authoritarian leader of the EG Pilots and ruler of an independent power centred on Clayakarma. A former military commander, he built his influence around discipline, nationalism and resistance to Federal expansion. His forces rely on direct military pressure and tightly controlled administration to secure their territory. His signature module is the Containment Missile launcher, prioritised early in his Powerplay 2.0 progression rather than exclusive to his supporters.",
            "liyongrui": "Li Yong-Rui is the chief executive of Sirius Corporation, one of humanity's most powerful technology and industrial conglomerates. From Lembava, he expands influence through investment, research partnerships, commercial incentives and access to Sirius technology. His pragmatic corporate diplomacy reaches across superpower borders, although critics accuse him of turning strategic dependencies into leverage. His signature module is the Pack-Hound Missile Rack, prioritised early in his Powerplay 2.0 progression rather than exclusive to his supporters."
        }
        return biographies[key]
               ? t("powerplay.leader." + key + ".biography", biographies[key])
               : ""
    }
    property var wishlistMaterialExpansion: ({})
    function wishlistExpansionKey(row, index) {
        return String(row.planId || (cockpit.ship + "|" + index))
    }
    function wishlistMaterialsExpanded(row, index) {
        var key = wishlistExpansionKey(row, index)
        if (Object.prototype.hasOwnProperty.call(wishlistMaterialExpansion, key))
            return wishlistMaterialExpansion[key]
        return cockpit.blueprints.length <= 1
    }
    function setWishlistMaterialsExpanded(row, index, expanded) {
        var updated = Object.assign({}, wishlistMaterialExpansion)
        updated[wishlistExpansionKey(row, index)] = expanded
        wishlistMaterialExpansion = updated
    }
    property var primaryNavigation: [
        {"id": "operations", "label": t("nav.operations", "OPERATIONS"), "icon": "⌂", "page": 0},
        {"id": "cmdr", "label": t("nav.commander", "CMDR"), "icon": "◆", "page": 10},
        {"id": "engineering", "label": t("nav.engineering", "ENGINEERING"), "icon": "⌁", "page": 3},
        {"id": "wishlist", "label": t("nav.wishlist", "WISHLIST"), "icon": "★", "page": 1},
        {"id": "materials", "label": t("nav.materials", "MATERIALS"), "icon": "◇", "page": 2},
        {"id": "engineers", "label": t("nav.engineers", "ENGINEERS"), "icon": "◎", "page": 4},
        {"id": "state-finds", "label": t("nav.state_finds", "STATE FINDS"), "icon": "⌖", "page": 8},
        {"id": "logbook", "label": t("nav.logbook", "LOGBOOK"), "icon": "≣", "page": 9},
        {"id": "settings", "label": t("nav.settings", "SETTINGS"), "icon": "≡", "page": 5},
        {"id": "powerplay", "label": t("nav.powerplay", "POWERPLAY"), "icon": "⚑", "page": 11}
    ]
    property var navigationOrder: cockpit.navigationOrder || []
    function orderedNavigation() {
        let byId = ({})
        primaryNavigation.forEach(function(item) { byId[item.id] = item })
        return navigationOrder.map(function(id) { return byId[id] }).filter(function(item) { return !!item })
    }
    function moveNavigation(fromIndex, toIndex) {
        if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex)
            return
        let order = navigationOrder.slice(0)
        let moved = order.splice(fromIndex, 1)[0]
        order.splice(toIndex, 0, moved)
        navigationOrder = order
        cockpit.setNavigationOrder(order)
    }
    property string feedbackMessage: ""
    property bool feedbackVisible: false
    onCurrentPageChanged: cockpit.setLastPage(currentPage)
    function applyInterfaceScale() {
        contentItem.scale = cockpit.uiScale
        contentItem.transformOrigin = Item.TopLeft
        contentItem.width = width / cockpit.uiScale
        contentItem.height = height / cockpit.uiScale
    }
    Component.onCompleted: {
        applyInterfaceScale()
        if (smokeInjectQmlError)
            console.warn("PHASE14 injected QML smoke-test failure")
        if (!cockpit.onboardingComplete)
            onboardingDialog.open()
    }
    onWidthChanged: applyInterfaceScale()
    onHeightChanged: applyInterfaceScale()
    Connections {
        target: cockpit
        function onUiChanged() {
            window.applyInterfaceScale()
            if (!cockpit.commanderUpdatePopups)
                window.feedbackVisible = false
            if (!cockpit.onboardingComplete)
                onboardingDialog.open()
        }
        function onActivityChanged() {
            window.showFeedback(cockpit.activity)
        }
    }
    function showFeedback(message) {
        if (!cockpit.commanderUpdatePopups || !message || message.length === 0)
            return
        feedbackMessage = message
        feedbackVisible = true
        feedbackTimer.restart()
    }
    property string materialTradeFilter: "AUTO"
    function automaticTradeCategory() {
        if ((cockpit.traderRoute || []).length > 0)
            return String(cockpit.traderRoute[0].category || "").toUpperCase()
        return ""
    }
    function visibleMaterialTrades(rows) {
        const automatic = automaticTradeCategory()
        const selected = materialTradeFilter === "AUTO"
                       ? automatic : materialTradeFilter
        const routeOrder = ({})
        ;(cockpit.traderRoute || []).forEach(function(stop, index) {
            routeOrder[String(stop.category || "").toUpperCase()] = index
        })
        const fallbackOrder = {"RAW": 0, "MANUFACTURED": 1, "ENCODED": 2}
        const filtered = (rows || []).filter(function(row) {
            return !selected
                   || String(row.category || "").toUpperCase() === selected
        })
        const sorted = filtered.map(function(row, index) {
            return {"row": row, "index": index}
        }).sort(function(left, right) {
            const leftCategory = String(left.row.category || "").toUpperCase()
            const rightCategory = String(right.row.category || "").toUpperCase()
            const leftRank = routeOrder[leftCategory] === undefined
                           ? (fallbackOrder[leftCategory] === undefined
                              ? 99 : 10 + fallbackOrder[leftCategory])
                           : routeOrder[leftCategory]
            const rightRank = routeOrder[rightCategory] === undefined
                            ? (fallbackOrder[rightCategory] === undefined
                               ? 99 : 10 + fallbackOrder[rightCategory])
                            : routeOrder[rightCategory]
            return leftRank === rightRank ? left.index - right.index
                                          : leftRank - rightRank
        }).map(function(entry) { return entry.row })
        const nextByCategory = ({})
        return sorted.map(function(row) {
            const category = String(row.category || "").toUpperCase()
            const isNext = row.status === "open" && !nextByCategory[category]
            if (isNext)
                nextByCategory[category] = true
            return Object.assign({}, row, {"nextInCategory": isNext})
        })
    }
    function activateGlobalResult(result) {
        if (!result)
            return
        window.currentPage = result.page
        if (result.kind === "MATERIAL")
            cockpit.selectMaterial(result.key)
        else if (result.kind === "BLUEPRINT")
            cockpit.selectBlueprint(result.key)
        globalSearchDialog.close()
    }
    Timer {
        id: feedbackTimer
        interval: 3800
        repeat: false
        onTriggered: window.feedbackVisible = false
    }
    Timer {
        interval: 15000
        repeat: true
        running: window.currentPage === 8
        triggeredOnStart: true
        onTriggered: cockpit.refreshHgeFinderLifetime()
    }
    Timer {
        interval: 1000
        repeat: true
        running: (window.currentPage === 9 && !!cockpit.currentSession.active)
                 || window.currentPage === 11
        triggeredOnStart: true
        onTriggered: window.sessionClock = Date.now()
    }
    Shortcut { sequence: "Ctrl+K"; onActivated: globalSearchDialog.open() }
    Shortcut { sequence: "F1"; onActivated: shortcutHelpDialog.open() }
    Shortcut { sequence: "Ctrl+R"; onActivated: cockpit.reloadJournalNow() }
    Shortcut { sequence: "Ctrl+Shift+J"; onActivated: cockpit.setJournalAuto(!cockpit.journalAuto) }
    Shortcut { sequence: "Alt+1"; onActivated: currentPage = 0 }
    Shortcut { sequence: "Alt+2"; onActivated: currentPage = 3 }
    Shortcut { sequence: "Alt+3"; onActivated: currentPage = 1 }
    Shortcut { sequence: "Alt+4"; onActivated: currentPage = 2 }
    Shortcut { sequence: "Alt+5"; onActivated: currentPage = 4 }
    Shortcut { sequence: "Alt+6"; onActivated: currentPage = 5 }
    Shortcut { sequence: "Alt+7"; onActivated: currentPage = 8 }
    Shortcut { sequence: "Alt+8"; onActivated: currentPage = 9 }

    Rectangle {
        anchors.fill: parent
        z: -20
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: backgroundSecondary }
            GradientStop { position: 0.55; color: backgroundPrimary }
            GradientStop { position: 1.0; color: backgroundTertiary }
        }
    }

    // Subtle structural texture for large cockpit surfaces. It remains faint
    // enough to preserve text contrast and disappears with enhanced visuals.
    Canvas {
        anchors.fill: parent
        z: -18
        visible: window.enhancedVisuals
        opacity: cockpit.theme === "crimson_light" ? 0.025 : 0.045
        property color gridColor: window.accentSecondary
        onGridColorChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            ctx.strokeStyle = gridColor
            ctx.lineWidth = 1
            var step = 64
            for (var x = step; x < width; x += step) {
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke()
            }
            for (var y = step; y < height; y += step) {
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke()
            }
        }
    }

    Item {
        anchors.fill: parent
        z: -15
        visible: window.enhancedVisuals
        opacity: 0.9
        Rectangle {
            width: 720; height: 260
            anchors.right: parent.right; anchors.top: parent.top
            anchors.rightMargin: -180; anchors.topMargin: -90
            rotation: -14; opacity: 0.16
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.52; color: cyan }
                GradientStop { position: 1.0; color: "transparent" }
            }
            SequentialAnimation on opacity {
                running: window.enhancedVisuals && !window.reducedMotion
                loops: Animation.Infinite
                NumberAnimation { to: 0.08; duration: 3600; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.16; duration: 3600; easing.type: Easing.InOutSine }
            }
        }
        Rectangle {
            width: 640; height: 220
            anchors.left: parent.left; anchors.bottom: parent.bottom
            anchors.leftMargin: -200; anchors.bottomMargin: -70
            rotation: 12; opacity: 0.10
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.58; color: green }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
    }

    component CockpitButton: Button {
        id: control
        property color accentColor: cyan
        property bool selected: false
        property string helpText: ""
        implicitHeight: 42
        focusPolicy: Qt.StrongFocus
        Accessible.name: text
        Accessible.description: helpText
        ToolTip.visible: hovered && helpText.length > 0
        ToolTip.text: helpText
        scale: down ? 0.975 : (hovered ? 1.01 : 1.0)
        Behavior on scale {
            enabled: !window.reducedMotion
            NumberAnimation { duration: 100; easing.type: Easing.OutCubic }
        }
        font.pixelSize: 13
        font.bold: true
        contentItem: Label {
            text: control.text
            color: control.selected ? backgroundPrimary : (control.hovered ? textPrimary : textSecondary)
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            leftPadding: 10
            rightPadding: 10
            elide: Text.ElideRight
            maximumLineCount: 1
            font: control.font
        }
        background: Rectangle {
            radius: 10
            color: control.selected
                   ? control.accentColor
                   : (control.hovered ? hover : inputBackground)
            border.width: control.activeFocus ? 2 : (control.selected ? 0 : 1)
            border.color: control.activeFocus || control.hovered ? control.accentColor : borderTone
            Rectangle {
                visible: window.enhancedVisuals && (control.selected || control.hovered)
                anchors.fill: parent
                anchors.margins: control.selected ? -2 : 1
                radius: parent.radius + 2
                color: "transparent"
                border.width: 1
                border.color: Qt.rgba(control.accentColor.r,
                                      control.accentColor.g,
                                      control.accentColor.b,
                                      control.selected ? 0.38 : 0.16)
            }
            Rectangle {
                visible: window.enhancedVisuals && control.selected
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                anchors.topMargin: 1
                height: 1
                radius: 1
                color: Qt.rgba(1, 1, 1, 0.34)
            }
            Behavior on color { enabled: !window.reducedMotion; ColorAnimation { duration: 130 } }
            Behavior on border.color { enabled: !window.reducedMotion; ColorAnimation { duration: 130 } }
        }
    }

    component SettingsNavigation: RowLayout {
        objectName: "qa-primary-settings-tabs"
        spacing: 6
        CockpitButton {
            Layout.preferredWidth: 112
            text: window.t("settings.tab.general", "GENERAL")
            selected: currentPage === 5
            onClicked: currentPage = 5
        }
        CockpitButton {
            Layout.preferredWidth: 142
            text: window.t("settings.tab.connections", "CONNECTIONS")
            selected: currentPage === 6
            onClicked: currentPage = 6
        }
        CockpitButton {
            Layout.preferredWidth: 134
            text: window.t("settings.tab.diagnostics", "DIAGNOSTICS")
            selected: currentPage === 7
            onClicked: currentPage = 7
        }
    }

    component SettingsHeader: Item {
        id: settingsHeader
        property string heading: ""
        property string subheading: ""
        property string qaName: ""
        objectName: qaName
        Layout.fillWidth: true
        Layout.preferredHeight: window.narrowWorkspace ? 112 : 64

        ColumnLayout {
            id: settingsHeaderTitle
            anchors.left: parent.left
            anchors.top: parent.top
            spacing: 2
            Label { text: settingsHeader.heading; color: textPrimary; font.pixelSize: 24; font.bold: true }
            Label { text: settingsHeader.subheading; color: muted; font.pixelSize: 13 }
        }
        SettingsNavigation {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: window.narrowWorkspace ? settingsHeaderTitle.bottom : parent.top
            anchors.topMargin: window.narrowWorkspace ? 12 : 0
        }
    }

    component ModernProgress: ProgressBar {
        id: progress
        property bool inactive: false
        property color progressColor: "transparent"
        implicitHeight: 14
        background: Rectangle {
            radius: 7
            color: divider
            border.width: 1
            border.color: Qt.rgba(textMuted.r, textMuted.g, textMuted.b, 0.16)
        }
        contentItem: Item {
            Rectangle {
                visible: window.enhancedVisuals && progress.visualPosition > 0
                width: progress.visualPosition * parent.width
                height: parent.height + 4
                anchors.verticalCenter: parent.verticalCenter
                radius: 9
                color: progress.progressColor.a > 0 ? progress.progressColor
                     : progress.value >= 1 ? green : cyan
                opacity: 0.14
            }
            Rectangle {
                width: progress.visualPosition * parent.width
                height: parent.height
                radius: 7
                color: progress.inactive ? textDisabled
                     : progress.progressColor.a > 0 ? progress.progressColor
                     : (progress.value >= 1 ? green : cyan)
                Behavior on width {
                    enabled: !window.reducedMotion
                    NumberAnimation { duration: 420; easing.type: Easing.OutCubic }
                }
                Rectangle {
                    visible: window.enhancedVisuals && parent.width > 12
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.leftMargin: 3
                    anchors.rightMargin: 3
                    anchors.topMargin: 2
                    height: 1
                    radius: 1
                    color: Qt.rgba(1, 1, 1, 0.34)
                }
            }
        }
    }

    component CommanderRankIcon: Item {
        property url motif: ""
        property int rankValue: -1
        property int maxRank: 13
        readonly property int filledSegments: rankValue < 0 ? 0 : Math.max(
            1, Math.min(5, Math.ceil((rankValue + 1) / (maxRank + 1) * 5))
        )
        implicitWidth: 58
        implicitHeight: 58
        Image {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            width: 34; height: 34
            source: parent.motif
            fillMode: Image.PreserveAspectFit
            opacity: parent.rankValue < 0 ? 0.35 : 0.92
        }
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            spacing: 3
            Repeater {
                model: 5
                delegate: Rectangle {
                    required property int index
                    width: 7; height: 5 + index * 2; radius: 2
                    anchors.bottom: parent.bottom
                    color: index < filledSegments ? cyan : divider
                }
            }
        }
    }

    component MaterialColumn: ShadowCard {
        id: materialColumn
        required property string category
        required property var rows
        property string query: ""
        property bool neededOnly: false
        property string statusFilter: "all"
        property string sortKey: "name"
        property bool sortDescending: false
        property color categoryColor: category === "Raw"
                                      ? accentSecondary
                                      : (category === "Manufactured" ? orange
                                         : (category === "Encoded" ? cyan : error))
        property var visibleRows: rows.filter(function(row) {
            var matchesCategory = row.category === materialColumn.category
            var matchesNeed = !materialColumn.neededOnly || row.need > 0
            var matchesStatus = materialColumn.statusFilter === "all"
                || (materialColumn.statusFilter === "needed" && row.need > 0)
                || (materialColumn.statusFilter === "tradeable" && row.tradeable)
                || row.status === materialColumn.statusFilter
            var matchesSearch = materialColumn.query.length === 0
                || row.name.toLowerCase().indexOf(materialColumn.query) >= 0
            var baseMatch = matchesCategory && matchesNeed && matchesSearch
            return baseMatch && matchesStatus
        }).sort(function(left, right) {
            var result = materialColumn.sortKey === "amount"
                ? left.have - right.have
                : left.name.localeCompare(right.name)
            return materialColumn.sortDescending ? -result : result
        })

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Rectangle { width: 4; height: 24; radius: 2; color: materialColumn.categoryColor }
                Label {
                    text: materialColumn.category.toUpperCase()
                    color: textPrimary; font.pixelSize: 15; font.bold: true
                }
                Item { Layout.fillWidth: true }
                Label {
                    text: materialColumn.visibleRows.length
                    color: materialColumn.categoryColor
                    font.pixelSize: 12; font.bold: true
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                    Label { text: window.t("common.sort", "SORT"); color: muted; font.pixelSize: 9; font.bold: true }
                CockpitButton {
                    text: window.t("common.name", "NAME") + " " + (materialColumn.sortKey === "name"
                                    ? (materialColumn.sortDescending ? "↓" : "↑") : "")
                    selected: materialColumn.sortKey === "name"
                    Layout.fillWidth: true
                    implicitHeight: 32
                    onClicked: {
                        if (materialColumn.sortKey === "name")
                            materialColumn.sortDescending = !materialColumn.sortDescending
                        else {
                            materialColumn.sortKey = "name"
                            materialColumn.sortDescending = false
                        }
                    }
                }
                CockpitButton {
                    text: window.t("common.amount", "AMOUNT") + " " + (materialColumn.sortKey === "amount"
                                      ? (materialColumn.sortDescending ? "↓" : "↑") : "")
                    selected: materialColumn.sortKey === "amount"
                    Layout.fillWidth: true
                    implicitHeight: 32
                    onClicked: {
                        if (materialColumn.sortKey === "amount")
                            materialColumn.sortDescending = !materialColumn.sortDescending
                        else {
                            materialColumn.sortKey = "amount"
                            materialColumn.sortDescending = true
                        }
                    }
                }
            }
            ListView {
                id: categoryList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 8
                model: materialColumn.visibleRows
                ScrollBar.vertical: CockpitScrollBar {}
                delegate: Rectangle {
                    required property var modelData
                    width: categoryList.width - 10
                    height: modelData.warning ? 136 : (modelData.need > 0 ? 112 : 88)
                    radius: 12
                    color: materialHover.containsMouse ? hover : panelRaised
                    border.width: modelData.missing > 0 ? 1 : 0
                    border.color: error
                    Behavior on color { enabled: !window.reducedMotion; ColorAnimation { duration: 130 } }
                    MouseArea {
                        id: materialHover
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: cockpit.selectMaterial(modelData.key)
                    }
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 5
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: modelData.name
                                color: textPrimary; font.pixelSize: 13; font.bold: true
                                Layout.fillWidth: true; elide: Text.ElideRight
                            }
                            Label {
                                visible: modelData.surplus > 0
                                text: "+" + modelData.surplus + " " + window.t("common.surplus", "SURPLUS")
                                color: green; font.pixelSize: 9; font.bold: true
                            }
                            Label {
                                text: modelData.capacityKnown ? "G" + modelData.grade : window.t("status.value.unknown", "UNKNOWN")
                                color: materialColumn.categoryColor
                                font.pixelSize: 10; font.bold: true
                            }
                        }
                        ModernProgress { Layout.fillWidth: true; value: modelData.capacityProgress }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: window.t("common.stock", "STOCK") + "  " + modelData.have + " / "
                                      + (modelData.capacityKnown ? modelData.capacity : "CAPACITY UNKNOWN")
                                color: muted; font.pixelSize: 10
                            }
                            Item { Layout.fillWidth: true }
                            Label {
                                text: modelData.need === 0
                                      ? "NOT NEEDED"
                                      : (modelData.missing > 0
                                         ? modelData.missing + " MISSING"
                                         : "READY")
                                color: modelData.missing > 0 ? orange : (modelData.need > 0 ? green : muted)
                                font.pixelSize: 10; font.bold: modelData.need > 0
                            }
                        }
                        ModernProgress {
                            visible: modelData.need > 0
                            Layout.fillWidth: true
                            value: modelData.needProgress
                        }
                        Label {
                            visible: !!modelData.warning
                            text: modelData.warning
                            color: error; font.pixelSize: 10; font.bold: true
                            Layout.fillWidth: true; elide: Text.ElideRight
                        }
                        Label {
                            visible: modelData.need > 0
                            text: window.t("common.build", "BUILD") + "  " + modelData.have + " / " + modelData.need
                            color: modelData.missing > 0 ? orange : green
                            font.pixelSize: 10; font.bold: true
                        }
                    }
                }
                Label {
                    anchors.centerIn: parent
                    visible: materialColumn.visibleRows.length === 0
                    text: materialColumn.neededOnly
                          ? "NO REQUIRED " + materialColumn.category.toUpperCase() + " MATERIALS"
                          : "NO MATERIALS MATCH THE ACTIVE FILTERS"
                    color: muted; font.pixelSize: 11; font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    width: parent.width - 30
                }
            }
        }
    }

    Rectangle {
        id: sidebar
        width: window.compactSidebar ? 76 : 210
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        color: backgroundSecondary
        Behavior on width {
            enabled: !window.reducedMotion
            NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
        }
        Rectangle {
            width: 1; anchors.right: parent.right; anchors.top: parent.top
            anchors.bottom: parent.bottom; color: divider
        }
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 10
            Label {
                text: window.compactSidebar ? "EC" : "EDEC"
                color: cyan
                font.pixelSize: 23
                font.bold: true
                Layout.bottomMargin: 24
            }
            ListView {
                id: navigationList
                objectName: "qa-navigation-drag-list"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 4
                interactive: contentHeight > height
                boundsBehavior: Flickable.StopAtBounds
                model: window.orderedNavigation()
                ScrollBar.vertical: CockpitScrollBar {
                    policy: ScrollBar.AlwaysOff
                }
                delegate: Item {
                    id: navTile
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: 52
                    property bool selectedNav: modelData.page === 5
                                               ? currentPage >= 5 && currentPage <= 7
                                               : currentPage === modelData.page
                    Rectangle {
                        id: navCard
                        x: 0; y: 2; width: navTile.width; height: 48
                        z: navDrag.drag.active ? 100 : 1
                        activeFocusOnTab: true
                        Accessible.name: navTile.modelData.label
                        Accessible.role: Accessible.Button
                        radius: 12
                        color: navTile.selectedNav ? active
                               : navMouse.containsMouse ? inputBackground : "transparent"
                        Rectangle {
                            visible: navTile.selectedNav
                            width: 4; height: 24; radius: 2; color: cyan
                            anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                        }
                        Label {
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: window.compactSidebar ? undefined : parent.left
                            anchors.leftMargin: window.compactSidebar ? 0 : 18
                            anchors.horizontalCenter: window.compactSidebar ? parent.horizontalCenter : undefined
                            text: window.compactSidebar ? navTile.modelData.icon : navTile.modelData.label
                            color: navTile.selectedNav ? textPrimary : muted
                            font.pixelSize: window.compactSidebar ? 20 : 13
                            font.bold: true
                        }
                        Label {
                            visible: !window.compactSidebar
                            anchors.right: parent.right; anchors.rightMargin: 7
                            anchors.verticalCenter: parent.verticalCenter
                            text: "⠿"; color: navTile.selectedNav ? cyan : muted
                            font.pixelSize: 12; font.bold: true
                        }
                        MouseArea {
                            id: navMouse
                            anchors.left: parent.left; anchors.top: parent.top
                            anchors.bottom: parent.bottom; anchors.right: navDrag.left
                            hoverEnabled: true
                            onClicked: currentPage = navTile.modelData.page
                        }
                        MouseArea {
                            id: navDrag
                            width: window.compactSidebar ? parent.width : 28
                            anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                            onClicked: {
                                if (window.compactSidebar)
                                    currentPage = navTile.modelData.page
                            }
                            drag.target: navCard
                            drag.minimumX: -navTile.x
                            drag.maximumX: navigationList.width - navTile.x - navCard.width
                            drag.minimumY: -navTile.y + 2
                            drag.maximumY: Math.max(2, navigationList.contentHeight - navTile.y - navCard.height - 2)
                            onReleased: {
                                let target = navigationList.indexAt(
                                    navTile.x + navCard.x + navCard.width / 2,
                                    navTile.y + navCard.y + navCard.height / 2)
                                navCard.x = 0
                                navCard.y = 2
                                if (target >= 0)
                                    window.moveNavigation(navTile.index, target)
                            }
                        }
                        Keys.onReturnPressed: currentPage = navTile.modelData.page
                        Keys.onSpacePressed: currentPage = navTile.modelData.page
                        ToolTip.visible: window.compactSidebar && navMouse.containsMouse
                        ToolTip.text: navTile.modelData.label
                    }
                }
            }
            Label {
                visible: cockpit.backgroundMode
                Layout.fillWidth: true
                text: window.compactSidebar ? "●" : window.t("common.tray_mode", "●  TRAY MODE")
                color: green; font.pixelSize: 10; font.bold: true
                horizontalAlignment: Text.AlignHCenter
                ToolTip.visible: trayModeMouse.containsMouse
                ToolTip.text: window.t("common.close_to_tray_help", "Closing the window keeps Journal and EDDN monitoring active")
                MouseArea { id: trayModeMouse; anchors.fill: parent; hoverEnabled: true }
            }
            CockpitButton {
                text: window.compactSidebar ? "⌕" : window.t("common.search_shortcut", "SEARCH  Ctrl+K")
                Layout.fillWidth: true
                onClicked: globalSearchDialog.open()
            }
            CockpitButton {
                objectName: "qa-kofi-support-button"
                text: window.compactSidebar ? "♥" : window.t("common.support", "SUPPORT ON KO-FI")
                Layout.fillWidth: true
                ToolTip.visible: hovered
                ToolTip.text: window.t("common.support_help", "Open ko-fi.com/cmdrforcer in your browser")
                onClicked: Qt.openUrlExternally("https://ko-fi.com/cmdrforcer")
            }
            CockpitButton {
                objectName: "qa-about-button"
                text: window.compactSidebar ? "ⓘ" : window.t("common.about", "ABOUT")
                Layout.fillWidth: true
                ToolTip.visible: hovered
                ToolTip.text: window.t("dialog.about.title", "About ED Engineering Companion")
                onClicked: aboutDialog.open()
            }
        }
    }

    Loader {
        id: pageLoader0
        anchors.fill: parent
        active: window.currentPage === 0
        asynchronous: false
        sourceComponent: Component {
    ColumnLayout {
        objectName: "qa-page-operations"
        visible: true
        anchors.fill: parent
        anchors.leftMargin: sidebar.width + (window.compactSidebar ? 18 : 26)
        anchors.rightMargin: window.compactSidebar ? 18 : 26
        anchors.topMargin: window.compactSidebar ? 18 : 26
        anchors.bottomMargin: window.compactSidebar ? 18 : 26
        spacing: 16

        GridLayout {
            Layout.fillWidth: true
            columns: window.narrowWorkspace ? 1 : 2
            columnSpacing: 16
            rowSpacing: 8
            ColumnLayout {
                objectName: "qa-header-operations"
                Layout.fillWidth: true
                spacing: 2
                Label { text: window.t("operations.title", "COMMANDER OPERATIONS"); color: textPrimary; font.pixelSize: 24; font.bold: true }
                Label {
                    text: window.tf("status.wishlist_ship", "WISHLIST · %1", [cockpit.ship])
                          + (cockpit.activeShip
                             ? "  ·  FLYING · " + cockpit.activeShip : "")
                          + "  ·  " + cockpit.system
                    color: muted; font.pixelSize: 13
                }
            }
            RowLayout {
                Layout.fillWidth: window.narrowWorkspace
                Layout.alignment: Qt.AlignRight
                ComboBox {
                    id: wishlistShip
                    Layout.preferredHeight: 42
                    Layout.fillWidth: window.narrowWorkspace
                    Layout.preferredWidth: 330
                    Layout.maximumWidth: window.narrowWorkspace ? 520 : 330
                    model: cockpit.ships
                    currentIndex: Math.max(0, cockpit.ships.indexOf(cockpit.ship))
                    onActivated: cockpit.setSelectedShip(currentText)
                    contentItem: Label {
                        leftPadding: 14
                        text: wishlistShip.displayText
                        color: textPrimary
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }
                    background: Rectangle {
                        radius: 12
                        color: inputBackground
                        border.width: wishlistShip.activeFocus ? 2 : 1
                        border.color: wishlistShip.activeFocus ? cyan : borderTone
                    }
                }
                CockpitButton {
                    visible: !!cockpit.activeShip
                             && (!cockpit.followActiveShip
                                 || cockpit.ship !== cockpit.activeShip)
                    text: window.t("operations.follow_current", "FOLLOW CURRENT")
                    selected: true
                    onClicked: cockpit.followCurrentShip()
                }
                Rectangle {
                    width: 82; height: 32; radius: 16; color: successBackground
                        Label { anchors.centerIn: parent; text: window.t("common.live", "●  LIVE"); color: green; font.bold: true }
                    SequentialAnimation on opacity {
                        running: !window.reducedMotion
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.68; duration: 900 }
                        NumberAnimation { to: 1.0; duration: 900 }
                    }
                }
            }
        }

        RowLayout {
            property int focusHeight: (cockpit.operationAction.engineerOptions || []).length > 0
                                      ? 410 : 320
            Layout.fillWidth: true
            Layout.fillHeight: false
            Layout.minimumHeight: focusHeight
            Layout.preferredHeight: focusHeight
            Layout.maximumHeight: focusHeight
            spacing: 16

        ShadowCard {
            objectName: "qa-card-operations"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: 500
            accent: actionHover.containsMouse ? cyan : "transparent"
            Behavior on scale { enabled: !window.reducedMotion; NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
            scale: actionHover.containsMouse ? 1.006 : 1.0
            MouseArea { id: actionHover; anchors.fill: parent; hoverEnabled: true }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 24; spacing: 8
                    Label { text: window.t("operations.next_action", "NEXT BEST ACTION  ·  WHAT NOW"); color: cyan; font.pixelSize: 12; font.bold: true }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14
                    Item {
                        width: 72; height: 72
                        visible: !!(cockpit.operationAction.portraitUrl)
                        Rectangle {
                            anchors.fill: parent
                            radius: 10
                            color: cardRaised
                            border.width: 1
                            border.color: borderTone
                            clip: true
                            Image {
                                anchors.fill: parent
                                source: cockpit.operationAction.portraitUrl || ""
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: cockpit.operationAction.title || cockpit.nextAction
                        color: textPrimary; font.pixelSize: 25; font.bold: true
                        wrapMode: Text.WordWrap
                    }
                }
                Label {
                    visible: !!cockpit.operationAction.detail
                    text: cockpit.operationAction.detail || ""
                    color: muted; font.pixelSize: 12
                    Layout.fillWidth: true; elide: Text.ElideRight
                }
                RowLayout {
                    visible: !!cockpit.operationAction.moduleName
                    Layout.fillWidth: true
                    spacing: 8
                    Label {
                        text: window.t("operations.module", "MODULE") + " · "
                              + (cockpit.operationAction.moduleName || "")
                              + (cockpit.operationAction.targetGrade > 0
                                 ? " · G" + cockpit.operationAction.targetGrade : "")
                        color: cyan; font.pixelSize: 11; font.bold: true
                        elide: Text.ElideRight; Layout.fillWidth: true
                    }
                    Label {
                        visible: !!cockpit.operationAction.experimentalName
                        text: window.t("operations.experimental", "EXPERIMENTAL") + " · "
                              + cockpit.operationAction.experimentalName
                        color: green; font.pixelSize: 11; font.bold: true
                        elide: Text.ElideRight; Layout.fillWidth: true
                    }
                }
                Label {
                    visible: !!cockpit.operationAction.blueprintName
                    text: window.t("operations.blueprint", "BLUEPRINT") + " · "
                          + cockpit.operationAction.blueprintName
                    color: textSecondary; font.pixelSize: 11; font.bold: true
                    Layout.fillWidth: true; elide: Text.ElideRight
                }
                ModernProgress {
                    Layout.fillWidth: true
                    value: window.materialDisplay(
                               cockpit.materialStatus, cockpit.completion,
                               cockpit.completionReliable).completion
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: window.t("common.material_prefix", "MATERIAL · ") + window.materialDisplay(
                                  cockpit.materialStatus, cockpit.completion,
                                  cockpit.completionReliable).status
                              + " · " + Math.round(window.materialDisplay(
                                                       cockpit.materialStatus,
                                                       cockpit.completion,
                                                       cockpit.completionReliable).completion * 100) + "%"
                        color: window.materialDisplay(
                                   cockpit.materialStatus, cockpit.completion,
                                   cockpit.completionReliable).status === "READY"
                               ? green : orange
                        font.pixelSize: 13; font.bold: true
                    }
                    Label {
                        text: window.tf("status.progress", "PROGRESS · %1", [cockpit.planProgressStatus])
                        color: cockpit.planProgressStatus === "COMPLETE" ? green : cyan
                        font.pixelSize: 13; font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                    text: window.tf(cockpit.required === 1 ? "status.material_unit" : "status.material_units",
                                    cockpit.required === 1 ? "%1 / %2 MATERIAL UNIT" : "%1 / %2 MATERIAL UNITS",
                                    [cockpit.covered, cockpit.required])
                        color: muted; font.pixelSize: 12
                    }
                }
                Label {
                    visible: !!cockpit.calculationWarning
                    text: cockpit.calculationWarning
                    color: error; font.pixelSize: 11; font.bold: true
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                }
                Label {
                    visible: cockpit.missingMaterials.length > 0
                    text: window.t("common.missing_prefix", "MISSING · ") + cockpit.missingMaterials.map(function(row) {
                        return row.name + " ×" + row.missing
                    }).join("   ·   ")
                    color: orange; font.pixelSize: 12; font.bold: true
                    Layout.fillWidth: true; elide: Text.ElideRight
                }
                Label {
                    visible: cockpit.missingMaterials.length === 0
                             && !!cockpit.nextEngineerStop.name
                    text: (cockpit.nextEngineerStop.readyJobs || 0)
                          + " READY JOB"
                          + ((cockpit.nextEngineerStop.readyJobs || 0) === 1 ? "" : "S")
                          + " · " + (cockpit.nextEngineerStop.jobNames || []).join("   ·   ")
                    color: cockpit.nextEngineerStop.craftable ? green : orange
                    font.pixelSize: 12; font.bold: true
                    Layout.fillWidth: true; elide: Text.ElideRight
                }
                Label {
                    visible: (cockpit.operationAction.engineerOptions || []).length > 0
                    text: window.t("operations.engineers", "ENGINEERS FOR TARGET GRADE · ALL CAPABLE OPTIONS")
                    color: orange; font.pixelSize: 10; font.bold: true
                }
                ListView {
                    visible: count > 0
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? 72 : 0
                    orientation: ListView.Horizontal
                    spacing: 8; clip: true
                    model: cockpit.operationAction.engineerOptions || []
                    ScrollBar.horizontal: CockpitScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: 340; height: 64; radius: 9
                        color: modelData.craftable ? successBackground : panelRaised
                        border.width: 1
                        border.color: modelData.craftable ? success
                                      : modelData.status === "rank_too_low" ? warning : borderTone
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 9; spacing: 8
                            Item {
                                width: 46; height: 46
                                visible: !!(modelData.portraitUrl)
                                Rectangle {
                                    anchors.fill: parent
                                    radius: 8
                                    color: cardRaised
                                    clip: true
                                    Image {
                                        anchors.fill: parent
                                        source: modelData.portraitUrl || ""
                                        fillMode: Image.PreserveAspectCrop
                                        asynchronous: true
                                    }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.name + " · " + modelData.system
                                    color: textPrimary; font.pixelSize: 10; font.bold: true
                                    elide: Text.ElideRight
                                }
                                Label {
                                    text: window.tf("status.max_grade", "MAX G%1 · %2", [modelData.maxGrade, modelData.statusText])
                                    color: modelData.craftable ? green
                                         : modelData.status === "rank_too_low" ? orange : muted
                                    font.pixelSize: 9; font.bold: true
                                }
                            }
                            CockpitButton {
                                text: window.t("common.copy", "COPY")
                                implicitWidth: 66; implicitHeight: 30
                                enabled: modelData.system !== "System not stored"
                                onClicked: cockpit.copySystem(modelData.system)
                            }
                        }
                    }
                }
                Label {
                    visible: !!cockpit.operationAction.reason
                             && (String(cockpit.operationAction.kind).indexOf("BLOCKER") >= 0
                                 || String(cockpit.operationAction.kind).indexOf("TECH_BROKER") >= 0
                                 || cockpit.operationAction.kind === "ENGINEER_UNLOCK"
                                 || cockpit.operationAction.kind === "ENGINEER_PREPARE")
                    text: window.tf("status.why", "WHY · %1", [cockpit.operationAction.reason || ""])
                    color: cyan; font.pixelSize: 11; font.bold: true
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    maximumLineCount: 2; elide: Text.ElideRight
                }
                Label {
                    visible: !!cockpit.operationAction.after
                             && (String(cockpit.operationAction.kind).indexOf("BLOCKER") >= 0
                                 || String(cockpit.operationAction.kind).indexOf("TECH_BROKER") >= 0
                                 || cockpit.operationAction.kind === "ENGINEER_UNLOCK"
                                 || cockpit.operationAction.kind === "ENGINEER_PREPARE"
                                 || cockpit.operationAction.kind === "TRADE"
                                 || cockpit.operationAction.kind === "COLLECT")
                    text: window.tf("status.after", "AFTER THIS · %1", [cockpit.operationAction.after || ""])
                    color: textSecondary; font.pixelSize: 11
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    maximumLineCount: 2; elide: Text.ElideRight
                }
                RowLayout {
                    Layout.fillWidth: true
                    CockpitButton {
                        text: cockpit.operationAction.buttonLabel || window.t("wishlist.open_engineering", "OPEN ENGINEERING")
                        selected: true
                        enabled: cockpit.operationAction.executable !== false
                        onClicked: {
                            if (!!cockpit.operationAction.system)
                                cockpit.copySystem(cockpit.operationAction.system)
                            else if ((cockpit.operationAction.targetPage || -1) >= 0) {
                                window.currentPage = cockpit.operationAction.targetPage
                                if (cockpit.operationAction.farmMissing)
                                    window.requestMaterialFarmMissing(false)
                            }
                        }
                    }
                    CockpitButton {
                        visible: cockpit.operationAction.kind === "ENGINEER_TRAVEL"
                        text: window.t("operations.engineer_later", "ENGINEER STOP LATER")
                        enabled: cockpit.engineerMissionRoute.length > 1
                        onClicked: cockpit.deferNextEngineer()
                    }
                    CockpitButton {
                        text: window.t("operations.activity_history", "ACTIVITY HISTORY")
                        onClicked: window.currentPage = 9
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: cockpit.serviceStatus.map(function(row) {
                            return row.name + " " + row.status
                        }).join(" · ")
                        color: muted; font.pixelSize: 10
                        elide: Text.ElideLeft; Layout.maximumWidth: 520
                    }
                }
            }
        }

        ShadowCard {
            visible: cockpit.trackedItems.length > 0
            Layout.preferredWidth: visible ? 340 : 0
            Layout.minimumWidth: visible ? 310 : 0
            Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 15; spacing: 7
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: window.t("operations.tracked_work", "TRACKED WORK")
                        color: cyan; font.pixelSize: 13; font.bold: true
                    }
                    Label {
                        text: window.tf("status.active_count", "%1 ACTIVE", [cockpit.trackedItems.length])
                        color: orange; font.pixelSize: 10; font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                }
                ListView {
                    id: trackedWorkList
                    Layout.fillWidth: true; Layout.fillHeight: true
                    orientation: ListView.Vertical
                    model: cockpit.trackedItems
                    spacing: 9; clip: true
                    ScrollBar.vertical: CockpitScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: trackedWorkList.width; height: 124; radius: 10
                        color: active; border.width: 1; border.color: cyan
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 9
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Label {
                                    text: modelData.kind + " · " + modelData.title
                                    color: textPrimary; font.pixelSize: 11; font.bold: true
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                }
                                Label {
                                    text: modelData.subtitle + " · " + modelData.status
                                          + " · " + window.countLabel(modelData.missingKinds, "TYPE", "TYPES") + " MISSING"
                                    color: modelData.missingKinds > 0 ? orange : green
                                    font.pixelSize: 9; font.bold: true
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                CockpitButton {
                                    text: window.t("common.details", "DETAILS")
                                    Layout.fillWidth: true
                                    implicitHeight: 34
                                    onClicked: {
                                        if (modelData.kind === "WISHLIST") {
                                            window.currentPage = 1
                                        } else {
                                            window.requestGuardianPage(modelData)
                                        }
                                    }
                                }
                                CockpitButton {
                                    text: window.t("common.untrack", "UNTRACK")
                                    implicitWidth: 118
                                    Layout.fillWidth: true; implicitHeight: 34
                                    onClicked: {
                                        if (modelData.kind === "WISHLIST")
                                            cockpit.prioritizePinnedPlan(modelData.id)
                                        else
                                            cockpit.trackTechBrokerUnlock(
                                                modelData.id,
                                                modelData.brokerSubtype)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16
            ShadowCard {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: window.compactSidebar ? 500 : 650
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: cockpit.trades.length > 0
                                  ? "MATERIAL TRADES" : "MATERIALS TO COLLECT"
                            color: textPrimary; font.pixelSize: 17; font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: cockpit.trades.length > 0
                                  ? cockpit.trades.length + " PLANNED"
                                  : window.countLabel(cockpit.missingMaterials.length, "MISSING TYPE", "MISSING TYPES")
                            color: cockpit.trades.length > 0 ? cyan : orange
                            font.pixelSize: 12; font.bold: true
                        }
                    }
                    Label {
                        text: cockpit.trades.length > 0
                              ? "In Elite select WANTED first, then GIVE. Journal checks completed trades automatically."
                              : "Collect these exact amounts. Open MATERIAL DETAILS for verified sources and safe trade options."
                        color: muted; font.pixelSize: 12
                    }
                    RowLayout {
                        visible: cockpit.trades.length > 0
                        Layout.fillWidth: true
                        spacing: 6
                        Repeater {
                            model: ["AUTO", "RAW", "MANUFACTURED", "ENCODED"]
                            CockpitButton {
                                required property string modelData
                                Layout.fillWidth: true
                                implicitHeight: 34
                                text: modelData === "AUTO"
                                      ? "AUTO" + (window.automaticTradeCategory()
                                                   ? " · " + window.automaticTradeCategory() : "")
                                      : modelData
                                selected: window.materialTradeFilter === modelData
                                onClicked: window.materialTradeFilter = modelData
                            }
                        }
                    }
                    ListView {
                        id: tradeList
                        property real retainedContentY: 0
                        property string activeTradeCategory: model.length
                                                             ? String(model[0].category || "") : ""
                        function rememberViewport() {
                            retainedContentY = contentY
                            const visibleIndex = indexAt(8, contentY - originY + 8)
                            if (visibleIndex >= 0 && visibleIndex < model.length)
                                activeTradeCategory = String(model[visibleIndex].category || "")
                        }
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 5
                        model: window.visibleMaterialTrades(cockpit.trades)
                        visible: cockpit.trades.length > 0
                        onMovementEnded: rememberViewport()
                        ScrollBar.vertical: CockpitScrollBar {}
                        Timer {
                            id: restoreTradeViewport
                            interval: 0
                            repeat: false
                            onTriggered: {
                                const maximum = tradeList.originY
                                              + Math.max(0, tradeList.contentHeight
                                                            - tradeList.height)
                                tradeList.contentY = Math.max(
                                    tradeList.originY,
                                    Math.min(tradeList.retainedContentY, maximum))
                                tradeList.rememberViewport()
                            }
                        }
                        section.property: "category"
                        section.criteria: ViewSection.FullString
                        section.delegate: Rectangle {
                            required property string section
                            width: tradeList.width - 12
                            height: 28
                            color: backgroundSecondary
                            Label {
                                anchors.left: parent.left
                                anchors.leftMargin: 10
                                anchors.verticalCenter: parent.verticalCenter
                                text: section.toUpperCase()
                                color: section === "Raw" ? accentSecondary
                                      : (section === "Encoded" ? cyan : orange)
                                font.pixelSize: 10
                                font.bold: true
                            }
                        }
                        Connections {
                            target: cockpit
                            function onStateChanged() {
                                restoreTradeViewport.restart()
                            }
                        }
                        delegate: Rectangle {
                            required property var modelData
                            readonly property bool isNextTrade: modelData.nextInCategory
                                                                 && modelData.category === tradeList.activeTradeCategory
                            width: tradeList.width - 12
                            height: modelData.traderWarning ? 104 : 86
                            radius: 10
                            color: isNextTrade ? active
                                               : (tradeMouse.containsMouse ? hover : panelRaised)
                            border.width: isNextTrade ? 2 : 1
                            border.color: isNextTrade ? cyan : borderTone
                            Behavior on color { enabled: !window.reducedMotion; ColorAnimation { duration: 140 } }
                            MouseArea { id: tradeMouse; anchors.fill: parent; hoverEnabled: true }
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 10; spacing: 10
                                Rectangle {
                                    width: 28; height: 28; radius: 14
                                    color: backgroundSecondary
                                    border.color: isNextTrade ? cyan : borderTone
                                    Label { anchors.centerIn: parent; text: "○"; color: isNextTrade ? cyan : muted; font.pixelSize: 16 }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        text: modelData.instruction
                                        color: textPrimary; font.pixelSize: 13; font.bold: true
                                        elide: Text.ElideRight; Layout.fillWidth: true
                                    }
                                    Label {
                                        visible: !!modelData.traderWarning
                                        text: modelData.traderWarning
                                        color: orange; font.pixelSize: 11; font.bold: true
                                    }
                                    Label {
                                        text: modelData.category
                                              + (modelData.station
                                                 ? " TRADER · " + modelData.station
                                                   + " · " + modelData.system : "")
                                        color: muted; font.pixelSize: 11
                                        elide: Text.ElideRight; Layout.fillWidth: true
                                    }
                                }
                                ColumnLayout {
                                    Layout.preferredWidth: 168
                                    Layout.minimumWidth: 168
                                    Layout.maximumWidth: 168
                                    Layout.alignment: Qt.AlignVCenter | Qt.AlignRight
                                    spacing: 5
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            text: isNextTrade ? window.t("operations.next_trade", "NEXT TRADE")
                                                   : (modelData.remaining <= 0
                                                      ? "PENDING · TRADE"
                                                      : modelData.remaining + " MISSING")
                                            color: modelData.remaining <= 0 ? green : orange
                                            font.pixelSize: 11; font.bold: true
                                        }
                                        Item { Layout.fillWidth: true }
                                    }
                                    CockpitButton {
                                        text: window.t("common.material_details", "MATERIAL DETAILS")
                                        Layout.fillWidth: true
                                        implicitHeight: 34
                                        onClicked: cockpit.selectMaterial(modelData.targetKey)
                                    }
                                }
                            }
                        }
                    }
                    ListView {
                        id: missingMaterialList
                        visible: cockpit.trades.length === 0
                                 && cockpit.missingMaterials.length > 0
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true; spacing: 10
                        model: cockpit.missingMaterials
                        ScrollBar.vertical: CockpitScrollBar {}
                        delegate: Rectangle {
                            required property var modelData
                            width: missingMaterialList.width - 12
                            height: 132; radius: 14
                            color: missingMouse.containsMouse ? hover : panelRaised
                            border.width: 1; border.color: orange
                            MouseArea {
                                id: missingMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.NoButton
                            }
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 16
                                spacing: 16
                                Rectangle {
                                    width: 42; height: 42; radius: 21
                                    color: warningBackground; border.color: orange
                                    Label {
                                        anchors.centerIn: parent
                                        text: modelData.missing
                                        color: orange; font.pixelSize: 17; font.bold: true
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 4
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            text: modelData.name
                                            color: textPrimary; font.pixelSize: 15
                                            font.bold: true; Layout.fillWidth: true
                                        }
                                        Label {
                                            text: (modelData.category || "").toUpperCase()
                                                  + " · G" + modelData.grade
                                            color: cyan; font.pixelSize: 10; font.bold: true
                                        }
                                    }
                                    Label {
                                        text: window.tf("status.have", "HAVE %1", [modelData.have])
                                              + "   ·   NEED " + modelData.need
                                              + "   ·   MISSING " + modelData.missing
                                        color: orange; font.pixelSize: 11; font.bold: true
                                    }
                                    Label {
                                        text: (modelData.sourceCards || []).length > 0
                                              ? modelData.sourceCards[0].label
                                                + "  →  " + modelData.sourceCards[0].detail
                                              : "No verified acquisition source stored."
                                        color: textSecondary; font.pixelSize: 10
                                        wrapMode: Text.WordWrap; Layout.fillWidth: true
                                        maximumLineCount: 2; elide: Text.ElideRight
                                    }
                                }
                                CockpitButton {
                                    text: window.t("common.material_details", "MATERIAL DETAILS")
                                    onClicked: cockpit.selectMaterial(modelData.key)
                                }
                            }
                        }
                    }
                    Item {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        visible: cockpit.trades.length === 0
                                 && cockpit.missingMaterials.length === 0
                        EmptyState {
                            anchors.centerIn: parent
                            width: Math.min(760, parent.width - 80)
                            symbol: cockpit.fleetKnown ? "✓" : "⌁"
                            title: cockpit.fleetKnown
                                   ? "READY · ALL REQUIRED MATERIALS"
                                   : "PENDING · COMMANDER JOURNAL"
                            detail: cockpit.fleetKnown
                                    ? "Your current build does not require a Material Trader. No collection action is required."
                                    : cockpit.emptyStateReason
                            tone: cockpit.fleetKnown ? green : orange
                            prominent: true
                        }
                    }
                }
            }

            ShadowCard {
                Layout.preferredWidth: window.compactSidebar ? 310 : 380
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: window.t("operations.trader_route", "TRADER ROUTE"); color: textPrimary; font.pixelSize: 17; font.bold: true }
                        Item { Layout.fillWidth: true }
                        ComboBox {
                            id: traderPreferenceChoice
                            Layout.preferredWidth: 124
                            Layout.preferredHeight: 32
                            model: ["CONFIRMED", "NEAREST"]
                            currentIndex: cockpit.traderPreference === "nearest" ? 1 : 0
                            onActivated: cockpit.setTraderPreference(
                                currentIndex === 1 ? "nearest" : "confirmed"
                            )
                            contentItem: Label {
                                leftPadding: 10
                                text: traderPreferenceChoice.displayText
                                color: cyan; font.pixelSize: 10; font.bold: true
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 9; color: inputBackground
                                border.color: traderPreferenceChoice.activeFocus
                                              ? cyan : borderTone
                            }
                            ToolTip.visible: hovered
                            ToolTip.text: currentIndex === 1
                                          ? "Prefer the shortest catalog distance; confidence is secondary."
                                          : "Prefer Journal-confirmed trader types; distance is secondary."
                        }
                        Label {
                            text: cockpit.routeDistance.toFixed(1) + " LY"
                            color: cyan; font.pixelSize: 12; font.bold: true
                        }
                    }
                    ScrollView {
                        id: traderRouteScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: availableWidth
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ScrollBar.vertical: CockpitScrollBar {}
                        Column {
                            width: traderRouteScroll.availableWidth
                            spacing: 12
                            ListView {
                                id: routeList
                                width: parent.width
                                height: Math.max(66, contentHeight)
                                model: cockpit.traderRoute
                                spacing: 8
                                interactive: false
                                delegate: Rectangle {
                                    required property var modelData
                                    width: routeList.width
                                    height: 66; radius: 10
                                    color: inputBackground; border.color: borderTone
                                    RowLayout {
                                        anchors.fill: parent; anchors.margins: 10
                                        Rectangle {
                                            width: 28; height: 28; radius: 14; color: active
                                            Label {
                                                anchors.centerIn: parent
                                                text: modelData.sequence; color: cyan; font.bold: true
                                            }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true; spacing: 1
                                            Label {
                                                text: modelData.system
                                                color: textPrimary; font.pixelSize: 13; font.bold: true
                                                elide: Text.ElideRight; Layout.fillWidth: true
                                            }
                                            Label {
                                                text: modelData.station + " · " + modelData.category
                                                      + (typeof modelData.leg_distance_ly === "number"
                                                         ? " · " + modelData.leg_distance_ly.toFixed(1) + " ly" : "")
                                                color: muted; font.pixelSize: 10
                                                elide: Text.ElideRight; Layout.fillWidth: true
                                            }
                                        }
                                        CockpitButton {
                                            text: window.t("common.copy", "COPY")
                                            onClicked: cockpit.copySystem(modelData.system)
                                        }
                                    }
                                }
                                Label {
                                    anchors.centerIn: parent
                                    visible: cockpit.traderRoute.length === 0
                                    text: window.t("operations.no_trader", "NO TRADER FLIGHT REQUIRED")
                                    color: green; font.pixelSize: 12; font.bold: true
                                }
                            }
                                Label { width: parent.width; text: window.t("operations.latest_trade", "LATEST CONFIRMED TRADE"); color: muted; font.pixelSize: 11; font.bold: true }
                            Label {
                                width: parent.width
                                text: cockpit.tradeHistory.length
                                      ? "✓ " + cockpit.tradeHistory[0].summary
                                      : "Waiting for a MaterialTrade Journal event"
                                color: cockpit.tradeHistory.length ? green : muted
                                font.pixelSize: 12; wrapMode: Text.WordWrap
                            }
                                Label { width: parent.width; text: window.t("operations.live_activity", "LIVE ACTIVITY"); color: muted; font.pixelSize: 11; font.bold: true }
                            Rectangle {
                                width: parent.width; height: 4; radius: 2
                                color: cyan
                                SequentialAnimation on opacity {
                                    running: !window.reducedMotion
                                    loops: Animation.Infinite
                                    NumberAnimation { to: 0.25; duration: 1000 }
                                    NumberAnimation { to: 1; duration: 1000 }
                                }
                            }
                            Label {
                                width: parent.width
                                text: cockpit.activity
                                color: textSecondary; font.pixelSize: 12
                                wrapMode: Text.WordWrap
                            }
                            Rectangle { width: parent.width; height: 1; color: borderTone }
                            RowLayout {
                                width: parent.width
                                    Label { text: window.t("operations.live_hge", "LIVE HGE"); color: green; font.pixelSize: 11; font.bold: true }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: window.liveHgeTargets.filter(function(row) { return row.active }).length
                                          + " / " + window.liveHgeTargets.length + " TARGETS"
                                    color: window.liveHgeTargets.some(function(row) { return row.active }) ? green : muted
                                    font.pixelSize: 10; font.bold: true
                                }
                            }
                            Label {
                                width: parent.width
                                text: {
                                    var active = window.liveHgeTargets.filter(function(row) { return row.active })
                                    if (active.length)
                                        return active[0].name + " · " + active[0].system
                                    if (window.liveHgeTargets.length)
                                        return "Monitoring " + window.liveHgeTargets.length + " missing HGE materials"
                                    return "No missing build material has a verified HGE source"
                                }
                                color: window.liveHgeTargets.some(function(row) { return row.active }) ? green : muted
                                font.pixelSize: 11; wrapMode: Text.WordWrap
                            }
                        }
                    }
                    CockpitButton {
                        text: window.t("common.refresh", "REFRESH NOW")
                        selected: true
                        Layout.fillWidth: true
                        Layout.preferredHeight: 42
                        onClicked: cockpit.refresh()
                    }
                }
            }
        }
    }
        }
    }

    Loader {
        id: pageLoader1
        anchors.fill: parent
        active: window.currentPage === 1
        asynchronous: false
        sourceComponent: Component {
    ColumnLayout {
        objectName: "qa-page-wishlist"
        visible: true
        anchors.fill: parent
        anchors.leftMargin: sidebar.width + (window.compactSidebar ? 18 : 26)
        anchors.rightMargin: window.compactSidebar ? 18 : 26
        anchors.topMargin: window.compactSidebar ? 18 : 26
        anchors.bottomMargin: window.compactSidebar ? 18 : 26
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                objectName: "qa-header-wishlist"
                Label { text: window.t("wishlist.title", "BLUEPRINT WISHLIST"); color: textPrimary; font.pixelSize: 24; font.bold: true }
                Label {
                    text: window.tf("status.viewing", "VIEWING · %1", [cockpit.ship])
                          + (cockpit.activeShip
                             ? "  ·  FLYING · " + cockpit.activeShip : "")
                          + "  ·  LIVE INVENTORY COVERAGE"
                    color: muted; font.pixelSize: 13
                }
            }
            Item { Layout.fillWidth: true }
            ComboBox {
                id: wishlistPageShip
                Layout.preferredWidth: 330
                Layout.preferredHeight: 42
                model: cockpit.ships
                currentIndex: Math.max(0, cockpit.ships.indexOf(cockpit.ship))
                onActivated: cockpit.setSelectedShip(currentText)
                contentItem: Label {
                    leftPadding: 14
                    text: wishlistPageShip.displayText
                    color: textPrimary
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
                background: Rectangle {
                    radius: 12
                    color: inputBackground
                    border.width: wishlistPageShip.activeFocus ? 2 : 1
                    border.color: wishlistPageShip.activeFocus ? cyan : borderTone
                }
            }
            CockpitButton {
                visible: !!cockpit.activeShip
                         && (!cockpit.followActiveShip
                             || cockpit.ship !== cockpit.activeShip)
                text: window.t("operations.follow_current", "FOLLOW CURRENT")
                selected: true
                onClicked: cockpit.followCurrentShip()
            }
            Rectangle {
                width: 140; height: 36; radius: 18; color: active
                Label {
                    anchors.centerIn: parent
                text: window.tf("wishlist.pinned_count", "%1 PINNED", [cockpit.blueprints.length])
                    color: cyan; font.bold: true
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: cockpit.fleetStatus
                color: muted; font.pixelSize: 10
                elide: Text.ElideRight
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 14
            Repeater {
                model: [
                            {"title": window.t("wishlist.material_status", "MATERIAL STATUS"), "value": window.materialDisplay(
                         cockpit.materialStatus, cockpit.completion,
                         cockpit.completionReliable).status, "tone": cyan},
                            {"title": window.t("status.progress", "PROGRESS"), "value": window.localizedStatus(cockpit.planProgressStatus), "tone": green},
                            {"title": window.t("wishlist.blueprints", "BLUEPRINTS"), "value": cockpit.blueprints.length, "tone": green},
                            {"title": window.t("wishlist.missing_types", "MISSING TYPES"), "value": cockpit.missingKinds, "tone": orange}
                ]
                delegate: ShadowCard {
                    required property var modelData
                    required property int index
                    objectName: index === 0
                                ? "qa-card-wishlist" : ""
                    Layout.fillWidth: true
                    Layout.preferredHeight: 104
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 18
                        Label { text: modelData.title; color: muted; font.pixelSize: 11; font.bold: true }
                        Label { text: modelData.value; color: modelData.tone; font.pixelSize: 25; font.bold: true }
                    }
                }
            }
        }

        ShadowCard {
            Layout.fillWidth: true
            Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 20; spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                        Label { text: window.t("wishlist.pinned_builds", "PINNED BUILDS"); color: textPrimary; font.pixelSize: 17; font.bold: true }
                    Item { Layout.fillWidth: true }
                        Label { text: window.t("wishlist.journal_automatic", "Journal inventory · automatic"); color: green; font.pixelSize: 11; font.bold: true }
                }
                Rectangle {
                    visible: cockpit.relevantCraftTrackingIssues.length > 0
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible
                                            ? 30 + Math.min(2, cockpit.relevantCraftTrackingIssues.length) * 30
                                            : 0
                    radius: 8; color: errorBackground
                    border.width: 1; border.color: error
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 6; spacing: 4
                        Label {
                            Layout.fillWidth: true
                            text: window.t("wishlist.craft_pending", "CRAFT MATCH PENDING · ")
                                  + cockpit.relevantCraftTrackingIssues.length
                                  + window.t("wishlist.relevant_conflicts_suffix", " RELEVANT CONFLICT(S)")
                            color: error; font.pixelSize: 10; font.bold: true
                        }
                        ListView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true; spacing: 4
                            model: cockpit.relevantCraftTrackingIssues
                            ScrollBar.vertical: CockpitScrollBar {}
                            delegate: RowLayout {
                                width: ListView.view.width; height: 27; spacing: 6
                                Label {
                                    Layout.fillWidth: true
                                    text: (modelData.displayReasonCode || modelData.reasonCode || window.t("status.unmatched", "UNMATCHED"))
                                          + " · " + (modelData.timestamp || window.t("status.time_unknown", "TIME UNKNOWN"))
                                          + " · " + (modelData.module || window.t("status.module_unknown", "MODULE UNKNOWN"))
                                          + " / " + (modelData.slot || window.t("status.slot_unknown", "SLOT UNKNOWN"))
                                          + " · " + (modelData.blueprintName || window.t("status.blueprint_unknown", "BLUEPRINT UNKNOWN"))
                                          + " · " + (modelData.reason || window.t("status.no_safe_match", "No safe match"))
                                    color: error; font.pixelSize: 9; font.bold: true
                                    elide: Text.ElideRight
                                }
                                CockpitButton {
                                    text: window.t("common.dismiss", "DISMISS"); implicitHeight: 24
                                        helpText: window.t("wishlist.dismiss_craft_help", "Dismiss exactly this unmatched Journal craft")
                                    onClicked: cockpit.dismissCraftTrackingIssue(
                                        modelData.fingerprint || ""
                                    )
                                }
                            }
                        }
                    }
                }
                Rectangle {
                    visible: cockpit.unrelatedCraftTrackingIssues.length > 0
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible
                                            ? 32 + Math.min(2, cockpit.unrelatedCraftTrackingIssues.length) * 28
                                            : 0
                    radius: 8; color: panelRaised
                    border.width: 1; border.color: borderTone
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 6; spacing: 3
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: cockpit.unrelatedCraftTrackingIssues.length
                                      + window.t("wishlist.unrelated_crafts_suffix", " NEW · NO PLAN / UNRELATED CRAFT(S)")
                                color: muted; font.pixelSize: 10; font.bold: true
                            }
                            CockpitButton {
                                text: window.t("common.dismiss_all", "DISMISS ALL"); implicitHeight: 24
                                    helpText: window.t("wishlist.dismiss_ship_crafts_help", "Dismiss only no-plan or unmatched craft remnants for this ship")
                                onClicked: cockpit.dismissAllUnrelatedCraftIssues()
                            }
                        }
                        ListView {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            clip: true; spacing: 3
                            model: cockpit.unrelatedCraftTrackingIssues
                            ScrollBar.vertical: CockpitScrollBar {}
                            delegate: RowLayout {
                                width: ListView.view.width; height: 25; spacing: 6
                                Label {
                                    Layout.fillWidth: true
                                    text: (modelData.displayReasonCode || modelData.relevanceLabel || window.t("status.unrelated", "UNRELATED"))
                                          + " · " + (modelData.timestamp || window.t("status.time_unknown", "TIME UNKNOWN"))
                                          + " · " + (modelData.module || window.t("status.module_unknown", "MODULE UNKNOWN"))
                                          + " / " + (modelData.slot || window.t("status.slot_unknown", "SLOT UNKNOWN"))
                                          + " · " + (modelData.blueprintName || window.t("status.blueprint_unknown", "BLUEPRINT UNKNOWN"))
                                    color: muted; font.pixelSize: 9
                                    elide: Text.ElideRight
                                }
                                CockpitButton {
                                    text: window.t("common.dismiss", "DISMISS"); implicitHeight: 23
                                    onClicked: cockpit.dismissCraftTrackingIssue(
                                        modelData.fingerprint || ""
                                    )
                                }
                            }
                        }
                    }
                }
                Rectangle {
                    visible: cockpit.historicalCraftTrackingIssues.length > 0
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? 34 : 0
                    radius: 7; color: backgroundSecondary
                    border.width: 1; border.color: divider
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 7; spacing: 8
                        Label {
                            property var historicalIssue:
                                cockpit.historicalCraftTrackingIssues.length > 0
                                ? cockpit.historicalCraftTrackingIssues[0] : ({})
                            Layout.fillWidth: true
                            text: cockpit.historicalCraftTrackingIssues.length
                                  + " HISTORICAL CRAFT ISSUE(S) · "
                                  + (historicalIssue.timestamp || "TIME UNKNOWN")
                                  + " · " + (historicalIssue.module || "MODULE UNKNOWN")
                                  + " / " + (historicalIssue.slot || "SLOT UNKNOWN")
                                  + " · " + (historicalIssue.blueprintName || "BLUEPRINT UNKNOWN")
                            color: muted; font.pixelSize: 9
                            elide: Text.ElideRight
                        }
                        CockpitButton {
                            text: window.t("wishlist.dismiss_historical", "DISMISS ALL HISTORICAL")
                            implicitHeight: 24
                                    helpText: window.t("wishlist.dismiss_historical_help", "Dismiss only reviewed historical craft issues for this ship")
                            onClicked: cockpit.dismissAllHistoricalCraftIssues()
                        }
                    }
                }
                ListView {
                    id: wishlistList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 10
                    model: cockpit.blueprints
                    ScrollBar.vertical: CockpitScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        property bool materialsExpanded: window.wishlistMaterialsExpanded(modelData, index)
                        width: wishlistList.width - 12
                        height: 280 + (materialsExpanded
                                       ? (modelData.materialProgress || []).length * 66 : 0)
                                + (modelData.calculationWarning ? 34 : 0)
                        radius: 14
                        color: wishHover.containsMouse ? hover : panelRaised
                        border.width: 1
                        border.color: modelData.priority ? orange
                                    : window.materialDisplay(
                                          modelData.materialStatus,
                                          modelData.completion,
                                          modelData.completionReliable).status === "READY"
                                      ? success : borderTone
                        Behavior on color { enabled: !window.reducedMotion; ColorAnimation { duration: 140 } }
                        MouseArea { id: wishHover; anchors.fill: parent; hoverEnabled: true }
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 16; spacing: 7
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: modelData.module + " · " + modelData.blueprint
                                    color: textPrimary; font.pixelSize: 16; font.bold: true
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                }
                                Label {
                                    text: modelData.grade > 0 ? "G" + modelData.grade : "EXP"
                                    color: orange; font.bold: true
                                }
                            }
                            Flow {
                                Layout.fillWidth: true
                                spacing: 7
                                CockpitButton {
                                    visible: modelData.editable
                                    text: window.t("wishlist.edit", "EDIT")
                                        helpText: window.t("wishlist.edit_help", "Edit this pinned engineering plan")
                                    implicitHeight: 32
                                    onClicked: {
                                        cockpit.editPinnedPlan(modelData.index)
                                        window.currentPage = 3
                                    }
                                }
                                CockpitButton {
                                    visible: modelData.editable
                                    text: window.t("wishlist.duplicate", "DUPLICATE")
                                        helpText: window.t("wishlist.duplicate_help", "Create a separate copy of this plan")
                                    implicitHeight: 32
                                    onClicked: cockpit.duplicatePinnedPlan(modelData.index)
                                }
                                CockpitButton {
                                    visible: modelData.editable && !!modelData.planId
                                    text: cockpit.armedPlanId === modelData.planId
                                          ? "✓ TRACK NEXT" : "TRACK NEXT"
                                    selected: cockpit.armedPlanId === modelData.planId
                                    implicitHeight: 32
                                        helpText: window.t("wishlist.track_help", "Apply the next matching Journal craft to this plan")
                                    onClicked: cockpit.armPlanForNextCraft(modelData.planId)
                                }
                                CockpitButton {
                                    visible: modelData.editable && !!modelData.planId
                                             && modelData.targetStatus !== "completed"
                                    text: modelData.priority
                                          ? "✓ PRIORISIERT" : "TRACK NOW"
                                    selected: modelData.priority
                                    implicitHeight: 32
                                    helpText: modelData.priority
                                              ? "Priorität aufheben und Fair-Share wiederherstellen"
                                              : "Diesen Plan bis zum Abschluss priorisieren"
                                    onClicked: cockpit.prioritizePinnedPlan(modelData.planId)
                                }
                                CockpitButton {
                                    text: window.t("wishlist.remove", "REMOVE PLAN")
                                        helpText: window.t("wishlist.remove_help", "Remove this plan from the current ship wishlist")
                                    implicitHeight: 32
                                    onClicked: cockpit.removePinnedPlan(modelData.index)
                                }
                                ComboBox {
                                    id: moveTarget
                                    visible: cockpit.ships.length > 1
                                    implicitWidth: 155; implicitHeight: 32
                                    model: cockpit.ships.filter(function(name) { return name !== cockpit.ship })
                                }
                                CockpitButton {
                                    visible: cockpit.ships.length > 1
                                    text: window.t("wishlist.move", "MOVE")
                                        helpText: window.t("wishlist.move_help", "Move this plan to the selected ship")
                                    implicitHeight: 32
                                    onClicked: cockpit.movePinnedPlan(modelData.index, moveTarget.currentText)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: modelData.instance || window.t("wishlist.legacy_plan", "Legacy plan")
                                    color: cyan; font.pixelSize: 11; font.bold: true
                                }
                                Label {
                                    visible: !!modelData.experimental
                                    text: window.tf("status.experimental", "EXPERIMENTAL · %1", [modelData.experimental])
                                    color: green; font.pixelSize: 11; font.bold: true
                                }
                                Item { Layout.fillWidth: true }
                                Label { text: modelData.engineer; color: muted; font.pixelSize: 11 }
                            }
                            RowLayout {
                                visible: modelData.craftsPlanned > 0
                                Layout.fillWidth: true
                                Label {
                                    text: window.t("wishlist.journal_crafts", "JOURNAL CRAFTS") + "  "
                                          + modelData.craftsDone + " / " + modelData.craftsPlanned
                                    color: modelData.craftsDone >= modelData.craftsPlanned
                                           ? green : cyan
                                    font.pixelSize: 10; font.bold: true
                                }
                                Label {
                                    visible: !!modelData.experimental
                                    text: modelData.experimentalComplete
                                          ? "✓ EXPERIMENTAL APPLIED"
                                          : "○ EXPERIMENTAL PENDING"
                                    color: modelData.experimentalComplete ? green : orange
                                    font.pixelSize: 10; font.bold: true
                                }
                                Item { Layout.fillWidth: true }
                            }
                            Label {
                                visible: modelData.bindingRequired
                                text: window.t("wishlist.binding_required", "⚠ BINDING REQUIRED · select the physical module slot")
                                color: error; font.pixelSize: 10; font.bold: true
                            }
                            Label {
                                visible: modelData.priority || modelData.deferred
                                text: modelData.priority
                                      ? "★ PRIORISIERT · MATERIALVORRANG · TRACK NOW"
                                      : "ZURÜCKGESTELLT · BLEIBT IM GESAMTBEDARF"
                                color: modelData.priority ? orange : muted
                                font.pixelSize: 10; font.bold: true
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: window.t("common.material_prefix", "MATERIAL · ") + window.materialDisplay(
                                              modelData.materialStatus,
                                              modelData.completion,
                                              modelData.completionReliable).status
                                    color: window.materialDisplay(
                                               modelData.materialStatus,
                                               modelData.completion,
                                               modelData.completionReliable).status === "READY"
                                           ? green : orange
                                    font.pixelSize: 10; font.bold: true
                                }
                                Label {
                                    text: window.tf("status.progress", "PROGRESS · %1", [modelData.progressStatus])
                                    color: modelData.progressStatus === "COMPLETE" ? green : cyan
                                    font.pixelSize: 10; font.bold: true
                                }
                                Item { Layout.fillWidth: true }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    visible: modelData.targetGrade > 0
                                    text: window.tf("status.grade", "GRADE · %1", [modelData.gradeStatusLabel])
                                    color: cyan; font.pixelSize: 9
                                }
                                Label {
                                    visible: !!modelData.experimental
                                    text: window.tf("status.experimental", "EXPERIMENTAL · %1", [modelData.experimentalStatusLabel])
                                    color: green; font.pixelSize: 9
                                }
                                Item { Layout.fillWidth: true }
                            }
                            Label {
                                visible: !!modelData.craftReason
                                text: window.tf("status.last_journal", "LAST JOURNAL UPDATE · %1", [modelData.craftReason])
                                color: muted; font.pixelSize: 10
                                Layout.fillWidth: true; elide: Text.ElideRight
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: window.t("wishlist.material_readiness", "MATERIAL READINESS")
                                    color: muted; font.pixelSize: 10; font.bold: true
                                }
                                Item { Layout.fillWidth: true }
                                CockpitButton {
                                    text: materialsExpanded
                                          ? window.t("materials.hide", "HIDE MATERIALS")
                                          : window.t("materials.show", "SHOW MATERIALS")
                                    helpText: materialsExpanded
                                              ? window.t("materials.collapse_details", "Collapse detailed material rows")
                                              : window.t("materials.show_progress", "Show detailed material progress")
                                    implicitHeight: 32
                                    onClicked: window.setWishlistMaterialsExpanded(
                                                   modelData, index, !materialsExpanded)
                                }
                                Label {
                                    text: Math.round(window.materialDisplay(
                                                         modelData.materialStatus,
                                                         modelData.completion,
                                                         modelData.completionReliable).completion * 100) + "%"
                                    color: window.materialDisplay(
                                               modelData.materialStatus,
                                               modelData.completion,
                                               modelData.completionReliable).completion >= 1
                                           ? green : cyan
                                    font.pixelSize: 13; font.bold: true
                                }
                            }
                            ModernProgress {
                                Layout.fillWidth: true
                                value: window.materialDisplay(
                                           modelData.materialStatus,
                                           modelData.completion,
                                           modelData.completionReliable).completion
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: window.tf("wishlist.materials_complete", "%1 of %2 materials complete · %3 missing",
                                                    [modelData.completeMaterialKinds,
                                                     modelData.totalMaterialKinds,
                                                     Math.max(0, modelData.totalMaterialKinds
                                                                 - modelData.completeMaterialKinds)])
                                    color: modelData.completeMaterialKinds === modelData.totalMaterialKinds
                                           ? green : textSecondary
                                    font.pixelSize: 11; font.bold: true
                                }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: window.tf("status.units", "%1 / %2 units", [modelData.covered, modelData.required])
                                    color: muted; font.pixelSize: 11
                                }
                            }
                            Repeater {
                                model: materialsExpanded
                                       ? (modelData.materialProgress || []) : []
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    height: 58
                                    radius: 9
                                    color: modelData.status === "ready" ? successBackground
                                         : modelData.status === "partial" ? warningBackground
                                         : errorBackground
                                    border.width: 1
                                    border.color: modelData.status === "ready" ? success
                                                : modelData.status === "partial" ? warning : error
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.margins: 9
                                        spacing: 10
                                        Label {
                                            text: modelData.category.toUpperCase()
                                            color: muted; font.pixelSize: 8; font.bold: true
                                            Layout.preferredWidth: 92
                                            elide: Text.ElideRight
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 3
                                            RowLayout {
                                                Layout.fillWidth: true
                                                Label {
                                                    text: modelData.name
                                                    color: textPrimary
                                                    font.pixelSize: 11; font.bold: true
                                                    Layout.fillWidth: true
                                                    elide: Text.ElideRight
                                                }
                                                Label {
                                                    visible: modelData.missing > 0
                                                             && modelData.sharedPlanCount > 1
                                                    text: window.tf("status.bottleneck", "BOTTLENECK · %1 PLANS", [modelData.sharedPlanCount])
                                                    color: orange; font.pixelSize: 8; font.bold: true
                                                }
                                            }
                                            ModernProgress {
                                                Layout.fillWidth: true
                                                implicitHeight: 7
                                                value: modelData.progress
                                                progressColor: modelData.status === "ready" ? green
                                                             : modelData.status === "partial" ? orange : error
                                            }
                                        }
                                        ColumnLayout {
                                            Layout.preferredWidth: 185
                                            spacing: 2
                                            Label {
                                                text: window.tf("status.have_need", "HAVE %1 / NEED %2", [modelData.have, modelData.need])
                                                color: modelData.status === "ready" ? green
                                                     : modelData.status === "partial" ? orange : error
                                                font.pixelSize: 10; font.bold: true
                                            }
                                            Label {
                                                text: modelData.missing === 0
                                                      ? "READY"
                                                      : "MISSING " + modelData.missing
                                                color: modelData.missing === 0 ? green
                                                     : modelData.status === "partial" ? orange : error
                                                font.pixelSize: 9; font.bold: true
                                            }
                                        }
                                    }
                                }
                            }
                            Label {
                                visible: !!modelData.calculationWarning
                                text: modelData.calculationWarning
                                color: error; font.pixelSize: 10; font.bold: true
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: modelData.missingKinds === 0 ? window.t("status.value.ready", "READY")
                                          : window.countLabel(modelData.missingKinds,
                                                              "MATERIAL TYPE", "MATERIAL TYPES") + " MISSING"
                                    color: modelData.missingKinds === 0 ? green : orange
                                    font.pixelSize: 11; font.bold: true
                                }
                            }
                        }
                    }
                    EmptyState {
                        anchors.centerIn: parent
                        visible: cockpit.blueprints.length === 0
                        symbol: "★"
                        title: window.t("wishlist.no_plans", "NO PLANS PINNED FOR THIS SHIP")
                        detail: window.t("wishlist.no_plans_help", "Open Engineering to choose a blueprint and pin a plan for this ship.")
                        tone: cyan
                        CockpitButton {
                            text: window.t("wishlist.open_engineering", "OPEN ENGINEERING")
                            Layout.alignment: Qt.AlignHCenter
                            onClicked: window.currentPage = 3
                        }
                    }
                }
            }
        }
    }
        }
    }

    Loader {
        id: pageLoader2
        anchors.fill: parent
        active: window.currentPage === 2
        asynchronous: false
        sourceComponent: Component {
    ColumnLayout {
        id: materialsPage
        objectName: "qa-page-materials"
        property bool neededOnly: window.materialsNeededOnlyState
        property bool farmMissing: window.materialsFarmMissingState
        property string statusFilter: window.materialsStatusFilterState
        property bool compactFilters: (window.width / Math.max(1.0, cockpit.uiScale)) < 1700
        onNeededOnlyChanged: window.materialsNeededOnlyState = neededOnly
        onFarmMissingChanged: window.materialsFarmMissingState = farmMissing
        onStatusFilterChanged: window.materialsStatusFilterState = statusFilter
        Component.onCompleted: {
            if (window.materialFarmMissingRequested) {
                farmMissing = true
                window.materialFarmMissingRequested = false
            }
        }
        property var farmMissingRows: cockpit.materials.filter(function(row) {
            return row.category === "Raw" && row.missing > 0
                && (materialSearch.text.length === 0
                    || row.name.toLowerCase().indexOf(
                        materialSearch.text.toLowerCase()) >= 0)
        }).sort(function(left, right) {
            if (left.grade !== right.grade)
                return right.grade - left.grade
            if (left.missing !== right.missing)
                return right.missing - left.missing
            return left.name.localeCompare(right.name)
        })
        visible: true
        anchors.fill: parent
        anchors.leftMargin: sidebar.width + (window.compactSidebar ? 18 : 26)
        anchors.rightMargin: window.compactSidebar ? 18 : 26
        anchors.topMargin: window.compactSidebar ? 18 : 26
        anchors.bottomMargin: window.compactSidebar ? 18 : 26
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                objectName: "qa-header-materials"
                Label { text: window.t("materials.title", "MATERIAL INVENTORY"); color: textPrimary; font.pixelSize: 24; font.bold: true }
                Label {
                text: window.tf("materials.inventory_summary", "%1 ENGINEERING MATERIALS · LIVE JOURNAL STOCK", [cockpit.materials.length])
                    color: muted; font.pixelSize: 13
                }
            }
            Item { Layout.fillWidth: true }
            TextField {
                id: materialSearch
                text: window.materialsSearchState
                onTextChanged: window.materialsSearchState = text
                Layout.preferredWidth: 340
                Layout.preferredHeight: 42
                placeholderText: window.t("materials.search", "Search material or category…")
                color: textPrimary
                placeholderTextColor: muted
                leftPadding: 16; rightPadding: 16
                background: Rectangle {
                    radius: 12; color: inputBackground
                    border.width: materialSearch.activeFocus ? 2 : 1
                    border.color: materialSearch.activeFocus ? cyan : borderTone
                }
            }
        }
        Item {
            Layout.fillWidth: true
            // Translated labels can be substantially wider than English. Give the
            // filter controls a second line on compact workspaces instead of
            // clipping them or shrinking their type.
            Layout.preferredHeight: materialsPage.compactFilters ? 84 : 44
            Label {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                visible: !materialsPage.compactFilters
                text: window.t("materials.show", "SHOW"); color: muted; font.pixelSize: 10; font.bold: true
            }
            Flow {
                id: materialFilterFlow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: materialsPage.compactFilters ? 0 : 64
                anchors.rightMargin: materialsPage.compactFilters ? 0 : 360
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                CockpitButton {
                    text: window.t("materials.needed", "NEEDED FOR BUILD")
                    selected: materialsPage.neededOnly
                    enabled: !materialsPage.farmMissing
                    onClicked: materialsPage.neededOnly = !materialsPage.neededOnly
                }
                CockpitButton {
                    text: window.t("materials.farm_missing", "FARM MISSING")
                    selected: materialsPage.farmMissing
                    onClicked: materialsPage.farmMissing = !materialsPage.farmMissing
                }
                Repeater {
                    visible: !materialsPage.farmMissing
                    model: [
                            {"key": "all", "label": window.t("common.all", "ALL")},
                            {"key": "missing", "label": window.t("common.missing", "MISSING")},
                            {"key": "ready", "label": window.t("common.ready", "READY")},
                            {"key": "surplus", "label": window.t("common.surplus", "SURPLUS")},
                            {"key": "tradeable", "label": window.t("common.tradeable", "TRADEABLE")}
                    ]
                    delegate: CockpitButton {
                        required property var modelData
                        text: modelData.label
                        selected: materialsPage.statusFilter === modelData.key
                        onClicked: materialsPage.statusFilter = modelData.key
                    }
                }
            }
            Label {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                visible: !materialsPage.compactFilters
                text: window.t("materials.stock_protected", "Protected build stock is never offered for trade.")
                color: green; font.pixelSize: 10; font.bold: true
            }
        }

        RowLayout {
            visible: !materialsPage.farmMissing
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            Repeater {
                model: ["Raw", "Manufactured", "Encoded"].concat(
                    cockpit.materials.some(function(row) {
                        return row.category === "unknown"
                    }) ? ["unknown"] : [])
                delegate: MaterialColumn {
                    required property string modelData
                    objectName: modelData === "Raw" ? "qa-card-materials" : ""
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    category: modelData
                    rows: cockpit.materials
                    query: materialSearch.text.toLowerCase()
                    neededOnly: materialsPage.neededOnly
                    statusFilter: materialsPage.statusFilter
                }
            }
        }
        ShadowCard {
            visible: materialsPage.farmMissing
            Layout.fillWidth: true
            Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: window.t("materials.raw_missing", "FARM MISSING · RAW MATERIALS")
                        color: accentSecondary; font.pixelSize: 16; font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: window.countLabel(materialsPage.farmMissingRows.length,
                                                "MISSING TYPE", "MISSING TYPES") + " · G4 FIRST"
                        color: muted; font.pixelSize: 11; font.bold: true
                    }
                }
                Label {
                    text: window.t("materials.raw_order", "Only current Wishlist demand · sorted by grade, then missing amount")
                    color: muted; font.pixelSize: 11
                }
                ListView {
                    id: farmMissingList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 8
                    model: materialsPage.farmMissingRows
                    ScrollBar.vertical: CockpitScrollBar {}
                    delegate: Rectangle {
                        id: farmRow
                        required property var modelData
                        property var source: modelData.farmSource || ({})
                        width: farmMissingList.width - 10
                        height: 118
                        radius: 12
                        color: farmMissingMouse.containsMouse ? hover : panelRaised
                        border.width: modelData.grade === 4 ? 2 : 1
                        border.color: modelData.grade === 4 ? accentSecondary : borderTone
                        MouseArea {
                            id: farmMissingMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: cockpit.selectMaterial(modelData.key)
                        }
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 14
                            ColumnLayout {
                                Layout.preferredWidth: 220
                                Label {
                                    text: window.tf("status.grade_short", "G%1", [modelData.grade]) + " · " + modelData.name
                                    color: textPrimary; font.pixelSize: 14; font.bold: true
                                }
                                Label {
                                    text: window.tf("status.missing_stock_prefix", "MISSING %1 · STOCK ", [modelData.missing])
                                          + modelData.have + " / " + modelData.need
                                    color: orange; font.pixelSize: 11; font.bold: true
                                }
                                Label {
                                    text: modelData.rawTraderCategory > 0
                                          ? "RAW TRADER CAT " + modelData.rawTraderCategory : ""
                                    color: muted; font.pixelSize: 10
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: (farmRow.source.system || window.t("materials.no_precise_system", "NO PRECISE SYSTEM"))
                                          + (farmRow.source.body ? " · " + farmRow.source.body : "")
                                    color: cyan; font.pixelSize: 13; font.bold: true
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                }
                                Label {
                                    text: farmRow.source.coordinates
                                          ? "COORDS · " + farmRow.source.coordinates
                                          : (farmRow.source.target || "Open details for source guidance")
                                    color: farmRow.source.coordinates ? green : muted
                                    font.pixelSize: 11; font.bold: true
                                }
                                Label {
                                    text: (farmRow.source.role
                                           ? farmRow.source.role.replace("_", " ") + " · "
                                           : "")
                                          + (farmRow.source.label || "SOURCE GUIDANCE")
                                    color: muted; font.pixelSize: 10
                                }
                            }
                            ColumnLayout {
                                Layout.preferredWidth: 154
                                spacing: 6
                                CockpitButton {
                                    Layout.fillWidth: true
                                    visible: !!farmRow.source.system
                                    text: window.t("common.copy_system", "COPY SYSTEM")
                                    implicitWidth: 154
                                    implicitHeight: 38
                                    onClicked: cockpit.copySystem(farmRow.source.system || "")
                                }
                                CockpitButton {
                                    Layout.fillWidth: true
                                    visible: !!farmRow.source.coordinates
                                    text: window.t("materials.copy_coords", "COPY COORDS")
                                    accentColor: green
                                    implicitWidth: 154
                                    implicitHeight: 38
                                    onClicked: cockpit.copyCoordinates(farmRow.source.coordinates || "")
                                }
                            }
                        }
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: materialsPage.farmMissingRows.length === 0
                        text: window.t("materials.raw_ready", "READY · NO RAW MATERIALS MISSING FOR CURRENT PLANS")
                        color: green; font.pixelSize: 13; font.bold: true
                    }
                }
            }
        }
    }
        }
    }

    Loader {
        id: pageLoader3
        anchors.fill: parent
        active: window.currentPage === 3
        asynchronous: false
        sourceComponent: Component {
    ColumnLayout {
        id: engineeringPage
        objectName: "qa-page-engineering"
        property string selectedCategory: window.engineeringCategoryState
        property string selectedModule: window.engineeringModuleState
        property string selectedInstalledSlot: ""
        property var selectedShipData: {
            var symbol = String(cockpit.selectedShipType || "")
            var normalized = symbol.toLowerCase().replace(/[^a-z0-9]/g, "")
            var rows = cockpit.engineeringShipCatalog || []
            for (var index = 0; index < rows.length; ++index) {
                var rowSymbol = String(rows[index].symbol || "")
                var rowName = String(rows[index].name || "")
                if (rowSymbol.toLowerCase() === symbol.toLowerCase()
                        || rowName.toLowerCase() === symbol.toLowerCase()
                        || rowSymbol.toLowerCase().replace(/[^a-z0-9]/g, "") === normalized
                        || rowName.toLowerCase().replace(/[^a-z0-9]/g, "") === normalized)
                    return rows[index]
            }
            return ({})
        }
        onSelectedCategoryChanged: window.engineeringCategoryState = selectedCategory
        onSelectedModuleChanged: window.engineeringModuleState = selectedModule
        property var categoryOrder: [
            "Core Internals", "Optional Internals", "Weapons / Hardpoints",
            "Utility Mounts", "Limpets / Controllers"
        ]
        property var moduleNames: {
            var names = []
            cockpit.blueprintCatalog.forEach(function(row) {
                if (row.category === engineeringPage.selectedCategory
                        && names.indexOf(row.module) < 0)
                    names.push(row.module)
            })
            return names.sort()
        }
        property var installedModules: {
            var rows = (cockpit.engineeringInstalledModules || []).filter(function(row) {
                return row.category === engineeringPage.selectedCategory
            })
            if (rows.length)
                return rows
            return engineeringPage.moduleNames.map(function(name) {
                return { module: name, slot: "", sizeRating: "", blueprintCount: 0 }
            })
        }
        property var selectedBlueprintChoices: cockpit.blueprintCatalog.filter(function(row) {
            return row.category === engineeringPage.selectedCategory
                && row.module === engineeringPage.selectedModule
                && (blueprintSearch.text.length === 0
                    || row.name.toLowerCase().indexOf(blueprintSearch.text.toLowerCase()) >= 0)
        })
        property string selectedModuleSlotLabel: {
            var rows = cockpit.moduleSlotOptions || []
            for (var index = 0; index < rows.length; ++index) {
                if (rows[index].slot === cockpit.selectedModuleSlot) {
                    var slot = String(rows[index].slot || "")
                    var label = String(rows[index].slotLabel || slot)
                    var optional = /^Slot(\d+)_Size(\d+)$/i.exec(slot)
                    if (optional)
                        return "OPTIONAL SLOT " + parseInt(optional[1])
                               + " · SIZE " + optional[2]
                    var utility = /^TinyHardpoint(\d+)$/i.exec(slot)
                    if (utility)
                        return "UTILITY SLOT " + parseInt(utility[1])
                    var hardpoint = /^(Small|Medium|Large|Huge)Hardpoint(\d+)$/i.exec(slot)
                    if (hardpoint)
                        return hardpoint[1].toUpperCase() + " HARDPOINT "
                               + parseInt(hardpoint[2])
                    return label
                }
            }
            return cockpit.selectedModuleSlot || "NO PHYSICAL SLOT"
        }
        function firstModuleFor(category) {
            var names = []
            cockpit.blueprintCatalog.forEach(function(row) {
                if (row.category === category && names.indexOf(row.module) < 0)
                    names.push(row.module)
            })
            names.sort()
            return names.length ? names[0] : ""
        }
        function selectPhysicalSlot(row) {
            if (!row || !row.engineerable)
                return
            selectedCategory = String(row.category || "")
            selectedModule = String(row.module || "")
            selectedInstalledSlot = String(row.slot || "")
            var choices = cockpit.blueprintCatalog.filter(function(blueprint) {
                return blueprint.category === selectedCategory
                    && blueprint.module === selectedModule
            })
            var currentMatches = choices.some(function(blueprint) {
                return blueprint.id === cockpit.selectedBlueprint.id
            })
            if (!currentMatches && choices.length)
                cockpit.selectBlueprint(choices[0].id)
            if (selectedInstalledSlot)
                cockpit.setSelectedModuleSlot(selectedInstalledSlot)
        }
        visible: true
        anchors.fill: parent
        anchors.leftMargin: sidebar.width + (window.compactSidebar ? 18 : 26)
        anchors.rightMargin: window.compactSidebar ? 18 : 26
        anchors.topMargin: window.compactSidebar ? 18 : 26
        anchors.bottomMargin: window.compactSidebar ? 18 : 26
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 104
            ColumnLayout {
                anchors.left: parent.left
                anchors.top: parent.top
                Label { text: window.t("engineering.title", "SHIP ENGINEERING"); color: textPrimary; font.pixelSize: 24; font.bold: true }
                Label { text: window.t("engineering.subtitle", "Ship → physical module → modification → target"); color: muted; font.pixelSize: 13 }
            }
            RowLayout {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                spacing: 8
                CockpitButton {
                    text: window.t("engineering.export", "EXPORT OUTFITTING")
                    enabled: cockpit.ships.length > 0
                    onClicked: cockpit.exportShipOutfitting()
                }
                CockpitButton {
                    text: window.t("engineering.import", "IMPORT BUILD")
                    enabled: cockpit.ships.length > 0
                    onClicked: {
                        buildImportTarget.currentIndex = Math.max(
                            0, cockpit.ships.indexOf(cockpit.ship))
                        buildImportDialog.open()
                    }
                }
            }
            TextField {
                id: blueprintSearch
                text: window.engineeringSearchState
                onTextChanged: window.engineeringSearchState = text
                anchors.right: parent.right
                anchors.top: parent.top
                width: 360
                height: 42
                Layout.preferredHeight: 42
                placeholderText: window.t("engineering.search", "Search modification…")
                font.pixelSize: 13
                color: textPrimary; placeholderTextColor: muted
                leftPadding: 16; rightPadding: 16
                background: Rectangle {
                    radius: 12; color: inputBackground
                    border.width: blueprintSearch.activeFocus ? 2 : 1
                    border.color: blueprintSearch.activeFocus ? cyan : borderTone
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 14
            ShadowCard {
                Layout.preferredWidth: 300
                Layout.fillHeight: true
                visible: !window.narrowWorkspace
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 12
                    Label {
                        text: window.t("engineering.active_ship", "ACTIVE SHIP")
                        color: cyan; font.pixelSize: 13; font.bold: true
                    }
                    ComboBox {
                        id: engineeringShipSelector
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44
                        model: cockpit.ships
                        currentIndex: Math.max(0, cockpit.ships.indexOf(cockpit.ship))
                        onActivated: {
                            engineeringPage.selectedInstalledSlot = ""
                            cockpit.setSelectedShip(currentText)
                        }
                        contentItem: Label {
                            leftPadding: 12
                            text: engineeringShipSelector.displayText
                            color: textPrimary
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                        }
                        background: Rectangle {
                            radius: 9; color: inputBackground
                            border.width: engineeringShipSelector.activeFocus ? 2 : 1
                            border.color: engineeringShipSelector.activeFocus ? cyan : borderTone
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 270
                        radius: 12; color: backgroundSecondary
                        border.width: 1; border.color: borderTone
                        clip: true
                        Image {
                            anchors.centerIn: parent
                            // The source schematics share a generous 1200x800
                            // safety canvas. Compensate for that transparent
                            // perimeter so the actual hull uses this panel.
                            width: parent.width * 1.58
                            height: parent.height * 1.58
                            source: engineeringPage.selectedShipData.symbol
                                    ? "assets/ships/" + engineeringPage.selectedShipData.symbol + ".svg"
                                    : ""
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                        }
                        Label {
                            anchors.centerIn: parent
                            visible: !engineeringPage.selectedShipData.symbol
                            text: window.t("engineering.no_schematic", "NO SHIP SCHEMATIC")
                            color: muted; font.pixelSize: 11; font.bold: true
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: cockpit.ship
                        color: textPrimary; font.pixelSize: 17; font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                    }
                    Label {
                        Layout.fillWidth: true
                        text: (engineeringPage.selectedShipData.manufacturer || window.t("engineering.unknown_manufacturer", "UNKNOWN MANUFACTURER"))
                              + " · " + String(engineeringPage.selectedShipData.size || "").toUpperCase()
                        color: muted; font.pixelSize: 10; font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2; columnSpacing: 8; rowSpacing: 8
                        Repeater {
                            model: [
                                    {label: window.t("engineering.max_speed", "MAX SPEED"), value: (engineeringPage.selectedShipData.maximumSpeed || "—") + " m/s"},
                                    {label: window.t("engineering.boost", "BOOST"), value: (engineeringPage.selectedShipData.boost || "—") + " m/s"},
                                    {label: window.t("engineering.jump_range", "JUMP RANGE"), value: cockpit.selectedShipStats.jumpRange !== null
                                        && cockpit.selectedShipStats.jumpRange !== undefined
                                        ? Number(cockpit.selectedShipStats.jumpRange).toFixed(1) + " LY" : "—"},
                                    {label: window.t("engineering.unladen_mass", "UNLADEN MASS"), value: cockpit.selectedShipStats.unladenMass !== null
                                        && cockpit.selectedShipStats.unladenMass !== undefined
                                        ? Number(cockpit.selectedShipStats.unladenMass).toFixed(1) + " t" : "—"},
                                    {label: window.t("engineering.cargo", "CARGO"), value: cockpit.selectedShipStats.cargoCapacity !== null
                                        && cockpit.selectedShipStats.cargoCapacity !== undefined
                                        ? cockpit.selectedShipStats.cargoCapacity + " t" : "—"},
                                    {label: window.t("engineering.engineerable", "ENGINEERABLE"), value: cockpit.engineeringInstalledModules.length}
                            ]
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true; Layout.preferredHeight: 64
                                radius: 9; color: panelRaised
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 9; spacing: 2
                                    Label { text: modelData.value; color: textPrimary; font.pixelSize: 15; font.bold: true }
                                    Label { text: modelData.label; color: muted; font.pixelSize: 9; font.bold: true }
                                }
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: window.t("engineering.ship_help", "Select any ship in your fleet to plan without switching ships in-game.")
                        color: muted; font.pixelSize: 10; wrapMode: Text.WordWrap
                    }
                    Item { Layout.fillHeight: true }
                }
            }
            ShadowCard {
                Layout.preferredWidth: window.compactSidebar ? 600 : 650
                Layout.fillWidth: true
                Layout.fillHeight: true
                RowLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 12
                    property var leftSlots: cockpit.engineeringShipSlots.filter(function(row) {
                        return row.group === "CORE INTERNALS" || row.group === "OPTIONAL INTERNALS"
                    })
                    property var rightSlots: cockpit.engineeringShipSlots.filter(function(row) {
                        return row.group === "HARDPOINTS" || row.group === "UTILITY MOUNTS"
                    })
                    Repeater {
                        model: [parent.leftSlots, parent.rightSlots]
                        delegate: ListView {
                            required property var modelData
                            Layout.fillWidth: true; Layout.fillHeight: true
                            clip: true; spacing: 4
                            model: modelData
                            // The two slot columns normally fit without a
                            // scrollbar. Keep wheel/touch scrolling available
                            // for short windows without drawing permanent rails.
                            ScrollBar.vertical: CockpitScrollBar {
                                policy: ScrollBar.AlwaysOff
                            }
                            section.property: "group"
                            section.criteria: ViewSection.FullString
                            section.delegate: Label {
                                required property string section
                                width: ListView.view.width - 8; height: 30
                                verticalAlignment: Text.AlignVCenter
                                text: section; color: cyan
                                font.pixelSize: 12; font.bold: true
                            }
                            delegate: Rectangle {
                                required property var modelData
                                width: ListView.view.width - 8
                                height: (modelData.moduleChange || modelData.planPending) ? 52 : 38
                                radius: 8
                                property bool exactSelection:
                                    engineeringPage.selectedInstalledSlot === String(modelData.slot || "")
                                color: exactSelection ? active
                                     : slotMouse.containsMouse ? hover : panelRaised
                                border.width: exactSelection ? 2 : 1
                                border.color: exactSelection ? cyan : borderTone
                                Rectangle {
                                    anchors.left: parent.left; anchors.leftMargin: 7
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 30; height: 26; radius: 6
                                    color: backgroundSecondary
                                    Label {
                                        anchors.centerIn: parent
                                        text: modelData.sizeRating || modelData.slotBadge
                                        color: cyan; font.pixelSize: 11; font.bold: true
                                    }
                                }
                                Label {
                                    anchors.left: parent.left; anchors.leftMargin: 45
                                    anchors.right: slotStatus.left; anchors.rightMargin: 6
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.verticalCenterOffset: (modelData.moduleChange || modelData.planPending) ? -8 : 0
                                    text: modelData.empty
                                          ? (modelData.restriction
                                             ? String(modelData.restriction).replace(/([A-Z])/g, " $1").toUpperCase()
                                             : "EMPTY")
                                          : modelData.module
                                    color: modelData.empty ? textDisabled : textPrimary
                                    font.pixelSize: 11; font.bold: !modelData.empty
                                    elide: Text.ElideRight
                                }
                                Label {
                                    visible: modelData.moduleChange || modelData.planPending
                                    anchors.left: parent.left; anchors.leftMargin: 45
                                    anchors.right: parent.right; anchors.rightMargin: 8
                                    anchors.bottom: parent.bottom; anchors.bottomMargin: 5
                                    text: modelData.moduleChange
                                          ? window.t("engineering.install_module", "INSTALL")
                                            + ": "
                                            + (modelData.desiredSizeRating
                                               ? modelData.desiredSizeRating + " " : "")
                                            + modelData.desiredModule
                                          : window.t("engineering.target_plan", "TARGET")
                                            + ": G" + modelData.planTargetGrade
                                            + " · " + modelData.planBlueprint
                                            + (modelData.planExperimental
                                               ? " · " + modelData.planExperimental : "")
                                    color: orange; font.pixelSize: 9; font.bold: true
                                    elide: Text.ElideRight
                                }
                                Label {
                                    id: slotStatus
                                    anchors.right: parent.right; anchors.rightMargin: 9
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.moduleChange ? "↻"
                                          : modelData.engineered
                                          ? "🔧 G" + modelData.engineeringGrade
                                          : modelData.engineerable ? "PLAN ›" : ""
                                    color: modelData.moduleChange ? orange
                                         : modelData.engineered ? orange
                                         : modelData.engineerable ? green : muted
                                    font.pixelSize: 9; font.bold: true
                                }
                                MouseArea {
                                    id: slotMouse
                                    anchors.fill: parent; hoverEnabled: true
                                    enabled: modelData.engineerable
                                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: engineeringPage.selectPhysicalSlot(modelData)
                                }
                            }
                        }
                    }
                }
            }
            ShadowCard {
                Layout.preferredWidth: window.compactSidebar ? 520 : 650
                Layout.fillWidth: true
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 9
                    Label {
                        visible: !cockpit.selectedBlueprint.id
                        text: window.t("engineering.select_blueprint", "SELECT A BLUEPRINT FROM THE CATALOG")
                        color: muted; font.pixelSize: 15; font.bold: true
                        Layout.alignment: Qt.AlignHCenter
                    }
                    RowLayout {
                        visible: !!cockpit.selectedBlueprint.id
                                 && engineeringPage.selectedBlueprintChoices.length > 0
                        Layout.fillWidth: true
                        Label {
                            text: window.t("engineering.modification", "MODIFICATION")
                            color: orange; font.pixelSize: 11; font.bold: true
                        }
                        ComboBox {
                            id: engineeringModificationSelector
                            Layout.fillWidth: true; Layout.preferredHeight: 42
                            model: engineeringPage.selectedBlueprintChoices
                            textRole: "name"
                            currentIndex: {
                                for (var index = 0; index < model.length; ++index) {
                                    if (model[index].id === cockpit.selectedBlueprint.id)
                                        return index
                                }
                                return model.length ? 0 : -1
                            }
                            onActivated: {
                                if (currentIndex < 0 || currentIndex >= model.length)
                                    return
                                cockpit.selectBlueprint(model[currentIndex].id)
                                if (engineeringPage.selectedInstalledSlot)
                                    cockpit.setSelectedModuleSlot(engineeringPage.selectedInstalledSlot)
                            }
                            contentItem: Label {
                                leftPadding: 12
                                text: engineeringModificationSelector.displayText
                                color: textPrimary; verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                            }
                            background: Rectangle {
                                radius: 9; color: backgroundSecondary
                                border.width: 1
                                border.color: engineeringModificationSelector.activeFocus ? cyan : borderTone
                            }
                        }
                    }
                    ScrollView {
                        id: engineeringDetailScroll
                        visible: !!cockpit.selectedBlueprint.id
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ColumnLayout {
                            id: blueprintDetail
                            width: engineeringDetailScroll.availableWidth
                            spacing: 7
                            property var guideData: (
                            cockpit.selectedBlueprint.grades
                            && cockpit.selectedBlueprint.grades.length >= cockpit.targetGrade
                        ) ? cockpit.selectedBlueprint.grades[cockpit.targetGrade - 1] : ({})
                        property var selectedExperimentalDetails: {
                            var rows = cockpit.selectedBlueprint.experimentals || []
                            for (var index = 0; index < rows.length; ++index) {
                                if (rows[index].id === cockpit.selectedExperimentalId)
                                    return rows[index]
                            }
                            return ({})
                        }
                        Label {
                            text: (cockpit.selectedBlueprint.module || "") + " · "
                                  + (cockpit.selectedBlueprint.name || "")
                            color: textPrimary; font.pixelSize: 23; font.bold: true
                            Layout.fillWidth: true; elide: Text.ElideRight
                        }
                        Label {
                            text: cockpit.selectedBlueprint.engineers || ""
                            color: muted; font.pixelSize: 13
                            Layout.fillWidth: true; elide: Text.ElideRight
                        }
                        Rectangle {
                            visible: !!cockpit.selectedBlueprint.installedEngineeringKnown
                            Layout.fillWidth: true; Layout.preferredHeight: 42
                            radius: 10
                            color: cockpit.selectedBlueprint.installedMatchesSelection
                                   ? successBackground : panelRaised
                            border.width: 1
                            border.color: cockpit.selectedBlueprint.installedMatchesSelection
                                          ? green : orange
                            Label {
                                anchors.left: parent.left; anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 12
                                text: window.t("status.installed_prefix", "🔧 INSTALLED · ")
                                      + cockpit.selectedBlueprint.installedBlueprint
                                      + " · G" + cockpit.selectedBlueprint.installedGrade
                                      + (cockpit.selectedBlueprint.installedExperimentalEffect
                                         ? " · " + cockpit.selectedBlueprint.installedExperimentalEffect : "")
                                      + (cockpit.selectedBlueprint.installedMatchesSelection
                                         ? "" : " · SELECTED BLUEPRINT WOULD REPLACE THIS")
                                color: cockpit.selectedBlueprint.installedMatchesSelection
                                       ? green : orange
                                font.pixelSize: 11; font.bold: true
                                elide: Text.ElideRight
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.max(64, modificationGuideText.implicitHeight + 22)
                            radius: 12; color: active
                            border.width: 1; border.color: accentSecondary
                            Label {
                                id: modificationGuideText
                                anchors.left: parent.left; anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 14
                                text: window.tf("status.what_it_does", "WHAT IT DOES · %1", [blueprintDetail.guideData.description || ""])
                                color: textSecondary; font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            Rectangle {
                                Layout.fillWidth: true; Layout.preferredHeight: 32
                                radius: 9
                                color: cockpit.selectedModuleSlot ? active : panelRaised
                                border.width: 1
                                border.color: cockpit.selectedModuleSlot ? cyan : orange
                                Label {
                                    anchors.fill: parent
                                    anchors.leftMargin: 12; anchors.rightMargin: 12
                                    text: cockpit.selectedModuleSlot
                                          ? "BOUND TO · "
                                            + engineeringPage.selectedModuleSlotLabel
                                            + " · " + engineeringPage.selectedModule.toUpperCase()
                                          : "NOT YET BOUND · JOURNAL CONFIRMATION REQUIRED"
                                    color: cockpit.selectedModuleSlot ? cyan : orange
                                    font.pixelSize: 10; font.bold: true
                                    verticalAlignment: Text.AlignVCenter
                                    elide: Text.ElideRight
                                }
                            }
                            CockpitButton {
                                visible: cockpit.editingPlanIndex >= 0
                                Layout.preferredWidth: 104
                                text: window.t("engineering.cancel_edit", "CANCEL EDIT")
                                onClicked: cockpit.cancelPlanEdit()
                            }
                        }
                        Label { text: window.t("engineering.plan_mode", "PLAN MODE"); color: cyan; font.pixelSize: 12; font.bold: true }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 6
                            CockpitButton { Layout.fillWidth: true; font.pixelSize: 10; text: window.t("engineering.grade_only", "GRADE ONLY"); selected: cockpit.planMode === "grade_only"; onClicked: cockpit.setPlanMode("grade_only") }
                            CockpitButton { Layout.fillWidth: true; font.pixelSize: 10; text: window.t("engineering.experimental_only", "EXPERIMENTAL ONLY"); selected: cockpit.planMode === "experimental_only"; onClicked: cockpit.setPlanMode("experimental_only") }
                            CockpitButton { Layout.fillWidth: true; font.pixelSize: 10; text: window.t("engineering.combined", "GRADE + EXPERIMENTAL"); selected: cockpit.planMode === "combined"; onClicked: cockpit.setPlanMode("combined") }
                        }
                        GridLayout {
                            visible: cockpit.planMode !== "experimental_only"
                            Layout.fillWidth: true
                            columns: width >= 620 ? 2 : 1
                            columnSpacing: 16; rowSpacing: 7
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 6
                                Label {
                                    text: window.t("engineering.current_grade", "GRADE · CURRENT GRADE")
                                    color: cyan; font.pixelSize: 12; font.bold: true
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    Repeater {
                                        model: (cockpit.selectedBlueprint.maxGrade || 0) + 1
                                        delegate: CockpitButton {
                                            required property int index
                                            Layout.fillWidth: true
                                text: index === 0 ? window.t("status.value.none", "NONE") : "G" + index
                                            selected: cockpit.currentGrade === index
                                            enabled: !cockpit.editingGradeComplete
                                            implicitWidth: index === 0 ? 62 : 48
                                            implicitHeight: 38
                                            onClicked: cockpit.setCurrentGrade(index)
                                        }
                                    }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 6
                                Label {
                                    text: window.t("engineering.target_grade", "TARGET GRADE")
                                    color: cyan; font.pixelSize: 12; font.bold: true
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    Repeater {
                                        model: cockpit.selectedBlueprint.maxGrade || 0
                                        delegate: CockpitButton {
                                            required property int index
                                            property int gradeValue: index + 1
                                            Layout.fillWidth: true
                                            text: window.tf("status.grade_short", "G%1", [gradeValue])
                                            selected: cockpit.targetGrade === gradeValue
                                            enabled: !cockpit.editingGradeComplete
                                            implicitWidth: 48; implicitHeight: 38
                                            onClicked: cockpit.setTargetGrade(gradeValue)
                                        }
                                    }
                                }
                            }
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                        Label { visible: cockpit.planMode !== "experimental_only"; text: window.t("engineering.target_ingredients", "TARGET-GRADE INGREDIENTS"); color: orange; font.pixelSize: 12; font.bold: true }
                        Flow {
                            visible: cockpit.planMode !== "experimental_only"
                            Layout.fillWidth: true
                            spacing: 6
                            property var targetData: blueprintDetail.guideData
                            Repeater {
                                model: parent.targetData.ingredients || []
                                delegate: Rectangle {
                                    required property var modelData
                                    width: ingredientText.implicitWidth + 24; height: 36; radius: 9
                                    color: modelData.missing > 0 ? errorBackground : successBackground
                                    border.width: 1
                                    border.color: modelData.missing > 0 ? error : success
                                    Label {
                                        id: ingredientText; anchors.centerIn: parent
                                        text: modelData.name + "  " + modelData.have + "/" + modelData.need
                                        color: modelData.missing > 0 ? orange : green
                                        font.pixelSize: 12; font.bold: true
                                    }
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            Label {
                                text: window.t("engineering.engineer", "ENGINEER")
                                color: cyan; font.pixelSize: 11; font.bold: true
                            }
                            ComboBox {
                                id: engineeringEngineerSelector
                                Layout.fillWidth: true; Layout.preferredHeight: 38
                                model: cockpit.selectedBlueprint.engineerOptions || []
                                textRole: "name"
                                currentIndex: {
                                    for (var index = 0; index < model.length; ++index) {
                                        if (model[index].name === cockpit.selectedEngineer)
                                            return index
                                    }
                                    return model.length ? 0 : -1
                                }
                                onActivated: {
                                    if (currentIndex >= 0 && currentIndex < model.length)
                                        cockpit.setSelectedEngineer(model[currentIndex].name)
                                }
                                contentItem: Label {
                                    leftPadding: 12
                                    text: engineeringEngineerSelector.currentIndex >= 0
                                          ? engineeringEngineerSelector.model[engineeringEngineerSelector.currentIndex].name
                                            + " · " + engineeringEngineerSelector.model[engineeringEngineerSelector.currentIndex].system
                                            + " · G" + engineeringEngineerSelector.model[engineeringEngineerSelector.currentIndex].capabilityGrade
                                          : "NO ENGINEER AVAILABLE"
                                    color: textPrimary; verticalAlignment: Text.AlignVCenter
                                    elide: Text.ElideRight
                                }
                                background: Rectangle {
                                    radius: 9; color: backgroundSecondary
                                    border.width: 1
                                    border.color: engineeringEngineerSelector.activeFocus ? cyan : borderTone
                                }
                            }
                        }
                        Label {
                            visible: cockpit.planMode !== "grade_only"
                                     && (cockpit.selectedBlueprint.experimentals || []).length > 0
                            text: window.t("engineering.available_experimentals", "AVAILABLE EXPERIMENTALS · SELECT DIRECTLY")
                            color: green; font.pixelSize: 11; font.bold: true
                        }
                        GridLayout {
                            visible: cockpit.planMode !== "grade_only"
                                     && (cockpit.selectedBlueprint.experimentals || []).length > 0
                            Layout.fillWidth: true
                            columns: width >= 920 ? 3 : width >= 580 ? 2 : 1
                            columnSpacing: 7; rowSpacing: 7
                            Repeater {
                                model: cockpit.selectedBlueprint.experimentals || []
                                delegate: Rectangle {
                                    required property var modelData
                                    property bool selectedEffect:
                                        String(modelData.id || "") === cockpit.selectedExperimentalId
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 66
                                    radius: 9
                                    color: selectedEffect ? successBackground
                                                          : panelRaised
                                    border.width: selectedEffect ? 2 : 1
                                    border.color: selectedEffect ? green : borderTone
                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 9; spacing: 2
                                        Label {
                                            Layout.fillWidth: true
                                            text: modelData.name || window.t("status.experimental", "EXPERIMENTAL")
                                            color: parent.parent.selectedEffect ? green : textPrimary
                                            font.pixelSize: 11; font.bold: true
                                            elide: Text.ElideRight
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: window.tf("status.benefit", "BENEFIT · %1", [modelData.benefits || window.t("engineering.no_benefit", "No listed benefit")])
                                            color: green; font.pixelSize: 9
                                            elide: Text.ElideRight
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: window.tf("status.tradeoff", "TRADE-OFF · %1", [modelData.tradeoffs || window.t("engineering.no_drawback", "No listed drawback")])
                                            color: orange; font.pixelSize: 9
                                            elide: Text.ElideRight
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent; hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: cockpit.setSelectedExperimental(modelData.id || "")
                                    }
                                }
                            }
                        }
                        Rectangle {
                            visible: cockpit.craftConfirmation.length > 0
                            Layout.fillWidth: true
                            height: 72; radius: 11
                            color: successBackground; border.width: 1; border.color: green
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 11; spacing: 3
                                Label {
                                    text: window.t("engineering.craft_confirmed", "✓ LAST JOURNAL CRAFT CONFIRMED")
                                    color: green; font.pixelSize: 10; font.bold: true
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: cockpit.recentCrafts.length
                                          ? cockpit.recentCrafts[0].summary : ""
                                    color: textPrimary; font.pixelSize: 11
                                    elide: Text.ElideRight
                                }
                            }
                        }
                        Label {
                            text: cockpit.planMode === "combined"
                                  ? window.t("engineering.sequence_combined", "SEQUENCE · EXPERIMENTAL AFTER GRADE COMPLETION")
                                  : cockpit.planMode === "experimental_only"
                                  ? window.t("engineering.sequence_experimental", "EXPERIMENTAL IS TRACKED INDEPENDENTLY")
                                  : window.t("engineering.sequence_grade", "GRADE IS TRACKED WITHOUT EXPERIMENTAL")
                            color: cyan; font.pixelSize: 12; font.bold: true
                        }
                        Label {
                            text: cockpit.engineeringStatus
                            color: orange; font.pixelSize: 11; font.bold: true
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                        }
                        }
                    }
                    CockpitButton {
                        visible: !!cockpit.selectedBlueprint.id
                        text: cockpit.editingPlanIndex >= 0
                              ? window.t("engineering.save_plan", "SAVE MODULE PLAN")
                              : cockpit.planMode === "experimental_only"
                              ? window.t("engineering.pin_experimental", "PIN EXPERIMENTAL EFFECT")
                              : cockpit.planMode === "combined"
                              ? window.t("engineering.pin_combined", "PIN GRADE + EXPERIMENTAL PLAN")
                              : window.t("engineering.pin_grade", "PIN GRADE PLAN")
                        enabled: cockpit.canPinEngineeringPlan
                        selected: enabled
                        Layout.fillWidth: true
                        Layout.preferredHeight: 48
                        onClicked: cockpit.pinEngineeringPlan()
                    }
                }
            }
        }
    }
        }
    }

    Loader {
        id: pageLoader4
        anchors.fill: parent
        active: window.currentPage === 4
        asynchronous: false
        sourceComponent: Component {
    ColumnLayout {
        id: engineersPage
        objectName: "qa-page-engineers"
        visible: true
        anchors.fill: parent
        anchors.leftMargin: sidebar.width + (window.compactSidebar ? 18 : 26)
        anchors.rightMargin: window.compactSidebar ? 18 : 26
        anchors.topMargin: window.compactSidebar ? 18 : 26
        anchors.bottomMargin: window.compactSidebar ? 18 : 26
        spacing: 14
        property string query: engineerSearch.text.toLowerCase()
        property string statusFilter: window.engineersStatusState
        property string brokerFilter: window.engineersBrokerState
        property bool unlockMode: window.engineersUnlockState
        property bool guardianMode: window.engineersGuardianState
        property string selectedEngineerName: window.selectedEngineerState
        property string selectedGuardianName: window.selectedGuardianState
        onBrokerFilterChanged: window.engineersBrokerState = brokerFilter
        onUnlockModeChanged: window.engineersUnlockState = unlockMode
        onGuardianModeChanged: window.engineersGuardianState = guardianMode
        onSelectedEngineerNameChanged: window.selectedEngineerState = selectedEngineerName
        onSelectedGuardianNameChanged: window.selectedGuardianState = selectedGuardianName
        Component.onCompleted: {
            if (Object.keys(window.guardianPageRequest).length) {
                unlockMode = false
                guardianMode = true
                brokerFilter = window.guardianPageRequest.brokerFilter || "ALL"
                selectedGuardianName = window.guardianPageRequest.selectedGuardianName || ""
                window.guardianPageRequest = ({})
            }
        }
        function toggleTechBrokerTrack(row) {
            if (!row || !row.name)
                return
            const enabling = !row.isTracked
            cockpit.trackTechBrokerUnlock(row.name, row.brokerSubtype)
            if (enabling)
                window.requestMaterialFarmMissing(false)
        }
        function openTrackedFarmMissing() {
            window.requestMaterialFarmMissing(true)
        }
        property var visibleRows: cockpit.engineers.filter(function(row) {
            if (statusFilter === "UNLOCKED" && row.statusGroup !== "unlocked") return false
            if (statusFilter === "PENDING"
                    && row.statusGroup !== "invited" && row.statusGroup !== "known") return false
            if (statusFilter === "MISSING"
                    && row.statusGroup !== "unknown" && row.statusGroup !== "locked") return false
            let hay = (row.name + " " + row.system + " "
                       + row.modules.join(" ") + " " + row.blueprints.join(" ")).toLowerCase()
            return !query || hay.indexOf(query) >= 0
        })
        property var selectedRow: {
            let rows = cockpit.engineers.filter(function(row) {
                return row.name === engineersPage.selectedEngineerName
            })
            if (rows.length)
                return rows[0]
            return cockpit.engineers.length ? cockpit.engineers[0] : {}
        }
        property var guardianRows: cockpit.techBrokerGuide.filter(function(row) {
            if (statusFilter !== "ALL" && row.statusText !== statusFilter)
                return false
            if (brokerFilter !== "ALL" && row.broker !== brokerFilter)
                return false
            return !query || (row.name + " " + row.broker + " " + row.category + " "
                              + row.statusText).toLowerCase().indexOf(query) >= 0
        })
        property var selectedGuardian: {
            let rows = engineersPage.guardianRows.filter(function(row) {
                return row.name === engineersPage.selectedGuardianName
            })
            if (rows.length)
                return rows[0]
            return engineersPage.guardianRows.length ? engineersPage.guardianRows[0] : {}
        }

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 104
            ColumnLayout {
                anchors.left: parent.left
                anchors.top: parent.top
                Label { text: window.t("engineers.title", "ENGINEER NAVIGATION"); color: textPrimary; font.pixelSize: 24; font.bold: true }
                Label {
                text: window.tf("engineers.subtitle", "%1 · JOURNAL UNLOCK STATE · OFFLINE SYSTEM COORDINATES", [cockpit.system])
                    color: muted; font.pixelSize: 13
                }
            }
            RowLayout {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                spacing: 8
                CockpitButton {
                    text: window.t("engineers.overview", "OVERVIEW")
                    selected: !engineersPage.unlockMode && !engineersPage.guardianMode
                    onClicked: {
                        engineersPage.unlockMode = false
                        engineersPage.guardianMode = false
                        engineerStatus.currentIndex = 0
                    }
                }
                CockpitButton {
                    text: window.t("engineers.unlock_guide", "UNLOCK GUIDE")
                    selected: engineersPage.unlockMode
                    onClicked: {
                        engineersPage.unlockMode = true
                        engineersPage.guardianMode = false
                        engineerStatus.currentIndex = 0
                    }
                }
                CockpitButton {
                    text: window.t("engineers.tech_brokers", "TECH BROKERS")
                    selected: engineersPage.guardianMode
                    onClicked: {
                        engineersPage.unlockMode = false
                        engineersPage.guardianMode = true
                        engineerStatus.currentIndex = 0
                    }
                }
            }
            RowLayout {
                anchors.right: parent.right
                anchors.top: parent.top
                spacing: 8
                TextField {
                    id: engineerSearch
                    text: window.engineersSearchState
                    onTextChanged: window.engineersSearchState = text
                    Layout.preferredWidth: 330
                    Layout.preferredHeight: 42
                    placeholderText: window.t("engineers.search", "Engineer, system, module or blueprint…")
                }
                ComboBox {
                    id: engineerStatus
                    Layout.preferredWidth: 170
                    Layout.preferredHeight: 42
                    model: engineersPage.guardianMode
                       ? ["ALL", "READY", "PENDING", "LOCKED", "UNLOCKED"]
                       : ["ALL", "UNLOCKED", "PENDING", "MISSING"]
                    Component.onCompleted: {
                        const savedIndex = model.indexOf(window.engineersStatusState)
                        currentIndex = savedIndex >= 0 ? savedIndex : 0
                    }
                    onCurrentTextChanged: {
                        if (currentText)
                            window.engineersStatusState = currentText
                    }
                }
            }
        }

        RowLayout {
            visible: !engineersPage.guardianMode
            Layout.fillWidth: true
            spacing: 12
            Repeater {
                model: [
                        {"label": window.t("nav.engineers", "ENGINEERS"), "value": cockpit.engineers.length, "tone": cyan},
                        {"label": window.t("common.unlocked", "UNLOCKED"), "value": cockpit.engineers.filter(function(r) { return r.statusGroup === "unlocked" }).length, "tone": green},
                        {"label": window.t("status.value.pending", "PENDING"), "value": cockpit.engineers.filter(function(r) { return r.statusGroup === "invited" || r.statusGroup === "known" }).length, "tone": orange}
                ]
                delegate: ShadowCard {
                    required property var modelData
                    Layout.fillWidth: true; Layout.preferredHeight: 88
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 15
                        Label { text: modelData.label; color: muted; font.pixelSize: 10; font.bold: true }
                        Label { text: modelData.value; color: modelData.tone; font.pixelSize: 23; font.bold: true }
                    }
                }
            }
        }

        ShadowCard {
            visible: !engineersPage.unlockMode && !engineersPage.guardianMode
            Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 18; spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                        Label { text: window.t("engineers.index", "ENGINEER INDEX"); color: textPrimary; font.pixelSize: 16; font.bold: true }
                    Item { Layout.fillWidth: true }
                        Label { text: window.t("engineers.sort_order", "STATUS → DISTANCE → NAME"); color: muted; font.pixelSize: 10; font.bold: true }
                }
                ListView {
                    id: engineerList
                    Layout.fillWidth: true; Layout.fillHeight: true
                    model: engineersPage.visibleRows
                    spacing: 9; clip: true
                    ScrollBar.vertical: CockpitScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: engineerList.width - 12; height: 120; radius: 13
                        color: engineerMouse.containsMouse ? hover : panelRaised
                        border.width: 1
                        border.color: modelData.statusGroup === "unlocked" ? success
                                      : modelData.statusGroup === "invited" ? warning : borderTone
                        MouseArea {
                            id: engineerMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: {
                                engineersPage.selectedEngineerName = modelData.name
                                engineersPage.unlockMode = true
                                engineersPage.guardianMode = false
                            }
                        }
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 15; spacing: 16
                            Item {
                                width: 64; height: 64
                                Rectangle {
                                    anchors.fill: parent
                                    radius: 10
                                    color: cardRaised
                                    border.width: 1
                                    border.color: modelData.statusGroup === "unlocked" ? success
                                                  : modelData.statusGroup === "invited" ? warning : borderTone
                                    clip: true
                                    Image {
                                        id: engPortrait
                                        anchors.fill: parent
                                        source: modelData.portraitUrl || ""
                                        fillMode: Image.PreserveAspectCrop
                                        asynchronous: true
                                        visible: status === Image.Ready
                                    }
                                    Label {
                                        anchors.centerIn: parent
                                        visible: !engPortrait.visible
                                        text: modelData.statusGroup === "unlocked" ? "✓" : "○"
                                        color: modelData.statusGroup === "unlocked" ? green : muted
                                        font.pixelSize: 22; font.bold: true
                                    }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 3
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label { text: modelData.name; color: textPrimary; font.pixelSize: 16; font.bold: true }
                                    Label {
                                        text: modelData.statusGroup === "unlocked" ? window.t("status.value.unlocked", "UNLOCKED")
                                              : modelData.statusGroup === "invited" || modelData.statusGroup === "known"
                                                ? window.t("status.value.pending", "PENDING")
                                                : window.t("status.value.missing", "MISSING")
                                        color: modelData.statusGroup === "unlocked" ? green : orange
                                        font.pixelSize: 10; font.bold: true
                                    }
                                    Label { text: modelData.rank > 0 ? window.tf("powerplay.rank_value", "RANK %1", [modelData.rank]) : ""; color: cyan; font.pixelSize: 10; font.bold: true }
                                }
                                Label {
                                    text: modelData.system
                                          + (modelData.distance >= 0 ? " · " + modelData.distance.toFixed(1) + " ly" : window.t("status.distance_unknown_suffix", " · distance unknown"))
                                    color: cyan; font.pixelSize: 12; font.bold: true
                                }
                                Label {
                                    text: window.tf("engineers.capabilities", "%1 module types · %2 modifications · up to G%3",
                                                    [modelData.moduleCount, modelData.blueprintCount, modelData.maxGrade])
                                          + " · " + modelData.openJobs + " open jobs"
                                    color: muted; font.pixelSize: 11
                                }
                                Label {
                                    text: modelData.modules.join(" · ")
                                    color: textSecondary; font.pixelSize: 10
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                }
                            }
                            CockpitButton {
                                text: window.t("common.copy_system", "COPY SYSTEM")
                                selected: true
                                onClicked: cockpit.copySystem(modelData.system)
                            }
                        }
                    }
                }
            }
        }

        ShadowCard {
            visible: engineersPage.unlockMode && !engineersPage.guardianMode
            Layout.fillWidth: true
            Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16
                    Item {
                        width: 88; height: 88
                        visible: !!(engineersPage.selectedRow && engineersPage.selectedRow.portraitUrl)
                        Rectangle {
                            anchors.fill: parent
                            radius: 12
                            color: cardRaised
                            border.width: 1
                            border.color: borderTone
                            clip: true
                            Image {
                                anchors.fill: parent
                                source: (engineersPage.selectedRow && engineersPage.selectedRow.portraitUrl) || ""
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                            }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Label {
                            text: engineersPage.selectedRow.name || window.t("engineers.select", "SELECT ENGINEER")
                            color: textPrimary; font.pixelSize: 22; font.bold: true
                        }
                        Label {
                            text: (engineersPage.selectedRow.system || window.t("status.unknown_system", "Unknown system"))
                                  + " · " + (engineersPage.selectedRow.unlockGuide
                                             ? engineersPage.selectedRow.unlockGuide.source : "")
                            color: muted; font.pixelSize: 11
                        }
                    }
                    ComboBox {
                        Layout.preferredWidth: 270
                        model: cockpit.engineers.map(function(row) { return row.name })
                        onActivated: engineersPage.selectedEngineerName = currentText
                        Component.onCompleted: {
                            if (!engineersPage.selectedEngineerName && count)
                                engineersPage.selectedEngineerName = currentText
                        }
                    }
                    CockpitButton {
                        text: window.t("common.copy_system", "COPY SYSTEM")
                        selected: true
                        enabled: (
                            (engineersPage.selectedRow.unlockGuide
                             ? engineersPage.selectedRow.unlockGuide.navigationSystem : "")
                            || engineersPage.selectedRow.system || ""
                        ) !== "System not stored"
                        onClicked: cockpit.copySystem(
                            (engineersPage.selectedRow.unlockGuide
                             && engineersPage.selectedRow.unlockGuide.navigationSystem)
                            ? engineersPage.selectedRow.unlockGuide.navigationSystem
                            : engineersPage.selectedRow.system
                        )
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 190
                    radius: 13
                    color: active
                    border.width: 1
                    border.color: engineersPage.selectedRow.statusGroup === "unlocked"
                                  ? success : warning
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 15; spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: window.t("engineers.chain_columns", "PREREQUISITE → STATUS → NEXT STEP"); color: cyan; font.pixelSize: 10; font.bold: true }
                            Item { Layout.fillWidth: true }
                            Label {
                                text: engineersPage.selectedRow.unlockGuide
                                      ? engineersPage.selectedRow.unlockGuide.completed + " / "
                                        + engineersPage.selectedRow.unlockGuide.total : "0 / 0"
                                color: orange; font.pixelSize: 11; font.bold: true
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                                                Label { text: window.t("engineers.prerequisite", "PREREQUISITE"); color: muted; font.pixelSize: 9; font.bold: true; Layout.preferredWidth: 118 }
                            Label {
                                Layout.fillWidth: true
                                text: (engineersPage.selectedRow.unlockGuide
                                       && engineersPage.selectedRow.unlockGuide.prerequisite)
                                      ? engineersPage.selectedRow.unlockGuide.prerequisite
                                      : window.t("engineers.no_prerequisite", "No external prerequisite")
                                color: textSecondary; font.pixelSize: 11; elide: Text.ElideRight
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                                                Label { text: window.t("common.status", "STATUS"); color: muted; font.pixelSize: 9; font.bold: true; Layout.preferredWidth: 118 }
                            Label {
                                text: engineersPage.selectedRow.statusGroup === "unlocked"
                                      ? window.t("status.value.unlocked", "UNLOCKED")
                                      : window.t("status.value.pending", "PENDING")
                                color: engineersPage.selectedRow.statusGroup === "unlocked" ? green : orange
                                font.pixelSize: 11; font.bold: true
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                                                Label { text: window.t("engineers.next_step", "NEXT STEP"); color: muted; font.pixelSize: 9; font.bold: true; Layout.preferredWidth: 118 }
                            Label {
                                Layout.fillWidth: true
                                text: engineersPage.selectedRow.unlockGuide
                                      ? engineersPage.selectedRow.unlockGuide.nextAction : "Unlock data unavailable."
                                color: textPrimary; font.pixelSize: 13; font.bold: true
                                elide: Text.ElideRight
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: {
                                    var guide = engineersPage.selectedRow.unlockGuide
                                    if (!guide || !guide.navigationSystem) return "DESTINATION · waiting for next navigable step"
                                    return "DESTINATION · " + guide.navigationSystem
                                           + (guide.navigationStation ? " · " + guide.navigationStation : "")
                                }
                                color: cyan; font.pixelSize: 10; font.bold: true
                            }
                            Label {
                                visible: {
                                    var guide = engineersPage.selectedRow.unlockGuide
                                    return guide && guide.requestType === "Commodity"
                                }
                                text: {
                                    var guide = engineersPage.selectedRow.unlockGuide
                                    return "CARGO · " + guide.cargoOwned + " carried · "
                                           + guide.cargoRequired + " t still required · "
                                           + guide.cargoCapacity + " t capacity"
                                }
                                color: orange; font.pixelSize: 10; font.bold: true
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true; height: 7; radius: 4; color: borderTone
                            Rectangle {
                                height: parent.height; radius: parent.radius; color: green
                                width: parent.width * Math.max(0, Math.min(1,
                                    engineersPage.selectedRow.unlockGuide
                                    ? engineersPage.selectedRow.unlockGuide.progress : 0))
                            }
                        }
                    }
                }

                            Label { text: window.t("engineers.guided_chain", "GUIDED UNLOCK CHAIN"); color: orange; font.pixelSize: 12; font.bold: true }
                ListView {
                    id: unlockStepList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 9; clip: true
                    model: engineersPage.selectedRow.unlockGuide
                           ? engineersPage.selectedRow.unlockGuide.steps : []
                    ScrollBar.vertical: CockpitScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: unlockStepList.width - 12
                        height: 86; radius: 12
                        color: modelData.state === "active" ? active : panelRaised
                        border.width: modelData.state === "active" ? 2 : 1
                        border.color: modelData.state === "complete" ? success
                                      : modelData.state === "active" ? cyan : borderTone
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 14; spacing: 14
                            Rectangle {
                                width: 38; height: 38; radius: 19
                                color: modelData.state === "complete" ? successBackground
                                      : modelData.state === "active" ? active : cardRaised
                                Label {
                                    anchors.centerIn: parent
                                    text: modelData.state === "complete" ? "✓"
                                          : modelData.state === "active" ? "→" : "○"
                                    color: modelData.state === "complete" ? green
                                         : modelData.state === "active" ? cyan : muted
                                    font.pixelSize: 18; font.bold: true
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 4
                                Label {
                                    text: modelData.label
                                    color: textPrimary; font.pixelSize: 14; font.bold: true
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.detail
                                    color: modelData.state === "blocked" ? muted : textSecondary
                                    font.pixelSize: 11; wrapMode: Text.WordWrap
                                }
                            }
                            Label {
                                text: modelData.state === "complete" ? window.t("status.value.ready", "READY")
                                      : modelData.state === "active" ? window.t("status.value.pending", "PENDING")
                                      : window.t("status.value.missing", "MISSING")
                                color: modelData.state === "complete" ? green
                                     : modelData.state === "active" ? orange : muted
                                font.pixelSize: 10; font.bold: true
                            }
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: window.t("engineers.unlock_evidence", "Journal evidence updates unlock progress. Unknown history remains PENDING.")
                    color: muted; font.pixelSize: 10; wrapMode: Text.WordWrap
                }
            }
        }

        RowLayout {
            visible: engineersPage.guardianMode
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            ShadowCard {
                Layout.preferredWidth: 480
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 10
                    ColumnLayout {
                        Layout.fillWidth: true
                            Label { text: window.t("engineers.tech_unlocks", "TECH BROKER UNLOCKS"); color: textPrimary; font.pixelSize: 16; font.bold: true }
                        Label {
                            Layout.fillWidth: true
                            text: cockpit.techBrokerGuide.filter(function(row) { return row.broker === "HUMAN" }).length
                                  + " HUMAN · "
                                  + cockpit.techBrokerGuide.filter(function(row) { return row.category === "MODULES" }).length
                                  + " MODULES · "
                                  + cockpit.techBrokerGuide.filter(function(row) { return row.category === "WEAPONS" }).length
                                  + " WEAPONS · "
                                  + cockpit.techBrokerGuide.filter(function(row) { return row.category === "FIGHTERS" }).length
                                  + " FIGHTERS"
                            color: cyan; font.pixelSize: 10; font.bold: true
                            wrapMode: Text.WordWrap
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: window.t("engineers.broker_intro", "One-time Human and Guardian Technology Broker unlocks")
                        color: muted; font.pixelSize: 10; wrapMode: Text.WordWrap
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 6
                        Repeater {
                            model: ["ALL", "HUMAN", "GUARDIAN"]
                            delegate: CockpitButton {
                                required property string modelData
                                text: modelData
                                selected: engineersPage.brokerFilter === modelData
                                implicitWidth: 104
                                implicitHeight: 34
                                onClicked: {
                                    engineersPage.brokerFilter = modelData
                                    engineersPage.selectedGuardianName = ""
                                }
                            }
                        }
                    }
                    ListView {
                        id: guardianList
                        Layout.fillWidth: true; Layout.fillHeight: true
                        model: engineersPage.guardianRows
                        spacing: 8; clip: true
                        ScrollBar.vertical: CockpitScrollBar {}
                        delegate: Rectangle {
                            required property var modelData
                            width: guardianList.width - 12; height: 86; radius: 11
                            color: modelData.name === engineersPage.selectedGuardian.name ? active : panelRaised
                            border.width: modelData.name === engineersPage.selectedGuardian.name ? 2 : 1
                            border.color: modelData.status === "unlocked" ? success
                                          : modelData.status === "ready" ? cyan
                                          : modelData.status === "pending" ? warning : borderTone
                            MouseArea {
                                anchors.fill: parent
                                onClicked: engineersPage.selectedGuardianName = modelData.name
                            }
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 12; spacing: 4
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        text: (modelData.isTracked ? window.t("engineers.tracked_prefix", "★ TRACKED · ") : "")
                                              + modelData.broker + " · #" + modelData.sequence
                                        color: modelData.isTracked ? cyan : muted
                                        font.pixelSize: 9; font.bold: true
                                    }
                                    Label { Layout.fillWidth: true; text: modelData.name; color: textPrimary; font.pixelSize: 12; font.bold: true; elide: Text.ElideRight }
                                    Label {
                                        text: window.localizedStatus(modelData.statusText)
                                        color: modelData.status === "unlocked" ? green
                                             : modelData.status === "ready" ? cyan
                                             : modelData.status === "pending" ? orange : muted
                                        font.pixelSize: 9; font.bold: true
                                    }
                                }
                                Label {
                                    text: modelData.category + " · " + modelData.readyMaterials
                                          + " / " + modelData.totalMaterials + " "
                                          + (modelData.totalMaterials === 1 ? "MATERIAL" : "MATERIALS") + " READY"
                                    color: muted; font.pixelSize: 9
                                }
                                            Label { text: window.tf("engineers.next_step_value", "NEXT STEP · %1", [modelData.nextAction]); color: cyan; font.pixelSize: 9; font.bold: true }
                            }
                        }
                    }
                }
            }

            ShadowCard {
                Layout.fillWidth: true
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 18; spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: engineersPage.selectedGuardian.name || window.t("engineers.select_broker_unlock", "SELECT TECH BROKER UNLOCK")
                                color: textPrimary; font.pixelSize: 20; font.bold: true
                                elide: Text.ElideRight
                            }
                            Label {
                                text: engineersPage.selectedGuardian.category || ""
                                color: engineersPage.selectedGuardian.status === "unlocked" ? green
                                     : engineersPage.selectedGuardian.status === "ready" ? cyan : orange
                                font.pixelSize: 11; font.bold: true
                            }
                        }
                        Label {
                            text: window.localizedStatus(engineersPage.selectedGuardian.statusText || "LOCKED")
                            color: engineersPage.selectedGuardian.status === "unlocked" ? green
                                 : engineersPage.selectedGuardian.status === "ready" ? cyan
                                 : engineersPage.selectedGuardian.status === "pending" ? orange : muted
                            font.pixelSize: 11; font.bold: true
                        }
                        CockpitButton {
                            visible: engineersPage.selectedGuardian.status !== "unlocked"
                            text: engineersPage.selectedGuardian.isTracked
                                  ? "★ TRACKING" : "TRACK NOW"
                            selected: !!engineersPage.selectedGuardian.isTracked
                            onClicked: engineersPage.toggleTechBrokerTrack(
                                engineersPage.selectedGuardian
                            )
                        }
                        CockpitButton {
                            visible: !!engineersPage.selectedGuardian.destinationSystem
                            text: window.t("common.copy_system", "COPY SYSTEM")
                            selected: true
                            onClicked: cockpit.copySystem(
                                engineersPage.selectedGuardian.destinationSystem
                            )
                        }
                    }

                    Rectangle {
                        visible: !!engineersPage.selectedGuardian.isTracked
                        Layout.fillWidth: true; height: 38; radius: 9
                        color: active; border.width: 1; border.color: cyan
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 5
                            Label {
                                Layout.fillWidth: true
                                text: window.t("engineers.active_track", "★ ACTIVE TRACK · MATERIAL PRIORITY · NEXT: ")
                                      + (engineersPage.selectedGuardian.nextAction || "PENDING")
                                color: cyan; font.pixelSize: 10; font.bold: true
                                elide: Text.ElideRight
                            }
                            CockpitButton {
                                text: window.t("engineers.open_missing", "OPEN FARM MISSING")
                                implicitHeight: 28
                                onClicked: engineersPage.openTrackedFarmMissing()
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 4
                        RowLayout {
                            Layout.fillWidth: true
                                        Label { text: window.t("engineers.prerequisite", "PREREQUISITE"); color: muted; font.pixelSize: 9; font.bold: true; Layout.preferredWidth: 128 }
                            Label { Layout.fillWidth: true; text: engineersPage.selectedGuardian.prerequisite || "Guardian Blueprint Segment"; color: textSecondary; font.pixelSize: 10 }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                                        Label { text: window.t("common.status", "STATUS"); color: muted; font.pixelSize: 9; font.bold: true; Layout.preferredWidth: 128 }
                            Label {
                                text: window.localizedStatus(engineersPage.selectedGuardian.statusText || "LOCKED")
                                color: engineersPage.selectedGuardian.status === "unlocked" ? green
                                     : engineersPage.selectedGuardian.status === "ready" ? cyan
                                     : engineersPage.selectedGuardian.status === "pending" ? orange : muted
                                font.pixelSize: 10; font.bold: true
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                                        Label { text: window.t("engineers.next_step", "NEXT STEP"); color: muted; font.pixelSize: 9; font.bold: true; Layout.preferredWidth: 128 }
                            Label { Layout.fillWidth: true; text: engineersPage.selectedGuardian.nextAction || window.t("status.value.pending", "PENDING"); color: cyan; font.pixelSize: 10; font.bold: true; elide: Text.ElideRight }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: engineersPage.selectedGuardian.nextActionDetail || ""
                        color: muted; font.pixelSize: 9; elide: Text.ElideRight
                    }
                    Label {
                        Layout.fillWidth: true
                        text: engineersPage.selectedGuardian.destinationSystem
                              ? (engineersPage.selectedGuardian.destinationEvidence.indexOf("Journal-confirmed") === 0
                                 ? "KNOWN BROKER · " : "RECOMMENDED BROKER · ")
                                + engineersPage.selectedGuardian.destinationSystem
                                + (engineersPage.selectedGuardian.destinationStation
                                   ? " · " + engineersPage.selectedGuardian.destinationStation : "")
                                + " · " + engineersPage.selectedGuardian.destinationEvidence
                              : engineersPage.selectedGuardian.broker === "HUMAN"
                                ? "WHERE · Station service: Human Technology Broker · use the Galaxy Map services filter"
                                : engineersPage.selectedGuardian.broker === "GUARDIAN"
                                  ? "WHERE · Station service: Guardian Technology Broker · use the Galaxy Map services filter"
                                  : ""
                        color: cyan; font.pixelSize: 9; font.bold: true
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: window.t("engineers.broker_catalog", "BROKER CATALOG · ")
                                  + (engineersPage.selectedGuardian.brokerSubtype || "")
                            color: orange; font.pixelSize: 10; font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: cockpit.techBrokerSyncStatus
                            color: muted; font.pixelSize: 8
                            elide: Text.ElideRight; Layout.maximumWidth: 420
                        }
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 76
                        orientation: ListView.Horizontal
                        spacing: 8; clip: true
                        model: engineersPage.selectedGuardian.brokerDestinations || []
                        ScrollBar.horizontal: CockpitScrollBar {}
                        delegate: Rectangle {
                            required property var modelData
                            width: 360; height: 66; radius: 9
                            color: panelRaised; border.width: 1; border.color: borderTone
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 9; spacing: 8
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    Label {
                                        Layout.fillWidth: true
                                        text: modelData.system + " · " + modelData.station
                                        color: textPrimary; font.pixelSize: 10; font.bold: true
                                        elide: Text.ElideRight
                                    }
                                    Label {
                                        text: (modelData.distance_ly !== undefined
                                               ? Number(modelData.distance_ly).toFixed(1) + " ly · " : "")
                                              + (modelData.distance_ls !== undefined
                                                 ? modelData.distance_ls + " ls · " : "")
                                              + (modelData.source || "Broker catalog")
                                        color: muted; font.pixelSize: 8
                                    }
                                }
                                CockpitButton {
                                    text: window.t("common.copy_system", "COPY SYSTEM")
                                    onClicked: cockpit.copySystem(modelData.system)
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: window.t("engineers.inventory_progress", "INVENTORY PROGRESS")
                            color: muted; font.pixelSize: 9; font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: (engineersPage.selectedGuardian.ownedRequired || 0)
                                  + " / " + (engineersPage.selectedGuardian.requiredTotal || 0)
                                  + " " + ((engineersPage.selectedGuardian.requiredTotal || 0) === 1 ? "UNIT" : "UNITS")
                                  + " · " + (engineersPage.selectedGuardian.missingKinds || 0)
                                  + " " + ((engineersPage.selectedGuardian.missingKinds || 0) === 1 ? "TYPE" : "TYPES") + " MISSING"
                            color: engineersPage.selectedGuardian.status === "ready"
                                   || engineersPage.selectedGuardian.status === "unlocked"
                                   ? green : orange
                            font.pixelSize: 9; font.bold: true
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; height: 7; radius: 4; color: borderTone
                        Rectangle {
                            height: parent.height; radius: parent.radius
                            color: engineersPage.selectedGuardian.status === "ready"
                                   || engineersPage.selectedGuardian.status === "unlocked"
                                   ? green : cyan
                            width: parent.width * Math.max(0, Math.min(1,
                                engineersPage.selectedGuardian.progress || 0))
                        }
                    }

                                    Label { text: window.t("engineers.required_materials", "REQUIRED MATERIALS"); color: orange; font.pixelSize: 11; font.bold: true }
                    GridView {
                        id: guardianMaterialGrid
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.min(260, contentHeight)
                        cellWidth: width / 2; cellHeight: 72
                        model: engineersPage.selectedGuardian.materials || []
                        clip: true
                        ScrollBar.vertical: CockpitScrollBar {}
                        delegate: Rectangle {
                            required property var modelData
                            width: guardianMaterialGrid.cellWidth - 8; height: 64; radius: 9
                            color: panelRaised; border.width: 1
                            border.color: modelData.ready ? success : borderTone
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 9
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    Label { Layout.fillWidth: true; text: modelData.name; color: textPrimary; font.pixelSize: 10; font.bold: true; elide: Text.ElideRight }
                                    Label { text: modelData.blueprint ? window.t("engineers.guardian_blueprint", "GUARDIAN BLUEPRINT") : window.t("common.material", "MATERIAL"); color: muted; font.pixelSize: 8 }
                                    Label { Layout.fillWidth: true; text: modelData.origin; color: muted; font.pixelSize: 8; elide: Text.ElideRight }
                                }
                                Label {
                                    text: window.tf("status.have_need_dot", "HAVE %1 · NEED %2", [modelData.have, modelData.need])
                                    color: modelData.ready ? green : orange
                                    font.pixelSize: 11; font.bold: true
                                }
                            }
                        }
                    }

                                    Label { text: window.t("engineers.guided_chain", "GUIDED UNLOCK CHAIN"); color: orange; font.pixelSize: 11; font.bold: true }
                    ListView {
                        id: guardianStepList
                        Layout.fillWidth: true; Layout.fillHeight: true
                        model: engineersPage.selectedGuardian.steps || []
                        spacing: 8; clip: true
                        ScrollBar.vertical: CockpitScrollBar {}
                        delegate: Rectangle {
                            required property var modelData
                            width: guardianStepList.width - 12; height: 86; radius: 11
                            color: modelData.state === "active" ? active : panelRaised
                            border.width: modelData.state === "active" ? 2 : 1
                            border.color: modelData.state === "complete" ? success
                                          : modelData.state === "active" ? cyan : borderTone
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 12; spacing: 12
                                Label {
                                    text: modelData.state === "complete" ? "✓"
                                          : modelData.state === "active" ? "→" : "○"
                                    color: modelData.state === "complete" ? green
                                         : modelData.state === "active" ? cyan : muted
                                    font.pixelSize: 18; font.bold: true
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 3
                                    Label { text: modelData.label; color: textPrimary; font.pixelSize: 12; font.bold: true }
                                    Label { Layout.fillWidth: true; text: modelData.detail; color: textSecondary; font.pixelSize: 10; wrapMode: Text.WordWrap }
                                }
                                Label {
                                    text: modelData.state === "complete" ? window.t("status.value.ready", "READY")
                                          : modelData.state === "active" ? window.t("status.value.pending", "PENDING")
                                          : window.t("status.value.locked", "LOCKED")
                                    color: modelData.state === "complete" ? green
                                         : modelData.state === "active" ? orange : muted
                                    font.pixelSize: 9; font.bold: true
                                }
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: window.t("engineers.chain_note", "Journal and inventory evidence update this chain; unknown unlock history remains LOCKED.")
                        color: muted; font.pixelSize: 9; wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }
        }
    }

    Loader {
        id: pageLoader8
        anchors.fill: parent
        active: window.currentPage === 8
        asynchronous: false
        sourceComponent: Component {
    ColumnLayout {
        id: hgeFinderPage
        objectName: "qa-page-state-finds"
        property string materialFilter: window.hgeMaterialState
        property string findTypeFilter: window.hgeFindTypeState
        property string stateFilter: window.hgeStatusState
        property string allegianceFilter: window.hgeAllegianceState
        property string evidenceFilter: window.hgeEvidenceState
        property bool advancedFiltersOpen: window.hgeAdvancedState
        property int nearbyRadius: window.hgeNearbyRadiusState
        property int visibleCandidateLimit: window.hgeVisibleLimitState
        onMaterialFilterChanged: window.hgeMaterialState = hgeFinderPage.materialFilter
        onFindTypeFilterChanged: window.hgeFindTypeState = hgeFinderPage.findTypeFilter
        onStateFilterChanged: window.hgeStatusState = hgeFinderPage.stateFilter
        onAllegianceFilterChanged: window.hgeAllegianceState = hgeFinderPage.allegianceFilter
        onEvidenceFilterChanged: window.hgeEvidenceState = hgeFinderPage.evidenceFilter
        onAdvancedFiltersOpenChanged: window.hgeAdvancedState = hgeFinderPage.advancedFiltersOpen
        onNearbyRadiusChanged: window.hgeNearbyRadiusState = hgeFinderPage.nearbyRadius
        onVisibleCandidateLimitChanged: window.hgeVisibleLimitState = hgeFinderPage.visibleCandidateLimit
        readonly property var findTypeOptions: [
            {"label": window.t("state.type_all", "All Find Types"), "key": "ALL FIND TYPES"},
            {"label": window.t("state.type_hge", "High Grade Emissions"), "key": "HGE"},
            {"label": window.t("state.type_conflict", "Conflict Zone"), "key": "CONFLICT_ZONE"},
            {"label": window.t("state.type_meds", "Seeking Meds"), "key": "SEEKING_MEDS"},
            {"label": window.t("state.type_foods", "Seeking Foods"), "key": "SEEKING_FOODS"}
        ]
        function evidenceLabel(key) {
            const labels = {
                "BGS_PREDICTION": window.t("state.source_bgs", "BGS Prediction"),
                "EDDN_SIGNAL": window.t("state.source_eddn", "EDDN Signal"),
                "LOCAL_JOURNAL": window.t("state.source_journal", "Local Journal"),
                "ENTERED": window.t("state.source_entered", "Local Journal · Entered")
            }
            return labels[key] || window.t("state.evidence_unavailable", "Evidence unavailable")
        }
        visible: true
        anchors.fill: parent
        anchors.leftMargin: sidebar.width + (window.compactSidebar ? 18 : 26)
        anchors.rightMargin: window.compactSidebar ? 18 : 26
        anchors.topMargin: window.compactSidebar ? 18 : 26
        anchors.bottomMargin: window.compactSidebar ? 18 : 26
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                spacing: 2
                Label { text: window.t("state.title", "STATE FINDS FINDER"); color: textPrimary; font.pixelSize: 24; font.bold: true }
                Label {
                    text: window.tf("status.state_system", "State-dependent signal intelligence · %1", [cockpit.system])
                    color: muted; font.pixelSize: 13
                }
            }
            Item { Layout.fillWidth: true }
            ColumnLayout {
                spacing: 2
                Label {
                    text: cockpit.eddnListenerStatus
                    color: cockpit.eddnListenerStatus.indexOf("Connected") === 0 ? green : orange
                    font.pixelSize: 11; font.bold: true
                    Layout.alignment: Qt.AlignRight
                }
                Label {
                    text: cockpit.stateFindRefreshStatus
                    color: muted; font.pixelSize: 9
                    Layout.alignment: Qt.AlignRight
                }
            }
            CockpitButton {
                text: window.t("state.refresh", "REFRESH NOW")
                        helpText: window.t("state.refresh_help", "Read new Journal evidence, flush live EDDN reports and remove expired finds")
                onClicked: cockpit.refreshStateFinds()
            }
        }

        ShadowCard {
            Layout.fillWidth: true
            Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: cockpit.stateFindCount(
                                  hgeFinderPage.findTypeFilter,
                                  hgeFinderPage.stateFilter,
                                  hgeFinderPage.allegianceFilter,
                                  hgeFinderPage.nearbyRadius,
                                  hgeFinderPage.materialFilter,
                                  hgeFinderPage.evidenceFilter)
                              + " MATCHING FINDS"
                        color: orange
                        font.pixelSize: 13; font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: window.t("state.sort", "FRESHEST FIRST · THEN DISTANCE")
                        color: muted; font.pixelSize: 10; font.bold: true
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Label {
                        text: window.t("state.filter_material", "FILTER MATERIAL")
                        color: muted; font.pixelSize: 10; font.bold: true
                    }
                    ComboBox {
                        id: hgeMaterialFilter
                        Layout.preferredWidth: 280
                        implicitHeight: 44
                        model: cockpit.hgeMaterialFilters
                        Accessible.name: window.t("state.filter_material_accessible", "Filter finds by predicted HGE material")
                        onCurrentTextChanged: {
                            hgeFinderPage.materialFilter = currentText
                            hgeFinderPage.visibleCandidateLimit = 250
                        }
                        delegate: ItemDelegate {
                            required property int index
                            required property var modelData
                            width: hgeMaterialFilter.width - 12
                            height: 40
                            text: modelData
                            highlighted: hgeMaterialFilter.highlightedIndex === index
                            font.pixelSize: 12; font.bold: true
                            contentItem: Label {
                                text: parent.text
                                color: parent.highlighted ? backgroundPrimary : textPrimary
                                font: parent.font
                                verticalAlignment: Text.AlignVCenter
                                leftPadding: 10
                            }
                            background: Rectangle {
                                radius: 8
                                color: parent.highlighted ? orange : "transparent"
                            }
                        }
                        indicator: Label {
                            anchors.right: parent.right
                            anchors.rightMargin: 14
                            anchors.verticalCenter: parent.verticalCenter
                            text: "⌄"
                            color: orange; font.pixelSize: 18; font.bold: true
                        }
                        contentItem: Label {
                            leftPadding: 14; rightPadding: 42
                            text: hgeMaterialFilter.displayText
                            color: textPrimary; font.pixelSize: 12; font.bold: true
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                        }
                        background: Rectangle {
                            radius: 10
                            color: panelRaised
                            border.width: hgeMaterialFilter.activeFocus ? 2 : 1
                            border.color: hgeMaterialFilter.activeFocus ? orange : warning
                        }
                        popup: Popup {
                            y: hgeMaterialFilter.height + 4
                            width: hgeMaterialFilter.width
                            implicitHeight: Math.min(contentItem.implicitHeight + 12, 360)
                            padding: 6
                            contentItem: ListView {
                                clip: true
                                implicitHeight: contentHeight
                                model: hgeMaterialFilter.popup.visible
                                       ? hgeMaterialFilter.delegateModel : null
                                currentIndex: hgeMaterialFilter.highlightedIndex
                                ScrollBar.vertical: CockpitScrollBar {}
                            }
                            background: Rectangle {
                                radius: 10
                                color: panel
                                border.width: 1
                                border.color: warning
                            }
                        }
                    }
                    Label {
                        text: hgeFinderPage.materialFilter === "ALL HGE MATERIALS"
                              ? window.t("state.showing_all", "SHOWING ALL FINDS")
                              : window.t("state.prediction_matches", "PREDICTION MATCHES ONLY")
                        color: orange; font.pixelSize: 9; font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Label {
                        text: window.t("state.range_quality", "RANGE & QUALITY")
                        color: muted; font.pixelSize: 10; font.bold: true
                    }
                    ComboBox {
                        id: nearbyFilter
                        Layout.preferredWidth: 150; implicitHeight: 44
                        model: [
                            window.t("state.all_distances", "ALL DISTANCES"),
                            window.t("state.nearby_25", "NEARBY · 25 LY"),
                            window.t("state.nearby_50", "NEARBY · 50 LY"),
                            window.t("state.nearby_100", "NEARBY · 100 LY")
                        ]
                        Accessible.name: window.t("state.filter_distance", "Filter finds by distance")
                        onCurrentIndexChanged: {
                            hgeFinderPage.nearbyRadius = [0, 25, 50, 100][currentIndex]
                            hgeFinderPage.visibleCandidateLimit = 250
                        }
                    }
                    ComboBox {
                        id: evidenceFilter
                        Layout.preferredWidth: 145; implicitHeight: 44
                        model: [
                            {"label": window.t("state.evidence_best", "BEST"), "key": "ALL EVIDENCE"},
                            {"label": window.t("state.evidence_live", "LIVE"), "key": "LIVE ONLY"},
                            {"label": window.t("state.evidence_predictions", "PREDICTIONS"), "key": "BGS CANDIDATES"}
                        ]
                        textRole: "label"
                        valueRole: "key"
                        Accessible.name: window.t("state.filter_evidence", "Filter finds by evidence quality")
                        onActivated: {
                            hgeFinderPage.evidenceFilter = currentValue
                            hgeFinderPage.visibleCandidateLimit = 250
                        }
                    }
                    CockpitButton {
                        text: hgeFinderPage.advancedFiltersOpen
                              ? window.t("settings.hide_advanced", "HIDE ADVANCED")
                              : window.t("state.advanced_filters", "ADVANCED FILTERS")
                        helpText: window.t("state.advanced_help", "Show optional find type, state and allegiance filters")
                        onClicked: hgeFinderPage.advancedFiltersOpen =
                                   !hgeFinderPage.advancedFiltersOpen
                    }
                    Item { Layout.fillWidth: true }
                }
                Flow {
                    Layout.fillWidth: true
                    visible: hgeFinderPage.advancedFiltersOpen
                    spacing: 10
                    ComboBox {
                        id: findTypeFilter
                        width: 220; height: 40
                        model: hgeFinderPage.findTypeOptions
                        textRole: "label"
                        valueRole: "key"
                            Accessible.name: window.t("state.filter_type", "Filter finds by type")
                        onActivated: {
                            hgeFinderPage.findTypeFilter = currentValue
                            hgeFinderPage.visibleCandidateLimit = 250
                        }
                    }
                    ComboBox {
                        id: stateFilter
                        width: 190; height: 40
                        model: cockpit.stateFindStateFilters
                            Accessible.name: window.t("state.filter_state", "Filter finds by system state")
                        onCurrentTextChanged: {
                            hgeFinderPage.stateFilter = currentText
                            hgeFinderPage.visibleCandidateLimit = 250
                        }
                    }
                    ComboBox {
                        id: allegianceFilter
                        width: 210; height: 40
                        model: cockpit.stateFindAllegianceFilters
                            Accessible.name: window.t("state.filter_allegiance", "Filter finds by allegiance")
                        onCurrentTextChanged: {
                            hgeFinderPage.allegianceFilter = currentText
                            hgeFinderPage.visibleCandidateLimit = 250
                        }
                    }
                    CockpitButton {
                        text: window.t("state.reset", "RESET FILTERS")
                            helpText: window.t("state.reset_help", "Show every cached find and restore all filter defaults")
                        implicitHeight: 40
                        onClicked: {
                            findTypeFilter.currentIndex = 0
                            stateFilter.currentIndex = 0
                            allegianceFilter.currentIndex = 0
                            nearbyFilter.currentIndex = 0
                            evidenceFilter.currentIndex = 0
                            hgeMaterialFilter.currentIndex = 0
                            hgeFinderPage.findTypeFilter = "ALL FIND TYPES"
                            hgeFinderPage.stateFilter = "ALL STATES"
                            hgeFinderPage.allegianceFilter = "ALL ALLEGIANCES"
                            hgeFinderPage.nearbyRadius = 0
                            hgeFinderPage.materialFilter = "ALL HGE MATERIALS"
                            hgeFinderPage.evidenceFilter = "ALL EVIDENCE"
                            hgeFinderPage.visibleCandidateLimit = 250
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: window.t("state.evidence_help", "Local Journal evidence updates matching finds: LOCAL LIVE and LOCAL ENTERED are direct evidence. EDDN LIVE has verified remaining lifetime; RECENT REPORT and BGS CANDIDATE are weaker.")
                    color: muted; font.pixelSize: 10; wrapMode: Text.WordWrap
                }
                ListView {
                    id: hgeFinderList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: cockpit.stateFindPage(
                        hgeFinderPage.findTypeFilter,
                        hgeFinderPage.stateFilter,
                        hgeFinderPage.allegianceFilter,
                        hgeFinderPage.nearbyRadius,
                        hgeFinderPage.materialFilter,
                        hgeFinderPage.evidenceFilter,
                        hgeFinderPage.visibleCandidateLimit
                    )
                    spacing: 9
                    clip: true
                    ScrollBar.vertical: CockpitScrollBar {}
                    footer: Button {
                        width: hgeFinderList.width
                        height: 44
                        visible: hgeFinderPage.visibleCandidateLimit
                                 < cockpit.stateFindCount(
                                       hgeFinderPage.findTypeFilter,
                                       hgeFinderPage.stateFilter,
                                       hgeFinderPage.allegianceFilter,
                                       hgeFinderPage.nearbyRadius,
                                       hgeFinderPage.materialFilter,
                                       hgeFinderPage.evidenceFilter)
                        text: window.t("state.load_more", "LOAD 250 MORE FINDS")
                        onClicked: hgeFinderPage.visibleCandidateLimit += 250
                    }
                    delegate: Rectangle {
                        required property var modelData
                        width: hgeFinderList.width
                        height: 118
                        radius: 12
                        color: panelRaised
                        border.width: 1
                        border.color: modelData.localNotConfirmed ? error
                                      : (modelData.evidenceKind === "LOCAL_JOURNAL"
                                         || modelData.evidenceKind === "ENTERED")
                                        ? success : warning
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 16
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 5
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        text: modelData.findLabel.toUpperCase()
                                        color: backgroundPrimary
                                        font.pixelSize: 9; font.bold: true
                                        padding: 5
                                        background: Rectangle { radius: 5; color: orange }
                                    }
                                    Label {
                                        text: modelData.system
                                        color: textPrimary; font.pixelSize: 15; font.bold: true
                                    }
                                    Label {
                                        text: modelData.matchClass
                                              ? window.localizedStatus(modelData.matchClass)
                                              : window.t("status.unresolved", "UNRESOLVED")
                                        color: modelData.matchClass === "EXACT MATCH" ? green
                                               : modelData.matchClass === "FAMILY MATCH" ? orange : muted
                                        font.pixelSize: 9; font.bold: true
                                    }
                                    Item { Layout.fillWidth: true }
                                    Label {
                                        text: modelData.freshness === "LIVE"
                                              ? window.tf("state.minutes_left", "%1 MIN LEFT", [Math.max(1, Math.floor(modelData.remainingSeconds / 60))])
                                              : window.tf("state.reported_ago", "REPORTED %1 MIN AGO", [modelData.lastReportedMinutes])
                                        color: modelData.freshness === "LIVE" ? green
                                               : modelData.freshness === "RECENT" ? orange : muted
                                        font.pixelSize: 11; font.bold: true
                                    }
                                    Label {
                                        text: window.localizedStatus(modelData.freshness || "STALE")
                                        color: modelData.freshness === "LIVE" ? green
                                               : modelData.freshness === "RECENT" ? orange : muted
                                        font.pixelSize: 10; font.bold: true
                                    }
                                    Label {
                                        text: window.localizedStatus(modelData.status || "UNKNOWN")
                                        color: modelData.localNotConfirmed ? error
                                               : (modelData.evidenceKind === "LOCAL_JOURNAL"
                                                  || modelData.evidenceKind === "ENTERED") ? green
                                               : modelData.status === "EDDN LIVE" ? orange : muted
                                        font.pixelSize: 10; font.bold: true
                                    }
                                    Label {
                                        visible: Boolean(modelData.eddnDelivery)
                                        text: window.localizedStatus(modelData.eddnDelivery || "")
                                        color: modelData.eddnDelivery === "EDDN SENT" ? green
                                               : modelData.eddnDelivery === "EDDN FAILED" ? error
                                               : (modelData.eddnDelivery === "EDDN QUEUED"
                                                  || modelData.eddnDelivery === "EDDN RETRY"
                                                  || modelData.eddnDelivery === "EDDN SENDING") ? orange
                                               : muted
                                        font.pixelSize: 10; font.bold: true
                                    }
                                }
                                Label {
                                    text: (modelData.distance >= 0
                                           ? modelData.distance.toFixed(1) + " ly"
                                           : window.t("status.distance_unknown", "Distance unknown"))
                                          + window.tf("state.reports_state", " · %1 reports · STATE: %2", [modelData.reportCount, modelData.state])
                                    color: cyan; font.pixelSize: 11
                                    elide: Text.ElideRight; Layout.fillWidth: true
                                }
                                Label {
                                    text: window.tf("status.allegiance", "ALLEGIANCE: %1", [modelData.allegiance])
                                          + window.tf("state.faction", " · FACTION: %1", [modelData.faction])
                                          + (modelData.intensity !== "UNKNOWN"
                                             ? window.tf("state.intensity", " · INTENSITY: %1", [modelData.intensity]) : "")
                                    color: orange
                                    font.pixelSize: 10; font.bold: true
                                    elide: Text.ElideRight; Layout.fillWidth: true
                                }
                                Label {
                                    text: modelData.findType === "HGE"
                                          ? window.tf("state.hge_materials", "HGE MATERIALS: %1", [modelData.materials || window.t("state.no_prediction", "No reliable material prediction")])
                                          : window.tf("state.evidence", "EVIDENCE: %1", [hgeFinderPage.evidenceLabel(modelData.evidenceKind)])
                                    color: muted; font.pixelSize: 9
                                    elide: Text.ElideRight; Layout.fillWidth: true
                                }
                            }
                            CockpitButton {
                                text: window.t("common.copy_system", "COPY SYSTEM")
                                onClicked: cockpit.copySystem(modelData.system)
                            }
                        }
                    }
                    EmptyState {
                        anchors.centerIn: parent
                        visible: parent.count === 0
                        symbol: "⌖"
                        title: hgeFinderPage.nearbyRadius > 0
                               && cockpit.system === "Unknown system"
                               ? window.t("state.position_unknown", "CURRENT POSITION UNKNOWN")
                               : cockpit.stateFindCount(
                                     "ALL FIND TYPES", "ALL STATES",
                                     "ALL ALLEGIANCES", 0,
                                     "ALL HGE MATERIALS", "ALL EVIDENCE") === 0
                                 ? window.t("state.no_data", "NO STATE FINDS DATA YET")
                                 : window.t("state.no_matches", "NO FINDS MATCH THESE FILTERS")
                        detail: hgeFinderPage.nearbyRadius > 0 && cockpit.system === "Unknown system"
                                ? window.t("state.position_help", "Nearby filtering needs a current three-dimensional Journal position. Jump or reload the Journal, then try again.")
                                : cockpit.stateFindCount(
                                      "ALL FIND TYPES", "ALL STATES",
                                      "ALL ALLEGIANCES", 0,
                                      "ALL HGE MATERIALS", "ALL EVIDENCE") > 0
                                  ? window.t("state.no_matches_help", "Cached finds exist, but none match the active filters. Use Reset Filters to show everything.")
                                  : cockpit.eddnListenerEnabled
                                    ? window.t("state.listening_help", "Listening for EDDN BGS and signal reports. Findings appear when relevant data arrives.")
                                    : window.t("state.enable_help", "Enable live State Finds intelligence under Settings → Connections.")
                        tone: cyan
                    }
                }
            }
        }
    }
        }
    }

    Loader {
        id: pageLoader9
        anchors.fill: parent
        active: window.currentPage === 9
        asynchronous: false
        sourceComponent: Component {
            LogbookPage {
                appWindow: window
                sidebarWidth: sidebar.width
                onEntryRequested: function(entryId) {
                    cockpit.selectLogbookEntry(entryId)
                    logbookDetailDialog.open()
                }
            }
        }
    }

    Loader {
        id: pageLoader10
        anchors.fill: parent
        active: window.currentPage === 10 || smokeTest
        visible: window.currentPage === 10
        asynchronous: false
        sourceComponent: Component {
    ColumnLayout {
        id: commanderPage
        objectName: "qa-page-cmdr"
        property var overview: cockpit.commanderOverview || ({})
        property var rankRows: overview.ranks || []
        property var cards: cockpit.commanderCards || ({})
        property var allowedCardIds: [
            "ranks", "major-reputation", "finances", "current-ship",
            "minor-reputation", "squadron"
        ]
        property var cardOrder: {
            let source = cockpit.commanderCardOrder || []
            let result = []
            for (let index = 0; index < source.length; ++index) {
                if (allowedCardIds.indexOf(source[index]) >= 0 && result.indexOf(source[index]) < 0)
                    result.push(source[index])
            }
            for (let fallback = 0; fallback < allowedCardIds.length; ++fallback) {
                if (result.indexOf(allowedCardIds[fallback]) < 0)
                    result.push(allowedCardIds[fallback])
            }
            return result
        }
        function moveCard(fromIndex, toIndex) {
            if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex)
                return
            let order = cardOrder.slice(0)
            let moved = order.splice(fromIndex, 1)[0]
            order.splice(toIndex, 0, moved)
            cardOrder = order
            cockpit.setCommanderCardOrder(order)
        }
        visible: true
        anchors.fill: parent
        anchors.leftMargin: sidebar.width + (window.compactSidebar ? 18 : 26)
        anchors.rightMargin: window.compactSidebar ? 18 : 26
        anchors.topMargin: window.compactSidebar ? 18 : 26
        anchors.bottomMargin: window.compactSidebar ? 18 : 26
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                Label {
                text: cockpit.commanderKnown ? "CMDR " + cockpit.commander : window.t("commander.overview", "CMDR OVERVIEW")
                    color: textPrimary; font.pixelSize: 24; font.bold: true
                }
                Label {
                    text: commanderPage.overview.lastUpdated
                          ? "JOURNAL SNAPSHOT · " + commanderPage.overview.lastUpdated
                          : "JOURNAL SNAPSHOT · WAITING FOR COMMANDER DATA"
                    color: muted; font.pixelSize: 11
                }
            }
            Label {
                text: cockpit.journalAuto
                      ? window.t("common.journal_live", "● JOURNAL LIVE")
                      : window.t("common.journal_paused", "Ⅱ JOURNAL PAUSED")
                color: cockpit.journalAuto ? green : orange
                font.pixelSize: 10; font.bold: true
            }
        }

        GridView {
            id: commanderGrid
            objectName: "qa-cmdr-card-grid"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: commanderPage.cardOrder
            cellWidth: width / (window.narrowWorkspace ? 1 : 2)
            cellHeight: 250
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AlwaysOff }
            ScrollBar.vertical: CockpitScrollBar {}
            delegate: Item {
                id: commanderTile
                required property int index
                required property var modelData
                width: commanderGrid.cellWidth
                height: commanderGrid.cellHeight
                property var cardData: commanderPage.cards[modelData] || ({"title": modelData, "rows": [], "empty": "NO DATA", "tone": "cyan"})
                property var cardRows: cardData.rows || []
                property var firstRow: cardRows.length > 0 ? cardRows[0] : ({})
                property color toneColor: cardData.tone === "green" ? green
                                          : cardData.tone === "orange" ? orange : cyan
                function numberFrom(value, fallback) {
                    let match = String(value || "").match(/-?\d+(?:[.,]\d+)?/)
                    return match ? Number(match[0].replace(",", ".")) : fallback
                }

                ShadowCard {
                    id: commanderCard
                    x: 7; y: 7
                    width: commanderTile.width - 14
                    height: commanderTile.height - 14
                    z: dragHandle.drag.active ? 100 : 1
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 16; spacing: 8
                        RowLayout {
                            Layout.fillWidth: true; Layout.preferredHeight: 26
                            Label {
                                Layout.fillWidth: true
                                text: commanderTile.cardData.title || window.t("commander.data", "CMDR DATA")
                                color: commanderTile.toneColor
                                font.pixelSize: 11; font.bold: true
                                elide: Text.ElideRight
                            }
                                Label { text: window.t("common.drag", "⠿ DRAG"); color: muted; font.pixelSize: 9; font.bold: true }
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                        GridLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            columns: 4; columnSpacing: 7; rowSpacing: 7
                            visible: modelData === "ranks"
                            Repeater {
                                model: commanderPage.rankRows
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true; Layout.preferredHeight: 80
                                    radius: 8; color: panelRaised
                                    RowLayout {
                                        anchors.fill: parent; anchors.margins: 7; spacing: 6
                                        CommanderRankIcon {
                                            Layout.preferredWidth: 38; Layout.preferredHeight: 48
                                            motif: modelData.icon || ""
                                            rankValue: modelData.known ? modelData.rank : -1
                                            maxRank: modelData.maxRank || 13
                                            scale: 0.72
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true; spacing: 2
                                            Label { Layout.fillWidth: true; text: modelData.label || "RANK"; color: cyan; font.pixelSize: 7; font.bold: true; elide: Text.ElideRight }
                                            Label { Layout.fillWidth: true; text: modelData.known ? window.tf("powerplay.rank_value", "RANK %1", [modelData.rank]) : window.t("status.value.unknown", "UNKNOWN"); color: textPrimary; font.pixelSize: 10; font.bold: true; elide: Text.ElideRight }
                                            Label { Layout.fillWidth: true; text: modelData.progressKnown ? modelData.progress + "%" : "—"; color: muted; font.pixelSize: 7 }
                                            ModernProgress {
                                                Layout.fillWidth: true; implicitHeight: 5
                                                value: modelData.progressKnown ? modelData.progress / 100 : 0
                                                inactive: !modelData.progressKnown
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        GridLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            columns: 2; columnSpacing: 8; rowSpacing: 7
                            visible: modelData === "major-reputation"
                            Repeater {
                                model: commanderTile.cardRows
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true; Layout.preferredHeight: 72
                                    radius: 8; color: panelRaised
                                    ColumnLayout {
                                        anchors.fill: parent; anchors.margins: 9; spacing: 5
                                        RowLayout {
                                            Layout.fillWidth: true; spacing: 6
                                            Label { Layout.fillWidth: true; text: modelData.label || "FACTION"; color: textSecondary; font.pixelSize: 9; font.bold: true; elide: Text.ElideRight }
                                            Label { text: modelData.value || window.t("status.value.unknown", "UNKNOWN"); color: textPrimary; font.pixelSize: 10; font.bold: true }
                                        }
                                        ModernProgress {
                                            Layout.fillWidth: true; implicitHeight: 7
                                            from: 0; to: 100
                                            value: Math.max(0, commanderTile.numberFrom(modelData.value, 0))
                                            progressColor: green
                                        }
                                    }
                                }
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 10
                            visible: modelData === "finances"
                            Repeater {
                                model: commanderTile.cardRows
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true; Layout.preferredHeight: 70
                                    radius: 9; color: panelRaised
                                    ColumnLayout {
                                        anchors.fill: parent; anchors.margins: 10; spacing: 3
                                        Label { text: modelData.label || "SNAPSHOT"; color: textSecondary; font.pixelSize: 9; font.bold: true }
                                        Label { Layout.fillWidth: true; text: modelData.value || window.t("status.value.unknown", "UNKNOWN"); color: textPrimary; font.pixelSize: 16; font.bold: true; elide: Text.ElideRight }
                                        Label { Layout.fillWidth: true; text: modelData.detail || ""; color: muted; font.pixelSize: 8; elide: Text.ElideRight }
                                    }
                                }
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
                            visible: modelData === "current-ship"
                            Label { Layout.fillWidth: true; text: commanderTile.firstRow.label || commanderTile.cardData.empty; color: textSecondary; font.pixelSize: 11; font.bold: true; elide: Text.ElideRight }
                            Label { Layout.fillWidth: true; text: commanderTile.firstRow.value || window.t("status.value.unknown", "UNKNOWN"); color: textPrimary; font.pixelSize: 20; font.bold: true; elide: Text.ElideRight }
                            Label { Layout.fillWidth: true; text: commanderTile.firstRow.detail || window.t("commander.location_unknown", "LOCATION UNKNOWN"); color: cyan; font.pixelSize: 10; elide: Text.ElideRight }
                            Item { Layout.fillHeight: true }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
                            visible: modelData === "minor-reputation"
                            Label { text: commanderTile.cardRows.length ? commanderTile.cardRows.length : "—"; color: textPrimary; font.pixelSize: 34; font.bold: true }
                            Label { text: commanderTile.cardRows.length ? window.t("commander.known_factions", "KNOWN MINOR FACTIONS") : commanderTile.cardData.empty; color: green; font.pixelSize: 10; font.bold: true }
                                Label { text: window.t("commander.reputation_note", "No aggregate reputation bar: faction values are independent."); color: muted; font.pixelSize: 8; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Item { Layout.fillHeight: true }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
                            visible: modelData === "squadron"
                            Label { Layout.fillWidth: true; text: commanderTile.firstRow.value || commanderTile.cardData.empty; color: textPrimary; font.pixelSize: 20; font.bold: true; elide: Text.ElideRight }
                            Label { Layout.fillWidth: true; text: commanderTile.firstRow.detail || window.t("commander.role_unknown", "ROLE UNKNOWN"); color: cyan; font.pixelSize: 11; font.bold: true; elide: Text.ElideRight }
                            Item { Layout.fillHeight: true }
                        }
                    }
                    MouseArea {
                        id: dragHandle
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.top: parent.top; height: 52
                        cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                        drag.target: commanderCard
                        drag.minimumX: -commanderTile.x + 4
                        drag.maximumX: commanderGrid.width - commanderTile.x - commanderCard.width - 4
                        drag.minimumY: -commanderTile.y + 4
                        drag.maximumY: Math.max(4, commanderGrid.contentHeight - commanderTile.y - commanderCard.height - 4)
                        onReleased: {
                            let target = commanderGrid.indexAt(
                                commanderTile.x + commanderCard.x + commanderCard.width / 2,
                                commanderTile.y + commanderCard.y + commanderCard.height / 2)
                            commanderCard.x = 7
                            commanderCard.y = 7
                            if (target >= 0)
                                commanderPage.moveCard(commanderTile.index, target)
                        }
                    }
                }
            }
        }
    }
        }
    }

    Loader {
        id: pageLoader11
        anchors.fill: parent
        active: window.currentPage === 11
        asynchronous: false
        sourceComponent: Component {
            PowerplayPage {
                appWindow: window
                sidebarWidth: sidebar.width
            }
        }
    }

    Loader {
        id: pageLoader5
        anchors.fill: parent
        active: window.currentPage === 5
        asynchronous: false
        sourceComponent: Component {
    ColumnLayout {
        id: settingsPage
        objectName: "qa-page-settings"
        property bool advancedOpen: window.settingsAdvancedState
        onAdvancedOpenChanged: window.settingsAdvancedState = advancedOpen
        visible: true
        anchors.fill: parent
        anchors.leftMargin: sidebar.width + (window.compactSidebar ? 18 : 26)
        anchors.rightMargin: window.compactSidebar ? 18 : 26
        anchors.topMargin: window.compactSidebar ? 18 : 26
        anchors.bottomMargin: window.compactSidebar ? 18 : 26
        spacing: 16

        SettingsHeader {
            qaName: "qa-header-settings"
            heading: window.t("settings.title", "SETTINGS")
            subheading: window.t("settings.subtitle", "Appearance, Journal and app behavior")
        }

        ScrollView {
            id: settingsScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        GridLayout {
            width: settingsScroll.availableWidth
            columns: window.narrowWorkspace ? 1 : 2
            columnSpacing: 16
            rowSpacing: 16

            ShadowCard {
                Layout.fillWidth: true
                Layout.preferredHeight: window.narrowWorkspace
                                        ? 650 : Math.max(620, settingsScroll.availableHeight)
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 22; spacing: 14
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: window.t("settings.appearance", "APPEARANCE"); color: cyan; font.pixelSize: 13; font.bold: true }
                        Item { Layout.fillWidth: true }
                        CockpitButton {
                        text: settingsPage.advancedOpen
                              ? window.t("settings.hide_advanced", "HIDE ADVANCED")
                              : window.t("settings.advanced", "ADVANCED")
                            onClicked: settingsPage.advancedOpen = !settingsPage.advancedOpen
                        }
                    }
                    ColumnLayout {
                        visible: settingsPage.advancedOpen
                        Layout.fillWidth: true
                        spacing: 8
                        Label {
                            text: window.tf("status.renderer", "GRAPHICS RENDERER · ACTIVE %1", [cockpit.rendererActive])
                            color: muted; font.pixelSize: 10; font.bold: true
                        }
                        ComboBox {
                            id: rendererSelector
                            Layout.fillWidth: true
                            Layout.preferredHeight: 44
                            model: ["AUTO", "GPU · DIRECT3D 11", "SAFE · SOFTWARE"]
                            currentIndex: cockpit.rendererMode === "gpu" ? 1
                                          : cockpit.rendererMode === "software" ? 2 : 0
                            onActivated: cockpit.setRendererMode(
                                currentIndex === 1 ? "gpu"
                                : currentIndex === 2 ? "software" : "auto"
                            )
                            contentItem: Label {
                                leftPadding: 14
                                text: rendererSelector.displayText
                                color: textPrimary
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 10; color: inputBackground
                                border.width: rendererSelector.activeFocus ? 2 : 1
                                border.color: rendererSelector.activeFocus ? cyan : borderTone
                            }
                        }
                        Label {
                            text: window.t("settings.renderer_help", "AUTO is recommended. Change only for GPU or compatibility troubleshooting.")
                            color: muted; font.pixelSize: 10; wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                    Label {
                        visible: cockpit.restartRequired
                        text: window.t("settings.restart_required", "RESTART REQUIRED TO APPLY THE NEW MODE")
                        color: orange; font.pixelSize: 11; font.bold: true
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                    Label { text: window.t("settings.interface", "INTERFACE"); color: muted; font.pixelSize: 10; font.bold: true }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label { text: window.t("settings.language", "LANGUAGE"); color: muted; font.pixelSize: 10; font.bold: true }
                            Label {
                                text: window.t("settings.language_help", "Applies immediately and is saved locally")
                                color: muted; font.pixelSize: 9
                            }
                        }
                        ComboBox {
                            id: languageSelector
                            Layout.preferredWidth: 190
                            Layout.preferredHeight: 42
                            model: cockpit.interfaceLanguages
                            textRole: "label"
                            valueRole: "id"
                            currentIndex: {
                                for (var index = 0; index < count; ++index) {
                                    if (valueAt(index) === cockpit.interfaceLanguage)
                                        return index
                                }
                                return 0
                            }
                            onActivated: cockpit.setInterfaceLanguage(currentValue)
                            contentItem: Label {
                                leftPadding: 14
                                text: languageSelector.displayText
                                color: textPrimary
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 10; color: inputBackground
                                border.width: languageSelector.activeFocus ? 2 : 1
                                border.color: languageSelector.activeFocus ? cyan : borderTone
                            }
                        }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: divider }
                    RowLayout {
                        Layout.fillWidth: true
                            Label { text: window.t("settings.ui_scale", "UI SCALE"); color: muted; font.pixelSize: 10; font.bold: true }
                        Slider {
                            Layout.fillWidth: true
                            from: 1.00; to: 1.50; stepSize: 0.05
                            value: cockpit.uiScale
                            onMoved: cockpit.setUiScale(value)
                        }
                        Label {
                            text: Math.round(cockpit.uiScale * 100) + "%"
                            color: textPrimary; font.pixelSize: 13; font.bold: true
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Label { text: window.t("settings.design_skin", "DESIGN SKIN"); color: muted; font.pixelSize: 10; font.bold: true }
                        Label {
                            text: window.t("settings.design_help", "Color and accent preview · applies immediately and is saved locally")
                            color: muted; font.pixelSize: 9
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 8
                            Repeater {
                                model: [
                                    {"id": "arctic_alloy", "label": "ARCTIC ALLOY"},
                                    {"id": "navy", "label": "NAVY"},
                                    {"id": "neon_vector", "label": "NEON VECTOR"},
                                    {"id": "orbital_dawn", "label": "ORBITAL DAWN"},
                                    {"id": "crimson_dark", "label": "CRIMSON DARK"},
                                    {"id": "crimson_light", "label": "CRIMSON LIGHT"}
                                ]
                                delegate: Rectangle {
                                    required property var modelData
                                    width: 158; height: 54; radius: 10
                                    color: cockpit.theme === modelData.id ? active : panelRaised
                                    border.width: cockpit.theme === modelData.id ? 2 : 1
                                    border.color: cockpit.theme === modelData.id ? cyan : borderTone
                                    RowLayout {
                                        anchors.fill: parent; anchors.margins: 9; spacing: 9
                                        Rectangle {
                                            width: 20; height: 20; radius: 10
                                            color: window.themeSets[modelData.id].accent
                                            border.width: 1
                                            border.color: window.themeSets[modelData.id].textPrimary
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: modelData.label
                                            color: textPrimary; font.pixelSize: 9; font.bold: true
                                            elide: Text.ElideRight
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: cockpit.setTheme(modelData.id)
                                    }
                                }
                            }
                        }
                    }
                    CheckBox {
                        text: window.t("settings.reduce_motion", "Reduce interface motion")
                        checked: cockpit.reducedMotion
                        onToggled: cockpit.setReducedMotion(checked)
                    }
                    CheckBox {
                        text: window.t("settings.commander_popups", "Show Commander update popups")
                        checked: cockpit.commanderUpdatePopups
                        onToggled: cockpit.setCommanderUpdatePopups(checked)
                    }
                    CheckBox {
                        text: window.t("settings.enhanced_visuals", "Enhanced GPU atmosphere and depth")
                        checked: cockpit.enhancedVisuals
                        onToggled: cockpit.setEnhancedVisuals(checked)
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                        Label { text: window.t("settings.windows_behavior", "WINDOWS BEHAVIOR"); color: green; font.pixelSize: 11; font.bold: true }
                    CheckBox {
                        text: window.t("settings.tray_keep", "Keep EDEC running in the system tray when the window closes")
                        checked: cockpit.backgroundMode
                        enabled: cockpit.systemTrayAvailable
                        onToggled: cockpit.setBackgroundMode(checked)
                    }
                    CheckBox {
                        text: window.t("settings.autostart", "Start EDEC with Windows in background mode")
                        checked: cockpit.autostartEnabled
                        enabled: cockpit.backgroundMode
                        onToggled: cockpit.setAutostartEnabled(checked)
                    }
                    Label {
                        text: !cockpit.systemTrayAvailable
                              ? "SYSTEM TRAY UNAVAILABLE · Closing the window exits EDEC."
                              : cockpit.backgroundMode
                              ? "TRAY MODE ENABLED · Journal, inventory and EDDN continue after closing the window. Use EXIT EDEC in the tray to stop."
                              : "DISABLED BY DEFAULT · Closing the window exits EDEC."
                        color: cockpit.backgroundMode ? green : muted
                        font.pixelSize: 10; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: window.tf("status.runtime", "RUNTIME · %1", [cockpit.backgroundRuntimeStatus])
                            color: cockpit.backgroundRuntimeStatus === "RUNNING IN BACKGROUND"
                                   ? green : cyan
                            font.pixelSize: 10; font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: window.t("settings.single_instance", "ONE INSTANCE ONLY")
                            color: muted; font.pixelSize: 10; font.bold: true
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        CockpitButton {
                            Layout.fillWidth: true
                        text: cockpit.restartRequired
                              ? window.t("settings.restart_apply", "RESTART · APPLY CHANGES")
                              : window.t("settings.restart", "RESTART EDEC")
                            selected: cockpit.restartRequired
                            onClicked: cockpit.requestRestart()
                        }
                        CockpitButton {
                            Layout.fillWidth: true
                            text: window.t("settings.exit", "EXIT EDEC")
                            onClicked: cockpit.requestExit()
                        }
                    }
                    CockpitButton {
                        text: window.t("settings.onboarding", "OPEN ONBOARDING")
                        onClicked: cockpit.reopenOnboarding()
                    }
                    Item { Layout.fillHeight: true }
                }
            }

            ShadowCard {
                Layout.fillWidth: true
                Layout.preferredHeight: window.narrowWorkspace
                                        ? 650 : Math.max(620, settingsScroll.availableHeight)
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 22; spacing: 14
                            Label { text: window.t("settings.live_data", "LIVE DATA"); color: green; font.pixelSize: 13; font.bold: true }
                            Label { text: window.t("settings.journal_directory", "JOURNAL DIRECTORY"); color: muted; font.pixelSize: 10; font.bold: true }
                    TextField {
                        id: journalPathField
                        text: cockpit.journalPath
                        Layout.fillWidth: true
                        selectByMouse: true
                        placeholderText: window.t("connections.journal_path", "Elite Dangerous Journal directory")
                    }
                    CockpitButton {
                        text: window.t("connections.use_journal", "USE JOURNAL DIRECTORY")
                        Layout.fillWidth: true
                        onClicked: cockpit.setJournalPath(journalPathField.text)
                    }
                    CheckBox {
                        text: window.t("connections.journal_auto", "Automatically process Elite Journal updates")
                        checked: cockpit.journalAuto
                        onToggled: cockpit.setJournalAuto(checked)
                    }
                    CockpitButton {
                        text: window.t("connections.reload", "RELOAD JOURNAL & FLEET NOW")
                        Layout.fillWidth: true
                        onClicked: cockpit.reloadJournalNow()
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: borderTone; Layout.topMargin: 8 }
                            Label { text: window.t("settings.application", "APPLICATION"); color: cyan; font.pixelSize: 10; font.bold: true }
                    Label { text: "GPU Cockpit " + cockpit.appVersion; color: textPrimary; font.pixelSize: 16; font.bold: true }
                            Label { text: window.t("settings.bundled_data", "Project data is bundled with this release."); color: muted; font.pixelSize: 10 }
                    Item { Layout.fillHeight: true }
                }
            }
        }
        }
    }
        }
    }

    Loader {
        id: pageLoader6
        anchors.fill: parent
        active: window.currentPage === 6
        asynchronous: false
        sourceComponent: Component {
    ColumnLayout {
        id: connectionsPage
        objectName: "qa-page-connections"
        visible: true
        anchors.fill: parent
        anchors.leftMargin: sidebar.width + (window.compactSidebar ? 18 : 26)
        anchors.rightMargin: window.compactSidebar ? 18 : 26
        anchors.topMargin: window.compactSidebar ? 18 : 26
        anchors.bottomMargin: window.compactSidebar ? 18 : 26
        spacing: 14
        property int connectionMode: window.connectionPreviewMode
        onConnectionModeChanged: window.connectionPreviewMode = connectionMode

        SettingsHeader {
            qaName: "qa-header-connections"
            heading: "CONNECTIONS"
            subheading: "Optional services with independent privacy controls"
        }
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            RowLayout {
                objectName: "qa-primary-connections"
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                CockpitButton { text: "INARA"; selected: connectionsPage.connectionMode === 0; onClicked: connectionsPage.connectionMode = 0 }
                    CockpitButton { text: window.t("connections.eddn_tab", "EDDN & STATE FINDS"); selected: connectionsPage.connectionMode === 1; accentColor: green; onClicked: connectionsPage.connectionMode = 1 }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Repeater {
                model: cockpit.serviceStatus
                delegate: ShadowCard {
                    required property var modelData
                    Layout.fillWidth: true; Layout.preferredHeight: 82
                    accent: modelData.healthy ? green : orange
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 3
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: modelData.name; color: muted; font.pixelSize: 9; font.bold: true }
                            Item { Layout.fillWidth: true }
                            Label { text: modelData.status; color: modelData.healthy ? green : orange; font.pixelSize: 11; font.bold: true }
                        }
                        Label { text: modelData.detail; color: textSecondary; font.pixelSize: 9; elide: Text.ElideRight; Layout.fillWidth: true }
                    }
                }
            }
        }

        RowLayout {
            visible: connectionsPage.connectionMode === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 14

            ShadowCard {
                Layout.preferredWidth: window.compactSidebar ? 470 : 570
                Layout.fillHeight: true
                accent: cyan
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 22; spacing: 12
                                Label { text: window.t("connections.inara_title", "INARA COMMANDER CONNECTION"); color: cyan; font.pixelSize: 14; font.bold: true }
                    Label {
                        text: window.t("connections.inara_privacy", "Nothing is sent until you enable consent. The API key is stored only in your local app profile and is never shown in logs or receipts.")
                        color: textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                                    Label { text: window.t("connections.commander", "COMMANDER"); color: muted; font.pixelSize: 10; font.bold: true }
                    TextField {
                        id: inaraCommanderField
                        Layout.fillWidth: true
                        text: cockpit.inaraCommander
                        placeholderText: window.t("connections.commander_name", "Commander name")
                    }
                    Label {
                                    text: cockpit.inaraKeyConfigured
                                          ? window.t("connections.api_configured", "API KEY · CONFIGURED")
                                          : window.t("connections.api_key", "API KEY")
                        color: cockpit.inaraKeyConfigured ? green : muted
                        font.pixelSize: 10
                        font.bold: true
                    }
                    TextField {
                        id: inaraKeyField
                        Layout.fillWidth: true
                        // Do not bind the secret into the field. An empty field on save
                        // keeps the existing key; use CLEAR KEY to remove it.
                        echoMode: TextInput.Password
                        placeholderText: cockpit.inaraKeyConfigured
                            ? "Paste new key to replace (leave empty to keep current)"
                            : "Paste INARA API key"
                    }
                    CheckBox {
                        id: inaraConsentBox
                        text: window.t("connections.inara_allow", "Allow this app to contact INARA")
                        checked: cockpit.inaraConsent
                    }
                    CheckBox {
                        id: inaraAutoBox
                        text: window.t("connections.inara_auto", "Allow automatic material snapshots after Journal changes")
                        checked: cockpit.inaraAutoSync
                        enabled: inaraConsentBox.checked
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        CockpitButton {
                            text: window.t("connections.save_local", "SAVE LOCALLY"); selected: true
                            onClicked: {
                                cockpit.saveInaraConfig(
                                    inaraKeyField.text,
                                    inaraCommanderField.text,
                                    inaraConsentBox.checked,
                                    inaraAutoBox.checked
                                )
                                // Clear the field after save so the key is not kept visible in UI
                                inaraKeyField.text = ""
                            }
                        }
                                        CockpitButton { text: window.t("connections.test", "TEST"); enabled: !cockpit.inaraBusy; onClicked: cockpit.testInaraConnection() }
                        CockpitButton {
                            text: window.t("connections.clear_key", "CLEAR KEY")
                                            helpText: window.t("connections.clear_key_help", "Delete the locally stored INARA API key")
                            onClicked: {
                                cockpit.clearInaraKey()
                                inaraKeyField.text = ""
                            }
                        }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                                Label { text: window.t("connections.commander_actions", "COMMANDER ACTIONS"); color: green; font.pixelSize: 11; font.bold: true }
                    CockpitButton {
                        text: window.t("connections.send_snapshot", "SEND CURRENT MATERIAL SNAPSHOT")
                        Layout.fillWidth: true
                        enabled: !cockpit.inaraBusy
                        onClicked: cockpit.syncInaraMaterials()
                    }
                    CockpitButton {
                        text: window.t("connections.import_fleet", "IMPORT FLEET · KEEP EXISTING PLANS")
                        Layout.fillWidth: true
                        enabled: !cockpit.inaraBusy
                        onClicked: cockpit.importInaraFleet()
                    }
                    Item { Layout.fillHeight: true }
                    Label {
                                text: cockpit.inaraBusy ? window.t("status.working", "WORKING…") : cockpit.inaraStatus
                        color: cockpit.inaraBusy ? orange
                               : cockpit.inaraStatus.indexOf("FAILED") === 0 ? error : green
                        wrapMode: Text.WordWrap; Layout.fillWidth: true; font.bold: true
                    }
                }
            }

            ShadowCard {
                Layout.fillWidth: true
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 22; spacing: 12
                                Label { text: window.t("connections.receipts", "RECEIPTS"); color: orange; font.pixelSize: 14; font.bold: true }
                    Label {
                        text: window.t("connections.delivery_privacy", "Only delivery status is retained — never credentials or transmitted inventory.")
                        color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    ListView {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        model: cockpit.inaraReceipts
                        spacing: 9
                        clip: true
                        delegate: Rectangle {
                            required property var modelData
                            width: ListView.view.width
                            height: 88
                            radius: 11
                            color: panelRaised
                            border.width: 1
                            border.color: borderTone
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 12; spacing: 3
                                Label {
                                    text: modelData.operation || "INARA EVENT"
                                    color: textPrimary; font.bold: true
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                }
                                Label {
                                    text: (modelData.timestamp || "") + " · HTTP " + (modelData.httpStatus || "?") + " · " + (modelData.elapsedMs || 0) + " ms"
                                    color: green; font.pixelSize: 11
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                }
                                Label {
                                    visible: !!modelData.detail; text: modelData.detail || ""
                                    color: muted; font.pixelSize: 11
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                }
                            }
                        }
                        EmptyState {
                            anchors.centerIn: parent
                            visible: parent.count === 0
                            symbol: "↗"
                            title: window.t("connections.no_inara", "NO INARA REQUESTS YET")
                            detail: window.t("connections.no_inara_help", "Receipts appear here after an explicitly approved connection action.")
                            tone: cyan
                        }
                    }
                }
            }
        }

        RowLayout {
            visible: connectionsPage.connectionMode === 1
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 14

            ShadowCard {
                Layout.preferredWidth: window.compactSidebar ? 430 : 510
                Layout.fillHeight: true
                accent: green
                ScrollView {
                    id: eddnCommunityScroll
                    anchors.fill: parent; anchors.margins: 14
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ScrollBar.vertical: CockpitScrollBar {}
                ColumnLayout {
                    width: eddnCommunityScroll.availableWidth
                    spacing: 11
                                Label { text: window.t("connections.eddn_title", "EDDN COMMUNITY NETWORK"); color: green; font.pixelSize: 14; font.bold: true }
                    Label {
                        text: window.t("connections.eddn_privacy", "Uploads contain only schema-approved public galaxy and station data. Commander identifiers and all *_Localised fields are removed before queueing.")
                        color: textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredHeight: edmcVerdictColumn.implicitHeight + 20
                        radius: 9
                        color: cockpit.edmcParallelStatus.tone === "READY" ? successBackground : warningBackground
                        border.width: 1
                        border.color: cockpit.edmcParallelStatus.tone === "READY" ? green : orange
                        ColumnLayout {
                            id: edmcVerdictColumn
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.top: parent.top; anchors.margins: 10; spacing: 4
                                        Label { text: window.t("connections.edmc_parallel", "EDMC PARALLEL STILL NEEDED?"); color: textSecondary; font.pixelSize: 9; font.bold: true }
                            Label {
                                text: cockpit.edmcParallelStatus.verdict
                                color: cockpit.edmcParallelStatus.tone === "READY" ? green : orange
                                font.pixelSize: 13; font.bold: true
                            }
                            Label { text: cockpit.edmcParallelStatus.reason; color: textPrimary; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 9 }
                            Label { text: cockpit.edmcParallelStatus.stationNote; color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 9 }
                                        Label { text: window.tf("connections.limit", "LIMIT · %1", [cockpit.edmcParallelStatus.capiNote]); color: orange; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 9 }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; height: 58; radius: 9
                        color: successBackground; border.width: 1; border.color: success
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 9; spacing: 2
                            Label {
                                text: window.tf("status.eddn_parity", "JOURNAL EDDN PARITY · %1", [cockpit.eddnParity.status])
                                color: green; font.pixelSize: 10; font.bold: true
                            }
                            Label {
                                text: cockpit.eddnParity.supported + " / " + cockpit.eddnParity.total
                                      + " SCHEMAS · " + cockpit.eddnParity.journalSchemas + " JOURNAL · "
                                      + cockpit.eddnParity.stationSchemas + " STATION · 1 FRONTIER CAPI PATH EXCLUDED · VALIDATED "
                                      + cockpit.eddnParity.validatedAt
                                color: textSecondary; font.pixelSize: 9
                            }
                        }
                    }
                    CheckBox {
                        id: eddnConsentBox
                        text: window.t("connections.eddn_allow", "Allow this app to contact EDDN")
                        checked: cockpit.eddnConsent
                    }
                    CheckBox {
                        id: eddnUploadBox
                        text: window.t("connections.eddn_share", "Share supported new Journal events anonymously")
                        checked: cockpit.eddnUploadEnabled
                        enabled: eddnConsentBox.checked
                    }
                    CheckBox {
                        id: eddnListenerBox
                        text: window.t("connections.eddn_receive", "Receive live State Finds intelligence")
                        checked: cockpit.eddnListenerEnabled
                        enabled: eddnConsentBox.checked
                    }
                    CockpitButton {
                        text: window.t("connections.eddn_save", "SAVE EDDN SETTINGS"); selected: true
                        Layout.fillWidth: true
                        onClicked: cockpit.saveEddnConfig(eddnConsentBox.checked, eddnUploadBox.checked, eddnListenerBox.checked)
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                                Label { text: window.t("connections.station_data", "STATION DATA"); color: orange; font.pixelSize: 10; font.bold: true }
                    Label {
                        text: cockpit.eddnStationStatus
                        color: textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    Repeater {
                        model: cockpit.eddnStationSnapshots
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true; Layout.preferredHeight: 52; radius: 8
                            color: panelRaised; border.width: 1
                            border.color: modelData.status === "SENT" ? green
                                          : (modelData.status === "QUEUED" || modelData.status === "SENDING" || modelData.status === "RETRY") ? orange
                                          : borderTone
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 8; spacing: 2
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label { text: modelData.kind; color: textPrimary; font.bold: true; font.pixelSize: 10 }
                                    Item { Layout.fillWidth: true }
                                    Label {
                                        text: modelData.status
                                        color: modelData.status === "SENT" ? green
                                               : modelData.status === "FAILED" || modelData.status === "INVALID" ? error
                                               : modelData.status === "NOT CURRENT" || modelData.status === "STALE" ? orange : cyan
                                        font.bold: true; font.pixelSize: 9
                                    }
                                }
                                Label {
                                    text: (modelData.system && modelData.station
                                           ? modelData.system + " · " + modelData.station + " · " + modelData.age
                                           : modelData.detail)
                                    color: muted; font.pixelSize: 9; elide: Text.ElideRight; Layout.fillWidth: true
                                }
                            }
                        }
                    }
                    Label {
                        text: window.t("connections.market_scope", "MARKET · OUTFITTING · SHIPYARD\nBlack-market schema retired: prohibitions travel through Market data when Elite supplies them.")
                        color: muted; font.pixelSize: 10; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                }
                }
            }

            ShadowCard {
                Layout.fillWidth: true
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 22; spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                                Label { text: window.t("connections.upload_queue", "UPLOAD QUEUE & RECEIPTS"); color: orange; font.pixelSize: 14; font.bold: true }
                        Item { Layout.fillWidth: true }
                                    CockpitButton { text: window.t("connections.retry_failed", "RETRY FAILED"); onClicked: cockpit.retryEddnFailed() }
                                    CockpitButton { text: window.t("connections.clear_sent", "CLEAR SENT"); helpText: window.t("connections.clear_sent_help", "Remove delivered receipts from the local queue history"); onClicked: cockpit.clearEddnSent() }
                    }
                    Label {
                                text: cockpit.eddnBusy ? window.t("status.sending", "SENDING…") : cockpit.eddnStatus
                        color: cockpit.eddnBusy ? orange
                               : cockpit.eddnStatus.indexOf("FAILED") === 0 ? error : green
                        wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredHeight: deliveryProofColumn.implicitHeight + 22
                        radius: 10; color: panelRaised; border.width: 1; border.color: borderTone
                        ColumnLayout {
                            id: deliveryProofColumn
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.top: parent.top; anchors.margins: 11; spacing: 5
                                            Label { text: window.t("connections.delivery_proof", "DELIVERY PROOF"); color: cyan; font.pixelSize: 11; font.bold: true }
                            Label {
                                text: window.tf("connections.queue_prefix", "QUEUE · %1 WAITING · ", [cockpit.eddnDeliverySummary.waiting])
                                      + cockpit.eddnDeliverySummary.sent + " SENT · "
                                      + cockpit.eddnDeliverySummary.failed + " FAILED"
                                color: textPrimary; font.bold: true; font.pixelSize: 10
                            }
                            Label {
                                text: cockpit.eddnDeliverySummary.lastSuccessAt
                                      ? "LAST ACCEPTED · " + cockpit.eddnDeliverySummary.lastSuccessAt
                                        + " · " + (cockpit.eddnDeliverySummary.lastSuccessEvent || cockpit.eddnDeliverySummary.lastSuccessSchema)
                                      : "LAST ACCEPTED · No gateway receipt recorded yet"
                                color: cockpit.eddnDeliverySummary.lastSuccessAt ? green : muted
                                wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 10
                            }
                            Label {
                                visible: Boolean(cockpit.eddnDeliverySummary.lastError)
                                text: window.tf("status.last_error", "LAST ERROR · %1", [cockpit.eddnDeliverySummary.lastError])
                                color: error; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 10
                            }
                            Label {
                                visible: Boolean(cockpit.eddnDeliverySummary.nextRetryAt)
                                text: window.tf("status.retry_pending", "RETRY PENDING · %1", [cockpit.eddnDeliverySummary.retry])
                                      + " JOB(S) · NEXT ATTEMPT "
                                      + cockpit.eddnDeliverySummary.nextRetryAt
                                color: orange; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 10
                            }
                            Label {
                                visible: Boolean(cockpit.eddnDeliverySummary.lastNotShareable)
                                text: window.tf("status.not_shareable", "NOT SHAREABLE · %1", [cockpit.eddnDeliverySummary.lastNotShareable])
                                color: orange; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 10
                            }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: traderSyncColumn.implicitHeight + 22
                        radius: 10; color: panelRaised
                        border.width: 1; border.color: borderTone
                        ColumnLayout {
                            id: traderSyncColumn
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.top: parent.top; anchors.margins: 11
                            spacing: 5
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: window.t("connections.spansh", "SPANSH CATALOGS")
                                    color: cyan; font.pixelSize: 11; font.bold: true
                                }
                                Item { Layout.fillWidth: true }
                                CockpitButton {
                                text: cockpit.spanshCatalogSyncBusy
                                      ? window.t("status.updating", "UPDATING…")
                                      : window.t("connections.update_spansh", "UPDATE VIA SPANSH · ALL")
                                    enabled: !cockpit.spanshCatalogSyncBusy
                                    onClicked: cockpit.updateSpanshCatalogs()
                                }
                            }
                            Label {
                                text: cockpit.spanshCatalogSyncStatus
                                color: cockpit.spanshCatalogSyncStatus.indexOf("failed") >= 0 ? orange : textSecondary
                                wrapMode: Text.WordWrap; Layout.fillWidth: true
                                font.pixelSize: 10
                            }
                            Label {
                                text: window.t("connections.spansh_help", "Updates Material Traders plus Human/Guardian Tech Brokers. The bundled 1,622-station catalog stays offline-ready; Sirius remains a separate bundled specialist catalog.")
                                color: muted; wrapMode: Text.WordWrap
                                Layout.fillWidth: true; font.pixelSize: 9
                            }
                        }
                    }
                    Label {
                        text: window.t("connections.http_help", "A green HTTP 200 receipt confirms gateway acceptance. Permanent schema rejections are never retried automatically.")
                        color: muted; font.pixelSize: 10; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    ListView {
                        id: eddnQueueList
                        Layout.fillWidth: true; Layout.fillHeight: true
                        model: cockpit.eddnQueue; spacing: 8; clip: true
                        ScrollBar.vertical: CockpitScrollBar {}
                        delegate: Rectangle {
                            required property var modelData
                            width: eddnQueueList.width; height: 102; radius: 10
                            color: panelRaised
                            border.width: 1
                            border.color: modelData.status === "sent" ? green : modelData.status === "failed" ? error : orange
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 11; spacing: 3
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label { text: modelData.eventName || "EDDN EVENT"; color: textPrimary; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Label { text: (modelData.status || "").toUpperCase(); color: modelData.status === "sent" ? green : orange; font.bold: true; font.pixelSize: 10 }
                                }
                                    Label { text: window.tf("connections.attempt", "%1 · attempt %2", [modelData.schema || "", modelData.attempts || 0]); color: cyan; font.pixelSize: 10 }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label { text: modelData.result || modelData.error || modelData.sentAt || modelData.created || ""; color: muted; font.pixelSize: 10; elide: Text.ElideRight; Layout.fillWidth: true }
                                    CockpitButton {
                                        visible: modelData.retryable
                                        text: window.t("common.retry", "RETRY")
                                        onClicked: cockpit.retryEddnJob(modelData.id)
                                    }
                                }
                            }
                        }
                        EmptyState {
                            anchors.centerIn: parent
                            visible: parent.count === 0
                            symbol: "✓"
                            title: window.t("connections.no_eddn", "NO EDDN UPLOADS WAITING")
                            detail: window.t("connections.no_eddn_help", "No supported new Journal events are waiting. New uploads will appear here before delivery.")
                            tone: green
                        }
                    }
                }
            }
        }
    }
        }
    }

    Loader {
        id: pageLoader7
        anchors.fill: parent
        active: window.currentPage === 7
        asynchronous: false
        sourceComponent: Component {
    ColumnLayout {
        id: diagnosticsPage
        objectName: "qa-page-diagnostics"
        visible: true
        anchors.fill: parent
        anchors.leftMargin: sidebar.width + (window.compactSidebar ? 18 : 26)
        anchors.rightMargin: window.compactSidebar ? 18 : 26
        anchors.topMargin: window.compactSidebar ? 18 : 26
        anchors.bottomMargin: window.compactSidebar ? 18 : 26
        spacing: 14
        property var health: cockpit.journalHealth

        SettingsHeader {
            qaName: "qa-header-diagnostics"
            heading: "DIAGNOSTICS & JOURNAL HEALTH"
            subheading: "Local health checks, parser state, logs and crash recovery"
        }
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            RowLayout {
                objectName: "qa-primary-diagnostics"
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                CheckBox {
                    text: window.t("diagnostics.advanced_logging", "Advanced logging")
                    checked: cockpit.debugMode
                    onToggled: cockpit.setDebugMode(checked)
                }
                        CockpitButton { text: window.t("diagnostics.copy_report", "COPY REPORT"); onClicked: cockpit.copyDiagnostics() }
                        CockpitButton { text: window.t("common.refresh", "REFRESH"); selected: true; onClicked: cockpit.refreshDiagnostics() }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Repeater {
                model: [
                            {"label": window.t("diagnostics.journal", "JOURNAL"), "value": window.localizedStatus(diagnosticsPage.health.status), "tone": diagnosticsPage.health.parserOk ? green : orange},
                            {"label": window.t("diagnostics.watcher", "WATCHER"), "value": window.localizedStatus(diagnosticsPage.health.watcherActive ? "ACTIVE" : "STOPPED"), "tone": diagnosticsPage.health.watcherActive ? green : orange},
                            {"label": window.t("diagnostics.last_event", "LAST EVENT"), "value": diagnosticsPage.health.lastEvent || window.t("status.value.none", "NONE"), "tone": cyan},
                            {"label": window.t("status.renderer", "RENDERER"), "value": diagnosticsPage.health.renderer, "tone": cyan}
                ]
                delegate: ShadowCard {
                    required property var modelData
                    Layout.fillWidth: true; Layout.preferredHeight: 92
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 15
                        Label { text: modelData.label; color: muted; font.pixelSize: 10; font.bold: true }
                        Label { text: modelData.value; color: modelData.tone; font.pixelSize: 19; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 14
            ShadowCard {
                Layout.preferredWidth: window.compactSidebar ? 360 : 430; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 18; spacing: 10
                            Label { text: window.t("diagnostics.journal_pipeline", "JOURNAL PIPELINE"); color: cyan; font.pixelSize: 13; font.bold: true }
                    Repeater {
                        model: [
                            {"name": "Directory", "value": diagnosticsPage.health.directoryExists ? "Available" : "Missing"},
                            {"name": "Journal files", "value": diagnosticsPage.health.fileCount},
                            {"name": "Latest file", "value": diagnosticsPage.health.latestFile || "None"},
                            {"name": "Age", "value": diagnosticsPage.health.ageSeconds >= 0 ? diagnosticsPage.health.ageSeconds + " seconds" : "Unknown"},
                            {"name": "File size", "value": diagnosticsPage.health.sizeBytes + " bytes"},
                            {"name": "JSON parser", "value": diagnosticsPage.health.parserOk ? "Last line valid" : "Needs attention"},
                            {"name": "Poll interval", "value": diagnosticsPage.health.pollIntervalMs + " ms"}
                        ]
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true; height: 48; radius: 9
                            color: panelRaised
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 11
                                Label { text: modelData.name; color: muted; font.pixelSize: 11 }
                                Item { Layout.fillWidth: true }
                                Label { text: modelData.value; color: textPrimary; font.pixelSize: 11; font.bold: true; elide: Text.ElideLeft; Layout.maximumWidth: 250 }
                            }
                        }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                                    Label { text: window.t("diagnostics.service_pipeline", "SERVICE PIPELINE"); color: green; font.pixelSize: 11; font.bold: true }
                    Repeater {
                        model: cockpit.serviceStatus
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Label { text: modelData.name; color: muted; font.pixelSize: 10; font.bold: true; Layout.preferredWidth: 70 }
                            Label { text: modelData.status; color: modelData.healthy ? green : orange; font.pixelSize: 10; font.bold: true; Layout.preferredWidth: 70 }
                            Label { text: modelData.detail; color: textSecondary; font.pixelSize: 9; elide: Text.ElideRight; Layout.fillWidth: true }
                        }
                    }
                    Label {
                        visible: !!diagnosticsPage.health.error
                        text: diagnosticsPage.health.error
                        color: orange; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                    Item { Layout.fillHeight: true }
                }
            }

            ShadowCard {
                Layout.fillWidth: true; Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 18; spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                            Label { text: window.t("diagnostics.local_log", "LOCAL LOG"); color: textPrimary; font.pixelSize: 13; font.bold: true }
                        Item { Layout.fillWidth: true }
                            Label { text: window.tf("diagnostics.lines", "%1 LINES", [cockpit.diagnosticLogs.length]); color: muted; font.pixelSize: 10 }
                                CockpitButton { text: window.t("diagnostics.clear_log", "CLEAR LOG"); helpText: window.t("diagnostics.clear_log_help", "Delete the local diagnostic log"); onClicked: cockpit.clearDiagnosticLog() }
                    }
                    ListView {
                        id: diagnosticLogList
                        Layout.fillWidth: true; Layout.fillHeight: true
                        model: cockpit.diagnosticLogs
                        clip: true; spacing: 3
                        ScrollBar.vertical: CockpitScrollBar {}
                        delegate: Label {
                            required property string modelData
                            width: diagnosticLogList.width
                            text: modelData
                            color: textSecondary; font.family: "Consolas"; font.pixelSize: 10
                            wrapMode: Text.WrapAnywhere
                        }
                        onCountChanged: positionViewAtEnd()
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                                    Label { text: window.t("diagnostics.crash_reports", "CRASH REPORTS"); color: orange; font.pixelSize: 11; font.bold: true }
                    Label {
                        Layout.fillWidth: true
                        text: cockpit.crashReports.length
                              ? cockpit.crashReports.map(function(row) { return row.name }).join(" · ")
                              : "No captured Python crash reports."
                        color: cockpit.crashReports.length ? orange : green
                        font.pixelSize: 10; wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }
        }
    }

    Rectangle {
        visible: cockpit.craftConfirmation.length > 0
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 22
        width: Math.min(implicitWidth, parent.width - 80)
        implicitWidth: craftToastText.implicitWidth + 48
        height: 52
        radius: 14
        color: successBackground
        border.width: 2
        border.color: green
        z: 1000
        Label {
            id: craftToastText
            anchors.centerIn: parent
            text: window.tf("status.confirmed", "✓  %1", [cockpit.craftConfirmation])
            color: green
            font.pixelSize: 13
            font.bold: true
        }
    }

    Rectangle {
        visible: window.feedbackVisible && cockpit.craftConfirmation.length === 0 && window.enhancedVisuals
        anchors.right: feedbackToast.right
        anchors.bottom: feedbackToast.bottom
        anchors.rightMargin: -7
        anchors.bottomMargin: -7
        width: feedbackToast.width
        height: feedbackToast.height
        radius: feedbackToast.radius
        color: backgroundPrimary
        opacity: 0.48
        z: 998
    }

    Rectangle {
        id: feedbackToast
        visible: window.feedbackVisible && cockpit.craftConfirmation.length === 0
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: 24
        anchors.bottomMargin: 24
        width: Math.min(520, parent.width - 48)
        height: 66
        radius: 13
        color: panelRaised
        border.width: 1
        border.color: cyan
        z: 999
        opacity: visible ? 1.0 : 0.0
        scale: visible ? 1.0 : 0.98
        Behavior on opacity { enabled: !window.reducedMotion; NumberAnimation { duration: 150 } }
        Behavior on scale { enabled: !window.reducedMotion; NumberAnimation { duration: 150 } }
        RowLayout {
            anchors.fill: parent
            anchors.margins: 13
            spacing: 11
            Rectangle {
                Layout.preferredWidth: 8
                Layout.preferredHeight: 8
                radius: 4
                color: green
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                    Label { text: window.t("common.commander_update", "COMMANDER UPDATE"); color: cyan; font.pixelSize: 9; font.bold: true }
                Label {
                    Layout.fillWidth: true
                    text: window.feedbackMessage
                    color: textPrimary
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }
            }
            Label { text: "F1"; color: muted; font.pixelSize: 9 }
        }
    }

    FileDialog {
        id: buildImportFileDialog
        title: window.t("dialog.import.choose_file", "Select build file")
        nameFilters: [window.t("dialog.import.json_files", "JSON build files (*.json)")]
        onAccepted: {
            buildImportSource.text = selectedFile.toString()
            buildImportPreviewTimer.restart()
        }
    }

    Dialog {
        id: buildImportDialog
        objectName: "qa-dialog-build-import"
        title: window.t("dialog.import.title", "Import Engineering Build")
        modal: true
        anchors.centerIn: parent
        width: Math.max(560, Math.min(1280, window.width - 36))
        height: Math.max(540, Math.min(940, window.height - 30))
        standardButtons: Dialog.Close
        onClosed: cockpit.clearBuildImport()
        Timer {
            id: buildImportPreviewTimer
            interval: 450
            repeat: false
            onTriggered: {
                if (buildImportSource.text.trim().length > 0
                        && buildImportTarget.currentText.length > 0)
                    cockpit.previewBuildImport(buildImportSource.text,
                                               buildImportTarget.currentText)
            }
        }
        contentItem: ColumnLayout {
            spacing: 10
            Label {
                Layout.fillWidth: true
                text: window.t("dialog.import.formats", "CORIOLIS JSON · EDSY/SLEF · JOURNAL LOADOUT")
                color: cyan; font.pixelSize: 12; font.bold: true
            }
            Label {
                Layout.fillWidth: true
                text: window.t("import.instructions", "Select or drop a JSON build file. You can also paste exported JSON/SLEF or an embedded-data link.")
                color: muted; font.pixelSize: 11; wrapMode: Text.WordWrap
            }
            CockpitButton {
                Layout.fillWidth: true
                text: window.t("dialog.import.choose_file", "SELECT JSON BUILD FILE")
                selected: true
                onClicked: buildImportFileDialog.open()
            }
            RowLayout {
                Layout.fillWidth: true
                    Label { text: window.t("dialog.import.target_ship", "TARGET SHIP"); color: orange; font.pixelSize: 10; font.bold: true }
                ComboBox {
                    id: buildImportTarget
                    Layout.fillWidth: true
                    model: cockpit.ships
                    onCurrentTextChanged: {
                        if (buildImportSource.text.trim().length > 0)
                            buildImportPreviewTimer.restart()
                    }
                }
                CockpitButton {
                    text: window.t("dialog.import.preview", "PREVIEW")
                    selected: true
                    enabled: buildImportSource.text.trim().length > 0
                             && buildImportTarget.currentText.length > 0
                    onClicked: cockpit.previewBuildImport(
                                   buildImportSource.text,
                                   buildImportTarget.currentText)
                }
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.minimumHeight: 170
                Layout.preferredHeight: Math.max(220, Math.min(340, buildImportDialog.height * 0.34))
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AsNeeded
                ScrollBar.vertical.policy: ScrollBar.AsNeeded
                TextArea {
                    id: buildImportSource
                    placeholderText: window.t("import.placeholder", "Drop a .json file here, or paste Coriolis JSON / EDSY-SLEF / embedded-data link")
                    wrapMode: TextEdit.NoWrap
                    font.family: "Consolas"
                    font.pixelSize: 10
                    selectByMouse: true
                    persistentSelection: true
                    onTextChanged: {
                        if (text.trim().length > 0)
                            buildImportPreviewTimer.restart()
                        else
                            cockpit.clearBuildImport()
                    }
                }
                DropArea {
                    anchors.fill: parent
                    onDropped: function(drop) {
                        if (drop.hasUrls && drop.urls.length > 0) {
                            buildImportSource.text = drop.urls[0].toString()
                            buildImportPreviewTimer.restart()
                            drop.acceptProposedAction()
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: (cockpit.buildImportPreview.source || window.t("import.no_preview", "NO PREVIEW"))
                          + " · " + (cockpit.buildImportPreview.shipType || "UNKNOWN SHIP")
                          + (cockpit.buildImportPreview.status
                             ? " · " + cockpit.buildImportPreview.status : "")
                    color: cockpit.buildImportPreview.compatible
                           && cockpit.buildImportPreview.status === "COMPLETE"
                           ? green : orange
                    font.pixelSize: 10; font.bold: true
                }
                Item { Layout.fillWidth: true }
                Label {
                    text: window.tf("import.mapped", "%1 IMPORT ITEMS MAPPED", [cockpit.buildImportPreview.recognized || 0])
                          + ((cockpit.buildImportPreview.moduleChanges || 0) > 0
                             ? " · " + cockpit.buildImportPreview.moduleChanges + " MODULE SWAPS" : "")
                          + ((cockpit.buildImportPreview.partial || 0) > 0
                             ? " · " + cockpit.buildImportPreview.partial + " PARTIAL" : "")
                    color: (cockpit.buildImportPreview.partial || 0) > 0 ? orange : cyan
                    font.pixelSize: 10; font.bold: true
                }
            }
            ListView {
                id: buildImportPreviewList
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 150
                clip: true; spacing: 6
                model: cockpit.buildImportPreview.rows || []
                ScrollBar.vertical: CockpitScrollBar {}
                boundsBehavior: Flickable.StopAtBounds
                delegate: Rectangle {
                    required property var modelData
                    width: buildImportPreviewList.width - 10
                    height: 74; radius: 9
                    color: modelData.status === "ready" ? successBackground
                           : modelData.status === "partial" ? active
                           : modelData.status === "warning" ? errorBackground : panelRaised
                    border.width: 1
                    border.color: modelData.status === "ready" ? green
                                  : modelData.status === "partial" ? orange
                                  : modelData.status === "warning" ? error : borderTone
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 10; spacing: 2
                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: modelData.slot; color: cyan; font.pixelSize: 10; font.bold: true }
                            Label { text: modelData.module; color: textPrimary; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                            Label {
                                text: modelData.status === "ready"
                                      ? (modelData.planMode === "module_only"
                                         ? window.t("import.module_swap", "MODULE SWAP")
                                         : modelData.planMode === "experimental_only"
                                         ? window.t("import.experimental_only", "EXP ONLY")
                                         : "G" + modelData.grade
                                           + (modelData.experimental ? " + EXP" : ""))
                                      : modelData.status === "partial" ? window.t("import.partial", "PARTIAL")
                                      : window.localizedStatus(modelData.status)
                                color: modelData.status === "ready" ? green : orange
                                font.pixelSize: 10; font.bold: true
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            text: modelData.status === "ready"
                                  ? (modelData.planMode === "module_only"
                                     ? window.t("import.module_swap_help", "Track desired module for this physical slot")
                                     : (modelData.blueprint || "Experimental only")
                                       + (modelData.experimental ? " · " + modelData.experimental : ""))
                                  : (modelData.status === "partial"
                                     ? (modelData.blueprint || "Experimental only")
                                       + (modelData.experimental ? " · " + modelData.experimental : "")
                                       + " · " + modelData.detail
                                     : modelData.detail)
                            color: muted; font.pixelSize: 10; elide: Text.ElideRight
                        }
                    }
                }
            }
            Rectangle {
                visible: (cockpit.buildImportPreview.warnings || []).length > 0
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(110, buildImportWarnings.implicitHeight + 20)
                radius: 9
                color: cockpit.buildImportPreview.compatible ? active : errorBackground
                border.width: 1
                border.color: cockpit.buildImportPreview.compatible ? orange : error
                Label {
                    id: buildImportWarnings
                    anchors.fill: parent; anchors.margins: 10
                    text: (cockpit.buildImportPreview.warnings || []).join("\n")
                    color: orange; font.pixelSize: 10; wrapMode: Text.WordWrap
                    elide: Text.ElideRight; maximumLineCount: 5
                }
            }
            Label {
                Layout.fillWidth: true
                visible: (cockpit.buildImportPreview.actionMessage || "").length > 0
                text: cockpit.buildImportPreview.actionMessage || ""
                color: cockpit.buildImportPreview.actionError ? orange : green
                font.pixelSize: 10
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: window.t("import.safety", "Existing plans stay intact; exact duplicates are skipped. Imported target modules remain unbound until confirmed by Journal.")
                    color: muted; font.pixelSize: 9; wrapMode: Text.WordWrap
                }
                CockpitButton {
                    text: window.t("dialog.import.apply", "APPLY TO WISHLIST")
                    selected: true
                    enabled: cockpit.buildImportPreview.compatible
                             && (cockpit.buildImportPreview.recognized || 0) > 0
                    onClicked: cockpit.applyBuildImport()
                }
            }
        }
    }

    Dialog {
        id: logbookDetailDialog
        objectName: "qa-dialog-logbook-detail"
        title: window.t("dialog.logbook.title", "Logbook Entry")
        modal: true
        anchors.centerIn: parent
        width: Math.min(680, window.width - 80)
        height: Math.min(760, window.height - 80)
        standardButtons: Dialog.Close
        onOpened: logbookNoteEditor.text = cockpit.selectedLogbookEntry.note || ""
        onClosed: cockpit.clearSelectedLogbookEntry()
        contentItem: ColumnLayout {
            spacing: 10
            Label {
                text: cockpit.selectedLogbookEntry.title || window.t("logbook.journal_event", "Journal event")
                color: textPrimary; font.pixelSize: 18; font.bold: true
            }
            Label {
                text: (cockpit.selectedLogbookEntry.event || "")
                      + " · " + (cockpit.selectedLogbookEntry.timestamp || "")
                color: cyan; font.pixelSize: 10; font.bold: true
            }
            Label {
                Layout.fillWidth: true
                text: cockpit.selectedLogbookEntry.summary || ""
                color: muted; font.pixelSize: 12; wrapMode: Text.WordWrap
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
            Repeater {
                model: Object.keys(cockpit.selectedLogbookEntry.details || ({})).map(function(key) {
                    return {"label": key, "value": cockpit.selectedLogbookEntry.details[key]}
                })
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    Label { text: modelData.label.toUpperCase(); color: orange; font.pixelSize: 9; font.bold: true; Layout.preferredWidth: 130 }
                    Label { text: modelData.value; color: textPrimary; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
                Label { text: window.t("dialog.logbook.commander_note", "COMMANDER NOTE"); color: orange; font.pixelSize: 10; font.bold: true }
            TextArea {
                id: logbookNoteEditor
                Layout.fillWidth: true
                Layout.preferredHeight: 110
                placeholderText: window.t("dialog.logbook.note", "Add a short personal note for this event…")
                wrapMode: TextEdit.Wrap
                onTextChanged: {
                    if (length > 500)
                        remove(500, length)
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: logbookNoteEditor.length + " / 500"
                    color: muted; font.pixelSize: 9
                }
                Item { Layout.fillWidth: true }
                CockpitButton {
                    text: window.t("dialog.logbook.delete", "DELETE NOTE")
                    visible: !!cockpit.selectedLogbookEntry.note
                    accentColor: error
                    onClicked: {
                        cockpit.deleteLogbookNote(cockpit.selectedLogbookEntry.id || "")
                        logbookNoteEditor.text = ""
                    }
                }
                CockpitButton {
                    text: window.t("dialog.logbook.save", "SAVE NOTE")
                    selected: true
                    onClicked: cockpit.setLogbookNote(
                                   cockpit.selectedLogbookEntry.id || "",
                                   logbookNoteEditor.text)
                }
            }
        }
    }

    Dialog {
        id: shortcutHelpDialog
        objectName: "qa-dialog-shortcuts"
        title: window.t("dialog.controls.title", "Commander Controls")
        modal: true
        anchors.centerIn: parent
        width: Math.min(640, window.width - 80)
        standardButtons: Dialog.Close
        contentItem: ColumnLayout {
            spacing: 8
            Label {
                text: window.t("dialog.controls.heading", "FAST COCKPIT CONTROL")
                color: cyan
                font.bold: true
                font.pixelSize: 12
            }
            Repeater {
                model: [
                    ["Ctrl+K", "Search materials, blueprints and ships"],
                    ["Alt+1 … Alt+7", "Open a primary workspace directly"],
                    ["Ctrl+R", "Reload Journal and fleet data now"],
                    ["Ctrl+Shift+J", "Pause or resume automatic Journal updates"],
                    ["F1", "Open this command overview"]
                ]
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 48
                    radius: 9
                    color: panelRaised
                    border.width: 1
                    border.color: borderTone
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 10
                        Label {
                            text: modelData[0]
                            color: orange
                            font.family: "Consolas"
                            font.bold: true
                            Layout.preferredWidth: 125
                        }
                        Label { text: modelData[1]; color: textPrimary; Layout.fillWidth: true }
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                text: cockpit.journalAuto
                      ? window.t("common.journal_live", "● JOURNAL LIVE")
                      : window.t("common.journal_paused", "Ⅱ JOURNAL PAUSED")
                      + "   ·   " + (window.enhancedVisuals ? "ENHANCED GPU VISUALS" : "FLAT VISUALS")
                color: cockpit.journalAuto ? green : orange
                font.pixelSize: 10
            }
        }
    }

    Dialog {
        id: globalSearchDialog
        objectName: "qa-dialog-global-search"
        title: window.t("dialog.search.title", "Global Search")
        modal: true
        anchors.centerIn: parent
        width: Math.min(820, window.width - 80)
        height: Math.min(650, window.height - 80)
        standardButtons: Dialog.Close
        onOpened: {
            globalSearchField.forceActiveFocus()
            globalSearchField.selectAll()
        }
        contentItem: ColumnLayout {
            spacing: 10
            TextField {
                id: globalSearchField
                Layout.fillWidth: true
                placeholderText: window.t("dialog.search.placeholder", "Material, blueprint, module, engineer or system…")
                onTextChanged: window.globalResults = cockpit.globalSearch(text)
                Keys.onDownPressed: {
                    if (globalResultList.count > 0) {
                        globalResultList.currentIndex = 0
                        globalResultList.forceActiveFocus()
                    }
                }
                Keys.onReturnPressed: {
                    if (window.globalResults.length > 0)
                        window.activateGlobalResult(window.globalResults[0])
                }
            }
            Label {
                text: window.tf("dialog.search.results", "%1 RESULTS", [window.globalResults.length])
                color: cyan; font.pixelSize: 10; font.bold: true
            }
            ListView {
                id: globalResultList
                Layout.fillWidth: true; Layout.fillHeight: true
                model: window.globalResults
                spacing: 7; clip: true
                keyNavigationEnabled: true
                highlightFollowsCurrentItem: true
                Keys.onReturnPressed: {
                    if (currentIndex >= 0)
                        window.activateGlobalResult(window.globalResults[currentIndex])
                }
                Keys.onEnterPressed: {
                    if (currentIndex >= 0)
                        window.activateGlobalResult(window.globalResults[currentIndex])
                }
                Keys.onSpacePressed: {
                    if (currentIndex >= 0)
                        window.activateGlobalResult(window.globalResults[currentIndex])
                }
                ScrollBar.vertical: CockpitScrollBar {}
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    width: globalResultList.width; height: 68; radius: 10
                    color: resultMouse.containsMouse || activeFocus || ListView.isCurrentItem
                           ? active : panelRaised
                    border.width: activeFocus || ListView.isCurrentItem ? 2 : 1
                    border.color: activeFocus || ListView.isCurrentItem ? cyan : borderTone
                    activeFocusOnTab: true
                    Accessible.name: modelData.kind + ": " + modelData.title
                    Accessible.role: Accessible.Button
                    Keys.onReturnPressed: window.activateGlobalResult(modelData)
                    Keys.onEnterPressed: window.activateGlobalResult(modelData)
                    Keys.onSpacePressed: window.activateGlobalResult(modelData)
                    MouseArea {
                        id: resultMouse; anchors.fill: parent
                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: window.activateGlobalResult(modelData)
                    }
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 11
                        Label { text: modelData.kind; color: cyan; font.pixelSize: 9; font.bold: true; Layout.preferredWidth: 75 }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 2
                            Label { text: modelData.title; color: textPrimary; font.pixelSize: 13; font.bold: true }
                            Label { text: modelData.detail; color: muted; font.pixelSize: 10; elide: Text.ElideRight; Layout.fillWidth: true }
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id: aboutDialog
        objectName: "qa-dialog-about"
        title: window.t("dialog.about.title", "About ED Engineering Companion")
        modal: true
        anchors.centerIn: parent
        width: Math.min(620, window.width - 80)
        standardButtons: Dialog.Close
        contentItem: ColumnLayout {
            spacing: 14
            Label {
                text: "ED Engineering Companion"
                color: cyan
                font.pixelSize: 26
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                text: window.t("dialog.about.subtitle", "Engineering Companion tool for Elite Dangerous")
                color: textPrimary
                font.pixelSize: 14
                wrapMode: Text.WordWrap
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
            RowLayout {
                Layout.fillWidth: true
                    Label { text: window.t("dialog.about.developer", "DEVELOPER"); color: muted; font.pixelSize: 10; font.bold: true }
                Item { Layout.fillWidth: true }
                Label { text: "CMDR Forcer"; color: textPrimary; font.pixelSize: 13; font.bold: true }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                CockpitButton {
                    objectName: "qa-about-github-link"
                    text: window.t("dialog.about.github", "OPEN GITHUB")
                    Layout.fillWidth: true
                    onClicked: Qt.openUrlExternally("https://github.com/CMDRForcer/ED-Engineering-Companion")
                }
                CockpitButton {
                    objectName: "qa-about-kofi-link"
                    text: window.t("dialog.about.support", "SUPPORT ON KO-FI")
                    Layout.fillWidth: true
                    onClicked: Qt.openUrlExternally("https://ko-fi.com/cmdrforcer")
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: borderTone }
            Label {
                text: window.t("dialog.about.thanks", "THANK YOU")
                color: orange
                font.pixelSize: 11
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                text: window.t("about.thanks_text", "Special thanks to everyone who helped shape EDEC — contributors, testers, bug reporters, translators, and the Elite Dangerous community. Thanks also to EDCD/EDDN, INARA, and Spansh for their documentation, services, and community resources.")
                color: textSecondary
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }
            Label {
                Layout.fillWidth: true
                text: window.t("about.license", "Licensed under the GNU General Public License v3.0")
                color: green
                font.pixelSize: 11
                font.bold: true
                wrapMode: Text.WordWrap
            }
            Label {
                Layout.fillWidth: true
                text: window.t("about.disclaimer", "ED Engineering Companion (EDEC) is a third-party tool and is not affiliated with Frontier Developments. Elite Dangerous is a trademark of Frontier Developments plc.")
                color: muted
                font.pixelSize: 10
                wrapMode: Text.WordWrap
            }
        }
    }

    Dialog {
        id: onboardingDialog
        objectName: "qa-dialog-onboarding"
        title: window.t("dialog.onboarding.title", "Welcome, Commander")
        modal: true
        closePolicy: Popup.NoAutoClose
        anchors.centerIn: parent
        width: 620
        standardButtons: Dialog.Ok
        onAccepted: cockpit.completeOnboarding()
        contentItem: ColumnLayout {
            spacing: 14
            Label {
                text: window.t("dialog.onboarding.heading", "YOUR ENGINEERING COCKPIT")
                color: cyan; font.pixelSize: 20; font.bold: true
            }
            Label {
                Layout.fillWidth: true
                text: window.t("onboarding.steps", "1 · Pin a module plan in Engineering.\n")
                      + "2 · Operations calculates materials, safe trades and routes.\n"
                      + "3 · Elite Journal events update inventory, trades and crafts automatically."
                color: textPrimary; font.pixelSize: 13
                wrapMode: Text.WordWrap
            }
            Label {
                Layout.fillWidth: true
                text: window.t("onboarding.network", "No network connection is required for planning. INARA and EDDN stay disabled until you explicitly grant access in Connections.")
                color: muted; font.pixelSize: 11; wrapMode: Text.WordWrap
            }
            Label {
                text: window.t("onboarding.search", "Press Ctrl+K anywhere for global search.")
                color: green; font.pixelSize: 12; font.bold: true
            }
        }
    }

    Rectangle {
        id: materialDetailsOverlay
        property var safeTrader: cockpit.selectedMaterial.trader || ({})
        anchors.fill: parent
        z: 100
        visible: cockpit.selectedMaterial.key !== undefined
                 && cockpit.selectedMaterial.key !== ""
        focus: visible
        Keys.onEscapePressed: cockpit.clearSelectedMaterial()
        onVisibleChanged: {
            if (visible)
                materialCloseButton.forceActiveFocus()
        }
        color: overlay
        MouseArea { anchors.fill: parent; onClicked: cockpit.clearSelectedMaterial() }

        ShadowCard {
            width: Math.min(1420, parent.width - 50)
            height: Math.min(860, parent.height - 40)
            anchors.centerIn: parent
            accent: cyan
            MouseArea { anchors.fill: parent }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 28
                spacing: 16
                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        Layout.fillWidth: true
                        Label {
                            text: cockpit.selectedMaterial.name || ""
                            color: textPrimary; font.pixelSize: 28; font.bold: true
                        }
                        Label {
                            text: (cockpit.selectedMaterial.category || "").toUpperCase()
                                  + " · G" + (cockpit.selectedMaterial.grade || 0)
                                  + " · " + (cockpit.selectedMaterial.rarity || "")
                                  + (cockpit.selectedMaterial.rawTraderCategory > 0
                                     ? " · RAW TRADER CAT " + cockpit.selectedMaterial.rawTraderCategory
                                     : "")
                            color: cyan; font.pixelSize: 14; font.bold: true
                        }
                        Label {
                            visible: (cockpit.selectedMaterial.rawAvailability || []).length > 0
                            text: window.tf("status.available", "AVAILABLE: %1", [
                                (cockpit.selectedMaterial.rawAvailability || []).map(function(value) {
                                      return value === "surface" ? "SURFACE" : "ASTEROIDS"
                                }).join(" + ")
                            ])
                            color: muted; font.pixelSize: 11; font.bold: true
                        }
                    }
                    CockpitButton {
                        id: materialCloseButton
                        text: window.t("common.close", "CLOSE")
                        implicitWidth: 92
                        implicitHeight: 44
                        Accessible.name: window.t("materials.close_details", "Close material details")
                        onClicked: cockpit.clearSelectedMaterial()
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Repeater {
                        model: [
                            {"title": window.t("common.stock", "STOCK"), "value": (cockpit.selectedMaterial.have || 0) + " / " + (cockpit.selectedMaterial.capacity || 0)},
                            {"title": window.t("materials.build_need", "BUILD NEED"), "value": cockpit.selectedMaterial.need > 0 ? cockpit.selectedMaterial.need : window.t("status.value.not_needed", "NOT NEEDED")},
                            {"title": window.t("materials.protected", "PROTECTED"), "value": cockpit.selectedMaterial.protected || 0},
                            {"title": window.t("materials.safe_surplus", "SAFE SURPLUS"), "value": cockpit.selectedMaterial.surplus || 0}
                        ]
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true; height: 92; radius: 14; color: panelRaised
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 16
                                Label { text: modelData.title; color: muted; font.pixelSize: 12; font.bold: true }
                                Label { text: modelData.value; color: textPrimary; font.pixelSize: 22; font.bold: true }
                            }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 18
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        radius: 12; color: backgroundSecondary
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 18
                    Label { text: window.t("materials.where_to_get", "WHERE TO GET IT"); color: orange; font.pixelSize: 16; font.bold: true }
                            ListView {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                clip: true
                                model: cockpit.selectedMaterial.sourceCards || []
                                spacing: 11
                                delegate: Rectangle {
                                    required property var modelData
                                    width: ListView.view.width; height: 138
                                    radius: 12; color: panelRaised
                                    ColumnLayout {
                                        anchors.fill: parent; anchors.margins: 14
                                        spacing: 7
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Label {
                                                Layout.fillWidth: true
                                                text: modelData.label
                                                color: orange; font.pixelSize: 13; font.bold: true
                                            }
                                            Label {
                                                text: modelData.verified
                                                      ? "VERIFIED SOURCE"
                                                      : modelData.confidence === "heuristic"
                                                        ? "HEURISTIC"
                                                        : "DERIVED"
                                                color: modelData.verified ? green : muted
                                                font.pixelSize: 9; font.bold: true
                                            }
                                            CockpitButton {
                                                visible: !!modelData.system
                                                text: window.t("common.copy_system", "COPY SYSTEM")
                                                accentColor: orange
                                                implicitWidth: 126
                                                implicitHeight: 42
                                        Accessible.name: window.t("materials.copy_source_system", "Copy material source system")
                                                onClicked: cockpit.copySystem(modelData.system || "")
                                            }
                                        }
                                        Label {
                                            text: modelData.detail
                                            color: textSecondary; font.pixelSize: 12
                                            wrapMode: Text.WordWrap; Layout.fillWidth: true
                                            maximumLineCount: 5; elide: Text.ElideRight
                                        }
                                    }
                                }
                                Label {
                                    anchors.centerIn: parent
                                    visible: (cockpit.selectedMaterial.sourceCards || []).length === 0
                                    text: window.t("common.no_source", "No source guidance available.")
                                    color: muted
                                }
                            }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        radius: 12; color: backgroundSecondary
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 18
                    Label { text: window.t("materials.best_safe_trades", "BEST SAFE TRADES"); color: cyan; font.pixelSize: 16; font.bold: true }
                            Rectangle {
                                visible: !!materialDetailsOverlay.safeTrader.system
                                Layout.fillWidth: true
                                height: materialDetailsOverlay.safeTrader.traderWarning ? 105 : 86
                                radius: 12; color: active
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 13
                                    Label {
                                        text: (cockpit.selectedMaterial.category || "").toUpperCase()
                                              + " TRADER · " + (materialDetailsOverlay.safeTrader.station || "")
                                        color: cyan; font.pixelSize: 13; font.bold: true
                                    }
                                    Label {
                                        text: (materialDetailsOverlay.safeTrader.system || "")
                                              + " · " + (materialDetailsOverlay.safeTrader.distance_ly || 0).toFixed(1)
                                              + " ly · " + (materialDetailsOverlay.safeTrader.pad || "") + " pad"
                                        color: textSecondary; font.pixelSize: 12
                                    }
                                    Label {
                                        visible: !!materialDetailsOverlay.safeTrader.traderWarning
                                        text: materialDetailsOverlay.safeTrader.traderWarning || ""
                                        color: orange; font.pixelSize: 11; font.bold: true
                                    }
                                }
                            }
                            ListView {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                clip: true
                                model: cockpit.selectedMaterial.tradeOptions || []
                                spacing: 11
                                delegate: Rectangle {
                                    required property var modelData
                                    width: ListView.view.width; height: 130; radius: 12; color: panelRaised
                                    ColumnLayout {
                                        anchors.fill: parent; anchors.margins: 14
                                        spacing: 7
                                        Label {
                                            text: window.tf("materials.give", "GIVE %1  %2", [modelData.spend, modelData.sourceName])
                                                  + "  →  RECEIVE " + modelData.receive
                                            color: textPrimary; font.pixelSize: 13; font.bold: true
                                            Layout.fillWidth: true; elide: Text.ElideRight
                                        }
                                        Label {
                                            text: window.tf("status.stock", "Stock %1", [modelData.stock])
                                                  + " · protected " + modelData.protected
                                                  + " · safe surplus " + modelData.surplus
                                            color: green; font.pixelSize: 12; font.bold: true
                                        }
                                        Label {
                                            text: modelData.reason
                                            color: muted; font.pixelSize: 11
                                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                                        }
                                    }
                                }
                                Label {
                                    anchors.centerIn: parent
                                    visible: (cockpit.selectedMaterial.tradeOptions || []).length === 0
                                    text: cockpit.selectedMaterial.tradeable
                                          ? "NO SAFE SURPLUS AVAILABLE"
                                          : "NOT AVAILABLE AT STANDARD MATERIAL TRADERS"
                                    color: muted; width: parent.width - 20
                                    horizontalAlignment: Text.AlignHCenter
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        radius: 12; color: backgroundSecondary
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 18
                    Label { text: window.t("materials.used_in", "USED IN"); color: green; font.pixelSize: 16; font.bold: true }
                            ListView {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                clip: true
                                model: cockpit.selectedMaterial.usedIn || []
                                spacing: 11
                                delegate: Rectangle {
                                    required property var modelData
                                    width: ListView.view.width; height: 88; radius: 11; color: panelRaised
                                    ColumnLayout {
                                        anchors.fill: parent; anchors.margins: 13
                                        Label {
                                            text: modelData.module + " · " + modelData.blueprint
                                            color: textPrimary; font.pixelSize: 13; font.bold: true
                                            elide: Text.ElideRight; Layout.fillWidth: true
                                        }
                                        Label {
                                            text: window.tf("status.grade_short", "G%1", [modelData.grade]) + " · " + modelData.amount
                                                  + " units" + (modelData.engineers ? " · " + modelData.engineers : "")
                                            color: muted; font.pixelSize: 11
                                            elide: Text.ElideRight; Layout.fillWidth: true
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
}
