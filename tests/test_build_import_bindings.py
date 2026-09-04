import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ed_companion.phase14.controller import CockpitController


class _Signal:
    def emit(self, *_args):
        pass


class BuildImportBindingTests(unittest.TestCase):
    def test_apply_binds_plan_to_desired_module_not_current_slot_module(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ship_metadata.json").write_text(json.dumps({
                "Test ship": {"id": 42, "type": "Mandalay"},
            }), encoding="utf-8")
            controller = CockpitController.__new__(CockpitController)
            controller._data_dir = root
            controller._state = {
                "ships": ["Test ship"],
                "engineeringShipSlots": [{
                    "slot": "PowerPlant",
                    "moduleId": "int_powerplant_size5_class1",
                }],
            }
            controller._build_import_target = "Test ship"
            controller._build_import_preview = {
                "compatible": True,
                "shipType": "Mandalay",
                "rows": [{
                    "status": "ready", "slotBound": True,
                    "slot": "PowerPlant", "planMode": "grade_only",
                    "blueprintGroup": "Power Plant\u241fOvercharged",
                    "grade": 1, "currentGrade": 0,
                    "desiredModule": "int_powerplant_size3_class5",
                    "moduleChange": True,
                }],
            }
            controller._blueprint_groups = {
                "Power Plant\u241fOvercharged": [{
                    "Type": "Power Plant", "Name": "Overcharged",
                    "Grade": 1, "Ingredients": [],
                }],
            }
            controller._experimentals = []
            controller.refresh = lambda: None
            controller.engineeringChanged = _Signal()

            controller.applyBuildImport()

            tasks = json.loads(
                (root / "ship_blueprints.json").read_text(encoding="utf-8")
            )["Test ship"]
            planner = tasks[0][0]["_Planner"]
            desired = json.loads(
                (root / "desired_outfitting.json").read_text(encoding="utf-8")
            )
            self.assertEqual(planner["ship_id"], "42")
            self.assertEqual(planner["slot"], "PowerPlant")
            self.assertEqual(
                planner["module_id"], "int_powerplant_size3_class5"
            )
            self.assertFalse(planner["binding_required"])
            self.assertEqual(
                desired["42"]["PowerPlant"],
                "int_powerplant_size3_class5",
            )


if __name__ == "__main__":
    unittest.main()
