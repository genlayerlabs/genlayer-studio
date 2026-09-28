<script setup lang="ts">
import { notify } from '@kyvg/vue3-notification';
import { useTransactionsStore, useContractsStore } from '@/stores';
import { ref } from 'vue';
import PageSection from '@/components/simulator/PageSection.vue';
import { ArchiveXIcon, XIcon } from 'lucide-vue-next';
import { useSetupStores } from '@/hooks';

const contractsStore = useContractsStore();
const transactionsStore = useTransactionsStore();
const { setupStores } = useSetupStores();

const isResetStorageModalOpen = ref(false);
const isResetting = ref(false);

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
</script>

<template>
  <PageSection>
    <template #title>Storage</template>

    <Btn @click="isResetStorageModalOpen = true" :icon="ArchiveXIcon" secondary>
      Reset Storage
    </Btn>

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
  </PageSection>
</template>
