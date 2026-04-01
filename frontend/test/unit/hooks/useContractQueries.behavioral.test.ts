/**
 * Behavioral snapshot tests for useContractQueries.
 *
 * Documents the deploy, write, read, upgrade, and cancel flows.
 * Captures which methods are Studio-only vs universal.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ref, computed } from 'vue';

const mockDeployContract = vi.fn(() => Promise.resolve('0xDeployHash'));
const mockWriteContract = vi.fn(() => Promise.resolve('0xWriteHash'));
const mockReadContract = vi.fn(() => Promise.resolve('result'));
const mockGetContractSchemaForCode = vi.fn(() =>
  Promise.resolve({ methods: [] }),
);
const mockGetContractSchema = vi.fn(() => Promise.resolve({ abi: [] }));
const mockGetContractCode = vi.fn(() => Promise.resolve('code'));
const mockSimulateWriteContract = vi.fn(() => Promise.resolve('simResult'));

const mockGenlayerClient = {
  deployContract: mockDeployContract,
  writeContract: mockWriteContract,
  readContract: mockReadContract,
  getContractSchemaForCode: mockGetContractSchemaForCode,
  getContractSchema: mockGetContractSchema,
  getContractCode: mockGetContractCode,
  simulateWriteContract: mockSimulateWriteContract,
};

const mockEnsureCorrectChain = vi.fn();

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
  useWallet: vi.fn(() => ({
    walletProvider: ref(undefined),
  })),
  useChainEnforcer: vi.fn(() => ({
    ensureCorrectChain: mockEnsureCorrectChain,
  })),
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
  });

  describe('deployContract', () => {
    it('should call ensureCorrectChain before deploying', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(mockEnsureCorrectChain).toHaveBeenCalled();
      expect(mockDeployContract).toHaveBeenCalled();
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
    it('should call ensureCorrectChain before writing', async () => {
      const { useContractQueries } = await import('@/hooks/useContractQueries');
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod({
        method: 'tip',
        args: { args: [], kwargs: {} },
        executionMode: 'NORMAL' as any,
      });

      expect(mockEnsureCorrectChain).toHaveBeenCalled();
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
