import json
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError

from ed_companion.integrations.inara import InaraError, build_event, send_events
from ed_companion.phase14.controller import CockpitController


class _Signal:
    def emit(self, *_args):
        pass


class _Response:
    status_code = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, results):
        self._results = results

    def json(self):
        return {
            "header": {"eventStatus": 200, "eventStatusText": "OK"},
            "events": self._results,
        }


def _config():
    return {
        "consent": True,
        "api_key": "test-key",
        "commander_name": "Test Commander",
    }


def _controller(pending_count=1):
    context = SimpleNamespace(
        key="profile-test",
        directory=Path("profile-test").resolve(),
        journal_root="journal-test",
    )
    events = [build_event(f"event-{index}") for index in range(pending_count)]
    fingerprints = [f"fingerprint-{index}" for index in range(pending_count)]
    controller = SimpleNamespace(
        profile_context=context,
        _profile_generation=7,
        _active_inara_request={
            "request_id": "request-test",
            "profile_key": context.key,
            "path_generation": 7,
            "directory": str(context.directory),
        },
        _inara_busy=True,
        _inara_pending_events=events,
        _inara_pending_fingerprints=fingerprints,
        _inara_inflight_fingerprints=list(fingerprints),
        _inara_cache={},
        _inara_receipts=[],
        _inara_failure_count=0,
        _inara_retry_not_before=0.0,
        _inara_recovery_candidate_file="",
        _inara_status="",
        connectionChanged=_Signal(),
        _inara_auto_enabled=lambda: True,
        _save_inara_journal_cache=lambda: True,
        _save_inara_receipts=lambda: True,
        _inara_last_success_label=lambda: "none",
    )
    return controller


class InaraBatchResultTests(unittest.TestCase):
    def test_http_429_exposes_retry_after_and_controller_uses_it(self):
        event = build_event("setCommanderTravelLocation")

        for header_value, expected in (("7200", 7200), (None, 3700)):
            with self.subTest(retry_after=header_value):
                headers = {} if header_value is None else {"Retry-After": header_value}

                def post(*_args, **_kwargs):
                    raise HTTPError(
                        "https://inara.invalid", 429, "limited", headers,
                        BytesIO(b""),
                    )

                with self.assertRaises(InaraError) as raised:
                    send_events(_config(), [event], post=post)
                self.assertEqual(raised.exception.status_code, 429)
                self.assertEqual(
                    raised.exception.retry_after,
                    7200 if header_value is not None else None,
                )

                controller = _controller()
                failure = {
                    "message": "opaque error",
                    "retryable": True,
                    "statusCode": 429,
                    "retryAfter": raised.exception.retry_after,
                }
                result = {
                    "context": dict(controller._active_inara_request),
                    "operation": "journal",
                    "success": False,
                    "message": json.dumps(failure),
                    "ships": [],
                }
                with mock.patch(
                    "ed_companion.phase14.controller.time.monotonic",
                    return_value=1000.0,
                ), mock.patch(
                    "ed_companion.phase14.controller.time.time",
                    return_value=2000.0,
                ):
                    CockpitController._finish_inara(controller, result)
                self.assertEqual(controller._inara_retry_not_before, 1000.0 + expected)
                self.assertEqual(controller._inara_cache["rate_limit_until"], 2000.0 + expected)

    def test_mixed_batch_commits_only_accepted_events(self):
        events = [build_event(f"event-{index}") for index in range(3)]
        response = _Response([
            {"eventName": "event-0", "eventStatus": 200, "eventStatusText": "OK"},
            {"eventName": "event-1", "eventStatus": 422, "eventStatusText": "Rejected"},
            {"eventName": "event-2", "eventStatus": 202, "eventStatusText": "OK"},
        ])
        receipt, _body = send_events(
            _config(), events, post=lambda *_args, **_kwargs: response,
        )
        self.assertEqual(receipt["acceptedIndexes"], [0, 2])
        self.assertEqual(receipt["failedIndexes"], [1])

        controller = _controller(3)
        result = {
            "context": dict(controller._active_inara_request),
            "operation": "journal",
            "success": True,
            "message": json.dumps(receipt),
            "ships": {},
        }
        CockpitController._finish_inara(controller, result)

        self.assertEqual(controller._inara_pending_fingerprints, ["fingerprint-1"])
        self.assertEqual(
            controller._inara_cache["fingerprints"],
            ["fingerprint-0", "fingerprint-2"],
        )
        self.assertEqual(len(controller._inara_pending_events), 1)
        self.assertIn("partially accepted", controller._inara_status)

    def test_successful_batch_still_commits_every_event(self):
        events = [build_event("event-0"), build_event("event-1")]
        response = _Response([
            {"eventName": "event-0", "eventStatus": 200, "eventStatusText": "OK"},
            {"eventName": "event-1", "eventStatus": 200, "eventStatusText": "OK"},
        ])
        receipt, _body = send_events(
            _config(), events, post=lambda *_args, **_kwargs: response,
        )
        controller = _controller(2)
        CockpitController._finish_inara(controller, {
            "context": dict(controller._active_inara_request),
            "operation": "journal",
            "success": True,
            "message": json.dumps(receipt),
            "ships": {},
        })
        self.assertEqual(controller._inara_pending_events, [])
        self.assertEqual(
            controller._inara_cache["fingerprints"],
            ["fingerprint-0", "fingerprint-1"],
        )


if __name__ == "__main__":
    unittest.main()
