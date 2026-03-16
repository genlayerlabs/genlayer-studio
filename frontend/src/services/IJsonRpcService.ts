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
  setFinalityWindowTime(time: number): Promise<any>;
  getFinalityWindowTime(): Promise<number>;
  upgradeContractCode(
    contractAddress: string,
    newCode: string,
    signature?: string,
  ): Promise<{ transaction_hash: string; message: string }>;
  getContractNonce(address: string): Promise<number>;
  cancelTransaction(
    transactionHash: string,
    signature?: string,
  ): Promise<{ transaction_hash: string; status: string }>;
}
