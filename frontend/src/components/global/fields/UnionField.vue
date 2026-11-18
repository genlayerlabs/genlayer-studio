<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { ContractParamsSchema } from 'genlayer-js/types';
import { useInputMap } from '@/hooks/useInputMap';
import { useUniqueId } from '@/hooks/useUniqueId';
import SelectInput from '../inputs/SelectInput.vue';
import FieldLabel from './FieldLabel.vue';
import {
  getDefaultUnionIndex,
  getDefaultValueForType,
  getUnionTypes,
} from './fieldValues';

const props = defineProps<{
  schema: ContractParamsSchema;
  name: string;
  label: string;
}>();
const model = defineModel();
const fieldId = useUniqueId('union');
const inputMap = useInputMap();
const types = computed(() => getUnionTypes(props.schema));
const selected = ref(String(getDefaultUnionIndex(types.value)));
const selectedType = computed(() => types.value[Number(selected.value)]!);
const options = computed(() =>
  types.value.map((type, index) => ({
    value: String(index),
    label:
      type === 'null'
        ? 'None'
        : typeof type === 'string'
          ? type
          : JSON.stringify(type),
  })),
);

watch(
  () => props.schema,
  () => {
    selected.value = String(getDefaultUnionIndex(types.value));
    model.value = getDefaultValueForType(selectedType.value);
  },
);
watch(selected, () => {
  model.value = getDefaultValueForType(selectedType.value);
});
</script>

<template>
  <div class="flex w-full flex-col gap-1">
    <FieldLabel :for="fieldId" tiny>{{ label }} type</FieldLabel>
    <SelectInput v-model="selected" :name="fieldId" :options="options" />
    <component
      :is="inputMap.getComponent(selectedType)"
      v-if="selectedType !== 'null'"
      :key="selected"
      v-model="model"
      :schema="selectedType"
      :name="name"
      :label="label"
      :placeholder="
        typeof selectedType === 'string'
          ? selectedType
          : JSON.stringify(selectedType)
      "
    />
  </div>
</template>
