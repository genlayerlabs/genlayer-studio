/**
 * Behavioral snapshot tests for useSetupStores.
 *
 * Documents the app initialization sequence — what gets called,
 * in what order, and what Studio-specific calls are made.
 * The multi-network refactor must preserve this behavior when isStudio=true
 * and skip sim_* calls when isStudio=false.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Track call order
const callOrder: string[] = [];
const track = (name: string) => {
  callOrder.push(name);
  return Promise.resolve();
};

const mockDb = {
  contractFiles: {
    toArray: vi.fn(() => {
      callOrder.push('db.contractFiles.toArray');
      return Promise.resolve([
        { id: 'test', name: 'test.py', content: 'pass' },
      ]);
    }),
  },
  deployedContracts: {
    toArray: vi.fn(() => {
      callOrder.push('db.deployedContracts.toArray');
      return Promise.resolve([]);
    }),
  },
  transactions: {
    toArray: vi.fn(() => {
      callOrder.push('db.transactions.toArray');
      return Promise.resolve([]);
    }),
  },
};

const mockContractsStore = {
  contracts: [],
  deployedContracts: [],
  addContractFile: vi.fn(),
  getInitialOpenedFiles: vi.fn(),
};

const mockAccountsStore = {
  accounts: [{ address: '0x1' }],
  generateNewAccount: vi.fn(),
};

const mockTransactionsStore = {
  transactions: [],
  initSubscriptions: vi.fn(() => track('initSubscriptions')),
  refreshPendingTransactions: vi.fn(() => track('refreshPendingTransactions')),
};

const mockNodeStore = {
  getValidatorsData: vi.fn(() => track('getValidatorsData')),
  getProvidersData: vi.fn(() => track('getProvidersData')),
};

const mockConsensusStore = {
  fetchFinalityWindowTime: vi.fn(() => track('fetchFinalityWindowTime')),
  setupReconnectionListener: vi.fn(() => track('setupReconnectionListener')),
};

const mockTutorialStore = {
  resetTutorialState: vi.fn(),
};

const mockTransactionListener = {
  init: vi.fn(() => track('transactionListener.init')),
};
const mockContractListener = {
  init: vi.fn(() => track('contractListener.init')),
};
const mockGenlayer = {
  client: { value: {} },
  initClient: vi.fn(() => track('initClient')),
};

vi.mock('@/stores', () => ({
  useContractsStore: vi.fn(() => mockContractsStore),
  useAccountsStore: vi.fn(() => mockAccountsStore),
  useTransactionsStore: vi.fn(() => mockTransactionsStore),
  useNodeStore: vi.fn(() => mockNodeStore),
  useUIStore: vi.fn(() => ({})),
  useConsensusStore: vi.fn(() => mockConsensusStore),
  useTutorialStore: vi.fn(() => mockTutorialStore),
}));

vi.mock('uuid', () => ({
  v4: vi.fn(() => 'test-uuid'),
}));

vi.mock('@/hooks', () => ({
  useTransactionListener: vi.fn(() => mockTransactionListener),
  useContractListener: vi.fn(() => mockContractListener),
  useGenlayer: vi.fn(() => mockGenlayer),
  useDb: vi.fn(() => mockDb),
  useWebSocketClientAsync: vi.fn(() => Promise.resolve()),
}));

describe('useSetupStores — behavioral contract', () => {
  beforeEach(() => {
    callOrder.length = 0;
    vi.clearAllMocks();
    mockContractsStore.contracts = [];
    mockContractsStore.deployedContracts = [];
    mockTransactionsStore.transactions = [];
    mockAccountsStore.accounts = [{ address: '0x1' }];
  });

  it('should call sim_* methods (validators, providers, finality window) during init', async () => {
    const { useSetupStores } = await import('@/hooks/useSetupStores');
    const { setupStores } = useSetupStores();
    await setupStores();

    expect(mockNodeStore.getValidatorsData).toHaveBeenCalled();
    expect(mockNodeStore.getProvidersData).toHaveBeenCalled();
    expect(mockConsensusStore.fetchFinalityWindowTime).toHaveBeenCalled();
  });

  it('should initialize transaction and contract listeners', async () => {
    const { useSetupStores } = await import('@/hooks/useSetupStores');
    const { setupStores } = useSetupStores();
    await setupStores();

    expect(mockTransactionListener.init).toHaveBeenCalled();
    expect(mockContractListener.init).toHaveBeenCalled();
  });

  it('should load persisted data from IndexedDB', async () => {
    const { useSetupStores } = await import('@/hooks/useSetupStores');
    const { setupStores } = useSetupStores();
    await setupStores();

    expect(mockDb.contractFiles.toArray).toHaveBeenCalled();
    expect(mockDb.deployedContracts.toArray).toHaveBeenCalled();
    expect(mockDb.transactions.toArray).toHaveBeenCalled();
  });

  it('should refresh pending transactions', async () => {
    const { useSetupStores } = await import('@/hooks/useSetupStores');
    const { setupStores } = useSetupStores();
    await setupStores();

    expect(mockTransactionsStore.refreshPendingTransactions).toHaveBeenCalled();
  });

  it('should initialize genlayer client', async () => {
    const { useSetupStores } = await import('@/hooks/useSetupStores');
    const { setupStores } = useSetupStores();
    await setupStores();

    expect(mockGenlayer.initClient).toHaveBeenCalled();
  });

  it('should not generate account if accounts already exist', async () => {
    mockAccountsStore.accounts = [{ address: '0x1' }];

    const { useSetupStores } = await import('@/hooks/useSetupStores');
    const { setupStores } = useSetupStores();
    await setupStores();

    expect(mockAccountsStore.generateNewAccount).not.toHaveBeenCalled();
  });

  it('should generate account if no accounts exist', async () => {
    mockAccountsStore.accounts = [];

    const { useSetupStores } = await import('@/hooks/useSetupStores');
    const { setupStores } = useSetupStores();
    await setupStores();

    expect(mockAccountsStore.generateNewAccount).toHaveBeenCalled();
  });
});
