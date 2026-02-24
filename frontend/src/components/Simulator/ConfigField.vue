<script setup lang="ts">
import NumberInput from '@/components/global/inputs/NumberInput.vue';
import TextInput from '@/components/global/inputs/TextInput.vue';
import SelectInput from '@/components/global/inputs/SelectInput.vue';
import type { SchemaProperty } from '@/types';
import { computed } from 'vue';

const props = defineProps<{
  name: string;
  property: SchemaProperty;
  error?: string;
}>();

const mapToNullIfEmpty = computed(() => {
  return (
    Array.isArray(props.property.type) && props.property.type.includes('null')
  );
});

const model = defineModel({
  set(value) {
    if (mapToNullIfEmpty.value && value === '') {
      return null;
    }
    return value;
  },
});

const isNumber = computed(
  () =>
    props.property.type === 'number' ||
    props.property.type === 'integer' ||
    (Array.isArray(props.property.type) &&
      (props.property.type.includes('number') ||
        props.property.type.includes('integer'))),
);

const isString = computed(() => {
  return (
    props.property.type === 'string' ||
    (Array.isArray(props.property.type) &&
      props.property.type.includes('string'))
  );
});

const isSelect = computed(() => {
  return props.property.enum;
});

const isSupported = computed(() => {
  const supportedField = isNumber.value || isString.value || isSelect.value;

  if (!supportedField) {
    console.warn('unsupported field', props.name, props.property);
  }

  return supportedField;
});

const tooltip = computed(() => {
  const text = [];
  if (props.property.$comment) {
    text.push(props.property.$comment);
  }
  if (props.property.minimum !== undefined) {
    text.push(`Min: ${props.property.minimum}`);
  }
  if (props.property.maximum !== undefined) {
    text.push(`Max: ${props.property.maximum}`);
  }
  if (props.property.multipleOf !== undefined) {
    text.push(`Multiple of: ${props.property.multipleOf}`);
  }
  return text.join(' <br/> ');
});

const testId = computed(() => {
  return `config-field-${props.name}`;
});
</script>

<template>
  <div v-if="isSupported" class="grid grid-cols-3 items-center gap-2">
    <div class="col-span-1">
      <div
        class="flex flex-row items-center gap-2 font-mono text-xs font-medium"
      >
        {{ name }}
      </div>
      <div v-if="error" class="text-xs text-red-500">{{ error }}</div>
    </div>

    <div class="col-span-2">
      <NumberInput
        v-if="isNumber"
        v-model="model"
        :id="name"
        :name="name"
        :min="property.minimum"
        :max="property.maximum"
        :step="property.multipleOf"
        :invalid="!!error"
        :testId="testId"
        v-tooltip.right="{
          content: tooltip,
          html: true,
          triggers: ['focus'],
          hideTriggers: ['focus'],
        }"
      />

      <TextInput
        v-if="isString"
        v-model="model"
        :id="name"
        :name="name"
        :invalid="!!error"
        :testId="testId"
        v-tooltip.right="{
          content: tooltip,
          html: true,
          triggers: ['focus'],
          hideTriggers: ['focus'],
        }"
      />

      <SelectInput
        v-if="isSelect"
        v-model="model"
        :id="name"
        :name="name"
        :options="property.enum || []"
        :invalid="!!error"
        :testId="testId"
      />
    </div>
  </div>
</template>
