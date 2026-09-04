import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError

from ed_companion.integrations.eddn import EddnError, prepare_event, send, update_context
from ed_companion.phase14.controller import CockpitController


def _prepared():
    context = update_context({}, {
        "event": "Fileheader", "gameversion": "4.2.0.0", "build": "r0",
    })
    context = update_context(context, {
        "event": "LoadGame", "Horizons": True, "Odyssey": True,
    })
    context = update_context(context, {
        "event": "Location", "StarSystem": "Test", "StarPos": [1, 2, 3],
        "SystemAddress": 42,
    })
    return prepare_event({
        "timestamp": "2026-09-04T10:00:00Z", "event": "FSDJump",
        "StarSystem": "Test 2", "StarPos": [1, 2, 3], "SystemAddress": 42,
    }, context)


class _Signal:
    def __init__(self, calls=None):
        self.calls = calls

    def emit(self, *_args):
        if self.calls is not None:
            self.calls.append("signal")


def _finish_controller(calls=None):
    job = {
        "id": "job", "status": "sending", "attempts": 1,
        "event": _prepared(),
    }
    controller = SimpleNamespace(
        _eddn_busy=True,
        _eddn_queue=[job],
        _eddn_config={"retry_failed": True},
        _eddn_status="before",
        connectionChanged=_Signal(calls),
        _eddn_delivery_summary=lambda: {"lastSuccessAt": ""},
        _process_eddn_queue=lambda: None,
        _publish_eddn_delivery_change=(
            (lambda: calls.append("publish")) if calls is not None else lambda: None
        ),
    )

    def save():
        if calls is not None:
            calls.append(("save", controller._eddn_queue[0]["status"]))
        return True

    controller._save_eddn = save
    return controller


class EddnRetryAndDeliveryWindowTests(unittest.TestCase):
    def test_http_429_exposes_retry_after_and_controller_prefers_it(self):
        def opener(*_args, **_kwargs):
            raise HTTPError(
                "https://eddn.invalid", 429, "limited",
                {"Retry-After": "1800"}, BytesIO(b"limited"),
            )

        with self.assertRaises(EddnError) as raised:
            send(_prepared(), {}, "anonymous", opener=opener)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.retry_after, 1800)

        controller = _finish_controller()
        with mock.patch(
            "ed_companion.phase14.controller.time.time", return_value=1000.0,
        ):
            CockpitController._finish_eddn(controller, "job", False, json.dumps({
                "message": "limited", "terminal": False,
                "statusCode": 429, "retryAfter": 1800,
            }))
        self.assertEqual(controller._eddn_queue[0]["next_retry_at"], 2800.0)
        self.assertEqual(controller._eddn_queue[0]["next_retry_seconds"], 1800)

    def test_429_without_header_and_non_429_keep_local_backoff(self):
        for status_code, retry_after in ((429, None), (503, 1800)):
            with self.subTest(status_code=status_code):
                controller = _finish_controller()
                with mock.patch(
                    "ed_companion.phase14.controller.time.time", return_value=1000.0,
                ):
                    CockpitController._finish_eddn(
                        controller, "job", False, json.dumps({
                            "message": "temporary", "terminal": False,
                            "statusCode": status_code, "retryAfter": retry_after,
                        }),
                    )
                self.assertEqual(
                    controller._eddn_queue[0]["next_retry_seconds"], 60,
                )

    def test_gateway_acceptance_is_checkpointed_before_ui_work(self):
        calls = []
        controller = _finish_controller(calls)
        with mock.patch(
            "ed_companion.phase14.controller.QTimer.singleShot"
        ):
            CockpitController._finish_eddn(controller, "job", True, json.dumps({
                "httpStatus": 200, "event": "FSDJump", "elapsedMs": 1,
            }))
        self.assertEqual(calls[0], ("save", "sent"))
        self.assertLess(calls.index(("save", "sent")), calls.index("publish"))

    def test_restart_logs_possible_duplicate_for_interrupted_sending_job(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "community_upload_queue.json"
            queue_path.write_text(json.dumps([{
                "id": "job", "target": "EDDN", "status": "sending",
                "event": _prepared(),
            }]), encoding="utf-8")
            controller = CockpitController.__new__(CockpitController)
            controller.eddn_queue_file = queue_path
            controller.eddn_quarantine_file = root / "quarantine.json"
            with self.assertLogs(
                "ed_companion.phase14.controller", level="WARNING"
            ) as captured:
                loaded = controller._load_eddn_queue()
            self.assertEqual(loaded[0]["status"], "retry")
            self.assertTrue(loaded[0]["recovered_after_restart"])
            self.assertIn("retry may produce a duplicate", " ".join(captured.output))


if __name__ == "__main__":
    unittest.main()
