from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import requests

from ed_companion.build_import import _read_input, preview_build
from ed_companion.integrations.eddn import (
    prepare_event,
    schema_parity_report,
    update_context,
    validate_prepared,
)
from ed_companion.integrations.inara import (
    INARA_BATCH_WINDOW_SECONDS,
    INARA_MAX_REQUESTS_PER_MINUTE,
    INARA_MIN_REQUEST_INTERVAL_SECONDS,
    MAX_EVENTS,
    prepare_journal_batch,
)
from ed_companion.phase14.state import (
    assign_plans_to_nearest_engineers,
    annotate_installed_target_conflicts,
    blueprint_rows,
    apply_engineer_craft,
    build_engineering_plan,
    build_experimental_plan,
    engineering_run_preflight,
    engineering_loadout_rows,
    latest_loadout_slots,
    migrate_wishlist_bindings,
    module_matches_type,
    partition_engineer_assignments,
    powerplay_journal_overview,
    required_materials,
    select_operation_action,
    ship_slot_layout,
)
from ed_companion.loadout_export import build_loadout_export
from ed_companion.navigation import find_nearest_catalog_trader
from ed_companion.navigation.trader_search import _spansh_json
from ed_companion.services import latest_delivery_proof


class ReleaseContractTests(unittest.TestCase):
    def test_workspace_design_system_covers_secondary_pages_and_dialogs(self):
        root = Path(__file__).resolve().parents[1]
        main_qml = (root / "Main.qml").read_text(encoding="utf-8")
        logbook_qml = (root / "qml/pages/LogbookPage.qml").read_text(encoding="utf-8")
        powerplay_qml = (root / "qml/pages/PowerplayPage.qml").read_text(encoding="utf-8")

        self.assertTrue((root / "qml/components/WorkspaceHeader.qml").is_file())
        self.assertTrue((root / "qml/components/StatusBadge.qml").is_file())
        self.assertGreaterEqual(main_qml.count("CockpitDialog {"), 6)
        self.assertIn("WorkspaceHeader {", logbook_qml)
        self.assertIn("WorkspaceHeader {", powerplay_qml)
        self.assertIn("id: inaraConfigScroll", main_qml)
        self.assertIn("anchors.fill: parent\n            visible: connectionsPage.connectionMode === 0", main_qml)
        self.assertIn("anchors.fill: parent\n            visible: connectionsPage.connectionMode === 1", main_qml)
        self.assertEqual(
            main_qml.count("anchors.right: parent.horizontalCenter"),
            2,
        )
        self.assertEqual(
            main_qml.count("anchors.left: parent.horizontalCenter"),
            2,
        )

    def test_engineer_route_excludes_locked_access_tasks(self):
        assignments = [
            {
                "name": "Unlocked Far", "craftable": True,
                "distance": 30.0, "coordinates": [30.0, 0.0, 0.0],
            },
            {
                "name": "Locked Near", "craftable": False,
                "distance": 1.0, "coordinates": [1.0, 0.0, 0.0],
            },
            {
                "name": "Unlocked Near", "craftable": True,
                "distance": 5.0, "coordinates": [5.0, 0.0, 0.0],
            },
        ]

        route, unlocks = partition_engineer_assignments(assignments)

        self.assertEqual(
            [row["name"] for row in route], ["Unlocked Near", "Unlocked Far"]
        )
        self.assertEqual([row["name"] for row in unlocks], ["Locked Near"])

    def test_engineering_run_preflight_reports_global_readiness(self):
        state = {"blueprints": [{
            "targetStatus": "in_progress", "boundSlot": "PowerPlant",
            "boundModule": "int_powerplant_size7_class5",
            "installedModule": "int_powerplant_size7_class5",
            "materialProgress": [{"key": "iron", "name": "Iron", "missing": 0}],
            "rollEstimateReliable": True,
        }]}
        route = [{"name": "Hera Tani", "craftable": True, "openJobs": 1}]

        result = engineering_run_preflight(state, route)

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["ready"])
        self.assertEqual(result["blockers"], [])

    def test_engineering_run_preflight_lists_independent_blockers(self):
        state = {"blueprints": [{
            "targetStatus": "not_started", "bindingRequired": True,
            "boundSlot": "TinyHardpoint1", "installedModule": "",
            "materialProgress": [{
                "key": "vanadium", "name": "Vanadium", "missing": 3,
            }],
            "rollEstimateReliable": False,
        }]}
        route = [{
            "name": "Ram Tah", "craftable": False, "openJobs": 1,
        }]

        result = engineering_run_preflight(state, route)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(
            {row["code"] for row in result["blockers"]},
            {"MATERIALS", "MODULE_BINDING", "ENGINEER_ACCESS"},
        )
        self.assertEqual(
            [row["code"] for row in result["warnings"]], ["ROLL_ESTIMATE"],
        )

    def test_installed_blueprint_conflict_is_visible_but_not_auto_removed(self):
        rows = [{
            "index": 2, "boundSlot": "MediumHardpoint2",
            "boundModule": "hpt_multicannon_gimbal_medium",
            "blueprint": "Efficient Weapon", "targetGrade": 5,
        }]
        annotate_installed_target_conflicts(rows, [{
            "slot": "MediumHardpoint2",
            "moduleId": "hpt_multicannon_gimbal_medium",
            "engineeringBlueprint": "Weapon_Overcharged",
            "engineeringGrade": 5,
        }])

        self.assertTrue(rows[0]["targetConflict"])
        self.assertEqual(rows[0]["installedBlueprint"], "Overcharged Weapon")
        self.assertEqual(rows[0]["blueprint"], "Efficient Weapon")

    def test_state_reconciles_wishlist_before_pending_craft_replay(self):
        source = Path("ed_companion/phase14/state.py").read_text(encoding="utf-8")
        build_state_source = source.split("def build_state(", 1)[1]
        migration = build_state_source.index(
            "migrate_wishlist_bindings(data_dir, fleet_state, profile_events)"
        )
        replay = build_state_source.index("reconcile_engineer_craft_batch(")
        self.assertLess(migration, replay)

    def test_inara_accepts_material_snapshot_before_loadgame(self):
        events = [
            {
                "timestamp": "2026-08-29T09:16:25Z",
                "event": "Fileheader",
                "Odyssey": True,
                "gameversion": "4.4.0.3",
            },
            {
                "timestamp": "2026-08-29T09:21:50Z",
                "event": "Commander",
                "FID": "F207773",
                "Name": "Forcer",
            },
            {
                "timestamp": "2026-08-29T09:21:50Z",
                "event": "Materials",
                "Raw": [{"Name": "iron", "Count": 117}],
                "Manufactured": [{"Name": "heatvanes", "Count": 20}],
                "Encoded": [{"Name": "dataminedwake", "Count": 5}],
            },
            {
                "timestamp": "2026-08-29T09:21:51Z",
                "event": "LoadGame",
                "FID": "F207773",
                "Commander": "Forcer",
                "Odyssey": True,
                "gameversion": "4.4.0.3",
            },
        ]

        identity, prepared, _ = prepare_journal_batch(
            events, expected_identity="F207773", max_events=None,
        )

        materials = [
            event for event in prepared
            if event.get("eventName") == "setCommanderInventoryMaterials"
        ]
        self.assertEqual(identity["frontier_id"], "F207773")
        self.assertEqual(len(materials), 1)
        self.assertEqual(
            materials[0]["eventData"],
            [
                {"itemName": "dataminedwake", "itemCount": 5},
                {"itemName": "heatvanes", "itemCount": 20},
                {"itemName": "iron", "itemCount": 117},
            ],
        )

    def test_spansh_transport_retries_then_accepts_valid_response(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": []}

        calls = []

        def post(*_args, **_kwargs):
            calls.append(True)
            if len(calls) < 3:
                raise requests.ConnectionError("temporary")
            return Response()

        with mock.patch(
            "ed_companion.navigation.trader_search.time.sleep"
        ) as sleep:
            result = _spansh_json(post, {"filters": {}}, 20)

        self.assertEqual(result, {"results": []})
        self.assertEqual(len(calls), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 3])

    def test_clearing_eddn_history_can_preserve_latest_delivery_proof(self):
        jobs = [
            {
                "status": "sent",
                "sent_at": "2026-08-28T21:34:34+00:00",
                "receipt": {"httpStatus": 200},
                "event": {
                    "schema": "journal/1",
                    "message": {
                        "event": "FSDJump",
                        "timestamp": "2026-08-28T21:33:10Z",
                    },
                },
            }
        ]

        proof = latest_delivery_proof(jobs)

        self.assertEqual(proof["sentAt"], "2026-08-28T21:34:34+00:00")
        self.assertEqual(proof["schema"], "journal/1")
        self.assertEqual(proof["eventName"], "FSDJump")
        self.assertEqual(proof["result"], "Gateway accepted HTTP 200")

    def test_operations_collects_all_build_materials_before_any_engineer_trip(self):
        state = {
            "blueprints": [
                {
                    "module": "Frame Shift Drive",
                    "blueprint": "Increased Range",
                    "targetGrade": 5,
                    "targetStatus": "not_started",
                    "priority": True,
                    "canCraftNext": True,
                    "materialProgress": [],
                },
                {
                    "module": "Hull Reinforcement Package",
                    "blueprint": "Heavy Duty Hull Reinforcement",
                    "targetGrade": 5,
                    "targetStatus": "not_started",
                    "priority": False,
                    "canCraftNext": False,
                    "materialProgress": [{
                        "key": "tungsten", "name": "Tungsten",
                        "missing": 4,
                    }],
                },
            ],
            "materials": [{
                "key": "tungsten", "name": "Tungsten", "missing": 4,
            }],
            "trades": [],
        }
        route = [{
            "name": "Felicity Farseer", "system": "Deciat",
            "station": "Farseer Inc", "craftable": True,
            "jobNames": ["Frame Shift Drive · Increased Range · G5"],
            "readyJobs": 1,
        }]

        action = select_operation_action(state, route)

        self.assertEqual(action["kind"], "COLLECT")
        self.assertIn("Tungsten", action["title"])

    def test_next_engineer_action_identifies_module_blueprint_and_experimental(self):
        plan = {
            "module": "Hull Reinforcement Package",
            "blueprint": "Heavy Duty Hull Reinforcement",
            "targetGrade": 5,
            "targetStatus": "not_started",
            "canCraftNext": True,
            "materialProgress": [],
            "experimental": "Deep Plating",
            "boundSlot": "Slot03_Size5",
        }
        state = {"blueprints": [plan], "materials": [], "trades": []}
        route = [{
            "name": "Selene Jean", "system": "Kuk", "station": "Prospector's Rest",
            "craftable": True, "readyJobs": 1,
            "jobNames": [
                "Hull Reinforcement Package · Heavy Duty Hull Reinforcement · G5"
            ],
        }]

        action = select_operation_action(state, route)

        self.assertEqual(action["kind"], "GRADE_CRAFT")
        self.assertEqual(action["shortTitle"], "Continue Hull Reinforcement Package")
        self.assertEqual(action["moduleName"], "Hull Reinforcement Package")
        self.assertEqual(action["blueprintName"], "Heavy Duty Hull Reinforcement")
        self.assertEqual(action["targetGrade"], 5)
        self.assertEqual(action["experimentalName"], "Deep Plating")
        self.assertEqual(action["physicalSlot"], "Slot03_Size5")
        self.assertEqual(
            action["physicalSlotLabel"], "OPTIONAL SLOT 3 · SIZE 5"
        )
        self.assertIn("OPTIONAL SLOT 3 · SIZE 5", action["title"])

    def test_next_engineer_action_distinguishes_identical_hardpoints(self):
        plan = {
            "module": "Multi-cannon",
            "blueprint": "High Capacity Magazine",
            "targetGrade": 5,
            "targetStatus": "not_started",
            "canCraftNext": True,
            "materialProgress": [],
            "boundSlot": "MediumHardpoint2",
        }
        route = [{
            "name": "Tod McQuinn", "system": "Wolf 397",
            "station": "Trophy Camp", "craftable": True,
            "jobNames": [
                "Multi-cannon · High Capacity Magazine · G5"
            ],
        }]

        action = select_operation_action(
            {"blueprints": [plan], "materials": [], "trades": []}, route
        )

        self.assertEqual(
            action["physicalSlotLabel"], "MEDIUM HARDPOINT 2 · SIZE 2"
        )
        self.assertIn("MEDIUM HARDPOINT 2 · SIZE 2", action["title"])

    def test_engineer_assignment_globally_minimizes_repeat_visits(self):
        plans = [
            {
                "module": "A", "blueprint": "One", "grade": 5,
                "eligibleEngineers": ["Nearby A", "Shared"],
                "completion": 1, "targetStatus": "not_started",
            },
            {
                "module": "B", "blueprint": "Two", "grade": 5,
                "eligibleEngineers": ["Nearby B", "Shared"],
                "completion": 1, "targetStatus": "not_started",
            },
            {
                "module": "C", "blueprint": "Three", "grade": 5,
                "eligibleEngineers": ["Nearby C", "Shared"],
                "completion": 1, "targetStatus": "not_started",
            },
        ]
        engineers = [
            {
                "name": name, "statusGroup": "unlocked", "rank": 5,
                "distance": distance, "status": "UNLOCKED",
            }
            for name, distance in (
                ("Nearby A", 1), ("Nearby B", 2), ("Nearby C", 3),
                ("Shared", 50),
            )
        ]

        route = assign_plans_to_nearest_engineers(plans, engineers)

        self.assertEqual([row["name"] for row in route], ["Shared"])
        self.assertEqual(route[0]["openJobs"], 3)

    def test_engineer_route_minimizes_total_distance_not_each_next_leg(self):
        plans = [{
            "module": name, "blueprint": "Target", "grade": 5,
            "eligibleEngineers": [name], "completion": 1,
            "targetStatus": "not_started",
        } for name in ("A", "B", "C", "D")]
        positions = {
            "A": [0, 1, 0], "B": [0, 2, 0],
            "C": [0, 3, 0], "D": [1, 0, 0],
        }
        engineers = [{
            "name": name, "statusGroup": "unlocked", "rank": 5,
            "distance": (sum(value * value for value in position) ** 0.5),
            "coordinates": position, "status": "UNLOCKED",
        } for name, position in positions.items()]

        route = assign_plans_to_nearest_engineers(plans, engineers)

        # Nearest-neighbour chooses A-B-C-D (6.16 ly). The global optimum is
        # D-A-B-C (4.41 ly), so no late cross-map return is introduced.
        self.assertEqual([row["name"] for row in route], ["D", "A", "B", "C"])

    def test_current_craftable_engineer_is_mandatory_first_stop(self):
        plans = [{
            "module": name, "blueprint": "Target", "grade": 1,
            "eligibleEngineers": [name], "completion": 1,
            "targetStatus": "not_started",
        } for name in ("Ram Tah", "Tiana Fortune", "Mel Brandon")]
        engineers = [
            {
                "name": "Ram Tah", "statusGroup": "unlocked", "rank": 2,
                "distance": 0, "coordinates": [0, 0, 0], "status": "UNLOCKED",
            },
            {
                "name": "Tiana Fortune", "statusGroup": "locked", "rank": 0,
                "distance": 100, "coordinates": [100, 0, 0], "status": "LOCKED",
            },
            {
                "name": "Mel Brandon", "statusGroup": "locked", "rank": 0,
                "distance": 1000, "coordinates": [-1000, 0, 0], "status": "LOCKED",
            },
        ]

        route = assign_plans_to_nearest_engineers(plans, engineers)

        self.assertEqual(route[0]["name"], "Ram Tah")
        self.assertTrue(route[0]["craftable"])

    def test_single_plan_uses_nearest_engineer_with_required_rank(self):
        plans = [{
            "module": "Frame Shift Drive", "blueprint": "Increased Range",
            "grade": 5, "eligibleEngineers": ["Near Low", "Near G5", "Far G5"],
            "completion": 1, "targetStatus": "not_started",
        }]
        engineers = [
            {
                "name": "Near Low", "statusGroup": "unlocked", "rank": 4,
                "distance": 1, "status": "UNLOCKED",
            },
            {
                "name": "Near G5", "statusGroup": "unlocked", "rank": 5,
                "distance": 10, "status": "UNLOCKED",
            },
            {
                "name": "Far G5", "statusGroup": "unlocked", "rank": 5,
                "distance": 100, "status": "UNLOCKED",
            },
        ]

        route = assign_plans_to_nearest_engineers(plans, engineers)

        self.assertEqual([row["name"] for row in route], ["Near G5"])
        self.assertTrue(route[0]["craftable"])

    def test_current_engineer_consolidates_all_compatible_jobs_before_departure(self):
        plans = [
            {
                "module": "Frame Shift Drive", "blueprint": "Increased Range",
                "grade": 5, "targetGrade": 5,
                "eligibleEngineers": ["Felicity Farseer"],
                "completion": 1, "canCraftNext": True,
                "targetStatus": "not_started", "priority": True,
                "materialProgress": [],
            },
            {
                "module": "Shield Generator", "blueprint": "Thermal Resistant",
                "grade": 5, "targetGrade": 5,
                "eligibleEngineers": ["Lei Cheung", "Didi Vatermann"],
                "selectedEngineer": "Didi Vatermann",
                "completion": 1, "canCraftNext": True,
                "targetStatus": "not_started", "priority": False,
                "materialProgress": [],
            },
        ]
        engineers = [
            {
                "name": "Lei Cheung", "statusGroup": "unlocked", "rank": 5,
                "distance": 0, "status": "UNLOCKED", "system": "Laksak",
            },
            {
                "name": "Didi Vatermann", "statusGroup": "unlocked", "rank": 5,
                "distance": 40, "status": "UNLOCKED", "system": "Leesti",
            },
            {
                "name": "Felicity Farseer", "statusGroup": "unlocked", "rank": 5,
                "distance": 60, "status": "UNLOCKED", "system": "Deciat",
            },
        ]

        route = assign_plans_to_nearest_engineers(plans, engineers)
        route.sort(key=lambda row: float(row.get("distance", -1)))
        action = select_operation_action(
            {"blueprints": plans, "materials": [], "trades": []}, route
        )

        self.assertEqual(route[0]["name"], "Lei Cheung")
        self.assertIn("Shield Generator", route[0]["jobNames"][0])
        self.assertEqual(action["kind"], "GRADE_CRAFT")
        self.assertEqual(action["moduleName"], "Shield Generator")
        self.assertEqual(action["engineerName"], "Lei Cheung")

    def test_build_import_accepts_file_dialog_urls(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "Krait build.json"
            payload = {"Ship": "Krait_MkII", "Modules": []}
            path.write_text(json.dumps(payload), encoding="utf-8")

            self.assertEqual(_read_input(path.as_uri()), payload)

    def test_build_import_preserves_matching_installed_grade_boundary(self):
        payload = {
            "Ship": "Krait_MkII",
            "Modules": [{
                "Slot": "LargeHardpoint1",
                "Item": "hpt_multicannon_gimbal_large",
                "Engineering": {
                    "BlueprintName": "Weapon_Overcharged",
                    "Level": 5,
                    "ExperimentalEffect": "special_weapon_auto_loader",
                },
            }],
        }
        blueprints = [{
            "Type": "Multi-cannon", "Name": "Overcharged Weapon", "Grade": 5,
        }]
        experimentals = [{
            "Name": "Auto Loader",
            "ExperimentalId": "multicannon::auto_loader",
            "EdName": "special_weapon_auto_loader",
            "ModuleTypes": ["Multi-cannon"],
        }]
        slots = [{
            "slot": "LargeHardpoint1",
            "moduleId": "hpt_multicannon_gimbal_large",
            "engineeringGrade": 5,
            "engineeringBlueprint": "Weapon_Overcharged",
            "experimentalEffect": "special_weapon_auto_loader",
        }]

        preview = preview_build(
            json.dumps(payload), "Krait Mk II", blueprints, experimentals,
            module_matches_type, physical_slots=slots,
        )

        self.assertEqual(preview["rows"][0]["currentGrade"], 4)
        self.assertTrue(preview["rows"][0]["experimentalComplete"])

    def test_loadout_preserves_last_known_quality_for_unchanged_module(self):
        events = [{
            "timestamp": "2026-08-30T10:00:00Z", "event": "Loadout",
            "ShipID": 37, "Modules": [{
                "Slot": "LargeHardpoint1",
                "Item": "hpt_multicannon_gimbal_large",
                "Engineering": {
                    "BlueprintName": "Weapon_Overcharged", "Level": 5,
                },
            }],
        }, {
            "timestamp": "2026-08-30T10:01:00Z", "event": "EngineerCraft",
            "ShipID": 37, "Slot": "LargeHardpoint1",
            "Module": "hpt_multicannon_gimbal_large",
            "BlueprintName": "Weapon_Overcharged", "Level": 5,
            "Quality": 0.6,
        }, {
            "timestamp": "2026-08-30T10:02:00Z", "event": "Loadout",
            "ShipID": 37, "Modules": [{
                "Slot": "LargeHardpoint1",
                "Item": "hpt_multicannon_gimbal_large",
                "Engineering": {
                    "BlueprintName": "Weapon_Overcharged", "Level": 5,
                },
            }],
        }]

        installed = latest_loadout_slots(events, 37)[0]

        self.assertTrue(installed["engineeringQualityKnown"])
        self.assertEqual(installed["engineeringQuality"], 0.6)

    def test_matching_installed_quality_reduces_remaining_materials(self):
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            plan = build_engineering_plan(
                [{
                    "Type": "Multi-cannon", "Name": "Overcharged Weapon",
                    "Grade": 5, "Ingredients": [{"Name": "Zirconium", "Size": 1}],
                }],
                0, 5, ship_id=37, slot="LargeHardpoint1",
                module_id="hpt_multicannon_gimbal_large",
            )
            (data_dir / "ship_blueprints.json").write_text(
                json.dumps({"Krait": [plan]}), encoding="utf-8"
            )
            events = [{
                "timestamp": "2026-08-30T10:00:00Z", "event": "Loadout",
                "ShipID": 37, "Modules": [{
                    "Slot": "LargeHardpoint1",
                    "Item": "hpt_multicannon_gimbal_large",
                    "Engineering": {
                        "BlueprintName": "Weapon_Overcharged", "Level": 5,
                        "Quality": 0.6,
                    },
                }],
            }]
            migrate_wishlist_bindings(
                data_dir, {"ships": [{"label": "Krait", "id": "37"}]}, events,
            )
            saved = json.loads(
                (data_dir / "ship_blueprints.json").read_text(encoding="utf-8")
            )["Krait"]

        planner = saved[0][0]["_Planner"]
        self.assertEqual(planner["current_grade"], 5)
        self.assertEqual(planner["crafts_completed"]["5"], 3)
        self.assertEqual(required_materials(saved), {"zirconium": 2})

    def test_partial_current_g5_can_target_g5_again(self):
        plan = build_engineering_plan(
            [{
                "Type": "Multi-cannon", "Name": "Overcharged Weapon",
                "Grade": 5, "Ingredients": [{"Name": "Zirconium", "Size": 1}],
            }],
            5, 5, ship_id=37, slot="MediumHardpoint2",
            module_id="hpt_multicannon_gimbal_medium",
            grade_progress={"5": 0.6}, crafts_completed={"5": 3},
        )

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["Grade"], 5)
        self.assertEqual(required_materials([plan]), {"zirconium": 2})

    def test_g1_target_calibrates_from_observed_quality(self):
        plan = build_engineering_plan(
            [{
                "Type": "Heat Sink Launcher", "Name": "Ammo Capacity",
                "Grade": 1, "Ingredients": [
                    {"Name": "Vanadium", "Size": 1},
                ],
            }],
            0, 1, ship_id=37, slot="TinyHardpoint4",
            module_id="hpt_heatsinklauncher_turret_tiny",
        )
        self.assertEqual(required_materials([plan]), {"vanadium": 1})
        planner = plan[0]["_Planner"]
        planner["grade_progress"] = {"1": 0.25}
        planner["crafts_completed"] = {"1": 1}
        self.assertEqual(required_materials([plan]), {"vanadium": 1})

    def test_roll_learning_is_scoped_to_exact_blueprint_and_grade(self):
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            heat_sink = build_engineering_plan(
                [{
                    "Type": "Heat Sink Launcher", "Name": "Ammo Capacity",
                    "Grade": 1,
                    "Ingredients": [{"Name": "Vanadium", "Size": 1}],
                }],
                0, 1, ship_id=37, slot="TinyHardpoint4",
                module_id="hpt_heatsinklauncher_turret_tiny",
            )
            heat_sink[0]["_Planner"].update({
                "grade_progress": {"1": 0.25},
                "crafts_completed": {"1": 1},
                "processed_crafts": ["current-craft"],
            })
            fsd = build_engineering_plan(
                [{
                    "Type": "Frame Shift Drive", "Name": "Increased Range",
                    "Grade": 1,
                    "Ingredients": [{"Name": "Atypical Wake Echoes", "Size": 1}],
                }],
                0, 1, ship_id=37, slot="FrameShiftDrive",
                module_id="int_hyperdrive_size5_class5",
            )
            (data_dir / "ship_blueprints.json").write_text(json.dumps({
                "Krait": [heat_sink, fsd],
            }), encoding="utf-8")
            events = [{
                "timestamp": "2026-06-28T16:09:00Z", "event": "EngineerCraft",
                "ShipID": 1, "BlueprintName": "Misc_HeatSinkCapacity",
                "Level": 1, "Quality": 0.2,
            }]
            migrate_wishlist_bindings(
                data_dir, {"ships": [{"label": "Krait", "id": "37"}]}, events,
            )
            saved = json.loads(
                (data_dir / "ship_blueprints.json").read_text(encoding="utf-8")
            )["Krait"]

        self.assertEqual(saved[0][0]["_Rolls"], 5)
        self.assertEqual(required_materials([saved[0]]), {"vanadium": 3})
        self.assertEqual(saved[1][0]["_Rolls"], 1)

    def test_different_installed_blueprint_grants_no_material_credit(self):
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            plan = build_engineering_plan(
                [{
                    "Type": "Multi-cannon", "Name": "Overcharged Weapon",
                    "Grade": 5, "Ingredients": [{"Name": "Zirconium", "Size": 1}],
                }],
                0, 5, ship_id=37, slot="LargeHardpoint1",
                module_id="hpt_multicannon_gimbal_large",
            )
            (data_dir / "ship_blueprints.json").write_text(
                json.dumps({"Krait": [plan]}), encoding="utf-8"
            )
            events = [{
                "timestamp": "2026-08-30T10:00:00Z", "event": "Loadout",
                "ShipID": 37, "Modules": [{
                    "Slot": "LargeHardpoint1",
                    "Item": "hpt_multicannon_gimbal_large",
                    "Engineering": {
                        "BlueprintName": "Weapon_HighCapacity", "Level": 5,
                        "Quality": 1.0,
                    },
                }],
            }]
            migrate_wishlist_bindings(
                data_dir, {"ships": [{"label": "Krait", "id": "37"}]}, events,
            )
            saved = json.loads(
                (data_dir / "ship_blueprints.json").read_text(encoding="utf-8")
            )["Krait"]

        planner = saved[0][0]["_Planner"]
        self.assertEqual(planner["current_grade"], 0)
        self.assertEqual(planner["crafts_completed"], {})
        self.assertEqual(required_materials(saved), {"zirconium": 5})

    def test_existing_wishlist_reconciles_installed_slot_without_recrafing_lower_grades(self):
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            planner = {
                "plan_id": "plan-1", "ship_id": "37",
                "slot": "LargeHardpoint1",
                "module_id": "hpt_multicannon_gimbal_large",
                "current_grade": 0, "target_grade": 5,
                "grade_progress": {},
                "experimental_id": "multicannon::auto_loader",
                "experimental_name": "Auto Loader",
                "experimental_complete": False,
                "plan_mode": "combined",
            }
            payload = {
                "Krait Mk II – Mechthild": [[{
                    "Type": "Multi-cannon", "Name": "Overcharged Weapon",
                    "Grade": 5, "Engineers": ["Tod McQuinn"],
                    "_Planner": planner,
                }]],
            }
            (data_dir / "ship_blueprints.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            events = [{
                "timestamp": "2026-08-30T10:00:00Z", "event": "Loadout",
                "ShipID": 37,
                "Modules": [{
                    "Slot": "LargeHardpoint1",
                    "Item": "hpt_multicannon_gimbal_large",
                    "Engineering": {
                        "BlueprintName": "Weapon_Overcharged", "Level": 5,
                        "ExperimentalEffect": "special_weapon_incendiary",
                    },
                }],
            }]
            fleet = {"ships": [{
                "label": "Krait Mk II – Mechthild", "id": "37",
            }]}

            migrate_wishlist_bindings(data_dir, fleet, events)

            saved = json.loads(
                (data_dir / "ship_blueprints.json").read_text(encoding="utf-8")
            )
            saved_planner = saved["Krait Mk II – Mechthild"][0][0]["_Planner"]
            self.assertEqual(saved_planner["current_grade"], 4)
            self.assertEqual(saved_planner["grade_progress"], {})
            self.assertFalse(saved_planner["experimental_complete"])

            craft = {
                "timestamp": "2026-08-30T10:05:00Z",
                "event": "EngineerCraft", "Slot": "LargeHardpoint1",
                "Module": "hpt_multicannon_gimbal_large",
                "Engineer": "Tod 'The Blaster' McQuinn",
                "BlueprintName": "Weapon_Overcharged",
                "BlueprintID": 128673806, "Level": 5, "Quality": 0.2,
                "Ingredients": [{"Name": "zirconium", "Count": 1}],
            }
            result = apply_engineer_craft(
                data_dir / "ship_blueprints.json",
                "Krait Mk II – Mechthild", craft, ship_id=37,
            )
            replayed = json.loads(
                (data_dir / "ship_blueprints.json").read_text(encoding="utf-8")
            )["Krait Mk II – Mechthild"][0][0]["_Planner"]

            self.assertEqual(result["status"], "applied")
            self.assertEqual(replayed["grade_progress"]["5"], 0.2)

    def test_qt_cache_and_runtime_data_share_the_canonical_app_directory(self):
        root = Path(__file__).resolve().parents[1]
        entrypoint = (root / "phase14_main.py").read_text(encoding="utf-8")

        self.assertIn(
            'app.setApplicationName("EDEngineeringCompanion")', entrypoint
        )
        self.assertIn(
            'app.setApplicationDisplayName("ED Engineering Companion")',
            entrypoint,
        )
        self.assertNotIn(
            'app.setApplicationName("ED Engineering Companion")', entrypoint
        )

    def test_trader_preference_switches_confidence_and_distance_priority(self):
        stations = [
            {
                "station": "Confirmed Port", "traderType": "Raw",
                "traderConfidence": "confirmed", "coordinates": [50, 0, 0],
                "distance_ls": 10,
            },
            {
                "station": "Nearby Port", "traderType": "Raw",
                "traderConfidence": "external", "coordinates": [10, 0, 0],
                "distance_ls": 100,
            },
        ]

        confirmed = find_nearest_catalog_trader(
            "Raw", [0, 0, 0], stations, "confirmed"
        )
        nearest = find_nearest_catalog_trader(
            "Raw", [0, 0, 0], stations, "nearest"
        )

        self.assertEqual(confirmed["station"], "Confirmed Port")
        self.assertEqual(nearest["station"], "Nearby Port")

    def _krait_slots(self):
        root = Path(__file__).resolve().parents[1]
        ships = json.loads((root / "ed_data" / "ships.json").read_text(encoding="utf-8"))
        ship = next(row for row in ships if row["symbol"] == "Krait_MkII")
        return ship_slot_layout(ship, [], [])

    def test_edec_export_round_trips_exact_physical_slot_to_import(self):
        root = Path(__file__).resolve().parents[1]
        events = [{
            "timestamp": "2026-08-25T10:00:00Z", "event": "Loadout",
            "Ship": "Krait_MkII", "ShipID": 42,
            "Modules": [{
                "Slot": "FrameShiftDrive",
                "Item": "$Int_Hyperdrive_Size5_Class5_Name;",
                "Engineering": {
                    "BlueprintName": "FSD_LongRange", "Level": 5,
                    "ExperimentalEffect": "special_fsd_heavy",
                },
            }],
        }]
        payload = build_loadout_export(
            events, 42, "Krait_MkII",
            latest_loadout_slots(events, 42),
            json.loads((root / "ed_data" / "experimental_effects.json").read_text(encoding="utf-8")),
        )
        preview = preview_build(
            json.dumps(payload), "Krait_MkII",
            json.loads((root / "ed_data" / "blueprints.json").read_text(encoding="utf-8")),
            json.loads((root / "ed_data" / "experimental_effects.json").read_text(encoding="utf-8")),
            module_matches_type, physical_slots=self._krait_slots(),
        )

        self.assertEqual(payload["status"], "COMPLETE")
        self.assertEqual(preview["status"], "COMPLETE")
        self.assertEqual(preview["recognized"], 1)
        self.assertEqual(preview["rows"][0]["slot"], "FrameShiftDrive")
        self.assertTrue(preview["rows"][0]["slotBound"])
        self.assertEqual(preview["rows"][0]["grade"], 5)
        self.assertEqual(preview["rows"][0]["experimental"], "Mass Manager")

        slef_preview = preview_build(
            json.dumps({
                "header": {"appName": "EDSY", "appVersion": "test"},
                "data": {"Ship": payload["Ship"], "Modules": payload["Modules"]},
            }),
            "Krait_MkII",
            json.loads((root / "ed_data" / "blueprints.json").read_text(encoding="utf-8")),
            json.loads((root / "ed_data" / "experimental_effects.json").read_text(encoding="utf-8")),
            module_matches_type, physical_slots=self._krait_slots(),
        )
        self.assertEqual(slef_preview["source"], "SLEF")
        self.assertEqual(slef_preview["rows"][0]["slot"], "FrameShiftDrive")
        self.assertTrue(slef_preview["rows"][0]["slotBound"])

    def test_import_tracks_non_engineered_module_replacements_by_slot(self):
        root = Path(__file__).resolve().parents[1]
        ships = json.loads((root / "ed_data" / "ships.json").read_text(encoding="utf-8"))
        ship = next(row for row in ships if row["symbol"] == "Krait_MkII")
        installed = [{
            "slot": "Slot03_Size5",
            "moduleId": "int_hullreinforcement_size5_class2",
        }]
        slots = ship_slot_layout(ship, installed, [])
        payload = {
            "format": "EDOPS_LOADOUT_V1", "Ship": "krait_mkii",
            "Modules": [{
                "Slot": "Slot03_Size5", "Item": "int_fuelscoop_size5_class5",
            }],
        }

        preview = preview_build(
            json.dumps(payload), "Krait_MkII", [], [],
            module_matches_type, physical_slots=slots,
        )
        desired_rows = ship_slot_layout(
            ship, installed, [],
            {"Slot03_Size5": "int_fuelscoop_size5_class5"},
        )
        desired = next(row for row in desired_rows if row["slot"] == "Slot03_Size5")

        self.assertEqual(preview["status"], "COMPLETE")
        self.assertEqual(preview["recognized"], 1)
        self.assertEqual(preview["moduleChanges"], 1)
        self.assertEqual(preview["rows"][0]["planMode"], "module_only")
        self.assertTrue(desired["moduleChange"])
        self.assertEqual(desired["desiredModule"], "FUEL SCOOP")
        self.assertEqual(desired["desiredSizeRating"], "5A")

    def test_coriolis_component_path_maps_only_through_hull_schema(self):
        root = Path(__file__).resolve().parents[1]
        build = {
            "ship": "Krait_MkII",
            "components": {
                "standard": {"frameShiftDrive": {
                    "item": "$Int_Hyperdrive_Size5_Class5_Name;",
                    "blueprint": {"name": "Increased FSD Range", "grade": 5},
                }},
                "internal": [{
                    "item": "$Int_ShieldGenerator_Size6_Class5_Name;",
                    "blueprint": {"name": "Reinforced Shields", "grade": 5},
                }],
                "mysteryBank": [{
                    "item": "$Int_Hyperdrive_Size5_Class5_Name;",
                    "blueprint": {"name": "Increased FSD Range", "grade": 5},
                }],
            },
        }
        preview = preview_build(
            json.dumps(build), "Krait_MkII",
            json.loads((root / "ed_data" / "blueprints.json").read_text(encoding="utf-8")),
            json.loads((root / "ed_data" / "experimental_effects.json").read_text(encoding="utf-8")),
            module_matches_type, physical_slots=self._krait_slots(),
        )

        bound = [row for row in preview["rows"] if row.get("slotBound")]
        blocked = [row for row in preview["rows"] if row.get("status") == "blocked"]
        self.assertEqual(
            [row["slot"] for row in bound],
            ["FrameShiftDrive", "Slot01_Size6"],
        )
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["slot"], "")
        self.assertEqual(preview["recognized"], 2)

    def test_every_supported_ship_has_a_top_view_schematic(self):
        root = Path(__file__).resolve().parents[1]
        catalog = json.loads(
            (root / "ed_data" / "ships.json").read_text(encoding="utf-8-sig")
        )

        self.assertEqual(len(catalog), 48)
        self.assertEqual(len({row["symbol"] for row in catalog}), 48)
        missing = [
            row["symbol"] for row in catalog
            if not (root / "assets" / "ships" / f"{row['symbol']}.svg").is_file()
        ]
        self.assertEqual(missing, [])
        for ship in catalog:
            slots = ship_slot_layout(ship, [], [])
            expected = (
                8 + len(ship.get("optional", []))
                + len(ship.get("hardpoints", [])) + int(ship.get("utility", 0))
            )
            self.assertEqual(
                len(slots), expected,
                f"physical slot schema mismatch for {ship['symbol']}",
            )
            self.assertEqual(
                len({row["slot"] for row in slots}), expected,
                f"duplicate physical slot key for {ship['symbol']}",
            )

    def test_engineering_loadout_rows_bind_concrete_ship_slots(self):
        rows = engineering_loadout_rows(
            [
                {
                    "slot": "FrameShiftDrive",
                    "moduleId": "$Int_Hyperdrive_Size5_Class5_Name;",
                },
                {
                    "slot": "TinyHardpoint1",
                    "moduleId": "$Hpt_HeatSinkLauncher_Turret_Tiny_Name;",
                },
            ],
            [
                {
                    "module": "Frame Shift Drive",
                    "category": "Core Internals",
                    "name": "Increased Range",
                },
                {
                    "module": "Frame Shift Drive",
                    "category": "Core Internals",
                    "name": "Shielded",
                },
            ],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slot"], "FrameShiftDrive")
        self.assertEqual(rows[0]["module"], "Frame Shift Drive")
        self.assertEqual(rows[0]["sizeRating"], "5A")
        self.assertEqual(rows[0]["displaySlot"], "CORE · FRAME SHIFT DRIVE")
        self.assertEqual(rows[0]["blueprintCount"], 2)
        self.assertEqual(
            rows[0]["bindingKey"],
            "FrameShiftDrive\u241f$Int_Hyperdrive_Size5_Class5_Name;",
        )

    def test_ship_slot_layout_uses_the_selected_hulls_exact_schema(self):
        ship = {
            "core": {
                "powerPlant": 7, "thrusters": 6, "frameShiftDrive": 5,
                "lifeSupport": 4, "powerDistributor": 7,
                "sensors": 6, "fuelTank": 5,
            },
            "optional": [
                {"size": 6},
                {"size": 1, "restriction": "planetaryApproachSuite"},
            ],
            "hardpoints": [{"size": 3}, {"size": 2}],
            "utility": 2,
        }
        rows = ship_slot_layout(
            ship,
            [{
                "slot": "FrameShiftDrive",
                "moduleId": "$Int_Hyperdrive_Size5_Class5_Name;",
            }],
            [{
                "module": "Frame Shift Drive",
                "category": "Core Internals",
                "name": "Increased Range",
            }],
        )

        self.assertEqual(len(rows), 14)
        self.assertEqual(
            [row["slot"] for row in rows if row["group"] == "HARDPOINTS"],
            ["LargeHardpoint1", "MediumHardpoint1"],
        )
        restricted = next(row for row in rows if row["restriction"])
        self.assertEqual(restricted["slot"], "Slot02_Size1")
        self.assertTrue(restricted["empty"])
        fsd = next(row for row in rows if row["slot"] == "FrameShiftDrive")
        self.assertEqual(fsd["sizeRating"], "5A")
        self.assertTrue(fsd["engineerable"])

    def test_loadout_engineering_grade_reaches_the_physical_slot(self):
        module_slots = latest_loadout_slots(
            [{
                "timestamp": "2026-08-25T19:00:00Z",
                "event": "Loadout",
                "ShipID": 42,
                "Modules": [{
                    "Slot": "FrameShiftDrive",
                    "Item": "$Int_Hyperdrive_Size5_Class5_Name;",
                    "Engineering": {
                        "Engineer": "Felicity Farseer",
                        "BlueprintName": "FSD_LongRange",
                        "Level": 4,
                        "Quality": 0.72,
                        "ExperimentalEffect": "special_fsd_heavy",
                        "ExperimentalEffect_Localised": "Mass Manager",
                    },
                }],
            }],
            42,
        )
        rows = ship_slot_layout(
            {
                "core": {"frameShiftDrive": 5},
                "optional": [], "hardpoints": [], "utility": 0,
            },
            module_slots,
            [{
                "module": "Frame Shift Drive",
                "category": "Core Internals",
                "name": "Increased Range",
            }],
        )
        fsd = next(row for row in rows if row["slot"] == "FrameShiftDrive")
        self.assertTrue(fsd["engineered"])
        self.assertEqual(fsd["engineeringGrade"], 4)
        self.assertEqual(fsd["engineeringBlueprint"], "FSD_LongRange")
        self.assertEqual(fsd["experimentalEffect"], "Mass Manager")

    def test_module_buy_symbol_matches_plain_desired_module_id(self):
        module_slots = latest_loadout_slots(
            [{
                "timestamp": "2026-08-28T20:09:51Z",
                "event": "ModuleBuy", "ShipID": 37,
                "Slot": "Slot01_Size5",
                "BuyItem": "$int_fuelscoop_size5_class5_name;",
            }],
            37,
        )
        rows = ship_slot_layout(
            {"optional": [{"size": 5}], "hardpoints": [], "utility": 0},
            module_slots, [],
            {"Slot01_Size5": "int_fuelscoop_size5_class5"},
        )
        scoop = next(row for row in rows if row["slot"] == "Slot01_Size5")

        self.assertEqual(scoop["moduleId"], "int_fuelscoop_size5_class5")
        self.assertFalse(scoop["moduleChange"])

    def test_engineer_craft_without_ship_id_updates_active_physical_slot(self):
        events = [
            {
                "timestamp": "2026-08-25T18:32:38Z", "event": "Loadout",
                "Ship": "ferdelance", "ShipID": 35,
                "Modules": [{
                    "Slot": "PowerPlant", "Item": "int_powerplant_size6_class5",
                    "Engineering": {
                        "BlueprintName": "PowerPlant_Boosted", "Level": 1,
                        "ExperimentalEffect": "special_powerplant_highcharge",
                    },
                }],
            },
            {
                "timestamp": "2026-08-25T19:01:56Z", "event": "EngineerCraft",
                "Slot": "PowerPlant", "Module": "int_powerplant_size6_class5",
                "BlueprintName": "PowerPlant_Armoured", "Level": 5,
            },
            {
                "timestamp": "2026-08-25T19:02:20Z", "event": "EngineerCraft",
                "Slot": "PowerPlant", "Module": "int_powerplant_size6_class5",
                "BlueprintName": "PowerPlant_Armoured", "Level": 5,
                "ExperimentalEffect": "special_powerplant_cooled",
            },
            {
                "timestamp": "2026-08-25T19:04:30Z", "event": "EngineerCraft",
                "Slot": "PowerPlant", "Module": "int_powerplant_size6_class5",
                "BlueprintName": "PowerPlant_Boosted", "Level": 5,
            },
        ]

        power_plant = next(
            row for row in latest_loadout_slots(events, 35)
            if row["slot"] == "PowerPlant"
        )
        self.assertEqual(power_plant["engineeringGrade"], 5)
        self.assertEqual(power_plant["engineeringBlueprint"], "PowerPlant_Boosted")
        self.assertEqual(power_plant["experimentalEffect"], "special_powerplant_cooled")

    def test_standalone_experimental_ready_drives_the_next_action(self):
        plan = build_experimental_plan(
            {
                "ExperimentalId": "power_plant::thermal_spread",
                "Name": "Thermal Spread",
                "Engineers": ["Hera Tani"],
                "Ingredients": [
                    {"Name": "Grid Resistors", "Size": 5},
                    {"Name": "Heat Vanes", "Size": 1},
                    {"Name": "Vanadium", "Size": 3},
                ],
            },
            ship_id=35,
            slot="PowerPlant",
            module_id="int_powerplant_size6_class5",
            module_type="Power Plant",
        )
        metadata = {
            "gridresistors": {"Name": "Grid Resistors", "Category": "Manufactured"},
            "heatvanes": {"Name": "Heat Vanes", "Category": "Manufactured"},
            "vanadium": {"Name": "Vanadium", "Category": "Raw"},
        }
        rows = blueprint_rows(
            [plan],
            {"gridresistors": 5, "heatvanes": 1, "vanadium": 3},
            metadata,
        )

        self.assertEqual(rows[0]["targetStatus"], "experimental_pending")
        self.assertTrue(rows[0]["experimentalReady"])
        self.assertEqual(
            sum(item["missing"] for item in rows[0]["experimentalMaterialProgress"]),
            0,
        )

    def test_journal_machine_effect_completes_canonical_experimental_plan(self):
        plan = build_experimental_plan(
            {
                "ExperimentalId": "power_plant::thermal_spread",
                "Name": "Thermal Spread",
                "Ingredients": [{"Name": "Grid Resistors", "Size": 5}],
            },
            ship_id=35, slot="PowerPlant",
            module_id="int_powerplant_size6_class5",
            module_type="Power Plant",
        )
        event = {
            "timestamp": "2026-08-25T19:40:02Z",
            "event": "EngineerCraft", "Slot": "PowerPlant",
            "Module": "int_powerplant_size6_class5",
            "BlueprintID": 128673769, "BlueprintName": "PowerPlant_Boosted",
            "Level": 5, "Quality": 0.4,
            "ApplyExperimentalEffect": "special_powerplant_cooled",
            "ExperimentalEffect": "special_powerplant_cooled",
            "ExperimentalEffect_Localised": "Thermal Spread",
            "Ingredients": [{"Name": "gridresistors", "Count": 5}],
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ship_blueprints.json"
            path.write_text(
                json.dumps({"Fer-de-Lance – Signe": [plan]}), encoding="utf-8"
            )
            result = apply_engineer_craft(
                path, "Fer-de-Lance – Signe", event, ship_id=35,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "applied")
        self.assertTrue(saved["Fer-de-Lance – Signe"][0][0]["_Planner"]["experimental_complete"])

    def test_unique_identical_module_plan_follows_the_actually_crafted_slot(self):
        plan = build_engineering_plan(
            [{
                "Type": "Multi-cannon", "Name": "High Capacity Magazine",
                "Grade": 1, "Engineers": ["Tod McQuinn"],
                "Ingredients": [{"Name": "Mechanical Scrap", "Size": 1}],
            }],
            0, 1, ship_id=37, slot="MediumHardpoint2",
            module_id="hpt_multicannon_gimbal_medium",
        )
        event = {
            "timestamp": "2026-08-30T06:35:59Z", "event": "EngineerCraft",
            "Slot": "MediumHardpoint1",
            "Module": "hpt_multicannon_gimbal_medium",
            "Engineer": "Tod 'The Blaster' McQuinn",
            "BlueprintID": 128673500,
            "BlueprintName": "Weapon_HighCapacity",
            "Level": 1, "Quality": 1.0,
            "Ingredients": [{"Name": "mechanicalscrap", "Count": 1}],
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ship_blueprints.json"
            path.write_text(
                json.dumps({"Krait Mk II – Mechthild": [plan]}),
                encoding="utf-8",
            )

            result = apply_engineer_craft(
                path, "Krait Mk II – Mechthild", event, ship_id=37,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            planner = saved["Krait Mk II – Mechthild"][0][0]["_Planner"]

        self.assertEqual(result["status"], "applied")
        self.assertEqual(planner["slot"], "MediumHardpoint1")
        self.assertEqual(planner["instance"], "MediumHardpoint1")

    def test_weapon_machine_effect_without_localized_name_is_canonical(self):
        plan = build_experimental_plan(
            {
                "ExperimentalId": "multi-cannon::corrosive_shell",
                "Name": "Corrosive Shell",
                "Ingredients": [{"Name": "Arsenic", "Size": 3}],
            },
            ship_id=37, slot="MediumHardpoint1",
            module_id="hpt_multicannon_gimbal_medium",
            module_type="Multi-cannon",
        )
        event = {
            "timestamp": "2026-08-30T06:40:20Z",
            "event": "EngineerCraft", "Slot": "MediumHardpoint1",
            "Module": "hpt_multicannon_gimbal_medium",
            "BlueprintID": 128673500,
            "BlueprintName": "Weapon_HighCapacity",
            "Level": 5, "Quality": 1.0,
            "ApplyExperimentalEffect": "special_corrosive_shell",
            "ExperimentalEffect": "special_corrosive_shell",
            "Ingredients": [{"Name": "arsenic", "Count": 3}],
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ship_blueprints.json"
            path.write_text(
                json.dumps({"Krait Mk II – Mechthild": [plan]}),
                encoding="utf-8",
            )
            result = apply_engineer_craft(
                path, "Krait Mk II – Mechthild", event, ship_id=37,
            )

        self.assertEqual(result["status"], "applied")

    def test_inara_safety_limits_remain_strict(self):
        self.assertEqual(INARA_MAX_REQUESTS_PER_MINUTE, 2)
        self.assertEqual(INARA_BATCH_WINDOW_SECONDS, 45)
        self.assertEqual(INARA_MIN_REQUEST_INTERVAL_SECONDS, 300)
        self.assertEqual(MAX_EVENTS, 50)

    def test_powerplay_snapshot_uses_only_observed_values(self):
        events = [
            {"timestamp": "2026-08-23T17:00:00Z", "event": "LoadGame"},
            {
                "timestamp": "2026-08-23T17:00:01Z",
                "event": "Powerplay",
                "Power": "Aisling Duval",
                "Rank": 5,
                "Merits": 14230,
                "TimePledged": 1713600,
            },
        ]
        overview = powerplay_journal_overview(events)
        self.assertTrue(overview["pledged"])
        self.assertEqual(overview["power"], "Aisling Duval")
        self.assertEqual(overview["rank"], 5)
        self.assertEqual(overview["merits"], 14230)
        self.assertEqual(overview["timePledgedSeconds"], 1713600)

        unpledged = powerplay_journal_overview([{
            "timestamp": "2026-08-23T17:00:00Z", "event": "LoadGame"
        }])
        self.assertFalse(unpledged["pledged"])
        self.assertFalse(unpledged["rankKnown"])
        self.assertFalse(unpledged["meritsKnown"])

    def test_powerplay_membership_survives_loadgame_without_repeated_snapshot(self):
        events = [
            {
                "timestamp": "2026-08-30T05:30:35Z", "event": "Powerplay",
                "Power": "Aisling Duval", "Rank": 5, "Merits": 17057,
                "TimePledged": 591292,
            },
            {"timestamp": "2026-08-30T05:41:01Z", "event": "LoadGame"},
            {
                "timestamp": "2026-08-30T05:41:05Z", "event": "Location",
                "StarSystem": "Laksak", "ControllingPower": "Yuri Grom",
            },
        ]

        overview = powerplay_journal_overview(events)

        self.assertTrue(overview["pledged"])
        self.assertEqual(overview["power"], "Aisling Duval")
        self.assertEqual(overview["rank"], 5)
        self.assertEqual(overview["merits"], 17057)

        left = powerplay_journal_overview(events + [{
            "timestamp": "2026-08-30T06:00:00Z", "event": "PowerplayLeave",
            "Power": "Aisling Duval",
        }])
        self.assertFalse(left["pledged"])

    def test_scanorganic_contract_filters_private_fields(self):
        context = {}
        for event in (
            {
                "event": "Fileheader",
                "gameversion": "4.2.0.0",
                "build": "r0",
            },
            {"event": "LoadGame", "Horizons": True, "Odyssey": True},
            {
                "event": "Location",
                "StarSystem": "Demo System",
                "SystemAddress": 1000000001,
                "StarPos": [0.0, 0.0, 0.0],
            },
        ):
            context = update_context(context, event)
        prepared = prepare_event({
            "timestamp": "2026-08-23T17:00:02Z",
            "event": "ScanOrganic",
            "ScanType": "Sample",
            "Genus": "$Codex_Ent_Bacterial_Genus_Name;",
            "Genus_Localised": "Bacterium",
            "Species": "$Codex_Ent_Bacterial_01_Name;",
            "Species_Localised": "Bacterium Aurasus",
            "Variant": "$Codex_Ent_Bacterial_01_A_Name;",
            "Variant_Localised": "Bacterium Aurasus Aquamarine",
            "SystemAddress": 1000000001,
            "Body": 4,
        }, context)
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared["schema"], "scanorganic/1")
        self.assertNotIn("Genus_Localised", prepared["message"])
        self.assertEqual(prepared["message"]["BodyID"], 4)
        self.assertTrue(validate_prepared(prepared))

    def test_schema_report_keeps_capi_boundary_honest(self):
        report = schema_parity_report()
        self.assertEqual(
            report["capiRequired"],
            ["blackmarket/1", "fcmaterials_capi/1"],
        )
        self.assertEqual(report["total"], report["supported"] + 2)

    def test_storage_delta_still_uses_authoritative_baseline(self):
        now = datetime.now(timezone.utc)
        timestamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        header = [
            {
                "timestamp": timestamp,
                "event": "Fileheader",
                "gameversion": "4.2.0.0",
                "Odyssey": True,
            },
            {
                "timestamp": timestamp,
                "event": "LoadGame",
                "Commander": "Demo Commander",
                "FID": "F0000000",
            },
            {
                "timestamp": timestamp,
                "event": "Location",
                "StarSystem": "Demo System",
                "StationName": "Demo Station",
                "MarketID": 1000000001,
            },
        ]
        baseline = {
            "timestamp": timestamp,
            "event": "StoredModules",
            "Items": [{
                "Name": "int_cargorack_size2_class1",
                "StorageSlot": 10,
                "BuyPrice": 1000,
                "Hot": False,
                "StarSystem": "Demo System",
                "StationName": "Demo Station",
                "MarketID": 1000000001,
            }],
        }
        _retained, _prepared, known = prepare_journal_batch(
            header + [baseline], now=now, max_events=None
        )
        module_store = {
            "timestamp": timestamp,
            "event": "ModuleStore",
            "MarketID": 1000000001,
            "StoredItem": "hpt_heat_sink_launcher",
            "Ship": "cobra",
            "ShipID": 42,
            "Hot": False,
        }
        _retained, prepared, _fingerprints = prepare_journal_batch(
            header + [baseline, module_store], known, now=now, max_events=None
        )
        snapshots = [
            event for event in prepared
            if event["eventName"] == "setCommanderStorageModules"
        ]
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(
            {row["itemName"] for row in snapshots[0]["eventData"]},
            {"int_cargorack_size2_class1", "hpt_heat_sink_launcher"},
        )


if __name__ == "__main__":
    unittest.main()
