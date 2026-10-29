"""Offline-only verifier for the ShadowOps Identity -> CKA staging PoC.

Trust material is an independently supplied JWKS, NEVER taken from a JWT header,
user-provided URL, a live discovery endpoint, or the legacy Chess Studio JWT.
This module verifies signatures, not user enrolment or session revocation.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


STAGING_ISSUER = "https://id-staging.shadowops.dpdns.org"
CKA_AUDIENCE = "cka-simulator"
_MAX_TOKEN_SIZE = 4096
_MAX_JWKS_SIZE = 16_384
_MAX_LIFETIME = 3600
_CLOCK_SKEW = 30


class IdentityPoCError(ValueError):
    """Generic rejection; never return token material or verification internals."""


def _reject() -> None:
    raise IdentityPoCError("Identity staging token rejected")


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            _reject()
        result[key] = value
    return result


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, TypeError):
        _reject()
    if not isinstance(parsed, dict):
        _reject()
    return parsed


def _decode(value: str, limit: int) -> bytes:
    if not isinstance(value, str) or not value or len(value) > limit * 2:
        _reject()
    try:
        decoded = base64.b64decode(value, altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error):
        _reject()
    if len(decoded) > limit or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        _reject()
    return decoded


def _keys(jwks: bytes) -> dict[str, Ed25519PublicKey]:
    if not isinstance(jwks, bytes) or len(jwks) > _MAX_JWKS_SIZE:
        _reject()
    data = _json_object(jwks)
    if set(data) != {"keys"} or not isinstance(data["keys"], list) or not (1 <= len(data["keys"]) <= 3):
        _reject()
    found: dict[str, Ed25519PublicKey] = {}
    for item in data["keys"]:
        if not isinstance(item, dict) or set(item) != {"kty", "crv", "alg", "use", "kid", "x"}:
            _reject()
        kid = item["kid"]
        if (item["kty"], item["crv"], item["alg"], item["use"]) != ("OKP", "Ed25519", "EdDSA", "sig"):
            _reject()
        if not isinstance(kid, str) or not (1 <= len(kid) <= 128) or kid in found:
            _reject()
        public_bytes = _decode(item["x"], 32)
        if len(public_bytes) != 32:
            _reject()
        try:
            found[kid] = Ed25519PublicKey.from_public_bytes(public_bytes)
        except ValueError:
            _reject()
    return found


def _subject(value: Any) -> str:
    if not isinstance(value, str):
        _reject()
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        _reject()
    if parsed.version != 4 or str(parsed) != value:
        _reject()
    return value


def verify_staging_cka_token(token: str, *, jwks: bytes, now: int | None = None) -> str:
    """Verify the exact staging issuer and CKA audience; return only UUIDv4 sub.

    This intentionally does NOT establish an authenticated login/session.
    Callers must separately enforce session state, revocation and app access
    before using an accepted token for any real protected resource.
    """
    if not isinstance(token, str) or len(token) > _MAX_TOKEN_SIZE:
        _reject()
    parts = token.split(".")
    if len(parts) != 3:
        _reject()
    header = _json_object(_decode(parts[0], 512))
    payload = _json_object(_decode(parts[1], 1024))
    signature = _decode(parts[2], 64)
    if set(header) != {"alg", "typ", "kid"} or header["alg"] != "EdDSA" or header["typ"] != "JWT":
        _reject()
    kid = header["kid"]
    if not isinstance(kid, str):
        _reject()
    key = _keys(jwks).get(kid)
    if key is None or len(signature) != 64:
        _reject()
    try:
        key.verify(signature, (parts[0] + "." + parts[1]).encode("ascii"))
    except (InvalidSignature, ValueError, UnicodeError):
        _reject()
    if payload.get("iss") != STAGING_ISSUER or payload.get("aud") != CKA_AUDIENCE:
        _reject()
    subject = _subject(payload.get("sub"))
    iat, nbf, exp = (payload.get(name) for name in ("iat", "nbf", "exp"))
    if any(type(value) is not int for value in (iat, nbf, exp)):
        _reject()
    current = int(time.time()) if now is None else now
    if (type(current) is not int or current <= 0 or iat <= 0
            or iat > current + _CLOCK_SKEW or nbf < iat
            or nbf > current + _CLOCK_SKEW or exp <= current
            or exp <= iat or exp - iat > _MAX_LIFETIME):
        _reject()
    return subject
