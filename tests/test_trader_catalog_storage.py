import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

from ed_companion.phase14.state import (
    build_state,
    clear_journal_event_cache,
    load_user_trader_catalog,
    resolve_profile_context,
    user_trader_catalog_path,
)


class TraderCatalogStorageTests(unittest.TestCase):
    def _environment(self, root, identity):
        journal = Path(root) / "journal"
        journal.mkdir(exist_ok=True)
        return {
            "LOCALAPPDATA": str(root),
            "EDOPS_JOURNAL_DIR": str(journal),
            "EDOPS_PROFILE_FID": identity,
        }, journal

    @staticmethod
    def _catalog(station, market_id):
        return {
            "fetched_at": "2026-08-30T10:00:00Z",
            "stations": [{
                "station": station,
                "system": "Synthetic System",
                "market_id": market_id,
                "category": "Raw",
                "distance_ly": 1.0,
                "source": "test",
            }],
        }

    def test_build_state_reads_synthetic_station_from_profile_catalog(self):
        with TemporaryDirectory() as directory:
            environment, journal = self._environment(directory, "F-ALPHA")
            (journal / "Journal.01.log").write_text(
                json.dumps({
                    "timestamp": "2026-08-30T09:59:00Z",
                    "event": "LoadGame",
                    "Commander": "Alpha",
                    "FID": "F-ALPHA",
                }) + "\n" + json.dumps({
                    "timestamp": "2026-08-30T10:00:00Z",
                    "event": "Location",
                    "StarSystem": "Synthetic System",
                    "StarPos": [0.0, 0.0, 0.0],
                }) + "\n",
                encoding="utf-8",
            )
            seen = []

            def capture(_category, _position, stations, _preference):
                seen.extend(stations)
                return None

            with mock.patch.dict(os.environ, environment, clear=False):
                clear_journal_event_cache()
                context = resolve_profile_context()
                user_trader_catalog_path(context).write_text(
                    json.dumps(self._catalog("Canonical Profile Trader", 990001)),
                    encoding="utf-8",
                )
                with mock.patch(
                    "ed_companion.phase14.state.find_nearest_catalog_trader",
                    side_effect=capture,
                ), mock.patch(
                    "ed_companion.phase14.state.update_trader_type_evidence",
                    return_value=False,
                ), mock.patch(
                    "ed_companion.phase14.state.resolve_trader_type",
                    return_value=SimpleNamespace(
                        trader_type="", confidence="", source=""
                    ),
                ):
                    build_state(Path(__file__).resolve().parents[1])

            self.assertTrue(any(
                row.get("station") == "Canonical Profile Trader" for row in seen
            ))

    def test_legacy_global_catalog_is_claimed_once_by_the_active_profile(self):
        with TemporaryDirectory() as directory:
            environment, _journal = self._environment(directory, "F-ALPHA")
            root = Path(directory) / "EDEngineeringCompanion"
            root.mkdir()
            legacy = root / "material_trader_catalog_user.json"
            legacy.write_text(
                json.dumps(self._catalog("Legacy Trader", 990002)),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                clear_journal_event_cache()
                alpha = resolve_profile_context()
                migrated = load_user_trader_catalog(alpha)
                self.assertEqual(migrated["stations"][0]["station"], "Legacy Trader")
                self.assertTrue(user_trader_catalog_path(alpha).is_file())

                os.environ["EDOPS_PROFILE_FID"] = "F-BRAVO"
                bravo = resolve_profile_context()
                self.assertEqual(load_user_trader_catalog(bravo), {})
                self.assertFalse(user_trader_catalog_path(bravo).exists())

    def test_profile_catalogs_remain_isolated(self):
        with TemporaryDirectory() as directory:
            environment, _journal = self._environment(directory, "F-ALPHA")
            with mock.patch.dict(os.environ, environment, clear=False):
                clear_journal_event_cache()
                alpha = resolve_profile_context()
                user_trader_catalog_path(alpha).write_text(
                    json.dumps(self._catalog("Alpha Trader", 990003)),
                    encoding="utf-8",
                )
                os.environ["EDOPS_PROFILE_FID"] = "F-BRAVO"
                bravo = resolve_profile_context()
                user_trader_catalog_path(bravo).write_text(
                    json.dumps(self._catalog("Bravo Trader", 990004)),
                    encoding="utf-8",
                )

                self.assertEqual(
                    load_user_trader_catalog(alpha)["stations"][0]["station"],
                    "Alpha Trader",
                )
                self.assertEqual(
                    load_user_trader_catalog(bravo)["stations"][0]["station"],
                    "Bravo Trader",
                )
                self.assertNotEqual(
                    user_trader_catalog_path(alpha),
                    user_trader_catalog_path(bravo),
                )


if __name__ == "__main__":
    unittest.main()
