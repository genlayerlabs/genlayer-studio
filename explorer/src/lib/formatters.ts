import { format } from 'date-fns';

/**
 * Format a wei value to a human-readable GEN string.
 * Uses BigInt for precision. Returns "X.XX GEN" (2–6 decimals), "-" for null, "0 GEN" for zero.
 */
export function formatGenValue(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return '-';

  const ZERO = BigInt(0);
  const WEI_PER_GEN = BigInt('1000000000000000000'); // 10^18

  let wei: bigint;
  try {
    wei = BigInt(value);
  } catch {
    return String(value);
  }

  if (wei === ZERO) return '0 GEN';

  const negative = wei < ZERO;
  const absWei = negative ? -wei : wei;
  const whole = absWei / WEI_PER_GEN;
  const remainder = absWei % WEI_PER_GEN;
  const sign = negative ? '-' : '';

  if (remainder === ZERO) return `${sign}${whole} GEN`;

  // Pad remainder to 18 digits, then trim trailing zeros but keep at least 2
  const fracStr = remainder.toString().padStart(18, '0');
  const trimmed = fracStr.replace(/0+$/, '');
  const decimals = Math.max(2, Math.min(6, trimmed.length));
  const finalFrac = fracStr.slice(0, decimals);

  return `${sign}${whole}.${finalFrac} GEN`;
}

/**
 * Format a duration in seconds to a human-readable string
 */
export function formatDuration(durationSeconds: number): string {
  const durationMs = durationSeconds * 1000;
  if (durationMs < 1000) return `${durationMs.toFixed(0)}ms`;
  if (durationMs < 60000) return `${(durationMs / 1000).toFixed(2)}s`;
  return `${(durationMs / 60000).toFixed(2)}m`;
}

/**
 * Format a Unix timestamp (in seconds or milliseconds) to a human-readable string
 */
export function formatTimestamp(ts: number): string {
  // Handle Unix timestamp (seconds) vs milliseconds
  const date = new Date(ts < 1e12 ? ts * 1000 : ts);
  return isNaN(date.getTime()) ? String(ts) : format(date, 'PPpp');
}

/**
 * Get the duration between two timestamps (auto-detects seconds vs milliseconds)
 */
export function getDuration(start: number, end: number): string {
  // Normalize to milliseconds (same logic as formatTimestamp)
  const startMs = start < 1e12 ? start * 1000 : start;
  const endMs = end < 1e12 ? end * 1000 : end;
  const durationMs = Math.abs(endMs - startMs);
  if (durationMs < 1000) return `${durationMs.toFixed(0)}ms`;
  if (durationMs < 60000) return `${(durationMs / 1000).toFixed(2)}s`;
  return `${(durationMs / 60000).toFixed(2)}min`;
}

/**
 * Truncate an address for display
 */
export function truncateAddress(address: string, startChars = 8, endChars = 6): string {
  if (address.length <= startChars + endChars) return address;
  return `${address.slice(0, startChars)}...${address.slice(-endChars)}`;
}

/**
 * Truncate a hash for display
 */
export function truncateHash(hash: string, startChars = 10, endChars = 8): string {
  if (hash.length <= startChars + endChars) return hash;
  return `${hash.slice(0, startChars)}...${hash.slice(-endChars)}`;
}
