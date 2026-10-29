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

The Go dependency graph is downloaded from the standard Go module proxy and
verified by the checksum database during CI. **No OCI installation, public
API endpoint, new Kubernetes RBAC policy or production auth has been
configured.** Before any real multitenant rollout we must provision
restricted read-only Kubernetes credentials, restrict target namespaces and
validate per-user isolated runners. The current kind kubeconfig still has
development privileges; this code only exercises read methods, not a
production-grade Kubernetes RBAC boundary.

## Staged migration contract

1. **Now:** Go reads and validates curated JSON in parallel with Python.
   The Python backend remains authoritative for Kubernetes labs, terminal,
   deterministic grading, scenario generation and API endpoints.
2. **Covered offline:** Deterministic grading parity on 30 shared
   Kubernetes snapshots, and candidate-safe view parity for the curated bank
   are independently checked against Python. Next: a pinned, read-only
   live-kind resource observer before any Go grading handoff.
3. **In progress:** client-go now supports an allowlisted, read-only
   observer for fixed kind-cka-dev and live-kind grading parity. Next port
   carefully isolated lab orchestration; never reuse the powerful local kind
   kubeconfig as production per-user credentials.
4. Port the operator-only Workers AI scenario factory, quarantine, budget
   and review gates to Go, preserving the no-auto-publication rule.
5. Only once equivalent integration and live-kind acceptance are green,
   review the OCI staging service as a Go ARM64 executable instead of the
   Python venv. This requires a *separate* ShadowOps Infra deployment PR.
   Keep a reversible transition; **do not activate both factories**.

The Go validator is deliberately stricter in two places: an absent or empty
bank fails, and curated files that are symlinks or share a namespace fail.
The current bank passes both languages. The code is not a production
public service, grader or authentication system.

No existing deployment, Python CLI, frontend, or OCI A1 runtime has changed.
