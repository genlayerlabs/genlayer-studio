<script setup lang="ts">
import { computed } from 'vue';
import PageSection from '@/components/Simulator/PageSection.vue';
import { useConsensusStore } from '@/stores';
import MoreInfo from '@/components/global/MoreInfo.vue';
import BooleanField from '@/components/global/fields/BooleanField.vue';
import ConsensusInputSection from './ConsensusInputSection.vue';
import type { ConsensusInput } from '@/types/store';

const consensusStore = useConsensusStore();

const feesEnabled = computed({
  get: () => consensusStore.feesEnabled,
  set: (value) => consensusStore.setFeesEnabled(value),
});

const createConsensusInput = (
  storeValue: number,
  label: string,
  id: string,
  setter: (value: number) => void,
): ConsensusInput => {
  return {
    value: storeValue,
    label,
    id,
    testId: `input-${id}`,
    setter,
  };
};

const timeUnitsInputs = computed(() => [
  createConsensusInput(
    consensusStore.leaderTimeoutFee,
    'Leader',
    'leaderTimeoutFee',
    consensusStore.setLeaderTimeoutFee,
  ),
  createConsensusInput(
    consensusStore.validatorsTimeoutFee,
    'Validators',
    'validatorsTimeoutFee',
    consensusStore.setValidatorsTimeoutFee,
  ),
]);

const appealInputs = computed(() => [
  createConsensusInput(
    consensusStore.appealRoundFee,
    'Rounds',
    'appealRoundFee',
    consensusStore.setAppealRoundFee,
  ),
]);

const rotationInputs = computed(() =>
  consensusStore.rotationsFee.map((fee, index) =>
    createConsensusInput(
      fee,
      `Round ${index + 1}`,
      `rotationsFee_${index}`,
      (value: number) => consensusStore.setRotationsFee(index, value),
    ),
  ),
);
</script>

<template>
  <PageSection>
    <template #title>
      Consensus
      <MoreInfo
        text="Configure consensus parameters for transaction processing and validation."
      />
    </template>

    <div class="space-y-2">
      <BooleanField
        v-model="feesEnabled"
        name="feesEnabled"
        label="Enable Fees"
        data-testid="toggle-fees"
      />

      <template v-if="feesEnabled">
        <ConsensusInputSection
          title="Time Units Allocation"
          :inputs="timeUnitsInputs"
        />

        <ConsensusInputSection
          title="Appeal Configuration"
          :inputs="appealInputs"
        />

        <ConsensusInputSection
          v-if="rotationInputs.length > 0"
          title="Rotations Per Round"
          :inputs="rotationInputs"
        />
      </template>
    </div>
  </PageSection>
</template>
