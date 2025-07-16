<script setup lang="ts">
import { computed } from 'vue';
import PageSection from '@/components/Simulator/PageSection.vue';
import FieldError from '@/components/global/fields/FieldError.vue';
import NumberInput from '@/components/global/inputs/NumberInput.vue';
import { useConsensusStore } from '@/stores';

const consensusStore = useConsensusStore();
const maxRotations = computed({
  get: () => consensusStore.maxRotations,
  set: (newRotations) => {
    consensusStore.setMaxRotations(Number(newRotations));
  },
});

const isMaxRotationsValid = computed(() => {
  return (
    !isNaN(maxRotations.value) &&
    Number.isInteger(maxRotations.value) &&
    maxRotations.value >= 0
  );
});

const maxAppealRound = computed({
  get: () => consensusStore.maxAppealRound,
  set: (newAppealRound) => {
    consensusStore.setMaxAppealRound(Number(newAppealRound));
  },
});

const isMaxAppealRoundValid = computed(() => {
  return (
    !isNaN(maxAppealRound.value) &&
    Number.isInteger(maxAppealRound.value) &&
    maxAppealRound.value >= 0
  );
});
</script>

<template>
  <PageSection>
    <template #title>Consensus</template>

    <div class="p-1">
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
          :invalid="!isMaxRotationsValid"
          :disabled="false"
          class="h-6 w-12"
          tiny
        />
      </div>

      <FieldError v-if="!isMaxRotationsValid"
        >Please enter a positive integer.</FieldError
      >
    </div>

    <div class="p-1">
      <div class="flex flex-wrap items-center gap-2">
        <label for="maxAppealRound" class="text-xs">Max Appeal Rounds</label>
        <NumberInput
          id="maxAppealRound"
          name="maxAppealRound"
          :min="0"
          :step="1"
          v-model.number="maxAppealRound"
          required
          testId="input-maxAppealRound"
          :invalid="!isMaxAppealRoundValid"
          :disabled="false"
          class="h-6 w-12"
          tiny
        />
      </div>

      <FieldError v-if="!isMaxAppealRoundValid"
        >Please enter a positive integer.</FieldError
      >
    </div>
  </PageSection>
</template>
