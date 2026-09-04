from pathlib import Path
import unittest

from ed_companion.build_import import _canonical_import_slot
from ed_companion.phase14.state import (
    canonical_module_id,
    module_purchase_identity,
    ship_slot_layout,
)


class ModulePurchaseDisplayTests(unittest.TestCase):
    def test_internal_module_includes_proven_size_and_rating(self):
        self.assertEqual(
            module_purchase_identity("int_powerplant_size6_class5"),
            ("POWER PLANT", "6A"),
        )

    def test_repairer_ids_display_as_afmu_for_every_size_and_rating(self):
        rating_letters = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A"}
        for size in range(1, 8):
            for module_class, rating in rating_letters.items():
                with self.subTest(size=size, module_class=module_class):
                    self.assertEqual(
                        module_purchase_identity(
                            f"int_repairer_size{size}_class{module_class}"
                        ),
                        ("AUTO FIELD-MAINTENANCE UNIT", f"{size}{rating}"),
                    )

    def test_nonstandard_frontier_classes_use_their_real_ratings(self):
        cases = {
            "int_buggybay_size2_class1": "2H",
            "int_buggybay_size2_class2": "2G",
            "int_largebuggybay_size4_class3": "4F",
            "int_mkiilargebuggybay_size6_class3": "6F",
            "int_corrosionproofcargorack_size4_class2": "4F",
            "int_expmodulestabiliser_size5_class3": "5F",
            "int_fighterbay_size5_class1": "5D",
            "int_fighterbaymk2_size6_class1": "6D",
            "int_largecargorack_size7_class1": "7D",
            "int_mkii_passengercabin_size5_class1": "5D",
            "int_mkii_passengercabin_size5_class2": "5C",
            "int_passengercabin_size4_class0": "4E",
        }
        for module_id, expected in cases.items():
            with self.subTest(module_id=module_id):
                self.assertEqual(
                    module_purchase_identity(module_id)[1], expected
                )

    def test_every_limpet_controller_subtype_keeps_its_specific_name(self):
        cases = {
            "int_dronecontrol_collection_size3_class5": "COLLECTOR LIMPET CONTROLLER",
            "int_dronecontrol_decontamination_size3_class5": "DECONTAMINATION LIMPET CONTROLLER",
            "int_dronecontrol_fueltransfer_size3_class5": "FUEL TRANSFER LIMPET CONTROLLER",
            "int_dronecontrol_prospector_size3_class5": "PROSPECTOR LIMPET CONTROLLER",
            "int_dronecontrol_recon_size3_class1": "RECON LIMPET CONTROLLER",
            "int_dronecontrol_repair_size1_class2": "REPAIR LIMPET CONTROLLER",
            "int_dronecontrol_resourcesiphon_size3_class5": "HATCH BREAKER LIMPET CONTROLLER",
            "int_dronecontrol_unkvesselresearch_size1_class1": "RESEARCH LIMPET CONTROLLER",
            "int_multidronecontrol_mining_size3_class3": "MINING MULTI-LIMPET CONTROLLER",
            "int_multidronecontrol_miningv2_size5_class5": "MK II MINING MULTI-LIMPET CONTROLLER",
            "int_multidronecontrol_operations_size3_class3": "OPERATIONS MULTI-LIMPET CONTROLLER",
            "int_multidronecontrol_rescue_size3_class3": "RESCUE MULTI-LIMPET CONTROLLER",
            "int_multidronecontrol_universal_size7_class3": "UNIVERSAL MULTI-LIMPET CONTROLLER",
            "int_multidronecontrol_xeno_size3_class3": "XENO MULTI-LIMPET CONTROLLER",
        }
        for module_id, expected in cases.items():
            with self.subTest(module_id=module_id):
                self.assertEqual(module_purchase_identity(module_id)[0], expected)

    def test_fixed_identity_modules_do_not_show_unknown_size_or_rating(self):
        cases = {
            "int_dockingcomputer_standard": ("STANDARD DOCKING COMPUTER", "1E"),
            "int_dockingcomputer_advanced": ("ADVANCED DOCKING COMPUTER", "1E"),
            "int_supercruiseassist": ("SUPERCRUISE ASSIST", "1E"),
            "int_guardianfsdbooster_size5": ("GUARDIAN FRAME SHIFT DRIVE BOOSTER", "5H"),
            "int_guardianpowerplant_size6": ("GUARDIAN HYBRID POWER PLANT", "6A"),
            "int_guardianpowerdistributor_size4": ("GUARDIAN HYBRID POWER DISTRIBUTOR", "4A"),
            "int_dronecontrol_resourcesiphon": ("HATCH BREAKER LIMPET CONTROLLER", "1I"),
            "int_dronecontrol_unkvesselresearch": ("RESEARCH LIMPET CONTROLLER", "1E"),
            "int_detailedsurfacescanner_tiny": ("DETAILED SURFACE SCANNER", "1I"),
        }
        for module_id, expected in cases.items():
            with self.subTest(module_id=module_id):
                self.assertEqual(module_purchase_identity(module_id), expected)

    def test_hardpoint_shows_name_and_mount_without_guessing_rating(self):
        self.assertEqual(
            module_purchase_identity("hpt_multicannon_turret_medium"),
            ("MULTI-CANNON · TURRETED", ""),
        )

    def test_new_mining_module_ids_have_purchase_names(self):
        self.assertEqual(
            module_purchase_identity("hpt_miningvolleyrepeater_fixed_large"),
            ("MINING VOLLEY REPEATER · FIXED", ""),
        )
        self.assertEqual(
            module_purchase_identity(
                "hpt_mining_subsurfdispmisle_fixed_medium"
            ),
            ("SUB-SURFACE DISPLACEMENT MISSILE · FIXED", ""),
        )

    def test_early_mining_mkii_alias_matches_frontier_miningv2_id(self):
        self.assertEqual(
            canonical_module_id(
                "int_multidronecontrol_mining_mkii_size5_class5"
            ),
            "int_multidronecontrol_miningv2_size5_class5",
        )

    def test_early_volley_alias_matches_observed_miningtoolv2_id(self):
        self.assertEqual(
            canonical_module_id("hpt_miningvolleyrepeater_fixed_large"),
            "hpt_miningtoolv2_fixed_large",
        )

    def test_utility_aliases_match_observed_frontier_ids(self):
        self.assertEqual(
            canonical_module_id("hpt_heatsinklauncher_tiny"),
            "hpt_heatsinklauncher_turret_tiny",
        )
        self.assertEqual(
            canonical_module_id("hpt_cloudscanner_size0_class5"),
            "hpt_mrascanner_size0_class5",
        )
        self.assertEqual(
            module_purchase_identity("hpt_mrascanner_size0_class5"),
            ("PULSE WAVE ANALYSER", "0A"),
        )

    def test_armour_grade_identity_is_hull_alias_independent(self):
        expected = "ship_armour_grade1"
        self.assertEqual(
            canonical_module_id("type11_prospector_armour_grade1"), expected
        )
        self.assertEqual(
            canonical_module_id("lakonminer_armour_grade1"), expected
        )
        self.assertEqual(
            canonical_module_id("cobramkiii_armour_grade1"), expected
        )
        self.assertNotEqual(
            canonical_module_id("cobramkiii_armour_unknown"), expected
        )

    def test_purchase_line_wraps_and_marks_unknown_rating(self):
        source = (
            Path(__file__).resolve().parents[1] / "Main.qml"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("modelData.slotBadge + \"? \"", source)
        self.assertIn("ToolTip.text: text", source)
        self.assertIn("maximumLineCount: 2", source)

    def test_named_optional_slots_override_positional_fallback_globally(self):
        rows = ship_slot_layout({
            "optional": [
                {"size": 5},
                {"size": 5, "name": "LimpetController01",
                 "restriction": "limpetController"},
                {"size": 4, "name": "Slot02_Size4"},
            ],
            "hardpoints": [], "utility": 0,
        }, [], [], {
            "Slot02_Size5": "int_multidronecontrol_miningv2_size5_class5",
            "Slot03_Size4": "int_detailedsurfacescanner_tiny",
        })

        optional = [row for row in rows if row["group"] == "OPTIONAL INTERNALS"]
        self.assertEqual(
            [row["slot"] for row in optional],
            ["Slot01_Size5", "LimpetController01", "Slot02_Size4"],
        )
        self.assertEqual(
            optional[1]["desiredModuleId"],
            "int_multidronecontrol_miningv2_size5_class5",
        )
        self.assertEqual(
            optional[2]["desiredModuleId"],
            "int_detailedsurfacescanner_tiny",
        )

    def test_named_hardpoints_override_size_ordinal_fallback_globally(self):
        rows = ship_slot_layout({
            "optional": [],
            "hardpoints": [
                {"size": 3, "name": "LargeMiningHardpoint1"},
                {"size": 2, "name": "MediumMiningHardpoint1"},
                {"size": 2, "name": "MediumHardpoint2"},
            ],
            "utility": 0,
        }, [], [], {
            "LargeHardpoint1": "hpt_miningvolleyrepeater_fixed_large",
            "MediumHardpoint1": "hpt_mining_subsurfdispmisle_fixed_medium",
        })
        hardpoints = [row for row in rows if row["group"] == "HARDPOINTS"]
        self.assertEqual(
            [row["slot"] for row in hardpoints],
            ["LargeMiningHardpoint1", "MediumMiningHardpoint1",
             "MediumHardpoint2"],
        )
        self.assertEqual(
            hardpoints[0]["desiredModuleId"],
            "hpt_miningvolleyrepeater_fixed_large",
        )

    def test_legacy_positional_import_maps_to_named_slot_by_position(self):
        slots = ship_slot_layout({
            "optional": [
                {"size": 5},
                {"size": 5, "name": "LimpetController01"},
                {"size": 4, "name": "Slot02_Size4"},
            ],
            "hardpoints": [], "utility": 0,
        }, [], [])

        self.assertEqual(
            _canonical_import_slot("Slot02_Size5", slots),
            ("LimpetController01", ""),
        )
        self.assertEqual(
            _canonical_import_slot("Slot03_Size4", slots),
            ("Slot02_Size4", ""),
        )

    def test_legacy_hardpoint_import_maps_to_named_slot_by_size_ordinal(self):
        slots = ship_slot_layout({
            "optional": [],
            "hardpoints": [
                {"size": 3, "name": "LargeMiningHardpoint1"},
                {"size": 2, "name": "MediumMiningHardpoint1"},
            ],
            "utility": 0,
        }, [], [])
        self.assertEqual(
            _canonical_import_slot("LargeHardpoint1", slots),
            ("LargeMiningHardpoint1", ""),
        )


if __name__ == "__main__":
    unittest.main()
