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

export interface StudioFeesDistribution {
  leaderTimeunitsAllocation?: string | number;
  validatorTimeunitsAllocation?: string | number;
  appealRounds?: string | number;
  executionBudgetPerRound?: string | number;
  executionConsumed?: string | number;
  totalMessageFees?: string | number;
  rotations?: Array<string | number>;
  maxPriceGenPerTimeUnit?: string | number;
  storageFeeMaxGasPrice?: string | number;
  receiptFeeMaxGasPrice?: string | number;
}

export interface StudioExecutionFeeReportMessage {
  messageFeeMode?: 'mode1' | 'mode2' | 'external';
  messageType: string;
  recipient: string;
  value: string | number;
  dataBytes: string | number;
  onAcceptance: boolean;
  saltNonce: string | number;
  feeParams?: string;
  feeParamsDecoded?: Record<string, string | number | Array<string | number>> | null;
  feeParamsBytes: string | number;
  declaredBudget: string | number;
  allocationSubtree?: string;
  allocationSubtreeBytes: string | number;
  callKey: string;
}

export interface StudioGenvmFeeBucket {
  index?: string | number;
  name?: string;
  consumed?: string | number;
}

export interface StudioGenvmFeeBucketReport {
  receiptAndNondetOutput?: string | number;
  storage?: string | number;
  message?: string | number;
  totalExecution?: string | number;
  totalWithMessage?: string | number;
  executionBudgetPerRound?: string | number;
  executionBudgetRemaining?: string | number;
  executionBudgetOverrun?: string | number;
  executionBudgetExceeded?: boolean;
  buckets?: StudioGenvmFeeBucket[];
}

export interface StudioExecutionFeeReport {
  receiptGasPrice?: string | number;
  budgetExhaustionReason?: string | null;
  proposalReceipt?: {
    eqBlocksOutputsLength?: string | number;
    receiptBytes?: string | number;
    estimatedGas?: string | number;
    fee?: string | number;
  };
  messageReveal?: {
    messageBytes?: string | number;
    messageCount?: string | number;
    estimatedGas?: string | number;
    fee?: string | number;
    consensusAdditionalGas?: string | number;
    consensusAdditionalFee?: string | number;
    studioFixedOverheadGas?: string | number;
    studioFixedOverheadFee?: string | number;
    messages?: StudioExecutionFeeReportMessage[];
  };
  genvmBuckets?: StudioGenvmFeeBucketReport;
  chargeableExecution?: StudioGenvmFeeBucketReport;
  executionMetering?: {
    chargeableExecutionFee?: string | number;
    genvmReportedExecution?: string | number;
    genvmDeltaFromChargeable?: string | number;
  };
  messageFees?: {
    budget?: string | number;
    declaredConsumed?: string | number;
    genvmMeteredConsumed?: string | number;
    externalReserved?: string | number;
    externalReimbursed?: string | number;
    externalRemainder?: string | number;
    totalConsumed?: string | number;
    declaredRefunded?: string | number;
    remaining?: string | number;
    meteringDelta?: string | number;
    reportedTotal?: string | number;
  };
  totalEstimatedFee?: string | number;
  totalStudioMeteredFee?: string | number;
}

export interface StudioRecommendedFeePreset {
  source?: string;
  paddingBps?: string | number;
  numOfInitialValidators?: string | number;
  distribution?: StudioFeesDistribution;
  feeValue?: string | number;
  messageAllocations?: unknown[];
  messageBudgetMode?:
    | 'current'
    | 'observed'
    | 'allocation-preserved'
    | (string & {});
  observed?: {
    executionFee?: string | number;
    messageFeeBudget?: string | number;
    declaredMessageFees?: string | number;
    externalMessageReserved?: string | number;
    totalEstimatedFee?: string | number;
    totalStudioMeteredFee?: string | number;
  };
}

export interface StudioFeeAccounting {
  version?: string | number;
  source?: string;
  status?: string;
  paid_fee_value?: string | number;
  required_fee_value?: string | number;
  primary_fee_required?: string | number;
  primary_fee_budget?: string | number;
  primary_fee_spent?: string | number;
  primary_fee_refunded?: string | number;
  execution_budget_total?: string | number;
  execution_fee_consumed?: string | number;
  execution_fee_consumed_buckets?: Array<string | number>;
  genvm_fee_consumed_buckets?: Array<string | number>;
  genvm_fee_bucket_report?: StudioGenvmFeeBucketReport;
  genvm_message_fee_consumed?: string | number;
  execution_fee_report?: StudioExecutionFeeReport;
  recommended_fee_preset?: StudioRecommendedFeePreset;
  message_fee_budget?: string | number;
  message_fee_consumed?: string | number;
  message_fee_refunded?: string | number;
  external_message_fee_reserved?: string | number;
  external_message_fee_reimbursed?: string | number;
  external_message_fee_remainder?: string | number;
  appeal_bonds_total?: string | number;
  total_refunded?: string | number;
  fees_distribution?: StudioFeesDistribution;
  message_allocations?: unknown[];
  allocation_consumed?: Record<string, string | number>;
  message_consumption_events?: unknown[];
  refunds?: unknown[];
  top_ups?: unknown[];
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
  tx_count?: number;
  created_at?: string | null;
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
