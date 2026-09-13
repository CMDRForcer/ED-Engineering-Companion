import unittest

from ed_companion.engineering.unlock import engineer_unlock_signals


class EngineerNameMatchingTests(unittest.TestCase):
    """_engineer_name() matches a Journal-reported Engineer name against the
    catalog even when Frontier splices an in-fiction nickname into it - see
    ed_companion/engineering/unlock.py for why this must generalize past the
    one known Tod McQuinn case.
    """

    def _catalog(self, name):
        return {name: {"request": {
            "type": "bounty", "name": "bounty vouchers", "quantity": 15,
        }}}

    def _contribution(self, engineer, quantity=5):
        return [{
            "event": "EngineerContribution", "Engineer": engineer,
            "Type": "bounty", "Quantity": quantity,
        }]

    def test_exact_name_matches(self):
        signals = engineer_unlock_signals(
            self._contribution("Tod McQuinn"), self._catalog("Tod McQuinn"),
        )
        self.assertEqual(signals["Tod McQuinn"].get("contributionTotal"), 5)

    def test_known_mcquinn_nickname_still_resolves(self):
        signals = engineer_unlock_signals(
            self._contribution("Tod 'The Blaster' McQuinn"),
            self._catalog("Tod McQuinn"),
        )
        self.assertEqual(signals["Tod McQuinn"].get("contributionTotal"), 5)

    def test_a_different_engineers_hypothetical_nickname_also_resolves(self):
        # Regression guard for the generalized fix: any future Frontier
        # nickname splice - not just the one hardcoded McQuinn case - must
        # resolve without a new one-off patch.
        signals = engineer_unlock_signals(
            self._contribution("Colonel 'Iron Fist' Bris Dekker", quantity=3),
            self._catalog("Colonel Bris Dekker"),
        )
        self.assertEqual(
            signals["Colonel Bris Dekker"].get("contributionTotal"), 3
        )

    def test_an_unrelated_engineer_name_does_not_match(self):
        signals = engineer_unlock_signals(
            self._contribution("Selene Jean"), self._catalog("Tod McQuinn"),
        )
        self.assertNotIn("contributionTotal", signals["Tod McQuinn"])

    def test_word_order_must_match_not_just_a_bag_of_words(self):
        signals = engineer_unlock_signals(
            self._contribution("McQuinn Tod"), self._catalog("Tod McQuinn"),
        )
        self.assertNotIn("contributionTotal", signals["Tod McQuinn"])


if __name__ == "__main__":
    unittest.main()
