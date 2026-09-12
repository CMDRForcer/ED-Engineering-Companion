import QtQuick

// A small DNA double-helix glyph drawn on a Canvas so it can be recolored
// exactly like the Segoe Fluent Icons glyphs used for every other nav
// entry (no icon font actually ships a DNA symbol, and baking a fixed
// color into an SVG asset would not follow the selected/unselected tint
// or theme changes the way this does).
Canvas {
    id: dnaIcon
    property color color: "#3bdcff"
    property real strokeWidth: 2.4
    width: 20
    height: 20
    antialiasing: true
    onColorChanged: requestPaint()
    onPaint: {
        var ctx = getContext("2d")
        ctx.reset()
        ctx.strokeStyle = dnaIcon.color
        ctx.lineWidth = dnaIcon.strokeWidth
        ctx.lineCap = "round"
        var w = width
        var h = height
        var amplitude = w * 0.32
        var midX = w / 2

        function strandX(t, phase) {
            return midX + amplitude * Math.sin(t * Math.PI * 2 + phase)
        }

        // Two intertwined strands.
        [0, Math.PI].forEach(function(phase) {
            ctx.beginPath()
            for (var i = 0; i <= 24; i++) {
                var t = i / 24
                var x = strandX(t, phase)
                var y = t * h
                if (i === 0) ctx.moveTo(x, y)
                else ctx.lineTo(x, y)
            }
            ctx.stroke()
        })

        // Rungs connecting the strands at regular intervals.
        for (var r = 1; r < 5; r++) {
            var t = r / 5
            var y = t * h
            var xA = strandX(t, 0)
            var xB = strandX(t, Math.PI)
            ctx.beginPath()
            ctx.moveTo(xA, y)
            ctx.lineTo(xB, y)
            ctx.stroke()
        }
    }
}
