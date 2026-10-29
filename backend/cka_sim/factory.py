"""One-shot, operator-only Workers AI scenario factory for a future OCI A1 job.

Never connects to Kubernetes, serves HTTP, invokes Ollama or publishes scenarios.
The local quota is intentionally conservative, not a Cloudflare billing limit.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile

from . import workers_ai
from .catalog import PROPOSALS, ROOT, save_generated
from .quality import audit_proposals

STATE = ROOT / ".local" / "factory-budget.json"
LOCK = ROOT / ".local" / "factory.lock"
MAX_DAILY_REQUESTS = 5
MAX_PER_RUN = 2
MAX_QUARANTINED = 5
TYPES = ("deployment_scale", "service_selector")


class FactoryError(ValueError):
    pass


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@contextmanager
def _exclusive_lock(path: Path):
    if path.parent.is_symlink() or path.is_symlink():
        raise FactoryError("Unsafe factory lock path")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FactoryError("Another factory run is in progress") from None
        yield
    finally:
        os.close(fd)


def _read_budget(path: Path, day: str) -> int:
    if path.is_symlink() or path.parent.is_symlink():
        raise FactoryError("Unsafe factory budget path")
    if not path.exists():
        return 0
    if not path.is_file() or path.stat().st_size > 1024:
        raise FactoryError("Invalid factory budget file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"utc_date", "attempts"}:
            raise ValueError("Unexpected budget shape")
        stored = raw["utc_date"]
        attempts = raw["attempts"]
        if (not isinstance(stored, str) or date.fromisoformat(stored).isoformat() != stored
                or type(attempts) is not int or not 0 <= attempts <= MAX_DAILY_REQUESTS):
            raise ValueError("Invalid budget values")
        if stored > day:
            raise ValueError("Budget date is in the future")
        return attempts if stored == day else 0
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        raise FactoryError("Invalid factory budget file") from None


def _write_budget(path: Path, *, day: str, attempts: int):
    fd, temp = tempfile.mkstemp(prefix=".factory-budget-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"utc_date": day, "attempts": attempts}, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _queued_count(directory: Path) -> int:
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise FactoryError("Unsafe proposal quarantine")
    if not directory.exists():
        return 0
    if not any(directory.iterdir()):
        return 0
    audit = audit_proposals(directory)
    if not audit["passed"]:
        raise FactoryError("Existing quarantine requires manual offline review")
    return audit["total"]


def _require_workers_credentials():
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    token = os.getenv("CLOUDFLARE_API_TOKEN", "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account):
        raise FactoryError("Workers AI account ID is not configured")
    if not token or not token.strip():
        raise FactoryError("Workers AI token is not configured")
    if os.getenv("CKA_SCENARIO_PROVIDER", "workers-ai") != "workers-ai":
        raise FactoryError("OCI scenario factory must use workers-ai, never Ollama")


def run_factory(*, execute: bool = False, count: int = 1,
                scenario_type: str = "deployment_scale",
                model: str = workers_ai.DEFAULT_MODEL,
                directory: Path | None = None,
                state: Path | None = None,
                lock: Path | None = None,
                generator=None) -> dict:
    """Dry-run by default. Opt-in execution is serial and reserves quota first."""
    if type(count) is not int or not 1 <= count <= MAX_PER_RUN:
        raise FactoryError("Factory count must be 1 or 2")
    if scenario_type not in TYPES:
        raise FactoryError("Unsupported factory scenario type")
    if model not in workers_ai.SUPPORTED_MODELS:
        raise FactoryError("Workers AI model is not in the structured-JSON allowlist")
    if type(execute) is not bool:
        raise FactoryError("Explicit execute flag required")
    directory = PROPOSALS if directory is None else directory
    state = STATE if state is None else state
    lock = LOCK if lock is None else lock
    if state.parent != lock.parent:
        raise FactoryError("Factory lock and budget must share a directory")

    with _exclusive_lock(lock):
        queued = _queued_count(directory)
        day = _utc_day()
        attempts = _read_budget(state, day)
        if queued + count > MAX_QUARANTINED:
            raise FactoryError("Factory quarantine limit reached; review the existing batch")
        if attempts + count > MAX_DAILY_REQUESTS:
            raise FactoryError("Daily factory inference budget exhausted")
        report = {"provider": "workers-ai", "scenario_type": scenario_type,
                  "model": model, "utc_date": day, "planned": count,
                  "queued_before": queued, "daily_remaining_before": MAX_DAILY_REQUESTS - attempts}
        if not execute:
            report.update(status="dry-run", generated=[])
            return report

        _require_workers_credentials()
        generate = workers_ai.generate if generator is None else generator
        generated = []
        for _ in range(count):
            # A failure or bad model response still costs quota; never retry blindly.
            attempts += 1
            _write_budget(state, day=day, attempts=attempts)
            try:
                scenario = generate(model=model, scenario_type=scenario_type)
                path = save_generated(scenario, directory)
            except Exception:
                # Never log provider bodies, credentials or model-generated stories.
                raise FactoryError("Factory inference or quarantine save failed; attempt counted") from None
            generated.append(path.name)
        report.update(status="quarantined-not-published", generated=generated,
                      daily_remaining_after=MAX_DAILY_REQUESTS - attempts)
        return report
