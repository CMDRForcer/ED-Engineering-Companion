import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from ed_companion.phase14.controller import CockpitController
from ed_companion.phase14.state import (
    ProfileContext,
    clear_journal_event_cache,
    profiled_journal_events,
    resolve_profile_context,
    runtime_data_dir,
)


class _Signal:
    def __init__(self, callback=None):
        self.callback = callback

    def emit(self, *_args):
        if self.callback:
            self.callback(*_args)


class ProfileContextTests(unittest.TestCase):
    def _context(self, root, identity):
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        directory = Path(root) / "EDEngineeringCompanion" / f"profile-{key}"
        directory.mkdir(parents=True, exist_ok=True)
        return ProfileContext(identity, key, directory, str(Path(root) / "journal"))

    def _controller(self, context, package_root):
        controller = CockpitController.__new__(CockpitController)
        controller.package_root = Path(package_root)
        controller._bind_profile_paths(context)
        controller._profile_generation = 1
        controller._eddn_profile_identity = context.identity
        controller._eddn_profile_key = context.key
        controller._eddn_journal_root = context.journal_root
        controller._eddn_busy = False
        controller._eddn_queue = []
        controller._eddn_config = controller._load_eddn_config()
        controller._journal_offsets = {}
        controller._station_fingerprints = {}
        controller._navroute_fingerprint = ""
        controller._eddn_baseline_established = False
        controller._eddn_context = {}
        controller._eddn_profile_paths_signature = None
        controller._eddn_profile_paths_cache = []
        controller._station_rejections = {}
        controller._navroute_rejections = {}
        controller._logbook_notes = {}
        controller._inara_config = {}
        controller._inara_cache = {}
        controller._inara_receipts = []
        controller._inara_busy = False
        controller._active_inara_request = None
        controller._inara_status = ""
        controller._inara_pending_since = 0.0
        controller._inara_retry_not_before = 0.0
        controller._inara_failure_count = 0
        controller._inara_material_fingerprint = ""
        controller._inara_request_times = []
        controller._inara_request_wall_times = []
        controller._inara_last_request_at = 0.0
        controller._inara_pending_events = []
        controller._inara_pending_fingerprints = []
        controller._inara_inflight_fingerprints = []
        controller._hge_sightings = []
        controller._derived_cache = {}
        controller._state = {"materials": []}
        controller._eddn_revision = 0
        controller.connectionChanged = _Signal()
        controller.hgeChanged = _Signal()
        controller._eddn_profile_journal_paths = lambda: []
        controller._rebuild_eddn_context = lambda: {}
        controller._ensure_eddn_listener = lambda: None
        return controller

    def _configure_inara(self, controller, key="alpha-secret"):
        controller._inara_config = {
            "api_key": key,
            "commander_name": "Alpha",
            "frontier_id": "F-ALPHA",
            "consent": True,
            "auto_sync": True,
            "request_times": [],
        }
        controller._save_inara_config()

    def test_explicit_profile_fid_uses_its_own_context_when_present_or_missing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / "journal"
            journal.mkdir()
            (journal / "Journal.01.log").write_text(json.dumps({
                "timestamp": "2026-08-30T10:00:00Z", "event": "LoadGame",
                "Commander": "Alpha", "FID": "F-ALPHA",
            }) + "\n", encoding="utf-8")
            environment = {
                "LOCALAPPDATA": str(root),
                "EDOPS_JOURNAL_DIR": str(journal),
                "EDOPS_PROFILE_FID": "F-ALPHA",
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                clear_journal_event_cache()
                present = resolve_profile_context()
                self.assertEqual(present.identity, "F-ALPHA")
                self.assertEqual(runtime_data_dir(present), present.directory)
                self.assertTrue(profiled_journal_events())

                os.environ["EDOPS_PROFILE_FID"] = "F-NOT-IN-JOURNAL"
                missing = resolve_profile_context()
                self.assertEqual(missing.identity, "F-NOT-IN-JOURNAL")
                self.assertEqual(
                    missing.key,
                    hashlib.sha256(b"F-NOT-IN-JOURNAL").hexdigest()[:16],
                )
                self.assertEqual(missing.directory.name, f"profile-{missing.key}")
                self.assertEqual(profiled_journal_events(), [])

            with self.assertRaises(TypeError):
                runtime_data_dir(None)

    def test_eddn_switches_persist_and_reload_all_three_flags_for_same_fid(self):
        with TemporaryDirectory() as directory:
            context = self._context(directory, "F-ALPHA")
            first = self._controller(context, Path(__file__).resolve().parents[1])
            first.saveEddnConfig(True, True, True)

            second = self._controller(context, Path(__file__).resolve().parents[1])
            second._eddn_config = second._load_eddn_config()

            self.assertTrue(second._eddn_config["consent"])
            self.assertTrue(second._eddn_config["upload_enabled"])
            self.assertTrue(second._eddn_config["listener_enabled"])
            self.assertEqual(second.eddn_config_file.parent, context.directory)
            self.assertEqual(second._eddn_profile_key, context.key)

    def test_profile_switch_a_to_b_to_a_restores_paths_flags_and_queue_key(self):
        with TemporaryDirectory() as directory:
            package_root = Path(__file__).resolve().parents[1]
            alpha = self._context(directory, "F-ALPHA")
            bravo = self._context(directory, "F-BRAVO")
            controller = self._controller(alpha, package_root)
            controller.saveEddnConfig(True, True, True)

            with mock.patch(
                "ed_companion.phase14.controller.resolve_profile_context",
                return_value=bravo,
            ):
                self.assertTrue(controller._sync_eddn_profile())
            self.assertEqual(controller.profile_context, bravo)
            self.assertEqual(controller.config_dir, bravo.directory)
            self.assertFalse(controller._eddn_config["consent"])

            controller.saveEddnConfig(True, True, False)
            prepared = {
                "schema": "journal/1",
                "message": {
                    "timestamp": "2026-08-30T10:00:00Z", "event": "FSDJump",
                    "StarSystem": "Bravo", "StarPos": [1, 2, 3],
                    "SystemAddress": 42,
                },
            }
            controller._enqueue_eddn(prepared)
            self.assertEqual(controller._eddn_queue[-1]["profile_key"], bravo.key)
            self.assertEqual(controller.eddn_queue_file.parent.name, f"profile-{bravo.key}")

            with mock.patch(
                "ed_companion.phase14.controller.resolve_profile_context",
                return_value=alpha,
            ):
                self.assertTrue(controller._sync_eddn_profile())
            self.assertEqual(controller.profile_context, alpha)
            self.assertEqual(controller._eddn_profile_key, alpha.key)
            self.assertTrue(controller._eddn_config["consent"])
            self.assertTrue(controller._eddn_config["upload_enabled"])
            self.assertTrue(controller._eddn_config["listener_enabled"])
            for path in (
                controller.config_file, controller.inara_config_file,
                controller.inara_receipts_file, controller.inara_journal_cache_file,
                controller.eddn_config_file, controller.eddn_queue_file,
                controller.eddn_cursor_file, controller.hge_cache_file,
                controller.trader_catalog_file, controller.tech_broker_catalog_file,
            ):
                self.assertEqual(path.parent, alpha.directory)

    def test_inara_profile_b_without_configuration_never_uses_profile_a(self):
        with TemporaryDirectory() as directory:
            package_root = Path(__file__).resolve().parents[1]
            alpha = self._context(directory, "F-ALPHA")
            bravo = self._context(directory, "F-BRAVO")
            controller = self._controller(alpha, package_root)
            self._configure_inara(controller)
            alpha_config = controller.inara_config_file.read_bytes()
            started = []
            controller._start_network_worker = (
                lambda _target, _name: started.append(True) or True
            )

            with mock.patch(
                "ed_companion.phase14.controller.resolve_profile_context",
                return_value=bravo,
            ):
                self.assertTrue(controller._sync_eddn_profile())
                controller._inara_pending_events = [{"eventName": "setCommanderTravelLocation"}]
                controller._inara_pending_fingerprints = ["bravo-event"]
                self.assertFalse(controller._start_inara("journal"))

            self.assertEqual(alpha_config, alpha.directory.joinpath(
                "inara_config.json"
            ).read_bytes())
            self.assertEqual(started, [])
            self.assertFalse((bravo.directory / "inara_receipts.json").exists())

    def test_inara_completion_is_discarded_after_profile_switch(self):
        with TemporaryDirectory() as directory:
            package_root = Path(__file__).resolve().parents[1]
            alpha = self._context(directory, "F-ALPHA")
            bravo = self._context(directory, "F-BRAVO")
            controller = self._controller(alpha, package_root)
            self._configure_inara(controller)
            workers = []
            controller._start_network_worker = (
                lambda target, _name: workers.append(target) or True
            )
            controller.inaraFinished = _Signal(controller._finish_inara)

            with mock.patch(
                "ed_companion.phase14.controller.resolve_profile_context",
                return_value=alpha,
            ):
                self.assertTrue(controller._start_inara("test"))
            request = dict(controller._active_inara_request)
            self.assertEqual(request["profile_key"], alpha.key)
            self.assertEqual(request["path_generation"], 1)
            self.assertEqual(request["directory"], str(alpha.directory.resolve()))
            self.assertTrue(request["request_id"])

            with mock.patch(
                "ed_companion.phase14.controller.resolve_profile_context",
                return_value=bravo,
            ):
                self.assertTrue(controller._sync_eddn_profile())

            with mock.patch(
                "ed_companion.phase14.controller.send_events",
                return_value=({
                    "timestamp": "2026-08-30T10:00:00Z",
                    "httpStatus": 200,
                    "elapsedMs": 1,
                }, {}),
            ):
                workers[0]()

            self.assertEqual(controller.profile_context, bravo)
            self.assertEqual(controller._inara_receipts, [])
            self.assertFalse((bravo.directory / "inara_receipts.json").exists())
            self.assertFalse((bravo.directory / "inara_journal_cache.json").exists())
            self.assertEqual(len(workers), 1)

    def test_new_controller_loads_inara_configuration_only_for_its_profile(self):
        with TemporaryDirectory() as directory:
            package_root = Path(__file__).resolve().parents[1]
            alpha = self._context(directory, "F-ALPHA")
            bravo = self._context(directory, "F-BRAVO")
            first = self._controller(alpha, package_root)
            self._configure_inara(first)

            alpha_restart = self._controller(alpha, package_root)
            bravo_restart = self._controller(bravo, package_root)
            alpha_config = alpha_restart._load_inara_config()
            bravo_config = bravo_restart._load_inara_config()

            self.assertEqual(alpha_config["api_key"], "alpha-secret")
            self.assertEqual(alpha_config["frontier_id"], "F-ALPHA")
            self.assertEqual(bravo_config["api_key"], "")
            self.assertFalse(bravo_config["consent"])


if __name__ == "__main__":
    unittest.main()
