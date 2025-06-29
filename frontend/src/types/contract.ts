import type { ContractMethod as BaseContractMethod } from 'genlayer-js/types';

export interface ContractMethod extends BaseContractMethod {
  payable?: boolean;
}
