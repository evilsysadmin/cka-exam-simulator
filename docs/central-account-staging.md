# Central account staging handoff

The account service is separate from `cka_sim.api`: the isolated image copies
only the account and Identity verification modules, never the developer lab,
terminal, scenario factory, leases, Kubernetes client or curated fixtures.
Its only authenticated route is `GET /api/account/me`. It has no database,
product registration/password, local session, admin grant or lab permission.
The stable account key is Identity's UUID, not a legacy username.

Infra owns deployment at `cka-api-staging.shadowops.dpdns.org` and the separate
frontend at `staging.cka.shadowops.dpdns.org`; the existing public read-only demo
is unchanged. No application merge publishes or enables this service.

The operator supplies the fixed read-only `/run/secrets/cka-account-staging.json`
with exactly `environment=staging`, independently provisioned public `jwks`,
the CKA-only `service_credential` and a 40-character `build` matching the image's
source SHA. Never reuse the Chronicles SOC, mount the whole Identity bundle,
accept a key URL from a token, or put any service proof into a browser build.

Every `/api/account/me` verifies the pinned Ed25519 key, exact staging issuer,
CKA audience, UUID, `sv`, `jti`, times and 120-second lifetime, then calls the
fixed HTTPS freshness endpoint with the separate CKA SOC. The nine-field reply
must include audience `cka-simulator` before projection to the established
eight-field evidence contract. No freshness result is cached. Redirects, proxy
environment, injected cookies, duplicate/oversized JSON, revocation, missing
authority and stale/mismatched evidence deny. Expiry is rechecked after RPC.
Network phases have two-second deadlines; a two-second elapsed budget rejects
slow/streaming replies. The process bounds concurrent RPCs to four, checks to
60/minute globally and 12/minute per verified UUID; unverified claims never
reach the authority. Run one pilot worker with bounded HTTP concurrency.

The browser may send only its exact staging Origin and Authorization header.
Cookies, query credentials, duplicate authorization and other origins deny;
the backend never issues a browser cookie. Identity's own host-only HttpOnly
cookie remains at `id-staging.shadowops.dpdns.org`. Login/registration/logout
belong there, with app `cka-simulator` and the pinned CKA staging return URL.

Acceptance requires the admitted signed multiarch image, a recent Identity
backup, explicit CKA consumer registration, exact-source API/Pages receipts,
and real desktop/mobile login, cross-product silent SSO and logout/revocation
tests. Labs remain unavailable after successful account authentication.

## Browser build

`npm run build:account-staging` produces only `dist-account-staging`, with
mode `account-staging-preview`. Account UI runs solely at the exact staging
origin; the ordinary `build:public` remains the static demo without account
requests. No password form, tokens in storage/URLs or SOC is shipped. The
client calls central token exchange using only Content-Type and checks the
account API with Authorization only; all grants come from the backend.

The UI checks on entry, visibility and each visible minute. Logout cancels old
checks and blocks silent restoration; it reports success only after Identity
returns 204. A failed logout remains visible and can be retried. The browser
smoke uses actual built React UI with explicitly synthetic service responses
at desktop and mobile sizes; live cross-product SSO requires separate public
acceptance after Infra deployment.
