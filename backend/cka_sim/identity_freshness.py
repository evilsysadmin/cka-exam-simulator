"""Strict, OFFLINE contract for future CKA Identity revocation checks.

A valid JWT signature does NOT prove the underlying user/session is active.
The caller must supply a trusted, request-scoped, authoritative checker that
reads the actual Identity session/token registry. None is deployed today.
This module must never authorize Kubernetes resources by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from .identity_poc import (
    STAGING_ISSUER,
    _decode,
    _json_object,
    _reject,
    verify_staging_cka_token,
)


_MAX_SESSION_TOKEN_LIFETIME = 120
_MAX_EVIDENCE_AGE = 2


@dataclass(frozen=True)
class SessionFreshnessEvidence:
    """Evidence from a TRUSTED Identity service, not from a JWT or browser.

    The future provider must verify the account state, exact session version,
    and the token JTI's relationship to a still-active server-side session.
    Merely looking up the account version is not enough after handle rotation.
    """

    issuer: str
    subject: str
    session_version: int
    token_jti: str
    checked_at: int
    account_active: bool
    session_active: bool
    token_active: bool


# This injectable adapter is a testable contract, not an operational IdP.
# The adapter must issue one strongly consistent, noncached bounded check per
# call, via a trusted backend channel with proper service authentication.
SessionFreshnessChecker = Callable[[str, int, str], SessionFreshnessEvidence]


def verify_current_staging_cka_session(
    token: str,
    *,
    jwks: bytes,
    check_freshness: SessionFreshnessChecker | None = None,
    now: int | None = None,
) -> str:
    """Require cryptographic verification AND live, exact-session freshness.

    Fails closed when the authoritative checker is unavailable. No HTTP
    caller or Kubernetes route uses this helper yet. A successful result
    still requires independent CKA authorization and lab tenancy checks.
    """
    current = int(time.time()) if now is None else now
    if type(current) is not int or current <= 0 or not callable(check_freshness):
        _reject()

    # Never consult a remote checker with claims that have not been signed
    # by a key from the independently provisioned trusted JWKS.
    subject = verify_staging_cka_token(token, jwks=jwks, now=current)
    payload = _json_object(_decode(token.split(".")[1], 1024))
    if set(payload) != {"iss", "aud", "sub", "iat", "nbf", "exp", "sv", "jti"}:
        _reject()
    version = payload["sv"]
    jti = payload["jti"]
    if type(version) is not int or not (1 <= version <= 2**63 - 1):
        _reject()
    if not isinstance(jti, str) or len(jti) != 22 or len(_decode(jti, 16)) != 16:
        _reject()
    if payload["nbf"] != payload["iat"] or not (
        0 < payload["exp"] - payload["iat"] <= _MAX_SESSION_TOKEN_LIFETIME
    ):
        _reject()

    try:
        evidence = check_freshness(subject, version, jti)
    except Exception:
        # Network failures, denied/missing records, malformed provider
        # responses and timeouts are all denial, never an auth bypass.
        _reject()

    # A bounded authority call may still cross token expiry. Check real time
    # again; injected deterministic clocks keep the offline contract stable.
    if now is None:
        current = int(time.time())
        if payload["exp"] <= current:
            _reject()

    if type(evidence) is not SessionFreshnessEvidence:
        _reject()
    if (
        evidence.issuer != STAGING_ISSUER
        or evidence.subject != subject
        or type(evidence.session_version) is not int
        or evidence.session_version != version
        or evidence.token_jti != jti
        or type(evidence.checked_at) is not int
        or not (current - _MAX_EVIDENCE_AGE <= evidence.checked_at <= current + 1)
        or evidence.account_active is not True
        or evidence.session_active is not True
        or evidence.token_active is not True
    ):
        _reject()
    return subject
