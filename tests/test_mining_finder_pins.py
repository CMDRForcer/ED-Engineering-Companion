import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ed_companion.phase14.controller import CockpitController


class MiningFinderPinTests(unittest.TestCase):
    def _controller(self, directory):
        controller = CockpitController.__new__(CockpitController)
        controller.mining_pins_file = Path(directory) / "mining_finder_pins.json"
        controller._mining_pins = set()
        controller.miningChanged = type("Signal", (), {"emit": lambda self: None})()
        return controller

    def test_toggling_an_unpinned_key_pins_it(self):
        with TemporaryDirectory() as directory:
            controller = self._controller(directory)
            controller.toggleMiningPin("48000123|Alpha A Ring")
            self.assertEqual(CockpitController.pinnedMiningSystems.fget(controller), ["48000123|Alpha A Ring"])

    def test_toggling_an_already_pinned_key_unpins_it(self):
        with TemporaryDirectory() as directory:
            controller = self._controller(directory)
            controller._mining_pins = {"48000123|Alpha A Ring"}
            controller.toggleMiningPin("48000123|Alpha A Ring")
            self.assertEqual(CockpitController.pinnedMiningSystems.fget(controller), [])

    def test_blank_key_is_ignored(self):
        with TemporaryDirectory() as directory:
            controller = self._controller(directory)
            controller.toggleMiningPin("")
            self.assertEqual(CockpitController.pinnedMiningSystems.fget(controller), [])

    def test_pins_persist_to_disk_and_reload(self):
        with TemporaryDirectory() as directory:
            controller = self._controller(directory)
            controller.toggleMiningPin("111|Beta B Ring")
            controller.toggleMiningPin("222|Gamma C Ring")

            on_disk = json.loads(controller.mining_pins_file.read_text(encoding="utf-8"))
            self.assertEqual(sorted(on_disk), ["111|Beta B Ring", "222|Gamma C Ring"])

            reloaded = CockpitController.__new__(CockpitController)
            reloaded.mining_pins_file = controller.mining_pins_file
            reloaded._mining_pins = set(
                str(item) for item in reloaded._read_local_json(
                    reloaded.mining_pins_file, [],
                ) if isinstance(item, str)
            )
            self.assertEqual(
                sorted(CockpitController.pinnedMiningSystems.fget(reloaded)),
                ["111|Beta B Ring", "222|Gamma C Ring"],
            )


if __name__ == "__main__":
    unittest.main()
