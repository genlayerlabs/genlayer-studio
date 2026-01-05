import { Transaction, NewConsensusHistory } from './types';
import { isNewConsensusFormat } from './consensusUtils';
import { formatDuration } from './formatters';

/**
 * Check if a transaction is a contract deployment
 */
export function isContractDeploy(contractSnapshot: Record<string, unknown> | null): boolean {
  if (!contractSnapshot) return false;

  // Check if contract_code exists in the snapshot (can be at root or nested in states)
  if (contractSnapshot.contract_code) return true;

  // Check in states.finalized or states.accepted
  const states = contractSnapshot.states as Record<string, unknown> | undefined;
  if (states) {
    const finalized = states.finalized as Record<string, unknown> | undefined;
    const accepted = states.accepted as Record<string, unknown> | undefined;
    if (finalized?.contract_code || accepted?.contract_code) return true;
  }

  return false;
}

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
