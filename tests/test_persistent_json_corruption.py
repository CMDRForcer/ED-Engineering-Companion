import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from ed_companion.persistence import (
    atomic_write,
    clear_persistence_errors,
    persistence_issues,
)
from ed_companion.phase14.controller import CockpitController
from ed_companion.phase14.state import read_json, write_ship_tasks


class PersistentJsonCorruptionTests(unittest.TestCase):
    def setUp(self):
        clear_persistence_errors()

    def tearDown(self):
        clear_persistence_errors()

    def _profile(self, root):
        profile = Path(root) / "EDEngineeringCompanion" / "profile-test"
        profile.mkdir(parents=True)
        return profile

    def test_corrupt_plan_survives_refresh_and_save_with_recovery_copy(self):
        with TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"LOCALAPPDATA": directory}, clear=False,
        ):
            profile = self._profile(directory)
            path = profile / "ship_blueprints.json"
            original = b'{"Alpha": [[{"Name": "unfinished"}'
            path.write_bytes(original)

            self.assertEqual(read_json(path, {}), {})
            write_ship_tasks(path, "Alpha", [[{"Name": "New Plan"}]])

            self.assertEqual(path.read_bytes(), original)
            backups = list(profile.glob("ship_blueprints.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            issues = persistence_issues(profile)
            self.assertEqual(len(issues), 1)
            self.assertIn("ship_blueprints.json", issues[0])
            self.assertNotIn("unfinished", issues[0])

    def test_corrupt_controller_queue_is_backed_up_and_not_overwritten(self):
        with TemporaryDirectory() as directory:
            profile = Path(directory)
            path = profile / "community_upload_queue.json"
            original = b'{"partial":'
            path.write_bytes(original)

            loaded = CockpitController._read_local_json(path, [])
            self.assertEqual(loaded, [])
            self.assertFalse(atomic_write(path, json.dumps([])))

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(
                len(list(profile.glob("community_upload_queue.json.corrupt-*"))),
                1,
            )

    def test_wrong_json_root_type_is_protected_like_parse_corruption(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inara_receipts.json"
            path.write_text("{}", encoding="utf-8")

            self.assertEqual(CockpitController._read_local_json(path, []), [])
            self.assertFalse(atomic_write(path, "[]"))
            self.assertEqual(path.read_text(encoding="utf-8"), "{}")
            self.assertEqual(len(list(Path(directory).glob(
                "inara_receipts.json.corrupt-*"
            ))), 1)

    def test_missing_json_uses_default_and_may_be_created(self):
        with TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"LOCALAPPDATA": directory}, clear=False,
        ):
            profile = self._profile(directory)
            path = profile / "missing.json"

            self.assertEqual(read_json(path, {"default": True}), {"default": True})
            self.assertTrue(atomic_write(path, json.dumps({"created": True})))
            self.assertEqual(json.loads(path.read_text()), {"created": True})

    def test_valid_json_loads_and_saves_unchanged_contract(self):
        with TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"LOCALAPPDATA": directory}, clear=False,
        ):
            profile = self._profile(directory)
            path = profile / "valid.json"
            path.write_text('{"value": 1}', encoding="utf-8")

            self.assertEqual(read_json(path, {}), {"value": 1})
            self.assertTrue(atomic_write(path, json.dumps({"value": 2})))
            self.assertEqual(read_json(path, {}), {"value": 2})
            self.assertEqual(persistence_issues(profile), [])


if __name__ == "__main__":
    unittest.main()
