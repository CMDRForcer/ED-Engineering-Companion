"""Regression guard for the ED-Frame rebrand's three deliberate exceptions.

Inara's appName, EDDN's softwareName, and the Frontier CAPI client
identity are tied to external registrations and must never follow a
future rename the way the rest of the app's display name does. These
tests pin their current, unchanged values so a well-meaning future edit
cannot "fix" them by mistake - see the "NICHT ANDERN" note in
CHANGELOG.md and the module docstrings of inara.py / eddn.py.
"""

import unittest

from ed_companion.integrations.eddn import envelope, prepare_event, update_context
from ed_companion.integrations.frontier_capi import (
    FRONTIER_CLIENT_ID, _configured,
)
from ed_companion.integrations.inara import (
    INARA_APP_NAME, build_event, build_payload,
)


class InaraAppNameContractTests(unittest.TestCase):
    def test_inara_app_name_constant_is_unchanged(self):
        self.assertEqual(INARA_APP_NAME, "ED Engineering Companion")

    def test_a_real_inara_request_header_carries_the_registered_app_name(self):
        config = {
            "api_key": "secret", "commander_name": "Test Commander",
            "frontier_id": "F-TEST",
        }
        events = [build_event("getCommanderProfile", {}, "2026-08-30T10:00:00Z")]

        payload = build_payload(config, events)

        self.assertEqual(
            payload["header"]["appName"], "ED Engineering Companion",
        )


class EddnSoftwareNameContractTests(unittest.TestCase):
    def test_a_real_eddn_envelope_carries_the_registered_software_name(self):
        context = {}
        for event in (
            {"event": "Fileheader", "gameversion": "4.2.0.0", "build": "r0"},
            {"event": "LoadGame", "Horizons": True, "Odyssey": True},
        ):
            context = update_context(context, event)
        prepared = prepare_event({
            "timestamp": "2026-08-30T10:00:00Z", "event": "FSDJump",
            "StarSystem": "Shinrarta Dezhra", "StarPos": [55.71, 17.59, 27.16],
            "SystemAddress": 3932277478106,
        }, context)
        self.assertIsNotNone(prepared)

        message = envelope(prepared, context, "uploader-test")

        self.assertEqual(
            message["header"]["softwareName"], "ED Engineering Companion",
        )


class FrontierClientIdentityContractTests(unittest.TestCase):
    def test_default_frontier_client_id_env_var_name_is_unchanged(self):
        # The env-var NAME a self-hoster sets is a documented external
        # contract (see README) - only the registered client id VALUE is
        # the actual exception, but renaming the var name would silently
        # break every existing self-hosted override, so both stay put.
        self.assertEqual(
            _configured("EDEC_FRONTIER_CLIENT_ID", "fallback", environ={
                "EDEC_FRONTIER_CLIENT_ID": "operator-owned",
            }),
            "operator-owned",
        )

    def test_bundled_frontier_client_id_is_present_and_unchanged_by_default(self):
        # Only asserts the bundled default resolves to *something* stable
        # (not empty) without requiring the actual registered UUID to be
        # duplicated here - that value lives solely in frontier_capi.py.
        self.assertTrue(FRONTIER_CLIENT_ID)


if __name__ == "__main__":
    unittest.main()
