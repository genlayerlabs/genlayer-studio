import { format } from 'date-fns';

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
 * Get the duration between two timestamps (in seconds)
 */
export function getDuration(start: number, end: number): string {
  const durationMs = Math.abs(end - start) * 1000;
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
