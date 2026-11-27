/**
 * Integration test for the wallet chain check on transaction submission.
 *
 * Wires the REAL useChainEnforcer into the REAL useContractQueries and drives
 * them with a simulated EIP-1193 wallet provider (MetaMask-like). Only the
 * outer boundaries are mocked: stores, the genlayer-js client, and the
 * notification sink. This covers the flow
 *   connected provider → chain enforcer → deploy/write → user notification.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ref } from 'vue';

const STUDIO_CHAIN_ID = 61999;
const STUDIO_CHAIN_NAME = 'Genlayer Studio Network';
const WRONG_CHAIN_ID = 1; // e.g. MetaMask left on Ethereum mainnet

const toHex = (n: number) => `0x${n.toString(16)}`;

/** Minimal EIP-1193 provider simulating a MetaMask-like wallet. */
function createWallet(
  initialChainId: number,
  behaviour: {
    rejectSwitch?: boolean;
    failAdd?: boolean;
    knownChains?: number[];
  } = {},
) {
  let chainId = initialChainId;
  const known = new Set(behaviour.knownChains ?? [initialChainId]);
  const request = vi.fn(
    async ({ method, params }: { method: string; params?: any[] }) => {
      switch (method) {
        case 'eth_chainId':
          return toHex(chainId);
        case 'wallet_switchEthereumChain': {
          if (behaviour.rejectSwitch) {
            throw Object.assign(new Error('User rejected the request.'), {
              code: 4001,
            });
          }
          const target = parseInt(params![0].chainId, 16);
          if (!known.has(target)) {
            throw Object.assign(new Error('Unrecognized chain ID.'), {
              code: 4902,
            });
          }
          chainId = target;
          return null;
        }
        case 'wallet_addEthereumChain': {
          if (behaviour.failAdd) {
            throw Object.assign(new Error('Internal JSON-RPC error.'), {
              code: -32603,
            });
          }
          const target = parseInt(params![0].chainId, 16);
          known.add(target);
          chainId = target;
          return null;
        }
        default:
          throw new Error(`Unsupported method ${method}`);
      }
    },
  );
  return { request, currentChainId: () => chainId };
}

const mockWalletProvider = ref<any>(undefined);

const mockDeployContract = vi.fn(() => Promise.resolve('0xDeployHash'));
const mockWriteContract = vi.fn(() => Promise.resolve('0xWriteHash'));
const mockGenlayerClient = {
  deployContract: mockDeployContract,
  writeContract: mockWriteContract,
};

const mockNetworkStore = {
  isStudio: false,
  chainId: STUDIO_CHAIN_ID,
  chain: {
    id: STUDIO_CHAIN_ID,
    name: STUDIO_CHAIN_NAME,
    rpcUrls: { default: { http: ['http://127.0.0.1:4000/api'] } },
    nativeCurrency: { name: 'GEN Token', symbol: 'GEN', decimals: 18 },
  },
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
  selectedAccount: { address: '0xUser', type: 'external' } as any,
};

vi.mock('@/stores', () => ({
  useConsensusStore: vi.fn(() => ({
    feeSettings: {
      leaderTimeunitsAllocation: '',
      validatorTimeunitsAllocation: '',
      appealRounds: '',
      rotations: '',
    },
  })),
  useContractsStore: vi.fn(() => mockContractsStore),
  useTransactionsStore: vi.fn(() => mockTransactionsStore),
  useAccountsStore: vi.fn(() => mockAccountsStore),
}));

vi.mock('@/stores/network', () => ({
  useNetworkStore: vi.fn(() => mockNetworkStore),
}));

// The enforcer obtains the provider through useWallet.
vi.mock('@/hooks/useWallet', () => ({
  useWallet: vi.fn(() => ({ walletProvider: mockWalletProvider })),
}));

vi.mock('@/hooks', async () => {
  // Real chain enforcer; other hooks are stubbed.
  const { useChainEnforcer } = await vi.importActual<
    typeof import('@/hooks/useChainEnforcer')
  >('@/hooks/useChainEnforcer');
  return {
    useChainEnforcer,
    useEventTracking: vi.fn(() => ({ trackEvent: vi.fn() })),
    useGenlayer: vi.fn(() => ({
      client: ref(mockGenlayerClient),
      transactionClient: ref(mockGenlayerClient),
    })),
    useRpcClient: vi.fn(() => ({})),
    useWallet: vi.fn(() => ({ walletProvider: mockWalletProvider })),
  };
});

vi.mock('@/hooks/useMockContractData', () => ({
  useMockContractData: vi.fn(() => ({
    mockContractId: 'mock-id',
    mockContractSchema: {},
  })),
}));

vi.mock('@kyvg/vue3-notification', () => ({ notify: vi.fn() }));

vi.mock('@tanstack/vue-query', () => ({
  useQuery: vi.fn(() => ({
    data: ref(null),
    isLoading: ref(false),
    refetch: vi.fn(),
  })),
  useQueryClient: vi.fn(() => ({ invalidateQueries: vi.fn() })),
}));

vi.mock('@vueuse/core', () => ({
  useDebounceFn: vi.fn((fn: any) => fn),
}));

import { notify } from '@kyvg/vue3-notification';
import { useContractQueries } from '@/hooks/useContractQueries';

const EXPECTED_MESSAGE =
  `Your wallet is connected to chain ID ${WRONG_CHAIN_ID}, but ` +
  `${STUDIO_CHAIN_NAME} uses chain ID ${STUDIO_CHAIN_ID}. Switch your wallet ` +
  `to ${STUDIO_CHAIN_NAME} (chain ID ${STUDIO_CHAIN_ID}) to send transactions.`;

describe('wallet chain enforcement — provider → enforcer → tx → notification', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockWalletProvider.value = undefined;
  });

  describe('deploy', () => {
    it('blocks the deploy and tells the user both chain IDs when the switch is rejected', async () => {
      const wallet = createWallet(WRONG_CHAIN_ID, {
        rejectSwitch: true,
        knownChains: [WRONG_CHAIN_ID, STUDIO_CHAIN_ID],
      });
      mockWalletProvider.value = wallet;
      const { deployContract } = useContractQueries();

      await expect(
        deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3),
      ).rejects.toThrow(EXPECTED_MESSAGE);

      expect(wallet.request).toHaveBeenCalledWith({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: toHex(STUDIO_CHAIN_ID) }],
      });
      expect(wallet.currentChainId()).toBe(WRONG_CHAIN_ID);
      expect(mockDeployContract).not.toHaveBeenCalled();
      expect(mockTransactionsStore.addTransaction).not.toHaveBeenCalled();
      expect(notify).toHaveBeenCalledWith({
        type: 'error',
        title: 'Error deploying contract',
        text: EXPECTED_MESSAGE,
      });
      const shown = vi.mocked(notify).mock.calls[0][0] as { text: string };
      expect(shown.text).toContain(`chain ID ${WRONG_CHAIN_ID}`);
      expect(shown.text).toContain(`chain ID ${STUDIO_CHAIN_ID}`);
    });

    it('keeps the chain explanation when the wallet fails to add the network', async () => {
      const wallet = createWallet(WRONG_CHAIN_ID, { failAdd: true });
      mockWalletProvider.value = wallet;
      const { deployContract } = useContractQueries();

      const error = await deployContract(
        { args: [], kwargs: {} },
        'NORMAL' as any,
        3,
      ).catch((e) => e);

      expect(error.message).toBe(EXPECTED_MESSAGE);
      expect((error as Error).cause).toMatchObject({ code: -32603 });
      expect(wallet.currentChainId()).toBe(WRONG_CHAIN_ID);
      expect(mockDeployContract).not.toHaveBeenCalled();
      expect(mockTransactionsStore.addTransaction).not.toHaveBeenCalled();
      expect(notify).toHaveBeenCalledWith({
        type: 'error',
        title: 'Error deploying contract',
        text: EXPECTED_MESSAGE,
      });
    });

    it('switches the wallet to the expected chain and then deploys', async () => {
      const wallet = createWallet(WRONG_CHAIN_ID, {
        knownChains: [WRONG_CHAIN_ID, STUDIO_CHAIN_ID],
      });
      mockWalletProvider.value = wallet;
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(wallet.currentChainId()).toBe(STUDIO_CHAIN_ID);
      expect(mockDeployContract).toHaveBeenCalledTimes(1);
      const switchOrder = wallet.request.mock.invocationCallOrder[1];
      expect(switchOrder).toBeLessThan(
        mockDeployContract.mock.invocationCallOrder[0],
      );
      expect(mockTransactionsStore.addTransaction).toHaveBeenCalledWith(
        expect.objectContaining({ hash: '0xDeployHash', type: 'deploy' }),
      );
      expect(notify).not.toHaveBeenCalledWith(
        expect.objectContaining({ type: 'error' }),
      );
    });

    it('adds the network when the wallet does not know it, then deploys', async () => {
      const wallet = createWallet(WRONG_CHAIN_ID);
      mockWalletProvider.value = wallet;
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(wallet.request).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'wallet_addEthereumChain' }),
      );
      expect(wallet.currentChainId()).toBe(STUDIO_CHAIN_ID);
      expect(mockDeployContract).toHaveBeenCalledTimes(1);
    });

    it('shows only a short message for a non-chain RPC failure, not the request body', async () => {
      mockWalletProvider.value = createWallet(STUDIO_CHAIN_ID);
      const body = '0xdeadbeef'.repeat(2000);
      const viemError = Object.assign(
        new Error(
          `HTTP request failed.\n\nRequest body: {"params":["${body}"]}`,
        ),
        { shortMessage: 'HTTP request failed.', details: 'insufficient funds' },
      );
      mockDeployContract.mockRejectedValueOnce(viemError);
      const { deployContract } = useContractQueries();

      await expect(
        deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3),
      ).rejects.toThrow();

      const shown = vi.mocked(notify).mock.calls[0][0] as { text: string };
      expect(shown.text).toBe('insufficient funds');
      expect(shown.text).not.toContain('Request body');
      expect(shown.text).not.toContain('0xdeadbeef');
    });

    it('deploys without touching the wallet network when already on the right chain', async () => {
      const wallet = createWallet(STUDIO_CHAIN_ID);
      mockWalletProvider.value = wallet;
      const { deployContract } = useContractQueries();

      await deployContract({ args: [], kwargs: {} }, 'NORMAL' as any, 3);

      expect(wallet.request).toHaveBeenCalledTimes(1);
      expect(wallet.request).toHaveBeenCalledWith({ method: 'eth_chainId' });
      expect(mockDeployContract).toHaveBeenCalledTimes(1);
    });
  });

  describe('write', () => {
    const writeArgs = {
      method: 'update',
      args: { args: [], kwargs: {} },
      executionMode: 'NORMAL' as any,
    };

    it('does not submit the write and surfaces both chain IDs when the switch is rejected', async () => {
      mockWalletProvider.value = createWallet(WRONG_CHAIN_ID, {
        rejectSwitch: true,
        knownChains: [WRONG_CHAIN_ID, STUDIO_CHAIN_ID],
      });
      const { callWriteMethod } = useContractQueries();

      // The caller (ContractMethodItem) shows this message in its error toast.
      await expect(callWriteMethod(writeArgs)).rejects.toThrow(
        EXPECTED_MESSAGE,
      );
      expect(mockWriteContract).not.toHaveBeenCalled();
      expect(mockTransactionsStore.addTransaction).not.toHaveBeenCalled();
    });

    it('switches the wallet and submits the write', async () => {
      const wallet = createWallet(WRONG_CHAIN_ID, {
        knownChains: [WRONG_CHAIN_ID, STUDIO_CHAIN_ID],
      });
      mockWalletProvider.value = wallet;
      const { callWriteMethod } = useContractQueries();

      await callWriteMethod(writeArgs);

      expect(wallet.currentChainId()).toBe(STUDIO_CHAIN_ID);
      expect(mockWriteContract).toHaveBeenCalledTimes(1);
    });
  });
});
