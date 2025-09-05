<script setup lang="ts">
import { ref, computed } from 'vue';
import Modal from '@/components/global/Modal.vue';
import { useContractImport } from '@/composables/useContractImport';

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  close: [];
}>();

const {
  importContract,
  isImporting,
  isValidAddress,
  isDuplicateContract,
} = useContractImport();

const contractAddress = ref('');
const contractName = ref('');

const isValidAddressComputed = computed(() => {
  return isValidAddress(contractAddress.value);
});

const isDuplicate = computed(() => {
  return isDuplicateContract(contractAddress.value);
});

const canImport = computed(() => {
  return isValidAddressComputed.value && !isDuplicate.value && !isImporting.value;
});

const resetForm = () => {
  contractAddress.value = '';
  contractName.value = '';
};

const handleImport = async () => {
  if (!canImport.value) return;

  const result = await importContract(contractAddress.value, contractName.value);
  
  if (result.success) {
    resetForm();
    emit('close');
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
            'border-red-500': contractAddress && !isValidAddressComputed,
            'border-yellow-500': isDuplicate,
          }"
        />
        <p
          v-if="contractAddress && !isValidAddressComputed"
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
