<script setup lang="ts">
import { useContractQueries } from '@/hooks';
import { computed, ref } from 'vue';
import PageSection from '@/components/Simulator/PageSection.vue';
import ContractMethodItem from '@/components/Simulator/ContractMethodItem.vue';
import EmptyListPlaceholder from '@/components/Simulator/EmptyListPlaceholder.vue';
import type { ContractSchema } from 'genlayer-js/types';
import type { ExecutionMode, ReadStateMode } from '@/types';

const props = defineProps<{
  executionMode: ExecutionMode;
}>();

const { contractAbiQuery } = useContractQueries();

const { data, isPending, isError, error, isRefetching } = contractAbiQuery;

const readMethods = computed(() => {
  const methods = (data.value as ContractSchema).methods;
  return Object.entries(methods).filter((x) => x[1].readonly);
});

const readStateMode = ref<ReadStateMode>('ACCEPTED');
</script>

<template>
  <PageSection data-testid="contract-read-methods">
    <template #title>
      <span class="flex items-center gap-2">
        Read Methods
        <Loader v-if="isRefetching" :size="14" />
      </span>
    </template>
    <template #actions>
      <div class="flex items-center gap-2 text-xs">
        <span class="text-gray-500 dark:text-gray-400">State:</span>
        <select
          v-model="readStateMode"
          class="w-24 rounded border border-gray-300 bg-white px-2 py-1 text-xs dark:border-gray-600 dark:bg-gray-700"
        >
          <option value="ACCEPTED">Accepted</option>
          <option value="FINALIZED">Finalized</option>
        </select>
      </div>
    </template>

    <ContentLoader v-if="isPending" />

    <Alert v-else-if="isError" error>
      {{ error?.message }}
    </Alert>

    <template v-else-if="data">
      <ContractMethodItem
        v-for="method in readMethods"
        :name="method[0]"
        :key="method[0]"
        :method="method[1]"
        methodType="read"
        :executionMode="props.executionMode"
        :readStateMode="readStateMode"
      />

      <EmptyListPlaceholder v-if="readMethods.length === 0">
        No read methods.
      </EmptyListPlaceholder>
    </template>
  </PageSection>
</template>
