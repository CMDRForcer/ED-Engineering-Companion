import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from ed_companion.i18n import SUPPORTED_LANGUAGES, TranslationCatalog


class TranslationCatalogTests(unittest.TestCase):
    def test_qml_has_no_untracked_static_interface_text(self):
        root = Path(__file__).resolve().parents[1]
        paths = [root / "Main.qml", *(root / "qml").rglob("*.qml")]
        binding = re.compile(
            r'\b(?:text|placeholderText|title|detail|helpText|Accessible\.name):\s*"'
        )
        allowed = {
            'title: "ED Engineering Companion (EDEC) · " + cockpit.appVersion',
            'text: "ED Engineering Companion"',
            'text: "⠿"; color: navTile.selectedNav ? cyan : muted',
            'text: "⌄"',
            'property string helpText: ""',
            'property string title: ""',
            'property string detail: ""',
        }
        offenders = []
        for path in paths:
            for number, line in enumerate(
                path.read_text(encoding="utf-8-sig").splitlines(), 1
            ):
                stripped = line.strip()
                if (
                    binding.search(line)
                    and "window.t(" not in line and "window.tf(" not in line
                    and "appWindow.t(" not in line and "appWindow.tf(" not in line
                    and stripped not in allowed
                    and not any(fragment in stripped for fragment in allowed)
                    and not re.search(
                        r'text:\s*"(?:○|F1|INARA|GPU Cockpit |CMDR Forcer)',
                        stripped,
                    )
                ):
                    offenders.append(f"{path.relative_to(root)}:{number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_supported_catalogs_follow_the_english_contract(self):
        root = Path(__file__).resolve().parents[1] / "ed_data" / "i18n"
        catalogs = {
            language: json.loads(
                (root / f"{language}.json").read_text(encoding="utf-8-sig")
            )
            for language in SUPPORTED_LANGUAGES
        }
        expected = set(catalogs["en"])

        self.assertEqual(SUPPORTED_LANGUAGES, ("en", "de", "es", "fr"))
        self.assertEqual(set(catalogs["de"]), expected)
        self.assertEqual(set(catalogs["es"]), expected)
        self.assertEqual(set(catalogs["fr"]), expected)
        for language, catalog in catalogs.items():
            self.assertTrue(set(catalog).issubset(expected), language)
            self.assertTrue(all(str(value).strip() for value in catalog.values()))

    def test_complete_catalogs_preserve_template_placeholders(self):
        root = Path(__file__).resolve().parents[1] / "ed_data" / "i18n"
        catalogs = {
            language: json.loads(
                (root / f"{language}.json").read_text(encoding="utf-8-sig")
            )
            for language in ("en", "de", "es", "fr")
        }
        placeholder = re.compile(r"%\d+")

        mismatches = []
        for language in ("de", "es", "fr"):
            for key, english in catalogs["en"].items():
                expected = sorted(placeholder.findall(english))
                actual = sorted(placeholder.findall(catalogs[language][key]))
                if actual != expected:
                    mismatches.append(
                        f"{key}: EN={expected}, {language.upper()}={actual}"
                    )
        self.assertEqual(mismatches, [])

    def test_all_powerplay_leader_biographies_are_localized(self):
        root = Path(__file__).resolve().parents[1]
        catalogs = {
            language: json.loads(
                (root / "ed_data" / "i18n" / f"{language}.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            for language in ("en", "de", "es", "fr")
        }
        leaders = {
            "aislingduval", "arissalavignyduval", "dentonpatreus",
            "zeminatorval", "feliciawinters", "jeromearcher",
            "edmundmahon", "nakatokaine", "pranavantal",
            "archondelaine", "yurigrom", "liyongrui",
        }
        for leader in leaders:
            key = f"powerplay.leader.{leader}.biography"
            self.assertIn(key, catalogs["en"])
            self.assertIn(key, catalogs["de"])
            self.assertIn(key, catalogs["es"])
            self.assertIn(key, catalogs["fr"])
            self.assertNotEqual(catalogs["de"][key], catalogs["en"][key])
            self.assertNotEqual(catalogs["es"][key], catalogs["en"][key])
            self.assertNotEqual(catalogs["fr"][key], catalogs["en"][key])

        signature_modules = {
            "aislingduval": "Prismatic Shield Generator",
            "arissalavignyduval": "Imperial Hammer railgun",
            "dentonpatreus": "Advanced Plasma Accelerator",
            "zeminatorval": "Mining Lance",
            "feliciawinters": "Pulse Disruptor",
            "jeromearcher": "Pacifier Frag-Cannon",
            "edmundmahon": "Retributor beam laser",
            "pranavantal": "Enforcer Cannon",
            "archondelaine": "Cytoscrambler burst laser",
            "yurigrom": "Containment Missile launcher",
            "liyongrui": "Pack-Hound Missile Rack",
        }
        for language in ("es", "fr"):
            for leader, module in signature_modules.items():
                self.assertIn(
                    module,
                    catalogs[language][f"powerplay.leader.{leader}.biography"],
                )

        qml = (root / "Main.qml").read_text(encoding="utf-8-sig")
        self.assertIn('key = "arissalavignyduval"', qml)
        self.assertIn('t("powerplay.leader." + key + ".biography"', qml)

    def test_missing_translation_falls_back_to_english_then_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "en.json").write_text(
                json.dumps({"known": "English value"}), encoding="utf-8"
            )
            (root / "de.json").write_text("{}", encoding="utf-8")
            catalog = TranslationCatalog(root)

        self.assertEqual(catalog.translate("de", "known", "Source"), "English value")
        self.assertEqual(catalog.translate("fr", "missing", "Source"), "Source")


if __name__ == "__main__":
    unittest.main()
