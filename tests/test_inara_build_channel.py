import unittest

from ed_companion import default_build_channel
from ed_companion.integrations.inara import build_event, build_payload


class DefaultBuildChannelTests(unittest.TestCase):
    def test_explicit_env_value_always_wins(self):
        self.assertEqual(
            default_build_channel(" Preview ", frozen=True), "preview"
        )

    def test_frozen_build_without_env_is_release(self):
        self.assertEqual(default_build_channel(None, frozen=True), "release")

    def test_bare_source_checkout_without_env_is_development(self):
        self.assertEqual(default_build_channel(None, frozen=False), "development")
        self.assertEqual(default_build_channel("", frozen=False), "development")


class InaraBuildChannelTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "api_key": "secret",
            "commander_name": "Test Commander",
            "frontier_id": "F-TEST",
        }
        self.events = [build_event(
            "getCommanderProfile", {}, "2026-08-30T10:00:00Z"
        )]

    def test_release_build_marks_inara_payload_not_being_developed(self):
        payload = build_payload(
            self.config, self.events, app_version="21.198",
            build_channel="release",
        )

        self.assertIs(payload["header"]["isBeingDeveloped"], False)
        self.assertEqual(payload["header"]["appVersion"], "21.198")
        self.assertEqual(payload["events"], self.events)

    def test_explicit_dev_and_preview_builds_mark_inara_payload_developed(self):
        for channel in ("dev", "preview"):
            with self.subTest(channel=channel):
                payload = build_payload(
                    self.config, self.events, build_channel=channel
                )
                self.assertIs(payload["header"]["isBeingDeveloped"], True)


if __name__ == "__main__":
    unittest.main()
