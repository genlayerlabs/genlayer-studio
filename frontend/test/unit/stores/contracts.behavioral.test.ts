/**
 * Behavioral snapshot tests for contractsStore.
 *
 * Documents deployed contract registration and the current lack of
 * network scoping (no chainId). The multi-network refactor must
 * add chainId scoping or clear-on-switch.
 */
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { useContractsStore } from '@/stores';
import { createPinia, setActivePinia } from 'pinia';

vi.mock('@/hooks', () => ({
  useFileName: vi.fn(() => ({
    getFileName: vi.fn((name: string) =>
      name.endsWith('.py') ? name : name + '.py',
    ),
  })),
  useWebSocketClient: vi.fn(() => ({
    on: vi.fn(),
    off: vi.fn(),
    id: 'mock-ws',
    connected: true,
  })),
  useDb: vi.fn(() => ({
    deployedContracts: {
      put: vi.fn(),
      where: vi.fn(() => ({
        equals: vi.fn(() => ({
          delete: vi.fn(),
        })),
      })),
      toArray: vi.fn(() => Promise.resolve([])),
    },
    contractFiles: {
      put: vi.fn(),
      delete: vi.fn(),
      toArray: vi.fn(() => Promise.resolve([])),
      clear: vi.fn(),
    },
    transactions: {
      clear: vi.fn(),
    },
  })),
}));

describe('contractsStore — deployed contract behavior', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('addDeployedContract stores by contractId', () => {
    const store = useContractsStore();
    store.addDeployedContract({
      contractId: 'my-contract',
      address: '0xAddr',
      defaultState: {},
    });

    expect(store.deployedContracts).toContainEqual(
      expect.objectContaining({
        contractId: 'my-contract',
        address: '0xAddr',
      }),
    );
  });

  it('deployed contracts have NO chainId field (current limitation)', () => {
    const store = useContractsStore();
    store.addDeployedContract({
      contractId: 'test',
      address: '0x1',
      defaultState: {},
    });

    const deployed = store.deployedContracts.find(
      (d: any) => d.contractId === 'test',
    );
    expect(deployed).toBeDefined();
    // This documents the current state — no chainId scoping
    expect((deployed as any).chainId).toBeUndefined();
  });

  it('removeDeployedContract removes by contractId', () => {
    const store = useContractsStore();
    store.addDeployedContract({
      contractId: 'to-remove',
      address: '0x1',
      defaultState: {},
    });
    expect(store.deployedContracts.length).toBe(1);

    store.removeDeployedContract('to-remove');
    expect(store.deployedContracts.length).toBe(0);
  });

  it('addDeployedContract replaces existing with same contractId', () => {
    const store = useContractsStore();
    store.addDeployedContract({
      contractId: 'dup',
      address: '0xOld',
      defaultState: {},
    });
    store.addDeployedContract({
      contractId: 'dup',
      address: '0xNew',
      defaultState: {},
    });

    const matches = store.deployedContracts.filter(
      (d: any) => d.contractId === 'dup',
    );
    expect(matches.length).toBe(1);
    expect(matches[0].address).toBe('0xNew');
  });
});
