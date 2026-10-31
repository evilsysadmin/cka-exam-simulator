# Migration to Go · scenario contract first

**Status:** First incremental Go slice, not the active student lab backend.

The Go module in `go/` validates the **same curated JSON scenario DSL**
currently used by the Python/Pydantic backend. No new YAML, Kubernetes commands,
manifests, grader code or browser APIs are accepted from the AI.

## Scope of this PR

- `go/internal/scenario` mirrors required fields, strict JSON types,
  allowlisted scenario types, DNS-safe names, time/replica bounds, forbidden
  candidate display text, and wrong-initial-state invariants.
- `go/cmd/cka-scenarios -bank ../scenarios` validates the curated bank
  offline, rejecting unknown fields, nonregular JSON entries, filename
  mismatches and duplicate namespaces.
- Go tests cover all three currently curated scenarios, negative schema and
  injection cases, and fail-closed bank handling.
- A separate workflow runs Go tests with the race detector, `go vet`,
  checks the live repository's three curated JSON files, and cross-compiles
  a **CGO-free Linux ARM64** validator. No cloud credentials, Docker,
  Kubernetes cluster, deployment or GPU is needed.

Run locally:

```bash
cd go
go test ./...
go run ./cmd/cka-scenarios -bank ../scenarios
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o /tmp/cka-scenarios-arm64 ./cmd/cka-scenarios
```

## Milestone 2: deterministic grading parity on Kubernetes snapshots

Go now has an **offline, read-only** `internal/grading.State` evaluator
for all three curated scenario types, matching the current Python
`cka_sim.grading.grade_state` check order, Spanish labels, 100/0 scores,
replica availability, EndpointSlice readiness and observed rollout freshness.

The shared `go/testdata/grading-cases.json` corpus covers **30** successful
and failing Kubernetes API snapshots, including absent resources, stale
rollouts, unexpected extra Service selector fields, nullable endpoints and
missing CPU resources. CI executes `go/cmd/cka-grade` against this corpus and
compares its **complete JSON output** with Python on exactly the same
snapshots; any grading difference fails the PR. Go unit tests also check
the expected outcomes independently.

Locally, from `go/` with the existing Python package installed:

```bash
go test ./...
go run ./cmd/cka-grade -bank ../scenarios \
  -fixtures testdata/grading-cases.json > /tmp/cka-go-grading.json
python ../scripts/check_go_grading_parity.py \
  --fixtures testdata/grading-cases.json \
  --go-report /tmp/cka-go-grading.json --bank ../scenarios
```

This **does not** yet use `client-go` or read a live Kubernetes cluster.
Python remains authoritative for actual student sessions and kind testing.
The next PR should port candidate-safe views, then add a read-only
`client-go` observer against the explicitly pinned development context
and compare live cluster snapshots. Never enable a public grader or remote
student terminal based solely on offline parity tests.

## Milestone 3: safe candidate presentation (no API handoff)

The Go `scenario.Candidate` / `CandidateBank` functions now return an
explicitly allowlisted **eight-field** JSON projection: `slug`, `title`,
`story`, `difficulty`, `minutes`, `domain`, `statement` and
`namespace`. This exactly mirrors the current Python
`Scenario.candidate_view()` contract, including Spanish task statements
for scaling, Service selectors and scheduling resource fixes.

The view never embeds a full `Scenario`; internal initial/target selectors,
initial replicas, scenario implementation type, deployment metadata and
grading/solution data cannot become JSON fields accidentally. The candidate
statement intentionally explains the requested outcome (including desired
replicas or CPU values), **not the initial fixture or secret answer code**.
Unvalidated Go scenarios are rejected before rendering.

The offline `go/cmd/cka-candidate-views` executable emits sorted public
projections. CI compares its complete JSON output field-for-field with
Python's reference for **every curated scenario**, also verifying an exact
public-field allowlist. Go regression tests explicitly check both the
serialized JSON keys and full Spanish statements; the CLI also builds
statically for Linux ARM64.

```bash
cd go
go run ./cmd/cka-candidate-views -bank ../scenarios > /tmp/cka-candidate-views.json
python ../scripts/check_go_candidate_parity.py \
  --bank ../scenarios --go-report /tmp/cka-candidate-views.json
```

**No Go HTTP server, student session or Kubernetes permission** is enabled
by this milestone. Python still owns the running dev API and grading.

## Milestone 4: read-only client-go observer for the dedicated kind lab

The Go `internal/observe` package introduces the first **actual Kubernetes
API read path** using `k8s.io/client-go` and `k8s.io/apimachinery`, without
any API server, exam session, shell executor or Kubernetes mutation operation.

- Accepts only an **explicit absolute**, private, non-symlink kubeconfig
  using the dedicated `kind-cka-dev` context, exactly one cluster and one
  embedded-cert user. It rejects remote/DNS Kubernetes endpoints, non-HTTPS
  connections, proxy URLs, insecure TLS, credential-file paths, bearer tokens,
  exec/auth-provider plugins, extra contexts and impersonation.
- Reads exactly the curated scenario's `apps/v1 deployments`, `v1 services`
  and `discovery.k8s.io/v1 endpointslices` using `GET` and `LIST` only.
  Service EndpointSlices are selected by the exact
  `kubernetes.io/service-name` label. It does not list other namespaces,
  watch events, or expose arbitrary GVRs.
- Missing Deployments/Services produce a **failed grade** (Python parity);
  forbidden API reads and incomplete lists fail closed rather than giving
  the candidate an imaginary result.
- Normalizes Kubernetes API JSON into the already 30-snapshot-parity-tested
  Go evaluator. `go/cmd/cka-observe` requires explicit `-dev-live`, an
  approved scenario slug, a private kubeconfig path and a curated bank. It
  prints only the deterministic grade, never the raw object or credentials.
- Unit tests cover unsafe context/auth options and exact allowed read
  operations with injected fake readers. The existing disposable-kind CI
  provisions and solves one real Deployment using **Python only**;
  Go observes both states and must agree with the reference grader after
  rollout readiness. The live-kind observer is never invoked on an unknown
  context, or outside the explicit CI/test operator flow.

The exact client-go dependency graph is now checked into `go/go.mod` and
`go/go.sum` with module checksums. CI runs `go mod tidy` and fails if
either tracked lock file changes, then runs `go mod verify`. The live-kind
test downloads and verifies those pinned dependencies before building. This
establishes reproducible module versions/checksums; it does not replace
artifact signatures, SBOM review or vulnerability scanning. **No OCI installation, public
API endpoint, new Kubernetes RBAC policy or production auth has been
configured.** Before any real multitenant rollout we must provision
restricted read-only Kubernetes credentials, restrict target namespaces and
validate per-user isolated runners. The current kind kubeconfig still has
development privileges; this code only exercises read methods, not a
production-grade Kubernetes RBAC boundary.

## Milestone 5: actual namespace-scoped Kubernetes RBAC

The Go `cka-observe -dev-live` CLI now **requires** a short-lived bearer
token for the `cka-grade-observer` ServiceAccount of the **exact scenario
namespace**, rather than forwarding the powerful developer client certificate.
The kubeconfig still must contain a single `kind-cka-dev` context, literal
loopback HTTPS server, embedded CA, no proxy, no exec plugins, no client
certificates and no external credential paths. The expected JWT ServiceAccount
subject is checked locally as an early guard; **Kubernetes itself verifies the
token signature and enforces Role/RoleBinding authorization**. Parsing a JWT
claim is not a substitute for cryptographic validation by the API server.

In disposable `kind` CI, trusted Python setup creates a per-exercise
ServiceAccount, namespace Role and RoleBinding. The Role grants only
`get deployments.apps` with `resourceNames: [payment-api]`,
`get services` with `resourceNames: [payment-api-svc]`, and
`list endpointslices.discovery.k8s.io` **within that namespace**.
Kubernetes RBAC cannot constrain a LIST using the request's label selector,
so all EndpointSlices in this dedicated exercise namespace are observable;
this **requires one isolated namespace per exam context** and is not safe
for a shared tenant namespace. It has no watch, list-deployment, write,
secrets, cluster or cross-namespace permissions.

The Python test harness requests a bounded-duration ServiceAccount token,
constructs a private, ephemeral kubeconfig, runs real
`kubectl auth can-i` SelfSubjectAccessReviews for required rights and
explicit denials, then compares Go and Python grades before/after a live
Deployment repair. The original administrator kubeconfig remains inside
the trusted test harness solely for setup/solution/cleanup. The Go CLI
is never given it.

**This is still a development-only isolation proof**, not a public exam
runner: no end-user identity, isolation across distinct cluster tenants,
per-user resource limits or production-safe RBAC provisioner has been
deployed. Never reuse the kind control-plane administrator certificate
for user-facing operations. No OCI or Workers AI inference occurs.

## Milestone 6: internal Go exam-session lease lifecycle (no HTTP or Kubernetes mutations)

A new internal `go/internal/examsession` memory store implements a bounded
session state machine: **active → expired/closed → cleanup pending → confirmed
cleanup**. It is only a deterministic foundation for a future controller,
not a user-login endpoint, not an Identity authorization service, and not a
durable database.

A trusted caller must supply a **currently authenticated and authorized,
canonical UUIDv4 subject** (the store only checks UUID syntax; it does NOT
verify tokens or current session-version/revocation status). `Start` atomically
enforces one outstanding lease per owner, a fixed 256-entry cap, validated
curated exercise duration and CSPRNG-generated independent session IDs and
namespaces. Every candidate receives a separate `cka-lab-<random>`
namespace rather than the static namespace from the curated bank; the
associated scenario is copied and revalidated with that new namespace.
The original curated exercise is never modified.

`Status`, `ActiveScenario` and `Finish` require matching owner and
session ID; foreign/unknown IDs return an indistinguishable denial.
Deadlines use Go's monotonic clock where available, expiry prevents
further access, and **expired or finished leases continue occupying their
owner slot until the trusted reaper has independently verified removal of
all namespace resources** and calls `ConfirmCleanup` with both the exact
lease ID and namespace. `CleanupPending` only reports outstanding cleanup;
it does not delete resources, mint tokens, or perform network operations.
The narrow owner-visible `View` JSON omits namespace, internal scenario
fields and credentials.

Go's race-enabled CI covers session ownership, expiration boundaries,
repeated cleanup, failed entropy, global capacity, random namespace
uniqueness and concurrent attempts for one/many owners.

**Memory store limitations:** Memory state is lost if the process
restarts; no durable lease journal, cleanup retry engine, reservation
recovery, per-tenant quotas, real Identity revocation/freshness enforcement,
Kubernetes provisioner or user-facing API exists. Do not wire these methods
to the public HTTP server before those independent gates are verified.
A validated JWT signature alone **does not authorize** opening a lab, even
if it contains a UUID matching the required input type.

## Milestone 7: durable Linux controller lease journal

`examsession.OpenJournal` adds a local disk journal around the same owner-bound
lease lifecycle, without changing the development API or deploying a runner.
The caller supplies an operator-created, private `0700` directory owned by the
process and an explicit capacity; the intended `evilsysadmin` staging pilot uses
capacity **one**. A username is not an owner identity or an authorization grant.
The controller must separately verify the UUID subject and live Identity session.

Each reservation is written to a private `0600` temporary file, flushed, atomically
renamed and followed by a directory flush **before Start returns a lease**. An
exclusive process lock prevents multiple controllers using the same directory.
Any write failure disables the journal instance, including reads that could
authorize further execution; a repaired filesystem alone does not resume it.

On reopening, every previous lease becomes **closed, pending cleanup**, including
unexpired ones. Its owner and capacity slot remain reserved until a trusted
controller independently verifies Kubernetes resource deletion and persists
`ConfirmCleanup`. The journal performs no Kubernetes deletion itself. Repeating
cleanup after a failed acknowledgement is required and safe; it must never be
treated as proof that a namespace has already disappeared.

Missing state beside an existing lock, invalid/oversized JSON, duplicate keys,
invalid/duplicate owners, IDs or namespaces, inconsistent deadlines, unsafe file
permissions, links or an incompatible capacity all stop recovery. Do not delete
the journal or lock to unblock provisioning: reconcile orphan namespaces and
credentials first. Use a local Linux filesystem with flock, atomic rename and
fsync, not NFS; controller state must live outside the checkout and be backed up
and restored together with reconciliation of the runner.

Race-enabled behavior tests include concurrent starts, process lock exclusion,
abrupt subprocess exit without Close, restart recovery, TTL, corrupt state,
storage failure and failed cleanup acknowledgement. They establish durable
reservation semantics; they do not establish power-loss behavior of a particular
storage device, Kubernetes isolation or a deployed public lab. Fresh per-operation
authorization, a runner/reaper, admission/quotas, constrained candidate transport,
actual kind scenario/isolation acceptance and an Infra handoff remain required.

## Staged migration contract

1. **Now:** Go reads and validates curated JSON in parallel with Python.
   The Python backend remains authoritative for Kubernetes labs, terminal,
   deterministic grading, scenario generation and API endpoints.
2. **Covered offline:** Deterministic grading parity on 30 shared
   Kubernetes snapshots, and candidate-safe view parity for the curated bank
   are independently checked against Python. Next: a pinned, read-only
   live-kind resource observer before any Go grading handoff.
3. **In progress:** client-go now supports an allowlisted, read-only
   observer with **real namespace-scoped Role/RoleBinding and ServiceAccount
   token** tested in the fixed kind-cka-dev cluster. Next: an isolated,
   authenticated multi-user lab controller and admission controls. Do not
   treat the local-kind role bootstrap as production provisioning.
4. **Foundation only:** Internal owner-bound Go session leases, independent
   randomly allocated namespaces, TTL and cleanup acknowledgements are
   covered by race-enabled tests, with an optional durable Linux journal and
   restart-to-cleanup recovery. Next: authenticated Identity revocation checks
   in the runner and trusted Kubernetes cleanup/provisioning.
   Do not treat the in-memory package as a production controller.
5. Port the operator-only Workers AI scenario factory, quarantine, budget
   and review gates to Go, preserving the no-auto-publication rule.
6. Only once equivalent integration and live-kind acceptance are green,
   review the OCI staging service as a Go ARM64 executable instead of the
   Python venv. This requires a *separate* ShadowOps Infra deployment PR.
   Keep a reversible transition; **do not activate both factories**.

The Go validator is deliberately stricter in two places: an absent or empty
bank fails, and curated files that are symlinks or share a namespace fail.
The current bank passes both languages. The code is not a production
public service, grader or authentication system.

No existing deployment, Python CLI, frontend, or OCI A1 runtime has changed.

## Milestone 8: private controller pipe over the durable Go journal

`go/cmd/cka-lease-stdio` now makes the existing Linux journal usable by the
trusted Python lab controller without implementing a second lease store.
It runs as one persistent child process over private stdin/stdout, with no
listener, Kubernetes access or user-facing endpoint. Capacity is fixed to one.
The caller must supply owner UUIDs only after fresh Identity authorization.

The strict, bounded line protocol supports start/status/active scenario,
finish/pending cleanup and cleanup confirmation. Duplicate/unknown fields,
nested/nonstring values, uncurated scenarios, foreign owners and oversized
requests are denied. A broken journal stops the process. Restart uses the
journal's existing rule: every prior lease is closed and still occupies capacity
until Kubernetes deletion has been independently verified and confirmed.

`cka_sim.lease_process.LeaseProcess` owns that child, serializes bounded
request/reply calls, enforces deadlines and reply schemas, and kills/poisons
the adapter on framing, transport or journal failure. It never automatically
restarts over an uncleaned lab. No password, JWT, SOC or Docker authority is
sent through this pipe. The active scenario/cleanup methods are private
controller data and must not be exposed as browser routes.

Go race tests exercise the actual journal through the protocol, including
owner isolation, capacity and restart recovery. CI compiles the real helper,
runs the Python-to-Go persistence/cleanup handshake, and builds Linux ARM64.
Local synthetic-pipe tests additionally cover timeouts, malformed/oversized
replies and refusal to continue after journal failure.

This wires the lease engine for the shared-A1 Compose pilot; it does not install
that runtime, create Kubernetes namespaces, expose a terminal or grant lab
access. Namespace preparation, candidate RBAC/admission/network policy,
verified deletion, per-operation Identity authorization and browser acceptance
remain separate controller responsibilities.
