<script setup lang="ts">
import { useNodeStore } from '@/stores';
import { ref, computed } from 'vue';
import PageSection from '@/components/simulator/PageSection.vue';
import { PlusIcon } from '@heroicons/vue/16/solid';
import { uniqBy } from 'lodash-es';
import GhostBtn from '@/components/global/GhostBtn.vue';
import ProviderItem from '@/components/simulator/ProviderItem.vue';
import ProviderModal from '@/components/simulator/ProviderModal.vue';
import { DatabaseBackup } from 'lucide-vue-next';
import { notify } from '@kyvg/vue3-notification';
import EmptyListPlaceholder from '@/components/simulator/EmptyListPlaceholder.vue';

const nodeStore = useNodeStore();
const isNewProviderModalOpen = ref(false);
const isResetProvidersModalOpen = ref(false);
const isResetting = ref(false);

const modelGroups = computed(() => {
  return uniqBy(nodeStore.nodeProviders, 'provider')
    .sort((a, b) => a.provider.localeCompare(b.provider))
    .map((provider: any) => ({
      provider: provider.provider,
      models: nodeStore.nodeProviders.filter(
        (p) => p.provider === provider.provider,
      ),
    }));
});

const handleResetProviders = async () => {
  isResetting.value = true;

  try {
    await nodeStore.resetProviders();

    notify({
      title: 'Successfully reset providers',
      type: 'success',
    });
  } catch (error) {
    console.error(error);
    notify({
      title: 'Could not reset providers',
      type: 'error',
    });
  } finally {
    isResetting.value = false;
    isResetProvidersModalOpen.value = false;
  }
};
</script>

<template>
  <PageSection>
    <template #title
      >Provider Presets
      <MoreInfo
        text="You can add more providers here, then create new validators using those presets."
      />
    </template>

    <template #actions>
      <GhostBtn
        @click="isNewProviderModalOpen = true"
        v-tooltip="'New Preset'"
        testId="create-new-provider-btn"
      >
        <PlusIcon class="h-4 w-4" />
      </GhostBtn>
    </template>

    <ContentLoader
      v-if="nodeStore.isLoadingProviders && nodeStore.nodeProviders.length < 1"
    />

    <EmptyListPlaceholder v-else-if="nodeStore.nodeProviders.length < 1">
      No providers.
    </EmptyListPlaceholder>

    <div v-for="group in modelGroups" :key="group.provider">
      <div class="mb-1 text-xs font-semibold opacity-50">
        {{ group.provider }}
      </div>

      <div
        class="overflow-hidden rounded-md border border-gray-300 dark:border-gray-800"
      >
        <div class="divide-y divide-gray-200 dark:divide-gray-800"></div>
        <ProviderItem
          v-for="model in group.models"
          :key="model.id"
          :provider="model"
        />
      </div>
    </div>

    <ProviderModal
      :open="isNewProviderModalOpen"
      @close="isNewProviderModalOpen = false"
    />

    <Btn
      @click="isResetProvidersModalOpen = true"
      :icon="DatabaseBackup"
      secondary
    >
      Reset Providers
    </Btn>

    <ConfirmationModal
      :open="isResetProvidersModalOpen"
      @confirm="handleResetProviders"
      @close="isResetProvidersModalOpen = false"
      buttonText="Reset Providers"
      buttonTestId="btn-reset-providers"
      :dangerous="true"
      :confirming="isResetting"
    >
      <template #title>Reset Provider Configs</template>
      <template #description
        >Are you sure? All providers and models will be reset to the default
        values.</template
      >
    </ConfirmationModal>
  </PageSection>
</template>
