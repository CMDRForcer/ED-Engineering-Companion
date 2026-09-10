import unittest
from pathlib import Path

from phase14_main import (
    FrontierOAuthCallbackRuntime,
    frontier_oauth_callback_argument,
    parse_single_instance_message,
    register_windows_url_protocol,
    single_instance_message,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def CreateKey(self, root, path):
        self.values.setdefault((root, path), {})
        return FakeKey(path)

    def SetValueEx(self, key, name, _reserved, _kind, value):
        self.values[(self.HKEY_CURRENT_USER, key.path)][name] = value


class FrontierOAuthCallbackTests(unittest.TestCase):
    def test_callback_argument_accepts_only_the_registered_route(self):
        valid = "edec://oauth/callback?code=secret&state=expected"

        self.assertEqual(frontier_oauth_callback_argument([valid]), valid)
        self.assertEqual(
            frontier_oauth_callback_argument([
                "https://oauth/callback?code=secret",
                "edec://other/callback?code=secret",
                "edec://oauth/wrong?code=secret",
            ]),
            "",
        )

    def test_single_instance_message_round_trips_valid_callback(self):
        callback = "edec://oauth/callback?code=secret&state=expected"

        self.assertEqual(
            parse_single_instance_message(single_instance_message(callback)),
            ("frontier_oauth", callback),
        )
        self.assertEqual(
            parse_single_instance_message(b"not-json"), ("show", "")
        )

    def test_callback_runtime_keeps_and_consumes_latest_valid_callback(self):
        callback = "edec://oauth/callback?code=secret&state=expected"
        runtime = FrontierOAuthCallbackRuntime()

        self.assertFalse(runtime.accept("edec://wrong/callback?code=secret"))
        self.assertTrue(runtime.accept(callback))
        self.assertEqual(runtime.pendingCallback, callback)
        self.assertEqual(runtime.take(), callback)
        self.assertEqual(runtime.pendingCallback, "")

    def test_windows_registration_quotes_executable_and_callback_argument(self):
        registry = FakeWinreg()
        executable = ROOT / "dist" / "EDEC" / "EDEC.exe"

        registered = register_windows_url_protocol(
            executable=executable, frozen=True, winreg_module=registry
        )

        self.assertTrue(registered)
        command_values = next(
            values for (root, path), values in registry.values.items()
            if path.endswith(r"shell\open\command")
        )
        self.assertEqual(
            command_values[""], f'"{executable.resolve()}" "%1"'
        )

    def test_https_callback_page_forwards_only_oauth_parameters(self):
        source = (ROOT / "docs" / "oauth" / "callback.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('const allowed = ["code", "state", "error", "error_description"]', source)
        self.assertIn('"edec://oauth/callback"', source)
        self.assertIn('name="referrer" content="no-referrer"', source)
        self.assertIn(
            'href="https://cmdrforcer.github.io/ED-Engineering-Companion/oauth/callback.html"',
            source,
        )
        self.assertNotIn("fetch(", source)
        self.assertNotIn("localStorage", source)

    def test_pages_workflow_publishes_only_the_callback_site(self):
        source = (
            ROOT / ".github" / "workflows" / "pages.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("cp docs/oauth/callback.html _site/oauth/callback.html", source)
        self.assertIn("actions/deploy-pages@v4", source)
        self.assertNotIn("docs/images", source)


if __name__ == "__main__":
    unittest.main()
