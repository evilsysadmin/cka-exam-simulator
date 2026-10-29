"""Single-user, in-process clock for the loopback-only development lab.

This is deliberately NOT an authenticated or distributed exam-session store.
A future SSO-backed isolated runner must supply per-user durable sessions.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from threading import Lock
from time import monotonic


class SessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalSession:
    scenario_slug: str
    deadline: float
    expires_at: str


_lock = Lock()
_session: LocalSession | None = None


def start_session(slug: str, minutes: int) -> dict:
    """Start only after the Kubernetes fixtures have been provisioned."""
    global _session
    with _lock:
        _session = LocalSession(
            scenario_slug=slug,
            deadline=monotonic() + minutes * 60,
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(),
        )
        return _snapshot(_session)


def _snapshot(session: LocalSession) -> dict:
    remaining = max(0, ceil(session.deadline - monotonic()))
    return {
        "scenario_slug": session.scenario_slug,
        "phase": "active" if remaining > 0 else "expired",
        "remaining_seconds": remaining,
        "expires_at": session.expires_at,
    }


def get_session() -> dict | None:
    with _lock:
        return _snapshot(_session) if _session is not None else None


def require_session(*, slug: str | None = None, active: bool = False) -> dict:
    state = get_session()
    if state is None:
        raise SessionError("Prepare a lab before using the terminal or grading")
    if slug is not None and state["scenario_slug"] != slug:
        raise SessionError("This scenario is not the current prepared lab")
    if active and state["phase"] != "active":
        raise SessionError("Exam time is up; prepare the lab again to retry")
    return state
