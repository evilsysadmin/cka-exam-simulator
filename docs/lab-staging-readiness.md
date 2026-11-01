# CKA labs: source readiness and staging activation gates

Snapshot: 2026-11-01, `main` at `c5df16dad4b7b16fb51929a6bbdd078705aca376`. This document is an **evidence checklist**, not a deployment receipt. Re-check the latest SHA and workflows before using it.

## What is implemented and tested in the repository

- Three reviewed JSON scenarios (`scenarios/`); CI runs every curated scenario against disposable kind and checks that the broken initial state fails and the canonical repair passes.
- Python remains the authoritative candidate lab HTTP/controller path; Go already provides scenario validation, candidate projection, deterministic grading parity, live read-only observation and a durable lease subprocess. **Go is not a replacement public lab API.**
- `frontend/src/PilotLabs.tsx` and `labSession.ts` implement the dedicated staging browser flow: central token exchange on each operation, prepare, restricted `kubectl`, grade, finish, resume, and busy-slot display. The browser tests use **synthetic Identity/lab responses**, not live A1.
- `lab_api.py` supports an explicitly configured `authenticated-staging` mode and a rollback single-subject pilot. Every candidate operation requires fresh authoritative Identity evidence and lease ownership; global capacity is **one active lab**.
- `.github/workflows/lab-staging.yml` checks lab isolation, authorization/ownership and fail-closed boot and, on `main`, publishes a provenance-attested multiarch lab image. `.github/workflows/account-staging.yml` separately publishes the account image. A successful publish job is **not evidence of rollout**.

Evidence for this snapshot: [merged PR #44](https://github.com/evilsysadmin/cka-exam-simulator/pull/44), [CI run](https://github.com/evilsysadmin/cka-exam-simulator/actions/runs/41327802012), [lab image workflow](https://github.com/evilsysadmin/cka-exam-simulator/actions/runs/41327802019), [account image workflow](https://github.com/evilsysadmin/cka-exam-simulator/actions/runs/41327802048).

## Not verified by source CI

No authenticated *real-user* complete run against the actual OCI A1 staging install is established here. In particular, source CI does not prove a provisioned Identity client and authoritative freshness service, accepted image digest, private Kubernetes bootstrap, installed runtime secrets, live HTTPS tunnel/DNS, Pages activation, release SHA parity, or real login/repair/grade/finish/revocation. These are independently owned operational gates.

Do not call this a usable public lab until a **separate, auditable staging acceptance record** links the running source/image digests, deployment receipts, and the end-to-end results. Never infer readiness from a green build, a container image, a DNS entry, or an HTTP 200 `/livez`.

## Next small Infra PR: fail-closed private A1 lab admission (no public activation)

Target: `evilsysadmin/shadowops-infra`, after inspection of its current `AGENTS.md` and deployment conventions. One PR, limited to the **lab API runtime only**, on the existing A1. This does not deploy account/Identity, turn on Pages, create new OCI capacity, or open public routes.

1. Declare a rootless private lab service pinned to an **approved immutable digest** and exact CKA source SHA, with a non-writable root filesystem, minimal writable journal, fixed private configuration file path and no Docker socket or general Internet egress.
2. Make deployment/admission fail closed on absent/invalid policy, mismatched SHA/digest, unready cluster reconciliation or missing pre-provisioned, explicitly allowlisted Kubernetes context. Never create a permissive fallback.
3. Add an **offline/CI contract test** proving the wiring rejects missing/mismatched inputs and cannot inadvertently publish a route, privileged dev API or generation endpoint.
4. Provide rollback to the previous private deployment or disabled state. Produce an operator-only installation/handoff recipe that does not print credential values.
5. Preserve current single-slot quota, namespace lease journal and cleanup semantics; no candidate-visible change.

**Acceptance:** exact digest and source SHA are recorded; admission cannot silently fall back to a floating tag; missing configuration fails before serving candidate operations; no public DNS/ingress or browser change; CI tests and current CKA image/security checks are green; an independent operator can verify `/readyz` and rollback *privately* without storing tokens in logs. Mark the PR **infrastructure prepared**, not **labs live**.

## Later activation and live acceptance (separate, gated change)

Once Identity/account staging and the private Kubernetes runner are actually installed and verified, Infra can explicitly enable the dedicated lab route and staging frontend with tested origin/CORS and the reviewed `authenticated-staging` configuration. With two real staging accounts, record: successful login and token exchange; initial failing grade; terminal repair and passing grade against Kubernetes state; finish and verified cleanup; second account blocked while the first owns the slot, then able to prepare after cleanup; expiry/restart recovery; logout/revocation of an otherwise unexpired token denies every operation. Include source SHA, digest and timestamp, without secrets, tokens, candidate data or private namespaces.

Production, candidate-paid access, arbitrary shell/PTY, public kubectl, multi-user capacity and auto-published AI scenarios are all **out of scope**.
