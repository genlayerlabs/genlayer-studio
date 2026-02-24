#!/bin/bash
#
# Run the update_error_transactions_metrics.py script from a k8s pod.
#
# Usage:
#   ./run_update_error_transactions.sh <namespace> [options]
#
# Examples:
#   # Dry run on staging
#   ./run_update_error_transactions.sh studio-stg --dry-run
#
#   # Process all error/undetermined transactions on production
#   ./run_update_error_transactions.sh studio-prd \
#       --api-url "https://your-metrics-api.com" \
#       --api-key "your-api-key"
#
#   # Process transactions after a specific hash (exclusive)
#   ./run_update_error_transactions.sh studio-prd \
#       --api-url "https://your-metrics-api.com" \
#       --api-key "your-api-key" \
#       --from-hash "0xabc123..."
#
#   # Process transactions before a specific hash (exclusive)
#   ./run_update_error_transactions.sh studio-prd \
#       --api-url "https://your-metrics-api.com" \
#       --api-key "your-api-key" \
#       --until-hash "0xabc123..."
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/update_error_transactions_metrics.py"
POD_NAME="update-error-transactions-pod"

if [ -z "$1" ]; then
    echo "Usage: $0 <namespace> [script-options]"
    echo ""
    echo "Namespaces:"
    echo "  studio-dev         -> simulator-dev cluster (DB: 10.127.216.9)"
    echo "  studio-stg         -> simulator-dev cluster (DB: 10.127.216.12)"
    echo "  rally-studio-dev   -> simulator-dev cluster (DB: 10.127.216.14)"
    echo "  studio-prd         -> simulator-prd cluster (DB: 10.24.72.12)"
    echo "  rally-studio-prd   -> rally-prd cluster (DB: 10.24.72.22)"
    echo ""
    echo "Script options (passed to Python script):"
    echo "  --api-url URL      Usage metrics API URL"
    echo "  --api-key KEY      Usage metrics API key"
    echo "  --from-hash HASH   Process transactions after this hash (exclusive)"
    echo "  --until-hash HASH  Process transactions before this hash (exclusive)"
    echo "  --batch-size N     Batch size for API calls (default: 50)"
    echo "  --dry-run          Print payloads without sending"
    echo "  --verbose          Verbose output"
    exit 1
fi

ENV="$1"
shift  # Remove namespace from args, rest are script options

# Select k8s context and DB IP based on namespace
case "$ENV" in
    studio-dev)
        CTX="gke_simulator-dev-473709_europe-west4_simulator-dev"
        DB_IP="10.127.216.9"
        ;;
    studio-stg)
        CTX="gke_simulator-dev-473709_europe-west4_simulator-dev"
        DB_IP="10.127.216.12"
        ;;
    rally-studio-dev)
        CTX="gke_simulator-dev-473709_europe-west4_simulator-dev"
        DB_IP="10.127.216.14"
        ;;
    studio-prd)
        CTX="gke_simulator-440803_europe-west4_simulator-prd"
        DB_IP="10.24.72.12"
        ;;
    rally-studio-prd)
        CTX="gke_simulator-440803_europe-west4_rally-prd"
        DB_IP="10.24.72.22"
        ;;
    *)
        echo "Error: Unknown namespace '$ENV'"
        echo "Valid namespaces: studio-dev, studio-stg, rally-studio-dev, studio-prd, rally-studio-prd"
        exit 1
        ;;
esac

echo "=== Configuration ==="
echo "Namespace: $ENV"
echo "Context: $CTX"
echo "DB IP: $DB_IP"
echo "===================="

# Switch context and namespace
echo "Switching k8s context to: $CTX"
kubectx "$CTX" || exit 1

echo "Switching namespace to: $ENV"
kubens "$ENV" || exit 1

# Get DB password from secret
echo "Fetching DB password from 'database-password' secret..."
DBPASSWORD=$(kubectl get secret database-password -o jsonpath='{.data.DBPASSWORD}' | base64 -d) || {
    echo "Failed to fetch DB password"
    exit 1
}

# Clean up any existing pod
echo "Cleaning up any existing '$POD_NAME' pod..."
kubectl delete pod "$POD_NAME" --ignore-not-found >/dev/null 2>&1 || true
kubectl wait pod "$POD_NAME" --for=delete --timeout=30s >/dev/null 2>&1 || true

# Create a ConfigMap with the Python script
echo "Creating ConfigMap with Python script..."
kubectl delete configmap update-error-transactions-script --ignore-not-found >/dev/null 2>&1 || true
kubectl create configmap update-error-transactions-script --from-file=script.py="$PYTHON_SCRIPT"

# Build the script arguments (escape for shell)
SCRIPT_ARGS="--db-host $DB_IP --db-password \$DBPASSWORD $*"

echo "Running script in pod..."
echo "Arguments: $SCRIPT_ARGS"
echo ""

# Create temporary pod manifest
POD_MANIFEST=$(mktemp)
cat > "$POD_MANIFEST" <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $POD_NAME
spec:
  restartPolicy: Never
  containers:
  - name: $POD_NAME
    image: python:3.11-slim
    command: ["/bin/bash", "-c"]
    args:
    - |
      pip install psycopg2-binary requests -q
      python /scripts/script.py $SCRIPT_ARGS
    env:
    - name: DBPASSWORD
      valueFrom:
        secretKeyRef:
          name: database-password
          key: DBPASSWORD
    volumeMounts:
    - name: script
      mountPath: /scripts
  volumes:
  - name: script
    configMap:
      name: update-error-transactions-script
EOF

# Apply the pod manifest
kubectl apply -f "$POD_MANIFEST"
rm -f "$POD_MANIFEST"

# Wait for pod to start and attach to it
echo "Waiting for pod to start..."
kubectl wait pod "$POD_NAME" --for=condition=Ready --timeout=120s 2>/dev/null || true

# Stream logs (will block until pod completes)
kubectl logs -f "$POD_NAME" || true

# Wait for completion and get exit code
echo ""
echo "Waiting for pod to complete..."
kubectl wait pod "$POD_NAME" --for=jsonpath='{.status.phase}'=Succeeded --timeout=86400s 2>/dev/null || \
kubectl wait pod "$POD_NAME" --for=jsonpath='{.status.phase}'=Failed --timeout=60s 2>/dev/null || true

# Get exit code
EXIT_CODE=$(kubectl get pod "$POD_NAME" -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}' 2>/dev/null || echo "unknown")
echo "Pod exit code: $EXIT_CODE"

# Cleanup
echo "Cleaning up..."
kubectl delete pod "$POD_NAME" --ignore-not-found >/dev/null 2>&1 || true
kubectl delete configmap update-error-transactions-script --ignore-not-found >/dev/null 2>&1 || true

echo "Done!"
