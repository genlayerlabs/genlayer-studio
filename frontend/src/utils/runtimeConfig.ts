/**
 * Runtime configuration helper
 *
 * Reads configuration from runtime-config.js (generated at container startup)
 * Falls back to build-time Vite environment variables if runtime config is not available
 */

interface RuntimeConfig {
  VITE_JSON_RPC_SERVER_URL?: string;
  VITE_WS_SERVER_URL?: string;
  VITE_IS_HOSTED?: string;
  VITE_FINALITY_WINDOW?: string;
  VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION?: string;
  VITE_MAX_ROTATIONS?: string;
  VITE_PLAUSIBLE_DOMAIN?: string;
}

declare global {
  interface Window {
    __RUNTIME_CONFIG__?: RuntimeConfig;
  }
}

/**
 * Get configuration value with fallback priority:
 * 1. Runtime config (from container environment variables)
 * 2. Build-time Vite environment variables
 * 3. Provided fallback value
 */
export function getRuntimeConfig(
  key: keyof RuntimeConfig,
  fallback: string = '',
): string {
  // First try runtime config
  const runtimeValue = window.__RUNTIME_CONFIG__?.[key];
  if (
    runtimeValue !== undefined &&
    runtimeValue !== null &&
    runtimeValue !== ''
  ) {
    return runtimeValue;
  }

  // Fall back to build-time env var
  const buildTimeValue = import.meta.env[key];
  if (
    buildTimeValue !== undefined &&
    buildTimeValue !== null &&
    buildTimeValue !== ''
  ) {
    return buildTimeValue;
  }

  // Use fallback
  return fallback;
}

/**
 * Get configuration value as a number
 */
export function getRuntimeConfigNumber(
  key: keyof RuntimeConfig,
  fallback: number,
): number {
  const value = getRuntimeConfig(key, String(fallback));
  const parsed = Number(value);
  return isNaN(parsed) ? fallback : parsed;
}

/**
 * Get configuration value as a boolean
 */
export function getRuntimeConfigBoolean(
  key: keyof RuntimeConfig,
  fallback: boolean,
): boolean {
  const value = getRuntimeConfig(key, String(fallback));
  return value === 'true';
}
