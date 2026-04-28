import { getRuntimeConfig } from './runtimeConfig';
import { useNetworkStore } from '@/stores/network';

/**
 * Get the explorer base URL for the currently selected network.
 *
 * Priority:
 * 1. VITE_EXPLORER_URL env var (explicit override).
 * 2. Non-Studio networks: the chain's own `blockExplorers.default.url`
 *    (e.g. Bradbury → https://explorer-bradbury.genlayer.com).
 * 3. Studio networks: derived from the current hostname
 *    (studio.genlayer.com → explorer-studio.genlayer.com, etc.).
 * 4. Fallback: http://localhost:3001
 */
export function getExplorerUrl(): string {
  const explicit = getRuntimeConfig('VITE_EXPLORER_URL');
  if (explicit) return explicit;

  // Chain-aware lookup when the network store is available (runtime).
  try {
    const networkStore = useNetworkStore();
    if (!networkStore.isStudio) {
      const chainExplorer =
        networkStore.chain.blockExplorers?.default?.url ?? '';
      if (chainExplorer) return chainExplorer.replace(/\/$/, '');
    }
  } catch {
    // Pinia not yet initialized (e.g. very early boot) — fall through to
    // the hostname-based heuristic.
  }

  const host = window.location.hostname;
  if (host.endsWith('.genlayer.com')) {
    return `https://explorer-${host.replace('.genlayer.com', '')}.genlayer.com`;
  }

  return 'http://localhost:3001';
}
