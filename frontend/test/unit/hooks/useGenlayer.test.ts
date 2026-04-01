/**
 * Behavioral snapshot tests for useGenlayer.
 *
 * These tests document the current client creation/recreation behavior
 * so the multi-network refactor doesn't break existing Studio flows.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockCreateClient = vi.fn(() => ({ mock: true }));
const mockCreateAccount = vi.fn(() => ({
  address: '0xLocalAccount',
  type: 'local',
}));
const mockGetRuntimeConfig = vi.fn((key: string, fallback: string) => fallback);

vi.mock('genlayer-js', () => ({
  createClient: (...args: any[]) => mockCreateClient(...args),
  createAccount: (...args: any[]) => mockCreateAccount(...args),
}));

vi.mock('genlayer-js/chains', () => ({
  localnet: {
    id: 61127,
    name: 'localnet',
    rpcUrls: { default: { http: ['http://127.0.0.1:4000/api'] } },
  },
  studionet: {
    id: 61999,
    name: 'studionet',
    rpcUrls: { default: { http: ['https://studio.genlayer.com/api'] } },
  },
  testnetAsimov: {
    id: 4221,
    name: 'testnetAsimov',
    rpcUrls: { default: { http: ['https://asimov.genlayer.com'] } },
  },
}));

vi.mock('@/utils/runtimeConfig', () => ({
  getRuntimeConfig: (...args: any[]) => mockGetRuntimeConfig(...args),
}));

vi.mock('@/stores', () => ({
  useAccountsStore: vi.fn(() => ({
    selectedAccount: { address: '0xTest', type: 'local', privateKey: '0xkey' },
  })),
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
    mockGetRuntimeConfig.mockImplementation(
      (key: string, fallback: string) => fallback,
    );
  });

  it('should create client with localnet chain by default', async () => {
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    useGenlayer();

    expect(mockCreateClient).toHaveBeenCalled();
    const opts = mockCreateClient.mock.calls[0][0];
    expect(opts.chain.name).toBe('localnet');
  });

  it('should use VITE_GENLAYER_NETWORK to select chain', async () => {
    mockGetRuntimeConfig.mockImplementation((key: string, fallback: string) => {
      if (key === 'VITE_GENLAYER_NETWORK') return 'studionet';
      return fallback;
    });

    vi.resetModules();
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    useGenlayer();

    const opts = mockCreateClient.mock.calls[0][0];
    expect(opts.chain.name).toBe('studionet');
  });

  it('should pass VITE_JSON_RPC_SERVER_URL as endpoint', async () => {
    mockGetRuntimeConfig.mockImplementation((key: string, fallback: string) => {
      if (key === 'VITE_JSON_RPC_SERVER_URL') return 'http://custom:4000/api';
      return fallback;
    });

    vi.resetModules();
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    useGenlayer();

    const opts = mockCreateClient.mock.calls[0][0];
    expect(opts.endpoint).toBe('http://custom:4000/api');
  });

  it('should create local account from private key for local accounts', async () => {
    const { useGenlayer } = await import('@/hooks/useGenlayer');
    useGenlayer();

    expect(mockCreateAccount).toHaveBeenCalledWith('0xkey');
  });
});
