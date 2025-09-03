<script setup lang="ts">
import { useContractsStore } from '@/stores';
import { FilePlus2, Upload, FileInput } from 'lucide-vue-next';
import { XMarkIcon } from '@heroicons/vue/16/solid';
import { ref } from 'vue';
import { v4 as uuidv4 } from 'uuid';
import { useRouter } from 'vue-router';
import ContractItem from '@/components/Simulator/ContractItem.vue';
import MainTitle from '@/components/Simulator/MainTitle.vue';
import ImportContractModal from '@/components/contracts/ImportContractModal.vue';
import { useEventTracking } from '@/hooks';

const store = useContractsStore();
const router = useRouter();
const showNewFileInput = ref(false);
const showImportModal = ref(false);
const { trackEvent } = useEventTracking();

/**
 * Loads content from a file and adds it to the contract file store.
 *
 * @param {Event} event - The event triggered by the file input element.
 */
const loadContentFromFile = (event: Event) => {
  const target = event.target as HTMLInputElement;

  if (target.files && target.files.length > 0) {
    const [file] = target.files;
    const reader = new FileReader();

    reader.onload = (ev: ProgressEvent<FileReader>) => {
      if (ev.target?.result) {
        const id = uuidv4();
        store.addContractFile({
          id,
          name: file.name,
          content: (ev.target?.result as string) || '',
        });
        store.openFile(id);
      }
    };

    reader.readAsText(file);
  }
};

const handleAddNewFile = () => {
  if (!showNewFileInput.value) {
    showNewFileInput.value = true;
  }
};

const handleSaveNewFile = (name: string) => {
  if (name && name.replace('.py', '') !== '') {
    const id = uuidv4();
    store.addContractFile({ id, name, content: '' });
    store.openFile(id);

    trackEvent('created_contract', {
      contract_name: name,
    });
  }

  showNewFileInput.value = false;
};

const handleImportedContractClick = (contractId: string) => {
  console.log('Imported contract clicked:', contractId);
  // Clear any selected file contract
  store.setCurrentContractId('');
  // Set the selected imported contract
  store.setSelectedImportedContract(contractId);
  // Navigate to Run & Debug
  router.push({ name: 'run-debug' });
};
</script>

<template>
  <div class="flex w-full flex-col">
    <MainTitle data-testid="contracts-page-title">
      Your Contracts

      <template #actions>
        <GhostBtn @click="handleAddNewFile" v-tooltip="'New Contract'">
          <FilePlus2 :size="16" />
        </GhostBtn>

        <GhostBtn class="!p-0" v-tooltip="'Add From File'">
          <label class="input-label p-1">
            <input type="file" @change="loadContentFromFile" accept=".py" />
            <Upload :size="16" />
          </label>
        </GhostBtn>

        <GhostBtn @click="showImportModal = true" v-tooltip="'Import Contract'">
          <FileInput :size="16" />
        </GhostBtn>
      </template>
    </MainTitle>

    <div id="tutorial-how-to-change-example">
      <ContractItem
        @click="store.openFile(contract.id)"
        v-for="contract in store.contractsOrderedByName"
        :key="contract.id"
        :contract="contract"
        :isActive="contract.id === store.currentContractId"
      />
    </div>

    <div v-if="store.importedContracts.length > 0" class="mt-6">
      <div class="mb-2 text-sm font-semibold text-gray-600 dark:text-gray-400">
        Imported Contracts
      </div>
      <div
        v-for="contract in store.importedContracts"
        :key="contract.id"
        @click="handleImportedContractClick(contract.id)"
        class="flex items-center justify-between rounded-md px-3 py-2 hover:bg-gray-100 dark:hover:bg-zinc-700 cursor-pointer"
      >
        <div class="flex items-center gap-2 flex-1 pointer-events-none">
          <FileInput :size="14" class="text-blue-500" />
          <div>
            <div class="text-sm font-medium">{{ contract.name }}</div>
            <div class="text-xs text-gray-500 dark:text-gray-400">
              {{ contract.address.slice(0, 10) }}...{{ contract.address.slice(-8) }}
            </div>
          </div>
        </div>
        <button
          @click.stop="store.removeImportedContract(contract.id)"
          class="text-gray-400 hover:text-red-500 pointer-events-auto"
          v-tooltip="'Remove'"
        >
          <XMarkIcon class="h-4 w-4" />
        </button>
      </div>
    </div>

    <ContractItem
      v-if="showNewFileInput"
      :isNewFile="true"
      @save="handleSaveNewFile"
      @cancel="showNewFileInput = false"
    />

    <ImportContractModal
      :open="showImportModal"
      @close="showImportModal = false"
    />
  </div>
</template>

<style scoped>
.input-label {
  cursor: pointer;
  position: relative;
  overflow: hidden;
}

.input-label input {
  position: absolute;
  top: 0;
  left: 0;
  z-index: -1;
  opacity: 0;
}
</style>
