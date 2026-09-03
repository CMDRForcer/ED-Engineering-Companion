import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ed_companion.phase14.controller import _last_complete_json_record

from ed_companion.phase14.controller import CockpitController


ROOT = Path(__file__).resolve().parents[1]
QML_FILES = [ROOT / "Main.qml", *(ROOT / "qml").rglob("*.qml")]


def qml_blocks(source, pattern):
    """Yield balanced QML blocks while ignoring braces inside strings."""
    for match in re.finditer(pattern, source):
        start = source.find("{", match.start())
        depth = 0
        quote = None
        escaped = False
        for index in range(start, len(source)):
            character = source[index]
            if quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                continue
            if character in {'"', "'"}:
                quote = character
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    yield match.start(), source[match.start():index + 1]
                    break


class QmlInteractionContractTests(unittest.TestCase):
    def test_journal_health_reads_last_complete_record_without_full_scan(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "Journal.test.log"
            path.write_bytes(
                (json.dumps({"event": "Earlier", "padding": "x" * 100000})
                 + "\n" + json.dumps({"event": "ReceiveText"}) + "\n").encode()
            )

            self.assertEqual(
                _last_complete_json_record(path)["event"], "ReceiveText"
            )

    def test_every_button_instance_has_an_action_handler(self):
        missing = []
        count = 0
        for path in QML_FILES:
            source = path.read_text(encoding="utf-8-sig")
            for offset, block in qml_blocks(
                source, r"\b(?:CockpitButton|Button)\s*\{"
            ):
                # This is the reusable CockpitButton component definition,
                # not an actionable instance.
                if (
                    "property color accentColor" in block
                    and "property bool selected" in block
                ):
                    continue
                count += 1
                if not re.search(
                    r"\bon(?:Clicked|Pressed|Released|Toggled|CheckedChanged)\s*:",
                    block,
                ):
                    line = source.count("\n", 0, offset) + 1
                    missing.append(f"{path.relative_to(ROOT)}:{line}")
        self.assertGreaterEqual(count, 98)
        self.assertEqual(missing, [])

    def test_all_qml_cockpit_actions_resolve_to_controller_methods(self):
        missing = []
        checked = set()
        for path in QML_FILES:
            source = path.read_text(encoding="utf-8-sig")
            for _offset, block in qml_blocks(
                source,
                r"\b(?:CockpitButton|Button|MouseArea)\s*\{",
            ):
                if not re.search(
                    r"\bon(?:Clicked|Pressed|Released|Toggled|CheckedChanged)\s*:",
                    block,
                ):
                    continue
                for method in re.findall(
                    r"\bcockpit\.([A-Za-z_]\w*)\s*\(", block
                ):
                    checked.add(method)
                    if not callable(getattr(CockpitController, method, None)):
                        missing.append(
                            f"{path.relative_to(ROOT)}: cockpit.{method}"
                        )
        self.assertGreaterEqual(len(checked), 48)
        self.assertEqual(missing, [])

    def test_accept_button_has_an_exclusive_hit_target(self):
        source = (ROOT / "Main.qml").read_text(encoding="utf-8-sig")
        button = next(
            block for _offset, block in qml_blocks(source, r"\bButton\s*\{")
            if "id: acceptCurrentButton" in block
        )
        mouse = next(
            block for _offset, block in qml_blocks(source, r"\bMouseArea\s*\{")
            if "id: slotMouse" in block
        )
        self.assertIn("z: 2", button)
        self.assertNotIn("anchors.fill: parent", mouse)
        self.assertIn("? acceptCurrentButton.left", mouse)


if __name__ == "__main__":
    unittest.main()
