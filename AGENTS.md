# Agent instructions

- Build a hands-on CKA simulator, not a quiz app. Grades must derive from real Kubernetes state.
- Work in small PRs; inspect CI, fix failures and enable automerge where possible.
- Treat all LLMs (local Ollama and Cloudflare Workers AI) as untrusted. Providers may only fill the same strictly validated scenario schema, never scripts, manifests, Kubernetes commands, grading code or host paths.
- Use Ollama only for local development, and Workers AI for OCI A1 generation. Keep Cloudflare API tokens only in backend secrets; never send them to clients or store them in repository files. No remote inference on public candidate routes.
- Never auto-publish generated scenarios. Require policy/schema review, disposable-cluster smoke proving initial failure and canonical solution success, and human review.
- No publicly exposed dev runner or kubectl access; production requires strong isolation, authentication, quotas, TTLs and abuse protection.
- Never execute against unknown Kubernetes contexts.
- Do not send initial fixture internals or reference solutions to candidates.
