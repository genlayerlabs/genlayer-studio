<script setup lang="ts">
import { computed } from 'vue';
import TextInput from '../inputs/TextInput.vue';
import { useUniqueId } from '@/hooks';
import { getCalldataParseError } from '@/calldata/errors';
import { AnyFieldValue } from './AnyFieldValue';
import FieldLabel from './FieldLabel.vue';
import FieldError from './FieldError.vue';

const fieldId = useUniqueId('anyField');

const props = defineProps<{
  name: string;
  label: string;
  testId?: string;
  placeholder?: string;
}>();

const model = defineModel({
  set(value) {
    console.assert(
      typeof value === 'string',
      `value typeof ${value} is ${typeof value} ${String(value)}`,
    );
    return new AnyFieldValue(value as string);
  },
});

// Inline feedback when the text cannot be encoded as calldata.
// Empty fields are not flagged here (pristine inputs would always error);
// submitting them still surfaces an error toast.
const parseError = computed(() => {
  const raw = String(model.value ?? '');
  if (raw.trim() === '') {
    return null;
  }
  return getCalldataParseError(raw);
});
</script>

<template>
  <div class="flex w-full flex-col">
    <FieldLabel :for="fieldId" tiny>{{ name }}</FieldLabel>
    <TextInput
      v-model="model"
      v-bind="props"
      :id="fieldId"
      :placeholder="props.placeholder"
      :invalid="!!parseError"
      tiny
    />
    <FieldError v-if="parseError" :data-testid="`field-error-${name}`">
      {{ parseError }}
    </FieldError>
  </div>
</template>
