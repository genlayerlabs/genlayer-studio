import type { NodeLog, NewValidatorDataModel, ValidatorModel } from '@/types';
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { useContractsStore } from './contracts';
import { notify } from '@kyvg/vue3-notification';
import { useRpcClient, useWebSocketClient } from '@/hooks';

export const useNodeStore = defineStore('nodeStore', () => {
  const rpcClient = useRpcClient();
  const webSocketClient = useWebSocketClient();
  const logs = ref<NodeLog[]>([]);
  const contractsStore = useContractsStore();
  const nodeProviders = ref<Record<string, string[]>>({});
  const validators = ref<ValidatorModel[]>([]);
  const isLoadingValidatorData = ref<boolean>(true);
  const searchFilter = ref<string>('');

  if (!webSocketClient.connected) webSocketClient.connect();

  const trackedEvents = [
    'endpoint_call',
    'endpoint_success',
    'endpoint_error',
    'transaction_status_updated',
    'consensus_reached',
    'consensus_failed',
    'read_contract',
    'write_contract',
    'write_contract_failed',
    'deploying_contract',
    'deployed_contract',
    'contract_deployment_failed',
  ];

  trackedEvents.forEach((eventName) => {
    webSocketClient.on(eventName, (data: any) => {
      addLog({
        scope: data.scope,
        name: data.name,
        type: data.type,
        message: data.message,
        data: data.data,
      });
    });
  });

  function addLog(log: NodeLog) {
    logs.value.push(log);
  }

  async function getValidatorsData() {
    isLoadingValidatorData.value = true;

    try {
      const [validatorsResult, modelsResult] = await Promise.all([
        rpcClient.getValidators(),
        rpcClient.getProvidersAndModels(),
      ]);
      validators.value = validatorsResult;
      nodeProviders.value = modelsResult;

      nodeProviders.value = modelsResult.reduce(
        (acc: Record<string, string[]>, llmprovider: any) => {
          const provider = llmprovider.provider;
          if (!acc[provider]) {
            acc[provider] = [];
          }
          acc[provider].push(llmprovider.model);
          return acc;
        },
        {},
      );
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
    const { stake, provider, model, config } = newValidatorData;
    const validatorConfig = JSON.parse(config || '{}');
    const result = await rpcClient.createValidator({
      stake,
      provider,
      model,
      config: validatorConfig,
    });
    validators.value.push(result);
  }

  async function cloneValidator(validator: ValidatorModel) {
    const result = await rpcClient.createValidator(validator);
    validators.value.push(result);
  }

  const contractsToDelete = computed(() =>
    contractsStore.contracts.filter((c) => c.example),
  );

  const validatorsOrderedById = computed(() =>
    validators.value.slice().sort((a, b) => a.id - b.id),
  );

  const clearLogs = () => {
    logs.value = [];
  };

  const hasAtLeastOneValidator = computed(() => validators.value.length >= 1);

  return {
    logs,
    validators,
    nodeProviders,
    contractsToDelete,
    isLoadingValidatorData,
    searchFilter,

    getValidatorsData,
    createNewValidator,
    cloneValidator,
    deleteValidator,
    updateValidator,
    clearLogs,

    validatorsOrderedById,
    hasAtLeastOneValidator,
  };
});
