import unittest

from ed_companion.phase14.controller import (
    NAVIGATION_IDS,
    initial_navigation_order,
)


class NavigationOrderTests(unittest.TestCase):
    def test_default_navigation_matches_product_order(self):
        self.assertEqual(
            NAVIGATION_IDS,
            (
                "operations", "engineering", "wishlist", "engineers", "materials",
                "mining-finder", "state-finds", "powerplay", "cmdr", "logbook",
                "settings",
            ),
        )

    def test_previous_default_is_migrated(self):
        previous_default = [
            "operations", "engineering", "wishlist", "engineers", "materials",
            "state-finds", "mining-finder", "cmdr", "logbook", "settings",
            "powerplay",
        ]
        self.assertEqual(initial_navigation_order(previous_default), list(NAVIGATION_IDS))

    def test_custom_navigation_order_is_preserved(self):
        custom = list(reversed(NAVIGATION_IDS))
        self.assertEqual(initial_navigation_order(custom), custom)


if __name__ == "__main__":
    unittest.main()
