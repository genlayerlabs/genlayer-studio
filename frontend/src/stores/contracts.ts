import type { ContractFile, DeployedContract } from '@/types';
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { notify } from '@kyvg/vue3-notification';
import { useDb, useFileName } from '@/hooks';
import { useNetworkStore } from '@/stores/network';

export const useContractsStore = defineStore('contractsStore', () => {
  const contracts = ref<ContractFile[]>([]);
  const openedFiles = ref<string[]>([]);
  const db = useDb();
  const { cleanupFileName } = useFileName();
  const networkStore = useNetworkStore();

  const currentContractId = ref<string | undefined>(
    localStorage.getItem('contractsStore.currentContractId') || '',
  );
  // Internal flat list across all chains. UI consumers read the filtered
  // `deployedContracts` computed below.
  const allDeployedContracts = ref<DeployedContract[]>([]);
  const deployedContracts = computed<DeployedContract[]>(() =>
    allDeployedContracts.value.filter(
      (c) => c.chainId === undefined || c.chainId === networkStore.chainId,
    ),
  );

  function setAllDeployedContracts(items: DeployedContract[]) {
    allDeployedContracts.value = items;
  }

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
    allDeployedContracts.value = allDeployedContracts.value.filter(
      (c) => c.contractId !== id,
    );
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
    if (!file) return;
    files.splice(oldIndex, 1);
    files.splice(newIndex, 0, file);
    openedFiles.value = [...files];
  }

  function addDeployedContract({
    contractId,
    address,
    defaultState,
    chainId,
  }: DeployedContract): void {
    const effectiveChainId = chainId ?? networkStore.chainId;
    // Dedupe by (chainId, contractId): a contract deployed on localnet and
    // then again on Bradbury should produce two distinct records.
    const index = allDeployedContracts.value.findIndex(
      (c) =>
        c.contractId === contractId &&
        (c.chainId ?? networkStore.chainId) === effectiveChainId,
    );

    const newItem = {
      contractId,
      address,
      defaultState,
      chainId: effectiveChainId,
    };

    if (index === -1) {
      allDeployedContracts.value.push(newItem);
    } else {
      allDeployedContracts.value.splice(index, 1, newItem);
    }

    notify({
      title: 'Contract deployed',
      type: 'success',
    });
  }

  function removeDeployedContract(contractId: string): void {
    // Only remove the record for the current chain; other chains keep theirs.
    allDeployedContracts.value = allDeployedContracts.value.filter(
      (c) =>
        !(
          c.contractId === contractId &&
          (c.chainId ?? networkStore.chainId) === networkStore.chainId
        ),
    );
  }

  function setCurrentContractId(id?: string) {
    currentContractId.value = id || '';
  }

  async function resetStorage(): Promise<void> {
    contracts.value = [];
    openedFiles.value = [];
    currentContractId.value = '';
    allDeployedContracts.value = [];

    await db.deployedContracts.clear();
    await db.contractFiles.clear();
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

  return {
    // state
    contracts,
    openedFiles,
    currentContractId,
    deployedContracts,
    allDeployedContracts,

    //getters
    currentContract,
    contractsOrderedByName,
    openedContracts,

    //actions
    addContractFile,
    removeContractFile,
    updateContractFile,
    openFile,
    closeFile,
    addDeployedContract,
    removeDeployedContract,
    setCurrentContractId,
    setAllDeployedContracts,
    resetStorage,
    getInitialOpenedFiles,
    moveOpenedFile,
  };
});
