<script setup lang="ts">
import { ref, computed } from 'vue';
import PageSection from '@/components/Simulator/PageSection.vue';
import FieldError from '@/components/global/fields/FieldError.vue';
import NumberInput from '@/components/global/inputs/NumberInput.vue';
import { useConsensusStore } from '@/stores';

const consensusStore = useConsensusStore();
const maxRotations = computed({
  get: () => consensusStore.maxRotations,
  set: (newTime) => {
    if (isMaxRotationsValid(newTime)) {
      consensusStore.setMaxRotations(newTime);
    }
  },
});

function isMaxRotationsValid(value: number) {
  return Number.isInteger(value) && value >= 0;
}
</script>

<template>
  <PageSection>
    <template #title>Consensus</template>

    <div class="p-2">
      <div class="flex flex-wrap items-center gap-2">
        <label for="maxRotations" class="text-xs">Max Rotations</label>
        <NumberInput
          id="maxRotations"
          name="maxRotations"
          :min="0"
          :step="1"
          v-model.number="maxRotations"
          required
          testId="input-maxRotations"
          :disabled="false"
          class="h-6 w-12"
          tiny
        />
      </div>

      <FieldError v-if="!isMaxRotationsValid"
        >Please enter a positive integer.</FieldError
      >
    </div>
  </PageSection>
</template>
