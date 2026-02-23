export type TransactionStatus =
  | 'PENDING'
  | 'ACTIVATED'
  | 'CANCELED'
  | 'PROPOSING'
  | 'COMMITTING'
  | 'REVEALING'
  | 'ACCEPTED'
  | 'FINALIZED'
  | 'UNDETERMINED'
  | 'LEADER_TIMEOUT'
  | 'VALIDATORS_TIMEOUT';

export type ExecutionMode = 'LEADER_ONLY' | 'LEADER_SELF_VALIDATOR' | 'NORMAL';

export interface Transaction {
  hash: string;
  status: TransactionStatus;
  from_address: string | null;
  to_address: string | null;
  input_data: Record<string, unknown> | null;
  data: Record<string, unknown> | null;
  consensus_data: Record<string, unknown> | null;
  nonce: number | null;
  value: number | null;
  type: number | null;
  gaslimit: number | null;
  created_at: string | null;
  leader_only: boolean;
  execution_mode: ExecutionMode;
  r: number | null;
  s: number | null;
  v: number | null;
  appeal_failed: number | null;
  consensus_history: ConsensusHistoryData | null;
  timestamp_appeal: number | null;
  appeal_processing_time: number | null;
  contract_snapshot: Record<string, unknown> | null;
  config_rotation_rounds: number | null;
  num_of_initial_validators: number | null;
  last_vote_timestamp: number | null;
  rotation_count: number | null;
  leader_timeout_validators: string[] | null;
  sim_config: Record<string, unknown> | null;
  triggered_by_hash: string | null;
  triggered_count?: number; // Count of transactions triggered by this one
  appealed: boolean;
  appeal_undetermined: boolean;
  appeal_leader_timeout: boolean;
  appeal_validators_timeout: boolean;
  timestamp_awaiting_finalization: number | null;
  blocked_at: string | null;
  worker_id: string | null;
}

export interface ConsensusHistoryEntry {
  // Legacy format
  leader?: ValidatorVote;
  validators?: ValidatorVote[];
  votes?: VoteResult[];
  final?: boolean;
  round?: number;
}

// New consensus history format
export interface ConsensusRoundMonitoring {
  [key: string]: number; // Timestamp values for various states like PENDING, ACCEPTED, etc.
}

export interface ConsensusResult {
  monitoring: ConsensusRoundMonitoring;
  status_changes: string[];
  consensus_round: string;
  validator_results: unknown[];
}

export interface NewConsensusHistory {
  consensus_results: ConsensusResult[];
  current_monitoring: ConsensusRoundMonitoring;
  current_status_changes: string[];
}

// Union type to handle both formats
export type ConsensusHistoryData = ConsensusHistoryEntry[] | NewConsensusHistory;

export interface ValidatorVote {
  address?: string;
  validator_id?: number;
  vote?: string;
  result?: unknown;
  calldata?: unknown;
  mode?: string;
  eq_outputs?: {
    leader?: unknown;
  };
}

export interface VoteResult {
  validator_address?: string;
  vote?: 'agree' | 'disagree' | 'timeout' | string;
  result?: unknown;
}

export interface Validator {
  id: number;
  stake: number;
  config: Record<string, unknown>;
  address: string | null;
  provider: string;
  model: string;
  plugin: string;
  plugin_config: Record<string, unknown>;
  created_at: string | null;
  private_key?: string | null;
}

export interface CurrentState {
  id: string;
  data: Record<string, unknown>;
  balance: number;
  updated_at: string | null;
}

export interface LLMProvider {
  id: number;
  provider: string;
  model: string;
  config: Record<string, unknown> | string;
  plugin: string;
  plugin_config: Record<string, unknown>;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface DashboardStats {
  totalTransactions: number;
  transactionsByStatus: Record<TransactionStatus, number>;
  totalValidators: number;
  totalContracts: number;
  recentTransactions: Transaction[];
}
