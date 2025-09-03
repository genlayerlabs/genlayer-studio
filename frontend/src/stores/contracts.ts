import type { ContractFile, DeployedContract, ImportedContract } from '@/types';
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { notify } from '@kyvg/vue3-notification';
import { useDb, useFileName } from '@/hooks';

export const useContractsStore = defineStore('contractsStore', () => {
  const contracts = ref<ContractFile[]>([]);
  const openedFiles = ref<string[]>([]);
  const db = useDb();
  const { cleanupFileName } = useFileName();

  const currentContractId = ref<string | undefined>(
    localStorage.getItem('contractsStore.currentContractId') || '',
  );
  const deployedContracts = ref<DeployedContract[]>([]);
  const importedContracts = ref<ImportedContract[]>([]);
  const selectedImportedContractId = ref<string | null>(null);

  function getInitialOpenedFiles() {
    const storage = localStorage.getItem('contractsStore.openedFiles');

    if (storage) {
      openedFiles.value = storage.split(',');
      openedFiles.value = openedFiles.value.filter((id) =>
        contracts.value.find((c) => c.id === id),
      );
    } else {
      return [];
    }
  }

  function addContractFile(
    contract: ContractFile,
    atBeginning?: boolean,
  ): void {
    const name = cleanupFileName(contract.name);

    if (atBeginning) {
      contracts.value.unshift({ ...contract, name });
    } else {
      contracts.value.push({ ...contract, name });
    }
  }

  function removeContractFile(id: string): void {
    contracts.value = [...contracts.value.filter((c) => c.id !== id)];
    deployedContracts.value = [
      ...deployedContracts.value.filter((c) => c.contractId !== id),
    ];
    openedFiles.value = openedFiles.value.filter(
      (contractId) => contractId !== id,
    );

    if (currentContractId.value === id) {
      setCurrentContractId('');
    }
  }

  function updateContractFile(
    id: string,
    {
      name,
      content,
      updatedAt,
    }: { name?: string; content?: string; updatedAt?: string },
  ) {
    contracts.value = [
      ...contracts.value.map((c) => {
        if (c.id === id) {
          const _name = cleanupFileName(name || c.name);
          const _content = content || c.content;
          return { ...c, name: _name, content: _content, updatedAt };
        }
        return c;
      }),
    ];
  }

  function openFile(id: string) {
    const index = contracts.value.findIndex((c) => c.id === id);
    const openedIndex = openedFiles.value.findIndex((c) => c === id);

    if (index > -1 && openedIndex === -1) {
      openedFiles.value = [...openedFiles.value, id];
    }
    currentContractId.value = id;
    // Clear selected imported contract when opening a file
    selectedImportedContractId.value = null;
  }

  function closeFile(id: string) {
    openedFiles.value = [...openedFiles.value.filter((c) => c !== id)];
    if (openedFiles.value.length > 0) {
      currentContractId.value = openedFiles.value[openedFiles.value.length - 1];
    } else {
      currentContractId.value = '';
    }
  }

  function moveOpenedFile(oldIndex: number, newIndex: number) {
    const files = openedFiles.value;
    const file = files[oldIndex];
    files.splice(oldIndex, 1);
    files.splice(newIndex, 0, file);
    openedFiles.value = [...files];
  }

  function addDeployedContract({
    contractId,
    address,
    defaultState,
  }: DeployedContract): void {
    const index = deployedContracts.value.findIndex(
      (c) => c.contractId === contractId,
    );

    const newItem = { contractId, address, defaultState };

    if (index === -1) {
      deployedContracts.value.push(newItem);
    } else {
      deployedContracts.value.splice(index, 1, newItem);
    }

    notify({
      title: 'Contract deployed',
      type: 'success',
    });
  }

  function removeDeployedContract(contractId: string): void {
    deployedContracts.value = [
      ...deployedContracts.value.filter((c) => c.contractId !== contractId),
    ];
  }

  function setCurrentContractId(id?: string) {
    currentContractId.value = id || '';
  }

  function setSelectedImportedContract(id: string | null) {
    selectedImportedContractId.value = id;
  }

  function addImportedContract(contract: ImportedContract): void {
    const existingIndex = importedContracts.value.findIndex(
      (c) => c.address === contract.address,
    );

    if (existingIndex === -1) {
      importedContracts.value.push(contract);
      notify({
        title: 'Contract imported successfully',
        type: 'success',
      });
    } else {
      notify({
        title: 'Contract already imported',
        type: 'warning',
      });
    }
  }

  function removeImportedContract(id: string): void {
    importedContracts.value = importedContracts.value.filter((c) => c.id !== id);
  }

  function getContractByAddress(
    address: string,
  ): ImportedContract | DeployedContract | undefined {
    const imported = importedContracts.value.find((c) => c.address === address);
    if (imported) return imported;

    const deployed = deployedContracts.value.find((c) => c.address === address);
    return deployed;
  }

  async function resetStorage(): Promise<void> {
    contracts.value = [];
    openedFiles.value = [];
    currentContractId.value = '';
    importedContracts.value = [];

    await db.deployedContracts.clear();
    await db.contractFiles.clear();
    await db.importedContracts.clear();
  }

  const currentContract = computed(() => {
    return contracts.value.find((c) => c.id === currentContractId.value);
  });

  const contractsOrderedByName = computed(() => {
    return contracts.value.slice().sort((a, b) => a.name.localeCompare(b.name));
  });

  const openedContracts = computed(() => {
    return openedFiles.value.flatMap((contractId) => {
      const contract = contracts.value.find(
        (contract) => contract.id === contractId,
      );
      if (contract) {
        return [contract];
      } else {
        return [];
      }
    });
  });

  const allAvailableContracts = computed(() => {
    const deployed = deployedContracts.value.map((c) => ({
      ...c,
      type: 'deployed' as const,
      name: contracts.value.find((cf) => cf.id === c.contractId)?.name || 'Unknown',
    }));
    const imported = importedContracts.value.map((c) => ({
      ...c,
      type: 'imported' as const,
    }));
    return [...deployed, ...imported];
  });

  return {
    // state
    contracts,
    openedFiles,
    currentContractId,
    deployedContracts,
    importedContracts,
    selectedImportedContractId,

    //getters
    currentContract,
    contractsOrderedByName,
    openedContracts,
    allAvailableContracts,

    //actions
    addContractFile,
    removeContractFile,
    updateContractFile,
    openFile,
    closeFile,
    addDeployedContract,
    removeDeployedContract,
    addImportedContract,
    removeImportedContract,
    getContractByAddress,
    setCurrentContractId,
    setSelectedImportedContract,
    resetStorage,
    getInitialOpenedFiles,
    moveOpenedFile,
  };
});
