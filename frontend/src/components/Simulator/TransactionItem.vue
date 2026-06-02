<script setup lang="ts">
import { ref, computed } from 'vue';
import type {
  StudioExecutionFeeReport,
  StudioExecutionFeeReportMessage,
  StudioFeeAccounting,
  TransactionItem,
} from '@/types';
import TransactionStatusBadge from '@/components/Simulator/TransactionStatusBadge.vue';
import { useTimeAgo } from '@vueuse/core';
import ModalSection from '@/components/Simulator/ModalSection.vue';
import JsonViewer from '@/components/JsonViewer/json-viewer.vue';
import { useUIStore, useNodeStore, useTransactionsStore } from '@/stores';
import { notify } from '@kyvg/vue3-notification';
import {
  CheckCircleIcon,
  XCircleIcon,
  EllipsisHorizontalCircleIcon,
} from '@heroicons/vue/16/solid';
import CopyTextButton from '../global/CopyTextButton.vue';
import {
  FilterIcon,
  GavelIcon,
  UserPen,
  UserSearch,
  ExternalLink,
} from 'lucide-vue-next';
import {
  resultToUserFriendlyJson,
  b64ToArray,
  calldataToUserFriendlyJson,
} from '@/calldata/jsonifier';
import {
  getRuntimeConfigBoolean,
  getRuntimeConfigNumber,
} from '@/utils/runtimeConfig';
import { getExplorerUrl } from '@/utils/explorerUrl';

const explorerUrl = computed(() => getExplorerUrl());

function extractErrorText(receipt: any): string | null {
  if (!receipt) return null;
  // Check genvm_result.stderr first (most detailed)
  const stderr = receipt.genvm_result?.stderr;
  if (stderr && typeof stderr === 'string' && stderr.trim())
    return stderr.trim();
  // Decode result bytes: first byte is status code, rest is error text for codes 1-3
  const raw = receipt.result;
  if (typeof raw === 'string' && raw.length > 0) {
    try {
      const decoded = resultToUserFriendlyJson(raw);
      if (
        decoded?.status &&
        decoded.status !== 'return' &&
        decoded.status !== 'none' &&
        decoded.payload
      ) {
        return `[${decoded.status}] ${decoded.payload}`;
      }
    } catch {
      // ignore decode failures
    }
  }
  return null;
}

const uiStore = useUIStore();
const nodeStore = useNodeStore();
const transactionsStore = useTransactionsStore();

const props = defineProps<{
  transaction: TransactionItem;
  finalityWindow: number;
}>();

const finalityWindowAppealFailedReduction = ref(
  getRuntimeConfigNumber('VITE_FINALITY_WINDOW_APPEAL_FAILED_REDUCTION', 0.2),
);

const isDetailsModalOpen = ref(false);

const timeThreshold = 6; // Number of hours after which the date should be displayed instead of time ago

const dateText = computed(() => {
  const currentDate = Date.now(); // Get the current timestamp in milliseconds
  const transactionDate = new Date(props.transaction.data.created_at).getTime(); // Convert transaction date to a timestamp
  const twelveHoursInMilliseconds = timeThreshold * 60 * 60 * 1000;

  if (currentDate - transactionDate > twelveHoursInMilliseconds) {
    return new Date(transactionDate).toLocaleString(); // Return formatted date string
  } else {
    return useTimeAgo(transactionDate).value; // Return time ago string (e.g., "3 hours ago")
  }
});

const leaderReceipt = computed(() => {
  return props.transaction?.data?.consensus_data?.leader_receipt?.[0];
});

function asRecord(value: unknown): Record<string, any> | null {
  return value && typeof value === 'object'
    ? (value as Record<string, any>)
    : null;
}

function isNonEmptyRecord(value: unknown): value is Record<string, any> {
  const record = asRecord(value);
  return Boolean(record && Object.keys(record).length > 0);
}

const feeAccounting = computed<StudioFeeAccounting | null>(() => {
  const tx = asRecord(props.transaction?.data);
  const txData = asRecord(tx?.data);
  const consensusData = asRecord(tx?.consensus_data);
  const genvmResult = asRecord(leaderReceipt.value?.genvm_result);
  const candidates = [
    txData?.fee_accounting,
    tx?.fee_accounting,
    consensusData?.fee_accounting,
    genvmResult?.fee_accounting,
  ];
  const found = candidates.find(isNonEmptyRecord);
  return found ? (found as StudioFeeAccounting) : null;
});

const feeReport = computed<StudioExecutionFeeReport | null>(() => {
  const report = feeAccounting.value?.execution_fee_report;
  return isNonEmptyRecord(report) ? report : null;
});

const genvmBuckets = computed(() => {
  return (
    feeReport.value?.genvmBuckets ??
    feeAccounting.value?.genvm_fee_bucket_report ??
    null
  );
});
const chargeableBucketRows = computed(() => {
  return feeBucketRows(feeReport.value?.chargeableExecution ?? null);
});
const genvmBucketRows = computed(() => {
  return feeBucketRows(genvmBuckets.value, 'GenVM message meter');
});

const messageFees = computed(() => feeReport.value?.messageFees ?? null);
const recommendedFeePreset = computed(() => {
  return feeAccounting.value?.recommended_fee_preset ?? null;
});

function toBigIntAmount(value: unknown): bigint | null {
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

function formatNumber(value: unknown): string {
  const amount = toBigIntAmount(value);
  return amount === null ? '-' : amount.toLocaleString();
}

function formatGenFromWei(wei: bigint): string {
  const weiPerGen = 1_000_000_000_000_000_000n;
  const negative = wei < 0n;
  const absWei = negative ? -wei : wei;
  const whole = absWei / weiPerGen;
  const remainder = absWei % weiPerGen;
  const sign = negative ? '-' : '';

  if (remainder === 0n) return `${sign}${whole.toLocaleString()}`;

  const fraction = remainder.toString().padStart(18, '0');
  const trimmed = fraction.replace(/0+$/, '');
  const decimals = Math.min(6, Math.max(3, trimmed.length));
  return `${sign}${whole.toLocaleString()}.${fraction.slice(0, decimals)}`;
}

function formatFeeAmount(value: unknown): string {
  const amount = toBigIntAmount(value);
  if (amount === null) return '-';

  const raw = `${amount.toLocaleString()} wei`;
  const absAmount = amount < 0n ? -amount : amount;
  if (absAmount < 1_000_000_000_000n) return raw;

  return `${formatGenFromWei(amount)} GEN (${raw})`;
}

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
const feeParamsDecodedFeeKeys = new Set([
  'executionBudgetPerRound',
  'maxGasPrice',
]);
const feeParamsDecodedIntegerKeys = new Set([
  'leaderTimeunitsAllocation',
  'validatorTimeunitsAllocation',
  'appealRounds',
  'gasLimit',
  'rotations',
]);

function formatFeeParamsDecodedValue(key: string, value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .map((item) => formatFeeParamsDecodedValue(key, item))
      .join(' / ');
  }

  if (feeParamsDecodedFeeKeys.has(key)) return formatFeeAmount(value);
  if (feeParamsDecodedIntegerKeys.has(key)) return formatNumber(value);
  return String(value);
}

function formatFeeParamsDecoded(value: unknown): string {
  const record = asRecord(value);
  if (!record || Object.keys(record).length === 0) return '-';

  const orderedKeys = [
    ...feeParamsDecodedOrder.filter((key) => key in record),
    ...Object.keys(record)
      .filter((key) => !(key in feeParamsDecodedLabels))
      .sort(),
  ];
  const rows = orderedKeys
    .filter((key) => record[key] !== undefined && record[key] !== null)
    .map((key) => {
      const label = feeParamsDecodedLabels[key] ?? key;
      return `${label} ${formatFeeParamsDecodedValue(key, record[key])}`;
    });

  return rows.length > 0 ? rows.join(', ') : '-';
}

function feeBucketRows(
  bucket: StudioExecutionFeeReport['genvmBuckets'] | null | undefined,
  messageLabel = 'Message meter',
) {
  if (!bucket) return [];
  return [
    ['Receipt/nondet used', bucket.receiptAndNondetOutput, 'fee'],
    ['Storage used', bucket.storage, 'fee'],
    ['Total execution', bucket.totalExecution, 'fee'],
    ['Execution budget', bucket.executionBudgetPerRound, 'fee'],
    ['Budget remaining', bucket.executionBudgetRemaining, 'fee'],
    ['Budget overrun', bucket.executionBudgetOverrun, 'fee'],
    ['Budget exceeded', bucket.executionBudgetExceeded, 'boolean'],
    [messageLabel, bucket.message, 'fee'],
    ['Total with message', bucket.totalWithMessage, 'fee'],
  ]
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([label, value, kind]) => ({
      label: String(label),
      value: kind === 'boolean' ? String(value) : formatFeeAmount(value),
    }));
}

function shortHex(value: string | undefined, start = 8, end = 6): string {
  if (!value) return '-';
  if (value.length <= start + end) return value;
  return `${value.slice(0, start)}...${value.slice(-end)}`;
}

const feeSummaryRows = computed(() => {
  const accounting = feeAccounting.value;
  if (!accounting) return [];
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
});

const feeDistributionRows = computed(() => {
  const distribution = feeAccounting.value?.fees_distribution;
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
    .map(([label, value]) => {
      const amountLabels = new Set([
        'Execution budget per round',
        'Message fee budget',
        'Max price per time unit',
        'Storage gas price',
        'Receipt gas price',
      ]);
      return {
        label: String(label),
        value: amountLabels.has(String(label))
          ? formatFeeAmount(value)
          : Array.isArray(value)
            ? value.join(' / ')
            : String(value),
      };
    });
});

const recommendedFeeRows = computed(() => {
  const preset = recommendedFeePreset.value;
  const distribution = preset?.distribution;
  if (!preset || !distribution) return [];

  return [
    ['Fee value', preset.feeValue],
    [
      'Padding',
      preset.paddingBps ? `${formatNumber(preset.paddingBps)} bps` : null,
    ],
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
  ]
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([label, value]) => {
      const feeLabels = new Set([
        'Fee value',
        'Execution budget per round',
        'Message fee budget',
        'Max price per time unit',
        'Storage gas price',
        'Receipt gas price',
      ]);
      return {
        label: String(label),
        value: feeLabels.has(String(label))
          ? formatFeeAmount(value)
          : String(value),
      };
    });
});

const recommendedObservedRows = computed(() => {
  const observed = recommendedFeePreset.value?.observed;
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
});

const reportedMessages = computed<StudioExecutionFeeReportMessage[]>(() => {
  return feeReport.value?.messageReveal?.messages ?? [];
});

const eqOutputs = computed(() => {
  const outputs = leaderReceipt.value?.eq_outputs || {};
  return Object.entries(outputs).map(([key, value]: [string, unknown]) => {
    const decodedResult = resultToUserFriendlyJson(value);
    const parsedValue = decodedResult?.payload?.readable ?? value;
    try {
      if (typeof parsedValue === 'string') {
        return {
          key,
          value: JSON.parse(parsedValue),
        };
      }
    } catch (e) {
      console.error('Error parsing JSON:', e);
    }
    return {
      key,
      value: parsedValue,
    };
  });
});

const shortHash = computed(() => {
  return props.transaction.hash?.slice(0, 6);
});

const handleSetTransactionAppeal = async () => {
  await transactionsStore.setTransactionAppeal(props.transaction.hash);
};

const isAppealed = computed(() => props.transaction.data.appealed);
const isHosted = getRuntimeConfigBoolean('VITE_IS_HOSTED', false);
const hasSenderAddress = computed(() =>
  Boolean(
    props.transaction.data?.from_address || props.transaction.data?.sender,
  ),
);

const canCancel = computed(() => {
  const status = String(props.transaction.statusName);
  const cancellableStatus = status === 'PENDING' || status === 'ACTIVATED';
  const requiresAdminOnly =
    isHosted && props.transaction.type === 'upgrade' && !hasSenderAddress.value;
  return cancellableStatus && !requiresAdminOnly;
});
const isCancelling = ref(false);
const handleCancelTransaction = async () => {
  isCancelling.value = true;
  try {
    await transactionsStore.cancelTransaction(props.transaction.hash);
  } catch (e: any) {
    notify({
      type: 'error',
      title: 'Error cancelling transaction',
      text: e?.message ?? 'Unable to cancel transaction',
    });
    console.error('Error cancelling transaction', e);
  } finally {
    isCancelling.value = false;
  }
};

function prettifyTxData(x: any): any {
  const oldResult = x?.consensus_data?.leader_receipt?.[0].result;

  if (oldResult) {
    try {
      x.consensus_data.leader_receipt[0].result =
        resultToUserFriendlyJson(oldResult);
    } catch (e) {
      console.log(e);
    }
  }

  const oldCalldata = x?.consensus_data?.leader_receipt?.[0].calldata;

  if (oldCalldata) {
    try {
      x.consensus_data.leader_receipt[0].calldata = {
        base64: oldCalldata,
        ...calldataToUserFriendlyJson(b64ToArray(oldCalldata)),
      };
    } catch (e) {
      console.log(e);
    }
  }

  const oldDataCalldata = x?.data?.calldata;

  if (oldDataCalldata) {
    try {
      x.data.calldata = {
        base64: oldDataCalldata,
        ...calldataToUserFriendlyJson(b64ToArray(oldDataCalldata)),
      };
    } catch (e) {
      console.log(e);
    }
  }

  const oldEqOutputs = x?.consensus_data?.leader_receipt?.[0].eq_outputs;
  if (oldEqOutputs == undefined) {
    return x;
  }
  try {
    const newEqOutputs = Object.fromEntries(
      Object.entries(oldEqOutputs).map(([k, v]) => {
        const val = resultToUserFriendlyJson(v);
        return [k, val];
      }),
    );
    const ret = {
      ...x,
      consensus_data: {
        ...x.consensus_data,
        leader_receipt: [
          {
            ...x.consensus_data.leader_receipt[0],
            eq_outputs: newEqOutputs,
          },
          x.consensus_data.leader_receipt[1],
        ],
      },
    };
    return ret;
  } catch (e) {
    console.log(e);
    return x;
  }
}

const leaderErrorDetail = computed(() => {
  if (leaderReceipt.value?.execution_result !== 'ERROR') return null;
  return extractErrorText(leaderReceipt.value);
});

const badgeColorClass = computed(() => {
  const status = props.transaction.statusName;
  if (status !== 'FINALIZED' && status !== 'ACCEPTED') {
    return '';
  }
  const executionResult =
    props.transaction.data?.last_round?.result || leaderReceipt.value?.result;
  if (leaderReceipt.value?.execution_result === 'ERROR') {
    return '!bg-red-500';
  } else if (executionResult === 6) {
    return '!bg-green-500';
  } else if (status === 'ACCEPTED') {
    return '!bg-green-500';
  } else if (status === 'FINALIZED') {
    return '!bg-red-500';
  }
  return '';
});
</script>

<template>
  <div
    class="group flex cursor-pointer flex-row items-center justify-between gap-2 rounded p-0.5 pl-1 hover:bg-gray-100 dark:hover:bg-zinc-700"
    @click="isDetailsModalOpen = true"
  >
    <div class="flex flex-row text-xs text-gray-500 dark:text-gray-400">
      <span class="font-mono">{{ shortHash }}</span>
      <span class="font-normal">...</span>
    </div>

    <div class="grow truncate text-left text-[11px] font-medium">
      {{
        transaction.type === 'method'
          ? transaction.decodedData?.functionName
          : transaction.type === 'upgrade'
            ? 'Upgrade'
            : 'Deploy'
      }}
    </div>

    <div class="hidden flex-row items-center gap-1 group-hover:flex">
      <CopyTextButton
        :text="transaction.hash"
        v-tooltip="'Copy transaction hash'"
        class="h-4 w-4"
      />

      <button
        @click.stop="nodeStore.searchFilter = transaction.hash"
        class="active:scale-90"
      >
        <FilterIcon
          v-tooltip="'Filter logs by hash'"
          class="h-4 w-4 text-gray-400 outline-none transition-all hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400"
        />
      </button>

      <a
        :href="`${explorerUrl}/tx/${transaction.hash}`"
        target="_blank"
        @click.stop
        v-tooltip="'View in Explorer'"
        class="active:scale-90"
      >
        <ExternalLink
          class="h-4 w-4 text-gray-400 outline-none transition-all hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400"
        />
      </a>
    </div>

    <div class="flex items-center justify-between gap-2 p-1">
      <Loader
        :size="15"
        v-if="
          transaction.statusName !== 'FINALIZED' &&
          transaction.statusName !== 'ACCEPTED' &&
          transaction.statusName !== 'UNDETERMINED' &&
          transaction.statusName !== 'LEADER_TIMEOUT' &&
          transaction.statusName !== 'VALIDATORS_TIMEOUT' &&
          transaction.statusName !== 'CANCELED'
        "
      />

      <div @click.stop="">
        <Btn
          v-if="canCancel"
          @click="handleCancelTransaction"
          tiny
          class="!h-[18px] !px-[4px] !py-[1px] !text-[9px] !font-medium"
          :data-testid="`cancel-transaction-btn-${transaction.hash}`"
          :loading="isCancelling"
          :disabled="isCancelling"
        >
          <div class="flex items-center gap-1">
            {{ isCancelling ? 'CANCELLING...' : 'CANCEL' }}
            <XCircleIcon class="h-2.5 w-2.5" />
          </div>
        </Btn>
      </div>

      <div @click.stop="">
        <Btn
          v-if="
            transaction.data.leader_only == false &&
            (transaction.statusName == 'ACCEPTED' ||
              transaction.statusName == 'UNDETERMINED' ||
              transaction.statusName == 'LEADER_TIMEOUT' ||
              transaction.statusName == 'VALIDATORS_TIMEOUT') &&
            Date.now() / 1000 -
              transaction.data.timestamp_awaiting_finalization -
              transaction.data.appeal_processing_time <=
              finalityWindow *
                (1 - finalityWindowAppealFailedReduction) **
                  transaction.data.appeal_failed
          "
          @click="handleSetTransactionAppeal"
          tiny
          class="!h-[18px] !px-[4px] !py-[1px] !text-[9px] !font-medium"
          :data-testid="`appeal-transaction-btn-${transaction.hash}`"
          :loading="isAppealed"
          :disabled="isAppealed"
        >
          <div class="flex items-center gap-1">
            {{ isAppealed ? 'APPEALED...' : 'APPEAL' }}
            <GavelIcon class="h-2.5 w-2.5" />
          </div>
        </Btn>
      </div>

      <TransactionStatusBadge
        :class="['px-[4px] py-[1px] text-[9px]', badgeColorClass]"
      >
        {{ transaction.statusName }}
      </TransactionStatusBadge>
    </div>

    <Modal :open="isDetailsModalOpen" @close="isDetailsModalOpen = false" wide>
      <template #title>
        <div class="flex flex-row items-center justify-between gap-2">
          <div>
            Transaction
            <span class="text-sm font-medium text-gray-400">
              {{
                transaction.type === 'method'
                  ? 'Method Call'
                  : transaction.type === 'upgrade'
                    ? 'Code Upgrade'
                    : 'Contract Deployment'
              }}
            </span>
          </div>

          <span class="text-[12px]">
            {{ dateText }}
          </span>
        </div>
      </template>

      <template #info>
        <div
          class="flex flex-row items-center justify-center gap-2 text-xs font-normal"
        >
          {{ transaction.hash }}
          <CopyTextButton :text="transaction.hash" />
        </div>
      </template>

      <div class="flex flex-col gap-4">
        <ModalSection>
          <template #title>Execution</template>

          <div class="flex flex-row gap-2 text-sm">
            <div class="flex items-center gap-2">
              <span class="font-medium">Status:</span>
              <Loader
                :size="15"
                v-if="
                  transaction.statusName !== 'FINALIZED' &&
                  transaction.statusName !== 'ACCEPTED' &&
                  transaction.statusName !== 'UNDETERMINED' &&
                  transaction.statusName !== 'LEADER_TIMEOUT' &&
                  transaction.statusName !== 'VALIDATORS_TIMEOUT' &&
                  transaction.statusName !== 'CANCELED'
                "
              />
              <TransactionStatusBadge
                :class="['px-[4px] py-[1px] text-[9px]', badgeColorClass]"
              >
                {{ transaction.statusName }}
              </TransactionStatusBadge>
            </div>

            <div v-if="leaderReceipt" class="flex items-center gap-2">
              <span class="font-medium">Result:</span>
              <TransactionStatusBadge
                :class="
                  leaderReceipt.execution_result === 'ERROR'
                    ? '!bg-red-500'
                    : '!bg-green-500'
                "
              >
                {{ leaderReceipt.execution_result }}
              </TransactionStatusBadge>
            </div>
          </div>
        </ModalSection>

        <ModalSection v-if="feeAccounting">
          <template #title>Fees</template>

          <div class="flex flex-col gap-3 text-xs">
            <div
              class="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5"
            >
              <div
                v-for="row in feeSummaryRows"
                :key="row.label"
                class="rounded border border-gray-200 bg-gray-50 p-2 dark:border-zinc-700 dark:bg-zinc-800"
              >
                <div class="text-[10px] text-gray-500 dark:text-gray-400">
                  {{ row.label }}
                </div>
                <div class="break-all font-mono text-[11px]">
                  {{ row.value }}
                </div>
              </div>
            </div>

            <div
              v-if="feeDistributionRows.length"
              class="overflow-hidden rounded border border-gray-200 dark:border-zinc-700"
            >
              <div
                v-for="row in feeDistributionRows"
                :key="row.label"
                class="grid grid-cols-[minmax(130px,180px)_1fr] border-b border-gray-200 last:border-b-0 dark:border-zinc-700"
              >
                <div
                  class="bg-gray-50 px-2 py-1.5 font-medium text-gray-600 dark:bg-zinc-800 dark:text-gray-300"
                >
                  {{ row.label }}
                </div>
                <div class="break-all px-2 py-1.5 font-mono">
                  {{ row.value }}
                </div>
              </div>
            </div>

            <div
              v-if="recommendedFeeRows.length"
              class="grid grid-cols-1 gap-2 lg:grid-cols-2"
            >
              <div
                class="rounded border border-gray-200 p-2 dark:border-zinc-700"
              >
                <div class="mb-1 font-medium">Recommended Preset</div>
                <div class="grid grid-cols-2 gap-x-2 gap-y-1">
                  <template v-for="row in recommendedFeeRows" :key="row.label">
                    <span class="text-gray-500 dark:text-gray-400">{{
                      row.label
                    }}</span>
                    <span class="break-all font-mono">{{ row.value }}</span>
                  </template>
                </div>
              </div>

              <div
                v-if="recommendedObservedRows.length"
                class="rounded border border-gray-200 p-2 dark:border-zinc-700"
              >
                <div class="mb-1 font-medium">Observed Usage</div>
                <div class="grid grid-cols-2 gap-x-2 gap-y-1">
                  <template
                    v-for="row in recommendedObservedRows"
                    :key="row.label"
                  >
                    <span class="text-gray-500 dark:text-gray-400">{{
                      row.label
                    }}</span>
                    <span class="break-all font-mono">{{ row.value }}</span>
                  </template>
                </div>
              </div>
            </div>

            <div v-if="feeReport" class="grid grid-cols-1 gap-2 md:grid-cols-3">
              <div
                v-if="feeReport.proposalReceipt"
                class="rounded border border-gray-200 p-2 dark:border-zinc-700"
              >
                <div class="mb-1 font-medium">Proposal Receipt</div>
                <div class="grid grid-cols-2 gap-x-2 gap-y-1">
                  <span class="text-gray-500 dark:text-gray-400">Bytes</span>
                  <span class="font-mono">{{
                    formatNumber(feeReport.proposalReceipt.receiptBytes)
                  }}</span>
                  <span class="text-gray-500 dark:text-gray-400">Gas</span>
                  <span class="font-mono">{{
                    formatNumber(feeReport.proposalReceipt.estimatedGas)
                  }}</span>
                  <span class="text-gray-500 dark:text-gray-400">Fee</span>
                  <span class="break-all font-mono">{{
                    formatFeeAmount(feeReport.proposalReceipt.fee)
                  }}</span>
                </div>
              </div>

              <div
                v-if="feeReport.messageReveal"
                class="rounded border border-gray-200 p-2 dark:border-zinc-700"
              >
                <div class="mb-1 font-medium">Message Reveal</div>
                <div class="grid grid-cols-2 gap-x-2 gap-y-1">
                  <span class="text-gray-500 dark:text-gray-400">Messages</span>
                  <span class="font-mono">{{
                    formatNumber(feeReport.messageReveal.messageCount)
                  }}</span>
                  <span class="text-gray-500 dark:text-gray-400">Bytes</span>
                  <span class="font-mono">{{
                    formatNumber(feeReport.messageReveal.messageBytes)
                  }}</span>
                  <span class="text-gray-500 dark:text-gray-400">Gas</span>
                  <span class="font-mono">{{
                    formatNumber(feeReport.messageReveal.estimatedGas)
                  }}</span>
                  <span class="text-gray-500 dark:text-gray-400"
                    >Chain gas</span
                  >
                  <span class="font-mono">{{
                    formatNumber(feeReport.messageReveal.consensusAdditionalGas)
                  }}</span>
                  <span class="text-gray-500 dark:text-gray-400"
                    >Fixed gas</span
                  >
                  <span class="font-mono">{{
                    formatNumber(feeReport.messageReveal.studioFixedOverheadGas)
                  }}</span>
                  <span class="text-gray-500 dark:text-gray-400"
                    >Chain fee</span
                  >
                  <span class="break-all font-mono">{{
                    formatFeeAmount(
                      feeReport.messageReveal.consensusAdditionalFee,
                    )
                  }}</span>
                  <span class="text-gray-500 dark:text-gray-400"
                    >Fixed fee</span
                  >
                  <span class="break-all font-mono">{{
                    formatFeeAmount(
                      feeReport.messageReveal.studioFixedOverheadFee,
                    )
                  }}</span>
                  <span class="text-gray-500 dark:text-gray-400"
                    >Studio meter</span
                  >
                  <span class="break-all font-mono">{{
                    formatFeeAmount(feeReport.messageReveal.fee)
                  }}</span>
                </div>
              </div>

              <div
                class="rounded border border-gray-200 p-2 dark:border-zinc-700"
              >
                <div class="mb-1 font-medium">Execution Report</div>
                <div class="grid grid-cols-2 gap-x-2 gap-y-1">
                  <span class="text-gray-500 dark:text-gray-400"
                    >Receipt gas price</span
                  >
                  <span class="break-all font-mono">{{
                    formatFeeAmount(feeReport.receiptGasPrice)
                  }}</span>
                  <span class="text-gray-500 dark:text-gray-400"
                    >Estimated fee</span
                  >
                  <span class="break-all font-mono">{{
                    formatFeeAmount(feeReport.totalEstimatedFee)
                  }}</span>
                  <span
                    v-if="feeReport.totalStudioMeteredFee !== undefined"
                    class="text-gray-500 dark:text-gray-400"
                    >Studio metered</span
                  >
                  <span
                    v-if="feeReport.totalStudioMeteredFee !== undefined"
                    class="break-all font-mono"
                    >{{
                      formatFeeAmount(feeReport.totalStudioMeteredFee)
                    }}</span
                  >
                  <span
                    v-if="feeReport.budgetExhaustionReason"
                    class="text-gray-500 dark:text-gray-400"
                    >Budget exhaustion</span
                  >
                  <span
                    v-if="feeReport.budgetExhaustionReason"
                    class="break-all font-mono"
                    >{{ feeReport.budgetExhaustionReason }}</span
                  >
                  <template v-if="messageFees">
                    <span class="text-gray-500 dark:text-gray-400"
                      >Message budget</span
                    >
                    <span class="break-all font-mono">{{
                      formatFeeAmount(messageFees.budget)
                    }}</span>
                    <span class="text-gray-500 dark:text-gray-400"
                      >Declared message spent</span
                    >
                    <span class="break-all font-mono">{{
                      formatFeeAmount(messageFees.declaredConsumed)
                    }}</span>
                    <span
                      v-if="messageFees.externalReserved !== undefined"
                      class="text-gray-500 dark:text-gray-400"
                      >External reserved</span
                    >
                    <span
                      v-if="messageFees.externalReserved !== undefined"
                      class="break-all font-mono"
                      >{{ formatFeeAmount(messageFees.externalReserved) }}</span
                    >
                    <span
                      v-if="messageFees.externalReimbursed !== undefined"
                      class="text-gray-500 dark:text-gray-400"
                      >External executor reimbursed</span
                    >
                    <span
                      v-if="messageFees.externalReimbursed !== undefined"
                      class="break-all font-mono"
                      >{{
                        formatFeeAmount(messageFees.externalReimbursed)
                      }}</span
                    >
                    <span
                      v-if="messageFees.externalRemainder !== undefined"
                      class="text-gray-500 dark:text-gray-400"
                      >External remainder</span
                    >
                    <span
                      v-if="messageFees.externalRemainder !== undefined"
                      class="break-all font-mono"
                      >{{
                        formatFeeAmount(messageFees.externalRemainder)
                      }}</span
                    >
                    <span
                      v-if="messageFees.totalConsumed !== undefined"
                      class="text-gray-500 dark:text-gray-400"
                      >Total message spent</span
                    >
                    <span
                      v-if="messageFees.totalConsumed !== undefined"
                      class="break-all font-mono"
                      >{{ formatFeeAmount(messageFees.totalConsumed) }}</span
                    >
                    <span
                      v-if="messageFees.reportedTotal !== undefined"
                      class="text-gray-500 dark:text-gray-400"
                      >Reported message total</span
                    >
                    <span
                      v-if="messageFees.reportedTotal !== undefined"
                      class="break-all font-mono"
                      >{{ formatFeeAmount(messageFees.reportedTotal) }}</span
                    >
                    <span class="text-gray-500 dark:text-gray-400"
                      >Declared message refunded</span
                    >
                    <span class="break-all font-mono">{{
                      formatFeeAmount(messageFees.declaredRefunded)
                    }}</span>
                    <span class="text-gray-500 dark:text-gray-400"
                      >Message remaining</span
                    >
                    <span class="break-all font-mono">{{
                      formatFeeAmount(messageFees.remaining)
                    }}</span>
                    <span class="text-gray-500 dark:text-gray-400"
                      >Metering delta</span
                    >
                    <span class="break-all font-mono">{{
                      formatFeeAmount(messageFees.meteringDelta)
                    }}</span>
                  </template>
                  <template v-if="feeReport.executionMetering">
                    <span class="text-gray-500 dark:text-gray-400"
                      >Chargeable exec</span
                    >
                    <span class="break-all font-mono">{{
                      formatFeeAmount(
                        feeReport.executionMetering.chargeableExecutionFee,
                      )
                    }}</span>
                    <span class="text-gray-500 dark:text-gray-400"
                      >GenVM raw exec</span
                    >
                    <span class="break-all font-mono">{{
                      formatFeeAmount(
                        feeReport.executionMetering.genvmReportedExecution,
                      )
                    }}</span>
                    <span class="text-gray-500 dark:text-gray-400"
                      >Raw delta</span
                    >
                    <span class="break-all font-mono">{{
                      formatFeeAmount(
                        feeReport.executionMetering.genvmDeltaFromChargeable,
                      )
                    }}</span>
                  </template>
                  <template v-if="chargeableBucketRows.length">
                    <span
                      class="col-span-2 mt-2 border-b border-gray-200 pb-1 text-[10px] font-semibold uppercase text-gray-500 first:mt-0 dark:border-zinc-700 dark:text-gray-400"
                      >Chargeable buckets</span
                    >
                    <template
                      v-for="row in chargeableBucketRows"
                      :key="`chargeable-${row.label}`"
                    >
                      <span class="text-gray-500 dark:text-gray-400">{{
                        row.label
                      }}</span>
                      <span class="break-all font-mono">{{ row.value }}</span>
                    </template>
                  </template>
                  <template v-if="genvmBucketRows.length">
                    <span
                      class="col-span-2 mt-2 border-b border-gray-200 pb-1 text-[10px] font-semibold uppercase text-gray-500 first:mt-0 dark:border-zinc-700 dark:text-gray-400"
                      >GenVM raw buckets</span
                    >
                    <template
                      v-for="row in genvmBucketRows"
                      :key="`genvm-${row.label}`"
                    >
                      <span class="text-gray-500 dark:text-gray-400">{{
                        row.label
                      }}</span>
                      <span class="break-all font-mono">{{ row.value }}</span>
                    </template>
                  </template>
                </div>
              </div>
            </div>

            <div
              v-if="reportedMessages.length"
              class="overflow-x-auto rounded border border-gray-200 dark:border-zinc-700"
            >
              <table class="min-w-full text-left text-[11px]">
                <thead
                  class="border-b border-gray-200 bg-gray-50 text-gray-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-gray-400"
                >
                  <tr>
                    <th class="px-2 py-1 font-medium">Type</th>
                    <th class="px-2 py-1 font-medium">Mode</th>
                    <th class="px-2 py-1 font-medium">Recipient</th>
                    <th class="px-2 py-1 font-medium">Value</th>
                    <th class="px-2 py-1 font-medium">Data</th>
                    <th class="px-2 py-1 font-medium">Fee Params</th>
                    <th class="px-2 py-1 font-medium">Declared Budget</th>
                    <th class="px-2 py-1 font-medium">Allocation</th>
                    <th class="px-2 py-1 font-medium">On</th>
                    <th class="px-2 py-1 font-medium">Call Key</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(message, index) in reportedMessages"
                    :key="`${message.callKey}-${index}`"
                    class="border-b border-gray-200 last:border-b-0 dark:border-zinc-700"
                  >
                    <td class="px-2 py-1">{{ message.messageType }}</td>
                    <td class="px-2 py-1">
                      {{ message.messageFeeMode || '-' }}
                    </td>
                    <td class="px-2 py-1 font-mono">
                      {{ shortHex(message.recipient) }}
                    </td>
                    <td class="px-2 py-1 font-mono">
                      {{ formatFeeAmount(message.value) }}
                    </td>
                    <td class="px-2 py-1 font-mono">
                      {{ formatNumber(message.dataBytes) }} B
                    </td>
                    <td class="px-2 py-1 font-mono">
                      {{ formatNumber(message.feeParamsBytes) }} B
                      <span
                        v-if="message.feeParams && message.feeParams !== '0x'"
                        class="block text-[10px] text-gray-500 dark:text-gray-400"
                      >
                        {{ shortHex(message.feeParams) }}
                      </span>
                      <span
                        v-if="
                          formatFeeParamsDecoded(message.feeParamsDecoded) !==
                          '-'
                        "
                        class="block text-[10px] text-gray-500 dark:text-gray-400"
                      >
                        {{ formatFeeParamsDecoded(message.feeParamsDecoded) }}
                      </span>
                    </td>
                    <td class="px-2 py-1 font-mono">
                      {{ formatFeeAmount(message.declaredBudget) }}
                    </td>
                    <td class="px-2 py-1 font-mono">
                      {{ formatNumber(message.allocationSubtreeBytes) }} B
                      <span
                        v-if="
                          message.allocationSubtree &&
                          message.allocationSubtree !== '0x'
                        "
                        class="block text-[10px] text-gray-500 dark:text-gray-400"
                      >
                        {{ shortHex(message.allocationSubtree) }}
                      </span>
                    </td>
                    <td class="px-2 py-1">
                      {{ message.onAcceptance ? 'accepted' : 'finalized' }}
                    </td>
                    <td class="px-2 py-1 font-mono">
                      {{ shortHex(message.callKey) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </ModalSection>

        <ModalSection v-if="transaction.data.data?.calldata">
          <template #title>Input</template>

          <pre
            v-if="transaction.data.data.calldata.readable"
            class="overflow-x-auto whitespace-pre rounded bg-gray-200 p-1 text-xs text-gray-600 dark:bg-zinc-800 dark:text-gray-300"
            >{{ transaction.data.data.calldata.readable }}</pre
          >
          <pre
            v-if="!transaction.data.data.calldata.readable"
            class="overflow-x-auto whitespace-pre rounded bg-gray-200 p-1 text-xs text-gray-600 dark:bg-zinc-800 dark:text-gray-300"
            >{{ transaction.data.data.calldata.base64 }}</pre
          >
        </ModalSection>

        <ModalSection v-if="transaction.data.data?.calldata">
          <template #title>Output</template>
          <div class="flex flex-col gap-2">
            <pre
              class="overflow-x-auto whitespace-pre rounded bg-gray-200 p-1 text-xs text-gray-600 dark:bg-zinc-800 dark:text-gray-300"
              >{{ leaderReceipt?.result?.payload?.readable || 'None' }}</pre
            >
            <div
              v-if="leaderErrorDetail"
              class="rounded border border-red-300 bg-red-50 p-2 text-xs text-red-700 dark:border-red-700 dark:bg-red-900/20 dark:text-red-300"
            >
              <div class="mb-1 font-medium">Error Detail</div>
              <pre class="whitespace-pre-wrap break-all">{{
                leaderErrorDetail
              }}</pre>
            </div>
          </div>
        </ModalSection>

        <ModalSection v-if="eqOutputs.length > 0">
          <template #title>Equivalence Principles Output</template>
          <div class="flex flex-col gap-2">
            <div v-for="(output, index) in eqOutputs" :key="index">
              <div class="mb-1 text-xs font-medium">
                Equivalence Principle #{{ output.key }}:
              </div>
              <pre
                class="overflow-x-auto whitespace-pre rounded bg-gray-200 p-1 text-xs text-gray-600 dark:bg-zinc-800 dark:text-gray-300"
                >{{ output.value }}</pre
              >
            </div>
          </div>
        </ModalSection>

        <ModalSection
          v-if="
            transaction.data.consensus_history &&
            (transaction.data.consensus_history.consensus_results?.length ||
              transaction.data.consensus_history.current_status_changes?.length)
          "
        >
          <template #title>Consensus History</template>

          <div
            v-for="(history, index) in transaction.data.consensus_history
              .consensus_results || []"
            :key="index"
            class="mb-4"
          >
            <div class="mb-2 flex flex-col gap-1">
              <span class="font-medium italic">
                {{
                  history?.consensus_round ||
                  `Consensus Round ${Number(index) + 1}`
                }}
              </span>
              <div
                class="flex items-center gap-2 text-[10px] text-gray-600 dark:text-gray-400"
              >
                <template
                  v-for="(status, sIndex) in history.status_changes"
                  :key="sIndex"
                >
                  <span>{{ status }}</span>
                  <span
                    v-if="Number(sIndex) < history.status_changes.length - 1"
                    class="text-gray-400"
                    >→</span
                  >
                </template>
              </div>
            </div>
          </div>
        </ModalSection>
        <ModalSection
          v-if="transaction.data.consensus_history?.consensus_results?.length"
        >
          <template #title>Validator Set</template>

          <div
            class="divide-y overflow-hidden rounded border dark:border-gray-600"
          >
            <template
              v-for="(history, index) in transaction.data.consensus_history
                .consensus_results || []"
              :key="index"
            >
              <!-- Leader row -->
              <div
                v-if="history?.leader_result"
                class="flex flex-col p-2 text-xs dark:border-gray-600"
              >
                <div class="flex flex-row items-center justify-between">
                  <div class="flex flex-col gap-0.5">
                    <div class="flex items-center gap-1">
                      <UserPen class="h-4 w-4" />
                      <span class="font-mono text-xs">{{
                        history.leader_result[0].node_config.address
                      }}</span>
                    </div>
                    <div
                      v-if="history.leader_result[0].node_config.primary_model"
                      class="ml-5 text-[10px] text-gray-500 dark:text-gray-400"
                    >
                      {{
                        history.leader_result[0].node_config.primary_model
                          .provider
                      }}
                      /
                      {{
                        history.leader_result[0].node_config.primary_model.model
                      }}
                    </div>
                  </div>
                  <div class="flex flex-row items-center gap-2">
                    <span
                      v-if="history.leader_result[0].execution_result"
                      :class="[
                        'rounded px-1.5 py-0.5 text-[9px] font-medium text-white',
                        history.leader_result[0].execution_result === 'ERROR'
                          ? 'bg-red-500'
                          : 'bg-green-500',
                      ]"
                    >
                      {{ history.leader_result[0].execution_result }}
                    </span>
                    <div class="flex flex-row items-center gap-1 capitalize">
                      <!-- LEADER_ONLY mode: single receipt means successful execution, not timeout -->
                      <template
                        v-if="
                          history.leader_result.length === 1 &&
                          transaction.data.execution_mode === 'LEADER_ONLY'
                        "
                      >
                        <CheckCircleIcon class="h-4 w-4 text-green-500" />
                        Leader Only
                      </template>
                      <template v-else-if="history.leader_result.length === 1">
                        <EllipsisHorizontalCircleIcon
                          class="h-4 w-4 text-yellow-500"
                        />
                        Timeout
                      </template>
                      <template v-else>
                        <template
                          v-if="history.leader_result[1].vote === 'agree'"
                        >
                          <CheckCircleIcon class="h-4 w-4 text-green-500" />
                          Agree
                        </template>
                        <template
                          v-if="history.leader_result[1].vote === 'disagree'"
                        >
                          <XCircleIcon class="h-4 w-4 text-red-500" />
                          Disagree
                        </template>
                        <template
                          v-if="history.leader_result[1].vote === 'timeout'"
                        >
                          <EllipsisHorizontalCircleIcon
                            class="h-4 w-4 text-yellow-500"
                          />
                          Timeout
                        </template>
                      </template>
                    </div>
                  </div>
                </div>
                <div
                  v-if="extractErrorText(history.leader_result[0])"
                  class="ml-5 mt-1 rounded bg-red-50 px-2 py-1 text-[10px] text-red-600 dark:bg-red-900/20 dark:text-red-300"
                >
                  <pre class="whitespace-pre-wrap break-all">{{
                    extractErrorText(history.leader_result[0])
                  }}</pre>
                </div>
              </div>

              <!-- Validator rows -->
              <div
                v-for="(validator, vIndex) in history.validator_results || []"
                :key="`${index}-${vIndex}`"
                class="flex flex-col p-2 text-xs dark:border-gray-600"
              >
                <div class="flex flex-row items-center justify-between">
                  <div class="flex flex-col gap-0.5">
                    <div class="flex items-center gap-1">
                      <UserSearch class="h-4 w-4" />
                      <span class="font-mono text-xs">{{
                        validator.node_config.address
                      }}</span>
                    </div>
                    <div
                      v-if="validator.node_config.primary_model"
                      class="ml-5 text-[10px] text-gray-500 dark:text-gray-400"
                    >
                      {{ validator.node_config.primary_model.provider }} /
                      {{ validator.node_config.primary_model.model }}
                    </div>
                  </div>
                  <div class="flex flex-row items-center gap-2">
                    <span
                      v-if="validator.execution_result"
                      :class="[
                        'rounded px-1.5 py-0.5 text-[9px] font-medium text-white',
                        validator.execution_result === 'ERROR'
                          ? 'bg-red-500'
                          : 'bg-green-500',
                      ]"
                    >
                      {{ validator.execution_result }}
                    </span>
                    <div class="flex flex-row items-center gap-1 capitalize">
                      <template v-if="validator.vote === 'agree'">
                        <CheckCircleIcon class="h-4 w-4 text-green-500" />
                        Agree
                      </template>
                      <template v-if="validator.vote === 'disagree'">
                        <XCircleIcon class="h-4 w-4 text-red-500" />
                        Disagree
                      </template>
                      <template v-if="validator.vote === 'timeout'">
                        <EllipsisHorizontalCircleIcon
                          class="h-4 w-4 text-yellow-500"
                        />
                        Timeout
                      </template>
                      <template v-if="validator.vote === 'idle'">
                        <EllipsisHorizontalCircleIcon
                          class="h-4 w-4 text-gray-400"
                        />
                        Idle
                      </template>
                    </div>
                  </div>
                </div>
                <div
                  v-if="extractErrorText(validator)"
                  class="ml-5 mt-1 rounded bg-red-50 px-2 py-1 text-[10px] text-red-600 dark:bg-red-900/20 dark:text-red-300"
                >
                  <pre class="whitespace-pre-wrap break-all">{{
                    extractErrorText(validator)
                  }}</pre>
                </div>
              </div>
            </template>
          </div>
        </ModalSection>

        <ModalSection v-if="leaderReceipt?.eq_outputs?.leader">
          <template #title>Equivalence Principle Output</template>

          <pre
            class="overflow-x-auto whitespace-pre rounded bg-gray-200 p-1 text-xs text-gray-600 dark:bg-zinc-800 dark:text-gray-300"
            >{{ leaderReceipt?.eq_outputs?.leader }}</pre
          >
        </ModalSection>

        <ModalSection v-if="transaction.data">
          <template #title>Full Transaction Data</template>

          <JsonViewer
            class="overflow-y-auto rounded-md bg-white p-2 dark:bg-zinc-800"
            :value="prettifyTxData(transaction.data || {})"
            :theme="uiStore.mode === 'light' ? 'light' : 'dark'"
            :expand="true"
            sort
          />
        </ModalSection>
      </div>
    </Modal>
  </div>
</template>
