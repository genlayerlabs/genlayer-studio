<script setup lang="ts">
import { ref, computed } from 'vue';
import { useContractsStore, useTransactionsStore } from '@/stores';
import { TrashIcon } from '@heroicons/vue/24/solid';
import TransactionItem from './TransactionItem.vue';
import PageSection from './PageSection.vue';
import EmptyListPlaceholder from '@/components/simulator/EmptyListPlaceholder.vue';

const contractsStore = useContractsStore();
const transactionsStore = useTransactionsStore();

const props = defineProps({
  finalityWindow: Number,
});

const transactions = computed(() => {
  const contractTransactions = transactionsStore.transactions.filter(
    (t) => t.localContractId === contractsStore.currentContractId,
  );

  const transactionsOrderedByDate = contractTransactions
    .slice()
    .sort(
      (a, b) =>
        new Date(b.data.created_at).getTime() -
        new Date(a.data.created_at).getTime(),
    );

  return transactionsOrderedByDate;
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
