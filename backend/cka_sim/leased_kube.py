"""Private staging cluster driver. No HTTP routes or host Docker authority.

The caller must serialize operations with its lease controller and authorize a
fresh Identity session before every candidate operation. Only Go-journal lease
names/IDs and validated curated scenarios belong here, never request parameters.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time

from .candidate_kubectl import NAMESPACE, NAME, plan_candidate_command
from .candidate_filters import split_filter


CONTEXT = "kind-cka-lab-staging"
ADMIN_CONFIG = Path("/run/secrets/cka-lab-admin.yaml")
OWNER_LABEL = "labs.shadowops.org/lease"
MANAGED_LABEL = "labs.shadowops.org/managed"


class ClusterUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Lab cluster unavailable")


def _scope(lease_id, namespace):
    if (type(lease_id) is not str or not re.fullmatch(r"[a-f0-9]{32}", lease_id)
            or type(namespace) is not str or not NAMESPACE.fullmatch(namespace)):
        raise ClusterUnavailable()


def namespace_policy(lease_id, namespace, deployment):
    """Trusted, bounded manifest set; CPU quota deliberately preserves CPU fault."""
    _scope(lease_id, namespace)
    if type(deployment) is not str or not NAME.fullmatch(deployment):
        raise ClusterUnavailable()
    service = deployment + "-svc"
    labels = {OWNER_LABEL: lease_id, MANAGED_LABEL: "staging-v1"}
    ns = {"apiVersion": "v1", "kind": "Namespace", "metadata": {
        "name": namespace, "labels": {**labels,
            "pod-security.kubernetes.io/enforce": "restricted",
            "pod-security.kubernetes.io/enforce-version": "v1.35",
            "pod-security.kubernetes.io/audit": "restricted",
            "pod-security.kubernetes.io/warn": "restricted"}}}

    def resource(api, kind, name, **fields):
        return {"apiVersion": api, "kind": kind,
                "metadata": {"name": name, "namespace": namespace, "labels": labels.copy()}, **fields}

    read = ["get", "list", "watch"]
    rules = [
        {"apiGroups": [""], "resources": ["pods", "services", "events"], "verbs": read},
        {"apiGroups": ["apps"], "resources": ["deployments", "replicasets"], "verbs": read},
        {"apiGroups": ["discovery.k8s.io"], "resources": ["endpointslices"], "verbs": read},
        {"apiGroups": ["apps"], "resources": ["deployments", "deployments/scale"],
         "resourceNames": [deployment], "verbs": ["patch", "update"]},
        {"apiGroups": [""], "resources": ["services"],
         "resourceNames": [service], "verbs": ["patch"]},
    ]
    return [ns,
        resource("v1", "ServiceAccount", "candidate", automountServiceAccountToken=False),
        resource("v1", "ResourceQuota", "pilot", spec={"hard": {
            "pods": "12", "services": "2", "secrets": "0",
            "persistentvolumeclaims": "0", "requests.memory": "384Mi",
            "limits.memory": "768Mi", "count/deployments.apps": "1",
            "count/replicasets.apps": "16", "count/jobs.batch": "0",
            "count/cronjobs.batch": "0", "services.nodeports": "0",
            "services.loadbalancers": "0"}}),
        resource("rbac.authorization.k8s.io/v1", "Role", "candidate", rules=rules),
        resource("rbac.authorization.k8s.io/v1", "RoleBinding", "candidate",
                 roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "candidate"},
                 subjects=[{"kind": "ServiceAccount", "name": "candidate", "namespace": namespace}]),
    ]


def run_bounded(argv, *, input_text=None, timeout=12, limit=131072, accepted_codes=(0,)):
    """Bound child lifetime and both pipes, including a nonreading/stuck child."""
    if (not isinstance(argv, list) or not argv or not all(type(x) is str for x in argv)
            or not 0 < timeout <= 40 or not 0 < limit <= 262144
            or accepted_codes not in ((0,), (0, 1))):
        raise ClusterUnavailable()
    data = b"" if input_text is None else input_text.encode("utf-8")
    if len(data) > 65536:
        raise ClusterUnavailable()
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, close_fds=True,
                                   env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
                                        "HOME": "/tmp/cka-kubectl"}, start_new_session=True, bufsize=0)
        output = bytearray()
        errors = bytearray()
        deadline = time.monotonic() + timeout
        offset = 0
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ, output)
            selector.register(process.stderr, selectors.EVENT_READ, errors)
            if data:
                selector.register(process.stdin, selectors.EVENT_WRITE, None)
            else:
                process.stdin.close()
            while selector.get_map():
                left = deadline - time.monotonic()
                if left <= 0:
                    raise ClusterUnavailable()
                for key, _ in selector.select(left):
                    if key.fileobj is process.stdin:
                        offset += os.write(process.stdin.fileno(), data[offset:offset + 4096])
                        if offset == len(data):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            key.data.extend(chunk)
                            if len(key.data) > (limit if key.data is output else 4096):
                                raise ClusterUnavailable()
        if process.wait(timeout=max(0.001, deadline - time.monotonic())) not in accepted_codes:
            raise ClusterUnavailable()
        return output.decode("utf-8", errors="strict")
    except (OSError, ValueError, subprocess.SubprocessError, ClusterUnavailable):
        # Kubernetes stderr and credentials never become an HTTP error or log.
        raise ClusterUnavailable() from None
    finally:
        if process is not None:
            # Kill the entire private group even if its parent has already exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


class LeasedCluster:
    def __init__(self, *, admin_config=ADMIN_CONFIG, context=CONTEXT,
                 binary=Path("/usr/local/bin/kubectl"), scratch=Path("/tmp/cka-candidate")):
        # Constructor configuration is operator-only; never derived from HTTP.
        if (not all(isinstance(x, Path) and x.is_absolute() for x in (admin_config, binary, scratch))
                or context != CONTEXT):
            raise ClusterUnavailable()
        self.admin, self.binary, self.scratch = admin_config, binary, scratch

    def ready(self):
        return self._admin(["get", "--raw", "/readyz"]).strip() == "ok"

    def _admin(self, args, *, body=None, timeout=12):
        return run_bounded([str(self.binary), "--kubeconfig", str(self.admin),
                            "--context", CONTEXT, "--request-timeout=8s", *args],
                           input_text=None if body is None else json.dumps(body), timeout=timeout)

    def _json(self, args, *, body=None):
        try:
            value = json.loads(self._admin(args, body=body))
            if type(value) is not dict:
                raise ValueError()
            return value
        except (ValueError, TypeError, RecursionError):
            raise ClusterUnavailable() from None

    def _namespace(self, lease_id, namespace):
        _scope(lease_id, namespace)
        value = self._json(["get", "namespace", namespace, "--ignore-not-found=true", "-o", "json"])
        return self._owned(value, lease_id, namespace)

    @staticmethod
    def _owned(value, lease_id, namespace):
        metadata = value.get("metadata", {})
        labels = metadata.get("labels", {})
        if (value.get("kind") != "Namespace" or metadata.get("name") != namespace
                or labels.get(OWNER_LABEL) != lease_id or labels.get(MANAGED_LABEL) != "staging-v1"
                or not re.fullmatch(r"[a-f0-9-]{36}", metadata.get("uid", ""))):
            raise ClusterUnavailable()
        return metadata["uid"]

    def provision(self, lease_id, scenario):
        from .render import fixture_objects
        policies = namespace_policy(lease_id, scenario.namespace, scenario.deployment)
        # Create (never apply/adopt) the namespace before preparing any fixtures.
        self._admin(["create", "-f", "-"], body=policies[0])
        self._namespace(lease_id, scenario.namespace)
        for item in policies[1:]:
            self._admin(["create", "-f", "-"], body=item)
        for item in fixture_objects(scenario)[1:]:
            if item["kind"] == "Deployment":
                item["spec"]["template"]["spec"]["securityContext"] = {
                    "seccompProfile": {"type": "RuntimeDefault"}}
                item["spec"]["template"]["spec"]["serviceAccountName"] = "candidate"
            self._admin(["create", "-f", "-"], body=item)

    @contextmanager
    def _candidate(self, lease_id, namespace):
        self._namespace(lease_id, namespace)
        # Request an expiring token; never store long-lived SA token Secrets.
        token = self._admin(["create", "token", "candidate", "-n", namespace, "--duration=600s"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{100,8192}", token):
            raise ClusterUnavailable()
        view = self._json(["config", "view", "--raw", "--minify", "-o", "json"])
        clusters = view.get("clusters", [])
        if len(clusters) != 1:
            raise ClusterUnavailable()
        cluster = clusters[0].get("cluster", {})
        # Never carry auth plugins, external CA paths, proxy URLs or TLS bypass.
        if (set(cluster) != {"server", "certificate-authority-data"}
                or not cluster["server"].startswith("https://")
                or type(cluster["certificate-authority-data"]) is not str):
            raise ClusterUnavailable()
        self.scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="lease-", dir=self.scratch) as directory:
            config = Path(directory) / "candidate.json"
            config.write_text(json.dumps({"apiVersion": "v1", "kind": "Config",
                "clusters": [{"name": "lab", "cluster": cluster}],
                "contexts": [{"name": "candidate", "context": {
                    "cluster": "lab", "user": "candidate", "namespace": namespace}}],
                "current-context": "candidate",
                "users": [{"name": "candidate", "user": {"token": token}}]}))
            config.chmod(0o600)
            yield [str(self.binary), "--kubeconfig", str(config), "--context", "candidate",
                   "--request-timeout=8s"]

    def command(self, lease_id, scenario, command):
        command, grep = split_filter(command)
        args = plan_candidate_command(command, namespace=scenario.namespace,
                                      deployment=scenario.deployment, service=scenario.service)
        if grep is not None and args[0] not in {'get', 'describe', 'explain'}:
            from .candidate_kubectl import CandidateCommandError
            raise CandidateCommandError()
        with self._candidate(lease_id, scenario.namespace) as base:
            output = run_bounded(base + args)
        return run_bounded(grep, input_text=output, timeout=1, accepted_codes=(0,1)) if grep else output

    def edit_snapshot(self, lease_id, scenario, kind, name):
        from .lab_editor import edit_target
        if edit_target('kubectl edit '+kind+'/'+name,scenario) != (kind,name):
            raise ClusterUnavailable()
        with self._candidate(lease_id,scenario.namespace) as base:
            try:
                return json.loads(run_bounded(base+['get',kind,name,'-n',scenario.namespace,'-o','json'],limit=65536))
            except (ValueError,TypeError):
                raise ClusterUnavailable() from None

    def edit_apply(self, lease_id, scenario, kind, name, patch):
        from .lab_editor import edit_target
        if edit_target('kubectl edit '+kind+'/'+name,scenario) != (kind,name):
            raise ClusterUnavailable()
        with self._candidate(lease_id,scenario.namespace) as base:
            run_bounded(base+['patch',kind,name,'-n',scenario.namespace,'--type=json','--patch',json.dumps(patch)])

    def grade(self, lease_id, scenario):
        from .grading import grade_state
        self._namespace(lease_id, scenario.namespace)
        with self._candidate(lease_id, scenario.namespace) as base:
            def read(resource, *names):
                try:
                    return json.loads(run_bounded(base + ["get", resource, *names,
                                      "-n", scenario.namespace, "-o", "json"]))
                except (ValueError, TypeError, RecursionError):
                    raise ClusterUnavailable() from None
            deployment = read("deployment", scenario.deployment)
            service = slices = None
            if scenario.scenario_type == "service_selector":
                service = read("service", scenario.service)
                slices = read("endpointslices", "-l", "kubernetes.io/service-name=" + scenario.service)
            return grade_state(scenario, deployment=deployment, service=service,
                               endpoint_slices=slices).as_dict()

    def cleanup(self, lease_id, namespace):
        """Return only after actual absence. Foreign objects and timeouts deny."""
        _scope(lease_id, namespace)
        raw = self._admin(["get", "namespace", namespace, "--ignore-not-found=true", "-o", "json"])
        if not raw.strip():
            return
        try:
            value = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            raise ClusterUnavailable() from None
        uid = self._owned(value, lease_id, namespace)
        self._admin(["delete", "--raw", "/api/v1/namespaces/" + namespace, "-f", "-"],
                    body={"apiVersion": "v1", "kind": "DeleteOptions",
                          "preconditions": {"uid": uid}, "propagationPolicy": "Foreground"})
        self._admin(["wait", "--for=delete", "namespace/" + namespace, "--timeout=30s"], timeout=35)
        if self._admin(["get", "namespace", namespace, "--ignore-not-found=true", "-o", "json"]).strip():
            raise ClusterUnavailable()
