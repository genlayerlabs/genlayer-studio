import { describe, it, expect, beforeEach, vi, type Mock } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useContractsStore, useTransactionsStore } from '@/stores';
import {
  useDb,
  useFileName,
  useSetupStores,
  useRpcClient,
  useWebSocketClient,
  useGenlayer,
} from '@/hooks';
import { notify } from '@kyvg/vue3-notification';
import type { Address } from 'genlayer-js/types';

const testContract = {
  id: '03bf999b-9b2f-48f1-b1e8-52becccc6e87',
  name: 'test.py',
  content: 'print("hello")',
  example: true,
};

const testDeployedContract = {
  contractId: '03bf999b-9b2f-48f1-b1e8-52becccc6e87',
  address: '0xBB3557F472b67BbBb8a138AaBbFE77bA1dFF909E' as Address,
  defaultState: '{}',
};

vi.mock('@/hooks', () => ({
  useDb: vi.fn(),
  useFileName: vi.fn(),
  useSetupStores: vi.fn(),
  useRpcClient: vi.fn(),
  useWebSocketClient: vi.fn(),
  useGenlayer: vi.fn(),
}));

vi.mock('@kyvg/vue3-notification', () => ({
  notify: vi.fn(),
}));

describe('useContractsStore', () => {
  let contractsStore: ReturnType<typeof useContractsStore>;
  let transactionsStore: ReturnType<typeof useTransactionsStore>;
  const mockDb = {
    deployedContracts: {
      clear: vi.fn(),
    },
    contractFiles: {
      clear: vi.fn(),
    },
    transactions: {
      clear: vi.fn(),
    },
  };

  const mockFileName = {
    cleanupFileName: vi.fn(),
  };

  const mockSetupStores = {
    setupStores: vi.fn(),
  };

  beforeEach(() => {
    setActivePinia(createPinia());
    (useDb as Mock).mockReturnValue(mockDb);
    (useFileName as Mock).mockReturnValue(mockFileName);
    (useSetupStores as Mock).mockReturnValue(mockSetupStores);
    (useRpcClient as Mock).mockReturnValue({});
    (useWebSocketClient as Mock).mockReturnValue({
      connected: true,
      emit: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
    });
    (useGenlayer as Mock).mockReturnValue({});

    contractsStore = useContractsStore();
    transactionsStore = useTransactionsStore();
    vi.clearAllMocks();
  });

  it('should add a contract file', () => {
    const newContract = testContract;
    mockFileName.cleanupFileName.mockReturnValue(newContract.name);

    contractsStore.addContractFile(newContract);
    expect(contractsStore.contracts).toContainEqual(newContract);
    expect(mockFileName.cleanupFileName).toHaveBeenCalledWith(newContract.name);
  });

  it('should remove a contract file', () => {
    contractsStore.contracts = [testContract];
    contractsStore.removeContractFile(testContract.id);

    expect(contractsStore.contracts).not.toContainEqual(
      expect.objectContaining({ id: testContract.id }),
    );
  });

  it('should open and close files correctly', () => {
    contractsStore.contracts = [testContract];

    contractsStore.openFile(testContract.id);
    expect(contractsStore.openedFiles).toContain(testContract.id);
    expect(contractsStore.currentContractId).toBe(testContract.id);

    contractsStore.closeFile(testContract.id);
    expect(contractsStore.openedFiles).not.toContain(testContract.id);
    expect(contractsStore.currentContractId).toBe('');
  });

  it('should add and remove deployed contracts', () => {
    const deployedContract = {
      contractId: '1',
      address: '0x123' as Address,
      defaultState: {},
    };

    contractsStore.addDeployedContract(testDeployedContract);
    expect(contractsStore.deployedContracts).toContainEqual(
      testDeployedContract,
    );
    expect(notify).toHaveBeenCalledWith({
      title: 'Contract deployed',
      type: 'success',
    });

    contractsStore.removeDeployedContract(testDeployedContract.contractId);
    expect(contractsStore.deployedContracts).not.toContainEqual(
      deployedContract,
    );
  });

  it('should reset storage and delete contracts from the database', async () => {
    const exampleContracts = [testContract];
    contractsStore.contracts = exampleContracts;

    await contractsStore.resetStorage();
    await transactionsStore.resetStorage();

    expect(mockDb.deployedContracts.clear).toHaveBeenCalled();
    expect(mockDb.contractFiles.clear).toHaveBeenCalled();
    expect(mockDb.transactions.clear).toHaveBeenCalled();
    expect(contractsStore.contracts).toHaveLength(0);
    expect(contractsStore.openedFiles).toHaveLength(0);
  });
});
