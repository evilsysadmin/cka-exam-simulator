"""Loopback-only developer preview API. Never deploy to an untrusted network."""
import os
import stat
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .catalog import load_bank
from .identity_poc import IdentityPoCError, verify_staging_cka_token
from .kube import LabError, execute_dev_kubectl, grade, setup
from .session import SessionError, get_session, require_session, start_session

app = FastAPI(title="CKA Simulator", version="0.2.0")


def dev_only(request: Request) -> None:
    peer = request.client.host if request.client else ""
    if (os.getenv("CKA_ENABLE_LOCAL_LAB", "0") != "1" or
            peer not in ("127.0.0.1", "::1", "testclient") or
            request.headers.get("X-CKA-Lab-Request") != "1"):
        raise HTTPException(403, detail="Local dev lab features disabled")


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": "local-development"}


@app.get("/api/dev/identity/poc/me")
def local_identity_poc(request: Request):
    """Opt-in loopback-only cryptographic handshake. No user session or lab grant."""
    peer = request.client.host if request.client else ""
    if (os.getenv("CKA_IDENTITY_POC_ENABLED") != "1"
            or peer not in ("127.0.0.1", "::1", "testclient")
            or request.headers.get("X-CKA-Identity-PoC") != "1"):
        raise HTTPException(404, detail="Identity PoC disabled")

    # The public JWKS file must be provisioned explicitly by the local
    # developer, never selected by a request parameter or remote JWT header.
    filename = os.getenv("CKA_IDENTITY_POC_JWKS_FILE", "")
    if not filename or not Path(filename).is_absolute():
        raise HTTPException(503, detail="Identity PoC trust keys unavailable")
    try:
        info = os.lstat(filename)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16_384:
            raise OSError("unsafe JWKS file")
        with open(filename, "rb") as source:
            jwks = source.read(16_385)
    except OSError:
        raise HTTPException(503, detail="Identity PoC trust keys unavailable") from None

    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 4110:
        raise HTTPException(401, detail="Staging identity token rejected")
    try:
        subject = verify_staging_cka_token(authorization[7:], jwks=jwks)
    except IdentityPoCError:
        raise HTTPException(401, detail="Staging identity token rejected") from None

    return {"sub": subject, "aud": "cka-simulator", "status": "signature-only-poc"}


@app.get("/api/scenarios")
def list_scenarios():
    return [s.candidate_view() for s in load_bank().values()]


@app.get("/api/scenarios/{slug}")
def get_scenario(slug: str):
    scenario = load_bank().get(slug)
    if scenario is None:
        raise HTTPException(404, "Unknown scenario")
    return scenario.candidate_view()


def scenario_or_404(slug: str):
    scenario = load_bank().get(slug)
    if scenario is None:
        raise HTTPException(404, "Unknown scenario")
    return scenario


def checked_session(*, slug: str | None = None, active: bool = False) -> dict:
    try:
        return require_session(slug=slug, active=active)
    except SessionError as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@app.get("/api/dev/session")
def local_session(request: Request):
    dev_only(request)
    return {"session": get_session()}


@app.post("/api/dev/scenarios/{slug}/setup")
def setup_scenario(slug: str, request: Request):
    dev_only(request)
    scenario = scenario_or_404(slug)
    try:
        setup(scenario)
        return {"status": "created", "namespace": scenario.namespace,
                "session": start_session(scenario.slug, scenario.minutes)}
    except LabError as exc:
        raise HTTPException(503, detail=str(exc)) from exc


@app.post("/api/dev/scenarios/{slug}/grade")
def grade_scenario(slug: str, request: Request):
    dev_only(request)
    checked_session(slug=slug)
    try:
        return grade(scenario_or_404(slug))
    except LabError as exc:
        raise HTTPException(503, detail=str(exc)) from exc


class Command(BaseModel):
    command: str = Field(min_length=1, max_length=600)


@app.post("/api/dev/terminal/command")
def run_kubectl(data: Command, request: Request):
    dev_only(request)
    checked_session(active=True)
    try:
        return {"output": execute_dev_kubectl(data.command)}
    except LabError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
