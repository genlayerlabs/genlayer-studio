<script setup lang="ts">
import { useNodeStore } from '@/stores';
import {
  type ValidatorModel,
  type NewValidatorDataModel,
  type ProviderModel,
} from '@/types';
import { notify } from '@kyvg/vue3-notification';
import { computed, ref, watch } from 'vue';
import SelectInput from '@/components/global/inputs/SelectInput.vue';
import NumberInput from '@/components/global/inputs/NumberInput.vue';
import TextAreaInput from '@/components/global/inputs/TextAreaInput.vue';
import FieldError from '@/components/global/fields/FieldError.vue';
import FieldLabel from '@/components/global/fields/FieldLabel.vue';
import { useEventTracking, useConfig } from '@/hooks';
import CopyTextButton from '../global/CopyTextButton.vue';
import { uniqBy } from 'lodash-es';
import Alert from '../global/Alert.vue';

const nodeStore = useNodeStore();
const { trackEvent } = useEventTracking();
const emit = defineEmits(['close']);
const error = ref('');
const isLoading = ref(false);
const { canUpdateValidators } = useConfig();

const props = defineProps<{
  validator?: ValidatorModel;
}>();

const isCreateMode = computed(() => !props.validator);

async function handleCreateValidator() {
  error.value = '';
  isLoading.value = true;

  try {
    await nodeStore.createNewValidator(newValidatorData.value);

    notify({
      title: 'New validator created',
      type: 'success',
    });

    trackEvent('created_validator', {
      validator_provider: newValidatorData.value.provider,
      validator_model: newValidatorData.value.model,
      validator_stake: newValidatorData.value.stake,
    });

    emit('close');
  } catch (err) {
    console.error(err);
    error.value = (err as Error)?.message;
  } finally {
    isLoading.value = false;
  }
}

async function handleUpdateValidator(validator: ValidatorModel) {
  error.value = '';
  isLoading.value = true;
  try {
    await nodeStore.updateValidator(validator, newValidatorData.value);
    notify({
      title: `Updated validator #${validator.id}`,
      type: 'success',
    });
    emit('close');
  } catch (err) {
    console.error(err);
    error.value = (err as Error)?.message;
  } finally {
    isLoading.value = false;
  }
}

const newValidatorData = ref<NewValidatorDataModel>({
  model: '',
  provider: '',
  stake: 1,
  config: '{ }',
  plugin: '',
  plugin_config: {},
});

const validatorModelValid = computed(() => {
  return (
    newValidatorData.value?.model !== '' &&
    newValidatorData.value?.provider !== '' &&
    isStakeValid.value &&
    isConfigValid.value
  );
});

const isConfigValid = computed(() => {
  // Allow empty config
  if (!newValidatorData.value.config) {
    return true;
  }

  // Try to parse JSON
  try {
    JSON.parse(newValidatorData.value.config);
    return true;
  } catch (error) {
    return false;
  }
});

const providerOptions = computed(() => {
  return uniqBy(nodeStore.nodeProviders, 'provider').map((provider: any) => {
    const hasIssue = !provider?.is_available;
    return {
      label: hasIssue
        ? `${provider.provider} (potential config issue)`
        : provider.provider,
      value: provider.provider,
      disabled: false,
    };
  });
});

const modelOptions = computed(() => {
  const providers = nodeStore.nodeProviders.filter(
    (provider: any) => provider.provider === newValidatorData.value.provider,
  );

  return uniqBy(providers, 'model').map((provider: any) => {
    const isDisabled = !provider?.is_model_available;
    return {
      value: provider.model,
      label: isDisabled
        ? `${provider.model} (potential config issue)`
        : provider.model,
      disabled: false,
    };
  });
});

const handleChangeProvider = () => {
  error.value = '';
  // Get all models for this provider (not just available ones)
  const allModels = nodeStore.nodeProviders
    .filter((p: any) => p.provider === newValidatorData.value.provider)
    .map((p: any) => p.model);

  newValidatorData.value.model = allModels.length > 0 ? allModels[0] : '';
  adaptValues();
};

const adaptValues = () => {
  newValidatorData.value.plugin = '';
  newValidatorData.value.config = '';
  newValidatorData.value.plugin_config = {};

  const provider = nodeStore.nodeProviders.find(
    (provider: ProviderModel) =>
      provider.model === newValidatorData.value.model &&
      provider.provider === newValidatorData.value.provider,
  );

  if (provider) {
    newValidatorData.value.plugin = provider.plugin;
    newValidatorData.value.config = JSON.stringify(provider.config, null, 2);
    newValidatorData.value.plugin_config = provider.plugin_config;
  }
};

const handleChangeModel = () => {
  error.value = '';
  adaptValues();
};

const tryInitValues = () => {
  if (!props.validator) {
    try {
      const firstProvider = nodeStore.nodeProviders[0];
      if (!firstProvider) return;
      newValidatorData.value.provider = firstProvider.provider;
      newValidatorData.value.config = JSON.stringify(
        firstProvider.config,
        null,
        2,
      );
      handleChangeProvider();
    } catch (err) {
      console.error('Could not initialize values', err);
    }
  } else {
    newValidatorData.value = {
      model: props.validator.model,
      provider: props.validator.provider,
      stake: props.validator.stake,
      config: JSON.stringify(props.validator.config, null, 2),
      plugin: props.validator.plugin,
      plugin_config: props.validator.plugin_config,
    };
  }
};

const isStakeValid = computed(() => {
  return (
    Number.isInteger(newValidatorData.value.stake) &&
    newValidatorData.value.stake >= 1
  );
});

// Watch for providers loading and initialize values once they're available
watch(
  () => nodeStore.nodeProviders.length,
  (newLength) => {
    if (newLength > 0 && !props.validator && !newValidatorData.value.provider) {
      tryInitValues();
    }
  },
  { immediate: true },
);
</script>

<template>
  <Modal @close="emit('close')" @onOpen="tryInitValues">
    <template #title v-if="isCreateMode">New Validator</template>
    <template #title v-else>Validator #{{ validator?.id }}</template>

    <Alert warning v-if="providerOptions.length === 0">
      No node providers available. Please configure a provider first.
    </Alert>

    <template #info v-if="!isCreateMode">
      <div class="flex grow flex-col">
        <span
          class="truncate text-xs font-semibold text-gray-400"
          data-testid="validator-item-provider"
        >
          {{ validator?.provider }}
        </span>
        <span
          class="truncate text-sm font-semibold"
          data-testid="validator-item-model"
        >
          {{ validator?.model }}
        </span>
      </div>

      <div
        class="mt-2 flex flex-row items-center gap-1 font-mono text-xs font-normal"
      >
        {{ validator?.address }}
        <CopyTextButton :text="validator?.address || ''" />
      </div>
    </template>

    <div v-if="isCreateMode">
      <FieldLabel for="provider">Provider:</FieldLabel>
      <SelectInput
        name="provider"
        :options="providerOptions"
        v-model="newValidatorData.provider"
        @change="handleChangeProvider"
        :invalid="!newValidatorData.provider"
        required
        testId="dropdown-provider"
        :disabled="providerOptions.length === 0"
      />
      <span class="float-right mt-1 text-xs opacity-50" v-if="isCreateMode">
        To add more providers, go to
        <RouterLink
          :to="{ name: 'settings' }"
          @click="emit('close')"
          class="underline"
          >settings</RouterLink
        >.
      </span>
    </div>

    <div v-if="isCreateMode">
      <FieldLabel for="model">Model:</FieldLabel>
      <SelectInput
        name="model"
        :options="modelOptions"
        v-model="newValidatorData.model"
        @change="handleChangeModel"
        :invalid="!newValidatorData.model"
        required
        testId="dropdown-model"
        :disabled="providerOptions.length === 0"
      />

      <Alert
        warning
        class="mt-2"
        v-if="!newValidatorData.model && !!newValidatorData.provider"
        >No available models for this provider. Check your
        <RouterLink
          @click="emit('close')"
          :to="{ name: 'settings' }"
          class="underline"
          >settings</RouterLink
        >.</Alert
      >
    </div>

    <div>
      <FieldLabel for="stake">Stake:</FieldLabel>
      <NumberInput
        id="stake"
        name="stake"
        :min="1"
        :step="1"
        :invalid="!isStakeValid"
        v-model="newValidatorData.stake"
        required
        testId="input-stake"
        :disabled="!canUpdateValidators"
      />

      <FieldError v-if="!isStakeValid"
        >Please enter an integer greater than 0.</FieldError
      >
    </div>

    <div>
      <FieldLabel for="config">Config:</FieldLabel>
      <TextAreaInput
        id="config"
        name="config"
        :rows="5"
        :cols="60"
        v-model="newValidatorData.config"
        :invalid="!isConfigValid"
        :disabled="!canUpdateValidators"
      />
      <FieldError v-if="!isConfigValid">Please enter valid JSON.</FieldError>
    </div>

    <Alert error v-if="error" type="error">{{ error }}</Alert>

    <Btn v-if="!canUpdateValidators" @click="emit('close')">Close</Btn>

    <Btn
      v-else-if="isCreateMode"
      @click="handleCreateValidator"
      :disabled="!validatorModelValid"
      testId="btn-create-validator"
      :loading="isLoading"
    >
      Create
    </Btn>

    <Btn
      v-else-if="!isCreateMode && validator"
      @click="handleUpdateValidator(validator)"
      :disabled="!validatorModelValid"
      testId="btn-update-validator"
      :loading="isLoading"
    >
      Save
    </Btn>
  </Modal>
</template>
