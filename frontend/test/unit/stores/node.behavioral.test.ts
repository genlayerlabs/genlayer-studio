/**
 * Behavioral snapshot tests for nodeStore.
 *
 * Documents that validator/provider management calls sim_* methods.
 * ALL of these are Studio-only and must be gated on isStudio.
 */
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { useNodeStore } from '@/stores';
import { createPinia, setActivePinia } from 'pinia';

const mockRpcService = {
  getValidators: vi.fn(() => Promise.resolve([])),
  getProvidersAndModels: vi.fn(() => Promise.resolve([])),
  createValidator: vi.fn(() => Promise.resolve()),
  updateValidator: vi.fn(() => Promise.resolve()),
  deleteValidator: vi.fn(() => Promise.resolve()),
  addProvider: vi.fn(() => Promise.resolve()),
  updateProvider: vi.fn(() => Promise.resolve()),
  deleteProvider: vi.fn(() => Promise.resolve()),
  resetDefaultsLlmProviders: vi.fn(() => Promise.resolve()),
};

vi.mock('@/hooks', () => ({
  useRpcClient: vi.fn(() => mockRpcService),
  useWebSocketClient: vi.fn(() => ({
    on: vi.fn(),
    off: vi.fn(),
    id: 'mock-ws',
    connected: true,
  })),
}));

describe('nodeStore — Studio-only sim_* calls', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('getValidatorsData calls sim_getAllValidators via rpcService', async () => {
    const store = useNodeStore();
    await store.getValidatorsData();
    expect(mockRpcService.getValidators).toHaveBeenCalled();
  });

  it('getProvidersData calls sim_getProvidersAndModels via rpcService', async () => {
    const store = useNodeStore();
    await store.getProvidersData();
    expect(mockRpcService.getProvidersAndModels).toHaveBeenCalled();
  });

  it('createNewValidator calls sim_createValidator', async () => {
    const store = useNodeStore();
    await store.createNewValidator({} as any);
    expect(mockRpcService.createValidator).toHaveBeenCalled();
  });

  it('updateValidator calls sim_updateValidator', async () => {
    const store = useNodeStore();
    await store.updateValidator(
      { address: '0x1' } as any,
      { stake: 100, provider: 'test', model: 'test', config: '{}' } as any,
    );
    expect(mockRpcService.updateValidator).toHaveBeenCalled();
  });

  it('deleteValidator calls sim_deleteValidator', async () => {
    const store = useNodeStore();
    await store.deleteValidator('0x1');
    expect(mockRpcService.deleteValidator).toHaveBeenCalled();
  });
});
