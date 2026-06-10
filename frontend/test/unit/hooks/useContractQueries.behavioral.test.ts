/**
 * Behavioral snapshot tests for useContractQueries.
 *
 * Documents the deploy, write, read, upgrade, and cancel flows.
 * Captures which methods are Studio-only vs universal.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ref } from 'vue';

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

const mockGenlayerClient = {
  deployContract: mockDeployContract,
  writeContract: mockWriteContract,
  readContract: mockReadContract,
  getContractSchemaForCode: mockGetContractSchemaForCode,
  getContractSchema: mockGetContractSchema,
  getContractCode: mockGetContractCode,
  simulateWriteContract: mockSimulateWriteContract,
  estimateTransactionFees: mockEstimateTransactionFees as any,
};

const mockEnsureCorrectChain = vi.fn();
const mockRpcEstimateTransactionFees = vi.fn(() =>
  Promise.resolve({
    scenario: 'tip',
    feeReport: { totalEstimatedFee: '120000000000000000' },
    recommendedPreset: {
      feeValue: '132000000000000000',
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
const mockNetworkStore = {
  isStudio: false,
  rpcUrl: 'http://127.0.0.1:4000/api',
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
    it('should attach trusted Studio default fees to deployments', async () => {
      mockNetworkStore.isStudio = true;
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(mockEstimateTransactionFees).toHaveBeenCalled();
      expect(mockDeployContract).toHaveBeenCalledWith(
        expect.objectContaining({
          fees: expect.objectContaining({
            feeValue: 120000000000000000n,
            distribution: expect.objectContaining({
              leaderTimeunitsAllocation: '100',
              totalMessageFees: '20000000000000000',
              maxPriceGenPerTimeUnit: '1000000000000000',
            }),
          }),
        }),
      );
    });

    it('should attach trusted Studio default fees to writes', async () => {
      mockNetworkStore.isStudio = true;
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod({
        method: 'tip',
        args: { args: ['hello'], kwargs: {} },
        executionMode: 'NORMAL' as any,
        value: 5n,
      });

      expect(mockEstimateTransactionFees).toHaveBeenCalled();
      expect(mockWriteContract).toHaveBeenCalledWith(
        expect.objectContaining({
          functionName: 'tip',
          value: 5n,
          fees: expect.objectContaining({
            feeValue: 120000000000000000n,
            distribution: expect.objectContaining({
              validatorTimeunitsAllocation: '50',
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
            feeValue: '120000000000000000',
            distribution: expect.objectContaining({
              leaderTimeunitsAllocation: '100',
              totalMessageFees: '20000000000000000',
            }),
          }),
        }),
      );
      expect(mockSimulateWriteContract).not.toHaveBeenCalled();
    });

    it('should fall back to sim_getFeeConfig when SDK fee defaults are unavailable', async () => {
      mockNetworkStore.isStudio = true;
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
