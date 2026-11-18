<script setup lang="ts">
import { notify } from '@kyvg/vue3-notification';
import { useTransactionsStore, useContractsStore } from '@/stores';
import { ref } from 'vue';
import PageSection from '@/components/Simulator/PageSection.vue';
import { ArchiveXIcon, DatabaseZapIcon, XIcon } from 'lucide-vue-next';
import { useConfig, useRpcClient, useSetupStores } from '@/hooks';

const contractsStore = useContractsStore();
const transactionsStore = useTransactionsStore();
const { setupStores } = useSetupStores();
const { canResetStudioState } = useConfig();
const rpcClient = useRpcClient();

const isResetStorageModalOpen = ref(false);
const isResetting = ref(false);
const isResetStudioStateModalOpen = ref(false);
const isResettingStudioState = ref(false);

const handleResetStorage = async () => {
  isResetting.value = true;
  try {
    await contractsStore.resetStorage();
    await transactionsStore.resetStorage();
    await setupStores();

    notify({
      title: 'Storage reset successfully',
      type: 'success',
    });
  } catch (error) {
    console.error(error);
    notify({
      title: 'Error',
      text: (error as Error)?.message || 'Error resetting the storage',
      type: 'error',
    });
  } finally {
    isResetStorageModalOpen.value = false;
    isResetting.value = false;
  }
};

// Wipes transactions and accounts in the Studio database and resets the
// consensus workers, so a stuck Studio can be recovered without
// `docker compose down -v`. Validators and contract files are kept.
const handleResetStudioState = async () => {
  isResettingStudioState.value = true;
  try {
    // Throws (and nothing is deleted) unless every consensus worker
    // confirmed it stopped its work, so local state is only cleared on success.
    const result = await rpcClient.resetStudioState();
    await transactionsStore.clearTransactionsForCurrentNetwork();
    await contractsStore.clearDeployedContractsForCurrentNetwork();

    notify({
      title: 'Transactions and accounts deleted',
      text: `Removed ${result?.deleted_transactions ?? 0} transactions and ${result?.deleted_accounts ?? 0} accounts; reset ${result?.workers_reset ?? 0} consensus workers`,
      type: 'success',
    });
  } catch (error) {
    console.error(error);
    notify({
      title: 'Error',
      text:
        (error as Error)?.message ||
        'Error resetting transactions and accounts',
      type: 'error',
    });
  } finally {
    isResetStudioStateModalOpen.value = false;
    isResettingStudioState.value = false;
  }
};
</script>

<template>
  <PageSection>
    <template #title>Storage</template>

    <div class="flex flex-row flex-wrap gap-2">
      <Btn
        @click="isResetStorageModalOpen = true"
        :icon="ArchiveXIcon"
        secondary
      >
        Reset Storage
      </Btn>

      <Btn
        v-if="canResetStudioState"
        @click="isResetStudioStateModalOpen = true"
        :icon="DatabaseZapIcon"
        secondary
        testId="btn-open-reset-studio-state"
      >
        Reset Transactions &amp; Accounts
      </Btn>
    </div>

    <ConfirmationModal
      :open="isResetStorageModalOpen"
      @confirm="handleResetStorage"
      @close="isResetStorageModalOpen = false"
      buttonText="Reset Storage"
      buttonTestId="btn-reset-storage"
      :dangerous="true"
      :confirming="isResetting"
    >
      <template #title>Reset Studio Storage</template>

      <template #description
        >The following items will be deleted from your local storage:
      </template>

      <div class="mx-auto text-sm font-normal">
        <div class="flex flex-row items-center gap-2">
          <XIcon :size="16" class="text-red-500" /> All contract files
        </div>
        <div class="flex flex-row items-center gap-2">
          <XIcon :size="16" class="text-red-500" /> All contract deployments
        </div>
        <div class="flex flex-row items-center gap-2">
          <XIcon :size="16" class="text-red-500" /> All transactions
        </div>
      </div>

      <Alert info>
        If you want to preserve contracts, download a copy of them from the
        <RouterLink
          :to="{ name: 'contracts' }"
          class="underline"
          @click="isResetStorageModalOpen = false"
          >contracts section</RouterLink
        >.
      </Alert>
    </ConfirmationModal>

    <ConfirmationModal
      :open="isResetStudioStateModalOpen"
      @confirm="handleResetStudioState"
      @close="isResetStudioStateModalOpen = false"
      buttonText="Reset Transactions & Accounts"
      buttonTestId="btn-reset-studio-state"
      :dangerous="true"
      :confirming="isResettingStudioState"
    >
      <template #title>Reset Transactions &amp; Accounts</template>

      <template #description
        >The following items will be deleted from the Studio database:
      </template>

      <div class="mx-auto text-sm font-normal">
        <div class="flex flex-row items-center gap-2">
          <XIcon :size="16" class="text-red-500" /> All transactions, including
          stuck ones
        </div>
        <div class="flex flex-row items-center gap-2">
          <XIcon :size="16" class="text-red-500" /> All account balances and
          deployed contract states
        </div>
      </div>

      <Alert info>
        Consensus workers are reset as well. Validators, providers and your
        contract files are kept.
      </Alert>
    </ConfirmationModal>
  </PageSection>
</template>
