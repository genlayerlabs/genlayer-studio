<script setup lang="ts">
import { useContractQueries } from '@/hooks';
import { ref, computed } from 'vue';
import PageSection from '@/components/Simulator/PageSection.vue';
import { ArrowUpTrayIcon } from '@heroicons/vue/16/solid';
import ContractParams from './ContractParams.vue';
import { type ArgData, unfoldArgsData } from './ContractParams';
import type { FeesDistribution } from '@/types';

const props = defineProps<{
  leaderOnly: boolean;
  feesDistribution?: FeesDistribution;
}>();

const { contract, contractSchemaQuery, deployContract, isDeploying } =
  useContractQueries();

const { data, isPending, isRefetching, isError } = contractSchemaQuery;

const calldataArguments = ref<ArgData>({ args: [], kwargs: {} });

const ctorMethod = computed(() => data.value.ctor);

const emit = defineEmits(['deployed-contract']);

const handleDeployContract = async () => {
  const args = calldataArguments.value;
  const newArgs = unfoldArgsData(args);
  await deployContract(newArgs, props.leaderOnly, props.feesDistribution);

  emit('deployed-contract');
};
</script>

<template>
  <PageSection>
    <template #title
      >Constructor Inputs
      <Loader v-if="isRefetching" :size="14" />
    </template>

    <ContentLoader v-if="isPending" />

    <Alert v-else-if="isError" error> Could not load contract schema. </Alert>

    <template v-else-if="data">
      <ContractParams
        :methodBase="ctorMethod"
        @argsChanged="
          (v: ArgData) => {
            calldataArguments = v;
          }
        "
      />

      <Btn
        testId="btn-deploy-contract"
        @click="handleDeployContract"
        :loading="isDeploying"
        :icon="ArrowUpTrayIcon"
      >
        <template v-if="isDeploying">Deploying...</template>
        <template v-else>Deploy {{ contract?.name }}</template>
      </Btn>
    </template>
  </PageSection>
</template>
