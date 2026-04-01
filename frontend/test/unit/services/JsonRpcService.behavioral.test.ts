/**
 * Behavioral snapshot tests for JsonRpcService.
 *
 * Documents which RPC methods are Studio-only (sim_*) vs universal.
 * The multi-network refactor must gate sim_* methods behind isStudio.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JsonRpcService } from '@/services/JsonRpcService';

const mockCall = vi.fn(() => Promise.resolve({ result: {}, error: null }));

const mockRpcClient = { call: mockCall };

describe('JsonRpcService — RPC method mapping', () => {
  let service: JsonRpcService;

  beforeEach(() => {
    vi.clearAllMocks();
    service = new JsonRpcService(mockRpcClient as any);
  });

  describe('Studio-only methods (sim_*)', () => {
    it('getValidators calls sim_getAllValidators', async () => {
      await service.getValidators();
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'sim_getAllValidators' }),
      );
    });

    it('getProvidersAndModels calls sim_getProvidersAndModels', async () => {
      await service.getProvidersAndModels();
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'sim_getProvidersAndModels' }),
      );
    });

    it('createValidator calls sim_createValidator', async () => {
      await service.createValidator({} as any);
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'sim_createValidator' }),
      );
    });

    it('deleteValidator calls sim_deleteValidator', async () => {
      await service.deleteValidator('0x1');
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'sim_deleteValidator' }),
      );
    });

    it('setFinalityWindowTime calls sim_setFinalityWindowTime', async () => {
      await service.setFinalityWindowTime(300);
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'sim_setFinalityWindowTime' }),
      );
    });

    it('getFinalityWindowTime calls sim_getFinalityWindowTime', async () => {
      await service.getFinalityWindowTime();
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'sim_getFinalityWindowTime' }),
      );
    });

    it('upgradeContractCode calls sim_upgradeContractCode', async () => {
      await service.upgradeContractCode('0x1', 'code', 'sig');
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'sim_upgradeContractCode' }),
      );
    });

    it('cancelTransaction calls sim_cancelTransaction', async () => {
      await service.cancelTransaction('0xhash');
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'sim_cancelTransaction' }),
      );
    });
  });

  describe('Universal methods (eth_*, gen_*)', () => {
    it('getContractState calls eth_call', async () => {
      await service.getContractState({ to: '0x1', data: '0x' } as any);
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'eth_call' }),
      );
    });

    it('getTransactionByHash calls eth_getTransactionByHash', async () => {
      await service.getTransactionByHash('0xhash');
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'eth_getTransactionByHash' }),
      );
    });

    it('getContractSchema calls gen_getContractSchemaForCode', async () => {
      await service.getContractSchema('code');
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'gen_getContractSchemaForCode' }),
      );
    });

    it('getDeployedContractSchema calls gen_getContractSchema', async () => {
      await service.getDeployedContractSchema('0x1');
      expect(mockCall).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'gen_getContractSchema' }),
      );
    });
  });
});
