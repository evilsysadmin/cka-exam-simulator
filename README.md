# CKA Exam Simulator

Local-first, hands-on Kubernetes practice with deterministic grading and an Ollama scenario proposal generator.

## Local developer preview (not safe for public hosting)

Requirements: Python 3.11+, Docker Engine, kind, kubectl; Ollama optional.

1. Install: python -m pip install -e '.[dev]'
2. Test: python -m pytest && python -m cka_sim validate-bank
3. Create dedicated lab: python -m cka_sim lab-up
4. Prepare example: python -m cka_sim lab-setup scale-payment-api
5. Inspect: kubectl --kubeconfig .local/kubeconfig --context kind-cka-dev get deployments -n cka-payments
6. Fix: kubectl --kubeconfig .local/kubeconfig --context kind-cka-dev scale deployment/payment-api -n cka-payments --replicas=3
7. Grade: python -m cka_sim lab-grade scale-payment-api
8. Clean: python -m cka_sim lab-clean scale-payment-api
9. Remove dedicated cluster: python -m cka_sim lab-down

Automated real-cluster validation (requires working kind cluster and images):
- python -m cka_sim lab-smoke scale-payment-api
- python -m cka_sim lab-smoke fix-catalog-service

Run the read-only scenario API and gated local developer API:
- CKA_ENABLE_LOCAL_LAB=1 uvicorn cka_sim.api:app --host 127.0.0.1 --port 8000
- GET /api/scenarios exposes only candidate-facing data, not solution details.
- Developer POST endpoints require both loopback origin and X-CKA-Lab-Request: 1 request header. This is NOT authentication or production security.

Generate local Ollama proposals:
- python -m cka_sim generate --model YOUR_LOCAL_MODEL --type deployment_scale

Proposals go under scenarios/generated and are never included in the curated scenario bank automatically. Generated proposals still require disposable-cluster smoke and human review before promotion.

Current boundaries: no authentication, no production isolation, no browser terminal, no session persistence, no real exam timer. Never expose the developer server or kind cluster over a network. Next iteration: browser xterm.js console with Vite proxy for localhost development only.
