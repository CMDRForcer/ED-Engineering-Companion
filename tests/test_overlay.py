from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtCore import QRect

from ed_companion.overlay import OverlaySettings, clamp_overlay_geometry


class OverlayTests(unittest.TestCase):
    def test_overlay_settings_and_geometry_survive_restart(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "overlay_settings.json"
            first = OverlaySettings(path)
            first.visible = True
            first.locked = True
            first.clickThrough = True
            first.opacity = 0.8
            first.scale = 1.25
            first.save_geometry("DISPLAY-2", QRect(120, 80, 460, 240))

            second = OverlaySettings(path)
            self.assertTrue(second.visible)
            self.assertTrue(second.locked)
            self.assertTrue(second.clickThrough)
            self.assertAlmostEqual(second.opacity, 0.8)
            self.assertAlmostEqual(second.scale, 1.25)
            self.assertEqual(second.geometry(), {
                "screen": "DISPLAY-2", "x": 120, "y": 80,
                "width": 460, "height": 240,
            })

    def test_missing_monitor_geometry_falls_back_to_visible_primary_area(self):
        rectangle, screen_id = clamp_overlay_geometry(
            {"screen": "DISCONNECTED", "x": 5000, "y": -4000,
             "width": 900, "height": 700},
            [{"id": "PRIMARY", "available": (100, 50, 1280, 720),
              "primary": True}],
        )

        x, y, width, height = rectangle
        self.assertEqual(screen_id, "PRIMARY")
        self.assertGreaterEqual(x, 100)
        self.assertGreaterEqual(y, 50)
        self.assertLessEqual(x + width, 1380)
        self.assertLessEqual(y + height, 770)

    def test_overlay_uses_main_process_controller_state_without_domain_logic(self):
        root = Path(__file__).resolve().parents[1]
        main_source = (root / "phase14_main.py").read_text(encoding="utf-8")
        qml_source = (root / "qml" / "Overlay.qml").read_text(encoding="utf-8")

        self.assertIn('setContextProperty("cockpit", controller)', main_source)
        self.assertIn('qml" / "Overlay.qml"', main_source)
        self.assertIn("cockpit.operationAction.title", qml_source)
        self.assertIn("cockpit.nextAction", qml_source)
        self.assertIn("cockpit.materialStatus", qml_source)
        self.assertNotIn("Journal", qml_source)
        self.assertNotIn("reloadJournalNow", qml_source)


if __name__ == "__main__":
    unittest.main()
