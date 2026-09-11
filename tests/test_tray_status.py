"""Regression cover for the system tray's persistent Status line.

It used to display controller.activity - a one-shot toast for the last
user action (route copied, craft dismissed, Windows autostart toggled) -
so the tray froze on whatever unrelated toast last fired instead of
showing anything about the Journal watcher it claims to report on.
"""

import unittest

from phase14_main import tray_status_text


class TrayStatusTextTests(unittest.TestCase):
    def test_shows_journal_health_not_an_unrelated_toast(self):
        text = tray_status_text(
            "WINDOW OPEN", {"status": "LIVE", "lastEvent": "FSDJump"}
        )
        self.assertEqual(text, "WINDOW OPEN · JOURNAL LIVE")

    def test_missing_or_malformed_journal_health_falls_back_safely(self):
        self.assertEqual(
            tray_status_text("RUNNING IN BACKGROUND", None),
            "RUNNING IN BACKGROUND · JOURNAL UNKNOWN",
        )
        self.assertEqual(
            tray_status_text("TRAY UNAVAILABLE", {}),
            "TRAY UNAVAILABLE · JOURNAL UNKNOWN",
        )
        self.assertEqual(
            tray_status_text("WINDOW OPEN", "not a dict"),
            "WINDOW OPEN · JOURNAL UNKNOWN",
        )


if __name__ == "__main__":
    unittest.main()
