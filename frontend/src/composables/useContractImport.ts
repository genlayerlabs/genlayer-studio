import { ref } from 'vue';
import { useContractsStore } from '@/stores';
import { v4 as uuidv4 } from 'uuid';
import { notify } from '@kyvg/vue3-notification';
import { useContractQueries } from '@/hooks';

export function useContractImport() {
  const contractsStore = useContractsStore();
  const { fetchContractCode } = useContractQueries();
  const isImporting = ref(false);

  const isValidAddress = (address: string): boolean => {
    return /^0x[a-fA-F0-9]{40}$/.test(address);
  };

  const isDuplicateContract = (address: string): boolean => {
    return contractsStore.deployedContracts.some(
      (c) => c.address.toLowerCase() === address.toLowerCase(),
    );
  };

  const importContract = async (
    contractAddress: string,
    contractName?: string,
  ): Promise<{ success: boolean; message: string; contractId?: string }> => {
    if (!isValidAddress(contractAddress)) {
      return {
        success: false,
        message: 'Invalid address format',
      };
    }

    if (isDuplicateContract(contractAddress)) {
      return {
        success: false,
        message: 'This contract has already been imported',
      };
    }

    isImporting.value = true;

    try {
      const contractId = uuidv4();
      const fileName =
        contractName || `imported_${contractAddress.slice(0, 10)}.py`;

      let contractCode = '';

      try {
        contractCode = await fetchContractCode(contractAddress);
      } catch (codeError) {
        console.warn('Failed to fetch contract code:', codeError);

        notify({
          title: 'Could not retrieve contract code',
          text: 'Review the contract address and try again',
          type: 'error',
        });

        return {
          success: false,
          message: 'Could not retrieve contract code',
        };
      }

      contractsStore.addContractFile({
        id: contractId,
        name: fileName.endsWith('.py') ? fileName : `${fileName}.py`,
        content: contractCode,
      });

      contractsStore.addDeployedContract({
        contractId: contractId,
        address: contractAddress as `0x${string}`,
        defaultState: '{}',
      });

      contractsStore.openFile(contractId);

      notify({
        title: 'Contract imported successfully',
        text: `Contract imported as ${fileName}`,
        type: 'success',
      });

      return {
        success: true,
        message: `Contract imported as ${fileName}`,
        contractId,
      };
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : 'Unknown error';

      notify({
        title: 'Failed to import contract',
        text: errorMessage,
        type: 'error',
      });

      return {
        success: false,
        message: errorMessage,
      };
    } finally {
      isImporting.value = false;
    }
  };

  return {
    importContract,
    isImporting,
    isValidAddress,
    isDuplicateContract,
  };
}
