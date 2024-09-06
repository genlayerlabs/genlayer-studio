import { describe, it, expect, beforeEach, vi, type Mock } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useNodeStore } from '@/stores';
import { useContractsStore } from '@/stores/contracts';
import { useRpcClient, useWebSocketClient } from '@/hooks';
import { notify } from '@kyvg/vue3-notification';
import { type ValidatorModel, type NodeLog } from '@/types';

const testValidator1: ValidatorModel = {
  id: 1,
  address: '0x123',
  stake: 100,
  provider: 'openai',
  model: 'gpt-4',
  config: '{}',
  updated_at: new Date().toISOString(),
};

const testValidator2: ValidatorModel = {
  id: 2,
  address: '0x321',
  stake: 200,
  provider: 'ollama',
  model: 'llama3',
  config: '{}',
  updated_at: new Date().toISOString(),
};

const testLog: NodeLog = {
  date: new Date().toISOString(),
  message: {
    function: 'test_function',
    trace_id: '123456',
    response: {
      status: 'success',
      message: 'Test message',
      data: {},
    },
  },
  mock: false,
};

vi.mock('@/hooks', () => ({
  useRpcClient: vi.fn(),
  useWebSocketClient: vi.fn(),
}));

vi.mock('@/stores/contracts', () => ({
  useContractsStore: vi.fn(),
}));

vi.mock('@kyvg/vue3-notification', () => ({
  notify: vi.fn(),
}));

describe('useNodeStore', () => {
  let nodeStore: ReturnType<typeof useNodeStore>;
  const mockRpcClient = {
    getValidators: vi.fn(),
    getProvidersAndModels: vi.fn(),
    updateValidator: vi.fn(),
    deleteValidator: vi.fn(),
    createValidator: vi.fn(),
  };

  const mockWebSocketClient = {
    connected: false,
    connect: vi.fn(),
    on: vi.fn(),
  };

  const mockContractsStore = {
    contracts: [{ id: 1, example: true }],
  };

  beforeEach(() => {
    setActivePinia(createPinia());
    (useRpcClient as Mock).mockReturnValue(mockRpcClient);
    (useWebSocketClient as Mock).mockReturnValue(mockWebSocketClient);
    (useContractsStore as unknown as Mock).mockReturnValue(mockContractsStore);
    nodeStore = useNodeStore();
    mockRpcClient.getValidators.mockClear();
    mockRpcClient.getProvidersAndModels.mockClear();
    mockRpcClient.updateValidator.mockClear();
    mockRpcClient.deleteValidator.mockClear();
    mockRpcClient.createValidator.mockClear();
    mockWebSocketClient.connect.mockClear();
    mockWebSocketClient.on.mockClear();
  });

  it('should fetch validators data successfully', async () => {
    mockRpcClient.getValidators.mockResolvedValue({
      status: 'success',
      data: [],
    });
    mockRpcClient.getProvidersAndModels.mockResolvedValue({
      status: 'success',
      data: {},
    });

    await nodeStore.getValidatorsData();

    expect(mockRpcClient.getValidators).toHaveBeenCalled();
    expect(mockRpcClient.getProvidersAndModels).toHaveBeenCalled();
    expect(nodeStore.isLoadingValidatorData).toBe(false);
  });

  it('should handle error when fetching validators data', async () => {
    const consoleErrorSpy = vi
      .spyOn(console, 'error')
      .mockImplementation(() => {}); // Mock to avoid console output
    const testError = new Error('Network error');

    mockRpcClient.getValidators.mockRejectedValue(testError);
    await nodeStore.getValidatorsData();

    expect(consoleErrorSpy).toHaveBeenCalledWith(testError);
    expect(notify).toHaveBeenCalledWith({
      title: 'Error',
      text: 'Network error',
      type: 'error',
    });
    expect(nodeStore.isLoadingValidatorData).toBe(false);
  });

  it('should create a new validator', async () => {
    const newValidatorData = testValidator1;

    mockRpcClient.createValidator.mockResolvedValue({
      status: 'success',
      data: newValidatorData,
    });

    await nodeStore.createNewValidator(newValidatorData);
    expect(nodeStore.validators).to.deep.include(newValidatorData);
  });

  it('should update a validator', async () => {
    const validator = testValidator1;
    const newValidatorData = {
      stake: 10000,
      provider: 'provider2',
      model: 'model2',
      config: '{}',
    };

    nodeStore.validators = [testValidator1];

    mockRpcClient.updateValidator.mockResolvedValue({
      status: 'success',
      data: { ...validator, ...newValidatorData },
    });
    await nodeStore.updateValidator(validator, newValidatorData);

    expect(nodeStore.validators[0]).toEqual({
      ...validator,
      ...newValidatorData,
    });
  });

  it('should delete a validator', async () => {
    nodeStore.validators = [testValidator1, testValidator2];
    mockRpcClient.deleteValidator.mockResolvedValue({ status: 'success' });

    await nodeStore.deleteValidator(testValidator1);
    expect(mockRpcClient.deleteValidator).toHaveBeenCalledWith({
      address: testValidator1.address,
    });
    expect(nodeStore.validators).not.to.toContain(testValidator1);
  });

  it('should clear logs', () => {
    nodeStore.logs = [testLog];
    nodeStore.clearLogs();

    expect(nodeStore.logs).toHaveLength(0);
  });

  it('should compute contractsToDelete', () => {
    expect(nodeStore.contractsToDelete).toEqual([{ id: 1, example: true }]);
  });

  it('should compute validatorsOrderedById', () => {
    nodeStore.validators = [testValidator2, testValidator1];
    expect(nodeStore.validatorsOrderedById).toEqual([
      testValidator1,
      testValidator2,
    ]);
  });

  it('should compute hasAtLeastOneValidator', () => {
    nodeStore.validators = [];
    expect(nodeStore.hasAtLeastOneValidator).toBe(false);
    nodeStore.validators = [testValidator1];
    expect(nodeStore.hasAtLeastOneValidator).toBe(true);
  });
});
