import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createApp } from 'vue';
import { createPinia, setActivePinia } from 'pinia';

// Stores every write through structuredClone, as IndexedDB does.
const fakeDb = vi.hoisted(() => {
  type Row = Record<string, any>;

  const createTable = (primaryKey: string[] = []) => {
    const rows: Row[] = [];
    const keyOf = (row: Row) => JSON.stringify(primaryKey.map((f) => row[f]));
    const indexOfKey = (key: string) =>
      primaryKey.length ? rows.findIndex((row) => keyOf(row) === key) : -1;
    const collection = (match: (row: Row) => boolean) => ({
      filter: (extra: (row: Row) => boolean) =>
        collection((row) => match(row) && extra(row)),
      count: async () => rows.filter(match).length,
      first: async () => rows.find(match),
      modify: async (changes: Row) => {
        const cloned = structuredClone(changes);
        rows.filter(match).forEach((row) => Object.assign(row, cloned));
      },
      delete: async () => {
        for (let i = rows.length - 1; i >= 0; i--) {
          if (match(rows[i]!)) rows.splice(i, 1);
        }
      },
    });

    return {
      rows,
      add: async (row: Row) => {
        if (indexOfKey(keyOf(row)) !== -1) {
          throw new DOMException('Key already exists', 'ConstraintError');
        }
        rows.push(structuredClone(row));
      },
      get: async (key: unknown[]) => rows[indexOfKey(JSON.stringify(key))],
      put: async (row: Row) => {
        const index = indexOfKey(keyOf(row));
        if (index === -1) rows.push(structuredClone(row));
        else rows[index] = structuredClone(row);
      },
      where: (field: string) => ({
        equals: (value: unknown) => collection((row) => row[field] === value),
      }),
      toArray: async () => structuredClone(rows),
      clear: async () => {
        rows.length = 0;
      },
    };
  };

  return {
    contractFiles: createTable(),
    deployedContracts: createTable(['contractId', 'address']),
    transactions: createTable(),
  };
});

vi.mock('@/hooks', () => ({
  useDb: vi.fn(() => fakeDb),
  useFileName: vi.fn(() => ({
    cleanupFileName: (name: string) => name,
  })),
  useGenlayer: vi.fn(() => ({ client: { value: null } })),
  useWebSocketClient: vi.fn(() => ({
    on: vi.fn(),
    off: vi.fn(),
    emit: vi.fn(),
    connected: false,
  })),
}));

vi.mock('@kyvg/vue3-notification', () => ({ notify: vi.fn() }));

import { persistStorePlugin } from '@/plugins/persistStore';
import { useContractsStore, useTransactionsStore } from '@/stores';
import { useNetworkStore } from '@/stores/network';

const STUDIO_CHAIN_ID = 61127;
const BRADBURY_CHAIN_ID = 4221;

function setupPinia() {
  const pinia = createPinia();
  pinia.use(persistStorePlugin);
  createApp({}).use(pinia);
  setActivePinia(pinia);
}

// What useSetupStores does on page load.
async function reload() {
  setupPinia();
  const contractsStore = useContractsStore();
  const transactionsStore = useTransactionsStore();
  contractsStore.setAllDeployedContracts(
    (await fakeDb.deployedContracts.toArray()) as any,
  );
  transactionsStore.setAllTransactions(
    (await fakeDb.transactions.toArray()) as any,
  );
  return { contractsStore, transactionsStore };
}

const pendingTransaction = (hash: string) =>
  ({
    hash,
    type: 'method',
    statusName: 'PENDING',
    contractAddress: '0xContract',
    localContractId: 'contract-1',
    data: {},
    decodedData: { functionName: 'update_storage', args: ['x'], kwargs: {} },
  }) as any;

describe('persistStorePlugin', () => {
  const unhandled = vi.fn();

  beforeEach(async () => {
    localStorage.clear();
    delete window.__RUNTIME_CONFIG__;
    await fakeDb.contractFiles.clear();
    await fakeDb.deployedContracts.clear();
    await fakeDb.transactions.clear();
    unhandled.mockClear();
    process.on('unhandledRejection', unhandled);
    setupPinia();
  });

  afterEach(() => {
    process.off('unhandledRejection', unhandled);
  });

  describe('transactions', () => {
    it('saves an added transaction with its chain and timestamp', async () => {
      const store = useTransactionsStore();

      store.addTransaction(pendingTransaction('0xTx1'));

      await vi.waitFor(() => expect(fakeDb.transactions.rows).toHaveLength(1));
      expect(fakeDb.transactions.rows[0]).toMatchObject({
        hash: '0xTx1',
        chainId: STUDIO_CHAIN_ID,
        addedAt: expect.any(Number),
        decodedData: { functionName: 'update_storage', args: ['x'] },
      });
      expect(unhandled).not.toHaveBeenCalled();
    });

    it('saves status updates of a stored transaction', async () => {
      const store = useTransactionsStore();
      store.addTransaction(pendingTransaction('0xTx1'));
      await vi.waitFor(() => expect(fakeDb.transactions.rows).toHaveLength(1));

      store.updateTransaction({
        hash: '0xTx1',
        statusName: 'FINALIZED',
        consensus_data: { votes: { '0xValidator': 'agree' } },
      });

      await vi.waitFor(() =>
        expect(fakeDb.transactions.rows[0]?.statusName).toBe('FINALIZED'),
      );
      expect(fakeDb.transactions.rows[0]?.data).toMatchObject({
        consensus_data: { votes: { '0xValidator': 'agree' } },
      });
      expect(unhandled).not.toHaveBeenCalled();
    });

    it('keeps the transaction list after a reload', async () => {
      useTransactionsStore().addTransaction(pendingTransaction('0xTx1'));
      await vi.waitFor(() => expect(fakeDb.transactions.rows).toHaveLength(1));

      const { transactionsStore } = await reload();

      expect(transactionsStore.transactions.map((t) => t.hash)).toEqual([
        '0xTx1',
      ]);
    });
  });

  describe('deployed contracts', () => {
    const deployed = (address: string) =>
      ({ contractId: 'contract-1', address, defaultState: '{}' }) as any;

    it('saves a deployed contract with the chain it was deployed on', async () => {
      useContractsStore().addDeployedContract(deployed('0xStudio'));

      await vi.waitFor(() =>
        expect(fakeDb.deployedContracts.rows).toHaveLength(1),
      );
      expect(fakeDb.deployedContracts.rows[0]).toEqual({
        contractId: 'contract-1',
        address: '0xStudio',
        defaultState: '{}',
        chainId: STUDIO_CHAIN_ID,
      });
    });

    it('does not show a Studio deployment on Bradbury after a reload', async () => {
      useContractsStore().addDeployedContract(deployed('0xStudio'));
      await vi.waitFor(() =>
        expect(fakeDb.deployedContracts.rows).toHaveLength(1),
      );

      const { contractsStore } = await reload();
      expect(contractsStore.deployedContracts).toHaveLength(1);

      useNetworkStore().setCurrentNetwork('testnetBradbury');
      expect(contractsStore.deployedContracts).toHaveLength(0);
    });

    it('keeps one record per chain for the same contract', async () => {
      const store = useContractsStore();
      store.addDeployedContract(deployed('0xStudio'));
      useNetworkStore().setCurrentNetwork('testnetBradbury');
      store.addDeployedContract(deployed('0xBradbury'));
      await vi.waitFor(() =>
        expect(fakeDb.deployedContracts.rows).toHaveLength(2),
      );
      store.addDeployedContract(deployed('0xBradbury2'));

      await vi.waitFor(() =>
        expect(
          fakeDb.deployedContracts.rows.map((r) => [r.address, r.chainId]),
        ).toEqual([
          ['0xStudio', STUDIO_CHAIN_ID],
          ['0xBradbury2', BRADBURY_CHAIN_ID],
        ]),
      );
    });

    it('removes only the record of the current chain', async () => {
      const store = useContractsStore();
      store.addDeployedContract(deployed('0xStudio'));
      useNetworkStore().setCurrentNetwork('testnetBradbury');
      store.addDeployedContract(deployed('0xBradbury'));
      await vi.waitFor(() =>
        expect(fakeDb.deployedContracts.rows).toHaveLength(2),
      );

      store.removeDeployedContract('contract-1');

      await vi.waitFor(() =>
        expect(fakeDb.deployedContracts.rows.map((r) => r.address)).toEqual([
          '0xStudio',
        ]),
      );
    });

    it('keeps a contract deployed at the same address on two chains', async () => {
      const store = useContractsStore();
      store.addDeployedContract(deployed('0xSame'));
      await vi.waitFor(() =>
        expect(fakeDb.deployedContracts.rows).toHaveLength(1),
      );
      useNetworkStore().setCurrentNetwork('testnetBradbury');
      store.addDeployedContract(deployed('0xSame'));
      await vi.waitFor(() =>
        expect(fakeDb.deployedContracts.rows[0]?.chainId).toBeUndefined(),
      );
      expect(fakeDb.deployedContracts.rows).toHaveLength(1);
      expect(unhandled).not.toHaveBeenCalled();

      const { contractsStore } = await reload();
      useNetworkStore().setCurrentNetwork('localnet');
      expect(contractsStore.deployedContracts).toHaveLength(1);
      useNetworkStore().setCurrentNetwork('testnetBradbury');
      expect(contractsStore.deployedContracts).toHaveLength(1);
    });

    it('adopts a record saved without a chain when the contract is redeployed', async () => {
      fakeDb.deployedContracts.rows.push({
        contractId: 'contract-1',
        address: '0xOld',
        defaultState: '{}',
      });

      useContractsStore().addDeployedContract(deployed('0xNew'));

      await vi.waitFor(() =>
        expect(fakeDb.deployedContracts.rows).toEqual([
          {
            contractId: 'contract-1',
            address: '0xNew',
            defaultState: '{}',
            chainId: STUDIO_CHAIN_ID,
          },
        ]),
      );
    });
  });
});
