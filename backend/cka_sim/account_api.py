"""Staging account boundary; deliberately has no runner/terminal/factory imports."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .identity_freshness import verify_current_staging_cka_session
from .identity_poc import CKA_AUDIENCE, STAGING_ISSUER, IdentityPoCError, _json_object, _keys
from .identity_transport import IdentityFreshnessClient, private_identity_tls

ORIGIN = "https://staging.cka.shadowops.dpdns.org"
HOST = "cka-api-staging.shadowops.dpdns.org"
CONFIG = Path("/run/secrets/cka-account-staging.json")


class _FreshnessBudget:
    """Bound upstream work after signature verification; one process per pilot."""
    def __init__(self, check):
        self.check = check
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(4)
        self.start = time.monotonic()
        self.counts = {}
        self.total = 0

    def __call__(self, subject, version, jti):
        with self.lock:
            if time.monotonic() - self.start >= 60:
                self.start, self.total, self.counts = time.monotonic(), 0, {}
            count = self.counts.get(subject, 0)
            if self.total >= 60 or count >= 12:
                raise IdentityPoCError("Account check limited")
            self.total += 1
            self.counts[subject] = count + 1
        if not self.slots.acquire(blocking=False):
            raise IdentityPoCError("Account check busy")
        try:
            return self.check(subject, version, jti)
        finally:
            self.slots.release()


def create_account_app(*, jwks: bytes, credential: str, build: str,
                       transport=None, tls_context=None) -> FastAPI:
    _keys(jwks)
    if not re.fullmatch("[a-f0-9]{40}", build):
        raise ValueError("Immutable account source required")
    freshness = _FreshnessBudget(IdentityFreshnessClient(credential, transport=transport, tls_context=tls_context))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        origins = request.headers.getlist("origin")
        if (request.headers.get("host") != HOST or request.url.query
                or request.headers.getlist("cookie") or len(origins) > 1
                or (origins and origins != [ORIGIN])):
            response = JSONResponse({"detail": "Request denied"}, status_code=403)
        elif request.method == "OPTIONS":
            if (request.url.path != "/api/account/me" or origins != [ORIGIN]
                    or request.headers.get("access-control-request-method") != "GET"
                    or request.headers.get("access-control-request-headers", "").lower().strip() != "authorization"):
                response = JSONResponse({"detail": "Preflight denied"}, status_code=403)
            else:
                response = JSONResponse(None, status_code=200)
                response.headers["Access-Control-Allow-Methods"] = "GET"
                response.headers["Access-Control-Allow-Headers"] = "Authorization"
        else:
            response = await call_next(request)
        if origins == [ORIGIN]:
            response.headers["Access-Control-Allow-Origin"] = ORIGIN
            response.headers["Vary"] = "Origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/livez")
    @app.get("/readyz")
    def ready():
        return {"status": "ok", "mode": "account-only-staging"}

    @app.get("/api/account/release")
    def release():
        return {"application": "cka-exam-simulator", "build": build,
                "mode": "account-only-staging"}

    @app.get("/api/account/me")
    def me(request: Request):
        headers = request.headers.getlist("authorization")
        if (len(headers) != 1 or not headers[0].startswith("Bearer ")
                or request.headers.getlist("origin") != [ORIGIN]):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        try:
            subject = verify_current_staging_cka_session(
                headers[0][7:], jwks=jwks, check_freshness=freshness)
        except Exception:
            # Never log a bearer, SOC, claims, database state or transport error.
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return {"subject": subject, "issuer": STAGING_ISSUER,
                "audience": CKA_AUDIENCE, "labs_enabled": False}

    return app


def configured_app() -> FastAPI:
    """Only the fixed private runtime file can enable this independent service."""
    try:
        if CONFIG.is_symlink() or not CONFIG.is_file() or CONFIG.stat().st_size > 24576:
            raise ValueError()
        config = _json_object(CONFIG.read_bytes())
        if (set(config) != {"environment", "jwks", "identity_ca_pem", "service_credential", "build"}
                or config["environment"] != "staging"
                or config["build"] != os.environ.get("CKA_ACCOUNT_BUILD_SHA")):
            raise ValueError()
        return create_account_app(jwks=json.dumps(config["jwks"]).encode(),
                                  credential=config["service_credential"], build=config["build"],
                                  tls_context=private_identity_tls(config["identity_ca_pem"]))
    except Exception:
        raise RuntimeError("Invalid private CKA staging account configuration") from None
