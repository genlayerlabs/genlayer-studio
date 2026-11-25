/**
 * Behavioral snapshot tests for useContractQueries.
 *
 * Documents the deploy, write, read, upgrade, and cancel flows.
 * Captures which methods are Studio-only vs universal.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ref } from 'vue';
import { JsonRpcServiceError } from '@/services/JsonRpcService';

const mockDeployContract = vi.fn(() => Promise.resolve('0xDeployHash'));
const mockWriteContract = vi.fn(() => Promise.resolve('0xWriteHash'));
const mockReadContract = vi.fn(() => Promise.resolve('result'));
const mockGetContractSchemaForCode = vi.fn(() =>
  Promise.resolve({ methods: [] }),
);
const mockGetContractSchema = vi.fn(() => Promise.resolve({ abi: [] }));
const mockGetContractCode = vi.fn(() => Promise.resolve('code'));
const mockSimulateWriteContract = vi.fn(() => Promise.resolve('simResult'));
const mockEstimateTransactionFees = vi.fn(() =>
  Promise.resolve({
    policy: { enabled: true },
    distribution: {
      leaderTimeunitsAllocation: '100',
      validatorTimeunitsAllocation: '50',
      appealRounds: '1',
      executionBudgetPerRound: '100000000000000000',
      executionConsumed: '0',
      totalMessageFees: '20000000000000000',
      rotations: ['0', '0'],
      maxPriceGenPerTimeUnit: '1000000000000000',
      storageFeeMaxGasPrice: '1',
      receiptFeeMaxGasPrice: '1',
    },
    feeValue: '120000000000000000',
  }),
);

const mockEstimateTransactionFeesForWrite = vi.fn();

const mockGenlayerClient = {
  deployContract: mockDeployContract,
  writeContract: mockWriteContract,
  readContract: mockReadContract,
  getContractSchemaForCode: mockGetContractSchemaForCode,
  getContractSchema: mockGetContractSchema,
  getContractCode: mockGetContractCode,
  simulateWriteContract: mockSimulateWriteContract,
  estimateTransactionFees: mockEstimateTransactionFees as any,
  estimateTransactionFeesForWrite: mockEstimateTransactionFeesForWrite as any,
};

const mockChainDeployContract = vi.fn(() => Promise.resolve('0xChainDeploy'));
const mockChainWriteContract = vi.fn(() => Promise.resolve('0xChainWrite'));
const mockChainEstimateTransactionFees = vi.fn();
const mockChainEstimateTransactionFeesForWrite = vi.fn();
const mockChainClient = {
  deployContract: mockChainDeployContract,
  writeContract: mockChainWriteContract,
  estimateTransactionFees: mockChainEstimateTransactionFees,
  estimateTransactionFeesForWrite: mockChainEstimateTransactionFeesForWrite,
};
const mockTransactionClient = ref<any>(mockGenlayerClient);

const mockEnsureCorrectChain = vi.fn();
const mockMessageAllocations = [
  {
    messageType: 1,
    onAcceptance: true,
    parentIndex:
      '0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
    recipient: '0x0000000000000000000000000000000000000001',
    callKey:
      '0x7469700000000000000000000000000000000000000000000000000000000000',
    budget: '22000000000000000',
    feeParams: '0x1234',
  },
];
const mockRpcEstimateTransactionFees = vi.fn(() =>
  Promise.resolve({
    scenario: 'tip',
    feeReport: { totalEstimatedFee: '120000000000000000' },
    recommendedPreset: {
      feeValue: '132000000000000000',
      messageAllocations: mockMessageAllocations,
      distribution: {
        leaderTimeunitsAllocation: '110',
        validatorTimeunitsAllocation: '55',
        appealRounds: '1',
        executionBudgetPerRound: '110000000000000000',
        executionConsumed: '0',
        totalMessageFees: '22000000000000000',
        rotations: ['0', '0'],
        maxPriceGenPerTimeUnit: '1000000000000000',
        storageFeeMaxGasPrice: '1',
        receiptFeeMaxGasPrice: '1',
      },
    },
  }),
);
const mockRpcSimulateCall = vi.fn(() => Promise.resolve({ status: 'ok' }));
const mockRpcGetFeeConfig = vi.fn();
const mockRpcClient = {
  estimateTransactionFees: mockRpcEstimateTransactionFees,
  simulateCall: mockRpcSimulateCall,
  getFeeConfig: mockRpcGetFeeConfig,
};
const mockNetworkStore: {
  isStudio: boolean;
  rpcUrl: string;
  chain: { feeManagerContract?: { address: string } };
} = {
  isStudio: false,
  rpcUrl: 'http://127.0.0.1:4000/api',
  chain: {},
};

const mockContractsStore = {
  currentContract: { id: 'test-id', name: 'Test', content: 'class Foo: pass' },
  deployedContracts: [{ contractId: 'test-id', address: '0xContract' }],
  addDeployedContract: vi.fn(),
  removeDeployedContract: vi.fn(),
};

const mockTransactionsStore = {
  transactions: [],
  addTransaction: vi.fn(),
  clearTransactionsForContract: vi.fn(() => Promise.resolve()),
};

const mockAccountsStore = {
  selectedAccount: {
    address: '0xUser',
    type: 'local',
    privateKey: '0xkey',
  },
};

vi.mock('@/stores', () => ({
  useContractsStore: vi.fn(() => mockContractsStore),
  useTransactionsStore: vi.fn(() => mockTransactionsStore),
  useAccountsStore: vi.fn(() => mockAccountsStore),
}));

vi.mock('@/hooks', () => ({
  useEventTracking: vi.fn(() => ({ trackEvent: vi.fn() })),
  useGenlayer: vi.fn(() => ({
    client: ref(mockGenlayerClient),
    transactionClient: mockTransactionClient,
  })),
  useRpcClient: vi.fn(() => mockRpcClient),
  useWallet: vi.fn(() => ({
    walletProvider: ref(undefined),
  })),
  useChainEnforcer: vi.fn(() => ({
    ensureCorrectChain: mockEnsureCorrectChain,
  })),
}));

vi.mock('@/stores/network', () => ({
  useNetworkStore: vi.fn(() => mockNetworkStore),
}));

vi.mock('@/hooks/useMockContractData', () => ({
  useMockContractData: vi.fn(() => ({
    mockContractId: 'mock-id',
    mockContractSchema: {},
  })),
}));

vi.mock('@kyvg/vue3-notification', () => ({
  notify: vi.fn(),
}));

vi.mock('@tanstack/vue-query', () => ({
  useQuery: vi.fn(() => ({
    data: ref(null),
    isLoading: ref(false),
    refetch: vi.fn(),
  })),
  useQueryClient: vi.fn(() => ({
    invalidateQueries: vi.fn(),
  })),
}));

vi.mock('@vueuse/core', () => ({
  useDebounceFn: vi.fn((fn: any) => fn),
}));

describe('useContractQueries — behavioral contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNetworkStore.isStudio = false;
    mockNetworkStore.rpcUrl = 'http://127.0.0.1:4000/api';
    mockNetworkStore.chain = {};
    mockTransactionClient.value = mockGenlayerClient;
    mockGenlayerClient.estimateTransactionFees = mockEstimateTransactionFees;
    mockRpcGetFeeConfig.mockResolvedValue({
      enabled: true,
      defaultFees: {
        distribution: {
          leaderTimeunitsAllocation: '100',
          validatorTimeunitsAllocation: '200',
          appealRounds: '0',
          executionBudgetPerRound: '500000',
          executionConsumed: '0',
          totalMessageFees: '0',
          rotations: ['0'],
          maxPriceGenPerTimeUnit: '1200000000000000',
          storageFeeMaxGasPrice: '2',
          receiptFeeMaxGasPrice: '2',
        },
        feeValue: '600000000000000000',
      },
    });
  });

  describe('deployContract', () => {
    it('should call ensureCorrectChain BEFORE deploying (order matters)', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(mockEnsureCorrectChain).toHaveBeenCalled();
      expect(mockDeployContract).toHaveBeenCalled();
      // Verify order: chain check must happen before tx submission
      const chainOrder = mockEnsureCorrectChain.mock.invocationCallOrder[0];
      const deployOrder = mockDeployContract.mock.invocationCallOrder[0];
      expect(chainOrder).toBeLessThan(deployOrder);
    });

    it('should call genlayerClient.deployContract with contract code', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract({ args: ['arg1'], kwargs: {} }, 'NORMAL' as any, 3);

      expect(mockDeployContract).toHaveBeenCalledWith(
        expect.objectContaining({
          args: ['arg1'],
          leaderOnly: false,
          consensusMaxRotations: 3,
        }),
      );
    });

    it('should add transaction to store after deploy', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(mockTransactionsStore.addTransaction).toHaveBeenCalledWith(
        expect.objectContaining({
          hash: '0xDeployHash',
          type: 'deploy',
        }),
      );
    });

    it('should clear existing transactions for contract before adding deploy tx', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(
        mockTransactionsStore.clearTransactionsForContract,
      ).toHaveBeenCalledWith('test-id');
    });
  });

  describe('callWriteMethod', () => {
    it('should call ensureCorrectChain BEFORE writing (order matters)', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod({
        method: 'tip',
        args: { args: [], kwargs: {} },
        executionMode: 'NORMAL' as any,
      });

      expect(mockEnsureCorrectChain).toHaveBeenCalled();
      expect(mockWriteContract).toHaveBeenCalled();
      const chainOrder = mockEnsureCorrectChain.mock.invocationCallOrder[0];
      const writeOrder = mockWriteContract.mock.invocationCallOrder[0];
      expect(chainOrder).toBeLessThan(writeOrder);
    });

    it('should call genlayerClient.writeContract with method and args', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod({
        method: 'tip',
        args: { args: ['hello'], kwargs: {} },
        executionMode: 'NORMAL' as any,
        value: BigInt(5000),
      });

      expect(mockWriteContract).toHaveBeenCalledWith(
        expect.objectContaining({
          functionName: 'tip',
          args: ['hello'],
          value: BigInt(5000),
        }),
      );
    });

    it('should add transaction to store with method type', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod({
        method: 'tip',
        args: { args: [], kwargs: {} },
        executionMode: 'NORMAL' as any,
      });

      expect(mockTransactionsStore.addTransaction).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'method',
          hash: '0xWriteHash',
          decodedData: expect.objectContaining({ functionName: 'tip' }),
        }),
      );
    });
  });

  describe('Studio fee defaults', () => {
    it('should estimate and attach transaction-specific Studio fees to deployments', async () => {
      mockNetworkStore.isStudio = true;
      const { abi } = await import('genlayer-js');
      const constructorArgs = {
        args: ['arg1'],
        kwargs: { owner: 'alice' },
      };
      const expectedData = abi.transactions.serialize([
        new TextEncoder().encode(mockContractsStore.currentContract.content),
        abi.calldata.encode(
          abi.calldata.makeCalldataObject(
            undefined,
            constructorArgs.args,
            constructorArgs.kwargs,
          ),
        ),
        false,
      ]);
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract(constructorArgs, 'NORMAL' as any, 3);

      expect(mockRpcEstimateTransactionFees).toHaveBeenCalledWith(
        expect.objectContaining({
          scenarioName: 'deploy',
          type: 'deploy',
          to: '0x0000000000000000000000000000000000000000',
          from: '0xUser',
          data: expectedData,
        }),
      );
      expect(mockEstimateTransactionFees).not.toHaveBeenCalled();
      expect(mockDeployContract).toHaveBeenCalledWith(
        expect.objectContaining({
          fees: expect.objectContaining({
            feeValue: '132000000000000000',
            messageAllocations: mockMessageAllocations,
            distribution: expect.objectContaining({
              leaderTimeunitsAllocation: '110',
              totalMessageFees: '22000000000000000',
              maxPriceGenPerTimeUnit: '1000000000000000',
            }),
          }),
          kwargs: constructorArgs.kwargs,
        }),
      );
    });

    it('should estimate and attach transaction-specific Studio fees to writes', async () => {
      mockNetworkStore.isStudio = true;
      const { abi } = await import('genlayer-js');
      const writeArgs = {
        args: ['hello'],
        kwargs: { recipient: 'alice' },
      };
      const expectedData = abi.transactions.serialize([
        abi.calldata.encode(
          abi.calldata.makeCalldataObject(
            'tip',
            writeArgs.args,
            writeArgs.kwargs,
          ),
        ),
        false,
      ]);
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod({
        method: 'tip',
        args: writeArgs,
        executionMode: 'NORMAL' as any,
        value: 5n,
      });

      expect(mockRpcEstimateTransactionFees).toHaveBeenCalledWith(
        expect.objectContaining({
          scenarioName: 'tip',
          type: 'write',
          to: '0xContract',
          from: '0xUser',
          value: '0x5',
          transaction_hash_variant: 'latest-nonfinal',
          data: expectedData,
        }),
      );
      expect(mockEstimateTransactionFees).not.toHaveBeenCalled();
      expect(mockWriteContract).toHaveBeenCalledWith(
        expect.objectContaining({
          functionName: 'tip',
          kwargs: writeArgs.kwargs,
          value: 5n,
          fees: expect.objectContaining({
            feeValue: '132000000000000000',
            messageAllocations: mockMessageAllocations,
            distribution: expect.objectContaining({
              validatorTimeunitsAllocation: '55',
              receiptFeeMaxGasPrice: '1',
            }),
          }),
        }),
      );
    });

    it('should send Studio simulations through sim_call with fee params', async () => {
      mockNetworkStore.isStudio = true;
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { simulateWriteMethod } = useContractQueries();

      await simulateWriteMethod({
        method: 'tip',
        args: { args: ['hello'], kwargs: {} },
        value: 5n,
      });

      expect(mockRpcSimulateCall).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'write',
          to: '0xContract',
          from: '0xUser',
          value: '0x5',
          transaction_hash_variant: 'latest-nonfinal',
          data: expect.stringMatching(/^0x/),
          fees: expect.objectContaining({
            feeValue: '132000000000000000',
            messageAllocations: mockMessageAllocations,
            distribution: expect.objectContaining({
              leaderTimeunitsAllocation: '110',
              totalMessageFees: '22000000000000000',
            }),
          }),
        }),
      );
      expect(mockSimulateWriteContract).not.toHaveBeenCalled();
    });

    it('should fall back to sim_getFeeConfig when SDK fee defaults are unavailable', async () => {
      mockNetworkStore.isStudio = true;
      mockRpcEstimateTransactionFees.mockRejectedValueOnce(
        new JsonRpcServiceError('execution failed', -32000),
      );
      mockGenlayerClient.estimateTransactionFees = undefined;
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod({
        method: 'tip',
        args: { args: ['hello'], kwargs: {} },
        executionMode: 'NORMAL' as any,
      });

      expect(mockRpcGetFeeConfig).toHaveBeenCalledTimes(1);
      expect(mockWriteContract).toHaveBeenCalledWith(
        expect.objectContaining({
          fees: expect.objectContaining({
            feeValue: 600000000000000000n,
            distribution: expect.objectContaining({
              executionBudgetPerRound: '500000',
              maxPriceGenPerTimeUnit: '1200000000000000',
              receiptFeeMaxGasPrice: '2',
            }),
          }),
        }),
      );
    });

    it('should omit Studio fees when fee config explicitly reports gasless mode', async () => {
      mockNetworkStore.isStudio = true;
      mockRpcEstimateTransactionFees.mockRejectedValueOnce(
        new JsonRpcServiceError('execution failed', -32000),
      );
      mockGenlayerClient.estimateTransactionFees = undefined;
      mockRpcGetFeeConfig.mockResolvedValue({
        enabled: false,
        defaultFees: {
          distribution: {},
          feeValue: '0',
        },
      });
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod({
        method: 'tip',
        args: { args: ['hello'], kwargs: {} },
        executionMode: 'NORMAL' as any,
      });

      expect(mockRpcGetFeeConfig).toHaveBeenCalledTimes(1);
      expect(mockWriteContract).toHaveBeenCalledWith(
        expect.not.objectContaining({
          fees: expect.anything(),
        }),
      );
    });

    it('should not submit with generic fees when transaction-specific estimation is unavailable', async () => {
      mockNetworkStore.isStudio = true;
      mockRpcEstimateTransactionFees.mockRejectedValueOnce(
        new JsonRpcServiceError('manager unavailable', -32002),
      );
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await expect(
        callWriteMethod({
          method: 'tip',
          args: { args: ['hello'], kwargs: {} },
          executionMode: 'NORMAL' as any,
        }),
      ).rejects.toThrow('manager unavailable');

      expect(mockWriteContract).not.toHaveBeenCalled();
      expect(mockEstimateTransactionFees).not.toHaveBeenCalled();
    });

    it('should not deploy with generic fees when transaction-specific estimation is unavailable', async () => {
      mockNetworkStore.isStudio = true;
      mockRpcEstimateTransactionFees.mockRejectedValueOnce(
        new JsonRpcServiceError('manager unavailable', -32002),
      );
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await expect(
        deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3),
      ).rejects.toThrow('manager unavailable');

      expect(mockDeployContract).not.toHaveBeenCalled();
      expect(mockEstimateTransactionFees).not.toHaveBeenCalled();
    });

    it('should not submit when the estimator omits its recommended preset', async () => {
      mockNetworkStore.isStudio = true;
      mockRpcEstimateTransactionFees.mockResolvedValueOnce({
        scenario: 'tip',
        feeReport: {},
      } as any);
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await expect(
        callWriteMethod({
          method: 'tip',
          args: { args: ['hello'], kwargs: {} },
          executionMode: 'NORMAL' as any,
        }),
      ).rejects.toThrow('Studio fee estimator returned no recommended preset');

      expect(mockWriteContract).not.toHaveBeenCalled();
      expect(mockEstimateTransactionFees).not.toHaveBeenCalled();
    });
  });

  describe('Network fees (chains with a FeeManager)', () => {
    const feeManagerChain = {
      feeManagerContract: {
        address: '0x10F2d70ABB8F588f96bEB1a02c99163EB75A2CF1',
      },
    };
    const writeEstimate = {
      policy: { enabled: true },
      distribution: {
        leaderTimeunitsAllocation: '120',
        validatorTimeunitsAllocation: '240',
        appealRounds: '0',
        executionBudgetPerRound: '2000000000000000',
        executionConsumed: '0',
        totalMessageFees: '22000000000000000',
        rotations: ['3'],
        maxPriceGenPerTimeUnit: '2',
        storageFeeMaxGasPrice: '300000000',
        receiptFeeMaxGasPrice: '12000000',
      },
      messageAllocations: mockMessageAllocations,
      feeValue: '30000000000000000',
    };

    it('should attach the SDK estimate to deployments', async () => {
      mockNetworkStore.chain = feeManagerChain;
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(mockEstimateTransactionFees).toHaveBeenCalledTimes(1);
      expect(mockRpcEstimateTransactionFees).not.toHaveBeenCalled();
      expect(mockDeployContract).toHaveBeenCalledWith(
        expect.objectContaining({
          fees: expect.objectContaining({
            feeValue: 120000000000000000n,
            distribution: expect.objectContaining({
              executionBudgetPerRound: '100000000000000000',
            }),
          }),
        }),
      );
    });

    it('should size write fees with the SDK write estimator', async () => {
      mockNetworkStore.chain = feeManagerChain;
      mockEstimateTransactionFeesForWrite.mockResolvedValueOnce(writeEstimate);
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod({
        method: 'tip',
        args: { args: [1], kwargs: {} },
        executionMode: 'NORMAL' as any,
        value: 5n,
      });

      expect(mockEstimateTransactionFeesForWrite).toHaveBeenCalledWith({
        address: '0xContract',
        functionName: 'tip',
        args: [1],
        kwargs: {},
        value: 5n,
        leaderOnly: false,
      });
      expect(mockEstimateTransactionFees).not.toHaveBeenCalled();
      expect(mockWriteContract).toHaveBeenCalledWith(
        expect.objectContaining({
          fees: {
            distribution: writeEstimate.distribution,
            feeValue: 30000000000000000n,
            messageAllocations: mockMessageAllocations,
          },
        }),
      );
    });

    it('should use the base estimate when the RPC cannot simulate the write', async () => {
      mockNetworkStore.chain = feeManagerChain;
      mockEstimateTransactionFeesForWrite.mockRejectedValueOnce(
        Object.assign(new Error('request failed'), {
          cause: {
            code: -32601,
            message: 'the method gen_call does not exist',
          },
        }),
      );
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod({
        method: 'tip',
        args: { args: [], kwargs: {} },
        executionMode: 'NORMAL' as any,
      });

      expect(mockEstimateTransactionFees).toHaveBeenCalledTimes(1);
      expect(mockWriteContract).toHaveBeenCalledWith(
        expect.objectContaining({
          fees: expect.objectContaining({ feeValue: 120000000000000000n }),
        }),
      );
    });

    it('should not submit a write whose estimate fails', async () => {
      mockNetworkStore.chain = feeManagerChain;
      mockEstimateTransactionFeesForWrite.mockRejectedValueOnce(
        new Error('execution failed'),
      );
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await expect(
        callWriteMethod({
          method: 'tip',
          args: { args: [], kwargs: {} },
          executionMode: 'NORMAL' as any,
        }),
      ).rejects.toThrow('execution failed');
      expect(mockEstimateTransactionFees).not.toHaveBeenCalled();
      expect(mockWriteContract).not.toHaveBeenCalled();
    });

    it('should simulate writes with the base estimate', async () => {
      mockNetworkStore.chain = feeManagerChain;
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { simulateWriteMethod } = useContractQueries();

      await simulateWriteMethod({
        method: 'tip',
        args: { args: [], kwargs: {} },
      });

      expect(mockEstimateTransactionFeesForWrite).not.toHaveBeenCalled();
      expect(mockSimulateWriteContract).toHaveBeenCalledWith(
        expect.objectContaining({
          functionName: 'tip',
          fees: expect.objectContaining({ feeValue: 120000000000000000n }),
        }),
      );
    });

    it('should send through the chain RPC client when the node RPC is read-only', async () => {
      mockNetworkStore.chain = feeManagerChain;
      mockTransactionClient.value = mockChainClient;
      mockChainEstimateTransactionFees.mockResolvedValue(writeEstimate);
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract, callWriteMethod } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);
      await callWriteMethod({
        method: 'tip',
        args: { args: [], kwargs: {} },
        executionMode: 'NORMAL' as any,
      });

      expect(mockChainEstimateTransactionFees).toHaveBeenCalledTimes(2);
      expect(mockChainEstimateTransactionFeesForWrite).not.toHaveBeenCalled();
      expect(mockEstimateTransactionFees).not.toHaveBeenCalled();
      expect(mockEstimateTransactionFeesForWrite).not.toHaveBeenCalled();
      expect(mockChainDeployContract).toHaveBeenCalledWith(
        expect.objectContaining({
          fees: expect.objectContaining({ feeValue: 30000000000000000n }),
        }),
      );
      expect(mockChainWriteContract).toHaveBeenCalledWith(
        expect.objectContaining({
          functionName: 'tip',
          fees: expect.objectContaining({ feeValue: 30000000000000000n }),
        }),
      );
      expect(mockDeployContract).not.toHaveBeenCalled();
      expect(mockWriteContract).not.toHaveBeenCalled();
    });

    it('should omit fees when the fee policy is disabled', async () => {
      mockNetworkStore.chain = feeManagerChain;
      mockEstimateTransactionFees.mockResolvedValueOnce({
        policy: { enabled: false },
        distribution: writeEstimate.distribution,
        feeValue: '0',
      });
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(mockDeployContract).toHaveBeenCalledWith(
        expect.not.objectContaining({ fees: expect.anything() }),
      );
    });

    it('should not estimate fees on a chain without a FeeManager', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract, callWriteMethod } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);
      await callWriteMethod({
        method: 'tip',
        args: { args: [], kwargs: {} },
        executionMode: 'NORMAL' as any,
      });

      expect(mockEstimateTransactionFees).not.toHaveBeenCalled();
      expect(mockEstimateTransactionFeesForWrite).not.toHaveBeenCalled();
      expect(mockDeployContract).toHaveBeenCalledWith(
        expect.not.objectContaining({ fees: expect.anything() }),
      );
      expect(mockWriteContract).toHaveBeenCalledWith(
        expect.not.objectContaining({ fees: expect.anything() }),
      );
    });
  });

  describe('Studio fee estimation', () => {
    it('should estimate write fees from the same method/value shape as a transaction', async () => {
      mockNetworkStore.isStudio = true;
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { estimateWriteMethodFees } = useContractQueries();

      const result = await estimateWriteMethodFees({
        method: 'tip',
        args: { args: ['hello'], kwargs: {} },
        value: 5n,
      });

      expect(result.recommendedPreset?.feeValue).toBe('132000000000000000');
      expect(mockRpcEstimateTransactionFees).toHaveBeenCalledWith(
        expect.objectContaining({
          scenarioName: 'tip',
          type: 'write',
          to: '0xContract',
          from: '0xUser',
          value: '0x5',
          transaction_hash_variant: 'latest-nonfinal',
          data: expect.stringMatching(/^0x/),
        }),
      );
    });

    it('should reject fee estimation outside Studio', async () => {
      mockNetworkStore.isStudio = false;
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { estimateWriteMethodFees } = useContractQueries();

      await expect(
        estimateWriteMethodFees({
          method: 'tip',
          args: { args: [], kwargs: {} },
        }),
      ).rejects.toThrow('Fee estimation is only available in Studio');
      expect(mockRpcEstimateTransactionFees).not.toHaveBeenCalled();
    });
  });

  describe('callReadMethod (universal — works on any network)', () => {
    it('should call genlayerClient.readContract', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callReadMethod } = useContractQueries();

      const result = await callReadMethod('get_balance', {
        args: [],
        kwargs: {},
      });

      expect(mockReadContract).toHaveBeenCalledWith(
        expect.objectContaining({
          functionName: 'get_balance',
          args: [],
        }),
      );
    });

    it('should NOT call ensureCorrectChain for reads', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callReadMethod } = useContractQueries();

      await callReadMethod('get_balance', { args: [], kwargs: {} });

      expect(mockEnsureCorrectChain).not.toHaveBeenCalled();
    });
  });

  describe('simulateWriteMethod (universal)', () => {
    it('should call genlayerClient.simulateWriteContract', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { simulateWriteMethod } = useContractQueries();

      await simulateWriteMethod({
        method: 'tip',
        args: { args: [], kwargs: {} },
      });

      expect(mockSimulateWriteContract).toHaveBeenCalledWith(
        expect.objectContaining({
          functionName: 'tip',
        }),
      );
    });
  });

  describe('executionMode mapping', () => {
    it('should set leaderOnly=false for NORMAL mode', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(mockDeployContract).toHaveBeenCalledWith(
        expect.objectContaining({ leaderOnly: false }),
      );
    });

    it('should set leaderOnly=true for LEADER_ONLY mode', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'LEADER_ONLY' as any, 3);

      expect(mockDeployContract).toHaveBeenCalledWith(
        expect.objectContaining({ leaderOnly: true }),
      );
    });
  });
});
