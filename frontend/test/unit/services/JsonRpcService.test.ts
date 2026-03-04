import { JsonRpcService } from '@/services/JsonRpcService';
import type { IJsonRpcService } from '@/services/IJsonRpcService';
import type { IRpcClient } from '@/clients/rpc';
import type { GetContractStateResult, JsonRPCResponse } from '@/types';
import { describe, expect, it, vi, afterEach, beforeEach } from 'vitest';

describe('JsonRprService', () => {
  let jsonRpcService: IJsonRpcService;
  const rpcClient: IRpcClient = vi.fn();
  beforeEach(() => {
    jsonRpcService = new JsonRpcService(rpcClient);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('getContractState', () => {
    const mockResponse: JsonRPCResponse<GetContractStateResult> = {
      id: 'test',
      jsonrpc: '2.0',
      result: {
        data: {
          get_have_coin: 'True',
          id: '0x58FaA28cbAA1b52F8Ec8D3c6FFCE6f1AaF8bEEB1',
        },
        message: '',
        status: 'success',
      },
    };
    const input = {
      to: '0x58FaA28cbAA1b52F8Ec8D3c6FFCE6f1AaF8bEEB1',
      from: '0xFEaedeC4c6549236EaF49C1F7c5cf860FD2C3fcB',
      data: '0x',
    };
    it('should call rpc client', async () => {
      const spy = vi
        .spyOn(rpcClient, 'call')
        .mockImplementationOnce(() => Promise.resolve(mockResponse));

      await jsonRpcService.getContractState(input);
      expect(spy.getMockName()).toEqual('call');
      expect(rpcClient.call).toHaveBeenCalledTimes(1);
      expect(rpcClient.call).toHaveBeenCalledWith({
        method: 'eth_call',
        params: [input],
      });
    });

    it('should return contract state', async () => {
      vi.spyOn(rpcClient, 'call').mockImplementationOnce(() =>
        Promise.resolve(mockResponse),
      );
      const result = await jsonRpcService.getContractState(input);
      expect(result).to.deep.equal(mockResponse.result);
    });
  });

  describe('cancelTransaction', () => {
    const txHash = '0x' + 'a'.repeat(64);
    const mockCancelResponse = {
      id: 'test',
      jsonrpc: '2.0',
      result: { transaction_hash: txHash, status: 'CANCELED' },
    };

    it('should call rpc client with correct method and params', async () => {
      const spy = vi
        .spyOn(rpcClient, 'call')
        .mockImplementationOnce(() => Promise.resolve(mockCancelResponse));

      await jsonRpcService.cancelTransaction(txHash);
      expect(spy.getMockName()).toEqual('call');
      expect(rpcClient.call).toHaveBeenCalledTimes(1);
      expect(rpcClient.call).toHaveBeenCalledWith({
        method: 'sim_cancelTransaction',
        params: [txHash, undefined],
      });
    });

    it('should pass signature when provided', async () => {
      vi.spyOn(rpcClient, 'call').mockImplementationOnce(() =>
        Promise.resolve(mockCancelResponse),
      );

      await jsonRpcService.cancelTransaction(txHash, '0xsignature');
      expect(rpcClient.call).toHaveBeenCalledWith({
        method: 'sim_cancelTransaction',
        params: [txHash, '0xsignature'],
      });
    });

    it('should return cancel result', async () => {
      vi.spyOn(rpcClient, 'call').mockImplementationOnce(() =>
        Promise.resolve(mockCancelResponse),
      );
      const result = await jsonRpcService.cancelTransaction(txHash);
      expect(result).to.deep.equal({
        transaction_hash: txHash,
        status: 'CANCELED',
      });
    });

    it('should throw error when rpc returns error', async () => {
      vi.spyOn(rpcClient, 'call').mockImplementationOnce(() =>
        Promise.resolve({
          result: null,
          error: { code: -32000, message: 'Cannot cancel' },
        }),
      );
      await expect(jsonRpcService.cancelTransaction(txHash)).rejects.toThrow(
        'Error cancelling transaction',
      );
    });
  });
});
