"""Reproducible offline preflight of untrusted scenario proposals.

This is not a Kubernetes smoke test and NEVER authorizes publication.
Output deliberately excludes model-generated story/title and raw parser errors.
"""
from hashlib import sha256
import re
import unicodedata
from pathlib import Path

import yaml
from pydantic import ValidationError

from .catalog import PROPOSALS, load_bank, load_proposal

MAX_BATCH_FILES = 200


def _story_fingerprint(scenario) -> tuple[str, str]:
    # An exact normalized narrative is a duplicate; this is NOT semantic similarity.
    normalized = unicodedata.normalize("NFKC", scenario.story).casefold()
    return (scenario.scenario_type, " ".join(re.findall(r"\w+", normalized)))


def audit_proposals(directory: Path = PROPOSALS) -> dict:
    """Audit an entire quarantine without executing Kubernetes or invoking an LLM."""
    if directory.is_symlink() or not directory.is_dir():
        return {"passed": False, "total": 0, "accepted": 0, "rejected": 0,
                "error": "missing-or-unsafe-directory", "entries": []}
    paths = sorted(directory.iterdir(), key=lambda p: p.name)
    if not paths:
        return {"passed": False, "total": 0, "accepted": 0, "rejected": 0,
                "error": "no-proposals", "entries": []}
    if len(paths) > MAX_BATCH_FILES:
        return {"passed": False, "total": len(paths), "accepted": 0,
                "rejected": len(paths), "error": "too-many-proposals", "entries": []}

    known = {_story_fingerprint(s): s.slug for s in load_bank().values()}
    rows = []
    for path in paths:
        entry = {"file": path.name}
        if path.suffix != ".yaml" or path.is_symlink() or not path.is_file():
            entry.update(status="rejected", reason="unsupported-file")
        else:
            try:
                candidate = load_proposal(path)
            except ValueError as exc:
                # No untrusted payload or path/error string may enter machine-readable output.
                reason = ("curated-collision" if "collides with a curated" in str(exc)
                          else "invalid-schema-or-policy")
                entry.update(status="rejected", reason=reason)
            except (ValidationError, yaml.YAMLError, OSError, UnicodeError):
                entry.update(status="rejected", reason="invalid-schema-or-policy")
            else:
                fingerprint = _story_fingerprint(candidate)
                if fingerprint in known:
                    entry.update(status="rejected", reason="duplicate-narrative",
                                 duplicate_of=known[fingerprint])
                else:
                    entry.update(status="accepted", scenario_type=candidate.scenario_type,
                                 sha256=sha256(path.read_bytes()).hexdigest())
                    known[fingerprint] = candidate.slug
        rows.append(entry)

    accepted = sum(entry["status"] == "accepted" for entry in rows)
    return {"passed": accepted == len(rows), "total": len(rows), "accepted": accepted,
            "rejected": len(rows) - accepted, "entries": rows}
