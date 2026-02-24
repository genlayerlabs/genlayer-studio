import { describe, it, expect, beforeEach, vi, type Mock } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useTransactionsStore } from '@/stores';
import { useDb, useGenlayer } from '@/hooks';
import type { TransactionItem } from '@/types';
import type { Address, TransactionHash } from 'genlayer-js/types';
import { TransactionStatus } from 'genlayer-js/types';

let mockWebSocketClientGlobal: any = {
  connected: true,
  emit: vi.fn(),
  on: vi.fn(),
  off: vi.fn(),
};

vi.mock('@/hooks', () => ({
  useGenlayer: vi.fn(),
  useRpcClient: vi.fn(),
  useWebSocketClient: vi.fn(() => mockWebSocketClientGlobal),
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
  let mockWebSocketClient: any;
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
    // Set up mocks BEFORE creating the store
    mockWebSocketClient = {
      connected: true,
      emit: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
    };
    mockWebSocketClientGlobal = mockWebSocketClient;

    setActivePinia(createPinia());
    (useGenlayer as Mock).mockReturnValue({
      client: { value: mockGenlayerClient },
    });
    (useDb as Mock).mockReturnValue(mockDb);

    // Clear mocks before creating store
    mockGenlayerClient.getTransaction.mockClear();
    mockWebSocketClient.emit.mockClear();
    mockWebSocketClient.on.mockClear();
    mockWebSocketClient.off.mockClear();

    // Now create the store - this will trigger the WebSocket setup
    transactionsStore = useTransactionsStore();
    transactionsStore.transactions = [];
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

  describe('WebSocket reconnection', () => {
    it('should set up connect event handler on store initialization', () => {
      expect(mockWebSocketClient.on).toHaveBeenCalledWith(
        'connect',
        expect.any(Function),
      );
    });

    it('should resubscribe to all transaction topics on WebSocket connect when subscriptions exist', () => {
      // Add some transactions to create subscriptions
      transactionsStore.addTransaction(testTransaction);
      const transaction2 = {
        ...testTransaction,
        hash: '0x9876543210987654321098765432109876543210' as TransactionHash,
      };
      transactionsStore.addTransaction(transaction2);

      mockWebSocketClient.emit.mockClear();

      // Simulate WebSocket connect event
      const connectHandler = mockWebSocketClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'connect',
      )?.[1];

      if (connectHandler) {
        connectHandler();
      }

      // Should resubscribe to both transaction hashes
      expect(mockWebSocketClient.emit).toHaveBeenCalledWith(
        'subscribe',
        expect.arrayContaining([testTransaction.hash, transaction2.hash]),
      );
    });

    it('should not emit subscribe on WebSocket connect when no subscriptions exist', () => {
      mockWebSocketClient.emit.mockClear();

      // Simulate WebSocket connect event without any transactions added
      const connectHandler = mockWebSocketClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'connect',
      )?.[1];

      if (connectHandler) {
        connectHandler();
      }

      expect(mockWebSocketClient.emit).not.toHaveBeenCalledWith(
        'subscribe',
        expect.anything(),
      );
    });

    it('should subscribe to transaction when adding new transaction', () => {
      mockWebSocketClient.emit.mockClear();

      transactionsStore.addTransaction(testTransaction);

      expect(mockWebSocketClient.emit).toHaveBeenCalledWith('subscribe', [
        testTransaction.hash,
      ]);
    });

    it('should unsubscribe from transaction when removing transaction', () => {
      // Add transaction first
      transactionsStore.addTransaction(testTransaction);
      mockWebSocketClient.emit.mockClear();

      // Remove transaction
      transactionsStore.removeTransaction(testTransaction);

      expect(mockWebSocketClient.emit).toHaveBeenCalledWith('unsubscribe', [
        testTransaction.hash,
      ]);
    });
  });
});
