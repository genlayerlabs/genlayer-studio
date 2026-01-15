import { Transaction } from './types';
import { isNewConsensusFormat } from './consensusUtils';
import { formatDuration } from './formatters';

/**
 * Get the time from PENDING to ACCEPTED for a transaction (new consensus format only)
 */
export function getTimeToAccepted(tx: Transaction): string | null {
  if (!tx.consensus_history || !isNewConsensusFormat(tx.consensus_history)) {
    return null;
  }

  const firstRound = tx.consensus_history.consensus_results[0];
  if (!firstRound?.monitoring) return null;

  const pendingTime = firstRound.monitoring.PENDING;
  const acceptedTime = firstRound.monitoring.ACCEPTED;

  if (pendingTime === undefined || acceptedTime === undefined) return null;

  return formatDuration((acceptedTime - pendingTime) / 1000);
}

/**
 * Get the time from PENDING to FINALIZED for a transaction (new consensus format only)
 */
export function getTimeToFinalized(tx: Transaction): string | null {
  if (!tx.consensus_history || !isNewConsensusFormat(tx.consensus_history)) {
    return null;
  }

  const firstRound = tx.consensus_history.consensus_results[0];
  if (!firstRound?.monitoring) return null;

  const pendingTime = firstRound.monitoring.PENDING;
  const finalizedTime = tx.consensus_history.current_monitoring?.FINALIZED;

  if (pendingTime === undefined || finalizedTime === undefined) return null;

  return formatDuration((finalizedTime - pendingTime) / 1000);
}

/**
 * Get the execution result from a transaction's consensus data
 */
export function getExecutionResult(tx: Transaction): {
  executionResult?: string;
  genvmResult?: { stdout?: string; stderr?: string };
} | null {
  const leaderReceipt = (tx.consensus_data as {
    leader_receipt?: Array<{
      execution_result?: string;
      genvm_result?: { stdout?: string; stderr?: string };
    }>;
  })?.leader_receipt;

  if (!leaderReceipt?.[0]) return null;

  return {
    executionResult: leaderReceipt[0].execution_result,
    genvmResult: leaderReceipt[0].genvm_result,
  };
}
