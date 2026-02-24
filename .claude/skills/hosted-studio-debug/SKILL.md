---
name: hosted-studio-debug
description: Debug GenLayer Studio deployments via ArgoCD CLI
---

# Hosted Studio Debug Skill

Debug GenLayer Studio deployments via ArgoCD CLI.

## Workload Manifests

Kubernetes manifests are in sibling repo `../devexp-apps-workload` (assume by default, ask user if not found):

```
devexp-apps-workload/workload/
├── dev/           # studio-dev, rally-studio-dev
├── stg/           # studio-stg
├── prd/           # studio-prd, rally-studio-prd
```

Each contains Deployments, Services, Ingresses, ExternalSecrets managed by ArgoCD.

## Prerequisites

- Logged into ArgoCD CLI
- Access to target cluster

## Full Status Check

To get complete visibility into Studio status, check all components across all replicas:

```bash
# 1. Overall app health
argocd app get <app>-workload

# 2. List all pods and their status
argocd app resources <app>-workload --kind Pod

# 3. Check consensus worker logs (all 4 replicas in prd)
argocd app logs <app>-workload --name studio-consensus-worker --tail 500 2>&1 | grep -iE "(error|exception|timeout|failed)"

# 4. Check JSON-RPC logs (all 2 replicas in prd)
argocd app logs <app>-workload --name studio-jsonrpc --tail 500 2>&1 | grep -iE "(error|exception|timeout|failed)"

# 5. Check webdriver logs
argocd app logs <app>-workload --name studio-webdriver --tail 200 2>&1 | grep -iE "(error|exception|crash)"
```

## Quick Commands

```bash
# Check app health
argocd app get <app>-workload

# List all resources (pods, deployments, services, etc.)
argocd app resources <app>-workload

# List all pods with their status
argocd app resources <app>-workload --kind Pod

# Tail consensus worker logs (aggregates from ALL replicas)
argocd app logs <app>-workload --name studio-consensus-worker --tail 200

# Tail JSON-RPC logs (aggregates from ALL replicas)
argocd app logs <app>-workload --name studio-jsonrpc --tail 200

# Check for errors across all replicas
argocd app logs <app>-workload --name studio-consensus-worker --tail 500 2>&1 | grep -i error
argocd app logs <app>-workload --name studio-jsonrpc --tail 500 2>&1 | grep -i error
```

## Multi-Replica Log Access

**Important:** The `argocd app logs --name <container>` command automatically aggregates logs from ALL pod replicas. This ensures full visibility across the entire deployment.

```bash
# Get logs from all consensus worker replicas (production has 4)
argocd app logs <app>-workload --name studio-consensus-worker --tail 500

# Get logs from all JSON-RPC replicas (production has 2)
argocd app logs <app>-workload --name studio-jsonrpc --tail 500

# Get logs from a specific pod (if needed for isolation)
argocd app logs <app>-workload --pod <pod-name> --tail 200

# List pods first to get pod names
argocd app resources <app>-workload --kind Pod
```

## Common Issues

### Transaction Timeouts

Consensus worker timeouts usually mean GenVM Manager is unresponsive. Check all 4 workers in production:

```bash
# Check for GenVM timeouts across all consensus worker replicas
argocd app logs <app>-workload --name studio-consensus-worker --tail 500 2>&1 | grep -E "(timeout|SocketTimeoutError|127.0.0.1:3999)"

# Empty stdout = GenVM never started
argocd app logs <app>-workload --name studio-consensus-worker --tail 500 2>&1 | grep "stdout=''"
```

**Root cause**: GenVM Manager (`genvm-modules manager --port 3999`) becomes unresponsive.
**Fix**: Worker restart (auto or manual) restarts GenVM Manager.

Key files:
- `backend/node/genvm/origin/base_host.py:463` - where timeouts occur
- `backend/node/base.py` - Manager.create() spawns GenVM
- `backend/consensus/worker_service.py` - worker startup/health

### Pod Restarts

```bash
# Check restart timestamps in logs
argocd app logs <app>-workload --name studio-consensus-worker --tail 500 2>&1 | grep -E "(Started|Uvicorn running)"

# Via kubectl
kubectl get pods -n <namespace> -o wide
kubectl get events -n <namespace> --sort-by='.lastTimestamp'
```

### External API Failures

Contracts calling external APIs (Twitter, etc.) through proxies:

```bash
# Check for HTTP errors in contract execution
argocd app logs <app>-workload --name studio-consensus-worker --tail 500 2>&1 | grep -E "(HTTP|fetch|proxy|api)"
```

## Environment Apps

| Env | App | Namespace |
|-----|-----|-----------|
| dev | studio-dev-workload | studio-dev |
| stg | studio-stg-workload | studio-stg |
| prd | studio-prd-workload | studio-prd |
| rally-prd | rally-studio-prd-workload | rally-studio-prd |

## Components

| Component | Purpose | Prd Replicas | Common Issues |
|-----------|---------|--------------|---------------|
| studio-consensus-worker | Tx processing | 4 | Timeouts, GenVM crashes |
| studio-jsonrpc | RPC API | 2 | DB connections |
| studio-webdriver | Browser sandbox | 1 | Memory, crashes |
| database-migration | Schema updates | 1 (job) | Lock contention |

**Note:** When debugging, always check logs from ALL replicas to get complete visibility. The `argocd app logs --name <container>` command handles this automatically.
