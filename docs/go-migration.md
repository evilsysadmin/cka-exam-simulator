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

## Staged migration contract

1. **Now:** Go reads and validates curated JSON in parallel with Python.
   The Python backend remains authoritative for Kubernetes labs, terminal,
   deterministic grading, scenario generation and API endpoints.
2. **In progress:** Deterministic grading parity is now covered against 30
   shared, offline Kubernetes-state snapshots. Next port candidate-safe views
   and add further live-kind observer parity before any grading handoff.
3. Port isolated lab orchestration with `client-go`, preserving the
   fixed-kind development context and strict per-user runner isolation.
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
