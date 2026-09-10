import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

import requests

from ed_companion.integrations.frontier_capi import (
    FRONTIER_CAPI_BASE,
    FRONTIER_TOKEN_URL,
    FrontierAuthError,
    FrontierCapiClient,
    FrontierCapiError,
    build_pkce_authorization,
    exchange_authorization_code,
    parse_authorization_callback,
    project_profile_snapshot,
    refresh_frontier_tokens,
)
from ed_companion.integrations.frontier_credentials import (
    FrontierCredentialError,
    FrontierCredentialStore,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class FrontierCapiTests(unittest.TestCase):
    def test_pkce_authorization_contains_required_scope_and_challenge(self):
        authorization = build_pkce_authorization(
            "public-client", "https://example.test/callback",
            state="expected-state", verifier="v" * 64,
        )
        query = parse_qs(urlparse(authorization.authorize_url).query)

        self.assertEqual(query["scope"], ["auth capi"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["state"], ["expected-state"])
        self.assertEqual(query["redirect_uri"], ["https://example.test/callback"])
        self.assertNotIn("client_secret", query)
        self.assertNotIn("v" * 64, authorization.authorize_url)
        self.assertNotIn("v" * 64, repr(authorization))
        self.assertNotIn("expected-state", repr(authorization))

    def test_non_https_redirect_is_rejected(self):
        with self.assertRaises(ValueError):
            build_pkce_authorization("client", "http://localhost/callback")

    def test_callback_requires_matching_state_and_code(self):
        callback = "https://example.test/callback?code=abc&state=expected"
        self.assertEqual(
            parse_authorization_callback(callback, "expected"), "abc"
        )
        with self.assertRaises(FrontierAuthError):
            parse_authorization_callback(callback, "different")

    def test_code_exchange_uses_pkce_without_shared_secret(self):
        calls = []

        def post(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse(200, {
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "token_type": "Bearer",
                "expires_in": 3600,
            })

        authorization = build_pkce_authorization(
            "client", "https://example.test/callback",
            state="state", verifier="x" * 64,
        )
        tokens = exchange_authorization_code(
            "client", authorization, "code", post=post, now=100
        )

        self.assertEqual(calls[0][0], FRONTIER_TOKEN_URL)
        self.assertNotIn("client_secret", calls[0][1]["data"])
        self.assertEqual(calls[0][1]["data"]["code_verifier"], "x" * 64)
        self.assertEqual(tokens.expires_at, 3700)
        self.assertNotIn("access-secret", repr(tokens))
        self.assertNotIn("refresh-secret", repr(tokens))

    def test_refresh_preserves_refresh_token_when_frontier_does_not_rotate_it(self):
        tokens = refresh_frontier_tokens(
            "client", "existing-refresh",
            post=lambda *_args, **_kwargs: FakeResponse(200, {
                "access_token": "new-access", "expires_in": 60,
            }),
            now=10,
        )

        self.assertEqual(tokens.refresh_token, "existing-refresh")
        self.assertEqual(tokens.expires_at, 70)

    def test_token_expiry_uses_a_refresh_margin(self):
        tokens = refresh_frontier_tokens(
            "client", "refresh",
            post=lambda *_args, **_kwargs: FakeResponse(200, {
                "access_token": "access", "expires_in": 120,
            }),
            now=100,
        )

        self.assertFalse(tokens.expires_within(60, now=100))
        self.assertTrue(tokens.expires_within(60, now=160))

    def test_credential_store_round_trips_only_protected_bytes(self):
        protected_prefix = b"protected:"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "frontier_credentials.dat"
            store = FrontierCredentialStore(
                path,
                protect=lambda value: protected_prefix + value[::-1],
                unprotect=lambda value: value[len(protected_prefix):][::-1],
            )
            tokens = refresh_frontier_tokens(
                "client", "refresh-secret",
                post=lambda *_args, **_kwargs: FakeResponse(200, {
                    "access_token": "access-secret", "expires_in": 60,
                }),
                now=10,
            )

            store.save(tokens)
            stored_text = path.read_text(encoding="ascii")
            restored = store.load()

            self.assertNotIn("access-secret", stored_text)
            self.assertNotIn("refresh-secret", stored_text)
            self.assertEqual(restored.access_token, "access-secret")
            self.assertEqual(restored.refresh_token, "refresh-secret")
            store.clear()
            self.assertFalse(path.exists())

    def test_credential_store_rejects_corrupt_ciphertext(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "frontier_credentials.dat"
            path.write_text("not base64", encoding="ascii")
            store = FrontierCredentialStore(
                path, protect=lambda value: value, unprotect=lambda value: value,
            )

            with self.assertRaises(FrontierCredentialError):
                store.load()

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is required")
    def test_windows_dpapi_credential_store_round_trip(self):
        with TemporaryDirectory() as directory:
            store = FrontierCredentialStore(
                Path(directory) / "frontier_credentials.dat"
            )
            tokens = refresh_frontier_tokens(
                "client", "refresh-secret",
                post=lambda *_args, **_kwargs: FakeResponse(200, {
                    "access_token": "access-secret", "expires_in": 60,
                }),
                now=10,
            )

            store.save(tokens)
            restored = store.load()

            self.assertEqual(restored.access_token, "access-secret")
            self.assertEqual(restored.refresh_token, "refresh-secret")

    def test_token_errors_never_include_response_credentials(self):
        with self.assertRaises(FrontierAuthError) as raised:
            refresh_frontier_tokens(
                "client", "private-refresh",
                post=lambda *_args, **_kwargs: FakeResponse(400, {
                    "error_description": "private-refresh was rejected",
                }),
            )

        self.assertNotIn("private-refresh", str(raised.exception))

    def test_capi_client_rate_limits_and_sends_bearer_header(self):
        clock_values = iter([0.0, 0.0, 10.0, 60.0])
        sleeps = []
        session = FakeSession([
            FakeResponse(200, {"commander": {}}, {
                "Date": "Thu, 10 Sep 2026 12:00:00 GMT",
            }),
            FakeResponse(200, {"commander": {}}, {
                "Date": "Thu, 10 Sep 2026 12:01:00 GMT",
            }),
        ])
        client = FrontierCapiClient(
            "access-secret", session=session, min_interval=60,
            clock=lambda: next(clock_values), sleeper=sleeps.append,
        )

        first = client.query("profile")
        client.query("profile")

        self.assertEqual(first["observedAt"], "2026-09-10T12:00:00Z")
        self.assertEqual(sleeps, [50.0])
        self.assertEqual(session.calls[0][0], FRONTIER_CAPI_BASE + "/profile")
        self.assertEqual(
            session.calls[0][1]["headers"]["Authorization"],
            "Bearer access-secret",
        )

    def test_capi_client_rejects_unknown_endpoints_before_network(self):
        session = FakeSession([])
        client = FrontierCapiClient("token", session=session)

        with self.assertRaises(ValueError):
            client.query("private-or-future-endpoint")
        self.assertEqual(session.calls, [])

    def test_capi_http_failure_is_retryable_without_leaking_body(self):
        session = FakeSession([FakeResponse(
            429, {"message": "token secret"}, {"Retry-After": "90"}
        )])
        client = FrontierCapiClient("token", session=session)

        with self.assertRaises(FrontierCapiError) as raised:
            client.query("profile")

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.retry_after, 90)
        self.assertNotIn("token secret", str(raised.exception))

    def test_transport_exception_is_normalized(self):
        class BrokenSession:
            def get(self, *_args, **_kwargs):
                raise requests.ConnectionError("private network detail")

        client = FrontierCapiClient("token", session=BrokenSession())
        with self.assertRaises(FrontierCapiError) as raised:
            client.query("profile")
        self.assertEqual(str(raised.exception), "Frontier CAPI could not be reached.")

    def test_authorization_requests_every_documented_audience(self):
        authorization = build_pkce_authorization(
            "client", "https://example.test/callback",
            state="state", verifier="v" * 64,
        )
        query = parse_qs(urlparse(authorization.authorize_url).query)

        self.assertEqual(query["audience"], ["all"])

    def test_callback_surfaces_a_frontier_error_response(self):
        callback = (
            "https://example.test/callback?error=access_denied"
            "&error_description=User%20cancelled%20the%20login&state=expected"
        )
        with self.assertRaises(FrontierAuthError) as raised:
            parse_authorization_callback(callback, "expected")

        message = str(raised.exception)
        self.assertIn("access_denied", message)
        self.assertIn("User cancelled the login", message)

    def test_callback_error_without_matching_state_stays_generic(self):
        callback = (
            "https://example.test/callback?error=server_error"
            "&error_description=leak%20me&state=wrong"
        )
        with self.assertRaises(FrontierAuthError) as raised:
            parse_authorization_callback(callback, "expected")

        self.assertIn("server_error", str(raised.exception))
        self.assertNotIn("leak me", str(raised.exception))

    def test_client_id_prefers_an_operator_override_over_the_bundled_default(self):
        from ed_companion.integrations.frontier_capi import _configured

        self.assertEqual(
            _configured("EDEC_FRONTIER_CLIENT_ID", "bundled", environ={}),
            "bundled",
        )
        self.assertEqual(
            _configured(
                "EDEC_FRONTIER_CLIENT_ID", "bundled",
                environ={"EDEC_FRONTIER_CLIENT_ID": "  operator-owned  "},
            ),
            "operator-owned",
        )
        self.assertEqual(
            _configured(
                "EDEC_FRONTIER_CLIENT_ID", "bundled",
                environ={"EDEC_FRONTIER_CLIENT_ID": "   "},
            ),
            "bundled",
        )

    def test_profile_projection_is_conservative(self):
        projected = project_profile_snapshot({
            "observedAt": "2026-09-10T12:00:00Z",
            "payload": {
                "commander": {
                    "name": "Forcer", "id": "F123", "credits": 500,
                    "currentShipId": 7,
                },
                "ship": {
                    "name": "Krait_MkII", "shipName": "Mechthild",
                    "shipIdent": "MECH-2", "value": {"total": 900},
                },
            },
        })

        self.assertEqual(projected["credits"]["value"], 500)
        self.assertEqual(projected["credits"]["basis"], "FRONTIER CAPI")
        self.assertEqual(projected["activeShip"]["id"], "7")
        self.assertEqual(projected["activeShip"]["type"], "Krait Mk II")
        self.assertEqual(projected["activeShip"]["value"], 900)


if __name__ == "__main__":
    unittest.main()
