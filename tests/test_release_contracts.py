from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ed_companion.build_import import preview_build
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
    blueprint_rows,
    apply_engineer_craft,
    build_experimental_plan,
    engineering_loadout_rows,
    latest_loadout_slots,
    module_matches_type,
    powerplay_journal_overview,
    ship_slot_layout,
)
from ed_companion.loadout_export import build_loadout_export
from ed_companion.navigation import find_nearest_catalog_trader


class ReleaseContractTests(unittest.TestCase):
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

        unpledged = powerplay_journal_overview([
            {"timestamp": "2026-08-23T17:00:00Z", "event": "LoadGame"}
        ])
        self.assertFalse(unpledged["pledged"])
        self.assertFalse(unpledged["rankKnown"])
        self.assertFalse(unpledged["meritsKnown"])

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
