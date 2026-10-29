# CKA Exam Simulator

An experimental **hands-on** Kubernetes CKA simulator with browser-based developer terminal, deterministic grading against live cluster state and optional local Ollama scenario proposals.

## Developer-only local preview

Not safe for public hosting: no authentication, tenant isolation, quotas, backend exam clock or production-grade terminal yet. Do not publish these endpoints or expose localhost via an internet proxy.

Requirements: Python 3.11+, Node 20+, npm, Docker Engine, kind, kubectl (native packages or source); Ollama optional.

1. Install backend: python -m pip install -e '.[dev]'
2. Install frontend: cd frontend && npm install --no-audit --no-fund && cd ..
3. Run tests: python -m pytest && python -m cka_sim validate-bank
4. Create cluster: python -m cka_sim lab-up
5. Start backend in one terminal: CKA_ENABLE_LOCAL_LAB=1 uvicorn cka_sim.api:app --host 127.0.0.1 --port 8000
6. Start frontend in another terminal: cd frontend && npm run dev
7. Open http://127.0.0.1:5173 and select a challenge, Prepare lab, use kubectl, Check solution.
8. Remove the dedicated cluster when done: python -m cka_sim lab-down

Sample fix (in the browser console):
- kubectl get deployments -n cka-payments
- kubectl scale deployment/payment-api --replicas=3 -n cka-payments

The browser console is **restricted kubectl**, not a full shell. It only targets the dedicated kind-cka-dev Kubernetes context. Requests to the local developer API require a specific header and a loopback peer, but this is NOT production authentication.

## Validate scenario design

Against a disposable, locally running kind cluster:
- python -m cka_sim lab-smoke scale-payment-api
- python -m cka_sim lab-smoke fix-catalog-service

This checks the initial state fails and the canonical solution passes against real Kubernetes resources. The image nginx:1.27-alpine must be pullable (or preloaded).

## Ollama proposal generation

- python -m cka_sim generate --model YOUR_LOCAL_MODEL --type deployment_scale
- python -m cka_sim generate --model YOUR_LOCAL_MODEL --type service_selector

The LLM writes only schema-validated proposals under scenarios/generated. It cannot supply code, arbitrary manifests, Kubernetes commands or graders. Proposals **must never be automatically promoted**: real-cluster smoke, policy review and approval are still required.

## Roadmap

1. Reliable VM-backed per-user ephemeral runner, auth, quotas and isolation.
2. Real PTY browser shell instead of restricted kubectl, plus exam sessions with authoritative server clock.
3. LLM model evaluation and automated acceptance pipeline with explicit human publication gate.
4. Expanded CKA domains and official blueprint mapping.
