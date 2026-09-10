"""Disabled-by-default Frontier OAuth and CAPI transport primitives.

This module deliberately owns no UI, persistence, or automatic scheduling.
Callers must explicitly supply a registered HTTPS redirect URI and tokens.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from ed_companion import APP_VERSION


FRONTIER_AUTH_BASE = "https://auth.frontierstore.net"
FRONTIER_AUTHORIZE_URL = f"{FRONTIER_AUTH_BASE}/auth"
FRONTIER_TOKEN_URL = f"{FRONTIER_AUTH_BASE}/token"
FRONTIER_CAPI_BASE = "https://companion.orerve.net"
FRONTIER_SCOPES = "auth capi"
FRONTIER_CLIENT_ID = "b17a6919-d902-430d-bb28-7fbb7cbbe5a9"
FRONTIER_REDIRECT_URI = "https://cmdrforcer.github.io/oauth/callback.html"
CAPI_ENDPOINTS = frozenset({"/profile", "/market", "/shipyard", "/fleetcarrier"})
CAPI_MIN_INTERVAL_SECONDS = 60.0
CAPI_TIMEOUT_SECONDS = 25


class FrontierCapiError(RuntimeError):
    """Privacy-safe CAPI failure suitable for user-facing diagnostics."""

    def __init__(
        self, message, *, status_code=None, retryable=False, retry_after=None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = bool(retryable)
        self.retry_after = retry_after


class FrontierAuthError(FrontierCapiError):
    """OAuth authorization or token failure without credential disclosure."""


@dataclass(frozen=True)
class PkceAuthorization:
    authorize_url: str = field(repr=False)
    state: str = field(repr=False)
    verifier: str = field(repr=False)
    redirect_uri: str


@dataclass(frozen=True)
class FrontierTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    token_type: str = "Bearer"
    expires_at: float = 0.0

    @property
    def authorization_header(self):
        return f"{self.token_type} {self.access_token}"

    def expires_within(self, seconds, *, now=None):
        current = float(time.time() if now is None else now)
        return self.expires_at <= current + max(0.0, float(seconds))


def _urlsafe(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _require_https_redirect(redirect_uri):
    parsed = urlparse(str(redirect_uri or ""))
    if parsed.scheme.casefold() != "https" or not parsed.netloc:
        raise ValueError("Frontier redirect URI must be an absolute HTTPS URL")
    return parsed.geturl()


def _readable_ship_type(value):
    internal = str(value or "").strip()
    conventional = {
        "ferdelance": "Fer-de-Lance",
        "krait_mkii": "Krait Mk II",
    }
    if internal.casefold() in conventional:
        return conventional[internal.casefold()]
    words = re.sub(r"[_-]+", " ", internal).split()
    return " ".join(
        word.upper() if word.casefold() in {"ii", "iii", "iv", "mk"}
        else word.capitalize()
        for word in words
    )


def build_pkce_authorization(
    client_id, redirect_uri, *, state=None, verifier=None
):
    """Create one OAuth authorization URL without contacting Frontier."""
    client_id = str(client_id or "").strip()
    if not client_id:
        raise ValueError("Frontier client ID is required")
    redirect_uri = _require_https_redirect(redirect_uri)
    verifier = str(verifier or secrets.token_urlsafe(64))
    if not 43 <= len(verifier) <= 128:
        raise ValueError("PKCE verifier must contain 43 to 128 characters")
    state = str(state or secrets.token_urlsafe(32))
    if not state:
        raise ValueError("OAuth state is required")
    challenge = _urlsafe(hashlib.sha256(verifier.encode("ascii")).digest())
    query = urlencode({
        "response_type": "code",
        "audience": "frontier,steam,epic",
        "scope": FRONTIER_SCOPES,
        "client_id": client_id,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "redirect_uri": redirect_uri,
    })
    return PkceAuthorization(
        authorize_url=f"{FRONTIER_AUTHORIZE_URL}?{query}",
        state=state,
        verifier=verifier,
        redirect_uri=redirect_uri,
    )


def parse_authorization_callback(callback_url, expected_state):
    """Return an OAuth code only after the anti-forgery state matches."""
    parameters = parse_qs(urlparse(str(callback_url or "")).query)
    received_state = (parameters.get("state") or [""])[0]
    if not expected_state or not secrets.compare_digest(
        str(expected_state), str(received_state)
    ):
        raise FrontierAuthError("Frontier authorization state did not match.")
    code = str((parameters.get("code") or [""])[0])
    if not code:
        raise FrontierAuthError("Frontier authorization was not completed.")
    return code


def _retry_after(response):
    value = str(getattr(response, "headers", {}).get("Retry-After") or "")
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _read_json_response(response, purpose, error_type=FrontierCapiError):
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise error_type(
            f"Frontier {purpose} failed (HTTP {status or 'unknown'}).",
            status_code=status or None,
            retryable=status == 418 or status == 429 or status >= 500,
            retry_after=_retry_after(response),
        )
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise error_type(f"Frontier {purpose} returned invalid JSON.") from exc
    if not isinstance(payload, Mapping):
        raise error_type(f"Frontier {purpose} returned an invalid document.")
    return dict(payload)


def _tokens_from_response(response, *, previous_refresh="", now=None):
    payload = _read_json_response(response, "token request", FrontierAuthError)
    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or previous_refresh or "")
    token_type = str(payload.get("token_type") or "Bearer")
    if not access_token or not refresh_token:
        raise FrontierAuthError("Frontier returned incomplete OAuth tokens.")
    try:
        expires_in = max(0.0, float(payload.get("expires_in") or 0))
    except (TypeError, ValueError):
        expires_in = 0.0
    issued_at = float(time.time() if now is None else now)
    return FrontierTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=token_type,
        expires_at=issued_at + expires_in,
    )


def exchange_authorization_code(
    client_id, authorization, code, *, post=requests.post,
    timeout=CAPI_TIMEOUT_SECONDS, now=None,
):
    """Exchange one PKCE code; a client secret is intentionally unsupported."""
    if not isinstance(authorization, PkceAuthorization):
        raise TypeError("authorization must be a PkceAuthorization")
    data = {
        "grant_type": "authorization_code",
        "client_id": str(client_id or ""),
        "code_verifier": authorization.verifier,
        "code": str(code or ""),
        "redirect_uri": authorization.redirect_uri,
    }
    try:
        response = post(FRONTIER_TOKEN_URL, data=data, timeout=timeout)
    except requests.RequestException as exc:
        raise FrontierAuthError(
            "Frontier token service could not be reached.", retryable=True
        ) from exc
    return _tokens_from_response(response, now=now)


def refresh_frontier_tokens(
    client_id, refresh_token, *, post=requests.post,
    timeout=CAPI_TIMEOUT_SECONDS, now=None,
):
    """Refresh OAuth tokens while preserving a non-rotated refresh token."""
    refresh_token = str(refresh_token or "")
    if not refresh_token:
        raise FrontierAuthError("No Frontier refresh token is available.")
    try:
        response = post(FRONTIER_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": str(client_id or ""),
            "refresh_token": refresh_token,
        }, timeout=timeout)
    except requests.RequestException as exc:
        raise FrontierAuthError(
            "Frontier token service could not be reached.", retryable=True
        ) from exc
    return _tokens_from_response(
        response, previous_refresh=refresh_token, now=now
    )


class FrontierCapiClient:
    """Small synchronous transport intended to run only in a worker thread."""

    def __init__(
        self, access_token, *, token_type="Bearer", session=None,
        min_interval=CAPI_MIN_INTERVAL_SECONDS, clock=time.monotonic,
        sleeper=time.sleep, utcnow=None,
    ):
        if not str(access_token or ""):
            raise ValueError("Frontier access token is required")
        self._authorization = f"{token_type or 'Bearer'} {access_token}"
        self._session = session or requests.Session()
        self._min_interval = max(0.0, float(min_interval))
        self._clock = clock
        self._sleeper = sleeper
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._last_request_at = None
        self._lock = threading.Lock()

    def query(self, endpoint, *, timeout=CAPI_TIMEOUT_SECONDS):
        endpoint = "/" + str(endpoint or "").strip().lstrip("/")
        if endpoint not in CAPI_ENDPOINTS:
            raise ValueError(f"Unsupported Frontier CAPI endpoint: {endpoint}")
        with self._lock:
            now = self._clock()
            if self._last_request_at is not None:
                delay = self._min_interval - (now - self._last_request_at)
                if delay > 0:
                    self._sleeper(delay)
            self._last_request_at = self._clock()
            try:
                response = self._session.get(
                    FRONTIER_CAPI_BASE + endpoint,
                    headers={
                        "Authorization": self._authorization,
                        "User-Agent": f"ED-Engineering-Companion/{APP_VERSION}",
                        "Accept": "application/json",
                    },
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                raise FrontierCapiError(
                    "Frontier CAPI could not be reached.", retryable=True
                ) from exc
        payload = _read_json_response(response, "CAPI request")
        if endpoint == "/profile" and not isinstance(
            payload.get("commander"), Mapping
        ):
            raise FrontierCapiError(
                "Frontier profile response did not contain Commander data."
            )
        observed_at = _response_timestamp(response, payload, self._utcnow)
        return {
            "endpoint": endpoint,
            "observedAt": observed_at,
            "payload": payload,
        }


def _response_timestamp(response, payload, utcnow: Callable):
    value = str(payload.get("timestamp") or "")
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
        except ValueError:
            pass
    date_header = str(getattr(response, "headers", {}).get("Date") or "")
    if date_header:
        try:
            return parsedate_to_datetime(date_header).astimezone(
                timezone.utc
            ).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError):
            pass
    return utcnow().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def project_profile_snapshot(snapshot):
    """Project only conservative Commander and current-ship profile fields."""
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    payload = snapshot.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    commander = payload.get("commander")
    commander = commander if isinstance(commander, Mapping) else {}
    ship = payload.get("ship")
    ship = ship if isinstance(ship, Mapping) else {}
    observed_at = str(snapshot.get("observedAt") or "")

    credits_value = commander.get("credits")
    credits_known = isinstance(credits_value, (int, float)) and not isinstance(
        credits_value, bool
    )
    ship_id = ship.get("id")
    if ship_id in (None, ""):
        ship_id = commander.get("currentShipId")
    ship_type = _readable_ship_type(ship.get("name") or ship.get("type"))
    ship_name = str(
        ship.get("shipName") or ship.get("userShipName") or ""
    ).strip()
    ship_ident = str(
        ship.get("shipIdent") or ship.get("userShipId") or ""
    ).strip()
    value = ship.get("value")
    if isinstance(value, Mapping):
        value = value.get("total")
    value_known = isinstance(value, (int, float)) and not isinstance(value, bool)

    return {
        "observedAt": observed_at,
        "commander": {
            "name": str(commander.get("name") or ""),
            "frontierId": str(commander.get("id") or ""),
        },
        "credits": {
            "known": credits_known,
            "value": max(0, int(credits_value)) if credits_known else 0,
            "timestamp": observed_at,
            "basis": "FRONTIER CAPI",
        },
        "activeShip": {
            "known": bool(ship_id not in (None, "") and ship_type),
            "id": str(ship_id or ""),
            "type": ship_type,
            "name": ship_name,
            "ident": ship_ident,
            "value": max(0, int(value)) if value_known else None,
            "observedAt": observed_at,
        },
    }
