"""Contract tests for the entirely offline, staging-only ShadowOps Identity PoC."""
import base64
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from cka_sim.api import app
from cka_sim.identity_poc import (
    CKA_AUDIENCE, STAGING_ISSUER, IdentityPoCError, verify_staging_cka_token,
)


SUBJECT = "11111111-1111-4111-8111-111111111111"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwk(key: Ed25519PrivateKey, kid: str = "identity-test-key") -> dict:
    return {
        "kty": "OKP", "crv": "Ed25519", "use": "sig", "alg": "EdDSA",
        "kid": kid,
        "x": b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)),
    }


def jwks(*keys: dict) -> bytes:
    return json.dumps({"keys": keys}, separators=(",", ":")).encode()


def signed(
    key: Ed25519PrivateKey,
    claims: dict,
    *,
    header: dict | None = None,
    raw_claims: bytes | None = None,
) -> str:
    header = header or {"alg": "EdDSA", "typ": "JWT", "kid": "identity-test-key"}
    head = b64(json.dumps(header, separators=(",", ":")).encode())
    body = b64(raw_claims if raw_claims is not None else json.dumps(claims, separators=(",", ":")).encode())
    content = head + "." + body
    return content + "." + b64(key.sign(content.encode()))


@pytest.fixture
def material():
    private = Ed25519PrivateKey.generate()
    now = int(time.time())
    claims = {
        "iss": STAGING_ISSUER, "aud": CKA_AUDIENCE, "sub": SUBJECT,
        "iat": now - 10, "nbf": now - 10, "exp": now + 300,
    }
    return private, claims, jwks(jwk(private))


def test_valid_staging_cka_ed25519_contract(material):
    private, claims, trusted = material
    assert verify_staging_cka_token(signed(private, claims), jwks=trusted) == SUBJECT
    rotated = jwks(jwk(Ed25519PrivateKey.generate(), "identity-rotated"), jwk(private))
    assert verify_staging_cka_token(signed(private, claims), jwks=rotated) == SUBJECT


@pytest.mark.parametrize(("field", "value"), [
    ("iss", "https://id.shadowops.dpdns.org"),
    ("iss", "https://api-staging.chess-studio.shadowops.dpdns.org"),
    ("aud", "chronicles"),
    ("aud", "shadowops-identity-probe"),
    ("aud", ["cka-simulator"]),
    ("sub", "probe:" + "a" * 32),
    ("sub", "test-user"),
    ("sub", str(uuid.uuid1())),
    ("exp", 0),
    ("exp", True),
    ("iat", True),
    ("nbf", "today"),
])
def test_invalid_claims_rejected(material, field, value):
    private, claims, trusted = material
    claims[field] = value
    with pytest.raises(IdentityPoCError):
        verify_staging_cka_token(signed(private, claims), jwks=trusted)


def test_expired_future_or_overlong_tokens_rejected(material):
    private, claims, trusted = material
    now = int(time.time())
    for edits in (
        {"exp": now},
        {"nbf": now + 90},
        {"iat": now + 90, "nbf": now + 90},
        {"iat": now - 4000, "nbf": now - 4000, "exp": now + 10},
        {"iat": now + 1, "nbf": now - 30},
    ):
        attempt = {**claims, **edits}
        with pytest.raises(IdentityPoCError):
            verify_staging_cka_token(signed(private, attempt), jwks=trusted, now=now)


def test_wrong_kid_algorithm_signature_and_trust_rejected(material):
    private, claims, trusted = material
    token = signed(private, claims)
    h, p, signature = token.split(".")
    attacker = Ed25519PrivateKey.generate()
    altered = signed(attacker, claims)
    wrong_header = {"alg": "none", "typ": "JWT", "kid": "identity-test-key"}
    for candidate, keys in (
        (altered, trusted),
        (h + "." + p + "." + b64(b"x" * 64), trusted),
        (signed(private, claims, header=wrong_header), trusted),
        (signed(private, claims, header={"alg": "EdDSA", "typ": "JWT", "kid": "unknown"}), trusted),
        (token, jwks(jwk(attacker))),
        ("abc.def.ghi", trusted),
        ("x" * 4097, trusted),
        (token + "=", trusted),
    ):
        with pytest.raises(IdentityPoCError):
            verify_staging_cka_token(candidate, jwks=keys)
    assert len(signature) > 0


def test_jwks_and_json_ambiguity_fails_closed(material):
    private, claims, trusted = material
    token = signed(private, claims)
    duplicate_iss = (
        b'{"iss":"' + STAGING_ISSUER.encode() + b'","iss":"' +
        STAGING_ISSUER.encode() + b'","aud":"cka-simulator","sub":"' +
        SUBJECT.encode() + b'","iat":1,"nbf":1,"exp":9999999999}'
    )
    for attempt in (
        signed(private, claims, raw_claims=duplicate_iss),
        signed(private, claims, header={"alg": "EdDSA", "typ": "JWT",
                                        "kid": "identity-test-key", "jku": "https://evil.test/jwks"}),
    ):
        with pytest.raises(IdentityPoCError):
            verify_staging_cka_token(attempt, jwks=trusted)
    for keys in (
        b'{"keys":[]}',
        b'{"keys":"not-array"}',
        b'{"keys":[], "keys":[]}',
        jwks(jwk(private), jwk(private)),
        jwks({**jwk(private), "use": "enc"}),
        jwks({**jwk(private), "x": "not-base64"}),
        jwks({**jwk(private), "x": b64(b"short")}),
        trusted * 200,
    ):
        with pytest.raises(IdentityPoCError):
            verify_staging_cka_token(token, jwks=keys)


def test_poc_endpoint_is_off_by_default_and_does_not_enable_lab(material, monkeypatch, tmp_path):
    private, claims, trusted = material
    monkeypatch.delenv("CKA_IDENTITY_POC_ENABLED", raising=False)
    token = signed(private, claims)
    path = tmp_path / "trusted-public.jwks"
    path.write_bytes(trusted)
    monkeypatch.setenv("CKA_IDENTITY_POC_JWKS_FILE", str(path))
    client = TestClient(app)
    headers = {"X-CKA-Identity-PoC": "1", "Authorization": "Bearer " + token}
    assert client.get("/api/dev/identity/poc/me", headers=headers).status_code == 404
    monkeypatch.setenv("CKA_IDENTITY_POC_ENABLED", "1")
    assert client.get("/api/dev/identity/poc/me", headers={"Authorization": "Bearer " + token}).status_code == 404
    response = client.get("/api/dev/identity/poc/me", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"sub": SUBJECT, "aud": CKA_AUDIENCE, "status": "signature-only-poc"}
    bad = client.get("/api/dev/identity/poc/me",
                     headers={**headers, "Authorization": "Bearer " + signed(Ed25519PrivateKey.generate(), claims)})
    assert bad.status_code == 401
    assert "token" not in json.dumps(bad.json()).lower()
    # The PoC flag never activates the existing developer Kubernetes endpoints.
    assert client.post("/api/dev/scenarios/scale-payment-api/setup",
                       headers={"X-CKA-Lab-Request": "1"}).status_code == 403
    monkeypatch.setenv("CKA_IDENTITY_POC_JWKS_FILE", str(tmp_path / "missing.jwks"))
    assert client.get("/api/dev/identity/poc/me", headers=headers).status_code == 503
