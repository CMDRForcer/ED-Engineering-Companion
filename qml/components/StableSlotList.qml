import QtQuick

ListView {
    id: view
    property var sourceRows: []
    property string viewportKey: ""
    property string previousViewportKey: ""
    function updateRows() {
        forceLayout()
        let anchor = ""
        let offset = 0
        const oldY = contentY
        for (let i = 0; i < count; ++i) {
            const item = itemAtIndex(i)
            if (item && item.y + item.height > contentY) {
                anchor = String(model[i].slot || "")
                offset = contentY - item.y
                break
            }
        }
        const sameShip = previousViewportKey === viewportKey
        model = sourceRows || []
        forceLayout()
        let restored = sameShip ? oldY : originY
        if (sameShip && anchor) {
            for (let i = 0; i < count; ++i) {
                if (String(model[i].slot || "") === anchor) {
                    positionViewAtIndex(i, ListView.Beginning)
                    forceLayout()
                    const item = itemAtIndex(i)
                    if (item)
                        restored = item.y + offset
                    break
                }
            }
        }
        contentY = Math.max(originY, Math.min(restored,
                            originY + Math.max(0, contentHeight - height)))
        previousViewportKey = viewportKey
    }
    onSourceRowsChanged: updateRows()
    onViewportKeyChanged: Qt.callLater(updateRows)
}
