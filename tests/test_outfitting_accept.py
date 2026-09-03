import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ed_companion.phase14.controller import CockpitController


class OutfittingAcceptTests(unittest.TestCase):
    def test_slot_selection_mouse_area_does_not_cover_accept_button(self):
        source = (
            Path(__file__).resolve().parents[1] / "Main.qml"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("id: acceptCurrentButton\n                                    z: 2", source)
        self.assertIn(
            "anchors.right: acceptCurrentButton.visible\n"
            "                                                   ? acceptCurrentButton.left",
            source,
        )

    def test_accept_empty_slot_removes_only_that_outfitting_request(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "desired_outfitting.json"
            path.write_text(json.dumps({
                "39": {
                    "TinyHardpoint2": "hpt_shieldbooster_size0_class5",
                    "TinyHardpoint3": "hpt_shieldbooster_size0_class5",
                },
            }), encoding="utf-8")
            controller = CockpitController.__new__(CockpitController)
            controller._data_dir = root
            controller._state = {
                "selectedShipId": "39",
                "engineeringShipSlots": [{
                    "slot": "TinyHardpoint2",
                    "desiredSourceSlot": "TinyHardpoint2",
                    "moduleChange": True,
                    "moduleId": "",
                }],
            }
            refreshed = []
            controller.refresh = lambda: refreshed.append(True)

            controller.acceptCurrentOutfittingSlot("TinyHardpoint2")

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("TinyHardpoint2", saved["39"])
            self.assertIn("TinyHardpoint3", saved["39"])
            self.assertEqual(refreshed, [True])

    def test_accept_named_slot_removes_its_legacy_source_key(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "desired_outfitting.json"
            path.write_text(json.dumps({
                "39": {"Slot06_Size5": "legacy-controller"},
            }), encoding="utf-8")
            controller = CockpitController.__new__(CockpitController)
            controller._data_dir = root
            controller._state = {
                "selectedShipId": "39",
                "engineeringShipSlots": [{
                    "slot": "LimpetController01",
                    "desiredSourceSlot": "Slot06_Size5",
                    "moduleChange": True,
                    "moduleId": "",
                }],
            }
            controller.refresh = lambda: None

            controller.acceptCurrentOutfittingSlot("LimpetController01")

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")), {}
            )


if __name__ == "__main__":
    unittest.main()
