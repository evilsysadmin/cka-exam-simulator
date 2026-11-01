# CKA labs: source readiness and staging activation gates

Snapshot: 2026-11-01, `main` at `c5df16dad4b7b16fb51929a6bbdd078705aca376`. This document is an **evidence checklist**, not a deployment receipt. Re-check the latest SHA and workflows before using it.

## What is implemented and tested in the repository

- Three reviewed JSON scenarios (`scenarios/`); CI runs every curated scenario against disposable kind and checks that the broken initial state fails and the canonical repair passes.
- Python remains the authoritative candidate lab HTTP/controller path; Go already provides scenario validation, candidate projection, deterministic grading parity, live read-only observation and a durable lease subprocess. **Go is not a replacement public lab API.**
- `frontend/src/PilotLabs.tsx` and `labSession.ts` implement the dedicated staging browser flow: central token exchange on each operation, prepare, restricted `kubectl`, grade, finish, resume, and busy-slot display. The browser tests use **synthetic Identity/lab responses**, not live A1.
- `lab_api.py` supports an explicitly configured `authenticated-staging` mode and a rollback single-subject pilot. Every candidate operation requires fresh authoritative Identity evidence and lease ownership; global capacity is **one active lab**.
- `.github/workflows/lab-staging.yml` checks lab isolation, authorization/ownership and fail-closed boot and, on `main`, publishes a provenance-attested multiarch lab image. `.github/workflows/account-staging.yml` separately publishes the account image. A successful publish job is **not evidence of rollout**.
- Separately, `shadowops-infra` **already includes** a private rootless Compose contract, signed image admission, bounded kind bootstrap/inspection and CI that boots the published controller against real disposable Kubernetes with synthetic TLS Identity and two distinct users. These controls must not be reimplemented as though absent. [Infra staging guide](https://github.com/evilsysadmin/shadowops-infra/blob/main/docs/cka-lab-staging.md), [successful preflight workflow](https://github.com/evilsysadmin/shadowops-infra/actions/runs/41328656310).

Evidence for this snapshot: [merged PR #44](https://github.com/evilsysadmin/cka-exam-simulator/pull/44), [CI run](https://github.com/evilsysadmin/cka-exam-simulator/actions/runs/41327802012), [lab image workflow](https://github.com/evilsysadmin/cka-exam-simulator/actions/runs/41327802019), [account image workflow](https://github.com/evilsysadmin/cka-exam-simulator/actions/runs/41327802048).

## Not verified by source CI

No authenticated *real-user* complete run against the actual OCI A1 staging install is established here. In particular, source CI does not prove a provisioned Identity client and authoritative freshness service, accepted image digest, private Kubernetes bootstrap, installed runtime secrets, live HTTPS tunnel/DNS, Pages activation, release SHA parity, or real login/repair/grade/finish/revocation. These are independently owned operational gates.

Do not call this a usable public lab until a **separate, auditable staging acceptance record** links the running source/image digests, deployment receipts, and the end-to-end results. Never infer readiness from a green build, a container image, a DNS entry, or an HTTP 200 `/livez`.

## Next small Infra PR: operator-controlled private cluster bootstrap (after rootless runtime)

**Dependency:** [Infra PR #387](https://github.com/evilsysadmin/shadowops-infra/pull/387) is already working on the admin rootless-runtime bootstrap/inspection Make targets. **Do not duplicate it.** Check that PR's current status and merged source before starting this follow-up.

Target: `evilsysadmin/shadowops-infra`. The fixed private cluster bootstrap and read-only cluster verification already exist in `scripts/cka_lab_cluster.py`, and disposable CI already tests them. This proposed small PR only makes that **existing** next operator step repeatable and safely inspectable on the adopted A1. It must not deploy the controller, copy Identity secrets, modify Pages/Cloudflare DNS, enable the tunnel or activate public labs.

1. Add a single narrowly named operator `make` target to invoke the existing `install-tools`, `bootstrap`, and `verify-cluster` actions under the fixed `shadowops-cka-lab` account, through the reviewed protected Infra checkout and the currently approved SSH/operator path. Add an independent **read-only** verify target.
2. Before any mutation, verify the exact reviewed source SHA, expected rootless runtime receipt/policy, private lab identity, sufficient available capacity, and that no lab cluster is already installed; fail rather than adopt/delete unfamiliar state. Never fall back to host Docker, a different Kubernetes context or a privileged runner.
3. Tests with synthetic SSH/Git/process fixtures must reject a wrong target, invalid source, missing runtime, existing/unexpected cluster, and failed bootstrap; propagate errors without claiming success, reset, cleanup or a public route. Keep all passwords, OCI credentials and kubeconfig out of logs.
4. Preserve the current cluster limits, internal-only network, pinned images, quota-one lease design and safety contracts; document the already implemented read-only `CKA_LAB_CLUSTER_VERIFIED` marker and rollback boundary.

**Acceptance:** operator action is explicit, exact-source and fail-closed; the same bounded private cluster is verified by the existing inspector; an unready A1 stops before public exposure; offline tests and the existing Infra rootless + published Compose CI are green; no account/Identity or user-facing deployment is claimed. If `#387` has not merged or the installed rootless-runtime receipt is missing, the operator invocation must remain gated.

This is a proposal, **not an assertion that the host bootstrap is installed** or that a real lab is reachable.

## Later activation and live acceptance (separate, gated change)

Once Identity/account staging and the private Kubernetes runner are actually installed and verified, Infra can explicitly enable the dedicated lab route and staging frontend with tested origin/CORS and the reviewed `authenticated-staging` configuration. With two real staging accounts, record: successful login and token exchange; initial failing grade; terminal repair and passing grade against Kubernetes state; finish and verified cleanup; second account blocked while the first owns the slot, then able to prepare after cleanup; expiry/restart recovery; logout/revocation of an otherwise unexpired token denies every operation. Include source SHA, digest and timestamp, without secrets, tokens, candidate data or private namespaces.

Production, candidate-paid access, arbitrary shell/PTY, public kubectl, multi-user capacity and auto-published AI scenarios are all **out of scope**.
