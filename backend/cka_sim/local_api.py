"""Single-workstation app. No central Identity, cloud credentials or Docker socket."""
import hmac
import os
from pathlib import Path
import secrets
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from .lab_api import create_lab_app
from .lab_controller import LabController
from .catalog import load_bank
from .lease_process import LeaseProcess
from .leased_kube import LeasedCluster
from .lab_editor import NanoEditors

OWNER = "f854a721-0c21-419a-95e8-c059fe7d9398"

def create_local_app(*, leases, cluster, bank, build, static, editors=None):
    # Regenerated each server start; never persisted or sent to another origin.
    nonce = secrets.token_urlsafe(32)
    def authenticate(token):
        if type(token) is not str or not hmac.compare_digest(token, nonce):
            raise ValueError("Local session required")
        return OWNER
    controller = LabController(leases=leases, cluster=cluster, bank=bank,
        authenticate=authenticate, pilot_subject=OWNER, editors=editors)
    def close():
        if editors:
            editors.close()
        leases.close()
    app = create_lab_app(controller=controller, build=build, close=close, local=True)
    @app.post("/api/local/token")
    async def token(request: Request):
        # Origin/Host/peer/content type are checked by the common boundary.
        # A fixed tiny body prevents cross-site forms and body-based exhaustion.
        if request.headers.get("content-length") != "2" or await request.body() != b"{}":
            return JSONResponse({"detail":"Request denied"},status_code=403)
        return {"token": nonce}
    @app.get("/release.json")
    def release():
        return {"application":"cka-exam-simulator", "mode":"local", "build":build}
    app.mount("/", StaticFiles(directory=static,html=True),name="local-ui")
    return app

def configured_app():
    journal = Path("/state/journal")
    journal.mkdir(mode=0o700, parents=True, exist_ok=True)
    leases = LeaseProcess(journal=journal, bank=Path("/app/scenarios"))
    try:
        return create_local_app(leases=leases,
            cluster=LeasedCluster(context="kind-cka-local"),bank=load_bank(),
            build=os.environ["CKA_LAB_BUILD_SHA"],static=Path("/app/web"),editors=NanoEditors())
    except Exception:
        leases.close()
        raise
