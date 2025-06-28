import { describe, it, expect, beforeEach, vi, type Mock } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useTransactionsStore } from '@/stores';
import { useDb, useGenlayer } from '@/hooks';
import type { TransactionItem } from '@/types';
import type { Address, TransactionHash } from 'genlayer-js/types';
import { TransactionStatus } from 'genlayer-js/types';

vi.mock('@/hooks', () => ({
  useGenlayer: vi.fn(),
  useRpcClient: vi.fn(),
  useWebSocketClient: vi.fn(() => ({
    connected: true,
    emit: vi.fn(),
  })),
  useDb: vi.fn(() => ({
    transaction: vi.fn(),
    get: vi.fn(),
    put: vi.fn(),
  })),
  useSetupStores: vi.fn(() => ({
    setupStores: vi.fn(),
  })),
  useFileName: vi.fn(() => ({
    cleanupFileName: vi.fn(),
  })),
}));

const testTransaction: TransactionItem = {
  hash: '0x1234567890123456789012345678901234567890',
  type: 'deploy',
  statusName: TransactionStatus.PENDING,
  contractAddress: '0xAf4ec2548dBBdc43ab6dCFbD4EdcEedde3FEAFB5' as Address,
  data: {
    contract_address: '0xAf4ec2548dBBdc43ab6dCFbD4EdcEedde3FEAFB5' as Address,
  },
  localContractId: '47490604-6ee9-4c0e-bf31-05d33197eedd',
};

const updatedTransactionPayload: TransactionItem = {
  ...testTransaction,
  statusName: TransactionStatus.FINALIZED,
};

describe('useTransactionsStore', () => {
  let transactionsStore: ReturnType<typeof useTransactionsStore>;
  const mockGenlayerClient = {
    getTransaction: vi.fn(),
  };
  const mockDb = {
    transactions: {
      where: vi.fn().mockReturnThis(),
      anyOf: vi.fn().mockReturnThis(),
      equals: vi.fn().mockReturnThis(),
      modify: vi.fn().mockResolvedValue(undefined),
      delete: vi.fn(),
    },
  };

  beforeEach(() => {
    setActivePinia(createPinia());
    (useGenlayer as Mock).mockReturnValue({
      client: { value: mockGenlayerClient },
    });
    (useDb as Mock).mockReturnValue(mockDb);
    transactionsStore = useTransactionsStore();
    transactionsStore.transactions = [];
    mockGenlayerClient.getTransaction.mockClear();
  });

  it('should add a transaction', () => {
    transactionsStore.addTransaction(testTransaction);
    expect(transactionsStore.transactions).to.deep.include(testTransaction);
  });

  it('should remove an added transaction', () => {
    transactionsStore.addTransaction(testTransaction);
    expect(transactionsStore.transactions).to.deep.include(testTransaction);
    transactionsStore.removeTransaction(testTransaction);
    expect(transactionsStore.transactions).not.to.deep.include(testTransaction);
  });

  it('should update a transaction', () => {
    transactionsStore.addTransaction(testTransaction);
    transactionsStore.updateTransaction(updatedTransactionPayload);
    expect(transactionsStore.transactions[0].statusName).toBe(
      TransactionStatus.FINALIZED,
    );
  });

  it('should get a transaction by hash using genlayer', async () => {
    const transactionHash =
      '0x1234567890123456789012345678901234567890' as TransactionHash;
    const transactionData = {
      id: transactionHash,
      statusName: TransactionStatus.PENDING,
    };
    mockGenlayerClient.getTransaction.mockResolvedValue(transactionData);

    const result = await transactionsStore.getTransaction(transactionHash);

    expect(mockGenlayerClient.getTransaction).toHaveBeenCalledWith({
      hash: transactionHash,
    });
    expect(result).toEqual(transactionData);
  });

  it('should clear transactions for a specific contract', () => {
    const tx1 = {
      ...testTransaction,
      hash: '0x1234567890123456789012345678901234567891' as TransactionHash,
      localContractId: 'contract-1',
    };
    const tx2 = {
      ...testTransaction,
      hash: '0x1234567890123456789012345678901234567892' as TransactionHash,
      localContractId: 'contract-2',
    };

    transactionsStore.addTransaction(tx1);
    transactionsStore.addTransaction(tx2);

    transactionsStore.clearTransactionsForContract('contract-1');

    expect(mockDb.transactions.where).toHaveBeenCalledWith('localContractId');
    expect(mockDb.transactions.equals).toHaveBeenCalledWith('contract-1');
    expect(mockDb.transactions.delete).toHaveBeenCalled();

    expect(transactionsStore.transactions).toEqual([tx2]);
  });

  it('should refresh pending transactions', async () => {
    const pendingTransaction = {
      ...testTransaction,
      statusName: TransactionStatus.PENDING,
    };
    const updatedTransaction = {
      ...pendingTransaction,
      statusName: TransactionStatus.FINALIZED,
    };

    transactionsStore.addTransaction(pendingTransaction);
    mockGenlayerClient.getTransaction.mockResolvedValue(updatedTransaction);

    await transactionsStore.refreshPendingTransactions();

    expect(mockGenlayerClient.getTransaction).toHaveBeenCalledWith({
      hash: pendingTransaction.hash,
    });
    expect(mockDb.transactions.where).toHaveBeenCalledWith('hash');
    expect(mockDb.transactions.equals).toHaveBeenCalledWith(
      pendingTransaction.hash,
    );
    expect(mockDb.transactions.modify).toHaveBeenCalledWith({
      statusName: TransactionStatus.FINALIZED,
      data: updatedTransaction,
    });
    expect(transactionsStore.transactions[0].statusName).toBe(
      TransactionStatus.FINALIZED,
    );
  });
});
