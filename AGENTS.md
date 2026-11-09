# Agent instructions

## GitHub account routing (mandatory)

- For `evilsysadmin/*` repository operations (reads, writes, PRs, checks, merges and workflows), explicitly select GitHub connection **ShadowOps** (profile `evilsysadmin`). When the tool exposes `link_id`, use the **current session's** ID identified by `link_name: ShadowOps`; never rely on an implicit/default account when two are connected.
- **Nullforge** (profile `nullforgeops`) is a separate connection. Do not use it for ShadowOps repos or fall back silently. On connector errors, check the explicit ShadowOps account selection before claiming GitHub is unavailable.
- Verify repository owner/name and check SHA/CI before writes or release claims. Never store `link_id`, OAuth tokens, or other credentials in Git; connection IDs may rotate.

- Build a hands-on CKA simulator, not a quiz app. Grades must derive from real Kubernetes state.
- Work in small PRs; inspect CI, fix failures and enable automerge where possible.
- Treat all LLMs (local Ollama and Cloudflare Workers AI) as untrusted. Providers may only fill the same strictly validated scenario schema, never scripts, manifests, Kubernetes commands, grading code or host paths.
- CKA is local-only. Keep the GitHub repository/history and isolated CI; do not publish Pages, cloud images, DNS, tunnels or deploy account/lab services. Local startup must not contact Identity or infer a cloud provider from ambient credentials. Ollama remains an optional explicitly requested local scenario factory.
- Never auto-publish generated scenarios. Require policy/schema review, disposable-cluster smoke proving initial failure and canonical solution success, and human review.
- No publicly exposed dev runner or kubectl access; production requires strong isolation, authentication, quotas, TTLs and abuse protection.
- Never execute against unknown Kubernetes contexts.
- Do not send initial fixture internals or reference solutions to candidates.
