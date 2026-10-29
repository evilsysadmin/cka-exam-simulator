# CKA Exam Simulator

An experimental **hands-on** Kubernetes CKA simulator with browser-based developer terminal, deterministic grading against live cluster state and optional local Ollama scenario proposals.

## Developer-only local preview

Not safe for public hosting: no authentication, tenant isolation, quotas, persistent multi-user sessions or production-grade terminal yet. Do not publish these endpoints or expose localhost via an internet proxy.

Requirements: Python 3.11+, Node 20+, npm, Docker Engine, kind, kubectl (native packages or source); Ollama optional.

1. Install backend: python -m pip install -e '.[dev]'
2. Install frontend: cd frontend && npm install --no-audit --no-fund && cd ..
3. Run tests: python -m pytest && python -m cka_sim validate-bank
4. Create cluster: python -m cka_sim lab-up
5. Start backend in one terminal: CKA_ENABLE_LOCAL_LAB=1 uvicorn cka_sim.api:app --host 127.0.0.1 --port 8000
6. Start frontend in another terminal: cd frontend && npm run dev
7. Open http://127.0.0.1:5173 and select a challenge, Prepare lab, use kubectl, Check solution.
8. Remove the dedicated cluster when done: python -m cka_sim lab-down

Sample fix (in the browser console):
- kubectl get deployments -n cka-payments
- kubectl scale deployment/payment-api --replicas=3 -n cka-payments

The local preview now uses an **in-process, server-authoritative monotonic timer**. Preparing a lab starts the clock only after fixture setup succeeds. Refreshing the browser resumes the current scenario and its remaining time; terminal commands are rejected when time expires, while checking the final grade remains available. Preparing another scenario starts a new attempt. This intentionally supports **one local developer session per API process only**: server restarts forget the timer, and this is not a substitute for authenticated, persistent, per-user sessions in the future isolated runner.

The browser console is **restricted kubectl**, not a full shell. It only targets the dedicated kind-cka-dev Kubernetes context. Requests to the local developer API require a specific header and a loopback peer, but this is NOT production authentication.

## Validate scenario design

Against a disposable, locally running kind cluster:
- python -m cka_sim lab-smoke scale-payment-api
- python -m cka_sim lab-smoke fix-catalog-service
- python -m cka_sim lab-smoke recover-scheduler-capacity

This checks the initial state fails and the canonical solution passes against real Kubernetes resources.
The scheduling scenario begins with an impossible 16-CPU container request (and matching limit);
the candidate must set requests.cpu to 10m and limits.cpu to 250m, then verify that
the new Deployment rollout is observed and all replicas are available.
Ollama generation remains limited to the two previously supported proposal types;
the new scenario is curated and must pass the same live kind acceptance gate. The image nginx:1.27-alpine must be pullable (or preloaded).

## Local Ollama scenario factory (no GPU required)

Dependencies: Python backend installed with `python -m pip install -e '.[dev]'`,
Docker Engine with the `docker compose` plugin, and GNU Make. This is strictly
a developer-only service and does **not** start a public runner, SSO or Kubernetes.

```bash
make ollama-up
make ollama-pull                           # MODEL=qwen2.5:7b-instruct by default (~4.7 GB)
make scenario-generate TYPE=deployment_scale COUNT=3
make scenario-generate TYPE=service_selector MODEL=qwen2.5:7b-instruct
make scenario-validate PROPOSAL=scenarios/generated/<generated-slug>.yaml
```

The Ollama container listens on host **127.0.0.1:11434 only** and keeps its
models in a Docker volume; `make ollama-down` stops it without deleting the
downloads. Use `MODEL=qwen2.5:1.5b-instruct` for a smaller CPU model. Only a
single model is loaded at a time. Native Ollama installed locally also works;
the generator only accepts a loopback URL via `OLLAMA_URL`.

Ollama is asked for **JSON constrained by the Pydantic JSON Schema**, never raw
YAML, Kubernetes manifests, scripts or graders. Our deterministic Python code
revalidates its DSL and writes `scenarios/generated/*.yaml` using `safe_dump`.
The YAML reader rejects duplicate keys, custom tags, aliases, oversized files,
unknown schema fields, unsafe names and collisions with curated scenarios.
`make scenario-validate` checks **structure and policy only**; it does not
guarantee a working Kubernetes scenario.

To test a proposal in a real disposable cluster, intentionally run:

```bash
make lab-up
make scenario-smoke PROPOSAL=scenarios/generated/<generated-slug>.yaml
make lab-down
```

The smoke test reuses **trusted fixture templates and deterministic graders**,
proves the initial state fails, applies the canonical repair and waits for the
graded result to pass, then cleans the namespace. This is a **manual local**
step (not performed by `make scenario-generate`). Only the fixed
`kind-cka-dev` context is used. Do not run another lab concurrently.
A reviewed, smoke-tested proposal must still be **manually promoted** to the
tracked curated JSON bank by a human-reviewed PR; no command automatically
publishes to the learner-facing API.

Currently the AI factory supports `deployment_scale` and `service_selector`
only; the CPU-resource scenario is curated until its generator quality gates
are extended. `COUNT` is limited to 1–20 per invocation. Partial batches can
remain quarantined if later generated outputs fail. Never expose local Ollama
or developer Kubernetes endpoints to the Internet.

## Roadmap

1. Reliable VM-backed per-user ephemeral runner, auth, quotas and isolation.
2. Real PTY browser shell instead of restricted kubectl, plus exam sessions with authoritative server clock.
3. Automated batch quality evaluation (novelty, repeated namespace/slug detection, actual live smoke) and explicit human publication gate.
4. Expanded CKA domains and official blueprint mapping.
