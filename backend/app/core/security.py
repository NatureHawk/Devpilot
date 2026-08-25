"""Cryptographic helpers: token encryption, session cookies, OAuth state.

All three derive from a single ``SECRET_KEY`` so a deployment has one secret to
manage. Rotating it invalidates existing sessions and makes stored GitHub tokens
undecryptable — which is the safe direction to fail.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import Settings

SESSION_COOKIE_NAME = "devpilot_session"
# Sessions last a working month; long enough to be convenient, short enough that
# a stolen cookie is not indefinite.
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
# OAuth round trips take seconds; anything older is a stale or replayed attempt.
OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60

_SESSION_SALT = "devpilot.session"
_STATE_SALT = "devpilot.oauth.state"


class TokenDecryptionError(Exception):
    """A stored token could not be decrypted, e.g. after a key rotation."""


def _fernet(settings: Settings) -> Fernet:
    """Derive a Fernet key from SECRET_KEY.

    Fernet needs exactly 32 url-safe base64 bytes; SECRET_KEY is arbitrary text,
    so it is hashed to a fixed width first.
    """
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(settings: Settings, plaintext: str) -> str:
    """Encrypt a third-party access token for storage at rest."""
    return _fernet(settings).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(settings: Settings, ciphertext: str) -> str:
    try:
        return _fernet(settings).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise TokenDecryptionError("Stored token could not be decrypted.") from exc


def _serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=salt)


def issue_session(settings: Settings, user_id: str) -> str:
    """Create a signed, timestamped session value naming the user."""
    return _serializer(settings, _SESSION_SALT).dumps({"user_id": user_id})


def read_session(settings: Settings, value: str) -> str | None:
    """Return the user id from a session cookie, or None if it is not usable."""
    try:
        payload: Any = _serializer(settings, _SESSION_SALT).loads(
            value, max_age=SESSION_MAX_AGE_SECONDS
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    user_id = payload.get("user_id")
    return user_id if isinstance(user_id, str) else None


def issue_oauth_state(settings: Settings, *, redirect_path: str) -> str:
    """Sign the CSRF state carried through the GitHub authorization round trip.

    A random nonce makes each state unique; ``redirect_path`` lets the callback
    return the user to where they started.
    """
    return _serializer(settings, _STATE_SALT).dumps(
        {"nonce": secrets.token_urlsafe(16), "redirect_path": redirect_path}
    )


def read_oauth_state(settings: Settings, value: str) -> str | None:
    """Validate returned state and yield its redirect path, or None if invalid."""
    try:
        payload: Any = _serializer(settings, _STATE_SALT).loads(
            value, max_age=OAUTH_STATE_MAX_AGE_SECONDS
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    path = payload.get("redirect_path")
    # Only same-site paths: an absolute URL here would be an open redirect.
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return "/"
    return path
