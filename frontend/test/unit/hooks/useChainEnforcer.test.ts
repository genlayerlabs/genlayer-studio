import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ref } from 'vue';

const mockRequest = vi.fn();
const mockWalletProvider = ref<any>({ request: mockRequest });

const mockAccountsStore = {
  selectedAccount: { address: '0xUser', type: 'external' } as any,
};

const mockNetworkStore = {
  chainId: 61999,
  isStudio: true,
  chain: {
    id: 61999,
    name: 'Genlayer Studio Network',
    rpcUrls: { default: { http: ['http://127.0.0.1:4000/api'] } },
    nativeCurrency: { name: 'GEN Token', symbol: 'GEN', decimals: 18 },
  },
};

vi.mock('@/stores', () => ({
  useAccountsStore: vi.fn(() => mockAccountsStore),
}));

vi.mock('@/stores/network', () => ({
  useNetworkStore: vi.fn(() => mockNetworkStore),
}));

vi.mock('@/hooks/useWallet', () => ({
  useWallet: vi.fn(() => ({ walletProvider: mockWalletProvider })),
}));

vi.mock('@/utils/runtimeConfig', () => ({
  getRuntimeConfig: vi.fn((_key: string, fallback: string) => fallback),
}));

import {
  useChainEnforcer,
  chainMismatchMessage,
  ChainMismatchError,
} from '@/hooks/useChainEnforcer';

const STUDIO_HEX = '0xf22f'; // 61999
const MAINNET_HEX = '0x1';

function rpcError(code: number, message = 'rpc error') {
  return Object.assign(new Error(message), { code });
}

/** Wallet whose chain changes only when the given switch handler allows it. */
function walletOn(
  initialHex: string,
  onSwitch: (setChain: (hex: string) => void) => void = (set) =>
    set(STUDIO_HEX),
  onAdd: (setChain: (hex: string) => void) => void = (set) => set(STUDIO_HEX),
) {
  let current = initialHex;
  const setChain = (hex: string) => {
    current = hex;
  };
  mockRequest.mockImplementation(async ({ method }: { method: string }) => {
    if (method === 'eth_chainId') return current;
    if (method === 'wallet_switchEthereumChain') return onSwitch(setChain);
    if (method === 'wallet_addEthereumChain') return onAdd(setChain);
    throw new Error(`unexpected ${method}`);
  });
}

describe('chainMismatchMessage', () => {
  it('names the wallet chain and the expected network', () => {
    expect(chainMismatchMessage(1, 61999, 'Genlayer Studio Network')).toBe(
      'Your wallet is connected to chain ID 1, but Genlayer Studio Network ' +
        'uses chain ID 61999. Switch your wallet to Genlayer Studio Network ' +
        '(chain ID 61999) to send transactions.',
    );
  });

  it('handles an unparseable wallet chain id', () => {
    expect(chainMismatchMessage(NaN, 61999, 'Studio')).toContain(
      'connected to an unknown chain',
    );
  });
});

describe('useChainEnforcer.ensureCorrectChain', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRequest.mockReset();
    mockAccountsStore.selectedAccount = { address: '0xUser', type: 'external' };
    mockWalletProvider.value = { request: mockRequest };
  });

  it('is a no-op for local accounts', async () => {
    mockAccountsStore.selectedAccount = { address: '0xUser', type: 'local' };
    await useChainEnforcer().ensureCorrectChain();
    expect(mockRequest).not.toHaveBeenCalled();
  });

  it('does nothing when the wallet is already on the expected chain', async () => {
    walletOn(STUDIO_HEX);
    await useChainEnforcer().ensureCorrectChain();
    expect(mockRequest).toHaveBeenCalledTimes(1);
    expect(mockRequest).toHaveBeenCalledWith({ method: 'eth_chainId' });
  });

  it('switches the wallet when it is on another chain', async () => {
    walletOn(MAINNET_HEX);
    await useChainEnforcer().ensureCorrectChain();
    expect(mockRequest).toHaveBeenCalledWith({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: STUDIO_HEX }],
    });
  });

  it('adds the network when the wallet does not know it (4902)', async () => {
    walletOn(MAINNET_HEX, () => {
      throw rpcError(4902);
    });
    await useChainEnforcer().ensureCorrectChain();
    expect(mockRequest).toHaveBeenCalledWith({
      method: 'wallet_addEthereumChain',
      params: [
        {
          chainId: STUDIO_HEX,
          chainName: 'Genlayer Studio Network',
          nativeCurrency: mockNetworkStore.chain.nativeCurrency,
          rpcUrls: ['http://127.0.0.1:4000/api'],
        },
      ],
    });
  });

  it('explains the mismatch when the user rejects the switch (4001)', async () => {
    walletOn(MAINNET_HEX, () => {
      throw rpcError(4001);
    });
    await expect(useChainEnforcer().ensureCorrectChain()).rejects.toThrow(
      chainMismatchMessage(1, 61999, 'Genlayer Studio Network'),
    );
  });

  it('explains the mismatch when the user rejects adding the network', async () => {
    walletOn(
      MAINNET_HEX,
      () => {
        throw rpcError(4902);
      },
      () => {
        throw rpcError(4001);
      },
    );
    await expect(useChainEnforcer().ensureCorrectChain()).rejects.toThrow(
      chainMismatchMessage(1, 61999, 'Genlayer Studio Network'),
    );
  });

  it('detects a wallet that stays on the wrong chain after switching', async () => {
    // Misconfigured wallet: switch call resolves but chain does not change.
    walletOn(MAINNET_HEX, () => undefined);
    await expect(useChainEnforcer().ensureCorrectChain()).rejects.toThrow(
      chainMismatchMessage(1, 61999, 'Genlayer Studio Network'),
    );
  });

  it('uses an explicit target over the network store', async () => {
    walletOn(STUDIO_HEX, () => undefined);
    await expect(
      useChainEnforcer().ensureCorrectChain({
        chainId: 4221,
        chainName: 'Bradbury',
        rpcUrl: 'https://example.invalid',
      }),
    ).rejects.toThrow(chainMismatchMessage(61999, 4221, 'Bradbury'));
  });

  it('explains the mismatch and keeps the wallet error as cause on unexpected failures', async () => {
    const err = rpcError(-32603, 'internal');
    walletOn(MAINNET_HEX, () => {
      throw err;
    });
    const result = await useChainEnforcer()
      .ensureCorrectChain()
      .catch((e) => e);
    expect(result).toBeInstanceOf(ChainMismatchError);
    expect(result.message).toContain('chain ID 1');
    expect(result.cause).toBe(err);
  });
});
