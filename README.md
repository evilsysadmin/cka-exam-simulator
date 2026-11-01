# CKA Exam Simulator

An experimental **hands-on** Kubernetes CKA simulator with browser-based developer terminal, deterministic grading against live cluster state and optional local Ollama scenario proposals.

## Central account staging handoff (not yet deployed)

A separate account-only API now verifies the staging Identity JWT and performs
an authenticated, audience-bound live revocation check on every `/api/account/me`.
It uses the same central UUID without a CKA password, profile database or lab grant.
Its isolated image excludes the developer API, Kubernetes and scenario factory.
Deployment and browser integration remain an Infra-owned staging handoff;
see [central account contract](docs/central-account-staging.md).

The older sections below document the offline PoC milestones. Their references
to an unwired Identity bootstrap describe those earlier proofs, not the current
independent Identity pilot already used by Chronicles.

## ShadowOps Identity: first local verifier PoC (NOT login)

CKA now has an **opt-in, loopback-only verification handshake**, `GET /api/dev/identity/poc/me`. It proves only that a staging JWT is signed by a **pre-pinned local public JWKS**, uses `iss=https://id-staging.shadowops.dpdns.org`, `aud=cka-simulator`, has a canonical UUIDv4 `sub` and valid `iat/nbf/exp` (maximum 1 hour). Both the issuer and audience are fixed in code; JWT headers cannot select a remote key source. Ed25519 signature and JWKS type, `kid`, duplicates, canonical encoding and timing are checked fail-closed. The endpoint returns a subject UUID and a **`signature-only-poc`** label, never a session or an authorization grant.

This is deliberately separate from Kubernetes lab APIs. No new public frontend route, cookie, login, password, session, token issuance, account registration, token exchange, refresh, logout, SSO browser redirect or real Identity deployment exists. The regular developer lab still requires its **separate** `CKA_ENABLE_LOCAL_LAB=1` and loopback guard, and the Cloudflare Pages public preview remains static and read-only.

The PoC endpoint is **404 by default**. To exercise it locally *only after* a trusted staging Identity app token issuer exists, set `CKA_IDENTITY_POC_ENABLED=1` and `CKA_IDENTITY_POC_JWKS_FILE` to an **absolute path to a trusted, regular, non-symlink public Ed25519 JWKS file** from the staging issuer. Start the existing developer API bound to `127.0.0.1`; send `X-CKA-Identity-PoC: 1` and `Authorization: Bearer <staging-cka-token>` to that GET endpoint. This local diagnostic flag must NOT be enabled in OCI staging or production. Missing/malformed JWKS fail closed. The local JWKS is a trust-anchor input, **not** a file to accept from browser or HTTP request parameters. No public keys, passwords, signing secrets or tokens are checked in.

For now, the standalone ShadowOps Identity runtime has **no app-token issuance** and only emits a two-minute synthetic token with `aud=shadowops-identity-probe`, which this CKA verifier *correctly rejects*. Therefore automated tests generate independent **ephemeral synthetic Ed25519 keys** and CKA-audience claims, verifying both accepted and rejected cases. This establishes a strict consumer contract, not a working cross-service login.

Run `python -m pytest backend/tests/test_identity_poc.py`. Next gates: audited real staging Identity app-token issuer/session revocation, consumer token/session binding, approved JWKS rotation and secure distribution, local login round trip, and isolated per-user Kubernetes runner/quotas/TTL before publicly enabling labs.

### Cross-repository Go Identity → CKA cryptographic handshake (CI-only)

The independent `.github/workflows/identity-contract.yml` gate checks out **`evilsysadmin/shadowops-infra` at exact reviewed commit `41e26d71a9ee5f0a2399fbd999f55ea81cc60648`**, rather than trusting a moving branch or downloading arbitrary JWKS from a browser. In an isolated CI job, `scripts/identity_go_contract_fixture.go` creates a new random Ed25519 seed, builds the **actual ShadowOps Identity Go `core.New("staging", seed)` JWKS and issuer**, and **synthetically signs** a CKA-audience JWT with the corresponding ephemeral key for interoperability testing only. The signer script is **not** part of the IdP API or the simulator runtime, and does not provide real user authentication. Its short-lived, synthetic UUID subject is never a registered account or a Kubernetes permission.

The Python `backend/tests/test_identity_crossrepo_contract.py` verifies that CKA accepts a correctly signed `aud=cka-simulator` token against that real Identity Go public-key encoding and rejects `core.ProbeToken`, wrong issuer, wrong audience, expired, future and mismatched `kid` tokens. Synthetic tokens and public keys are written only under the ephemeral GitHub Actions runner temp directory with file mode 0600 and removed after the job; the seed itself is never exported. No real private keys, users, databases, network IdP, product session or lab resources are accessed.

**This is a cryptographic interoperability PoC, not a completed CKA login.** The standalone Identity service still cannot mint a live, session-bound CKA user token and stays `/readyz=503`. Any future Identity core changes must deliberately update the pinned SHA and rerun this contract. A proper login requires verified user enrollment, token issuance bound to an authenticated session, revocation checks and safe JWKS distribution; real CKA labs additionally require isolated per-user runners, quotas, TTL and authorization.

### Versioned Identity session → CKA handshake with real MongoDB (CI-only)

The same cross-repository gate now spins up a disposable **localhost-only MongoDB 7 replica set** before generating verification vectors. A Go fixture calls the **actual** staging `issuance.NewStagingIssuer` and `Issue` against a synthetic active UUIDv4 account with an ephemeral test-only password, then calls `issuance.NewCKAPoCSigner` and `IssuePoCToken` using the resulting opaque version-bound session. It checks that the session signer's JWKS matches Identity `core.New("staging", seed)`, that a wrong password is refused, and that **suspending and revoking the account prevents further sessions or JWT issuance**. The resulting signed JWT is passed to CKA's Python verifier and its existing localhost-only diagnostic endpoint; both verify the issuer, audience, Ed25519 signature and subject. Tests additionally check `sv=1`, `jti` and the signer-enforced **120-second maximum JWT lifetime**. All state, keys, handles and user data are synthetic, remain in the ephemeral runner and are discarded.

**An explicitly tested risk remains:** the test revokes the account *before* passing the previously issued JWT to CKA; signature-only verification still succeeds. Therefore the diagnostic endpoint must remain disabled by default and cannot authorize sessions or Kubernetes access. CKA's lab setup must stay **403**. No public login, actual staged identity service, production deployment, tokens for real users, distributed revocation check or multi-user runner exists. The next production-grade gate is **fresh server-side session/version revocation and product authorization on every protected CKA operation**, not merely JWKS validation. Do not confuse this diagnostic acceptance of a revoked token with valid login.

### CKA revocation freshness contract: signature alone must fail closed (offline)

`backend/cka_sim/identity_freshness.py` now defines `verify_current_staging_cka_session(token, jwks=..., check_freshness=...)`. This is a **strict internal contract for a future authenticated Identity session introspection service**, not a new HTTP handler or a working login. It first checks the trusted JWKS/Ed25519 signature and exact staging issuer and CKA audience. It then requires **exactly the real session-bound CKA JWT claims**, including signed positive 64-bit `sv`, canonical 16-byte random `jti`, `nbf=iat` and a maximum **120-second** lifetime. Older synthetic signature-only vectors without `sv` or `jti` are deliberately **not** eligible.

Next the consumer calls a **server-supplied, trusted** `check_freshness(subject, session_version, token_jti)` once **on every validation attempt**, after signature verification. The checker must return a typed `SessionFreshnessEvidence` bound to the exact `iss/sub/sv/jti`, with separate `account_active`, `session_active` and `token_active` verdicts, plus a check timestamp no older than two seconds or more than one second in the future. Missing checkers, exceptions, timeouts, malformed/mismatched answers, disabled accounts, revoked sessions, token JTI revocations or stale timestamps **always deny**. A check of account version **alone is insufficient**, because rotating a session handle can invalidate a token without increasing the account version. The actual future IdP checker must enforce the JTI-to-live-session binding and use a noncached, strongly consistent read from an authoritative backend, with service authentication and bounded network deadlines. An arbitrary callback or caller-supplied `checked_at` is **not an authority** and must never be accepted from a browser or untrusted service.

**Not yet operational:** ShadowOps Identity has no deployed, authenticated session/token introspection endpoint or persistent JWT-JTI registry. Accordingly **no checker is configured in the CKA runtime** and a signed token *cannot* pass this freshness gate by default. In the existing isolated cross-repository Mongo/Go test, a JWT signed before account suspension remains cryptographically valid but **fails this gate** without a trusted live provider. Positive and negative unit tests use strictly synthetic in-memory checker results to validate the consumer contract; they do **not** prove a real service has checked a real session.

The existing `/api/dev/identity/poc/me` stays a separately labeled **signature-only diagnostic** (404 unless explicitly enabled), and cannot grant access to labs. Neither the public frontend nor `CKA_ENABLE_LOCAL_LAB` is changed. A successful freshness result will *still not* authorize Kubernetes: per-user lab tenancy, quotas, TTL, runner isolation, authorization and audit remain independent mandatory gates.

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

To prepare a **separate, human-authored Git PR** from that unchanged review
packet, run `make scenario-pr-bundle SLUG=<slug>`. The CLI rechecks the
complete live verification batch, source SHA-256, review-packet status and
validated scenario content; it refuses changed YAML, tampered packets and
existing bundle output. It creates a private `.local/pr-bundles/<slug>/`
folder containing a curated-format `<slug>.json` and `PR_REVIEW.txt`.
The tracked `scenarios/` bank is **never** written by this command; an
operator must review and explicitly copy the JSON into a new Git branch,
then create a PR. The operator label and local evidence remain unauthenticated.

After any human-approved JSON enters a PR, CI now runs
`python -m cka_sim lab-smoke-all`: it discovers **every** tracked curated
scenario, proves the initial lab fails and the trusted fix passes in the
disposable `kind-cka-dev` cluster. Newly added scenarios cannot bypass the
live smoke simply because the CI list was not updated. On failure the PR
remains blocked; neither generator nor PR preparation is a publication API.

Currently both AI providers support `deployment_scale` and `service_selector`
only; the CPU-resource scenario is curated until its generator quality gates
are extended. `COUNT` is limited to 1–20 per invocation. Partial batches can
remain quarantined if later generated outputs fail. Never expose local Ollama
or developer Kubernetes endpoints to the Internet.

## OCI A1 guarded Workers AI operator factory (not deployed)

The remote scenario generator now has a separate **one-shot, backend-only**
entry point suitable for a future isolated OCI A1 staging worker:

```bash
make factory-plan COUNT=1 TYPE=deployment_scale    # no credentials, no AI calls
make factory-execute COUNT=1 TYPE=deployment_scale # explicit paid inference
```

A hardened staging runtime can now use a **noneditable installed wheel**:
`CKA_SCENARIO_SOURCE_ROOT=/opt/shadowops/cka-factory-staging/source` points
the wheel at the separate SHA-pinned, read-only curated JSON bank. The env path
must be absolute, existing and free of symlink components; local development
defaults remain unchanged. The application and its dependencies can therefore
live inside a separate root-owned Python venv rather than relying on an
editable import hook. The standalone staging installer must still be updated
to verify artifact provenance for this layout before host adoption.

The factory **never** invokes Ollama, Kubernetes or a public endpoint.
For a hardened A1 staging service, `make factory-plan FACTORY_WORKSPACE=/var/lib/shadowops/cka-factory-staging`
(and `make factory-execute FACTORY_WORKSPACE=...`) separates mutable state and
the YAML quarantine from the **read-only application checkout**. The workspace
must be a preprovisioned absolute non-symlink directory; its state and queue
remain under that single persistent root and cannot be redirected separately.
The local-development default stays unchanged when the option is absent.
Execution requires account-scoped Workers AI credentials from the backend
runtime, an allowlisted structured-JSON model and explicit opt-in. It uses a
local exclusive lock, limits each run to 2 requests, records up to **5 requests
per UTC day including failed attempts**, and refuses to exceed 5 pending YAML
proposals. Quota state resides under ignored `.local/`, and generated YAML
remains under ignored `scenarios/generated/`; production operators MUST
persist both paths securely across deployments. Local quotas are not
Cloudflare billing limits. No timer, worker deployment, secret provisioning,
public runner or auto-publishing is enabled by this source change.

See [OCI A1 factory handoff](docs/oci-a1-scenario-factory.md) for staging
environment requirements, persistence, budget and ownership gates.

## Go migration (in progress)

The new `go/` module is the first safe, independently tested step toward a
**Go-first backend and an ARM64 OCI runtime**. Its standard-library-only
scenario validator accepts the three curated JSON exercises and rejects
unknown fields, invalid names, unsafe prompt text and unsound initial states.
It does not run Kubernetes, serve HTTP or publish AI proposals.

```bash
cd go
go test ./...
go run ./cmd/cka-scenarios -bank ../scenarios
```

The separate **Go scenario contract** CI builds a Linux ARM64 executable
without CGO, in addition to running Go tests and static analysis. For now
the existing **Python backend is still authoritative** for hands-on labs and
grading; this is intentionally not a big-bang rewrite. The shared scenario
DSL, reviewed YAML quarantine and live-kind acceptance remain mandatory.

See [Go migration plan](docs/go-migration.md) for the incremental sequence,
test gates, security boundaries and future `client-go` handoff.

## Roadmap

1. Reliable VM-backed per-user ephemeral runner, auth, quotas and isolation.
2. Real PTY browser shell instead of restricted kubectl, plus exam sessions with authoritative server clock.
3. Automated batch quality evaluation (novelty, repeated namespace/slug detection, actual live smoke) and explicit human publication gate.
4. Expanded CKA domains and official blueprint mapping.

### Editing and filtering in staging labs

The owned lab terminal accepts `kubectl` and its `k` shorthand. Filter read output
with a single stdin-only GNU grep pipeline, for example `k get pods | grep -i Running`.
No file-search, recursive grep or general shell is exposed.

Use `kubectl edit svc/<name>` or `kubectl edit deploy/<name>` to open **real GNU
nano** on the live resource YAML. `nano svc/<name>` and `nano deploy/<name>` also
open that editor. Save with Ctrl+O / Enter and leave with Ctrl+X. Exiting without
saving leaves Kubernetes unchanged. The server accepts only the exercise's
Service selector or Deployment replica/CPU fields, retaining its identity and
resource version; invalid or stale edits do not overwrite other changes.

The editor has its own short-lived PTY, scrubbed environment, kernel filesystem
and syscall policy, resource limits, fresh Identity heartbeat and lease expiry. Heartbeats have a separate 18/minute
nonmutating budget; the existing command/cluster-operation budget is unchanged.
It receives no kubeconfig or service credential. Saving to Kubernetes requires
fresh authorization again. Disconnect, revocation, finish and expiry close the
editor. The deployment must verify the nano isolation probe on the actual A1
before replacing its current controller.
