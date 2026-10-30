"""Signed JWT -> actual HTTP adapter -> exact authority evidence -> account only."""
from dataclasses import asdict
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from cka_sim.account_api import HOST, ORIGIN, create_account_app
from cka_sim.identity_freshness import verify_current_staging_cka_session
from cka_sim.identity_poc import IdentityPoCError, STAGING_ISSUER
from test_identity_freshness import material, signed, SUBJECT, JTI

SOC = "soc1_" + "A" * 43  # Canonical synthetic zero bytes, never an operator key.


def client(material, handler):
    _, jwks, _, _, _ = material
    app = create_account_app(jwks=jwks, credential=SOC, build="a" * 40,
                             transport=httpx.MockTransport(handler))
    return TestClient(app, base_url="https://" + HOST)


def auth(material):
    key, _, claims, _, _ = material
    return {"Origin": ORIGIN, "Authorization": "Bearer " + signed(key, claims)}


def evidence(material, **changes):
    data = {**asdict(material[3]), "audience": "cka-simulator", **changes}
    return httpx.Response(200, json=data)


def test_account_checks_live_authority_every_time_and_never_grants_a_lab(material):
    calls = []
    def authority(request):
        calls.append(request)
        assert str(request.url) == STAGING_ISSUER + "/api/identity/freshness"
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer " + SOC
        assert "origin" not in request.headers and "cookie" not in request.headers
        assert json.loads(request.content) == {"subject": SUBJECT, "session_version": 3, "token_jti": JTI}
        return evidence(material)
    api = client(material, authority)
    for _ in range(2):
        r = api.get("/api/account/me", headers=auth(material))
        assert r.status_code == 200
        assert r.json() == {"subject": SUBJECT, "issuer": STAGING_ISSUER,
                            "audience": "cka-simulator", "labs_enabled": False}
        assert r.headers["cache-control"] == "no-store"
        assert "set-cookie" not in r.headers
    assert len(calls) == 2
    # This process doesn't mount the developer API or session lease controller.
    for path in ["/api/dev/lab/start", "/api/scenarios", "/api/sessions", "/docs", "/openapi.json"]:
        assert api.get(path, headers=auth(material)).status_code == 404
    assert len(calls) == 2


@pytest.mark.parametrize("changes", [
    {"audience": "chronicles"}, {"issuer": "https://id.shadowops.dpdns.org"},
    {"session_active": False}, {"token_active": False}, {"account_active": False},
    {"session_version": True}, {"session_version": 4}, {"token_jti": "other"},
    {"subject": "22222222-2222-4222-8222-222222222222"},
    {"checked_at": 1}, {"account_active": 1}, {"extra": "untrusted"},
])
def test_wrong_audience_revoked_stale_or_malformed_service_evidence_denies(material, changes):
    api = client(material, lambda r: evidence(material, **changes))
    assert api.get("/api/account/me", headers=auth(material)).status_code == 401


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 429, 500, 503])
def test_authority_errors_and_redirects_deny_without_following(material, status):
    calls = []
    def failed(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://other.invalid/"})
    assert client(material, failed).get("/api/account/me", headers=auth(material)).status_code == 401
    assert len(calls) == 1


def test_timeout_oversized_duplicate_json_and_cookie_injection_deny(material):
    def timeout(_):
        raise httpx.ReadTimeout("synthetic timeout")
    malformed = [
        timeout,
        lambda _: httpx.Response(200, content=b"x" * 4097, headers={"Content-Type": "application/json"}),
        lambda _: httpx.Response(200, content=b'{"audience":"cka-simulator","audience":"chronicles"}', headers={"Content-Type": "application/json"}),
        lambda _: httpx.Response(200, json={**asdict(material[3]), "audience": "cka-simulator"}, headers={"Set-Cookie": "injected=1"}),
    ]
    for handler in malformed:
        assert client(material, handler).get("/api/account/me", headers=auth(material)).status_code == 401


def test_bad_signature_never_contacts_authority_and_revocation_is_observed(material):
    calls = []
    def authority(r):
        calls.append(r)
        return evidence(material) if len(calls) == 1 else httpx.Response(401)
    api = client(material, authority)
    bad = {**auth(material), "Authorization": auth(material)["Authorization"] + "bad"}
    assert api.get("/api/account/me", headers=bad).status_code == 401
    assert not calls
    assert api.get("/api/account/me", headers=auth(material)).status_code == 200
    assert api.get("/api/account/me", headers=auth(material)).status_code == 401
    assert material[2]["exp"] > int(time.time())


def test_browser_origin_cookie_query_and_duplicate_authorization_guards(material):
    def never(_):
        pytest.fail("untrusted browser request reached Identity")
    api = client(material, never)
    for headers, path in [
        ({**auth(material), "Origin": "https://cka.shadowops.dpdns.org"}, "/api/account/me"),
        ({**auth(material), "Cookie": "session=untrusted"}, "/api/account/me"),
        (auth(material), "/api/account/me?token=forbidden"),
        ({**auth(material), "Host": "other.invalid"}, "/api/account/me"),
    ]:
        assert api.get(path, headers=headers).status_code == 403
    assert api.get("/api/account/me", headers=[("Origin", ORIGIN), ("Authorization", "Bearer a"), ("Authorization", "Bearer b")]).status_code == 401
    assert api.get("/api/account/me", headers={"Origin": ORIGIN}).status_code == 401
    for requested, status in [("authorization", 200), ("authorization,x-request-id", 403)]:
        r = api.options("/api/account/me", headers={"Origin": ORIGIN,
            "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": requested})
        assert r.status_code == status
    assert api.options("/api/account/me", headers={"Origin": "https://other.invalid"}).headers.get("Access-Control-Allow-Origin") is None


def test_expiry_during_authority_check_cannot_extend_token(material, monkeypatch):
    key, jwks, claims, proof, now = material
    clock = [now]
    monkeypatch.setattr("cka_sim.identity_freshness.time.time", lambda: clock[0])
    def expired(*_):
        clock[0] = claims["exp"]
        return proof
    with pytest.raises(IdentityPoCError):
        verify_current_staging_cka_session(signed(key, claims), jwks=jwks, check_freshness=expired)


def test_verified_subject_budget_bounds_authority_load(material):
    calls = []
    def authority(request):
        calls.append(request)
        return evidence(material)
    api = client(material, authority)
    for _ in range(12):
        assert api.get("/api/account/me", headers=auth(material)).status_code == 200
    assert api.get("/api/account/me", headers=auth(material)).status_code == 401
    assert len(calls) == 12
