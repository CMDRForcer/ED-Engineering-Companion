import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from ed_companion.integrations.frontier_capi import FrontierTokens
from ed_companion.integrations.frontier_credentials import FrontierCredentialStore
from ed_companion.persistence import migrate_app_dir_if_needed
from ed_companion.phase14.state import (
    APP_DATA_DIR_NAME,
    LEGACY_APP_DATA_DIR_NAME,
    app_data_dir,
    read_json,
)


def _fake_protect(value):
    """Stand-in for real DPAPI - reversible, not Windows-only, not secure.

    These tests assert the migration copies bytes as-is; they are not a
    test of DPAPI itself (see frontier_credentials.py for the real thing).
    """
    return bytes(reversed(value))


def _fake_unprotect(value):
    return bytes(reversed(value))


class MigrateAppDirIfNeededTests(unittest.TestCase):
    """Unit tests for the copy-not-move helper itself, independent of the
    app's own directory names."""

    def test_copies_the_whole_tree_when_only_the_old_directory_exists(self):
        with TemporaryDirectory() as root:
            old = Path(root) / "Old"
            new = Path(root) / "New"
            (old / "sub").mkdir(parents=True)
            (old / "sub" / "file.txt").write_text("hello", encoding="utf-8")

            logged = []
            result = migrate_app_dir_if_needed(old, new, log=logged.append)

            self.assertTrue(result)
            self.assertEqual(
                (new / "sub" / "file.txt").read_text(encoding="utf-8"), "hello",
            )
            self.assertTrue(old.is_dir())  # never removed - safety net
            self.assertEqual(len(logged), 1)
            self.assertIn(str(old), logged[0])
            self.assertIn(str(new), logged[0])

    def test_new_directory_already_present_wins_with_no_recopy(self):
        with TemporaryDirectory() as root:
            old = Path(root) / "Old"
            new = Path(root) / "New"
            old.mkdir()
            (old / "file.txt").write_text("old value", encoding="utf-8")
            new.mkdir()
            (new / "file.txt").write_text("new value", encoding="utf-8")

            logged = []
            result = migrate_app_dir_if_needed(old, new, log=logged.append)

            self.assertFalse(result)
            self.assertEqual(
                (new / "file.txt").read_text(encoding="utf-8"), "new value",
            )
            self.assertEqual(logged, [])

    def test_neither_directory_exists_is_a_silent_first_start(self):
        with TemporaryDirectory() as root:
            old = Path(root) / "Old"
            new = Path(root) / "New"

            logged = []
            result = migrate_app_dir_if_needed(old, new, log=logged.append)

            self.assertFalse(result)
            self.assertFalse(new.exists())
            self.assertEqual(logged, [])

    def test_old_path_that_is_a_file_not_a_directory_is_ignored(self):
        with TemporaryDirectory() as root:
            old = Path(root) / "Old"
            new = Path(root) / "New"
            old.write_text("not a directory", encoding="utf-8")

            result = migrate_app_dir_if_needed(old, new)

            self.assertFalse(result)
            self.assertFalse(new.exists())


class AppDataDirMigrationRegressionTests(unittest.TestCase):
    """Whole-app regression for the ED Engineering Companion -> ED-Frame
    rename: existing Commander data must survive untouched, and a stored
    Frontier OAuth token must stay usable without forcing a re-login."""

    def test_wishlist_and_materials_and_oauth_token_survive_migration(self):
        with TemporaryDirectory() as root, mock.patch.dict(
            os.environ, {"LOCALAPPDATA": str(root)}, clear=False,
        ):
            legacy_root = Path(root) / LEGACY_APP_DATA_DIR_NAME
            profile_dir = legacy_root / "profile-test"
            profile_dir.mkdir(parents=True)

            # Existing wishlist data under the pre-rebrand folder name -
            # written directly (not via write_ship_tasks(), which itself
            # calls read_json() -> app_data_dir() and would trigger the
            # migration mid-fixture-setup, before this file exists yet).
            # A real pre-upgrade install would have this fully written
            # already, long before the new app ever touches app_data_dir().
            wishlist_path = profile_dir / "ship_blueprints.json"
            wishlist_path.write_text(
                json.dumps({"Alpha": [[{"Name": "New Plan"}]]}), encoding="utf-8",
            )

            # Existing materials inventory alongside it.
            materials_path = profile_dir / "materials_inventory.json"
            materials_path.write_text(
                json.dumps({"Raw": {"carbon": 150}}), encoding="utf-8",
            )

            # A config value that must travel 1:1, never rewritten - the same
            # rule the real Inara/EDDN/Frontier appName/client fields fall
            # under (this file just stands in for that shape).
            frontier_config_path = profile_dir / "frontier_config.json"
            frontier_config_path.write_text(
                json.dumps({"consent": True, "client_id": "real-registered-id"}),
                encoding="utf-8",
            )

            # A Frontier OAuth token, "DPAPI"-protected with a fake
            # reversible transform standing in for the real Windows API.
            tokens = FrontierTokens(
                access_token="real-access-token",
                refresh_token="real-refresh-token",
                token_type="Bearer",
                expires_at=9999999999.0,
            )
            old_store = FrontierCredentialStore(
                profile_dir / "frontier_credentials.dat",
                protect=_fake_protect, unprotect=_fake_unprotect,
            )
            old_store.save(tokens)

            # The rename in action: the very first call anywhere in the app
            # makes to app_data_dir() after upgrading performs the migration.
            new_root = app_data_dir()
            self.assertEqual(new_root, Path(root) / APP_DATA_DIR_NAME)
            new_profile_dir = new_root / "profile-test"

            self.assertEqual(
                read_json(new_profile_dir / "ship_blueprints.json", {}),
                read_json(wishlist_path, {}),
            )
            self.assertEqual(
                json.loads(
                    (new_profile_dir / "materials_inventory.json")
                    .read_text(encoding="utf-8")
                ),
                {"Raw": {"carbon": 150}},
            )
            self.assertEqual(
                json.loads(
                    (new_profile_dir / "frontier_config.json")
                    .read_text(encoding="utf-8")
                ),
                {"consent": True, "client_id": "real-registered-id"},
            )

            new_store = FrontierCredentialStore(
                new_profile_dir / "frontier_credentials.dat",
                protect=_fake_protect, unprotect=_fake_unprotect,
            )
            migrated_tokens = new_store.load()
            self.assertIsNotNone(migrated_tokens)
            self.assertEqual(migrated_tokens.access_token, "real-access-token")
            self.assertEqual(migrated_tokens.refresh_token, "real-refresh-token")

            # Copy-not-move: the old directory is left in place, untouched.
            self.assertTrue(profile_dir.is_dir())
            self.assertTrue((profile_dir / "frontier_credentials.dat").is_file())

    def test_a_fresh_install_with_neither_directory_is_untouched(self):
        with TemporaryDirectory() as root, mock.patch.dict(
            os.environ, {"LOCALAPPDATA": str(root)}, clear=False,
        ):
            root_dir = app_data_dir()

            self.assertEqual(root_dir, Path(root) / APP_DATA_DIR_NAME)
            self.assertTrue(root_dir.is_dir())
            self.assertFalse((Path(root) / LEGACY_APP_DATA_DIR_NAME).exists())

    def test_an_already_migrated_install_is_never_recopied(self):
        with TemporaryDirectory() as root, mock.patch.dict(
            os.environ, {"LOCALAPPDATA": str(root)}, clear=False,
        ):
            legacy_root = Path(root) / LEGACY_APP_DATA_DIR_NAME
            legacy_root.mkdir()
            (legacy_root / "stale.json").write_text("{}", encoding="utf-8")

            new_root = Path(root) / APP_DATA_DIR_NAME
            new_root.mkdir()
            (new_root / "current.json").write_text(
                '{"already": "here"}', encoding="utf-8",
            )

            resolved = app_data_dir()

            self.assertEqual(resolved, new_root)
            self.assertFalse((new_root / "stale.json").exists())
            self.assertEqual(
                json.loads((new_root / "current.json").read_text(encoding="utf-8")),
                {"already": "here"},
            )


if __name__ == "__main__":
    unittest.main()
