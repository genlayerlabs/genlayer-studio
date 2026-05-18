/**
 * Behavioral snapshot tests for consensusStore.
 *
 * Documents that finality window management calls sim_* methods.
 * Studio-only — must be gated on isStudio.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useConsensusStore } from '@/stores';
import { createPinia, setActivePinia } from 'pinia';

const mockRpcService = {
  getFinalityWindowTime: vi.fn(() => Promise.resolve(300)),
  setFinalityWindowTime: vi.fn(() => Promise.resolve()),
};

vi.mock('@/hooks', () => ({
  useRpcClient: vi.fn(() => mockRpcService),
  useWebSocketClient: vi.fn(() => ({
    on: vi.fn(),
    off: vi.fn(),
    emit: vi.fn(),
    id: 'mock-ws',
    connected: true,
  })),
}));

vi.mock('@/utils/runtimeConfig', () => ({
  getRuntimeConfig: vi.fn((_key: string, fallback: string) => fallback),
  getRuntimeConfigNumber: vi.fn((_key: string, fallback: number) => fallback),
  getRuntimeConfigBoolean: vi.fn((_key: string, fallback: boolean) => fallback),
}));

describe('consensusStore — Studio-only sim_* calls', () => {
  beforeEach(async () => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    // Ensure we're on a Studio network for the existing cases (Bradbury is
    // covered by the non-Studio guard test below).
    const { useNetworkStore } = await import('@/stores/network');
    useNetworkStore().setCurrentNetwork('localnet');
  });

  it('fetchFinalityWindowTime calls sim_getFinalityWindowTime on Studio', async () => {
    const store = useConsensusStore();
    await store.fetchFinalityWindowTime();
    expect(mockRpcService.getFinalityWindowTime).toHaveBeenCalled();
  });

  it('fetchFinalityWindowTime updates store state', async () => {
    mockRpcService.getFinalityWindowTime.mockResolvedValue(600);
    const store = useConsensusStore();
    await store.fetchFinalityWindowTime();
    expect(store.finalityWindow).toBe(600);
  });

  it('setFinalityWindowTime calls sim_setFinalityWindowTime', async () => {
    const store = useConsensusStore();
    await store.setFinalityWindowTime(120);
    expect(mockRpcService.setFinalityWindowTime).toHaveBeenCalledWith(120);
  });

  it('fetchFinalityWindowTime skips the RPC on non-Studio networks', async () => {
    const { useNetworkStore } = await import('@/stores/network');
    useNetworkStore().setCurrentNetwork('testnetBradbury');
    const store = useConsensusStore();
    await store.fetchFinalityWindowTime();
    expect(mockRpcService.getFinalityWindowTime).not.toHaveBeenCalled();
  });
});
