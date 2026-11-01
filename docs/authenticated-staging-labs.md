# Authenticated CKA staging POC

The playable POC accepts active ShadowOps Identity accounts with a correctly
signed CKA-audience token and an authoritative live account/session/JTI check.
A username, decoded browser claim or valid signature alone never grants a lab.
The existing stricter single-UUID pilot remains available for rollback.

## Explicit operator activation

The independent lab service reads only `/run/secrets/cka-lab-staging.json`.
For the authenticated POC, its exact fields are:

- `environment`: `staging`.
- `access_mode`: `authenticated-staging`.
- `build`: the exact CKA source SHA belonging to the admitted image digest.
- `jwks`: independently provisioned public staging Identity verification keys.
- `service_credential`: the registered CKA-only session-freshness credential.

This mode must not contain `pilot_subject`. A legacy private configuration with
`pilot_subject` and no `access_mode` retains the single-UUID pilot restriction.
Unknown modes, contradictory fields and missing policy fail closed. There is
no browser setting, anonymous fallback or automatic account linking.

## One shared lab, separate ownership

The existing A1 budget permits one active lease globally. Other authenticated
users can view the candidate-safe scenario bank and see that the slot is busy;
they cannot see its namespace/lease ID, restore it, execute commands, grade it
or finish it. The browser disables preparation until the slot is available.
After verified cleanup, another account can start its own random namespace.

Every candidate operation verifies current Identity state again. A global
authority budget and a per-subject budget bound upstream work. A local budget
exhaustion returns 429 with `Retry-After`, so the browser retains its terminal
and countdown instead of treating throttling as logout. A real authentication
denial still removes the active view. Cookie/JWT storage, admin credentials,
Docker authority and fixture/reference-solution data never reach the browser.

## Staging delivery

The image and the dedicated `lab-staging` frontend build are independent
deliverables. Infra owns the first rootless installation on the existing A1,
private Kubernetes bootstrap, signed-image admission, narrow private config,
dedicated lab tunnel/DNS route, Compose rollout and frontend activation.
No new OCI VM or paid capacity is part of this POC.

Source CI covers two signed authenticated subjects, ownership denial, global
capacity/cleanup and the shared-slot UI. Actual deployment acceptance must
still run on the A1 with real Identity: login, prepare, terminal repair, live
grade, finish/expiry cleanup, another user's attempt, and revocation of a still
unexpired token after logout. A CI or publication receipt is not deployment.
