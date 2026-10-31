"""Fixed TLS transport for live CKA session freshness. No browser credentials."""
from __future__ import annotations

import json
import re
import ssl
import time

import httpx

from .identity_freshness import SessionFreshnessEvidence
from .identity_poc import CKA_AUDIENCE, STAGING_ISSUER, _decode, _json_object, _reject


FRESHNESS_URL = STAGING_ISSUER + ":9443/api/identity/freshness"
IDENTITY_HOST = "id-staging.shadowops.dpdns.org"


def private_identity_tls(ca_pem: str) -> ssl.SSLContext:
    # Trust only the independently provisioned public staging CA. The fixed
    # internal DNS alias still requires the real Identity hostname/SNI.
    if not isinstance(ca_pem, str) or "PRIVATE KEY" in ca_pem:
        raise ValueError("Public Identity CA required")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=ca_pem)
    return context


class IdentityFreshnessClient:
    def __init__(self, credential: str, *, transport: httpx.BaseTransport | None = None,
                 tls_context: ssl.SSLContext | None = None):
        if not isinstance(credential, str) or not re.fullmatch(r"soc1_[A-Za-z0-9_-]{43}", credential):
            _reject()
        if len(_decode(credential[5:], 32)) != 32:
            _reject()
        self._credential = credential
        self._transport = transport
        self._tls = tls_context if tls_context is not None else True

    def __call__(self, subject: str, version: int, jti: str) -> SessionFreshnessEvidence:
        # A new client prevents a response cookie from becoming authority on a
        # later call. Ignore proxy environment; never follow redirects or retry.
        started = time.monotonic()
        with httpx.Client(timeout=2.0, follow_redirects=False, trust_env=False,
                          transport=self._transport, verify=self._tls) as client:
            with client.stream("POST", FRESHNESS_URL,
                               headers={"Host": IDENTITY_HOST, "Authorization": "Bearer " + self._credential,
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
