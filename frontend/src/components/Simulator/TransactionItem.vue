<script setup lang="ts">
import { ref, computed } from 'vue';
import type { TransactionItem } from '@/types';
import TransactionStatusBadge from '@/components/Simulator/TransactionStatusBadge.vue';
import { useTimeAgo } from '@vueuse/core';
import ModalSection from '@/components/Simulator/ModalSection.vue';
import JsonViewer from '@/components/JsonViewer/json-viewer.vue';
import { useUIStore, useNodeStore, useTransactionsStore } from '@/stores';
import {
  CheckCircleIcon,
  XCircleIcon,
  EllipsisHorizontalCircleIcon,
} from '@heroicons/vue/16/solid';
import CopyTextButton from '../global/CopyTextButton.vue';
import { FilterIcon, GavelIcon, UserPen, UserSearch } from 'lucide-vue-next';
import {
  resultToUserFriendlyJson,
  b64ToArray,
  calldataToUserFriendlyJson,
} from '@/calldata/jsonifier';
import { getRuntimeConfigNumber } from '@/utils/runtimeConfig';

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

const badgeColorClass = computed(() => {
  if (props.transaction.statusName !== 'FINALIZED') {
    return '';
  } else {
    const executionResult =
      props.transaction.data?.last_round?.result || leaderReceipt.value?.result;
    if (
      executionResult === 6 &&
      leaderReceipt.value?.execution_result !== 'ERROR'
    ) {
      return '!bg-green-500';
    } else {
      return '!bg-red-500';
    }
  }
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
    </div>

    <div class="flex items-center justify-between gap-2 p-1">
      <Loader
        :size="15"
        v-if="
          transaction.statusName !== 'FINALIZED' &&
          transaction.statusName !== 'ACCEPTED' &&
          transaction.statusName !== 'UNDETERMINED' &&
          transaction.statusName !== 'LEADER_TIMEOUT' &&
          transaction.statusName !== 'VALIDATORS_TIMEOUT'
        "
      />

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
        <div class="mt-2 flex flex-col">
          <p
            class="text-md mb-1 flex flex-row items-center gap-2 font-semibold"
          >
            Status:
            <Loader
              :size="15"
              v-if="
                transaction.statusName !== 'FINALIZED' &&
                transaction.statusName !== 'ACCEPTED' &&
                transaction.statusName !== 'UNDETERMINED' &&
                transaction.statusName !== 'LEADER_TIMEOUT' &&
                transaction.statusName !== 'VALIDATORS_TIMEOUT'
              "
            />
            <TransactionStatusBadge
              :class="['px-[4px] py-[1px] text-[9px]', badgeColorClass]"
            >
              {{ transaction.statusName }}
            </TransactionStatusBadge>
          </p>
        </div>

        <ModalSection v-if="leaderReceipt">
          <template #title>
            Execution
            <TransactionStatusBadge
              :class="
                leaderReceipt.execution_result === 'ERROR'
                  ? '!bg-red-500'
                  : '!bg-green-500'
              "
            >
              {{ leaderReceipt.execution_result }}
            </TransactionStatusBadge>
          </template>

          <span class="text-sm font-semibold">Leader:</span>

          <div class="flex flex-row items-start gap-4 text-xs">
            <div>
              <div>
                <span class="font-medium">Gas used:</span>
                {{ leaderReceipt.gas_used }}
              </div>
              <div>
                <span class="font-medium"
                  >Stake: {{ leaderReceipt.node_config.stake }}</span
                >
              </div>
            </div>

            <div>
              <div class="mb-1 font-medium">Primary LLM:</div>
              <div>
                <span class="font-medium">Model:</span>
                {{ leaderReceipt.node_config.primary_model?.model }}
              </div>
              <div>
                <span class="font-medium">Provider:</span>
                {{ leaderReceipt.node_config.primary_model?.provider }}
              </div>
            </div>

            <div v-if="leaderReceipt.node_config.secondary_model">
              <div class="mb-1 font-medium">Secondary LLM:</div>
              <div>
                <span class="font-medium">Model:</span>
                {{ leaderReceipt.node_config.secondary_model.model }}
              </div>
              <div>
                <span class="font-medium">Provider:</span>
                {{ leaderReceipt.node_config.secondary_model.provider }}
              </div>
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
          <div>
            <pre
              class="overflow-x-auto whitespace-pre rounded bg-gray-200 p-1 text-xs text-gray-600 dark:bg-zinc-800 dark:text-gray-300"
              >{{ leaderReceipt?.result?.payload?.readable || 'None' }}</pre
            >
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

            <div
              class="divide-y overflow-hidden rounded border dark:border-gray-600"
            >
              <div
                v-if="history?.leader_result"
                class="flex flex-row items-center justify-between p-2 text-xs dark:border-gray-600"
              >
                <div class="flex items-center gap-1">
                  <UserPen class="h-4 w-4" />
                  <span class="font-mono text-xs">{{
                    history.leader_result[0].node_config.address
                  }}</span>
                </div>
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
                    <template v-if="history.leader_result[1].vote === 'agree'">
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

              <div
                v-for="(validator, vIndex) in history?.validator_results || []"
                :key="vIndex"
                class="flex flex-row items-center justify-between p-2 text-xs dark:border-gray-600"
              >
                <div class="flex items-center gap-1">
                  <UserSearch class="h-4 w-4" />
                  <span class="font-mono text-xs">{{
                    validator.node_config.address
                  }}</span>
                </div>
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
                </div>
              </div>
            </div>
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
