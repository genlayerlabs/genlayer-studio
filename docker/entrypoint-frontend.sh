#!/bin/sh
set -e

# Determine if we're in dev or prod mode based on which directory exists
if [ -d "/app/dist" ]; then
  # Production mode: built app exists in /app/dist
  CONFIG_DIR="/app/dist"
  START_CMD="npm run preview"
  MODE="production"
else
  # Development mode: no build, use /app/public for Vite to serve
  CONFIG_DIR="/app/public"
  START_CMD="npm run dev"
  MODE="development"
fi

echo "Generating runtime configuration for ${MODE}..."

# Create config directory if it doesn't exist
mkdir -p "${CONFIG_DIR}"

# Generate runtime config that overrides build-time values
cat > "${CONFIG_DIR}/runtime-config.js" <<EOF
window.__RUNTIME_CONFIG__ = {
  VITE_JSON_RPC_SERVER_URL: "${VITE_JSON_RPC_SERVER_URL:-http://127.0.0.1:4000/api}",
  VITE_WS_SERVER_URL: "${VITE_WS_SERVER_URL:-ws://127.0.0.1:4000}",
  VITE_IS_HOSTED: "${VITE_IS_HOSTED:-false}",
  VITE_FINALITY_WINDOW: "${VITE_FINALITY_WINDOW:-10}",
  VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION: "${VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION:-0.2}",
  VITE_MAX_ROTATIONS: "${VITE_MAX_ROTATIONS:-3}",
  VITE_PLAUSIBLE_DOMAIN: "${VITE_PLAUSIBLE_DOMAIN:-studio.genlayer.com}"
};
EOF

echo "Runtime configuration generated successfully at ${CONFIG_DIR}/runtime-config.js"
cat "${CONFIG_DIR}/runtime-config.js"
echo ""
echo "Starting application in ${MODE} mode..."

# Start the application
exec ${START_CMD}
