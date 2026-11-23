<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue';
import {
  useContractsStore,
  useTransactionsStore,
  useNetworkStore,
} from '@/stores';
import { useRpcClient } from '@/hooks';
import {
  compareTimestamps,
  toSentTransactionItem,
} from '@/utils/sentTransactions';
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
// `items` is ordered newest first, as the server sorts (created_at, hash).
// The polled head page is reconciled into it and "Load more" appends older
// pages using the last row as a cursor, so insertions never shift pages.
const messagesState = ref<{
  address: string;
  items: TransactionItemType[];
  hasMore: boolean;
} | null>(null);
const loadingMore = ref(false);
const messageDetails = ref<Record<string, any>>({});

// Clear Transactions hides every message of a contract created up to the
// newest one listed at that moment (also the older, not yet loaded pages).
// The cutoff is kept per contract address in localStorage.
const CLEARED_STORAGE_KEY = 'transactionsList.clearedMessagesBefore';
// Cutoffs are the raw ISO created_at of the newest cleared message, so the
// backend's microsecond precision is preserved.
function readCutoffs(): Record<string, string> {
  try {
    const stored = JSON.parse(
      localStorage.getItem(CLEARED_STORAGE_KEY) ?? '{}',
    ) as Record<string, string | number>;
    return Object.fromEntries(
      Object.entries(stored).map(([address, value]) => [
        address,
        // Older versions stored epoch milliseconds.
        typeof value === 'number' ? new Date(value).toISOString() : value,
      ]),
    );
  } catch {
    return {};
  }
}
const clearedCutoffs = ref<Record<string, string>>(readCutoffs());
function setCutoff(address: string, cutoff: string) {
  clearedCutoffs.value = { ...clearedCutoffs.value, [address]: cutoff };
  try {
    localStorage.setItem(
      CLEARED_STORAGE_KEY,
      JSON.stringify(clearedCutoffs.value),
    );
  } catch {
    // Storage unavailable: the cutoff lasts for this session only.
  }
}

const currentAddress = computed(() => {
  const id = contractsStore.currentContractId;
  return contractsStore.deployedContracts
    .find((c) => c.contractId === id)
    ?.address?.toLowerCase();
});

const messages = computed<TransactionItemType[]>(() => {
  const state = messagesState.value;
  if (!state || state.address !== currentAddress.value) return [];
  const cutoff = clearedCutoffs.value[state.address];
  const seen = new Set<string>();
  return state.items
    .filter((m) => {
      // A new message may shift pages, so the same hash can appear twice.
      if (seen.has(m.hash)) return false;
      seen.add(m.hash);
      return !cutoff || compareTimestamps(m.data?.created_at, cutoff) > 0;
    })
    .map((m) => {
      const details = messageDetails.value[m.hash];
      return details ? { ...m, data: { ...m.data, ...details } } : m;
    });
});

const canLoadMore = computed(() => {
  const state = messagesState.value;
  return Boolean(
    state && state.address === currentAddress.value && state.hasMore,
  );
});

async function loadMoreMessages() {
  const state = messagesState.value;
  const address = currentAddress.value;
  if (!state || state.address !== address || !address || loadingMore.value) {
    return;
  }
  const gen = generation;
  loadingMore.value = true;
  try {
    const result = await rpcClient.getSentTransactions({
      address,
      limit: MESSAGES_PAGE_SIZE,
      before: cursorOf(state.items),
    });
    if (gen !== generation) return;
    const current = messagesState.value;
    // The list was reset meanwhile (gap or contract change): drop the page.
    if (!current || current.address !== address) return;
    if (cursorOf(current.items)?.hash !== cursorOf(state.items)?.hash) return;
    const page = toItems(result);
    messagesState.value = {
      ...current,
      items: [...current.items, ...page],
      hasMore: page.length === MESSAGES_PAGE_SIZE,
    };
  } catch (e) {
    console.warn('Error loading more sent transactions', e);
  } finally {
    loadingMore.value = false;
  }
}

function cursorOf(items: TransactionItemType[]) {
  const last = items[items.length - 1];
  return last
    ? { createdAt: last.data?.created_at, hash: last.hash }
    : undefined;
}

// Merge a freshly polled head page into the loaded list. Rows only get
// inserted at the head, so the loaded older rows are kept below the head's
// last overlapping row. Without overlap on a full page more than a page of
// new rows arrived: restart from the head to avoid a gap.
function mergeHead(
  previous: TransactionItemType[],
  head: TransactionItemType[],
) {
  if (previous.length === 0 || head.length < MESSAGES_PAGE_SIZE) {
    return {
      items: head,
      reset: previous.length === 0 || head.length < MESSAGES_PAGE_SIZE,
    };
  }
  const known = new Map(previous.map((m, i) => [m.hash, i]));
  // Index in `previous` of the last (oldest) head row it already knew.
  let overlapIndex = -1;
  for (const m of head) {
    const index = known.get(m.hash);
    if (index !== undefined) overlapIndex = index;
  }
  if (overlapIndex === -1) return { items: head, reset: true };
  const tail = previous.slice(overlapIndex + 1);
  return { items: [...head, ...tail], reset: false };
}

function toItems(result: any[] | undefined) {
  return (result ?? [])
    .filter((tx: any) => tx?.hash)
    .map((tx: any) =>
      toSentTransactionItem(tx, contractsStore.currentContractId ?? ''),
    );
}

function statusOf(tx: any): string | undefined {
  return tx?.statusName ?? tx?.status_name ?? tx?.status;
}

// Apply a fresher status to the loaded row (and so to its modal).
function applyStatus(hash: string, status: string | undefined) {
  const state = messagesState.value;
  if (!state || !status) return;
  if (!state.items.some((m) => m.hash === hash && m.statusName !== status)) {
    return;
  }
  messagesState.value = {
    ...state,
    items: state.items.map((m) =>
      m.hash === hash ? { ...m, statusName: status as any } : m,
    ),
  };
}

const FINAL_MESSAGE_STATUSES = new Set(['FINALIZED', 'CANCELED']);
const MAX_STALE_REFRESHES_PER_POLL = 10;

// Polling only refreshes the newest page. Older loaded rows that can still
// change status are re-checked with a status-only request, a bounded number
// per poll, rotating through all eligible rows so none is starved.
let staleRotation = 0;
async function refreshStaleMessages(gen: number, headHashes: Set<string>) {
  const eligible = (messagesState.value?.items ?? []).filter(
    (m) =>
      !headHashes.has(m.hash) &&
      !FINAL_MESSAGE_STATUSES.has(String(m.statusName)),
  );
  if (eligible.length === 0) return;
  const start = staleRotation % eligible.length;
  const count = Math.min(MAX_STALE_REFRESHES_PER_POLL, eligible.length);
  staleRotation = start + count;
  const batch = [...eligible.slice(start), ...eligible.slice(0, start)].slice(
    0,
    count,
  );
  await Promise.all(
    batch.map(async (m) => {
      try {
        const status = await rpcClient.getTransactionStatus({ hash: m.hash });
        if (gen === generation) applyStatus(m.hash, status);
      } catch (e) {
        console.warn('Error refreshing transaction status', e);
      }
    }),
  );
}

async function loadMessageDetails(hash: string) {
  try {
    const tx: any = await transactionsStore.getTransaction(hash as any);
    if (tx) {
      applyStatus(hash, statusOf(tx));
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
      limit: MESSAGES_PAGE_SIZE,
    });
    if (gen !== generation) return;
    const items = toItems(result);
    const previous = messagesState.value;
    const base = previous?.address === address ? previous.items : [];
    const merged = mergeHead(base, items);
    messagesState.value = {
      address,
      items: merged.items,
      hasMore:
        merged.reset || !previous || previous.address !== address
          ? items.length === MESSAGES_PAGE_SIZE
          : previous.hasMore,
    };
    await refreshStaleMessages(gen, new Set(items.map((m) => m.hash)));
    // Refresh details of rows already opened when their status moved on.
    for (const item of messagesState.value?.items ?? []) {
      const details = messageDetails.value[item.hash];
      if (details && statusOf(details) !== item.statusName) {
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
    messageDetails.value = {};
    startMessages();
  },
  { immediate: true },
);
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
  // Messages are not stored locally: hide everything up to the newest one.
  const address = currentAddress.value;
  const newest = messages.value
    .map((m) => m.data?.created_at as string)
    .filter(Boolean)
    .reduce<string | undefined>(
      (max, value) =>
        max === undefined || compareTimestamps(value, max) > 0 ? value : max,
      undefined,
    );
  if (address && newest) setCutoff(address, newest);

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
        :disabled="loadingMore"
        @click="loadMoreMessages"
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
        this contract so far (including older ones not loaded yet) are hidden
        permanently; only newer ones will appear.</template
      >
    </ConfirmationModal>
  </PageSection>
</template>
