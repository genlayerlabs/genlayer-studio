/**
 * Behavioral snapshot tests for transactionsStore.
 *
 * Documents transaction lifecycle and the current lack of network scoping.
 */
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { useTransactionsStore } from '@/stores';
import { createPinia, setActivePinia } from 'pinia';

const mockGenlayerClient = {
  getTransaction: vi.fn(() => Promise.resolve({ hash: '0x1', status: 5 })),
  appealTransaction: vi.fn(() => Promise.resolve('0x1')),
};

const mockDb = {
  transactions: {
    put: vi.fn(),
    where: vi.fn(() => ({
      equals: vi.fn(() => ({
        delete: vi.fn(),
      })),
    })),
    toArray: vi.fn(() => Promise.resolve([])),
  },
};

vi.mock('@/hooks', () => ({
  useGenlayer: vi.fn(() => ({
    client: { value: mockGenlayerClient },
  })),
  useRpcClient: vi.fn(() => ({
    cancelTransaction: vi.fn(() => Promise.resolve({ status: 'CANCELED' })),
  })),
  useWebSocketClient: vi.fn(() => ({
    on: vi.fn(),
    off: vi.fn(),
    emit: vi.fn(),
    id: 'mock-ws',
    connected: true,
  })),
  useDb: vi.fn(() => mockDb),
}));

describe('transactionsStore — behavioral contract', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('addTransaction adds to transactions array', () => {
    const store = useTransactionsStore();
    store.addTransaction({
      hash: '0xTx1',
      type: 'method',
      contractAddress: '0xC',
      localContractId: 'c1',
      statusName: 'PENDING',
      data: {},
    } as any);

    expect(store.transactions).toContainEqual(
      expect.objectContaining({ hash: '0xTx1' }),
    );
  });

  it('transactions have NO chainId field (current limitation)', () => {
    const store = useTransactionsStore();
    store.addTransaction({
      hash: '0xTx2',
      type: 'deploy',
      contractAddress: '0xC',
      localContractId: 'c2',
      statusName: 'PENDING',
      data: {},
    } as any);

    const tx = store.transactions.find((t: any) => t.hash === '0xTx2');
    expect(tx).toBeDefined();
    expect((tx as any).chainId).toBeUndefined();
  });

  it('updateTransaction replaces existing by hash', () => {
    const store = useTransactionsStore();
    store.addTransaction({
      hash: '0xUpdate',
      type: 'method',
      contractAddress: '0xC',
      localContractId: 'c1',
      statusName: 'PENDING',
      data: {},
    } as any);

    store.updateTransaction({
      hash: '0xUpdate',
      type: 'method',
      contractAddress: '0xC',
      localContractId: 'c1',
      statusName: 'ACCEPTED',
      data: { status: 'ACCEPTED' },
    } as any);

    const tx = store.transactions.find((t: any) => t.hash === '0xUpdate');
    expect(tx?.statusName).toBe('ACCEPTED');
  });

  it('removeTransaction removes by hash', () => {
    const store = useTransactionsStore();
    store.addTransaction({
      hash: '0xRemove',
      type: 'method',
      contractAddress: '0xC',
      localContractId: 'c1',
      statusName: 'PENDING',
      data: {},
    } as any);

    store.removeTransaction({ hash: '0xRemove' } as any);
    expect(
      store.transactions.find((t: any) => t.hash === '0xRemove'),
    ).toBeUndefined();
  });

  it('getTransaction fetches from genlayer client', async () => {
    const store = useTransactionsStore();
    const result = await store.getTransaction('0xFetch');

    expect(mockGenlayerClient.getTransaction).toHaveBeenCalledWith(
      expect.objectContaining({ hash: '0xFetch' }),
    );
  });

  it('clearTransactionsForContract removes all txs for a contract', () => {
    const store = useTransactionsStore();
    store.addTransaction({
      hash: '0xA',
      localContractId: 'c1',
      contractAddress: '0xC',
      type: 'method',
      statusName: 'PENDING',
      data: {},
    } as any);
    store.addTransaction({
      hash: '0xB',
      localContractId: 'c1',
      contractAddress: '0xC',
      type: 'deploy',
      statusName: 'PENDING',
      data: {},
    } as any);
    store.addTransaction({
      hash: '0xC',
      localContractId: 'c2',
      contractAddress: '0xD',
      type: 'method',
      statusName: 'PENDING',
      data: {},
    } as any);

    store.clearTransactionsForContract('c1');

    expect(store.transactions.length).toBe(1);
    expect(store.transactions[0].hash).toBe('0xC');
  });
});
