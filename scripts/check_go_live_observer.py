"""Check real-kind Go read-only observations against authoritative Python grades.

Only Python creates/solves/cleans the known test scenario. Go never mutates
Kubernetes, and the script is CI-only against the dedicated kind-cka-dev context.
"""
import json
from pathlib import Path
import subprocess
import sys
import time

from cka_sim import kube
from cka_sim.catalog import load_bank

REPO = Path(__file__).resolve().parents[1]
BIN = Path(sys.argv[1])
KUBECONFIG = REPO / ".local" / "kubeconfig"


def go_grade(slug: str) -> dict:
    result = subprocess.run(
        [str(BIN), "-dev-live", "-bank", str(REPO / "scenarios"),
         "-kubeconfig", str(KUBECONFIG), "-slug", slug],
        check=True, capture_output=True, text=True, timeout=40,
    )
    return json.loads(result.stdout)


def until_ready(s, timeout=100):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        python_grade = kube.grade(s)
        go_result = go_grade(s.slug)
        if python_grade["passed"] and go_result["passed"]:
            if python_grade != go_result:
                raise AssertionError("Go/Python grades differ on stable solved kind state")
            return
        time.sleep(2)
    raise AssertionError("Real-kind Go/Python observer did not both reach solved state")


def main():
    if not BIN.is_file() or not KUBECONFIG.is_file():
        raise ValueError("Dedicated Go observer binary or kubeconfig missing")
    s = load_bank()["scale-payment-api"]
    kube.setup(s)
    try:
        initial_python = kube.grade(s)
        initial_go = go_grade(s.slug)
        if initial_python["passed"] or initial_go["passed"]:
            raise AssertionError("Known broken kind fixture was graded solved")
        kube.solve_for_smoke(s)
        until_ready(s)
        print("CKA_GO_KIND_READ_ONLY_PARITY_OK scenario=scale-payment-api")
    finally:
        kube.cleanup(s)


if __name__ == "__main__":
    main()
