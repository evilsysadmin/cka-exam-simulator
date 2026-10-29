"""Loopback-only developer preview API. Never deploy to an untrusted network."""
import os
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .catalog import load_bank
from .kube import LabError, execute_dev_kubectl, grade, setup

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


@app.post("/api/dev/scenarios/{slug}/setup")
def setup_scenario(slug: str, request: Request):
    dev_only(request)
    scenario = scenario_or_404(slug)
    try:
        setup(scenario)
        return {"status": "created", "namespace": scenario.namespace}
    except LabError as exc:
        raise HTTPException(503, detail=str(exc)) from exc


@app.post("/api/dev/scenarios/{slug}/grade")
def grade_scenario(slug: str, request: Request):
    dev_only(request)
    try:
        return grade(scenario_or_404(slug))
    except LabError as exc:
        raise HTTPException(503, detail=str(exc)) from exc


class Command(BaseModel):
    command: str = Field(min_length=1, max_length=600)


@app.post("/api/dev/terminal/command")
def run_kubectl(data: Command, request: Request):
    dev_only(request)
    try:
        return {"output": execute_dev_kubectl(data.command)}
    except LabError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
