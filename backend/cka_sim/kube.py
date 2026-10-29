"""Fixed-context local lab driver. This is NOT a production sandbox."""
import json
from pathlib import Path
import shlex
import subprocess

from .grading import grade_state
from .models import Scenario
from .render import render_yaml

LAB_CLUSTER = "cka-dev"
LAB_CONTEXT = "kind-cka-dev"
KUBECONFIG = Path(__file__).resolve().parents[2] / ".local" / "kubeconfig"


class LabError(RuntimeError):
    pass


def _run(binary: str, args: list[str], *, input_text: str | None = None, timeout: int = 35) -> str:
    try:
        result = subprocess.run([binary, *args], input=input_text, text=True,
                                capture_output=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LabError(f"Lab command unavailable: {exc}") from exc
    if result.returncode != 0:
        raise LabError((result.stderr or result.stdout)[:1200])
    return result.stdout[:100_000]


def kubectl(args: list[str], *, input_text: str | None = None) -> str:
    if not KUBECONFIG.is_file():
        raise LabError("Dedicated kubeconfig missing. Run cka-sim lab-up first.")
    return _run("kubectl", ["--kubeconfig", str(KUBECONFIG), "--context", LAB_CONTEXT,
                            *args], input_text=input_text)


def create_lab() -> str:
    KUBECONFIG.parent.mkdir(parents=True, exist_ok=True)
    return _run("kind", ["create", "cluster", "--name", LAB_CLUSTER,
                         "--kubeconfig", str(KUBECONFIG), "--wait", "120s"], timeout=250)


def delete_lab() -> str:
    # Never accept a user-supplied cluster name.
    return _run("kind", ["delete", "cluster", "--name", LAB_CLUSTER], timeout=120)


def setup(s: Scenario) -> None:
    kubectl(["apply", "-f", "-"], input_text=render_yaml(s))


def cleanup(s: Scenario) -> None:
    kubectl(["delete", "namespace", s.namespace, "--ignore-not-found=true"])


def _get_json(args: list[str]) -> dict:
    try:
        return json.loads(kubectl(["get", *args, "-o", "json"]))
    except LabError as exc:
        if "notfound" in str(exc).lower() or "not found" in str(exc).lower():
            return {}
        raise


def grade(s: Scenario) -> dict:
    deployment = _get_json(["deployment", s.deployment, "-n", s.namespace])
    service = endpoint_slices = None
    if s.scenario_type == "service_selector":
        service = _get_json(["service", s.service, "-n", s.namespace])
        endpoint_slices = _get_json(["endpointslices", "-n", s.namespace,
                                      "-l", f"kubernetes.io/service-name={s.service}"])
    return grade_state(s, deployment=deployment, service=service,
                       endpoint_slices=endpoint_slices).as_dict()


def solve_for_smoke(s: Scenario) -> None:
    if s.scenario_type == "deployment_scale":
        kubectl(["scale", f"deployment/{s.deployment}", "-n", s.namespace,
                 f"--replicas={s.target_replicas}"])
    else:
        patch = json.dumps({"spec": {"selector": {"app": s.target_selector}}})
        kubectl(["patch", "service", s.service, "-n", s.namespace,
                 "--type=merge", "-p", patch])


ALLOWED_VERBS = frozenset({"get", "describe", "scale", "patch", "rollout", "explain"})


def execute_dev_kubectl(command: str) -> str:
    """Restricted developer kubectl, not a shell or production-capable terminal."""
    if len(command) > 600 or "\n" in command or "\r" in command:
        raise LabError("Command too long or multiline")
    try:
        words = shlex.split(command)
    except ValueError as exc:
        raise LabError("Malformed command") from exc
    if len(words) < 2 or words[0] != "kubectl" or words[1] not in ALLOWED_VERBS:
        raise LabError("Only kubectl get/describe/scale/patch/rollout/explain are supported")
    forbidden = ("--kubeconfig", "--context", "--server", "--token", "--client-key", "--client-certificate",
                 "--certificate-authority", "--as", "--as-group", "--raw", "--filename", "--output-watch-events")
    for word in words[1:]:
        if word == "--" or any(word == flag or word.startswith(flag + "=") for flag in forbidden):
            raise LabError("Forbidden kubectl option")
        if word in ("-f", "-k", "--patch-file"):
            raise LabError("File inputs are not supported")
        if any(c in word for c in (";", "|", "&", chr(96), "$", ">", "<")):
            raise LabError("Shell syntax is not supported")
    return kubectl(words[1:])
