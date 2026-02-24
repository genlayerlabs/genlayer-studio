import type { IRpcClient } from '@/clients/rpc';
import type { IJsonRpcService } from './IJsonRpcService';
import type {
  GetContractStateRequest,
  GetContractStateResult,
  GetContractSchemaRequest,
  GetDeployedContractSchemaRequest,
  CreateValidatorRequest,
  UpdateValidatorRequest,
  AddProviderRequest,
  UpdateProviderRequest,
  DeleteProviderRequest,
  TransactionItem,
  GetTransactionCountRequest,
} from '@/types';

export class JsonRpcService implements IJsonRpcService {
  constructor(protected rpcClient: IRpcClient) {}

  private async callRpcMethod<T>(
    method: string,
    params: any[],
    errorMessage: string,
  ): Promise<T | T[]> {
    const { result, error } = await this.rpcClient.call<T>({
      method,
      params,
    });
    if (error) {
      console.error(error.message, error.code);
      throw new Error(errorMessage);
    }
    return result;
  }

  async getContractState({
    to,
    from,
    data,
  }: GetContractStateRequest): Promise<GetContractStateResult> {
    return this.callRpcMethod<GetContractStateResult>(
      'eth_call',
      [{ to, from, data }],
      'Error getting contract state',
    );
  }

  async sendTransaction(signedTransaction: string): Promise<any> {
    return this.callRpcMethod<any>(
      'eth_sendRawTransaction',
      [signedTransaction],
      'Error sending transaction',
    );
  }

  async getContractSchema({ code }: GetContractSchemaRequest): Promise<any> {
    return this.callRpcMethod<any>(
      'gen_getContractSchemaForCode',
      [code],
      'Error getting contract schema',
    );
  }

  async getDeployedContractSchema({
    address,
  }: GetDeployedContractSchemaRequest): Promise<any> {
    return this.callRpcMethod<any>(
      'gen_getContractSchema',
      [address],
      'Error getting deployed contract schema',
    );
  }

  async getValidators(): Promise<any> {
    return this.callRpcMethod<any>(
      'sim_getAllValidators',
      [],
      'Error getting validators',
    );
  }

  async addProvider({
    provider,
    model,
    config,
    plugin,
    plugin_config,
  }: AddProviderRequest): Promise<any> {
    return this.callRpcMethod<any>(
      'sim_addProvider',
      [{ provider, model, config, plugin, plugin_config }],
      'Error adding provider',
    );
  }

  async updateProvider({
    id,
    provider,
    model,
    config,
    plugin,
    plugin_config,
  }: UpdateProviderRequest): Promise<any> {
    return this.callRpcMethod<any>(
      'sim_updateProvider',
      [id, { provider, model, config, plugin, plugin_config }],
      'Error updating provider',
    );
  }

  async deleteProvider({ id }: DeleteProviderRequest): Promise<any> {
    return this.callRpcMethod<any>(
      'sim_deleteProvider',
      [id],
      'Error deleting provider',
    );
  }

  async getProvidersAndModels(): Promise<any> {
    return this.callRpcMethod<any>(
      'sim_getProvidersAndModels',
      [],
      'Error getting providers and models',
    );
  }

  async resetDefaultsLlmProviders(): Promise<any> {
    return this.callRpcMethod<any>(
      'sim_resetDefaultsLlmProviders',
      [],
      'Error resetting default LLM providers',
    );
  }

  async createValidator({
    stake,
    provider,
    model,
    config,
    plugin,
    plugin_config,
  }: CreateValidatorRequest): Promise<any> {
    return this.callRpcMethod<any>(
      'sim_createValidator',
      [stake, provider, model, config, plugin, plugin_config],
      'Error creating validator',
    );
  }

  async updateValidator({
    address,
    stake,
    provider,
    model,
    config,
  }: UpdateValidatorRequest): Promise<any> {
    return this.callRpcMethod<any>(
      'sim_updateValidator',
      [address, stake, provider, model, config],
      'Error updating validator',
    );
  }

  async deleteValidator({ address }: { address: string }): Promise<any> {
    return this.callRpcMethod<any>(
      'sim_deleteValidator',
      [address],
      'Error deleting validator',
    );
  }

  async getTransactionByHash(txId: string): Promise<TransactionItem> {
    return this.callRpcMethod<TransactionItem>(
      'eth_getTransactionByHash',
      [String(txId)],
      'Error getting transaction by ID',
    ) as Promise<TransactionItem>;
  }

  async getTransactionCount({
    address,
  }: GetTransactionCountRequest): Promise<number> {
    return this.callRpcMethod<any>(
      'eth_getTransactionCount',
      [address],
      'Error getting transaction count',
    );
  }

  async setFinalityWindowTime(time: number): Promise<any> {
    return this.callRpcMethod<any>(
      'sim_setFinalityWindowTime',
      [time],
      'Error setting finality window duration',
    );
  }

  async getFinalityWindowTime(): Promise<number> {
    return this.callRpcMethod<any>(
      'sim_getFinalityWindowTime',
      [],
      'Error getting finality window time',
    );
  }

  async upgradeContractCode(
    contractAddress: string,
    newCode: string,
    signature?: string,
  ): Promise<{ transaction_hash: string; message: string }> {
    return this.callRpcMethod<{ transaction_hash: string; message: string }>(
      'sim_upgradeContractCode',
      [contractAddress, newCode, signature],
      'Error upgrading contract code',
    ) as Promise<{ transaction_hash: string; message: string }>;
  }

  async getContractNonce(address: string): Promise<number> {
    return this.callRpcMethod<number>(
      'gen_getContractNonce',
      [address],
      'Error getting contract nonce',
    ) as Promise<number>;
  }
}
