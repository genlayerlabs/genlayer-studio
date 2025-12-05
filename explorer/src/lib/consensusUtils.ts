import { NewConsensusHistory, ConsensusHistoryData, ConsensusHistoryEntry } from './types';

/**
 * Check if consensus_history is in the new format with monitoring timestamps
 */
export function isNewConsensusFormat(data: unknown): data is NewConsensusHistory {
  return (
    data !== null &&
    typeof data === 'object' &&
    'consensus_results' in data &&
    Array.isArray((data as NewConsensusHistory).consensus_results)
  );
}

/**
 * Check if consensus_history is in the legacy format (array of entries)
 */
export function isLegacyConsensusFormat(data: ConsensusHistoryData | null): data is ConsensusHistoryEntry[] {
  return data !== null && Array.isArray(data) && !isNewConsensusFormat(data);
}

/**
 * Get the number of consensus rounds from either format
 */
export function getConsensusRoundCount(data: ConsensusHistoryData | null): number {
  if (!data) return 0;

  if (isNewConsensusFormat(data)) {
    return data.consensus_results.length;
  }

  if (Array.isArray(data)) {
    return data.length;
  }

  return 0;
}

/**
 * Phase configuration for consensus monitoring timeline
 */
export const CONSENSUS_PHASES = {
  PENDING: { color: 'bg-slate-400', label: 'Pending' },
  PROPOSING: { color: 'bg-blue-500', label: 'Proposing' },
  COMMITTING: { color: 'bg-violet-500', label: 'Committing' },
  REVEALING: { color: 'bg-amber-500', label: 'Revealing' },
  ACCEPTED: { color: 'bg-emerald-500', label: 'Accepted' },
  FINALIZED: { color: 'bg-green-600', label: 'Finalized' },
} as const;

/**
 * Get the color class for a consensus phase
 */
export function getPhaseColor(key: string): string {
  for (const [phase, config] of Object.entries(CONSENSUS_PHASES)) {
    if (key.startsWith(phase)) return config.color;
  }
  return 'bg-gray-400';
}
