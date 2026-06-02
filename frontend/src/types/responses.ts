export interface JsonRPCResponse<T> {
  id: string;
  jsonrpc: string;
  result: T;
  error?: {
    code: number;
    message: string;
    data?: any;
  };
}

export interface StudioFeesDistribution {
  leaderTimeunitsAllocation: string;
  validatorTimeunitsAllocation: string;
  appealRounds: string;
  executionBudgetPerRound: string;
  executionConsumed: string;
  totalMessageFees: string;
  rotations: string[];
  maxPriceGenPerTimeUnit: string;
  storageFeeMaxGasPrice: string;
  receiptFeeMaxGasPrice: string;
}

export interface StudioFeeConfig {
  enabled: boolean;
  policy: Record<string, string>;
  capabilities?: {
    messageFees?: Record<
      string,
      {
        accounting?: boolean;
        genvmExecution?: boolean;
        reason?: string;
      }
    >;
  };
  defaultFees: {
    distribution: StudioFeesDistribution;
    feeValue: string;
  };
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
  feeParamsDecoded?: Record<
    string,
    string | number | Array<string | number>
  > | null;
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
  messageBudgetMode?: 'current' | 'observed' | 'allocation-preserved' | string;
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

export interface StudioFeeEstimateResult {
  scenario?: string;
  receipt?: Record<string, unknown>;
  feeAccounting?: StudioFeeAccounting;
  feeReport?: StudioExecutionFeeReport;
  recommendedPreset?: StudioRecommendedFeePreset;
}
