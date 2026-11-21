<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue';
import {
  useContractsStore,
  useTransactionsStore,
  useNetworkStore,
} from '@/stores';
import { useRpcClient } from '@/hooks';
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

// Transactions sent by the current contract (messages it emitted). They are
// not created from this client, so they are fetched from the Studio backend.
const messages = ref<TransactionItemType[]>([]);

const currentAddress = computed(() => {
  const id = contractsStore.currentContractId;
  return contractsStore.deployedContracts
    .find((c) => c.contractId === id)
    ?.address?.toLowerCase();
});

function toMessageItem(tx: any): TransactionItemType {
  return {
    hash: tx.hash,
    type: tx.type === 1 || tx.type === 'deploy' ? 'deploy' : 'method',
    statusName: tx.statusName ?? tx.status_name ?? tx.status,
    contractAddress: tx.to_address ?? tx.recipient ?? '',
    localContractId: contractsStore.currentContractId ?? '',
    data: { ...tx },
  };
}

let messagesRequest = 0;
async function loadMessages() {
  const address = currentAddress.value;
  const request = ++messagesRequest;
  if (!address || !networkStore.isStudio) {
    messages.value = [];
    return;
  }
  try {
    const result = await rpcClient.getTransactionsForAddress({ address });
    // Drop stale responses (contract or network changed meanwhile).
    if (request !== messagesRequest) return;
    messages.value = (result ?? [])
      .filter(
        (tx: any) => tx?.hash && tx.from_address?.toLowerCase() === address,
      )
      .map(toMessageItem);
  } catch (e) {
    if (request !== messagesRequest) return;
    console.warn('Error loading transactions sent by contract', e);
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null;
watch(
  () => [currentAddress.value, networkStore.isStudio] as const,
  () => {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    loadMessages();
    if (currentAddress.value && networkStore.isStudio) {
      pollTimer = setInterval(loadMessages, MESSAGES_POLL_INTERVAL_MS);
    }
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  messagesRequest++;
  if (pollTimer) clearInterval(pollTimer);
});

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
      <TransactionItem
        v-for="transaction in transactions"
        :key="transaction.hash"
        :transaction="transaction"
        :finalityWindow="props.finalityWindow ?? 0"
      />
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
        >Are you sure you want to clear all transactions?</template
      >
    </ConfirmationModal>
  </PageSection>
</template>
