# ArgoCD Debug Skill

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

## Quick Commands

```bash
# Check app health
argocd app get <app>-workload

# List resources
argocd app resources <app>-workload

# Tail consensus worker logs
argocd app logs <app>-workload --name studio-consensus-worker --tail 200

# Check for errors
argocd app logs <app>-workload --name studio-consensus-worker --tail 500 2>&1 | grep -i error
```

## Common Issues

### Transaction Timeouts

Consensus worker timeouts usually mean GenVM Manager is unresponsive.

```bash
# Check for GenVM timeouts
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

| Component | Purpose | Common Issues |
|-----------|---------|---------------|
| studio-consensus-worker | Tx processing | Timeouts, GenVM crashes |
| studio-jsonrpc | RPC API | DB connections |
| studio-webdriver | Browser sandbox | Memory, crashes |
| database-migration | Schema updates | Lock contention |
