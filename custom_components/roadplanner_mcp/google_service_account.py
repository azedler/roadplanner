"""Turning a service-account key into a bearer token for Vertex AI.

Lyria lives on Vertex AI, and Vertex does not accept the API key the rest
of this integration uses. It wants an OAuth2 access token, and the only
way to get one without a browser is the service-account flow: sign a
short-lived assertion with the account's private key, hand it to Google's
token endpoint, get an access token back.

That is forty lines of well-specified work, so it is written here rather
than pulled in as a dependency. A custom integration adds its
requirements to somebody's Home Assistant, and `cryptography` is already
part of core - this needs nothing that is not there.

**The key is a credential and behaves like one.** Nothing from it is
logged, nothing from it is returned to the panel, and the only value that
ever leaves this module is the access token itself. The private key is
never written anywhere by this code; it arrives as configuration and
stays in memory.

Tokens are cached until shortly before they expire. A film's music is a
handful of calls a few seconds apart, and minting a fresh token for each
would be three round trips for nothing.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# What the token is FOR. The narrowest scope that reaches Vertex: this
# integration has no business asking for anything wider.
VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

# Google issues one-hour tokens. Renewed a minute early, so a call that
# starts just before the boundary cannot arrive just after it.
TOKEN_LIFETIME_SECONDS = 3600
TOKEN_REFRESH_MARGIN_SECONDS = 60

# The fields a usable key file has. Checked by name so a wrong file - an
# OAuth client, an API key in JSON, somebody's downloaded settings - is
# refused with a sentence rather than a KeyError three calls later.
REQUIRED_FIELDS = ("client_email", "private_key", "token_uri", "type")


class ServiceAccountError(ValueError):
    """The key cannot be used. Always says which part is missing."""


def _b64(data: bytes) -> str:
    """base64url without padding, which is what JWT wants."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def parse_service_account(raw: Any) -> dict[str, Any]:
    """Read a key file, refusing anything that is not one.

    Accepts the JSON text or the already-parsed object, because the value
    reaches this from a config field where somebody may have pasted
    either.
    """
    if isinstance(raw, dict):
        data = raw
    else:
        text = str(raw or "").strip()
        if not text:
            raise ServiceAccountError(
                "Für Vertex AI fehlt der Dienstkonto-Schlüssel (JSON)"
            )
        try:
            data = json.loads(text)
        except ValueError as err:
            raise ServiceAccountError(
                "Der Dienstkonto-Schlüssel ist kein gültiges JSON"
            ) from err
    if not isinstance(data, dict):
        raise ServiceAccountError("Der Dienstkonto-Schlüssel ist kein Objekt")
    missing = [name for name in REQUIRED_FIELDS if not str(data.get(name) or "").strip()]
    if missing:
        raise ServiceAccountError(
            "Dem Dienstkonto-Schlüssel fehlen Felder: " + ", ".join(missing)
        )
    if str(data.get("type")) != "service_account":
        raise ServiceAccountError(
            "Das ist kein Dienstkonto-Schlüssel "
            f"(type={str(data.get('type'))!r}) - Vertex AI braucht einen"
        )
    return data


def build_assertion(account: dict[str, Any], *, now: float, scope: str = VERTEX_SCOPE) -> str:
    """The signed JWT that is exchanged for an access token.

    A pure function of the key and the clock, so the shape can be checked
    without a network and without minting anything.
    """
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import padding  # noqa: PLC0415

    issued = int(now)
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": str(account["client_email"]),
        "scope": scope,
        "aud": str(account.get("token_uri") or TOKEN_ENDPOINT),
        "iat": issued,
        "exp": issued + TOKEN_LIFETIME_SECONDS,
    }
    signing_input = ".".join(
        _b64(json.dumps(part, separators=(",", ":")).encode("utf-8"))
        for part in (header, claims)
    ).encode("ascii")
    try:
        key = serialization.load_pem_private_key(
            str(account["private_key"]).encode("utf-8"), password=None
        )
    except Exception as err:  # noqa: BLE001 - any failure here means "bad key"
        raise ServiceAccountError(
            "Der private Schlüssel des Dienstkontos ist nicht lesbar"
        ) from err
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode('ascii')}.{_b64(signature)}"


def token_request(account: dict[str, Any], *, now: float) -> tuple[str, dict[str, str]]:
    """Where to ask for a token, and with what."""
    return str(account.get("token_uri") or TOKEN_ENDPOINT), {
        "grant_type": GRANT_TYPE,
        "assertion": build_assertion(account, now=now),
    }


class ServiceAccountTokens:
    """One account, one cached token.

    Deliberately small: it holds a token and knows when it goes stale.
    Everything about *using* the token belongs to whoever asked for it.
    """

    def __init__(self, account: dict[str, Any]) -> None:
        self._account = parse_service_account(account)
        self._token = ""
        self._expires_at = 0.0

    @property
    def client_email(self) -> str:
        """Which account this is. Safe to show - it is not a secret."""
        return str(self._account.get("client_email") or "")

    def cached(self, *, now: float | None = None) -> str:
        """The token, if it is still good. Empty string means "ask again"."""
        moment = time.time() if now is None else now
        if self._token and moment < self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
            return self._token
        return ""

    def request(self, *, now: float | None = None) -> tuple[str, dict[str, str]]:
        return token_request(self._account, now=time.time() if now is None else now)

    def store(self, payload: Any, *, now: float | None = None) -> str:
        """Keep what the token endpoint answered, and say what went wrong."""
        moment = time.time() if now is None else now
        if not isinstance(payload, dict):
            raise ServiceAccountError("Die Token-Antwort ist kein Objekt")
        token = str(payload.get("access_token") or "").strip()
        if not token:
            # Google puts the reason in `error_description`, and that
            # sentence is the difference between "it does not work" and
            # "the Vertex AI API is not enabled for this project".
            detail = str(payload.get("error_description") or payload.get("error") or "")
            raise ServiceAccountError(
                "Google hat kein Zugriffstoken ausgestellt"
                + (f": {detail[:200]}" if detail else "")
            )
        try:
            lifetime = float(payload.get("expires_in") or TOKEN_LIFETIME_SECONDS)
        except (TypeError, ValueError):
            lifetime = float(TOKEN_LIFETIME_SECONDS)
        self._token = token
        self._expires_at = moment + max(0.0, lifetime)
        return token


__all__ = [
    "GRANT_TYPE",
    "REQUIRED_FIELDS",
    "TOKEN_ENDPOINT",
    "VERTEX_SCOPE",
    "ServiceAccountError",
    "ServiceAccountTokens",
    "build_assertion",
    "parse_service_account",
    "token_request",
]
