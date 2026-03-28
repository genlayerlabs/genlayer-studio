export interface GetContractStateResult extends Record<string, any> {}

export interface GetProvidersAndModelsData
  extends Array<{
    config: Record<string, any>;
    id: number;
    model: string;
    plugin: string;
    plugin_config: Record<string, any>;
    provider: string;
    is_available: boolean;
    is_model_available: boolean;
    is_default: boolean;
  }> {}
