<script setup lang="ts">
import type { ContractMethod } from 'genlayer-js/types';
import { abi } from 'genlayer-js';
import { TransactionHashVariant } from 'genlayer-js/types';
import { computed, ref } from 'vue';
import { Collapse } from 'vue-collapsed';
import { notify } from '@kyvg/vue3-notification';
import { ChevronDownIcon } from '@heroicons/vue/16/solid';
import { useEventTracking, useContractQueries } from '@/hooks';
import { unfoldArgsData, type ArgData } from './ContractParams';
import ContractParams from './ContractParams.vue';
import type {
  ExecutionMode,
  ReadStateMode,
  StudioExecutionFeeReportMessage,
  StudioFeeEstimateResult,
} from '@/types';

const {
  callWriteMethod,
  callReadMethod,
  simulateWriteMethod,
  estimateWriteMethodFees,
  contract,
} = useContractQueries();
const { trackEvent } = useEventTracking();

const props = defineProps<{
  name: string;
  method: ContractMethod;
  methodType: 'read' | 'write';
  executionMode: ExecutionMode;
  consensusMaxRotations?: number;
  simulationMode?: boolean;
  readStateMode?: ReadStateMode;
}>();

const isExpanded = ref(false);
const isCalling = ref(false);
const isEstimatingFees = ref(false);
const responseMessage = ref('');
const responseMessageAccepted = ref('');
const responseMessageFinalized = ref('');
const feeEstimateMessage = ref('');
const feeEstimateResult = ref<StudioFeeEstimateResult | null>(null);

const calldataArguments = ref<ArgData>({ args: [], kwargs: {} });
const payableValue = ref('');
const WEI_PER_GEN = BigInt('1000000000000000000');

type FeeEstimateRow = {
  label: string;
  value: string;
};

function payableValueWei(): bigint | undefined {
  return props.method.payable && payableValue.value
    ? BigInt(payableValue.value) * WEI_PER_GEN
    : undefined;
}

const formatResponseIfNeeded = (response: string): string => {
  if (!response) {
    return '';
  }
  // Check if the string looks like a malformed JSON (starts with { and ends with })
  if (response.startsWith('{') && response.endsWith('}')) {
    try {
      // Try to parse it as-is first
      return JSON.stringify(JSON.parse(response), null, 2);
    } catch {
      // If parsing fails, try to add commas between properties
      const fixedResponse = response.replace(/"\s*"(?=[^:]*:)/g, '","');
      try {
        // Validate the fixed string can be parsed as JSON
        return JSON.stringify(JSON.parse(fixedResponse), null, 2);
      } catch {
        // If still can't parse, return original
        return response;
      }
    }
  }
  // Remove quotes if the response is just a quoted empty string
  if (response === '""') {
    return '';
  }
  return response;
};

const formatIntegerLike = (
  value: string | number | bigint | boolean | null | undefined,
): string => {
  if (value === undefined || value === null) {
    return '';
  }
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false';
  }
  const raw = String(value);
  return /^-?\d+$/.test(raw) ? BigInt(raw).toLocaleString('en-US') : raw;
};

const formatFeeAmount = (
  value: string | number | bigint | null | undefined,
): string => {
  const formatted = formatIntegerLike(value);
  return formatted ? `${formatted} wei` : '';
};

const formatPaddingBps = (
  value: string | number | bigint | null | undefined,
): string => {
  if (value === undefined || value === null) {
    return '';
  }
  return `${formatIntegerLike(value)} bps`;
};

const formatRotations = (rotations: unknown): string => {
  if (!Array.isArray(rotations)) {
    return '';
  }
  return rotations.map((rotation) => formatIntegerLike(rotation)).join(', ');
};

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

const formatFeeParamsDecodedValue = (key: string, value: unknown): string => {
  if (Array.isArray(value)) {
    return value
      .map((item) => formatFeeParamsDecodedValue(key, item))
      .join(' / ');
  }

  if (feeParamsDecodedFeeKeys.has(key)) {
    return formatFeeAmount(value as string | number | bigint);
  }
  if (feeParamsDecodedIntegerKeys.has(key)) {
    return formatIntegerLike(value as string | number | bigint);
  }
  return String(value);
};

const formatFeeParamsDecoded = (value: unknown): string => {
  if (!value || typeof value !== 'object') {
    return '';
  }

  const record = value as Record<string, unknown>;
  const keys = Object.keys(record);
  if (keys.length === 0) {
    return '';
  }

  const orderedKeys = [
    ...feeParamsDecodedOrder.filter((key) => key in record),
    ...keys
      .filter((key) => !(key in feeParamsDecodedLabels))
      .sort((left, right) => left.localeCompare(right)),
  ];
  return orderedKeys
    .filter((key) => record[key] !== undefined && record[key] !== null)
    .map((key) => {
      const label = feeParamsDecodedLabels[key] ?? key;
      return `${label} ${formatFeeParamsDecodedValue(key, record[key])}`;
    })
    .join(', ');
};

const shortHex = (value: string | undefined, start = 8, end = 6): string => {
  if (!value) {
    return '';
  }
  if (value.length <= start + end) {
    return value;
  }
  return `${value.slice(0, start)}...${value.slice(-end)}`;
};

const addFeeEstimateRow = (
  rows: FeeEstimateRow[],
  label: string,
  value: string,
) => {
  if (value !== '') {
    rows.push({ label, value });
  }
};

const feeEstimateRows = computed<FeeEstimateRow[]>(() => {
  const result = feeEstimateResult.value;
  if (!result) {
    return [];
  }

  const preset = result.recommendedPreset;
  const distribution = preset?.distribution;
  const observed = preset?.observed;
  const report = result.feeReport;
  const messageFees = report?.messageFees;
  const metering = report?.executionMetering;
  const chargeable = report?.chargeableExecution;
  const proposalReceipt = report?.proposalReceipt;
  const messageReveal = report?.messageReveal;
  const rows: FeeEstimateRow[] = [];

  addFeeEstimateRow(rows, 'Scenario', result.scenario ?? '');
  addFeeEstimateRow(
    rows,
    'Recommended fee value',
    formatFeeAmount(preset?.feeValue),
  );
  addFeeEstimateRow(
    rows,
    'Execution budget / round',
    formatFeeAmount(distribution?.executionBudgetPerRound),
  );
  addFeeEstimateRow(
    rows,
    'Leader time units',
    formatIntegerLike(distribution?.leaderTimeunitsAllocation),
  );
  addFeeEstimateRow(
    rows,
    'Validator time units',
    formatIntegerLike(distribution?.validatorTimeunitsAllocation),
  );
  addFeeEstimateRow(
    rows,
    'Message fee budget',
    formatFeeAmount(distribution?.totalMessageFees),
  );
  addFeeEstimateRow(
    rows,
    'Appeal rounds',
    formatIntegerLike(distribution?.appealRounds),
  );
  addFeeEstimateRow(
    rows,
    'Rotations',
    formatRotations(distribution?.rotations),
  );
  addFeeEstimateRow(
    rows,
    'Max GEN / time unit',
    formatFeeAmount(distribution?.maxPriceGenPerTimeUnit),
  );
  addFeeEstimateRow(
    rows,
    'Storage gas price',
    formatFeeAmount(distribution?.storageFeeMaxGasPrice),
  );
  addFeeEstimateRow(
    rows,
    'Receipt gas price',
    formatFeeAmount(distribution?.receiptFeeMaxGasPrice),
  );
  addFeeEstimateRow(
    rows,
    'Proposal receipt bytes',
    formatIntegerLike(proposalReceipt?.receiptBytes),
  );
  addFeeEstimateRow(
    rows,
    'Proposal receipt gas',
    formatIntegerLike(proposalReceipt?.estimatedGas),
  );
  addFeeEstimateRow(
    rows,
    'Message count',
    formatIntegerLike(messageReveal?.messageCount),
  );
  addFeeEstimateRow(
    rows,
    'Message bytes',
    formatIntegerLike(messageReveal?.messageBytes),
  );
  addFeeEstimateRow(
    rows,
    'Message reveal gas',
    formatIntegerLike(messageReveal?.estimatedGas),
  );
  addFeeEstimateRow(rows, 'Padding', formatPaddingBps(preset?.paddingBps));
  addFeeEstimateRow(
    rows,
    'Message budget mode',
    preset?.messageBudgetMode ?? '',
  );
  addFeeEstimateRow(
    rows,
    'Observed execution',
    formatFeeAmount(observed?.executionFee),
  );
  addFeeEstimateRow(
    rows,
    'Observed message budget',
    formatFeeAmount(observed?.messageFeeBudget),
  );
  addFeeEstimateRow(
    rows,
    'Observed external reserve',
    formatFeeAmount(observed?.externalMessageReserved),
  );
  addFeeEstimateRow(
    rows,
    'Total estimated fee',
    formatFeeAmount(report?.totalEstimatedFee),
  );
  addFeeEstimateRow(
    rows,
    'Chargeable execution',
    formatFeeAmount(metering?.chargeableExecutionFee),
  );
  addFeeEstimateRow(
    rows,
    'Chargeable storage',
    formatFeeAmount(chargeable?.storage),
  );
  addFeeEstimateRow(
    rows,
    'Chargeable receipt/non-det',
    formatFeeAmount(chargeable?.receiptAndNondetOutput),
  );
  addFeeEstimateRow(
    rows,
    'Chargeable message',
    formatFeeAmount(chargeable?.message),
  );
  addFeeEstimateRow(
    rows,
    'GenVM raw execution',
    formatFeeAmount(metering?.genvmReportedExecution),
  );
  addFeeEstimateRow(
    rows,
    'Message fees spent',
    formatFeeAmount(messageFees?.declaredConsumed),
  );
  addFeeEstimateRow(
    rows,
    'External message reserved',
    formatFeeAmount(messageFees?.externalReserved),
  );
  addFeeEstimateRow(
    rows,
    'External message reimbursed',
    formatFeeAmount(messageFees?.externalReimbursed),
  );
  addFeeEstimateRow(
    rows,
    'External message remainder',
    formatFeeAmount(messageFees?.externalRemainder),
  );
  addFeeEstimateRow(
    rows,
    'Message fees remaining',
    formatFeeAmount(messageFees?.remaining),
  );

  return rows;
});

const feeEstimateMessages = computed<StudioExecutionFeeReportMessage[]>(() => {
  return feeEstimateResult.value?.feeReport?.messageReveal?.messages ?? [];
});

const handleCallReadMethod = async () => {
  responseMessage.value = '';
  isCalling.value = true;

  try {
    const variant =
      props.readStateMode === 'FINALIZED'
        ? TransactionHashVariant.LATEST_FINAL
        : TransactionHashVariant.LATEST_NONFINAL;

    const result = await callReadMethod(
      props.name,
      unfoldArgsData(calldataArguments.value),
      variant,
    );

    responseMessage.value =
      result !== undefined
        ? formatResponseIfNeeded(abi.calldata.toString(result))
        : '';

    trackEvent('called_read_method', {
      contract_name: contract.value?.name || '',
      method_name: props.name,
    });
  } catch (error) {
    notify({
      title: 'Error',
      text: (error as Error)?.message || 'Error getting contract state',
      type: 'error',
    });
  } finally {
    isCalling.value = false;
  }
};

const handleCallWriteMethod = async () => {
  isCalling.value = true;

  try {
    if (props.simulationMode) {
      // Simulation mode - call simulateWriteMethod
      responseMessageAccepted.value = '';
      responseMessageFinalized.value = '';

      const simValue = payableValueWei();
      const result = await simulateWriteMethod({
        method: props.name,
        args: unfoldArgsData({
          args: calldataArguments.value.args,
          kwargs: calldataArguments.value.kwargs,
        }),
        value: simValue,
      });

      responseMessageAccepted.value = formatResponseIfNeeded(
        typeof result === 'string' ? result : JSON.stringify(result, null, 2),
      );

      notify({
        text: 'Simulation completed',
        type: 'success',
      });

      trackEvent('simulated_write_method', {
        contract_name: contract.value?.name || '',
        method_name: props.name,
      });
    } else {
      // Real transaction mode
      // User inputs GEN, convert to wei (1 GEN = 10^18 wei)
      const txValue = payableValueWei() ?? BigInt(0);
      await callWriteMethod({
        method: props.name,
        executionMode: props.executionMode,
        consensusMaxRotations: props.consensusMaxRotations,
        args: unfoldArgsData({
          args: calldataArguments.value.args,
          kwargs: calldataArguments.value.kwargs,
        }),
        value: txValue,
      });

      notify({
        text: 'Write method called',
        type: 'success',
      });

      trackEvent('called_write_method', {
        contract_name: contract.value?.name || '',
        method_name: props.name,
      });
    }
  } catch (error) {
    notify({
      title: 'Error',
      text: (error as Error)?.message || 'Error getting contract state',
      type: 'error',
    });
  } finally {
    isCalling.value = false;
  }
};

const handleEstimateFees = async () => {
  isEstimatingFees.value = true;
  feeEstimateMessage.value = '';
  feeEstimateResult.value = null;

  try {
    const result = await estimateWriteMethodFees({
      method: props.name,
      args: unfoldArgsData({
        args: calldataArguments.value.args,
        kwargs: calldataArguments.value.kwargs,
      }),
      value: payableValueWei(),
    });

    feeEstimateResult.value = result;
    feeEstimateMessage.value = JSON.stringify(
      {
        scenario: result.scenario,
        feeReport: result.feeReport,
        recommendedPreset: result.recommendedPreset,
      },
      null,
      2,
    );

    notify({
      text: 'Fee estimate completed',
      type: 'success',
    });

    trackEvent('estimated_write_method_fees', {
      contract_name: contract.value?.name || '',
      method_name: props.name,
    });
  } catch (error) {
    notify({
      title: 'Error',
      text: (error as Error)?.message || 'Error estimating transaction fees',
      type: 'error',
    });
  } finally {
    isEstimatingFees.value = false;
  }
};
</script>

<template>
  <div
    class="flex flex-col overflow-hidden rounded-md bg-slate-100 dark:bg-zinc-800/60"
  >
    <button
      class="flex grow flex-row items-center justify-between bg-slate-200 p-2 text-xs hover:bg-slate-300 dark:bg-zinc-700 dark:hover:bg-zinc-600"
      @click="isExpanded = !isExpanded"
      :data-testid="`expand-method-btn-${name}`"
    >
      <div class="flex items-center gap-1 truncate">
        <span>{{ name }}</span>
        <span
          v-if="method.payable"
          class="text-[10px] italic text-slate-400 dark:text-zinc-500"
          >payable</span
        >
      </div>

      <ChevronDownIcon
        class="h-4 w-4 opacity-70 transition-all duration-300"
        :class="isExpanded && 'rotate-180'"
      />
    </button>

    <Collapse :when="isExpanded">
      <div class="flex flex-col items-start gap-2 p-2">
        <ContractParams
          :methodBase="props.method"
          @argsChanged="
            (v: ArgData) => {
              calldataArguments = v;
            }
          "
        />

        <div
          v-if="methodType === 'write' && method.payable"
          class="flex w-full flex-col"
        >
          <label class="mb-1 text-xs text-slate-500 dark:text-zinc-400"
            >Value (GEN)</label
          >
          <input
            v-model="payableValue"
            type="number"
            min="0"
            step="1"
            placeholder="0"
            class="rounded border border-slate-300 bg-white px-2 py-1 text-xs dark:border-zinc-600 dark:bg-zinc-700 dark:text-zinc-200"
            :data-testid="`payable-value-input-${name}`"
          />
        </div>

        <div class="flex flex-wrap gap-2">
          <Btn
            v-if="methodType === 'read'"
            @click="handleCallReadMethod"
            tiny
            :data-testid="`read-method-btn-${name}`"
            :loading="isCalling"
            :disabled="isCalling"
            >{{ isCalling ? 'Calling...' : 'Call Contract' }}</Btn
          >

          <Btn
            v-if="methodType === 'write'"
            @click="handleCallWriteMethod"
            tiny
            :data-testid="`write-method-btn-${name}`"
            :loading="isCalling"
            :disabled="isCalling"
            >{{
              isCalling
                ? simulationMode
                  ? 'Simulating...'
                  : 'Sending...'
                : simulationMode
                  ? 'Simulate'
                  : 'Send Transaction'
            }}</Btn
          >

          <Btn
            v-if="methodType === 'write' && simulationMode"
            @click="handleEstimateFees"
            tiny
            secondary
            :data-testid="`estimate-fees-btn-${name}`"
            :loading="isEstimatingFees"
            :disabled="isCalling || isEstimatingFees"
            >{{ isEstimatingFees ? 'Estimating...' : 'Estimate Fees' }}</Btn
          >
        </div>

        <!-- Read method response (single response based on mode) -->
        <div
          v-if="methodType === 'read' && responseMessage !== ''"
          class="w-full break-all text-sm"
        >
          <div class="mb-1 text-xs font-medium">
            Response ({{
              readStateMode === 'FINALIZED' ? 'Finalized' : 'Accepted'
            }}):
          </div>
          <div
            :data-testid="`method-response-${name}`"
            class="w-full whitespace-pre-wrap rounded bg-white p-1 font-mono text-xs dark:bg-zinc-700 dark:text-zinc-300"
          >
            {{ responseMessage }}
          </div>
        </div>

        <!-- Write method response (simulation result) -->
        <div
          v-if="methodType === 'write' && responseMessageAccepted !== ''"
          class="w-full break-all text-sm"
        >
          <div class="mb-1 text-xs font-medium">Simulation Result:</div>
          <div
            :data-testid="`method-response-${name}`"
            class="w-full whitespace-pre-wrap rounded bg-white p-1 font-mono text-xs dark:bg-zinc-700 dark:text-zinc-300"
          >
            {{ responseMessageAccepted }}
          </div>
        </div>

        <div
          v-if="methodType === 'write' && feeEstimateResult"
          class="w-full text-sm"
        >
          <div class="mb-1 text-xs font-medium">Fee Estimate:</div>
          <div
            :data-testid="`fee-estimate-summary-${name}`"
            class="grid w-full grid-cols-1 gap-2 md:grid-cols-2"
          >
            <div
              v-for="row in feeEstimateRows"
              :key="row.label"
              class="rounded border border-slate-200 bg-white p-2 dark:border-zinc-700 dark:bg-zinc-800"
            >
              <div
                class="text-[10px] font-semibold uppercase text-slate-500 dark:text-zinc-400"
              >
                {{ row.label }}
              </div>
              <div class="break-words font-mono text-xs dark:text-zinc-200">
                {{ row.value }}
              </div>
            </div>
          </div>
          <div
            v-if="feeEstimateMessages.length"
            class="mt-2 overflow-x-auto rounded border border-slate-200 bg-white dark:border-zinc-700 dark:bg-zinc-800"
            :data-testid="`fee-estimate-messages-${name}`"
          >
            <table class="min-w-full text-left text-[11px]">
              <thead
                class="border-b border-slate-200 bg-slate-50 text-slate-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400"
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
                  v-for="(message, index) in feeEstimateMessages"
                  :key="`${message.callKey}-${index}`"
                  class="border-b border-slate-200 last:border-b-0 dark:border-zinc-700"
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
                    {{ formatIntegerLike(message.dataBytes) }} B
                  </td>
                  <td class="px-2 py-1 font-mono">
                    {{ formatIntegerLike(message.feeParamsBytes) }} B
                    <span
                      v-if="message.feeParams && message.feeParams !== '0x'"
                      class="block text-[10px] text-slate-500 dark:text-zinc-400"
                    >
                      {{ shortHex(message.feeParams) }}
                    </span>
                    <span
                      v-if="formatFeeParamsDecoded(message.feeParamsDecoded)"
                      class="block text-[10px] text-slate-500 dark:text-zinc-400"
                    >
                      {{ formatFeeParamsDecoded(message.feeParamsDecoded) }}
                    </span>
                  </td>
                  <td class="px-2 py-1 font-mono">
                    {{ formatFeeAmount(message.declaredBudget) }}
                  </td>
                  <td class="px-2 py-1 font-mono">
                    {{ formatIntegerLike(message.allocationSubtreeBytes) }} B
                    <span
                      v-if="
                        message.allocationSubtree &&
                        message.allocationSubtree !== '0x'
                      "
                      class="block text-[10px] text-slate-500 dark:text-zinc-400"
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
          <details class="mt-2 w-full">
            <summary
              class="cursor-pointer text-xs text-slate-500 dark:text-zinc-400"
            >
              Raw estimate
            </summary>
            <pre
              :data-testid="`fee-estimate-response-${name}`"
              class="mt-1 w-full overflow-auto rounded bg-white p-2 font-mono text-xs dark:bg-zinc-700 dark:text-zinc-300"
              v-text="feeEstimateMessage"
            />
          </details>
        </div>
      </div>
    </Collapse>
  </div>
</template>
