import { watch, ref, computed } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import type { TransactionItem, FeesDistribution } from '@/types';
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
    feesDistribution?: FeesDistribution,
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
        feesDistribution: feesDistribution,
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
    feesDistribution,
  }: {
    method: string;
    args: {
      args: CalldataEncodable[];
      kwargs: { [key: string]: CalldataEncodable };
    };
    leaderOnly: boolean;
    feesDistribution?: FeesDistribution;
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
        feesDistribution: feesDistribution,
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

    mockContractSchema,
    isMock,
  };
}
