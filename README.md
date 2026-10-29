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

## Dual-provider scenario factory: Ollama locally, Workers AI on OCI A1

Dependencies: Python backend installed with `python -m pip install -e '.[dev]'`
and GNU Make. Local Ollama also requires Docker Engine with the `docker compose`
plugin. Remote Workers AI **does not** require Docker, Ollama or a GPU on the OCI A1.
This factory runs operationally from the backend/CLI; it does **not** start a
public runner, SSO, or user-accessible Kubernetes endpoint.

```bash
make ollama-up
make ollama-pull                           # OLLAMA_MODEL=qwen2.5:7b-instruct (~4.7 GB)
make scenario-generate TYPE=deployment_scale COUNT=3
make scenario-generate TYPE=service_selector MODEL=qwen2.5:7b-instruct
make scenario-audit                       # Offline audit of the whole quarantine
make scenario-validate PROPOSAL=scenarios/generated/<generated-slug>.yaml
```

For **OCI A1 / remote generation**, configure these secrets in the backend's
runtime secret configuration (to be wired into the OCI deployment), never in source code,
Compose files, frontend bundles, logs or GitHub actions:

```bash
# Provide securely to the backend process:
# CKA_SCENARIO_PROVIDER=workers-ai
# CLOUDFLARE_ACCOUNT_ID=<account-id>
# CLOUDFLARE_API_TOKEN=<restricted Workers AI token>
make scenario-generate PROVIDER=workers-ai TYPE=deployment_scale COUNT=3
```

Workers AI inference calls Cloudflare's fixed REST API endpoint over HTTPS,
using an account-scoped API token with **Workers AI Read** permission and
JSON Schema structured output. The default approved model is
`@cf/meta/llama-3.3-70b-instruct-fp8-fast`; alternative models must be
in the JSON Mode allowlist and verified before deployment. The model is
**never** invoked by a browser or the student-facing exam API, and the token
stays server-side. Configure outbound egress and generation quotas separately
in OCI. The inference provider is selected with `--provider` or the
`CKA_SCENARIO_PROVIDER` environment variable. CLI and Make share the
same schema, quarantine and grading policy regardless of provider.

For **local Ollama**, the container listens on host **127.0.0.1:11434 only** and keeps its
models in a Docker volume; `make ollama-down` stops it without deleting the
downloads. Use `make ollama-pull OLLAMA_MODEL=qwen2.5:1.5b-instruct` and then
`make scenario-generate MODEL=qwen2.5:1.5b-instruct` for a smaller CPU model. Only a
single model is loaded at a time. Native Ollama installed locally also works;
the generator only accepts a loopback URL via `OLLAMA_URL`.

Both providers are asked for **JSON constrained by the same Pydantic JSON
Schema**, never raw YAML, Kubernetes manifests, scripts or graders. Cloudflare
Workers AI supports `response_format.type=json_schema`; Ollama uses
`format=<schema>`. Our deterministic Python code
revalidates its DSL and writes `scenarios/generated/*.yaml` using `safe_dump`.
The YAML reader rejects duplicate keys, custom tags, aliases, oversized files,
unknown schema fields, unsafe names and collisions with curated scenarios.
Run `make scenario-audit` (or `python -m cka_sim audit-proposals`) to
inspect the whole quarantine **offline**, with a bounded 200-file limit.
Its machine-readable JSON report gives accepted/rejected counts, reason codes
and SHA-256 hashes of accepted YAML without exposing generated story text.
It also rejects **exact normalized duplicate stories** within the same scenario
type, including copies of curated scenarios. This is a deterministic copy check,
**not** a semantic novelty score or a proof that a challenge is educational.
For a convenient operator-only sequence use `make scenario-batch COUNT=5
PROVIDER=workers-ai`: generate then run the offline audit. On failure the
proposals remain in quarantine for inspection; nothing is published.
The report can be redirected to a local file under `.local/` if desired.
`make scenario-validate` checks **structure and policy only**; it does not
guarantee a working Kubernetes scenario.

To test proposals in a real disposable cluster, intentionally run:

```bash
make lab-up
make scenario-smoke PROPOSAL=scenarios/generated/<generated-slug>.yaml
# Or verify up to five offline-audited proposals sequentially:
make scenario-verify
cat .local/proposal-verification.json
make scenario-review-queue
make lab-down
```

The smoke test reuses **trusted fixture templates and deterministic graders**,
proves the initial state fails, applies the canonical repair and waits for the
graded result to pass, then cleans the namespace. `scenario-verify` first
requires **every** quarantined YAML to pass the offline audit; if any fail,
no Kubernetes commands run. It refuses batches larger than five and uses only
the fixed `scenarios/generated` quarantine and the dedicated `kind-cka-dev`
context. It halts after the first failed live smoke (to avoid continuing on
a potentially dirty cluster) and writes an atomic, local-only verification
report with immutable YAML SHA-256 references, generic failure codes, and a
`needs-human-review` status for each success. Changes to YAML during the
smoke invalidate success. No raw model output or Kubernetes diagnostic text
is included in the JSON evidence. The live verification does **not** start or
delete a cluster, so the operator remains responsible for the dedicated lab. This is a **manual local**
step (not performed by `make scenario-generate`). Only the fixed
`kind-cka-dev` context is used. Do not run another lab concurrently.
Use `make scenario-review-queue` to list candidates that currently have
matching offline-policy and Kubernetes verification evidence. A candidate is
`needs-human-review` only when the complete evidence report belongs to the
fixed `kind-cka-dev` context and its SHA-256 digest matches the current YAML.
Modified, missing, new or invalid proposals require verification again. This
is a **read-only operator command**: it neither approves nor publishes anything.
The local verification file is not cryptographically signed and must be treated
as trusted operator-local state. Human review and a separate Git PR remain
mandatory before any scenario reaches students.

When an operator has **personally reviewed** an unchanged proposal, they can
prepare a private, non-publishing review packet using its exact digest from
`make scenario-review-queue`:

```bash
make scenario-prepare-review SLUG=<slug> SHA256=<64-hex-digest> REVIEWER=<label>
```

This is an explicit CLI action, never part of generation or verification.
It requires the entire batch to retain valid live smoke evidence, checks the
source digest again, and creates `.local/review-drafts/<slug>.review.json`
(mode 0600, no overwrite). The packet records the reviewer's **unverified
operator label**, the exact source digest, and the schema-validated scenario.
It is **not** a curated bank entry or proof of authenticated approval. An
operator must independently inspect the packet and prepare a separate Git PR
adding the reviewed scenario to `scenarios/` before it can become public.
No automatic publication or deployment is performed.

Currently both AI providers support `deployment_scale` and `service_selector`
only; the CPU-resource scenario is curated until its generator quality gates
are extended. `COUNT` is limited to 1–20 per invocation. Partial batches can
remain quarantined if later generated outputs fail. Never expose local Ollama
or developer Kubernetes endpoints to the Internet.

## Roadmap

1. Reliable VM-backed per-user ephemeral runner, auth, quotas and isolation.
2. Real PTY browser shell instead of restricted kubectl, plus exam sessions with authoritative server clock.
3. Automated batch quality evaluation (novelty, repeated namespace/slug detection, actual live smoke) and explicit human publication gate.
4. Expanded CKA domains and official blueprint mapping.
