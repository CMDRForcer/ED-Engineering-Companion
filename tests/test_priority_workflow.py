import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ed_companion.journal import material_event_changes
from ed_companion.phase14.state import (
    apply_engineer_craft,
    attach_operation_experimental_effects,
    attach_operation_plan_context,
    blueprint_rows,
    build_engineering_plan,
    scope_operation_action_materials,
    select_operation_action,
)


class PriorityWorkflowTests(unittest.TestCase):
    def test_collect_action_inherits_owning_plan_engineer_portrait(self):
        action = {"kind": "COLLECT", "materialKey": "vanadium"}
        state = {"blueprints": [{
            "module": "Detailed Surface Scanner",
            "blueprint": "Expanded Probe Scanning Radius", "targetGrade": 5,
            "targetStatus": "in_progress", "nextGrade": 5,
            "materialProgress": [{"key": "vanadium", "missing": 2}],
        }]}
        engineers = [{
            "name": "Lei Cheung", "statusGroup": "unlocked", "rank": 5,
            "system": "Laksak", "station": "Trader's Rest",
            "portraitUrl": "file:///lei.png", "distance": 10,
        }]
        records = [{
            "Type": "Detailed Surface Scanner",
            "Name": "Expanded Probe Scanning Radius", "Grade": 5,
            "Engineers": ["Lei Cheung"],
        }]
        result = attach_operation_plan_context(action, state, engineers, records)
        self.assertEqual(result["moduleName"], "Detailed Surface Scanner")
        self.assertEqual(result["engineerName"], "Lei Cheung")
        self.assertEqual(result["portraitUrl"], "file:///lei.png")
        self.assertEqual(result["system"], "Laksak")

    def test_experimental_effect_details_use_global_catalog_identity(self):
        records = [{
            "Type": "Beam Laser", "Name": "Stripped Down",
            "ExperimentalId": "beam_laser::stripped_down",
            "Effects": [{"Property": "Mass", "Effect": "-10%", "IsGood": True}],
        }, {
            "Type": "Power Plant", "Name": "Stripped Down",
            "ExperimentalId": "power_plant::stripped_down",
            "Effects": [{"Property": "Mass", "Effect": "-10%", "IsGood": True},
                        {"Property": "Integrity", "Effect": "-25%", "IsGood": False}],
        }]
        action = attach_operation_experimental_effects({
            "moduleName": "Power Plant", "experimentalName": "Stripped Down",
            "experimentalId": "power_plant::stripped_down",
        }, records)
        self.assertEqual(action["experimentalEffects"], [
            {"property": "Mass", "effect": "-10%", "isGood": True,
             "summary": "Mass -10%"},
            {"property": "Integrity", "effect": "-25%", "isGood": False,
             "summary": "Integrity -25%"},
        ])

    def test_experimental_effect_details_fallback_is_module_scoped(self):
        records = [{
            "Type": module_type, "Name": "Reinforced",
            "ExperimentalId": experimental_id,
            "Effects": [{"Property": "Effect", "Effect": effect, "IsGood": True}],
        } for module_type, experimental_id, effect in (
            ("Shield Generator", "shield_generator::reinforced", "+5%"),
            ("Shield Booster", "shield_booster::reinforced", "+8%"),
        )]
        action = attach_operation_experimental_effects({
            "moduleName": "Shield Generator", "experimentalName": "Reinforced",
        }, records)
        self.assertEqual(action["experimentalEffects"][0]["effect"], "+5%")

    def test_real_journal_cycle_keeps_bars_truthful_then_resumes_full_plan(self):
        grades = [{
            "Type": "Power Plant", "Name": "Overcharged",
            "BlueprintName": "PowerPlant_Boosted", "BlueprintID": 1000 + grade,
            "Grade": grade, "Engineers": ["Hera Tani"],
            "Ingredients": [{"Name": f"Mat {grade}", "Size": 1}],
        } for grade in range(1, 6)]
        priority = build_engineering_plan(
            grades, 0, 5, plan_id="power-plant", instance="PowerPlant",
            experimental_id="power_plant::stripped_down",
            experimental_name="Stripped Down", plan_mode="combined",
            ship_id=7, slot="PowerPlant",
            module_id="int_powerplant_size5_class5",
        )
        priority[0]["_Planner"]["priority"] = True
        experimental = [{
            "Type": "Power Plant", "Name": "Stripped Down",
            "ExperimentalId": "power_plant::stripped_down",
            "Kind": "ExperimentalEffect", "Grade": None,
            "Engineers": ["Hera Tani"],
            "Ingredients": [{"Name": "Exp Mat", "Size": 2}],
            "_ParentPlanId": "power-plant",
        }]
        other = build_engineering_plan([{
            "Type": "Shield Generator", "Name": "Reinforced",
            "BlueprintName": "ShieldGenerator_Reinforced", "BlueprintID": 2001,
            "Grade": 1, "Engineers": ["Lei Cheung"],
            "Ingredients": [{"Name": "Other Mat", "Size": 1}],
        }], 0, 1, plan_id="shield", instance="Slot03_Size4",
            ship_id=7, slot="Slot03_Size4",
            module_id="int_shieldgenerator_size4_class5")
        inventory = {f"mat{grade}": grade for grade in range(1, 6)}
        inventory.update({"expmat": 2, "othermat": 0})
        metadata = {
            key: {"Name": key, "Category": "Raw"} for key in inventory
        }
        route = [{
            "name": "Hera Tani", "craftable": True,
            "jobNames": ["Power Plant · Overcharged · G5"],
        }, {
            "name": "Lei Cheung", "craftable": True,
            "jobNames": ["Shield Generator · Reinforced · G1"],
        }]

        with TemporaryDirectory() as directory:
            path = Path(directory) / "ship_blueprints.json"
            path.write_text(json.dumps({"Mandalay": [priority, experimental, other]}),
                            encoding="utf-8")

            def project():
                tasks = json.loads(path.read_text(encoding="utf-8"))["Mandalay"]
                rows = blueprint_rows(tasks, inventory, metadata)
                global_missing = [
                    dict(material)
                    for row in rows for material in row["materialProgress"]
                    if material["missing"] > 0
                ]
                state = {"blueprints": rows, "materials": global_missing, "trades": []}
                action = scope_operation_action_materials(
                    state, select_operation_action(state, route)
                )
                return rows, action

            for grade in range(1, 6):
                rows, action = project()
                power_plant = next(row for row in rows if row["planId"] == "power-plant")
                self.assertEqual(power_plant["nextGrade"], grade)
                self.assertEqual(action["kind"], "GRADE_CRAFT")
                self.assertEqual(action["actionGrade"], grade)
                self.assertEqual(action["materialStatus"], "READY")
                self.assertEqual(action["materialCompletion"], 1.0)
                self.assertEqual(action["materialScope"], "PRIORITY PLAN")
                self.assertTrue(action["priority"])
                self.assertEqual(action["moduleName"], "Power Plant")
                self.assertEqual(action["experimentalName"], "Stripped Down")
                event = {
                    "timestamp": f"2026-09-05T10:00:0{grade}Z",
                    "event": "EngineerCraft", "ShipID": 7,
                    "Slot": "PowerPlant", "Module": "int_powerplant_size5_class5",
                    "Engineer": "Hera Tani", "BlueprintName": "PowerPlant_Boosted",
                    "BlueprintID": 1000 + grade, "Level": grade, "Quality": 1.0,
                    "Ingredients": [{"Name": f"mat{grade}", "Count": 1}],
                }
                self.assertEqual(
                    apply_engineer_craft(path, "Mandalay", event, ship_id=7)["status"],
                    "applied",
                )
                for key, _category, delta in material_event_changes(event):
                    inventory[key] += delta

            rows, action = project()
            power_plant = next(row for row in rows if row["planId"] == "power-plant")
            self.assertEqual(power_plant["targetStatus"], "experimental_pending")
            self.assertEqual(power_plant["gradeStatus"], "completed")
            self.assertEqual(power_plant["experimentalStatus"], "pending")
            self.assertEqual(action["kind"], "EXPERIMENTAL_CRAFT")
            self.assertEqual(action["materialCompletion"], 1.0)
            effect_event = {
                "timestamp": "2026-09-05T10:01:00Z", "event": "EngineerCraft",
                "ShipID": 7, "Slot": "PowerPlant",
                "Module": "int_powerplant_size5_class5",
                "BlueprintName": "PowerPlant_Boosted", "BlueprintID": 1005,
                "Level": 5, "Quality": 1.0,
                "ApplyExperimentalEffect": "special_powerplant_lightweight",
                "ExperimentalEffect_Localised": "Stripped Down",
                "Ingredients": [{"Name": "expmat", "Count": 2}],
            }
            result = apply_engineer_craft(path, "Mandalay", effect_event, ship_id=7)
            self.assertTrue(result["completed"])
            rows, action = project()
            power_plant = next(row for row in rows if row["planId"] == "power-plant")
            self.assertEqual(power_plant["targetStatus"], "completed")
            self.assertFalse(power_plant["priority"])
            self.assertEqual(action["kind"], "COLLECT")
            self.assertNotIn("materialScope", action)
            self.assertIn("othermat", action["title"])

    def test_priority_trade_is_limited_to_its_shortage(self):
        state = {"blueprints": [{"priority": True, "targetStatus": "not_started",
                 "materialProgress": [{"key": "iron", "missing": 2}]}],
                 "materials": [{"key": "iron", "missing": 42}],
                 "trades": [{"targetKey": "iron", "giveAmount": 7,
                             "system": "Sol", "station": "Trader",
                             "receiveAmount": 42, "giveName": "Source",
                             "receiveName": "Iron"}]}
        action = select_operation_action(state, [])
        self.assertEqual(action["kind"], "TRADE")
        self.assertEqual(action["title"], "WANTED · 6 Iron · GIVE · 1 Source")

    def test_priority_collect_craft_experimental_then_resume(self):
        pp = {"module": "Power Plant", "blueprint": "Overcharged",
              "targetGrade": 5, "targetStatus": "not_started", "priority": True,
              "canCraftNext": True, "experimental": "Stripped Down",
              "materialProgress": [{"key": "iron", "name": "Iron", "missing": 2}],
              "experimentalMaterialProgress": []}
        other = {"module": "Shield Generator", "blueprint": "Reinforced",
                 "targetStatus": "not_started", "canCraftNext": True,
                 "materialProgress": [{"key": "iron", "name": "Iron", "missing": 40}]}
        state = {"blueprints": [other, pp], "trades": [],
                 "materials": [{"key": "iron", "name": "Iron", "missing": 42}]}
        route = [{"name": "Other", "craftable": True,
                  "jobNames": ["Shield Generator · Reinforced · G5"]},
                 {"name": "PP Engineer", "craftable": True,
                  "jobNames": ["Power Plant · Overcharged · G5"]}]
        action = select_operation_action(state, route)
        self.assertEqual(action["title"], "Collect 2 × Iron")
        pp["materialProgress"] = []
        action = select_operation_action(state, route)
        self.assertEqual(action["kind"], "GRADE_CRAFT")
        self.assertEqual(action["moduleName"], "Power Plant")
        pp.update(targetStatus="experimental_pending", experimentalReady=True,
                  experimentalMaterialProgress=[{"key": "iron", "missing": 0}])
        self.assertEqual(select_operation_action(state, route)["kind"], "EXPERIMENTAL_CRAFT")
        pp["targetStatus"] = "completed"
        self.assertEqual(select_operation_action(state, route)["kind"], "COLLECT")

    def test_cancel_priority_restores_whole_build_material_requirement(self):
        state = {"blueprints": [{"priority": False, "targetStatus": "not_started",
                  "materialProgress": [{"key": "iron", "missing": 3}]}],
                 "materials": [{"key": "iron", "name": "Iron", "missing": 3}]}
        self.assertEqual(select_operation_action(state, [])["title"], "Collect 3 × Iron")
