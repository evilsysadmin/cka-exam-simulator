# OCI A1 scenario factory handoff (not yet deployed)

**Source owner:** `evilsysadmin/cka-exam-simulator`. **Infrastructure owner:** `evilsysadmin/shadowops-infra`.

The publicly deployed `cka.shadowops.dpdns.org` site is **static/read-only**.
This CLI is for a **separate backend-only OCI A1 operator process**. It does
not provide authenticated CKA sessions, Kubernetes terminals, or user APIs.
Do not enable the service until infrastructure ownership, staging secrets and
a persistent data directory are explicitly provisioned and validated.

## Local staging preflight (no inference)

Install the Python package, then from the CKA source root:

```bash
make factory-plan COUNT=1 TYPE=deployment_scale
```

This requires **no Cloudflare credentials** and makes **no inference requests**.
Output is a structured JSON plan without secrets or prompt text.

## Explicit inference, only after infrastructure gates

The OCI host needs only a Python runtime and outbound HTTPS access to
`api.cloudflare.com:443`; **do not install Ollama or depend on an A1 GPU**.
An isolated least-privilege OS service account must own only its data path,
with dedicated runtime secret injection of `CLOUDFLARE_ACCOUNT_ID` and
`CLOUDFLARE_API_TOKEN` (Workers AI Read, restricted account), plus
`CKA_SCENARIO_PROVIDER=workers-ai`. Never store these in Git, Compose,
systemd unit files, public Cloudflare Pages, or shell command arguments.

```bash
make factory-execute COUNT=1 TYPE=deployment_scale
# Outputs only scenarios/generated/*.yaml, never scenarios/*.json
make scenario-audit
```

**Safety caps per shared persistent installation:** one-shot invocation,
1–2 inferencing attempts per run, at most 5 attempts per UTC day (failures
count), and at most 5 proposals retained in quarantine. Concurrent factory
calls refuse to run; the advisory lock and daily budget are under `.local/`.
Corrupt or future-dated budget state fails closed. An operator must preserve
both `.local/factory-budget.json` and `scenarios/generated/` across
deployments, otherwise these are NOT durable quotas. External Cloudflare
account limits, billing controls and monitoring are additionally required.
Never scale this local quota across instances and assume a global cap.

There is intentionally **no timer/scheduler enabled by this repository**.
A future controlled OCI staging timer may call one execution at a time only
after explicit infra configuration, idempotency review and cost controls.
Do not auto-run `scenario-verify` on OCI A1: it requires a dedicated disposable
`kind-cka-dev` cluster with strict isolation and is separate from inference.
No inference call may access an exam cluster, kubeconfig or the public browser.

Proposals remain untrusted after generation and MUST pass all existing gates:
offline YAML + policy audit; live disposable-kind smoke proving initial failure
and canonical solution success; fresh SHA-256 review queue; explicit human
approval; and a separate human-reviewed Git PR. Green factory status alone
never implies that an exercise is safe, correct or publicly available.

## Staging adoption checklist

1. CKA repo issue #9: stage restricted Cloudflare Workers AI token / account ID
   as **backend secrets**, not the Pages publisher credentials by assumption.
2. Provision an isolated OCI process account, writable persistent quarantine,
   private `.local` state, explicit single-run dispatch, outbound HTTPS and
   monitored Cloudflare usage/budget.
3. Confirm the selected model supports Workers AI JSON Mode using the official
   Cloudflare docs; dry-run the CLI, then explicitly make **one** staging call.
4. Inspect the generated YAML, run `make scenario-audit`, and capture only
   nonsecret success/failure counters in operational logs.
5. Only afterward design a separate isolated ephemeral Kubernetes validation
   runner. Do not expose a user-facing lab until ShadowOps Identity and
   per-user sandbox isolation are independently complete.

Cloudflare structured JSON mode:
https://developers.cloudflare.com/workers-ai/features/json-mode/

Cloudflare Workers AI rate and resource limits:
https://developers.cloudflare.com/workers-ai/platform/limits/
