import type {
  GetContractStateRequest,
  GetContractStateResult,
  GetDeployedContractSchemaRequest,
  AddProviderRequest,
  UpdateProviderRequest,
  DeleteProviderRequest,
  CreateValidatorRequest,
  UpdateValidatorRequest,
  DeleteValidatorRequest,
  GetContractSchemaRequest,
  GetTransactionCountRequest,
} from '@/types';

export interface IJsonRpcService {
  getContractState(
    request: GetContractStateRequest,
  ): Promise<GetContractStateResult>;
  sendTransaction(signedTransaction: string): Promise<string>;
  getContractSchema(request: GetContractSchemaRequest): Promise<any>;
  getDeployedContractSchema(
    request: GetDeployedContractSchemaRequest,
  ): Promise<any>;
  getValidators(): Promise<any[]>;
  getProvidersAndModels(): Promise<any[]>;
  addProvider(request: AddProviderRequest): Promise<any>;
  updateProvider(request: UpdateProviderRequest): Promise<any>;
  deleteProvider(request: DeleteProviderRequest): Promise<any>;
  createValidator(request: CreateValidatorRequest): Promise<any>;
  updateValidator(request: UpdateValidatorRequest): Promise<any>;
  deleteValidator(request: DeleteValidatorRequest): Promise<any>;
  getTransactionByHash(hash: string): Promise<any>;
  getTransactionCount(address: GetTransactionCountRequest): Promise<number>;
  setTransactionAppeal(tx_address: string): Promise<any>;
  setFinalityWindowTime(time: number): Promise<any>;
  getFinalityWindowTime(): Promise<number>;
}
