import { getRuntimeConfig } from './runtimeConfig';

/**
 * Get the explorer base URL.
 *
 * Priority:
 * 1. VITE_EXPLORER_URL env var (explicit override)
 * 2. Derived from current hostname:
 *    studio.genlayer.com       → https://explorer-studio.genlayer.com
 *    studio-stage.genlayer.com → https://explorer-studio-stage.genlayer.com
 *    studio-dev.genlayer.com   → https://explorer-studio-dev.genlayer.com
 * 3. Fallback: http://localhost:3001
 */
export function getExplorerUrl(): string {
  const explicit = getRuntimeConfig('VITE_EXPLORER_URL');
  if (explicit) return explicit;

  const host = window.location.hostname;
  if (host.endsWith('.genlayer.com')) {
    return `https://explorer-${host.replace('.genlayer.com', '')}.genlayer.com`;
  }

  return 'http://localhost:3001';
}
