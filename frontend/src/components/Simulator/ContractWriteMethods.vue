<script setup lang="ts">
import { useContractQueries } from '@/hooks';
import { computed, ref } from 'vue';
import PageSection from '@/components/Simulator/PageSection.vue';
import ContractMethodItem from '@/components/Simulator/ContractMethodItem.vue';
import EmptyListPlaceholder from '@/components/Simulator/EmptyListPlaceholder.vue';
import type { ContractSchema } from 'genlayer-js/types';
import type { ExecutionMode } from '@/types';

const props = defineProps<{
  executionMode: ExecutionMode;
  consensusMaxRotations: number;
}>();

const { contractAbiQuery } = useContractQueries();

const { data, isPending, isError, error, isRefetching } = contractAbiQuery;

const writeMethods = computed(() => {
  const methods = (data.value as ContractSchema).methods;
  return Object.entries(methods).filter((x) => !x[1].readonly);
});

const simulationMode = ref(false);
</script>

<template>
  <PageSection>
    <template #title>
      <span class="flex items-center gap-2">
        Write Methods
        <Loader v-if="isRefetching" :size="14" />
      </span>
    </template>
    <template #actions>
      <div class="flex items-center gap-2 text-xs">
        <label class="flex cursor-pointer items-center gap-1">
          <input type="checkbox" v-model="simulationMode" class="rounded" />
          <span>Simulation Mode</span>
        </label>
      </div>
    </template>

    <ContentLoader v-if="isPending" />

    <Alert v-else-if="isError" error>
      {{ error?.message }}
    </Alert>

    <template v-else-if="data">
      <ContractMethodItem
        v-for="method in writeMethods"
        :name="method[0]"
        :key="method[0]"
        :method="method[1]"
        methodType="write"
        :executionMode="props.executionMode"
        :consensusMaxRotations="consensusMaxRotations"
        :simulationMode="simulationMode"
      />

      <EmptyListPlaceholder v-if="writeMethods.length === 0">
        No read methods.
      </EmptyListPlaceholder>
    </template>
  </PageSection>
</template>
