export * from './results';
export * from './requests';
export * from './responses';
export * from './store';
export * from './events';

export interface ValidatorModel {
  address: string;
  config: any;
  id: number;
  model: string;
  provider: string;
  stake: number;
  updated_at: string;
  plugin: string;
  plugin_config: Record<string, any>;
}

export interface NewValidatorDataModel {
  config: string;
  model: string;
  provider: string;
  stake: number;
  plugin: string;
  plugin_config: Record<string, any>;
}

export interface ProviderModel {
  id: number;
  provider: string;
  model: string;
  config: Record<string, any>;
  plugin: string;
  plugin_config: Record<string, any>;
  is_available: boolean;
  is_model_available: boolean;
  is_default: boolean;
}

export interface NewProviderDataModel {
  provider: string;
  model: string;
  config: Record<string, any>;
  plugin: string;
  plugin_config: Record<string, any>;
}

export type Address = `0x${string}`;

/**
 * Transaction execution mode for controlling validation behavior.
 * - NORMAL: Full multi-validator consensus with time-based finalization
 * - LEADER_ONLY: Leader executes, NO validation, immediate finalization
 * - LEADER_SELF_VALIDATOR: Leader executes AND validates themselves, immediate finalization
 */
export type ExecutionMode = 'NORMAL' | 'LEADER_ONLY' | 'LEADER_SELF_VALIDATOR';

/**
 * Read state mode for controlling which contract state to read.
 * - ACCEPTED: Read from the latest non-finalized (accepted) state
 * - FINALIZED: Read from the latest finalized state
 */
export type ReadStateMode = 'ACCEPTED' | 'FINALIZED';

export interface SchemaProperty {
  type?: string | string[];
  default?: any;
  minimum?: number;
  maximum?: number;
  multipleOf?: number;
  enum?: any[];
  $comment?: string;
  properties?: Record<string, SchemaProperty>;
}
