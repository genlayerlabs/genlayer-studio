import type {
  NodeLog,
  NewValidatorDataModel,
  ValidatorModel,
  GetProvidersAndModelsData,
  NewProviderDataModel,
  ProviderModel,
} from '@/types';
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { notify } from '@kyvg/vue3-notification';
import { useRpcClient, useWebSocketClient } from '@/hooks';

export const useNodeStore = defineStore('nodeStore', () => {
  const rpcClient = useRpcClient();
  const webSocketClient = useWebSocketClient();
  const logs = ref<NodeLog[]>([]);
  const leaderTxHashes = new Set<string>();
  const nodeProviders = ref<GetProvidersAndModelsData>([]);
  const validators = ref<ValidatorModel[]>([]);
  const isLoadingValidatorData = ref<boolean>(true);
  const isLoadingProviders = ref<boolean>(true);
  const searchFilter = ref<string>('');

  const trackedEvents = [
    'endpoint_call',
    'endpoint_success',
    'endpoint_error',
    'transaction_status_updated',
    'consensus_event',
    'contract_stdout',
    'read_contract',
    'write_contract',
    'write_contract_failed',
    'deploying_contract',
    'deployed_contract',
    'contract_deployment_failed',
    'execution_finished',
  ];

  trackedEvents.forEach((eventName) => {
    webSocketClient.on(eventName, (eventData: any) => {
      let role: 'leader' | 'validator' | undefined;
      if (
        eventData.name === 'execution_finished' &&
        eventData.transaction_hash
      ) {
        if (!leaderTxHashes.has(eventData.transaction_hash)) {
          leaderTxHashes.add(eventData.transaction_hash);
          role = 'leader';
        } else {
          role = 'validator';
        }
      }
      addLog({
        scope: eventData.scope,
        name: eventData.name,
        type: eventData.type,
        message: eventData.message,
        data: eventData.data,
        role,
      });
    });
  });

  function addLog(log: NodeLog) {
    logs.value.push(log);
  }

  async function resetProviders() {
    await rpcClient.resetDefaultsLlmProviders();
    getProvidersData();
  }

  async function getValidatorsData() {
    isLoadingValidatorData.value = true;

    try {
      validators.value = await rpcClient.getValidators();
    } catch (error) {
      console.error(error);
      notify({
        title: 'Error',
        text: (error as Error)?.message || 'Error loading validators',
        type: 'error',
      });
    } finally {
      isLoadingValidatorData.value = false;
    }
  }

  async function getProvidersData() {
    isLoadingProviders.value = true;

    try {
      nodeProviders.value = await rpcClient.getProvidersAndModels();
    } catch (error) {
      console.error(error);
      notify({
        title: 'Error',
        text: (error as Error)?.message || 'Error loading providers',
        type: 'error',
      });
    } finally {
      isLoadingProviders.value = false;
    }
  }

  async function updateValidator(
    validator: ValidatorModel,
    newValidatorData: NewValidatorDataModel,
  ) {
    const { stake, provider, model, config } = newValidatorData;

    if (stake <= 0 || !provider || !model || !config) {
      throw new Error('Please fill all the required fields');
    }
    const validatorConfig = JSON.parse(config || '{}');
    const result = await rpcClient.updateValidator({
      address: validator.address || '',
      stake,
      provider,
      model,
      config: validatorConfig,
    });
    const index = validators.value.findIndex(
      (v) => v.address === validator.address,
    );

    if (index >= 0) {
      validators.value.splice(index, 1, result);
    }
  }

  const deleteValidator = async (validator: ValidatorModel) => {
    if (validators.value.length === 1) {
      throw new Error('You must have at least one validator');
    }
    await rpcClient.deleteValidator({
      address: validator.address || '',
    });
    validators.value = validators.value.filter(
      (v) => v.address !== validator.address,
    );
  };

  async function createNewValidator(newValidatorData: NewValidatorDataModel) {
    const { stake, provider, model, config, plugin, plugin_config } =
      newValidatorData;
    const validatorConfig = JSON.parse(config || '{}');
    const result = await rpcClient.createValidator({
      stake,
      provider,
      model,
      config: validatorConfig,
      plugin,
      plugin_config,
    });
    validators.value.push(result);
  }

  async function cloneValidator(validator: ValidatorModel) {
    const result = await rpcClient.createValidator(validator);
    validators.value.push(result);
  }

  async function addProvider(providerData: NewProviderDataModel) {
    await rpcClient.addProvider({
      ...providerData,
    });
    getProvidersData();
  }

  async function updateProvider(
    provider: ProviderModel,
    newProviderData: NewProviderDataModel,
  ) {
    await rpcClient.updateProvider({
      id: provider.id,
      ...newProviderData,
    });
    getProvidersData();
  }

  async function deleteProvider(id: number) {
    await rpcClient.deleteProvider({ id });
    getProvidersData();
  }

  const validatorsOrderedById = computed(() =>
    validators.value.slice().sort((a, b) => a.id - b.id),
  );

  const clearLogs = () => {
    logs.value = [];
    leaderTxHashes.clear();
  };

  const hasAtLeastOneValidator = computed(() => validators.value.length >= 1);

  const availableProviders = computed(() => [
    ...new Set(
      nodeProviders.value
        .filter((provider) => provider.is_available)
        .map((provider) => provider.provider),
    ),
  ]);

  const availableModelsForProvider = computed(
    () => (selectedProvider: string) => [
      ...new Set(
        nodeProviders.value
          .filter(
            (provider) =>
              provider.is_model_available &&
              provider.provider === selectedProvider,
          )
          .map((provider) => provider.model),
      ),
    ],
  );

  return {
    logs,
    validators,
    nodeProviders,
    isLoadingValidatorData,
    isLoadingProviders,
    searchFilter,

    getValidatorsData,
    getProvidersData,
    resetProviders,
    createNewValidator,
    cloneValidator,
    deleteValidator,
    updateValidator,
    clearLogs,
    addProvider,
    updateProvider,
    deleteProvider,

    availableProviders,
    availableModelsForProvider,
    validatorsOrderedById,
    hasAtLeastOneValidator,
  };
});
