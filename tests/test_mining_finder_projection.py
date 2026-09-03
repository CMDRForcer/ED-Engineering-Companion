import json
from pathlib import Path
import unittest

from ed_companion.navigation.mining_finder import (
    project_local_mining_evidence,
    project_spansh_mining_candidates,
)


FIXTURE = json.loads(Path(__file__).with_name("fixtures").joinpath(
    "mining_finder_observations.json"
).read_text(encoding="utf-8"))


class MiningFinderProjectionTests(unittest.TestCase):
    def test_local_scan_and_saa_form_one_confirmed_ring_candidate(self):
        result = project_local_mining_evidence(FIXTURE["local_events"])

        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["system"], "Synthetic Mining System")
        self.assertEqual(candidate["ring"], "Synthetic Mining System 1 A Ring")
        self.assertEqual(candidate["evidence"], "LOCAL_CONFIRMED")
        self.assertEqual(candidate["hotspots"], [
            {"commodity": "painite", "count": 1},
            {"commodity": "platinum", "count": 2},
        ])
        serialized = json.dumps(candidate)
        self.assertNotIn("must-not-project", serialized)
        self.assertNotIn("private display", serialized)

    def test_prospector_sample_is_not_falsely_bound_to_a_ring(self):
        result = project_local_mining_evidence(FIXTURE["local_events"])

        self.assertEqual(len(result["prospectorSamples"]), 1)
        sample = result["prospectorSamples"][0]
        self.assertFalse(sample["boundToRing"])
        self.assertEqual(sample["motherlode"], "alexandrite")
        self.assertEqual(sample["materials"], [
            {"commodity": "platinum", "proportion": 22.4},
        ])

    def test_spansh_dump_projects_catalog_candidate_and_source_timestamp(self):
        candidates = project_spansh_mining_candidates(
            FIXTURE["spansh_dump"], origin=[10.0, 20.0, 30.0]
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["evidence"], "CATALOG_CANDIDATE")
        self.assertEqual(candidate["distanceLy"], 5.0)
        self.assertEqual(candidate["ringType"], "Metallic")
        self.assertEqual(candidate["reserveLevel"], "MajorResources")
        self.assertEqual(candidate["observedAt"], "2026-09-02T13:00:00Z")
        self.assertEqual(candidate["hotspots"], [
            {"commodity": "platinum", "count": 1},
        ])

    def test_invalid_or_partial_spansh_payload_stays_empty_or_unknown(self):
        self.assertEqual(project_spansh_mining_candidates({}), [])
        candidates = project_spansh_mining_candidates({
            "system": {
                "name": "Partial", "bodies": [{
                    "name": "Partial 1", "rings": [{"name": "A Ring"}],
                }],
            },
        })
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["hotspots"], [])
        self.assertEqual(candidates[0]["reserveLevel"], "")
        self.assertIsNone(candidates[0]["distanceLy"])


if __name__ == "__main__":
    unittest.main()
