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
