"""Cloudflare Workers AI provider: remote inference, no Kubernetes access or publication."""
import json
import os
import re

import httpx

from .models import Scenario
from .ollama import GenerationError, SYSTEM

# Only models documented to support Workers AI structured JSON Mode.
DEFAULT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
SUPPORTED_MODELS = frozenset({
    DEFAULT_MODEL,
    "@cf/meta/llama-3.1-8b-instruct",
    "@cf/meta/llama-3-8b-instruct",
})


def generate(*, model: str = DEFAULT_MODEL,
             scenario_type: str = "deployment_scale",
             account_id: str | None = None, api_token: str | None = None,
             transport: httpx.BaseTransport | None = None) -> Scenario:
    """Request bounded structured JSON using Cloudflare's fixed API host.

    Credential values never enter prompts, response data, URLs, or error messages.
    """
    if scenario_type not in ("deployment_scale", "service_selector"):
        raise ValueError("Unsupported scenario type")
    if model not in SUPPORTED_MODELS:
        raise GenerationError("Workers AI model is not in the JSON Mode allowlist")
    account = account_id if account_id is not None else os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    token = api_token if api_token is not None else os.getenv("CLOUDFLARE_API_TOKEN", "")
    if not re.fullmatch(r"[A-Fa-f0-9]{32}", account or ""):
        raise GenerationError("Workers AI requires a valid CLOUDFLARE_ACCOUNT_ID")
    if not token or not token.strip():
        raise GenerationError("Workers AI requires CLOUDFLARE_API_TOKEN")

    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Generate one varied CKA lab of type {scenario_type}."},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": Scenario.model_json_schema(),
        },
        "temperature": 0.6,
        "max_tokens": 1024,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=60, transport=transport, trust_env=False,
                          follow_redirects=False) as client:
            response = client.post(url, json=payload, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            })
            response.raise_for_status()
            if len(response.content) > 65_536:
                raise GenerationError("Workers AI response exceeds size limit")
            envelope = response.json()
        if not isinstance(envelope, dict) or envelope.get("success") is not True:
            raise GenerationError("Workers AI returned a failed response")
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise GenerationError("Workers AI result is missing")
        raw = result.get("response")
        if isinstance(raw, dict):
            raw = json.dumps(raw, ensure_ascii=False)
        if not isinstance(raw, str):
            raise GenerationError("Workers AI result is not structured JSON")
        scenario = Scenario.model_validate_json(raw)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        # Avoid leaking provider response bodies and authorization headers.
        raise GenerationError("Workers AI request or schema validation failed") from None
    if scenario.scenario_type != scenario_type:
        raise GenerationError("Workers AI returned another scenario type")
    return scenario
