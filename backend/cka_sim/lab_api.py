"""Independent, opt-in staging pilot API. Never mounts the developer runner."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .candidate_kubectl import CandidateCommandError
from .identity_freshness import verify_current_staging_cka_session
from .identity_poc import _json_object, _keys
from .identity_transport import IdentityFreshnessClient
from .lab_controller import LabBusy, LabController, LabDenied, LabUnavailable

HOST = "cka-labs-staging.shadowops.dpdns.org"
ORIGIN = "https://staging.cka.shadowops.dpdns.org"
CONFIG = Path("/run/secrets/cka-lab-staging.json")
ACTIONS = {"capabilities": set(), "prepare": {"slug"}, "status": {"id"},
           "command": {"id", "command"}, "grade": {"id"}, "finish": {"id"}}


def pilot_authorizer(*, jwks, credential, pilot_subject, transport=None):
    _keys(jwks)
    upstream = IdentityFreshnessClient(credential, transport=transport, public_staging=True)
    lock = threading.Lock()
    started, count = time.monotonic(), 0

    def evidence(subject, version, jti):
        nonlocal started, count
        if subject != pilot_subject:
            raise ValueError()
        with lock:
            if time.monotonic() - started >= 60:
                started, count = time.monotonic(), 0
            if count >= 36:
                raise ValueError()
            count += 1
        return upstream(subject, version, jti)

    def authenticate(token):
        return verify_current_staging_cka_session(token, jwks=jwks, check_freshness=evidence)
    return authenticate


def create_lab_app(*, controller, build, close=None):
    if not re.fullmatch(r"[a-f0-9]{40}", build):
        raise ValueError("Immutable lab source required")

    @asynccontextmanager
    async def lifespan(app):
        stopped = threading.Event()
        app.state.ready = await run_in_threadpool(controller.reap)

        def reconcile():
            while not stopped.wait(3):
                app.state.ready = controller.reap()
        worker = threading.Thread(target=reconcile, name="cka-lab-reaper", daemon=True)
        worker.start()
        try:
            yield
        finally:
            stopped.set()
            await run_in_threadpool(worker.join, 45)
            if close:
                await run_in_threadpool(close)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.ready = False

    @app.middleware("http")
    async def boundary(request, call_next):
        origins = request.headers.getlist("origin")
        path = request.url.path
        action = path.removeprefix("/api/labs/") if path.startswith("/api/labs/") else None
        if (request.headers.getlist("host") != [HOST] or request.url.query
                or request.headers.getlist("cookie") or len(origins) > 1
                or (origins and origins != [ORIGIN])):
            response = JSONResponse({"detail": "Request denied"}, status_code=403)
        elif request.method == "OPTIONS":
            fields = {x.strip().lower() for x in request.headers.get("access-control-request-headers", "").split(",")}
            if (action not in ACTIONS or origins != [ORIGIN]
                    or request.headers.get("access-control-request-method") != "POST"
                    or fields != {"authorization", "content-type"}):
                response = JSONResponse({"detail": "Preflight denied"}, status_code=403)
            else:
                response = JSONResponse(None)
                response.headers["Access-Control-Allow-Methods"] = "POST"
                response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        elif request.method == "POST" and (origins != [ORIGIN]
                or request.headers.getlist("content-type") != ["application/json"]
                or request.headers.getlist("content-encoding")):
            response = JSONResponse({"detail": "Request denied"}, status_code=403)
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
    def live():
        return {"status": "ok", "mode": "restricted-lab-pilot"}

    @app.get("/readyz")
    def ready():
        return JSONResponse({"status": "ok" if app.state.ready else "reconciling"},
                            status_code=200 if app.state.ready else 503)

    @app.get("/api/labs/release")
    def release():
        return {"application": "cka-exam-simulator", "build": build, "mode": "restricted-lab-pilot"}

    @app.post("/api/labs/{action}")
    async def operate(action: str, request: Request):
        if action not in ACTIONS:
            return JSONResponse({"detail": "Operation unavailable"}, status_code=404)
        headers = request.headers.getlist("authorization")
        if (len(headers) != 1 or not headers[0].startswith("Bearer ")
                or not 8 <= len(headers[0]) <= 4103):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        try:
            body = bytearray()
            async with asyncio.timeout(2):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 2048:
                        return JSONResponse({"detail": "Request too large"}, status_code=413)
                    body.extend(chunk)
            data = _json_object(bytes(body))
            if set(data) != ACTIONS[action] or any(type(v) is not str or not v or len(v) > 600 for v in data.values()):
                raise ValueError()
        except (ValueError, TypeError, TimeoutError, RecursionError):
            return JSONResponse({"detail": "Invalid request"}, status_code=400)
        try:
            if not app.state.ready:
                return JSONResponse({"detail": "Laboratorio temporalmente no disponible"}, status_code=503)
            result = await run_in_threadpool(controller.operation, headers[0][7:], action,
                identity=data.get("id"), slug=data.get("slug"), command=data.get("command"))
            return result
        except CandidateCommandError:
            return JSONResponse({"detail": "Comando no permitido en este laboratorio"}, status_code=400)
        except LabDenied:
            return JSONResponse({"detail": "Sesión o acceso al laboratorio no disponible"}, status_code=401)
        except LabBusy:
            return JSONResponse({"detail": "Laboratorio ocupado; termina el intento activo o espera a la limpieza"}, status_code=409)
        except LabUnavailable:
            return JSONResponse({"detail": "Laboratorio temporalmente no disponible"}, status_code=503)

    return app


def configured_app():
    """No default allowlist, automatic enrolment or user-selectable trust URL."""
    from .catalog import load_bank
    from .lease_process import LeaseProcess
    from .leased_kube import LeasedCluster
    leases = None
    try:
        if CONFIG.is_symlink() or not CONFIG.is_file() or CONFIG.stat().st_size > 24576:
            raise ValueError()
        config = _json_object(CONFIG.read_bytes())
        if (set(config) != {"environment", "jwks", "service_credential", "pilot_subject", "build"}
                or config["environment"] != "staging"
                or config["build"] != os.environ.get("CKA_LAB_BUILD_SHA")):
            raise ValueError()
        authenticate = pilot_authorizer(jwks=json.dumps(config["jwks"]).encode(),
            credential=config["service_credential"], pilot_subject=config["pilot_subject"])
        journal = Path("/var/lib/cka-lab/journal")
        journal.mkdir(mode=0o700, parents=True, exist_ok=True)
        bank = load_bank()
        leases = LeaseProcess(journal=journal, bank=Path("/app/scenarios"))
        controller = LabController(leases=leases, cluster=LeasedCluster(), bank=bank,
                                  authenticate=authenticate, pilot_subject=config["pilot_subject"])
        return create_lab_app(controller=controller, build=config["build"], close=leases.close)
    except Exception:
        if leases:
            leases.close()
        raise RuntimeError("Invalid private CKA lab staging configuration") from None
