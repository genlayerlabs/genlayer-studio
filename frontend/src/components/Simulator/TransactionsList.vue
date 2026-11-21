<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue';
import {
  useContractsStore,
  useTransactionsStore,
  useNetworkStore,
} from '@/stores';
import { useRpcClient } from '@/hooks';
import { toSentTransactionItem } from '@/utils/sentTransactions';
import type { TransactionItem as TransactionItemType } from '@/types';
import { TrashIcon } from '@heroicons/vue/24/solid';
import TransactionItem from './TransactionItem.vue';
import PageSection from './PageSection.vue';
import EmptyListPlaceholder from '@/components/Simulator/EmptyListPlaceholder.vue';

const contractsStore = useContractsStore();
const transactionsStore = useTransactionsStore();
const networkStore = useNetworkStore();
const rpcClient = useRpcClient();

const props = defineProps({
  finalityWindow: Number,
});

const MESSAGES_POLL_INTERVAL_MS = 5_000;
const MESSAGES_PAGE_SIZE = 20;

// Transactions sent by the current contract (messages it emitted). They are
// not created from this client, so a lightweight, paginated summary is polled
// from the Studio backend; full details are fetched when a row is opened.
// Rows are keyed by the contract address they were fetched for, so a row of
// another contract is never shown.
const messagesState = ref<{
  address: string;
  items: TransactionItemType[];
} | null>(null);
const messagesLimit = ref(MESSAGES_PAGE_SIZE);
const messageDetails = ref<Record<string, any>>({});
const dismissedMessages = ref<Set<string>>(new Set());

const currentAddress = computed(() => {
  const id = contractsStore.currentContractId;
  return contractsStore.deployedContracts
    .find((c) => c.contractId === id)
    ?.address?.toLowerCase();
});

const messages = computed<TransactionItemType[]>(() => {
  const state = messagesState.value;
  if (!state || state.address !== currentAddress.value) return [];
  return state.items
    .filter((m) => !dismissedMessages.value.has(m.hash))
    .map((m) => {
      const details = messageDetails.value[m.hash];
      return details ? { ...m, data: { ...m.data, ...details } } : m;
    });
});

const canLoadMore = computed(() => {
  const state = messagesState.value;
  return (
    state?.address === currentAddress.value &&
    state?.items.length === messagesLimit.value
  );
});

async function loadMessageDetails(hash: string) {
  try {
    const tx: any = await transactionsStore.getTransaction(hash as any);
    if (tx) {
      messageDetails.value = {
        ...messageDetails.value,
        [hash]: { ...tx, hash },
      };
    }
  } catch (e) {
    console.warn('Error loading transaction details', e);
  }
}

// A message row opens its modal with the summary right away; the full
// transaction (consensus data, result, ...) is fetched on demand.
function onRowClick(transaction: TransactionItemType) {
  if (
    messages.value.some((m) => m.hash === transaction.hash) &&
    !messageDetails.value[transaction.hash]
  ) {
    loadMessageDetails(transaction.hash);
  }
}

let generation = 0;
let pollTimer: ReturnType<typeof setTimeout> | null = null;

function stopMessages() {
  generation++;
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
}

// One request at a time per contract: the next refresh is scheduled only
// after the previous one settled, so slow responses are never discarded.
async function refreshMessages(gen: number, address: string) {
  try {
    const result = await rpcClient.getSentTransactions({
      address,
      limit: messagesLimit.value,
    });
    if (gen !== generation) return;
    const items = (result ?? [])
      .filter((tx: any) => tx?.hash)
      .map((tx: any) =>
        toSentTransactionItem(tx, contractsStore.currentContractId ?? ''),
      );
    messagesState.value = { address, items };
    // Refresh details of rows already opened when their status moved on.
    for (const item of items) {
      const details = messageDetails.value[item.hash];
      if (
        details &&
        (details.statusName ?? details.status) !== item.statusName
      ) {
        loadMessageDetails(item.hash);
      }
    }
  } catch (e) {
    if (gen !== generation) return;
    console.warn('Error loading transactions sent by contract', e);
  }
  if (gen === generation) {
    pollTimer = setTimeout(
      () => refreshMessages(gen, address),
      MESSAGES_POLL_INTERVAL_MS,
    );
  }
}

function startMessages() {
  stopMessages();
  const address = currentAddress.value;
  if (!address || !networkStore.isStudio) {
    messagesState.value = null;
    return;
  }
  refreshMessages(generation, address);
}

watch(
  () => [currentAddress.value, networkStore.isStudio] as const,
  () => {
    messagesState.value = null;
    messagesLimit.value = MESSAGES_PAGE_SIZE;
    messageDetails.value = {};
    dismissedMessages.value = new Set();
    startMessages();
  },
  { immediate: true },
);
watch(messagesLimit, startMessages);
onBeforeUnmount(stopMessages);

const transactions = computed(() => {
  const contractTransactions = transactionsStore.transactions.filter(
    (t) => t.localContractId === contractsStore.currentContractId,
  );
  const known = new Set(contractTransactions.map((t) => t.hash));
  const all = [
    ...contractTransactions,
    ...messages.value.filter((m) => !known.has(m.hash)),
  ];

  return all.sort(
    (a, b) =>
      new Date(b.data?.created_at).getTime() -
      new Date(a.data?.created_at).getTime(),
  );
});

const isClearTransactionsModalOpen = ref(false);

const handleClearTransactions = () => {
  transactionsStore.clearTransactionsForContract(
    contractsStore.currentContractId ?? '',
  );
  // Messages are not stored locally: hide the ones currently listed.
  dismissedMessages.value = new Set([
    ...dismissedMessages.value,
    ...messages.value.map((m) => m.hash),
  ]);

  isClearTransactionsModalOpen.value = false;
};
</script>

<template>
  <PageSection data-testid="latest-transactions">
    <template #title>Transactions</template>

    <template #actions
      ><GhostBtn
        v-if="transactions.length > 0"
        @click="isClearTransactionsModalOpen = true"
        v-tooltip="'Clear Transactions List'"
      >
        <TrashIcon class="h-4 w-4" /></GhostBtn
    ></template>

    <div class="flex flex-col">
      <div
        v-for="transaction in transactions"
        :key="transaction.hash"
        @click="onRowClick(transaction)"
      >
        <TransactionItem
          :transaction="transaction"
          :finalityWindow="props.finalityWindow ?? 0"
        />
      </div>
      <button
        v-if="canLoadMore"
        class="py-1 text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400"
        data-testid="load-more-messages"
        @click="messagesLimit += MESSAGES_PAGE_SIZE"
      >
        Load more sent transactions
      </button>
    </div>

    <EmptyListPlaceholder v-if="transactions.length === 0">
      No transactions yet.
    </EmptyListPlaceholder>

    <ConfirmationModal
      :open="isClearTransactionsModalOpen"
      @close="isClearTransactionsModalOpen = false"
      @confirm="handleClearTransactions"
      buttonText="Clear Transactions"
      dangerous
    >
      <template #title>Clear Transaction List</template>
      <template #description
        >Are you sure you want to clear all transactions? Transactions sent by
        this contract are hidden from the list until new ones are
        sent.</template
      >
    </ConfirmationModal>
  </PageSection>
</template>
