"""Regression cover for the Spansh request pacing in trader_search.py.

fetch_nearest_traders had no pause between the (up to three) Spansh
requests it makes, while every sibling multi-request loop in this module
does pause; fetch_trader_catalog_updates paused twice per iteration
instead of once. Both were accidental drift from the shared
_pause_between_requests(index, total) pattern.
"""

import unittest
from unittest.mock import patch

from ed_companion.navigation import trader_search as ts


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _fake_post(*_args, **_kwargs):
    return _FakeResponse({"results": []})


class TraderSearchPacingTests(unittest.TestCase):
    def test_fetch_nearest_traders_pauses_between_requests(self):
        categories = ["Raw", "Manufactured", "Encoded"]
        with patch.object(ts.time, "sleep") as sleep_mock:
            ts.fetch_nearest_traders(categories, (1.0, 2.0, 3.0), _fake_post)
        # One fewer pause than requests: never wait after the last one.
        self.assertEqual(sleep_mock.call_count, len(categories) - 1)

    def test_fetch_trader_catalog_updates_pauses_exactly_once_per_gap(self):
        categories = ["Raw", "Manufactured", "Encoded"]
        with patch.object(ts.time, "sleep") as sleep_mock:
            ts.fetch_trader_catalog_updates(
                categories, (1.0, 2.0, 3.0), _fake_post,
            )
        self.assertEqual(sleep_mock.call_count, len(categories) - 1)


if __name__ == "__main__":
    unittest.main()
