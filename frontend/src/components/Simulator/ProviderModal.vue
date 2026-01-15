<script setup lang="ts">
import { useNodeStore } from '@/stores';
import { notify } from '@kyvg/vue3-notification';
import Alert from '../global/Alert.vue';
import providerSchema from '../../assets/schemas/providers_schema.json';
import { computed, markRaw, reactive, ref, watch } from 'vue';
import Ajv2020 from 'ajv/dist/2020';
import { type ValidateFunction } from 'ajv/dist/2020';
import {
  type ProviderModel,
  type NewProviderDataModel,
  type SchemaProperty,
} from '@/types';
import TextInput from '@/components/global/inputs/TextInput.vue';
import SelectInput from '@/components/global/inputs/SelectInput.vue';
import FieldLabel from '@/components/global/fields/FieldLabel.vue';
import ConfigField from '@/components/Simulator/ConfigField.vue';
import { Eye, EyeOff } from 'lucide-vue-next';

const nodeStore = useNodeStore();
const emit = defineEmits(['close']);
const error = ref('');
const isLoading = ref(false);
const props = defineProps<{
  provider?: ProviderModel;
}>();
const isConfigExpanded = ref(false);

const isCreateMode = computed(() => !props.provider);

async function handleCreateProvider() {
  error.value = '';
  isLoading.value = true;
  try {
    await nodeStore.addProvider(newProviderData);

    notify({
      title: 'Added new provider',
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

async function handleUpdateProvider(provider: ProviderModel) {
  error.value = '';
  isLoading.value = true;
  try {
    await nodeStore.updateProvider(provider, newProviderData);
    notify({
      title: `Updated provider`,
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

const ajv = new Ajv2020({
  allErrors: true,
  verbose: true,
  strict: false,
  // ...options,
});

const newProviderData = reactive<NewProviderDataModel>({
  model: '',
  provider: '',
  config: {},
  plugin: '',
  plugin_config: {},
});

const defaultProvider = 'openai';

const schema = markRaw(providerSchema);

let validate: ValidateFunction | undefined;

try {
  validate = ajv.compile(schema);
} catch (err) {
  console.error(err);
}

const errors = ref<any[]>([]);
const pluginOptions = ref<string[]>([]);
const modelOptions = ref<string[]>([]);
const isPluginLocked = ref(false);
const customProvider = ref(false);
const providerOptions = ref<string[]>([]);

const availablePluginOptions = computed(() => {
  return pluginOptions.value.map((plugin) => {
    const exists = checkExistingPreset(
      newProviderData.provider,
      newProviderData.model,
      plugin,
    );

    return {
      value: plugin,
      label: exists ? `${plugin} (already exists)` : plugin,
      disabled: exists,
    };
  });
});

const availableModelOptions = computed(() => {
  return modelOptions.value.map((model) => {
    const exists = checkExistingPreset(
      newProviderData.provider,
      model,
      newProviderData.plugin,
    );

    return {
      value: model,
      label: exists ? `${model} (already exists)` : model,
      disabled: exists,
    };
  });
});

const checkExistingPreset = (
  provider: string,
  model: string,
  plugin: string,
) => {
  return nodeStore.nodeProviders.some(
    (preset) =>
      preset.provider === provider &&
      preset.model === model &&
      preset.plugin === plugin,
  );
};

const presetAlreadyExists = computed(() => {
  return nodeStore.nodeProviders.some(
    (provider) =>
      provider.provider === newProviderData.provider &&
      provider.model === newProviderData.model &&
      provider.plugin === newProviderData.plugin,
  );
});

const tryInitValues = () => {
  errors.value = [];
  providerOptions.value = schema.properties.provider.examples;
  pluginOptions.value = schema.properties.plugin.enum;

  if (props.provider) {
    const initialProviderOption = providerOptions.value.find(
      (option) => option === props.provider?.provider,
    );

    if (!initialProviderOption) {
      customProvider.value = true;
    }

    newProviderData.provider = props.provider.provider;
    newProviderData.model = props.provider.model;
    newProviderData.plugin = props.provider.plugin;
    newProviderData.plugin_config = props.provider.plugin_config;
    newProviderData.config = props.provider.config;
  } else {
    providerOptions.value = schema.properties.provider.examples;
    newProviderData.provider = defaultProvider;
  }

  checkRules();
};

function extractDefaults(
  properties: Record<string, SchemaProperty>,
): Record<string, any> {
  const defaults: Record<string, any> = {};

  Object.keys(properties).forEach((key) => {
    const prop = properties[key];

    if ('default' in prop) {
      defaults[key] = prop.default;
    } else if (prop.type === 'object' && prop.properties) {
      defaults[key] = extractDefaults(prop.properties);
    } else {
      defaults[key] = null;
    }
  });
  return defaults;
}

const pluginConfigProperties = ref<Record<string, any>>({});
const configProperties = ref<Record<string, any>>({});

const checkRules = () => {
  isPluginLocked.value = false;
  modelOptions.value = [];

  schema.allOf.forEach((rule) => {
    // Provider rules
    if (rule.if?.properties?.provider?.const === newProviderData.provider) {
      if (rule.then?.properties?.plugin?.const) {
        newProviderData.plugin = rule.then?.properties?.plugin?.const;
        isPluginLocked.value = true;
      }

      if (isCreateMode.value) {
        if (rule.then?.properties?.model?.enum) {
          modelOptions.value = rule.then?.properties?.model?.enum;

          const availableModel = modelOptions.value.find(
            (model) =>
              !checkExistingPreset(
                newProviderData.provider,
                model,
                newProviderData.plugin,
              ),
          );

          if (availableModel) {
            newProviderData.model = availableModel || modelOptions.value[0];
          } else {
            newProviderData.model = rule.then?.properties?.model?.enum[0];
          }
        } else {
          newProviderData.model = '';
        }
      }
    }

    // Plugin rules
    if (rule.if?.properties?.plugin?.const === newProviderData.plugin) {
      pluginConfigProperties.value =
        rule.then?.properties?.plugin_config?.properties || {};

      // Handle other plugins normally
      configProperties.value = rule.then?.properties?.config?.properties || {};
      if (isCreateMode.value) {
        const config = extractDefaults(configProperties.value);
        newProviderData.config = config ? { ...config } : {};
      }

      if (isCreateMode.value) {
        const pluginConfig = extractDefaults(pluginConfigProperties.value);
        newProviderData.plugin_config = pluginConfig ? { ...pluginConfig } : {};
      }
    }
  });
};

const toggleCustomProvider = () => {
  customProvider.value = !customProvider.value;
  if (customProvider.value) {
    newProviderData.provider = '';
    newProviderData.model = '';
  } else {
    newProviderData.provider = defaultProvider;
  }

  checkRules();
};

const validateData = async () => {
  if (validate) {
    const res = validate(newProviderData);
    if (res) {
      errors.value = [];
    } else {
      errors.value = validate.errors || [];
      console.error('Validation errors', errors.value);
    }
  }
};

watch(newProviderData, () => {
  error.value = '';
  validateData();
});

const showPluginConfig = computed(() => {
  return !!newProviderData.plugin;
});

const showConfig = computed(() => {
  return !!newProviderData.plugin;
});

const fieldError = computed(() => (prefix: string, key: string) => {
  const matchingError = errors.value.find(
    (error) => error.instancePath === `/${prefix}/${key}`,
  );

  if (matchingError) {
    return matchingError.message;
  } else {
    return null;
  }
});

const configurationError = computed(() => {
  return (
    props.provider &&
    (!props.provider?.is_available || !props.provider?.is_model_available)
  );
});
</script>

<template>
  <Modal @close="emit('close')" @onOpen="tryInitValues" wide>
    <template #title v-if="isCreateMode">New Provider Preset</template>
    <template #title v-else>Provider Preset #{{ provider?.id }}</template>
    <template #info v-if="provider">
      <div
        class="flex flex-row items-center justify-center gap-4 rounded-md text-center"
      >
        <div>
          <span class="opacity-50">Provider:</span> {{ provider.provider }}
        </div>
        <div><span class="opacity-50">Model:</span> {{ provider.model }}</div>
        <div><span class="opacity-50">Plugin:</span> {{ provider.plugin }}</div>
      </div>
    </template>

    <Alert warning v-if="configurationError"
      >There is a problem with this configuration. Make sure your API keys/urls
      environment variables are properly set.
    </Alert>

    <Alert error v-if="!validate">Could not compile provider schema</Alert>

    <template v-else>
      <template v-if="isCreateMode">
        <div>
          <div class="flex justify-between">
            <FieldLabel for="provider"
              >Provider:
              <MoreInfo
                text="The name of the inference provider. Use a standard provider or a custom provider."
            /></FieldLabel>

            <button
              @click="toggleCustomProvider"
              class="mr-1 text-xs opacity-50 hover:opacity-70"
              data-testid="toggle-custom-provider"
            >
              {{
                customProvider ? 'Use standard provider' : 'Use custom provider'
              }}
            </button>
          </div>

          <TextInput
            id="provider"
            v-if="customProvider"
            name="provider"
            v-model="newProviderData.provider"
            required
            testId="input-provider"
            @update:modelValue="checkRules"
            placeholder="i.e. my-provider"
          />
          <SelectInput
            v-else
            id="provider"
            name="provider"
            v-model="newProviderData.provider"
            required
            :options="providerOptions"
            testId="input-provider"
            @update:modelValue="checkRules"
          />
        </div>

        <div>
          <FieldLabel for="model"
            >Model:

            <MoreInfo text="The name of the model." />
          </FieldLabel>
          <TextInput
            v-if="modelOptions.length === 0"
            id="model"
            name="model"
            v-model="newProviderData.model"
            required
            testId="input-model"
            placeholder="i.e. my-model"
          />
          <SelectInput
            v-if="modelOptions.length > 0"
            id="plugin"
            name="plugin"
            v-model="newProviderData.model"
            :options="availableModelOptions"
            required
            testId="input-model"
          />
        </div>

        <div v-if="!isPluginLocked">
          <FieldLabel for="plugin"
            >Plugin:

            <MoreInfo
              text="When using a custom provider, a plugin will be used to interface with the provider."
            />
          </FieldLabel>

          <SelectInput
            id="plugin"
            name="plugin"
            v-model="newProviderData.plugin"
            :options="availablePluginOptions"
            :disabled="isPluginLocked"
            required
            testId="input-plugin"
            @update:modelValue="checkRules"
          />
        </div>

        <Alert warning v-if="presetAlreadyExists">
          A preset with this provider and model already exists.
        </Alert>
      </template>

      <div v-if="showPluginConfig">
        <FieldLabel
          >Config:

          <MoreInfo
            text="The parameters used to interface with the provider."
          />
        </FieldLabel>

        <div
          class="flex flex-col gap-2 rounded-md bg-slate-100 px-2 py-2 dark:bg-black dark:bg-opacity-10"
        >
          <ConfigField
            v-for="(property, key) in pluginConfigProperties"
            :key="key"
            :name="key"
            :property="property"
            v-model="newProviderData.plugin_config[key]"
            :error="fieldError('plugin_config', key)"
          />
        </div>
      </div>

      <div v-if="showConfig">
        <div class="flex flex-row items-center gap-2">
          <FieldLabel
            >Default Validator Config:
            <MoreInfo
              text="When creating a new validator, this config will be used as default."
          /></FieldLabel>

          <GhostBtn
            @click="isConfigExpanded = !isConfigExpanded"
            class="text-xs"
          >
            <Eye v-if="!isConfigExpanded" :size="14" />
            <EyeOff v-else :size="14" />
            {{ isConfigExpanded ? 'Hide' : 'Show' }}
          </GhostBtn>
        </div>

        <div
          v-show="isConfigExpanded"
          class="flex flex-col gap-2 rounded-md bg-slate-100 px-2 py-2 dark:bg-black dark:bg-opacity-10"
        >
          <ConfigField
            v-for="(property, key) in configProperties"
            :key="key"
            :name="key"
            :property="property"
            v-model="newProviderData.config[key]"
            :error="fieldError('config', key)"
          />
        </div>
      </div>

      <!-- We can use this in the future if we want to display all errors -->
      <!-- <div v-for="error in errors" class="text-xs text-red-500">
        {{ error.instancePath }}: {{ error.message }}
      </div> -->

      <Alert error v-if="error" type="error">{{ error }}</Alert>

      <Btn
        v-if="isCreateMode"
        @click="handleCreateProvider"
        :disabled="errors.length > 0 || presetAlreadyExists"
        testId="btn-create-provider"
        :loading="isLoading"
      >
        Create
      </Btn>

      <Btn
        v-if="!isCreateMode && provider"
        @click="handleUpdateProvider(provider)"
        :disabled="errors.length > 0"
        testId="btn-update-provider"
        :loading="isLoading"
      >
        Save
      </Btn>
    </template>
  </Modal>
</template>
