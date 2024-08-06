import type { ContractFile, DeployedContract } from '@/types';
import { db, getContractFileName, setupStores } from '@/utils';
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { notify } from '@kyvg/vue3-notification';

const getInitialOPenedFiles = (): string[] => {
  const storage = localStorage.getItem('contractsStore.openedFiles');
  if (storage) return storage.split(',');
  return [];
};

export const useContractsStore = defineStore('contractsStore', () => {
  const contracts = ref<ContractFile[]>([]);
  const openedFiles = ref<string[]>(getInitialOPenedFiles());

  const currentContractId = ref<string | undefined>(
    localStorage.getItem('contractsStore.currentContractId') || '',
  );
  const deployedContracts = ref<DeployedContract[]>([]);

  const currentConstructorInputs = ref<{ [k: string]: string }>({});
  const currentErrorConstructorInputs = ref<Error>();
  const currentDeployedContractAbi = ref<any>();

  const loadingConstructorInputs = ref(false);
  const deployingContract = ref(false);

  function addContractFile(contract: ContractFile): void {
    const name = getContractFileName(contract.name);
    contracts.value.push({ ...contract, name });
  }

  function removeContractFile(id: string): void {
    contracts.value = [...contracts.value.filter((c) => c.id !== id)];
    deployedContracts.value = [
      ...deployedContracts.value.filter((c) => c.contractId !== id),
    ];

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
          const _name = getContractFileName(name || c.name);
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
  }

  function closeFile(id: string) {
    openedFiles.value = [...openedFiles.value.filter((c) => c !== id)];
    if (openedFiles.value.length > 0) {
      currentContractId.value = openedFiles.value[openedFiles.value.length - 1];
    } else {
      currentContractId.value = '';
    }
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
    if (index === -1) deployedContracts.value.push(newItem);
    else deployedContracts.value.splice(index, 1, newItem);

    notify({
      title: 'Contract deployed',
      text: `to ${address}`,
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

  async function resetStorage(): Promise<void> {
    try {
      const idsToDelete = contracts.value
        .filter((c) => c.example || (!c.example && !c.updatedAt))
        .map((c) => c.id);

      await db.deployedContracts
        .where('contractId')
        .anyOf(idsToDelete)
        .delete();
      await db.contractFiles.where('id').anyOf(idsToDelete).delete();

      deployedContracts.value = [
        ...deployedContracts.value.filter(
          (c) => !idsToDelete.includes(c.contractId),
        ),
      ];
      contracts.value = [
        ...contracts.value.filter((c) => !idsToDelete.includes(c.id)),
      ];
      openedFiles.value = [
        ...openedFiles.value.filter((c) => !idsToDelete.includes(c)),
      ];
      if (
        currentContractId.value &&
        idsToDelete.includes(currentContractId.value)
      ) {
        currentContractId.value = '';
      }

      localStorage.setItem(
        'mainStore.currentContractId',
        currentContractId.value || '',
      );
      localStorage.setItem(
        'mainStore.openedFiles',
        openedFiles.value.join(','),
      );

      await setupStores();
    } catch (error) {
      console.error(error);
    }
  }

  const currentContract = computed(() => {
    return contracts.value.find((c) => c.id === currentContractId.value);
  });

  const contractsOrderedByName = computed(() => {
    return contracts.value.slice().sort((a, b) => a.name.localeCompare(b.name));
  });
  const contractsOrderedByName = computed(() => {
    return contracts.value.slice().sort((a, b) => a.name.localeCompare(b.name));
  });

  return {
    // state
    contracts,
    openedFiles,
    currentContractId,
    deployedContracts,
    currentConstructorInputs,
    currentErrorConstructorInputs,
    currentDeployedContractAbi,
    loadingConstructorInputs,
    deployingContract,

    //getters
    currentContract,
    contractsOrderedByName,

    //actions
    addContractFile,
    removeContractFile,
    updateContractFile,
    openFile,
    closeFile,
    addDeployedContract,
    removeDeployedContract,
    setCurrentContractId,
    resetStorage,
  };
});
