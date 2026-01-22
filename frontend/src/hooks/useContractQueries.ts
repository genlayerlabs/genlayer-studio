import { watch, ref, computed } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import type { TransactionItem } from '@/types';
import {
  useContractsStore,
  useTransactionsStore,
  useAccountsStore,
} from '@/stores';
import { useDebounceFn } from '@vueuse/core';
import { notify } from '@kyvg/vue3-notification';
import { useMockContractData } from './useMockContractData';
import { useEventTracking, useGenlayer } from '@/hooks';
import type {
  Address,
  TransactionHash,
  CalldataEncodable,
  TransactionHashVariant,
} from 'genlayer-js/types';
import { TransactionStatus } from 'genlayer-js/types';

const schema = ref<any>();

export function useContractQueries() {
  const genlayer = useGenlayer();
  const genlayerClient = computed(() => genlayer.client.value);
  const accountsStore = useAccountsStore();
  const transactionsStore = useTransactionsStore();
  const contractsStore = useContractsStore();
  const queryClient = useQueryClient();
  const { trackEvent } = useEventTracking();
  const contract = computed(() => contractsStore.currentContract);

  const { mockContractId, mockContractSchema } = useMockContractData();

  const isMock = computed(() => contract.value?.id === mockContractId);

  const deployedContract = computed(() =>
    contractsStore.deployedContracts.find(
      ({ contractId }) => contractId === contract.value?.id,
    ),
  );

  const isDeployed = computed(() => !!deployedContract.value);
  const address = computed(() => deployedContract.value?.address);

  const fetchContractSchemaDebounced = useDebounceFn(() => {
    return fetchContractSchema();
  }, 300);

  watch(
    () => contract.value?.content,
    () => {
      queryClient.invalidateQueries({
        queryKey: ['schema', contract.value?.id],
      });
    },
  );

  const contractSchemaQuery = useQuery({
    queryKey: ['schema', () => contract.value?.id],
    queryFn: fetchContractSchemaDebounced,
    refetchOnWindowFocus: false,
    retry: 0,
    enabled: !!contract.value?.id,
  });

  async function fetchContractSchema() {
    if (isMock.value) {
      return mockContractSchema;
    }

    try {
      const result = await genlayerClient.value?.getContractSchemaForCode(
        contract.value?.content ?? '',
      );

      schema.value = result;
      return schema.value;
    } catch (error: any) {
      throw new Error(error.details);
    }
  }

  const isDeploying = ref(false);

  async function deployContract(
    args: {
      args: CalldataEncodable[];
      kwargs: { [key: string]: CalldataEncodable };
    },
    leaderOnly: boolean,
    consensusMaxRotations: number,
  ) {
    isDeploying.value = true;

    try {
      if (!contract.value || !accountsStore.selectedAccount) {
        throw new Error('Error Deploying the contract');
      }

      const code = contract.value?.content ?? '';
      const code_bytes = new TextEncoder().encode(code);

      const result = await genlayerClient.value?.deployContract({
        code: code_bytes as any as string, // FIXME: code should accept both bytes and string in genlayer-js
        args: args.args,
        leaderOnly,
        consensusMaxRotations,
      });

      const tx: TransactionItem = {
        contractAddress: '',
        localContractId: contract.value?.id ?? '',
        hash: result as TransactionHash,
        type: 'deploy',
        statusName: TransactionStatus.PENDING,
        data: {},
      };

      notify({
        title: 'Started deploying contract',
        type: 'success',
      });

      trackEvent('deployed_contract', {
        contract_name: contract.value?.name || '',
      });

      await transactionsStore.clearTransactionsForContract(
        contract.value?.id ?? '',
      ); // await this to avoid race condition causing the added transaction below to be erased
      transactionsStore.addTransaction(tx);
      contractsStore.removeDeployedContract(contract.value?.id ?? '');
      return tx;
    } catch (error) {
      isDeploying.value = false;
      notify({
        type: 'error',
        title: 'Error deploying contract',
      });
      console.error('Error Deploying the contract', error);
      throw new Error('Error Deploying the contract');
    }
  }

  const abiQueryEnabled = computed(
    () => !!contract.value && !!isDeployed.value,
  );

  const contractAbiQuery = useQuery({
    queryKey: [
      'abi',
      () => contract.value?.id,
      () => deployedContract.value?.address,
    ],
    queryFn: fetchContractAbi,
    enabled: abiQueryEnabled,
    refetchOnWindowFocus: false,
    retry: 2,
  });

  async function fetchContractAbi() {
    if (isMock.value) {
      return mockContractSchema;
    }

    const result = await genlayerClient.value?.getContractSchema(
      deployedContract.value?.address ?? '0x0',
    );

    return result;
  }

  async function callReadMethod(
    method: string,
    args: {
      args: CalldataEncodable[];
      kwargs: { [key: string]: CalldataEncodable };
    },
    transactionHashVariant: TransactionHashVariant,
  ) {
    try {
      const result = await genlayerClient.value?.readContract({
        address: address.value as Address,
        functionName: method,
        args: args.args,
        transactionHashVariant,
      });

      return result;
    } catch (error) {
      console.error(error);
      throw new Error('Error getting the contract state');
    }
  }

  async function callWriteMethod({
    method,
    args,
    leaderOnly,
    consensusMaxRotations,
  }: {
    method: string;
    args: {
      args: CalldataEncodable[];
      kwargs: { [key: string]: CalldataEncodable };
    };
    leaderOnly: boolean;
    consensusMaxRotations?: number;
  }) {
    try {
      if (!accountsStore.selectedAccount) {
        throw new Error('Error writing to contract');
      }

      const result = await genlayerClient.value?.writeContract({
        address: address.value as Address,
        functionName: method,
        args: args.args,
        value: BigInt(0),
        leaderOnly,
        consensusMaxRotations,
      });

      transactionsStore.addTransaction({
        contractAddress: address.value || '',
        localContractId: contract.value?.id || '',
        hash: result as TransactionHash,
        type: 'method',
        statusName: TransactionStatus.PENDING,
        data: {},
        decodedData: {
          functionName: method,
          ...args,
        },
      });
      return true;
    } catch (error) {
      console.error(error);
      throw new Error('Error writing to contract');
    }
  }

  async function simulateWriteMethod({
    method,
    args,
    consensusMaxRotations,
  }: {
    method: string;
    args: {
      args: CalldataEncodable[];
      kwargs: { [key: string]: CalldataEncodable };
    };
    leaderOnly: boolean;
    consensusMaxRotations?: number;
  }) {
    try {
      const result = await genlayerClient.value?.simulateWriteContract({
        address: address.value as Address,
        functionName: method,
        args: args.args,
      });

      return result;
    } catch (error) {
      console.error(error);
      throw new Error('Error simulating write method');
    }
  }

  async function fetchContractCode(contractAddress: string): Promise<string> {
    try {
      if (!genlayerClient.value) {
        throw new Error('Genlayer client not initialized');
      }

      const code = await genlayerClient.value.getContractCode(
        contractAddress as Address,
      );

      if (!code || !code.trim()) {
        throw new Error('Contract code not found');
      }

      return code;
    } catch (error) {
      console.error('Error fetching contract code:', error);
      throw error;
    }
  }

  const isUpgrading = ref(false);

  async function upgradeContract() {
    if (!contract.value || !deployedContract.value) {
      notify({
        type: 'error',
        title: 'Cannot upgrade: contract not deployed',
      });
      return;
    }

    const contractCode = contract.value.content;
    const contractAddress = deployedContract.value.address;

    if (!contractCode) {
      notify({
        type: 'error',
        title: 'No contract code to upgrade',
      });
      return;
    }

    isUpgrading.value = true;

    try {
      // Sign the upgrade request
      // Message: keccak256(contract_address + nonce_bytes32 + keccak256(new_code))
      // Including nonce prevents replay attacks (nonce increments after each tx)
      const { keccak256, toBytes, concat, toHex, stringToBytes, pad } =
        await import('viem');
      const { privateKeyToAccount } = await import('viem/accounts');

      // Fetch contract nonce to include in signature
      const { useRpcClient } = await import('@/hooks/useRpcClient');
      const rpcClient = useRpcClient();
      const nonce = await rpcClient.getContractNonce(contractAddress);

      const account = accountsStore.selectedAccount;
      let signature: string | undefined;

      if (account) {
        // Create message hash matching backend
        // nonce as 32-byte big-endian
        const nonceBytes = pad(toHex(nonce), { size: 32 });
        const newCodeHash = keccak256(stringToBytes(contractCode));
        const messageHash = keccak256(
          concat([
            toBytes(contractAddress as `0x${string}`),
            toBytes(nonceBytes),
            toBytes(newCodeHash),
          ]),
        );

        if (account.type === 'local' && account.privateKey) {
          // Local account - sign directly with private key
          const signer = privateKeyToAccount(
            account.privateKey as `0x${string}`,
          );
          signature = await signer.signMessage({
            message: { raw: messageHash },
          });
        } else if (account.type === 'metamask' && window.ethereum) {
          // MetaMask - request signature
          signature = await window.ethereum.request({
            method: 'personal_sign',
            params: [toHex(messageHash), account.address],
          });
        } else {
          console.warn(
            `Unsupported account type '${account.type}' for signing - upgrade will proceed without signature`,
          );
        }
      }

      // Use JsonRpcService to call the upgrade endpoint (rpcClient already initialized above)
      const result = await rpcClient.upgradeContractCode(
        contractAddress,
        contractCode,
        signature,
      );

      // Add to transaction store
      const tx: TransactionItem = {
        hash: result.transaction_hash as TransactionHash,
        type: 'upgrade',
        statusName: TransactionStatus.PENDING,
        contractAddress: contractAddress,
        localContractId: contract.value?.id ?? '',
        data: { new_code: contractCode },
      };
      transactionsStore.addTransaction(tx);

      notify({
        title: 'Upgrade queued',
        type: 'success',
      });

      trackEvent('upgraded_contract', {
        contract_name: contract.value?.name || '',
      });

      return result.transaction_hash;
    } catch (error: any) {
      notify({
        type: 'error',
        title: 'Error upgrading contract',
        text: error.message,
      });
      console.error('Error upgrading contract', error);
      throw error;
    } finally {
      isUpgrading.value = false;
    }
  }

  return {
    contractSchemaQuery,
    contractAbiQuery,
    contract,
    isDeploying,
    isDeployed,
    address,

    deployContract,
    callReadMethod,
    callWriteMethod,
    simulateWriteMethod,
    fetchContractCode,
    upgradeContract,
    isUpgrading,

    mockContractSchema,
    isMock,
  };
}
