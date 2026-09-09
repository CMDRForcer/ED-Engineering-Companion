import os
from pathlib import Path
import subprocess
import sys
import unittest


class StableSlotListTests(unittest.TestCase):
    def test_real_qml_preserves_anchor_after_module_updates(self):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--qml"],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__" and "--qml" in sys.argv:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    app = QGuiApplication([])
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    base = Path(__file__).resolve().parents[1] / "qml/components"
    component.setData(b'''
import QtQuick
StableSlotList {
    width: 300; height: 200
    spacing: 4
    section.property: "group"
    section.delegate: Rectangle { width: 300; height: 24 }
    viewportKey: "Mandalay"
    delegate: Rectangle {
        required property var modelData
        width: 300; height: modelData.h
    }
    function fill(shrink) {
        let rows = []
        for (let i=0;i<30;i++) rows.push({slot: "Slot"+i,
            group: i<8 ? "CORE" : "OPTIONAL", h: shrink && i<10 ? 40 : 80})
        sourceRows = rows
    }
    function scroll() { positionViewAtIndex(12, ListView.Beginning); contentY += 13 }
    function anchorOffset() {
        forceLayout()
        return contentY - itemAtIndex(12).y
    }
}
''', QUrl.fromLocalFile(str(base / "test.qml")))
    obj = component.create()
    assert obj is not None, component.errors()
    obj.fill(False)
    for _ in range(5): app.processEvents()
    obj.scroll()
    for _ in range(5): app.processEvents()
    assert abs(obj.anchorOffset()-13) < 1
    for shrink in (True, True, False, True):
        obj.fill(shrink)
        for _ in range(5): app.processEvents()
        assert abs(obj.anchorOffset()-13) < 1, obj.anchorOffset()
    obj.setProperty("viewportKey", "Other ship")
    for _ in range(5): app.processEvents()
    assert abs(obj.property("contentY")-obj.property("originY")) < 1
    del obj
    del component
    del engine
