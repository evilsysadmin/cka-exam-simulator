"""Ollama generates DSL data, never scripts, manifests or graders."""
import os
from urllib.parse import urlsplit
import httpx
from .models import Scenario

SYSTEM = """You design ORIGINAL Kubernetes CKA practice labs.
Return ONLY a valid JSON object following the exact provided schema.
Never include commands, YAML, Kubernetes manifests, URLs, credentials, scripts or grading logic.
deployment_scale: replicas must differ, selectors equal.
service_selector: selectors must differ, replicas equal.
Namespace must start with cka-. All object names must be DNS labels.
Story in Spanish, short, neutral, realistic and without a solution.
"""


class GenerationError(RuntimeError):
    pass


def generate(*, model: str, scenario_type: str = "deployment_scale",
             base_url: str | None = None, transport: httpx.BaseTransport | None = None) -> Scenario:
    if scenario_type not in ("deployment_scale", "service_selector"):
        raise ValueError("Unsupported scenario type")
    if not model or len(model) > 120:
        raise ValueError("Invalid model")
    url = (base_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
    parts = urlsplit(url)
    if (parts.scheme != "http" or parts.hostname not in ("localhost", "127.0.0.1", "::1")
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/")):
        raise GenerationError("Ollama must be accessed through a local loopback URL")
    payload = {
        "model": model, "system": SYSTEM,
        "prompt": f"Generate one varied CKA lab of type {scenario_type}.",
        "format": Scenario.model_json_schema(), "stream": False,
        "options": {"temperature": 0.6},
    }
    try:
        with httpx.Client(timeout=120, transport=transport, trust_env=False) as client:
            response = client.post(url + "/api/generate", json=payload)
            response.raise_for_status()
            scenario = Scenario.model_validate_json(response.json()["response"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise GenerationError(f"Ollama response rejected: {exc}") from exc
    if scenario.scenario_type != scenario_type:
        raise GenerationError("Ollama returned another scenario type")
    return scenario
