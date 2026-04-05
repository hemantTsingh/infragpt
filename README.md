# InfraGPT

An AI-powered Kubernetes operations assistant. InfraGPT watches your cluster, fetches pod logs from Loki, queries metrics from Prometheus, and uses Claude (Anthropic) to explain what's wrong, classify anomalies, and suggest remediation — all through a REST API.

---

## Architecture

```
                         ┌──────────────────────────────────────────────┐
                         │            AWS EC2 — ap-south-1              │
                         │                                              │
  Developer / Browser    │  ┌─────────────────────────────────────────┐ │
         │               │  │   kubeadm Cluster (K8s v1.31, CRI-O)   │ │
         │               │  │                                         │ │
         ├──:31406 ──────┼──▶  InfraGPT  (FastAPI, default ns)       │ │
         ├──:31300 ──────┼──▶  Grafana   (monitoring ns)             │ │
         ├──:31900 ──────┼──▶  Prometheus(monitoring ns)             │ │
         ├──:31080 ──────┼──▶  ArgoCD    (argocd ns)                 │ │
         │               │  │                                         │ │
         │               │  │  InfraGPT internal calls:               │ │
         │               │  │  ┌──────────┐                           │ │
         │               │  │  │InfraGPT  │──▶ Loki  (logs)          │ │
         │               │  │  │          │──▶ Prometheus (metrics)   │ │
         │               │  │  │          │──▶ K8s API (pod ops)      │ │
         │               │  │  │          │──▶ Anthropic Claude API   │ │
         │               │  │  └──────────┘       (internet)          │ │
         │               │  │                                         │ │
         │               │  │  CI/CD:                                 │ │
         │               │  │  GitHub ──▶ GitHub Actions              │ │
         │               │  │              test → build → deploy      │ │
         │               │  │  ArgoCD ──▶ GitHub (watch k8s/)         │ │
         │               │  │              auto-sync on every push    │ │
         │               │  └─────────────────────────────────────────┘ │
         │               └──────────────────────────────────────────────┘
         │
         └── Loki has no browser UI — access logs via Grafana → Explore
```

---

## Cluster

| Role | Hostname | Private IP | Public IP |
|---|---|---|---|
| Master (control-plane) | ip-172-31-29-153 | 172.31.29.153 | 13.201.172.218 |
| Worker 1 | ip-172-31-31-192 | 172.31.31.192 | 13.200.18.244 |
| Worker 2 | ip-172-31-25-173 | 172.31.25.173 | 13.235.61.131 |

**Stack:** Ubuntu 24.04, Kubernetes v1.31.14, CRI-O, Flannel CNI

---

## Live URLs

| Service | URL | Credentials |
|---|---|---|
| InfraGPT API docs | http://13.201.172.218:31406/docs | — |
| Grafana | http://13.201.172.218:31300 | `admin` / `infragpt123` |
| Prometheus | http://13.201.172.218:31900 | — |
| ArgoCD | http://13.201.172.218:31080 | `admin` / see secret below |
| Loki | API only — no UI | use Grafana → Explore |

```bash
# Get ArgoCD admin password
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d
```

---

## Features

### 1. Log Explanation — `POST /api/explain`
Fetches pod logs (Loki first, kubectl fallback), sends them to Claude Sonnet, returns structured JSON analysis.

**Request:**
```json
{ "pod_name": "my-pod-abc123", "namespace": "default", "lines": 100 }
```

**Response:**
```json
{
  "pod_name": "my-pod-abc123",
  "namespace": "default",
  "audit_id": 42,
  "severity": "critical",
  "summary": "The pod is OOMKilled due to memory limit being exceeded during request spike.",
  "top_causes": [
    "Memory limit set too low (128Mi)",
    "No horizontal pod autoscaler configured",
    "Memory leak in request handler"
  ],
  "suggested_action": "kubectl set resources deployment/my-app --limits=memory=512Mi -n default",
  "log_lines_analyzed": 87
}
```

### 2. Cluster Status — `POST /api/status`
Lists all pods in a namespace with ready/restart state.

**Request:** `{ "namespace": "default" }`

### 3. Ask InfraGPT — `POST /api/ask`
Free-form question answering with optional namespace context. Automatically fetches unhealthy pod list as context.

**Request:** `{ "question": "Why is my pod restarting?", "namespace": "default" }`

### 4. Remediation — `POST /api/remediate`
Given a pod + issue type, returns suggested kubectl commands ranked by risk score.

**Request:** `{ "namespace": "default", "pod": "my-pod", "issue": "CrashLoopBackOff" }`

**Response includes:** `suggested_commands`, `risk_score (0-1)`, `risk_label (low/medium/high)`

### 5. Anomaly Classification — `POST /api/anomalies`
Rule-based + AI classification of log anomalies. Detects: OOMKill, connection errors, panics, restarts.

**Request:** `{ "namespace": "default", "pod": "my-pod" }` or `{ "namespace": "default", "logs": "raw log text" }`

### 6. Health Check — `GET /health`
```json
{ "status": "ok", "version": "1.0.0" }
```

---

## Audit Log

Every AI call is written to a SQLite database at `/tmp/infragpt_audit.db` inside the pod.

| Column | Description |
|---|---|
| `id` | Auto-increment (used as `audit_id` in API responses) |
| `timestamp` | UTC ISO timestamp |
| `action` | `explain_logs`, `ask`, `remediate`, `anomaly` |
| `resource` | `namespace/pod` |
| `decision` | JSON snapshot of severity + summary |
| `confidence` | Float 0–1 |

---

## Integrations

### Loki (log source)
- Service: `loki-stack.monitoring.svc.cluster.local:3100`
- LogQL query: `{namespace="$ns"} |= "$pod_name"`
- Deployed via Helm (`loki-stack` chart, v2.6.1)
- Promtail scrapes all pod logs across all namespaces

### Prometheus (metrics)
- Service: `kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090`
- Used for: CPU cores, memory MB per pod
- Deployed via `kube-prometheus-stack` Helm chart

### Grafana (dashboards)
- Service: `kube-prometheus-stack-grafana.monitoring:80`
- Datasources: Prometheus, Loki, Alertmanager
- Custom dashboard: **InfraGPT — Namespace & Pod Explorer**
  - 3 dropdowns: Namespace → Pod → Container
  - Panels: pod count stats, CPU graph, memory graph, restart graph, pod status table, live log stream

### Anthropic Claude
- `explain_logs` → `claude-sonnet-4-20250514`
- `ask_claude`, `remediate`, `anomalies` → `claude-opus-4-6`
- API key stored in Kubernetes Secret `infragpt-secrets` (default namespace)

---

## CI/CD Pipeline

**GitHub Actions** (`.github/workflows/ci-cd.yaml`) triggers on every push to `main`:

```
push to main
    │
    ▼
[test]   pytest backend/tests/  (Python 3.12)
    │      10 tests: explainer (3) + routes (7)
    │
    ▼
[build]  docker build → push to DockerHub
    │      Image: hemantsingh1023/infragpt:<7-char-SHA>
    │      Also tags :latest
    │
    ▼
[deploy] base64-decode KUBECONFIG_B64 secret
           kubectl set image deployment/infragpt → wait for rollout
```

**GitHub Secrets required:**

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | `hemantsingh1023` |
| `DOCKERHUB_TOKEN` | DockerHub PAT |
| `KUBECONFIG_B64` | `cat ~/.kube/config \| base64 -w 0` (with public IP + insecure-skip-tls-verify) |

---

## ArgoCD (GitOps)

ArgoCD watches `k8s/` directory in the `main` branch and auto-syncs to the cluster.

```
GitHub push → ArgoCD detects change in k8s/ → applies to cluster → self-heals drift
```

**Application config:**
- Repo: `https://github.com/hemantTsingh/infragpt`
- Path: `k8s/`
- Target: `default` namespace
- Sync: automated, prune enabled, self-heal enabled

**Check status:**
```bash
kubectl get application infragpt -n argocd
```

---

## Project Structure

```
infragpt/
├── .github/
│   └── workflows/
│       └── ci-cd.yaml          # GitHub Actions pipeline
├── backend/
│   ├── main.py                 # FastAPI app entrypoint
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── .env.example
│   ├── ai/
│   │   ├── log_explainer.py    # explain_logs(), ask_claude()
│   │   ├── anomaly_classifier.py
│   │   ├── autoscaler.py
│   │   └── remediation.py
│   ├── api/
│   │   ├── models.py           # Pydantic request/response models
│   │   └── routes.py           # FastAPI route handlers
│   ├── db/
│   │   └── audit_log.py        # SQLite audit trail
│   ├── integrations/
│   │   ├── k8s_client.py       # kubernetes Python SDK wrapper
│   │   ├── loki_client.py      # httpx Loki queries
│   │   ├── prom_client.py      # httpx Prometheus queries
│   │   └── slack_bot.py        # Slack notifications (future)
│   └── tests/
│       ├── conftest.py         # kubernetes SDK stub for CI
│       ├── test_explainer.py   # 3 AI layer tests
│       └── test_routes.py      # 7 route integration tests
└── k8s/
    └── deployment.yaml         # Deployment + Service (watched by ArgoCD)
```

---

## Local Development

```bash
# Clone
git clone https://github.com/hemantTsingh/infragpt
cd infragpt/backend

# Install dependencies
pip install -r requirements.txt

# Set env
export ANTHROPIC_API_KEY=sk-ant-...

# Run
uvicorn main:app --reload --port 8000

# Test
pytest tests/ -v
```

---

## Kubernetes Secret Setup

```bash
# Create/update API key secret
kubectl create secret generic infragpt-secrets \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  -n default --dry-run=client -o yaml | kubectl apply -f -

# Restart pod to pick up new secret
kubectl rollout restart deployment/infragpt -n default
```

---

## Grafana Dashboard

**InfraGPT — Namespace & Pod Explorer**
URL: `http://13.201.172.218:31300/d/infragpt-explorer`

Use the top dropdowns to explore any namespace and pod:
- Namespace → Pod → Container (all auto-populate from live cluster data)
- Live log stream at the bottom via Loki
- CPU, memory, and restart graphs per selection

To view raw logs via Loki API:
```bash
curl "http://13.201.172.218:31310/loki/api/v1/query_range" \
  --data-urlencode 'query={namespace="default"}' \
  --data-urlencode 'limit=50'
```

---

## Tech Stack Summary

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| API framework | FastAPI + Uvicorn |
| AI | Anthropic Claude (Sonnet + Opus) |
| Container runtime | CRI-O |
| Orchestration | Kubernetes v1.31 (kubeadm, bare EC2) |
| Networking | Flannel CNI |
| Logs | Loki + Promtail |
| Metrics | Prometheus + kube-prometheus-stack |
| Dashboards | Grafana |
| GitOps | ArgoCD |
| CI/CD | GitHub Actions |
| Registry | DockerHub |
| Cloud | AWS EC2 (ap-south-1) |
| IaC | kubectl + Helm |
