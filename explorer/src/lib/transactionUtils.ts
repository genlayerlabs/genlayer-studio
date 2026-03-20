import { Transaction } from './types';
import { isNewConsensusFormat } from './consensusUtils';
import { formatDuration } from './formatters';
import { decodeResult, type DecodedResult } from './resultDecoder';

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
 * Get the consensus round result (e.g. "Accepted", "Majority Disagree", "Leader Rotation")
 * from the last consensus round in the transaction's history.
 */
export function getConsensusRoundResult(tx: Transaction): string | null {
  if (!tx.consensus_history || !isNewConsensusFormat(tx.consensus_history)) {
    return null;
  }

  const results = tx.consensus_history.consensus_results;
  if (!results.length) return null;

  return results[results.length - 1].consensus_round || null;
}

/**
 * Get the execution result from a transaction's consensus data,
 * including decoded result payload and equivalence principle outputs.
 */
export function getExecutionResult(tx: Transaction): {
  executionResult?: string;
  genvmResult?: { stdout?: string; stderr?: string };
  decodedResult?: DecodedResult;
  eqOutputs?: Record<string, DecodedResult>;
} | null {
  const leaderReceipt = (tx.consensus_data as {
    leader_receipt?: Array<{
      execution_result?: string;
      genvm_result?: { stdout?: string; stderr?: string };
      result?: unknown;
      eq_outputs?: Record<string, unknown>;
    }>;
  })?.leader_receipt;

  if (!leaderReceipt?.[0]) return null;

  const receipt = leaderReceipt[0];

  // Decode the result payload (base64 → status + human-readable payload)
  let decodedResult: DecodedResult | undefined;
  if (receipt.result !== undefined && receipt.result !== null) {
    try {
      decodedResult = decodeResult(receipt.result);
    } catch {
      // Silently ignore decode failures
    }
  }

  // Decode equivalence principle outputs
  let eqOutputs: Record<string, DecodedResult> | undefined;
  if (receipt.eq_outputs && typeof receipt.eq_outputs === 'object') {
    eqOutputs = {};
    for (const [key, value] of Object.entries(receipt.eq_outputs)) {
      try {
        eqOutputs[key] = decodeResult(value);
      } catch {
        eqOutputs[key] = { raw: value, status: '<unknown>' };
      }
    }
  }

  return {
    executionResult: receipt.execution_result,
    genvmResult: receipt.genvm_result,
    decodedResult,
    eqOutputs,
  };
}
