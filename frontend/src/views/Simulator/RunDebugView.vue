<script setup lang="ts">
import ConstructorParameters from '@/components/Simulator/ConstructorParameters.vue';
import ContractReadMethods from '@/components/Simulator/ContractReadMethods.vue';
import ContractWriteMethods from '@/components/Simulator/ContractWriteMethods.vue';
import TransactionsList from '@/components/Simulator/TransactionsList.vue';
import { useContractQueries, useConfig } from '@/hooks';
import MainTitle from '@/components/Simulator/MainTitle.vue';
import { ref, watch, computed } from 'vue';
import {
  useContractsStore,
  useNodeStore,
  useConsensusStore,
  useUIStore,
} from '@/stores';

import ContractInfo from '@/components/Simulator/ContractInfo.vue';
import BooleanField from '@/components/global/fields/BooleanField.vue';
import FieldError from '@/components/global/fields/FieldError.vue';
import NumberInput from '@/components/global/inputs/NumberInput.vue';

const uiStore = useUIStore();
const contractsStore = useContractsStore();
const { isDeployed, address, contract } = useContractQueries();
const nodeStore = useNodeStore();
const consensusStore = useConsensusStore();
const leaderOnly = ref(false);
const selectedImportedContract = ref<string | null>(contractsStore.selectedImportedContractId);

const isDeploymentOpen = ref(!isDeployed.value);
const finalityWindow = computed({
  get: () => consensusStore.finalityWindow,
  set: (newTime) => {
    if (isFinalityWindowValid(newTime)) {
      consensusStore.setFinalityWindowTime(newTime);
    }
  },
});
const isLoading = computed(() => consensusStore.isLoading);
const { canUpdateFinalityWindow } = useConfig();

// Hide constructors by default when contract is already deployed
const setConstructorVisibility = () => {
  isDeploymentOpen.value = !isDeployed.value;
};

watch(
  [() => contract.value?.id, () => isDeployed.value, () => address.value],
  setConstructorVisibility,
);

// Watch for changes in selected imported contract from store
watch(
  () => contractsStore.selectedImportedContractId,
  (newValue) => {
    selectedImportedContract.value = newValue;
  }
);

// Watch for changes in local selectedImportedContract and update store
watch(
  selectedImportedContract,
  (newValue) => {
    contractsStore.setSelectedImportedContract(newValue);
  }
);

function isFinalityWindowValid(value: number) {
  return Number.isInteger(value) && value >= 0;
}

const consensusMaxRotations = computed(() => consensusStore.maxRotations);

const currentImportedContract = computed(() => {
  if (!selectedImportedContract.value) return null;
  return contractsStore.importedContracts.find(
    (c) => c.id === selectedImportedContract.value
  );
});

const hasContractSelected = computed(() => {
  return (contractsStore.currentContract && contractsStore.currentContractId) || selectedImportedContract.value;
});
</script>

<template>
  <div class="flex max-h-[93vh] w-full flex-col overflow-y-auto">
    <MainTitle data-testid="run-debug-page-title">Run and Debug</MainTitle>

    <template v-if="hasContractSelected">
      <BooleanField
        v-model="leaderOnly"
        name="leaderOnly"
        label="Leader Only (Fast Execution)"
        class="p-2"
      />

      <div v-if="isLoading">Loading finality window...</div>
      <div v-else>
        <div v-if="canUpdateFinalityWindow" class="p-2">
          <div class="flex flex-wrap items-center gap-2">
            <label for="finalityWindow" class="text-xs"
              >Finality Window (seconds)</label
            >
            <NumberInput
              id="finalityWindow"
              name="finalityWindow"
              :min="0"
              :step="1"
              v-model.number="finalityWindow"
              required
              testId="input-finalityWindow"
              :disabled="false"
              class="w-20"
              tiny
            />
          </div>

          <FieldError v-if="!isFinalityWindowValid"
            >Please enter a positive integer.</FieldError
          >
        </div>
      </div>

      <ContractInfo
        v-if="!selectedImportedContract"
        :showNewDeploymentButton="!isDeploymentOpen"
        @openDeployment="isDeploymentOpen = true"
      />

      <div v-else class="bg-slate-100 px-2 py-2 dark:bg-zinc-700">
        <div class="text-sm font-semibold mb-1">Imported Contract</div>
        <div class="flex items-center gap-2 text-sm">
          <span>{{ currentImportedContract?.name }}</span>
          <span class="text-xs text-gray-500">
            {{ currentImportedContract?.address }}
          </span>
        </div>
      </div>

      <template v-if="nodeStore.hasAtLeastOneValidator || uiStore.showTutorial">
        <ConstructorParameters
          id="tutorial-how-to-deploy"
          v-if="isDeploymentOpen && !selectedImportedContract"
          @deployedContract="isDeploymentOpen = false"
          :leaderOnly="leaderOnly"
          :consensusMaxRotations="consensusMaxRotations"
        />

        <ContractReadMethods
          v-if="isDeployed || selectedImportedContract"
          id="tutorial-read-methods"
          :leaderOnly="leaderOnly"
        />
        <ContractWriteMethods
          v-if="isDeployed || selectedImportedContract"
          id="tutorial-write-methods"
          :leaderOnly="leaderOnly"
          :consensusMaxRotations="consensusMaxRotations"
        />
        <TransactionsList
          id="tutorial-tx-response"
          :finalityWindow="finalityWindow"
        />
      </template>
    </template>

    <div
      v-else
      class="flex w-full flex-col bg-slate-100 px-2 py-2 dark:dark:bg-zinc-700"
    >
      <div v-if="contractsStore.importedContracts.length > 0" class="mb-4">
        <label class="block text-sm font-medium mb-2">
          Select an imported contract to interact with:
        </label>
        <select
          v-model="selectedImportedContract"
          class="w-full rounded-md border-gray-300 dark:border-zinc-600 dark:bg-zinc-800 dark:text-white"
        >
          <option :value="null">-- Select a contract --</option>
          <option
            v-for="contract in contractsStore.importedContracts"
            :key="contract.id"
            :value="contract.id"
          >
            {{ contract.name }} ({{ contract.address.slice(0, 10) }}...)
          </option>
        </select>
      </div>
      <div class="text-sm">
        Please first select an intelligent contract in the
        <RouterLink
          :to="{ name: 'contracts' }"
          class="text-primary underline dark:text-white"
        >
          Files list.
        </RouterLink>
      </div>
    </div>
  </div>
</template>
