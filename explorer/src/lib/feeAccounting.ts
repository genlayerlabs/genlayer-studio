import type { StudioFeeAccounting, StudioGenvmFeeBucketReport, Transaction } from './types';

export type FeeAccountingRow = {
  label: string;
  value: string;
};

const amountDistributionLabels = new Set([
  'Execution budget per round',
  'Message fee budget',
  'Max price per time unit',
  'Storage gas price',
  'Receipt gas price',
]);

const recommendedPresetFeeLabels = new Set([
  'Fee value',
  'Execution budget per round',
  'Message fee budget',
  'Max price per time unit',
  'Storage gas price',
  'Receipt gas price',
]);

const feeParamsDecodedLabels: Record<string, string> = {
  leaderTimeunitsAllocation: 'Leader',
  validatorTimeunitsAllocation: 'Validator',
  appealRounds: 'Appeals',
  executionBudgetPerRound: 'Exec budget',
  rotations: 'Rotations',
  gasLimit: 'Gas limit',
  maxGasPrice: 'Max gas price',
};

const feeParamsDecodedOrder = Object.keys(feeParamsDecodedLabels);
const feeParamsDecodedFeeKeys = new Set(['executionBudgetPerRound', 'maxGasPrice']);
const feeParamsDecodedIntegerKeys = new Set([
  'leaderTimeunitsAllocation',
  'validatorTimeunitsAllocation',
  'appealRounds',
  'gasLimit',
  'rotations',
]);

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null;
}

function isNonEmptyRecord(value: unknown): value is Record<string, unknown> {
  const record = asRecord(value);
  return Boolean(record && Object.keys(record).length > 0);
}

export function getStudioFeeAccounting(tx: Transaction): StudioFeeAccounting | null {
  const data = asRecord(tx.data);
  const consensusData = asRecord(tx.consensus_data);
  const leaderReceipts = consensusData?.leader_receipt;
  const leaderReceipt = Array.isArray(leaderReceipts) ? asRecord(leaderReceipts[0]) : null;
  const genvmResult = asRecord(leaderReceipt?.genvm_result);
  const candidates = [
    data?.fee_accounting,
    consensusData?.fee_accounting,
    genvmResult?.fee_accounting,
  ];
  const found = candidates.find(isNonEmptyRecord);
  return found ?? null;
}

export function toBigIntAmount(value: unknown): bigint | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'bigint') return value;
  if (typeof value === 'number') {
    return Number.isFinite(value) ? BigInt(Math.trunc(value)) : null;
  }
  if (typeof value === 'string') {
    try {
      return BigInt(value.trim());
    } catch {
      return null;
    }
  }
  return null;
}

export function formatInteger(value: unknown): string {
  const amount = toBigIntAmount(value);
  return amount === null ? '-' : amount.toLocaleString();
}

function formatGenFromWei(wei: bigint): string {
  const zero = BigInt(0);
  const weiPerGen = BigInt('1000000000000000000');
  const negative = wei < zero;
  const absWei = negative ? -wei : wei;
  const whole = absWei / weiPerGen;
  const remainder = absWei % weiPerGen;
  const sign = negative ? '-' : '';

  if (remainder === zero) return `${sign}${whole.toLocaleString()}`;

  const fraction = remainder.toString().padStart(18, '0');
  const trimmed = fraction.replace(/0+$/, '');
  const decimals = Math.min(6, Math.max(3, trimmed.length));
  return `${sign}${whole.toLocaleString()}.${fraction.slice(0, decimals)}`;
}

export function formatFeeAmount(value: unknown): string {
  const amount = toBigIntAmount(value);
  if (amount === null) return '-';

  const raw = `${amount.toLocaleString()} wei`;
  const zero = BigInt(0);
  const absAmount = amount < zero ? -amount : amount;
  if (absAmount < BigInt('1000000000000')) return raw;

  return `${formatGenFromWei(amount)} GEN (${raw})`;
}

function formatFeeParamsDecodedValue(key: string, value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .map((item) => formatFeeParamsDecodedValue(key, item))
      .join(' / ');
  }

  if (feeParamsDecodedFeeKeys.has(key)) return formatFeeAmount(value);
  if (feeParamsDecodedIntegerKeys.has(key)) return formatInteger(value);
  return String(value);
}

export function formatFeeParamsDecoded(value: unknown): string {
  const record = asRecord(value);
  if (!record || Object.keys(record).length === 0) return '-';

  const orderedKeys = [
    ...feeParamsDecodedOrder.filter((key) => key in record),
    ...Object.keys(record)
      .filter((key) => !(key in feeParamsDecodedLabels))
      .sort((left, right) => left.localeCompare(right)),
  ];

  const rows = orderedKeys
    .filter((key) => record[key] !== undefined && record[key] !== null)
    .map((key) => {
      const label = feeParamsDecodedLabels[key] ?? key;
      return `${label} ${formatFeeParamsDecodedValue(key, record[key])}`;
    });

  return rows.length > 0 ? rows.join(', ') : '-';
}

export function feeMetricRows(accounting: StudioFeeAccounting): FeeAccountingRow[] {
  return [
    ['Paid fee', accounting.paid_fee_value],
    ['Required fee', accounting.required_fee_value],
    ['Primary budget', accounting.primary_fee_budget],
    ['Primary spent', accounting.primary_fee_spent],
    ['Primary refunded', accounting.primary_fee_refunded],
    ['Execution budget', accounting.execution_budget_total],
    ['Execution consumed', accounting.execution_fee_consumed],
    ['GenVM message meter', accounting.genvm_message_fee_consumed],
    ['Message budget', accounting.message_fee_budget],
    ['Declared message spent', accounting.message_fee_consumed],
    ['Declared message refunded', accounting.message_fee_refunded],
    ['External reserved', accounting.external_message_fee_reserved],
    ['External reimbursed', accounting.external_message_fee_reimbursed],
    ['External remainder', accounting.external_message_fee_remainder],
    ['Appeal bonds', accounting.appeal_bonds_total],
    ['Total refunded', accounting.total_refunded],
  ]
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([label, value]) => ({
      label: String(label),
      value: formatFeeAmount(value),
    }));
}

export function feeDistributionRows(accounting: StudioFeeAccounting): FeeAccountingRow[] {
  const distribution = accounting.fees_distribution;
  if (!distribution) return [];
  return [
    ['Leader time units', distribution.leaderTimeunitsAllocation],
    ['Validator time units', distribution.validatorTimeunitsAllocation],
    ['Appeal rounds', distribution.appealRounds],
    ['Rotations', distribution.rotations?.join(' / ')],
    ['Execution budget per round', distribution.executionBudgetPerRound],
    ['Message fee budget', distribution.totalMessageFees],
    ['Max price per time unit', distribution.maxPriceGenPerTimeUnit],
    ['Storage gas price', distribution.storageFeeMaxGasPrice],
    ['Receipt gas price', distribution.receiptFeeMaxGasPrice],
  ]
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([label, value]) => ({
      label: String(label),
      value: amountDistributionLabels.has(String(label))
        ? formatFeeAmount(value)
        : String(value),
    }));
}

export function feeRecommendedPresetRows(
  accounting: StudioFeeAccounting,
): FeeAccountingRow[] {
  const preset = accounting.recommended_fee_preset;
  const distribution = preset?.distribution;
  if (!preset || !distribution) return [];

  return [
    ['Fee value', preset.feeValue],
    ['Padding', preset.paddingBps ? `${formatInteger(preset.paddingBps)} bps` : null],
    ['Validators', preset.numOfInitialValidators],
    ['Leader time units', distribution.leaderTimeunitsAllocation],
    ['Validator time units', distribution.validatorTimeunitsAllocation],
    ['Appeal rounds', distribution.appealRounds],
    ['Rotations', distribution.rotations?.join(' / ')],
    ['Execution budget per round', distribution.executionBudgetPerRound],
    ['Message fee budget', distribution.totalMessageFees],
    ['Max price per time unit', distribution.maxPriceGenPerTimeUnit],
    ['Storage gas price', distribution.storageFeeMaxGasPrice],
    ['Receipt gas price', distribution.receiptFeeMaxGasPrice],
    ['Message budget mode', preset.messageBudgetMode],
    ['Message allocations', preset.messageAllocations?.length],
  ]
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([label, value]) => ({
      label: String(label),
      value: recommendedPresetFeeLabels.has(String(label))
        ? formatFeeAmount(value)
        : String(value),
    }));
}

export function feeRecommendedObservedRows(
  accounting: StudioFeeAccounting,
): FeeAccountingRow[] {
  const observed = accounting.recommended_fee_preset?.observed;
  if (!observed) return [];

  return [
    ['Execution fee', observed.executionFee],
    ['Message fee budget', observed.messageFeeBudget],
    ['Declared message fees', observed.declaredMessageFees],
    ['External reserved', observed.externalMessageReserved],
    ['Estimated fee', observed.totalEstimatedFee],
    ['Studio metered fee', observed.totalStudioMeteredFee],
  ]
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([label, value]) => ({
      label: String(label),
      value: formatFeeAmount(value),
    }));
}

export function feeBucketRows(
  bucketReport: StudioGenvmFeeBucketReport | null | undefined,
): FeeAccountingRow[] {
  if (!bucketReport) return [];

  return [
    ['Receipt/nondet used', bucketReport.receiptAndNondetOutput, 'fee'],
    ['Storage used', bucketReport.storage, 'fee'],
    ['Total execution', bucketReport.totalExecution, 'fee'],
    ['Execution budget', bucketReport.executionBudgetPerRound, 'fee'],
    ['Budget remaining', bucketReport.executionBudgetRemaining, 'fee'],
    ['Budget overrun', bucketReport.executionBudgetOverrun, 'fee'],
    ['Budget exceeded', bucketReport.executionBudgetExceeded, 'boolean'],
    ['Message meter', bucketReport.message, 'fee'],
    ['Total with message', bucketReport.totalWithMessage, 'fee'],
  ]
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([label, value, kind]) => ({
      label: String(label),
      value: kind === 'boolean' ? String(value) : formatFeeAmount(value),
    }));
}
