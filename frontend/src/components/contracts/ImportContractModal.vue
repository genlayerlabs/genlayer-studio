<script setup lang="ts">
import { ref, computed } from 'vue';
import Modal from '@/components/global/Modal.vue';
import { useContractsStore } from '@/stores';
import { v4 as uuidv4 } from 'uuid';
import { notify } from '@kyvg/vue3-notification';
import { ContractService } from '@/services/ContractService';
import { useContractQueries } from '@/hooks';

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  close: [];
}>();

const contractsStore = useContractsStore();
const { fetchContractCode } = useContractQueries();

const contractAddress = ref('');
const contractName = ref('');
const isImporting = ref(false);

const isValidAddress = computed(() => {
  return /^0x[a-fA-F0-9]{40}$/.test(contractAddress.value);
});

const isDuplicate = computed(() => {
  return contractsStore.deployedContracts.some(
    (c) => c.address.toLowerCase() === contractAddress.value.toLowerCase(),
  );
});

const canImport = computed(() => {
  return isValidAddress.value && !isDuplicate.value && !isImporting.value;
});

const resetForm = () => {
  contractAddress.value = '';
  contractName.value = '';
  isImporting.value = false;
};

const handleImport = async () => {
  if (!canImport.value) return;

  isImporting.value = true;

  try {
    // Create a unique ID for this contract
    const contractId = uuidv4();
    const fileName =
      contractName.value || `imported_${contractAddress.value.slice(0, 10)}.py`;

    let contractCode = '';

    try {
      // Try to fetch the actual contract code
      contractCode = await fetchContractCode(contractAddress.value);

      notify({
        title: 'Contract code retrieved',
        text: 'Successfully fetched the contract code',
        type: 'success',
      });
    } catch (codeError) {
      console.warn('Failed to fetch contract code:', codeError);

      // Fallback to empty file if we can't get the actual code
      contractCode = '# ERROR: Could not retrieve contract code';

      notify({
        title: 'Could not retrieve contract code',
        text: 'The contract will be added without source code',
        type: 'error',
      });
    }

    // Add the contract file
    contractsStore.addContractFile({
      id: contractId,
      name: fileName.endsWith('.py') ? fileName : `${fileName}.py`,
      content: contractCode,
    });

    // Add the deployed contract entry
    contractsStore.addDeployedContract({
      contractId: contractId,
      address: contractAddress.value as `0x${string}`,
      defaultState: '{}',
    });

    // Open the file in the editor
    contractsStore.openFile(contractId);

    resetForm();
    emit('close');

    notify({
      title: 'Contract imported successfully',
      text: `Contract imported as ${fileName}`,
      type: 'success',
    });
  } catch (error) {
    notify({
      title: 'Failed to import contract',
      text: error instanceof Error ? error.message : 'Unknown error',
      type: 'error',
    });
  } finally {
    isImporting.value = false;
  }
};

const handleClose = () => {
  resetForm();
  emit('close');
};
</script>

<template>
  <Modal :open="open" @close="handleClose" :wide="true">
    <template #title>Import Contract</template>
    <template #description>
      Import an existing deployed contract by its address
    </template>

    <div class="flex flex-col gap-4">
      <div>
        <label
          for="contract-address"
          class="block text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          Contract Address *
        </label>
        <input
          id="contract-address"
          v-model="contractAddress"
          type="text"
          placeholder="0x..."
          class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm dark:border-zinc-600 dark:bg-zinc-800 dark:text-white"
          :class="{
            'border-red-500': contractAddress && !isValidAddress,
            'border-yellow-500': isDuplicate,
          }"
        />
        <p
          v-if="contractAddress && !isValidAddress"
          class="mt-1 text-sm text-red-500"
        >
          Invalid address format
        </p>
        <p v-if="isDuplicate" class="mt-1 text-sm text-yellow-500">
          This contract has already been imported
        </p>
      </div>

      <div>
        <label
          for="contract-name"
          class="block text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          Contract Name (optional)
        </label>
        <input
          id="contract-name"
          v-model="contractName"
          type="text"
          placeholder="My Contract"
          class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm dark:border-zinc-600 dark:bg-zinc-800 dark:text-white"
        />
        <p class="mt-1 text-sm text-gray-500">
          Leave empty to auto-name as imported_&lt;address_prefix&gt;.py
        </p>
      </div>

      <div class="mt-4 flex justify-end gap-2">
        <button
          @click="handleClose"
          type="button"
          class="inline-flex justify-center rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:border-zinc-600 dark:bg-zinc-800 dark:text-white dark:hover:bg-zinc-700"
        >
          Cancel
        </button>
        <button
          @click="handleImport"
          type="button"
          :disabled="!canImport"
          class="inline-flex justify-center rounded-md border border-transparent bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {{ isImporting ? 'Importing...' : 'Import' }}
        </button>
      </div>
    </div>
  </Modal>
</template>
