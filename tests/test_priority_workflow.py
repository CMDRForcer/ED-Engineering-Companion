import unittest
from ed_companion.phase14.state import select_operation_action


class PriorityWorkflowTests(unittest.TestCase):
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
