/**
 * Behavioral tests for useGenlayer.
 *
 * Documents client creation/recreation, including reactive network switching
 * through the network store.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockCreateClient = vi.fn(() => ({ mock: true }));
const mockCreateAccount = vi.fn(() => ({
  address: '0xLocalAccount',
  type: 'local',
}));

const localnetChain = {
  id: 61127,
  name: 'localnet',
  isStudio: true,
  rpcUrls: { default: { http: ['http://127.0.0.1:4000/api'] } },
};
const studionetChain = {
  id: 61999,
  name: 'studionet',
  isStudio: true,
  rpcUrls: { default: { http: ['https://studio.genlayer.com/api'] } },
};
const testnetBradburyChain = {
  id: 4221,
  name: 'Genlayer Bradbury Testnet',
  isStudio: false,
  rpcUrls: { default: { http: ['https://rpc.testnet-chain.genlayer.com'] } },
};

const mockNetworkStore: {
  chain: typeof localnetChain;
  rpcUrl: string;
  transactionRpcUrl?: string;
  currentNetwork: string;
} = {
  chain: localnetChain,
  rpcUrl: 'http://127.0.0.1:4000/api',
  currentNetwork: 'localnet',
};

vi.mock('genlayer-js', () => ({
  createClient: (...args: any[]) => mockCreateClient(...args),
  createAccount: (...args: any[]) => mockCreateAccount(...args),
}));

vi.mock('@/stores', () => ({
  useAccountsStore: vi.fn(() => ({
    selectedAccount: { address: '0xTest', type: 'local', privateKey: '0xkey' },
  })),
}));

vi.mock('@/stores/network', () => ({
  useNetworkStore: vi.fn(() => mockNetworkStore),
}));

vi.mock('@/hooks/useWallet', () => ({
  useWallet: vi.fn(() => ({
    walletProvider: { value: undefined },
  })),
}));

vi.mock('vue', async () => {
  const actual = await vi.importActual('vue');
  return {
    ...actual,
    markRaw: (v: any) => v,
  };
});

describe('useGenlayer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNetworkStore.chain = localnetChain;
    mockNetworkStore.rpcUrl = 'http://127.0.0.1:4000/api';
    mockNetworkStore.transactionRpcUrl = undefined;
    mockNetworkStore.currentNetwork = 'localnet';
  });

  it('creates a client using the chain from the network store', async () => {
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    useGenlayer();

    expect(mockCreateClient).toHaveBeenCalled();
    const opts = mockCreateClient.mock.calls[0][0];
    expect(opts.chain.name).toBe('localnet');
  });

  it('picks up studionet when the network store reports studionet', async () => {
    mockNetworkStore.chain = studionetChain;
    mockNetworkStore.rpcUrl = 'https://studio.genlayer.com/api';
    mockNetworkStore.currentNetwork = 'studionet';

    vi.resetModules();
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    useGenlayer();

    const opts = mockCreateClient.mock.calls[0][0];
    expect(opts.chain.name).toBe('studionet');
  });

  it('passes the network store rpc URL as the client endpoint', async () => {
    mockNetworkStore.rpcUrl = 'http://custom:4000/api';

    vi.resetModules();
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    useGenlayer();

    const opts = mockCreateClient.mock.calls[0][0];
    expect(opts.endpoint).toBe('http://custom:4000/api');
  });

  it('honors a non-Studio chain (e.g. Bradbury testnet)', async () => {
    mockNetworkStore.chain = testnetBradburyChain;
    mockNetworkStore.rpcUrl = 'https://rpc.bradbury.genlayer.com';
    mockNetworkStore.currentNetwork = 'testnetBradbury';

    vi.resetModules();
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    useGenlayer();

    const opts = mockCreateClient.mock.calls[0][0];
    expect(opts.chain.isStudio).toBe(false);
    expect(opts.endpoint).toBe('https://rpc.bradbury.genlayer.com');
  });

  it('reuses one client when reads and transactions share an RPC', async () => {
    mockNetworkStore.transactionRpcUrl = 'http://127.0.0.1:4000/api';

    vi.resetModules();
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    const { client, transactionClient } = useGenlayer();

    expect(mockCreateClient).toHaveBeenCalledTimes(1);
    expect(transactionClient.value).toBe(client.value);
  });

  it('builds a separate transaction client for the chain RPC', async () => {
    mockNetworkStore.chain = testnetBradburyChain;
    mockNetworkStore.rpcUrl = 'https://rpc.bradbury.genlayer.com';
    mockNetworkStore.transactionRpcUrl =
      'https://rpc.testnet-chain.genlayer.com';
    mockNetworkStore.currentNetwork = 'testnetBradbury';

    vi.resetModules();
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    const { client, transactionClient } = useGenlayer();

    expect(mockCreateClient).toHaveBeenCalledTimes(2);
    expect(mockCreateClient.mock.calls[0][0].endpoint).toBe(
      'https://rpc.bradbury.genlayer.com',
    );
    expect(mockCreateClient.mock.calls[1][0].endpoint).toBe(
      'https://rpc.testnet-chain.genlayer.com',
    );
    expect(mockCreateClient.mock.calls[1][0].account).toEqual(
      mockCreateClient.mock.calls[0][0].account,
    );
    expect(transactionClient.value).not.toBe(client.value);
  });

  it('creates a local account from the private key for local accounts', async () => {
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    useGenlayer();

    expect(mockCreateAccount).toHaveBeenCalledWith('0xkey');
  });
});
