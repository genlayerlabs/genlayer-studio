<script setup lang="ts">
import { useNodeStore } from '@/stores';
import ValidatorItem from '@/components/simulator/ValidatorItem.vue';
import ValidatorModal from '@/components/simulator/ValidatorModal.vue';
import { ref } from 'vue';
import MainTitle from '@/components/simulator/MainTitle.vue';
import PageSection from '@/components/simulator/PageSection.vue';
import { PlusIcon } from '@heroicons/vue/16/solid';
import EmptyListPlaceholder from '@/components/simulator/EmptyListPlaceholder.vue';
import GhostBtn from '@/components/global/GhostBtn.vue';
import { useConfig } from '@/hooks';

const nodeStore = useNodeStore();
const isNewValidatorModalOpen = ref(false);
const { canUpdateValidators } = useConfig();
</script>

<template>
  <div class="flex max-h-[93vh] w-full flex-col overflow-y-auto">
    <MainTitle data-testid="validators-page-title">Your Validators</MainTitle>

    <PageSection id="tutorial-validators">
      <template #title>
        Validators
        <span class="opacity-50">{{ nodeStore.validators.length }}</span>
      </template>

      <template #actions>
        <GhostBtn
          v-if="canUpdateValidators"
          @click="isNewValidatorModalOpen = true"
          v-tooltip="'New Validator'"
          testId="create-new-validator-btn"
        >
          <PlusIcon class="h-4 w-4" />
        </GhostBtn>
      </template>

      <ContentLoader v-if="nodeStore.isLoadingValidatorData" />

      <EmptyListPlaceholder v-else-if="nodeStore.validators.length < 1">
        No validators.
      </EmptyListPlaceholder>

      <div
        class="overflow-hidden rounded-md border border-gray-300 dark:border-gray-800"
        v-if="nodeStore.validators.length > 0"
      >
        <div class="divide-y divide-gray-200 dark:divide-gray-800">
          <ValidatorItem
            v-for="validator in nodeStore.validatorsOrderedById"
            :key="validator.id"
            :validator="validator"
          />
        </div>
      </div>

      <Btn
        v-if="
          canUpdateValidators &&
          !nodeStore.hasAtLeastOneValidator &&
          !nodeStore.isLoadingValidatorData
        "
        @click="isNewValidatorModalOpen = true"
        :icon="PlusIcon"
      >
        New Validator
      </Btn>

      <ValidatorModal
        :open="isNewValidatorModalOpen"
        @close="isNewValidatorModalOpen = false"
      />
    </PageSection>
  </div>
</template>
