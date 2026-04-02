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
  getRuntimeConfigNumber: vi.fn((_key: string, fallback: number) => fallback),
}));

describe('consensusStore — Studio-only sim_* calls', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('fetchFinalityWindowTime calls sim_getFinalityWindowTime', async () => {
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
});
