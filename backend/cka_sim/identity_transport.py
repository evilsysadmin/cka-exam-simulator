"""Fixed TLS transport for live CKA session freshness. No browser credentials."""
from __future__ import annotations

import json
import re
import time

import httpx

from .identity_freshness import SessionFreshnessEvidence
from .identity_poc import CKA_AUDIENCE, STAGING_ISSUER, _decode, _json_object, _reject


class IdentityFreshnessClient:
    def __init__(self, credential: str, *, transport: httpx.BaseTransport | None = None):
        if not isinstance(credential, str) or not re.fullmatch(r"soc1_[A-Za-z0-9_-]{43}", credential):
            _reject()
        if len(_decode(credential[5:], 32)) != 32:
            _reject()
        self._credential = credential
        self._transport = transport

    def __call__(self, subject: str, version: int, jti: str) -> SessionFreshnessEvidence:
        # A new client prevents a response cookie from becoming authority on a
        # later call. Ignore proxy environment; never follow redirects or retry.
        started = time.monotonic()
        with httpx.Client(timeout=2.0, follow_redirects=False, trust_env=False,
                          transport=self._transport) as client:
            with client.stream("POST", STAGING_ISSUER + "/api/identity/freshness",
                               headers={"Authorization": "Bearer " + self._credential,
                                        "Content-Type": "application/json",
                                        "Accept-Encoding": "identity"},
                               content=json.dumps({"subject": subject,
                                                   "session_version": version,
                                                   "token_jti": jti}).encode()) as response:
                if (response.status_code != 200 or "set-cookie" in response.headers
                        or response.headers.get("content-type", "").split(";")[0].strip() != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"):
                    _reject()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 4096 or time.monotonic() - started > 2.0:
                        _reject()
        if time.monotonic() - started > 2.0:
            _reject()
        data = _json_object(bytes(raw))
        fields = set(SessionFreshnessEvidence.__dataclass_fields__)
        if set(data) != fields | {"audience"} or data.pop("audience") != CKA_AUDIENCE:
            _reject()
        return SessionFreshnessEvidence(**data)
