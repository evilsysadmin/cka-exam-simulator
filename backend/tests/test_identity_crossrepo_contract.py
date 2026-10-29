"""Cross-repository Go Identity -> Python CKA contract test with EPHEMERAL CI keys.

Fixtures are produced during CI by importing the pinned real ShadowOps
Identity Go core. This test never obtains passwords or signing secrets and
never creates a login or Kubernetes entitlement.
"""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cka_sim.api import app
from cka_sim.identity_poc import IdentityPoCError, verify_staging_cka_token


@pytest.fixture(scope="module")
def go_identity_contract():
    filename = os.environ.get("CKA_IDENTITY_CONTRACT_FIXTURE_FILE")
    if not filename:
        pytest.skip("cross-repository Identity Go fixture only available in isolated CI gate")
    values = json.loads(Path(filename).read_text())
    assert set(values) == {
        "jwks", "subject", "valid", "probe",
        "wrong_audience", "wrong_issuer", "expired", "future",
    }
    assert values["subject"] == "11111111-1111-4111-8111-111111111111"
    assert isinstance(values["jwks"], dict)
    return values


def test_actual_go_identity_jwks_compatible_with_python_consumer(go_identity_contract):
    data = go_identity_contract
    trusted_jwks = json.dumps(data["jwks"], separators=(",", ":")).encode()
    subject = verify_staging_cka_token(data["valid"], jwks=trusted_jwks)
    assert subject == data["subject"]


@pytest.mark.parametrize("field", [
    "probe", "wrong_audience", "wrong_issuer", "expired", "future",
])
def test_real_go_identity_key_must_not_override_audience_or_time(go_identity_contract, field):
    data = go_identity_contract
    trusted_jwks = json.dumps(data["jwks"], separators=(",", ":")).encode()
    with pytest.raises(IdentityPoCError):
        verify_staging_cka_token(data[field], jwks=trusted_jwks)


def test_real_go_identity_public_key_cannot_be_replaced(go_identity_contract):
    data = go_identity_contract
    wrong_key = {
        "keys": [{
            "kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig",
            "kid": "identity-forged", "x": data["jwks"]["keys"][0]["x"],
        }],
    }
    with pytest.raises(IdentityPoCError):
        verify_staging_cka_token(
            data["valid"], jwks=json.dumps(wrong_key).encode()
        )


def test_go_identity_signed_token_reaches_local_cka_diagnostic_only(
    go_identity_contract, monkeypatch, tmp_path
):
    data = go_identity_contract
    public_key_file = tmp_path / "identity-public.jwks"
    public_key_file.write_text(json.dumps(data["jwks"], separators=(",", ":")))
    monkeypatch.setenv("CKA_IDENTITY_POC_JWKS_FILE", str(public_key_file))
    monkeypatch.setenv("CKA_IDENTITY_POC_ENABLED", "1")
    monkeypatch.delenv("CKA_ENABLE_LOCAL_LAB", raising=False)
    client = TestClient(app)
    headers = {
        "X-CKA-Identity-PoC": "1",
        "Authorization": "Bearer " + data["valid"],
    }
    resp = client.get("/api/dev/identity/poc/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {
        "sub": data["subject"],
        "aud": "cka-simulator",
        "status": "signature-only-poc",
    }
    for rejected in ("probe", "wrong_audience", "wrong_issuer", "expired", "future"):
        resp = client.get(
            "/api/dev/identity/poc/me",
            headers={**headers, "Authorization": "Bearer " + data[rejected]},
        )
        assert resp.status_code == 401
        assert data[rejected] not in json.dumps(resp.json())

    # Signed identity alone does not enable the developer's kube runner.
    attempt = client.post(
        "/api/dev/scenarios/scale-payment-api/setup",
        headers={"X-CKA-Lab-Request": "1", "Authorization": "Bearer " + data["valid"]},
    )
    assert attempt.status_code == 403
