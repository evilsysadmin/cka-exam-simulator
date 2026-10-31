"""Private pipe adapter to Go's durable, capacity-one lab lease journal.

This is trusted controller IPC, not an authentication boundary or public API.
Only freshly verified Identity UUIDs may be supplied by the eventual controller.
No Docker/Kubernetes authority, password, JWT or service proof goes over this pipe.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time


class LeaseUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Lab lease controller unavailable")


class LeaseRejected(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__("Lab lease request rejected: " + code)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate reply field")
        value[key] = item
    return value


class LeaseProcess:
    def __init__(self, *, journal: Path, bank: Path,
                 binary: Path = Path("/usr/local/bin/cka-lease-stdio"), timeout: float = 2.0):
        if (not all(isinstance(p, Path) and p.is_absolute() for p in (journal, bank, binary))
                or type(timeout) not in (float, int) or not 0 < timeout <= 5):
            raise LeaseUnavailable()
        self._lock = threading.Lock()
        self._timeout = timeout
        self._broken = False
        try:
            self._process = subprocess.Popen(
                [str(binary), "-journal", str(journal), "-bank", str(bank)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                close_fds=True, start_new_session=True, bufsize=0)
        except OSError:
            raise LeaseUnavailable() from None

    def _stop(self):
        self._broken = True
        if self._process.poll() is None:
            try:
                os.killpg(self._process.pid, signal.SIGTERM)
                self._process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                os.killpg(self._process.pid, signal.SIGKILL)
                self._process.wait(timeout=0.5)
            except ProcessLookupError:
                self._process.wait(timeout=0.5)
        self._process.stdin.close()
        self._process.stdout.close()

    def close(self):
        with self._lock:
            self._stop()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _call(self, operation, **fields):
        if (not all(type(value) is str and 0 < len(value) <= 128 for value in fields.values())
                or not self._lock.acquire(timeout=self._timeout)):
            raise LeaseUnavailable()
        try:
            if self._broken or self._process.poll() is not None:
                raise LeaseUnavailable()
            data = json.dumps({"op": operation, **fields}, separators=(",", ":")).encode() + b"\n"
            if len(data) > 2048:
                raise LeaseUnavailable()
            deadline = time.monotonic() + self._timeout
            if self._process.stdin.write(data) != len(data):
                raise LeaseUnavailable()
            reply = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(self._process.stdout, selectors.EVENT_READ)
                while b"\n" not in reply:
                    left = deadline - time.monotonic()
                    if left <= 0 or not selector.select(left):
                        raise LeaseUnavailable()
                    chunk = os.read(self._process.stdout.fileno(), 16385 - len(reply))
                    if not chunk:
                        raise LeaseUnavailable()
                    reply.extend(chunk)
                    if len(reply) > 16384:
                        raise LeaseUnavailable()
            if not reply.endswith(b"\n") or reply.count(b"\n") != 1:
                raise LeaseUnavailable()
            result = json.loads(reply, object_pairs_hook=_unique)
            if type(result) is not dict or type(result.get("ok")) is not bool:
                raise LeaseUnavailable()
            if result["ok"]:
                if set(result) != {"ok", "result"}:
                    raise LeaseUnavailable()
                return result["result"]
            if (set(result) != {"ok", "error"} or result["error"] not in
                    {"denied", "conflict", "capacity", "invalid", "journal-unavailable"}):
                raise LeaseUnavailable()
            if result["error"] == "journal-unavailable":
                raise LeaseUnavailable()
            raise LeaseRejected(result["error"])
        except LeaseRejected:
            raise
        except (OSError, ValueError, TypeError, LeaseUnavailable):
            self._stop()
            raise LeaseUnavailable() from None
        finally:
            self._lock.release()

    def start(self, subject: str, slug: str):
        return self._call("start", owner=subject, slug=slug)

    def status(self, subject: str, lease_id: str):
        return self._call("status", owner=subject, id=lease_id)

    def scenario(self, subject: str, lease_id: str):
        return self._call("scenario", owner=subject, id=lease_id)

    def finish(self, subject: str, lease_id: str):
        return self._call("finish", owner=subject, id=lease_id)

    def pending(self):
        return self._call("pending")

    def confirm_cleanup(self, lease_id: str, namespace: str):
        """Trusted reaper only, after actual Kubernetes deletion is verified."""
        return self._call("confirm", id=lease_id, namespace=namespace)
