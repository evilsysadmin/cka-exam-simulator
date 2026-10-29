# Agent instructions

- Build a hands-on CKA simulator, not a quiz app. Grades must derive from real Kubernetes state.
- Work in small PRs; inspect CI, fix failures and enable automerge where possible.
- Treat Ollama as untrusted. The LLM may only fill a strictly validated scenario schema, never scripts, manifests, Kubernetes commands, grading code or host paths.
- Never auto-publish generated scenarios. Require policy/schema review, disposable-cluster smoke proving initial failure and canonical solution success, and human review.
- No publicly exposed dev runner or kubectl access; production requires strong isolation, authentication, quotas, TTLs and abuse protection.
- Never execute against unknown Kubernetes contexts.
- Do not send initial fixture internals or reference solutions to candidates.
