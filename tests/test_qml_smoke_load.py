"""Load the real Main.qml headless so QML runtime errors fail CI.

The other QML tests only parse the source text. This drives the actual
``phase14_main`` smoke runner under the offscreen platform, in a throwaway
``LOCALAPPDATA`` and with a unique single-instance name so it never touches a
running app or the user's profile.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_smoke(extra_env=None, timeout=240):
    with tempfile.TemporaryDirectory(prefix="edec-qml-smoke-") as scratch:
        env = {
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "LOCALAPPDATA": scratch,
            "EDEC_SINGLE_INSTANCE_NAME": f"EDEC-qml-smoke-{os.getpid()}",
            "PHASE14_SMOKE_TEST": "1",
        }
        env.update(extra_env or {})
        completed = subprocess.run(
            [sys.executable, str(ROOT / "phase14_main.py")],
            env=env, capture_output=True, text=True, timeout=timeout,
        )
    report = None
    for line in completed.stdout.splitlines():
        if line.startswith("PHASE14_SMOKE_REPORT="):
            report = json.loads(line[len("PHASE14_SMOKE_REPORT="):])
    return completed, report


class QmlSmokeLoadTests(unittest.TestCase):
    def test_main_qml_loads_without_runtime_errors(self):
        # The smoke runner steps pages faster than any human, which can trip a
        # rare delegate-incubation teardown race. Retry once so only a
        # reproducible QML error fails the suite.
        for attempt in range(2):
            completed, report = _run_smoke()
            self.assertIsNotNone(
                report, f"no smoke report\nstdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}",
            )
            if report["status"] == "PASS" and completed.returncode == 0:
                return
            last_failed = [
                row for row in report["areas"] if row["status"] != "PASS"
            ]
        self.fail(
            "Main.qml smoke load failed twice: "
            + json.dumps(last_failed, ensure_ascii=False)
        )

    def test_injected_qml_error_is_detected(self):
        """Guard the guard: a deliberate QML error must fail the smoke load."""
        completed, report = _run_smoke(
            {"PHASE14_SMOKE_INJECT_QML_ERROR": "1"}
        )
        self.assertIsNotNone(report, completed.stdout + completed.stderr)
        self.assertEqual(report["status"], "FAIL")
        self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
