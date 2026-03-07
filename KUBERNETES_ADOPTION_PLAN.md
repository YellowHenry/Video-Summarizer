# Kubernetes Adoption Plan for This Repo (Low-Risk, GKE-First)

## Summary
Adopt Kubernetes in two phases so you get K8s operational value quickly without breaking your current YouTube-cookie worker flow.

1. Phase 1 (recommended): move `api` and `web` to GKE; keep the worker on VM.
2. Phase 2 (optional): move the worker to Kubernetes only after solving persistent Chrome-profile requirements.

### What This Means
This is a risk-managed migration strategy, not an all-at-once rewrite.

- **Phase 1** gives you immediate Kubernetes experience on the stateless services (`api`, `web`) while preserving the current worker behavior that depends on VM-style Chrome cookies.
- **Phase 2** is deferred until worker reliability is proven in Kubernetes for browser-profile persistence, restarts, and scheduling behavior.

In short: get fast K8s wins first, then move the hardest component only after you can do it safely.

### Kubernetes Basics (What and Why)
**What Kubernetes is:**  
Kubernetes is a platform that runs your app containers for you across a cluster of machines.  
Instead of manually starting processes on one VM, you declare what should run (for example: 3 API replicas), and Kubernetes keeps that state true.

**What the point of it is:**
- **Reliability:** Automatically restarts crashed containers and replaces unhealthy instances.
- **Scalability:** Scales services up/down based on traffic and resource usage.
- **Safer deployments:** Supports rolling updates and easy rollbacks.
- **Consistency:** Same deployment model across dev/staging/prod.
- **Operational control:** Central place for config, secrets, networking, and service discovery.

In this plan, Kubernetes is mainly used to make your `api` and `web` layers more production-ready and easier to operate at scale.

This matches current repo reality:
- Cloud Run + VM worker model in [README.md](README.md)
- VM worker is the supported runtime in [deploy_config.py](infra/gcp/deploy_config.py)
- Existing health endpoint in [api.py](backend/webapp/api.py)

## Current-State Constraints to Preserve
- Worker currently depends on desktop-like Chrome cookie/session behavior (VM-oriented flow).
  - **What this means:** the worker relies on a persisted Chrome user profile/cookies setup that behaves like a long-lived desktop session.
  - **Why this is brought up here:** this is the main technical risk in a Kubernetes migration, because worker pods are more ephemeral and may restart/reschedule in ways that can break browser-session continuity if not engineered carefully.
  - This constraint explains the phased plan: move `api`/`web` first, then migrate the worker only after profile persistence and reliability are validated.
- API already supports queue-driven async processing and has `/healthz`.
  - **What this means:** API requests can stay fast because long-running work is pushed to a background queue and processed by workers asynchronously.
  - The `/healthz` endpoint is a lightweight uptime check used by load balancers and Kubernetes probes to confirm the API process is alive.
  - This is Kubernetes-friendly because async workers and health probes are standard building blocks for stable autoscaling and self-healing.
- Data plane is externalized already (Cloud SQL, Redis, GCS), which is Kubernetes-friendly.
  - **What this means:** Your important stateful systems (database, queue/cache, and object storage) already live outside the app runtime.
  - Because of that, `api` and `web` pods can be treated as mostly stateless and replaced/rescheduled by Kubernetes without losing core data.
  - This lowers migration risk: you can move compute to Kubernetes while keeping data services stable and managed.

## Target Kubernetes Architecture
1. Platform
- GKE Autopilot, `us-central1`, one cluster per environment (`dev`, `prod` namespaces).

**What this means:**
- **GKE Autopilot** is Google’s managed Kubernetes mode where Google handles most cluster operations (node management, patching, and baseline scaling behavior).
- **`us-central1`** is the chosen region where the cluster and most related resources should live to keep latency and egress costs predictable.
- **One cluster per environment with `dev` and `prod` namespaces** keeps development and production workloads logically separated, reducing accidental cross-environment impact while still using a consistent platform model.

2. Workloads
- `api` as a `Deployment` + `Service` (ClusterIP).
- `web` as a `Deployment` + `Service` (ClusterIP), fronted by Ingress.
- Worker remains VM in Phase 1.
- Optional Phase 2: worker as `StatefulSet` with PVC for browser profile.

**What this means:**
- **Deployment + Service (ClusterIP) for `api` and `web`:** Kubernetes keeps the app pods running and reachable internally with stable service names.
- **Ingress in front of `web`:** external users hit one public entry point, and Kubernetes routes traffic to the correct internal service.
- **What Ingress is:** an Ingress is a Kubernetes traffic-routing rule set (host/path + TLS) that, with an Ingress controller, acts like the app’s HTTP/HTTPS front door and forwards requests to internal Services.
- **Why this calls out `web` first:** the browser UI is always user-facing, so it is the default public entry point.
- **When `api` is not public:** if only your web app calls the API, `api` can stay internal (`ClusterIP`) and be reached only from `web`.
- **When `api` should also be behind Ingress:** if mobile apps, external clients, or third-party integrations must call the API directly, publish `api` through Ingress too (typically under `/api` or an `api.` subdomain).
- **Worker on VM in Phase 1:** keep current behavior stable while moving lower-risk services first.
- **Worker as StatefulSet + PVC in Phase 2:** use a stateful Kubernetes pattern only when you are ready to preserve browser profile files across restarts.

3. Networking/Ingress
- GKE Ingress (or Gateway API) for `web` and `api`.
- TLS via managed cert.
- Internal service-to-service traffic stays in-cluster.

**What this means:**
- Public internet traffic enters through a managed edge layer (Ingress/Gateway), not by exposing each pod directly.
- TLS managed certificates provide HTTPS termination so users connect securely without manual cert handling on each service.
  - **Plain English:** users connect over HTTPS to Google’s managed load balancer, and certificate issuance/renewal is automated instead of handled per app container.
  - **Operational impact:** you avoid manual cert installs, expiration outages, and duplicated TLS setup across `web` and `api` services.
  - **What TLS is:** TLS (Transport Layer Security) is the encryption protocol behind HTTPS that protects data in transit between a user and your service.
- After ingress routes a request, service-to-service calls stay on private cluster networking, reducing external exposure and simplifying access control.

**Who manages this in Google Cloud?**
- **Google Cloud manages most of it** when you use GKE + Google-managed Ingress/Gateway + managed certificates: load balancer infrastructure, certificate provisioning/renewal, and core control-plane operations.
- **You still manage** the Kubernetes resources and app policy: Ingress/Gateway rules (hosts/paths), which services are exposed, DNS records, and app-level auth/authorization.
- In practice, this is a **shared responsibility** model: Google runs the platform, you define how your application traffic should be routed and secured.

4. Data/Dependencies
- Cloud SQL remains external; connect via Cloud SQL Auth Proxy sidecar.
- Redis remains external (Memorystore recommended for K8s mode).
- GCS remains artifact store.

5. Config/Secrets
- Non-secret env in `ConfigMap`.
- Secrets from Secret Manager via CSI/External Secrets.

6. Scaling
- HPA for `api` (CPU/memory target).
- HPA for `web` (CPU target).
- Optional when worker moves to K8s: KEDA ScaledObject on Redis queue depth.

## Implementation Plan
1. Create Kubernetes deployment assets
- Add `k8s/base/` manifests for `api`, `web`, services, ingress, configmap, secrets references, PDB.
- Add `k8s/overlays/dev` and `k8s/overlays/prod` (kustomize) with env-specific values.

2. Adapt runtime config
- Map current env vars from GCP scripts to ConfigMap/Secret.
- Keep existing app env contract unchanged to avoid code churn.

3. Deploy Phase 1 hybrid model
- Deploy `api` and `web` to GKE.
- Point `REDIS_URL` to current VM Redis.
- Keep worker VM deployment path unchanged initially.

4. Add operational safeguards
- Liveness/readiness probes on `/healthz` for API.
- Resource requests/limits for all pods.
- PodDisruptionBudgets for API/web.
- Workload Identity for GCS/Cloud SQL access.

5. Optional Phase 2 worker migration
- Build worker StatefulSet prototype with persistent browser profile volume.
- Validate yt-dlp + browser-cookie behavior under pod restarts and rescheduling.
- Only cut over worker after parity tests pass.

## Public APIs / Interfaces / Types
- No external API contract changes required for Phase 1.
- Optional internal enhancement: add `/readyz` endpoint with dependency checks (DB/Redis/object store) while keeping `/healthz` as lightweight process health.

## Test Cases and Scenarios
1. API/web health and reachability
- `GET /healthz` returns `{"ok": true}` through Ingress.
- Web app loads and calls API successfully.

2. Core workflow
- Create job, enqueue, worker processes, summary/transcript retrievable.
- Search endpoint returns chunk hits.

3. Failure modes
- Restart API pods during active traffic; no data loss and no 5xx burst beyond SLO.
- Rotate secrets without image rebuild.
- DB proxy sidecar restart does not permanently break app.

4. Scale behavior
- API scales out under synthetic load.
- If KEDA is enabled later, worker scales with queue depth.

5. Rollback
- Re-point DNS/traffic from GKE back to existing Cloud Run stack in under 15 minutes.

## Rollout and Monitoring
1. Dev rollout
- Stand up GKE `dev`, run full smoke and e2e flow.
2. Prod canary
- Shift 10% traffic to GKE API/web, monitor error/latency.
3. Full cutover
- Move to 100% after 24–48h stable metrics.
4. Observability
- Centralized logs, request latency dashboards, queue depth dashboard, alerting on error rate and worker lag.

## Assumptions and Defaults
- Cloud provider remains GCP.
- Default cluster mode: GKE Autopilot.
- Default queue backend for K8s: Memorystore Redis.
- Worker stays on VM initially because browser-cookie flow is a hard constraint.
- No functional changes to existing endpoints are required in first migration wave.
