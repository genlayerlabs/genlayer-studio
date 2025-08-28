<script setup lang="ts">
import NumberInput from '@/components/global/inputs/NumberInput.vue';
import type { ConsensusInput } from '@/types/store';

interface Props {
  title: string;
  inputs: ConsensusInput[];
}

defineProps<Props>();

const isInputValid = (value: number) => {
  return !isNaN(value) && Number.isInteger(value) && value >= 0;
};

const handleValueUpdate = (value: number, setter: (value: number) => void) => {
  if (isInputValid(value)) {
    setter(value);
  }
};
</script>

<template>
  <div>
    <div class="mb-1 text-xs font-semibold opacity-50">{{ title }}</div>
    <div
      class="overflow-hidden rounded-md border border-gray-300 bg-slate-100 dark:border-gray-800 dark:bg-gray-700"
    >
      <div
        v-for="input in inputs"
        :key="input.id"
        class="flex items-center justify-between px-2 py-1.5"
      >
        <div class="truncate text-sm font-medium">{{ input.label }}</div>
        <NumberInput
          :id="input.id"
          :name="input.id"
          :min="0"
          :step="1"
          :model-value="input.value"
          @update:model-value="
            (val) => handleValueUpdate(Number(val), input.setter)
          "
          required
          :testId="input.testId"
          :invalid="!isInputValid(input.value)"
          :disabled="false"
          class="h-6 w-20"
          tiny
        />
      </div>
    </div>
  </div>
</template>
