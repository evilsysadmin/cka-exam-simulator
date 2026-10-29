"""No authorization from a signed JWT without fresh server-side evidence."""
import base64
from dataclasses import replace
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from cka_sim.identity_poc import (
    CKA_AUDIENCE,
    STAGING_ISSUER,
    IdentityPoCError,
    verify_staging_cka_token,
)
from cka_sim.identity_freshness import (
    SessionFreshnessEvidence,
    verify_current_staging_cka_session,
)

SUBJECT = "11111111-1111-4111-8111-111111111111"
JTI = base64.urlsafe_b64encode(b"0123456789abcdef").rstrip(b"=").decode()


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def signed(key, claims):
    header = b64(b'{"alg":"EdDSA","typ":"JWT","kid":"identity-test"}')
    body = b64(json.dumps(claims, separators=(",", ":")).encode())
    message = header + "." + body
    return message + "." + b64(key.sign(message.encode()))


@pytest.fixture
def material():
    now = int(time.time())
    key = Ed25519PrivateKey.generate()
    public = b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    keys = json.dumps({"keys": [{
        "kty": "OKP", "crv": "Ed25519", "alg": "EdDSA",
        "use": "sig", "kid": "identity-test", "x": public,
    }]}).encode()
    claims = {
        "iss": STAGING_ISSUER, "aud": CKA_AUDIENCE, "sub": SUBJECT,
        "iat": now - 5, "nbf": now - 5, "exp": now + 115,
        "sv": 3, "jti": JTI,
    }
    evidence = SessionFreshnessEvidence(
        issuer=STAGING_ISSUER, subject=SUBJECT, session_version=3,
        token_jti=JTI, checked_at=now, account_active=True,
        session_active=True, token_active=True,
    )
    return key, keys, claims, evidence, now


def test_only_exact_fresh_authoritative_session_and_token_pass(material):
    key, keys, claims, evidence, now = material
    token = signed(key, claims)
    calls = []
    def check(subject, version, jti):
        calls.append((subject, version, jti))
        return evidence

    assert verify_current_staging_cka_session(
        token, jwks=keys, check_freshness=check, now=now,
    ) == SUBJECT
    assert verify_current_staging_cka_session(
        token, jwks=keys, check_freshness=check, now=now,
    ) == SUBJECT
    assert calls == [(SUBJECT, 3, JTI), (SUBJECT, 3, JTI)]
    # The older signature-only PoC function is NOT an authorization check.
    assert verify_staging_cka_token(token, jwks=keys, now=now) == SUBJECT


def test_fail_closed_without_provider_or_on_provider_failure(material):
    key, keys, claims, evidence, now = material
    token = signed(key, claims)
    def failed(_sub, _sv, _jti):
        raise TimeoutError("synthetic Identity unavailable")
    for checker in (None, failed, lambda *args: None,
                    lambda *args: True, lambda *args: {
                        "issuer": STAGING_ISSUER, "account_active": True,
                    }):
        with pytest.raises(IdentityPoCError):
            verify_current_staging_cka_session(
                token, jwks=keys, check_freshness=checker, now=now,
            )


@pytest.mark.parametrize("field,value", [
    ("account_active", False),
    ("session_active", False),
    ("token_active", False),
    ("account_active", 1),
    ("session_active", "true"),
    ("token_active", None),
    ("issuer", "https://id.shadowops.dpdns.org"),
    ("subject", "22222222-2222-4222-8222-222222222222"),
    ("session_version", 4),
    ("session_version", True),
    ("token_jti", b64(b"aaaaaaaaaaaaaaaa")),
    ("checked_at", 0),
    ("checked_at", True),
])
def test_reject_false_stale_mismatched_or_untrusted_evidence(material, field, value):
    key, keys, claims, evidence, now = material
    bad = replace(evidence, **{field: value})
    with pytest.raises(IdentityPoCError):
        verify_current_staging_cka_session(
            signed(key, claims), jwks=keys,
            check_freshness=lambda *_: bad, now=now,
        )


def test_reject_evidence_older_than_two_seconds_or_from_future(material):
    key, keys, claims, evidence, now = material
    token = signed(key, claims)
    for timestamp in (now - 3, now + 2):
        with pytest.raises(IdentityPoCError):
            verify_current_staging_cka_session(
                token, jwks=keys,
                check_freshness=lambda *_: replace(
                    evidence, checked_at=timestamp,
                ),
                now=now,
            )


@pytest.mark.parametrize("edit", [
    {"sv": 0}, {"sv": -1}, {"sv": True}, {"sv": "3"},
    {"sv": 2**63}, {"jti": ""}, {"jti": "bad"},
    {"jti": b64(b"short")}, {"jti": True},
    {"aud": "chronicles"}, {"iss": "https://id.shadowops.dpdns.org"},
    {"exp": 9999999999}, {"nbf": 1},
    {"irrelevant_user_role": "superadmin"},
])
def test_reject_invalid_session_claims_before_checking_provider(material, edit):
    key, keys, claims, evidence, now = material
    called = []
    def checker(*args):
        called.append(args)
        return evidence
    with pytest.raises(IdentityPoCError):
        verify_current_staging_cka_session(
            signed(key, {**claims, **edit}),
            jwks=keys, check_freshness=checker, now=now,
        )
    assert called == []


def test_reject_missing_version_and_jti_and_token_lifetime_over_120(material):
    key, keys, claims, evidence, now = material
    attempts = [
        {k: v for k, v in claims.items() if k != "sv"},
        {k: v for k, v in claims.items() if k != "jti"},
        {**claims, "exp": claims["iat"] + 121},
    ]
    for candidate in attempts:
        with pytest.raises(IdentityPoCError):
            verify_current_staging_cka_session(
                signed(key, candidate), jwks=keys,
                check_freshness=lambda *_: evidence, now=now,
            )


def test_signature_only_token_remains_valid_after_revocation_but_freshness_denies(material):
    key, keys, claims, evidence, now = material
    token = signed(key, claims)
    assert verify_staging_cka_token(token, jwks=keys, now=now) == SUBJECT
    state = {"current": evidence}
    def checker(*_):
        return state["current"]
    assert verify_current_staging_cka_session(
        token, jwks=keys, check_freshness=checker, now=now,
    ) == SUBJECT
    state["current"] = replace(evidence, session_active=False, token_active=False)
    with pytest.raises(IdentityPoCError):
        verify_current_staging_cka_session(
            token, jwks=keys, check_freshness=checker, now=now,
        )
    # JWT math did not change: the rejection came from current state.
    assert verify_staging_cka_token(token, jwks=keys, now=now) == SUBJECT


def test_never_calls_provider_before_verifying_signature(material):
    key, keys, claims, evidence, now = material
    forged = signed(Ed25519PrivateKey.generate(), claims)
    called = []
    with pytest.raises(IdentityPoCError):
        verify_current_staging_cka_session(
            forged, jwks=keys,
            check_freshness=lambda *args: called.append(args) or evidence,
            now=now,
        )
    assert called == []

