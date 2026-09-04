import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from ed_companion.phase14.controller import (
    COMMANDER_CARD_IDS,
    NAVIGATION_IDS,
    CockpitController,
)
from ed_companion.phase14.state import journal_dir, set_journal_dir


class _Signal:
    def emit(self, *_args):
        pass


class PersistenceSaveResultTests(unittest.TestCase):
    def _ui_controller(self, root):
        controller = CockpitController.__new__(CockpitController)
        controller.config_file = root / "phase14_graphics.json"
        controller._renderer_mode = "auto"
        controller._ui_scale = 1.0
        controller._theme = "navy"
        controller._interface_language = "en"
        controller._reduced_motion = False
        controller._commander_update_popups = True
        controller._enhanced_visuals = True
        controller._onboarding_complete = True
        controller._last_page = 0
        controller._debug_mode = False
        controller._journal_auto = True
        controller._background_mode = False
        controller._autostart_enabled = False
        controller._trader_preference = "confirmed"
        controller._commander_card_order = list(COMMANDER_CARD_IDS)
        controller._navigation_order = list(NAVIGATION_IDS)
        controller._activity = "Ready"
        controller.activityChanged = _Signal()
        controller.uiChanged = _Signal()
        return controller

    def test_journal_path_success_and_failure_preserve_disk_contract(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old_journal = root / "old-journal"
            new_journal = root / "new-journal"
            old_journal.mkdir()
            new_journal.mkdir()
            environment = {"LOCALAPPDATA": str(root), "EDOPS_JOURNAL_DIR": ""}
            with mock.patch.dict(os.environ, environment, clear=False):
                self.assertTrue(set_journal_dir(old_journal))
                controller = CockpitController.__new__(CockpitController)
                controller._last_journal_stamp = "old"
                controller._selected_ship = "Ship"
                controller._activity = "Ready"
                controller.refresh = mock.Mock()
                controller.activityChanged = _Signal()

                with mock.patch(
                    "ed_companion.phase14.state.atomic_write", return_value=False
                ):
                    controller.setJournalPath(str(new_journal))

                self.assertEqual(journal_dir(), old_journal)
                self.assertIn("could not be saved", controller._activity)
                controller.refresh.assert_not_called()

                controller.setJournalPath(str(new_journal))
                self.assertEqual(journal_dir(), new_journal)
                self.assertEqual(controller._activity, "Journal directory updated.")
                controller.refresh.assert_called_once_with()

    def test_inara_config_failure_rolls_back_and_success_persists(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            controller = CockpitController.__new__(CockpitController)
            controller.inara_config_file = root / "inara_config.json"
            controller._inara_config = {
                "api_key": "old-key", "commander_name": "Old Commander",
                "frontier_id": "F-OLD", "consent": True,
                "auto_sync": True, "request_times": [],
            }
            controller._sync_eddn_profile = lambda: True
            controller.connectionChanged = _Signal()
            controller._save_inara_config()
            old_bytes = controller.inara_config_file.read_bytes()

            with mock.patch(
                "ed_companion.phase14.controller.atomic_write", return_value=False
            ):
                controller.saveInaraConfig(
                    "new-key", "New Commander", True, True
                )

            self.assertEqual(controller._inara_config["api_key"], "old-key")
            self.assertEqual(controller.inara_config_file.read_bytes(), old_bytes)
            self.assertIn("could not be saved", controller._inara_status)

            controller.saveInaraConfig("new-key", "New Commander", True, True)
            saved = json.loads(
                controller.inara_config_file.read_text(encoding="utf-8")
            )
            self.assertEqual(saved["api_key"], "new-key")
            self.assertIn("Configuration saved", controller._inara_status)

    def test_navigation_and_commander_order_save_or_roll_back_together(self):
        with TemporaryDirectory() as directory:
            controller = self._ui_controller(Path(directory))
            navigation = list(reversed(NAVIGATION_IDS))
            cards = list(reversed(COMMANDER_CARD_IDS))

            controller.setNavigationOrder(navigation)
            controller.setCommanderCardOrder(cards)
            saved = json.loads(controller.config_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["navigation_order"], navigation)
            self.assertEqual(saved["commander_card_order"], cards)

            previous_navigation = list(controller._navigation_order)
            previous_cards = list(controller._commander_card_order)
            with mock.patch(
                "ed_companion.phase14.controller.atomic_write", return_value=False
            ):
                controller.setNavigationOrder(list(NAVIGATION_IDS))
                controller.setCommanderCardOrder(list(COMMANDER_CARD_IDS))

            self.assertEqual(controller._navigation_order, previous_navigation)
            self.assertEqual(controller._commander_card_order, previous_cards)
            self.assertIn("could not be saved", controller._activity)


if __name__ == "__main__":
    unittest.main()
