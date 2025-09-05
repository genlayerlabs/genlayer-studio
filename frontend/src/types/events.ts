export interface EventProperties {
  created_account: {};
  created_validator: {
    validator_provider: string;
    validator_model: string;
    validator_stake: number;
  };
  created_contract: {
    contract_name: string;
  };
  deployed_contract: {
    contract_name: string;
  };
  called_read_method: {
    contract_name: string;
    method_name: string;
  };
  called_write_method: {
    contract_name: string;
    method_name: string;
  };
  simulated_write_method: {
    contract_name: string;
    method_name: string;
  };
}

export type EventName = keyof EventProperties;
