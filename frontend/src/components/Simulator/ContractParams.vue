<script setup lang="ts">
import { useInputMap } from '@/hooks';
import type { ContractMethodBase } from 'genlayer-js/types';
import { onMounted, ref, watch } from 'vue';
import {
  getDefaultValueForType,
  getComponentProps,
} from '../global/fields/fieldValues';
import EmptyListPlaceholder from '@/components/Simulator/EmptyListPlaceholder.vue';
import type { ArgData } from './ContractParams';

const props = defineProps<{
  methodBase: ContractMethodBase | undefined;
}>();

const inputMap = useInputMap();

const generation = ref(0);
const args = ref<ArgData | undefined>(undefined);

// Helper to safely get args value for template usage
// Used in v-else branch where args is guaranteed to be defined
function getArg(index: number) {
  return args.value!.args[index]!;
}

function getKwarg(key: string) {
  return args.value!.kwargs[key]!;
}

const setInputParams = (method: ContractMethodBase | undefined) => {
  generation.value += 1;
  if (method === undefined) {
    args.value = undefined;
    return;
  }
  const newArgs: ArgData = {
    args: [],
    kwargs: {},
  };
  let i = 0;
  for (const [_paramName, paramType] of method.params) {
    newArgs.args.push({
      val: getDefaultValueForType(paramType),
      key: i,
    });
    i += 1;
  }
  for (const [paramName, paramType] of Object.entries(method.kwparams)) {
    newArgs.kwargs[paramName] = {
      val: getDefaultValueForType(paramType),
      key: paramName,
    };
  }
  args.value = newArgs;
};

watch(
  () => props.methodBase,
  (newValue) => {
    setInputParams(newValue);
  },
);

const emit = defineEmits<{
  (e: 'argsChanged', newArgs: ArgData): void;
}>();

watch(
  () => args.value,
  (newValue) => {
    if (newValue === undefined) {
      return;
    }
    emit('argsChanged', newValue);
  },
);

onMounted(() => {
  setInputParams(props.methodBase);
});
</script>
<template>
  <EmptyListPlaceholder
    v-if="
      methodBase === undefined ||
      args === undefined ||
      (args.args.length === 0 && Object.keys(args.kwargs).length === 0)
    "
  >
    No parameters.
  </EmptyListPlaceholder>

  <div
    v-else
    class="flex flex-col justify-start gap-1"
    :class="false && 'pointer-events-none opacity-60'"
  >
    <div
      v-for="([paramName, paramType], i) in methodBase.params || []"
      :key="`${generation}:${paramName}`"
    >
      <component
        :is="inputMap.getComponent(paramType)"
        v-model="getArg(i).val"
        v-bind="getComponentProps(paramType)"
        :name="paramName"
        :placeholder="`${paramType}`"
        :label="paramName"
      />
    </div>
    <div
      v-for="[paramName, paramType] in Object.entries(
        methodBase.kwparams || {},
      )"
      :key="`${generation}:${paramName}`"
    >
      <component
        :is="inputMap.getComponent(paramType)"
        v-model="getKwarg(paramName).val"
        v-bind="getComponentProps(paramType)"
        :name="paramName"
        :placeholder="`${paramType}`"
        :label="paramName"
      />
    </div>
  </div>
</template>
