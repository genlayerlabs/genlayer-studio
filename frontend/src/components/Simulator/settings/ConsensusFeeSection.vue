<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { useRpcClient } from '@/hooks';
import PageSection from '@/components/Simulator/PageSection.vue';
import Btn from '@/components/global/Btn.vue';
import FieldError from '@/components/global/fields/FieldError.vue';
import { useConsensusStore } from '@/stores';
import {
  feeFields,
  feeTimeoutError,
  isConsistentSchedule,
  isValidFeeSetting,
  type FeePolicy,
} from '@/utils/consensusFees';

const consensusStore = useConsensusStore();
const settings = reactive({ ...consensusStore.feeSettings });
const error = ref('');
const saved = ref(false);
const policy = ref<FeePolicy>();

async function loadPolicy() {
  try {
    policy.value = (await useRpcClient().getFeeConfig()).policy;
    return true;
  } catch {
    return false;
  }
}

onMounted(loadPolicy);

async function save() {
  saved.value = false;
  const hasTimeout =
    settings.leaderTimeunitsAllocation.trim() ||
    settings.validatorTimeunitsAllocation.trim();
  if (hasTimeout && !policy.value && !(await loadPolicy())) {
    error.value =
      'Could not load the fee policy from Studio, so timeouts cannot be validated. Try again once Studio is reachable.';
    return;
  }
  for (const field of Object.keys(feeFields) as (keyof typeof feeFields)[]) {
    if (!isValidFeeSetting(field, settings[field])) {
      error.value = `Enter a valid value for ${feeFields[field]}.`;
      return;
    }
  }

  for (const field of [
    'leaderTimeunitsAllocation',
    'validatorTimeunitsAllocation',
  ] as const) {
    const timeoutError = feeTimeoutError(field, settings[field], policy.value);
    if (timeoutError) {
      error.value = timeoutError;
      return;
    }
  }

  const rounds = settings.appealRounds.trim();
  const rotations = settings.rotations.trim();
  if (!isConsistentSchedule(settings)) {
    error.value =
      'Enter one rotation count for the initial round and each appeal round.';
    return;
  }

  const persisted = consensusStore.setFeeSettings({
    leaderTimeunitsAllocation: settings.leaderTimeunitsAllocation.trim(),
    validatorTimeunitsAllocation: settings.validatorTimeunitsAllocation.trim(),
    appealRounds: rounds,
    rotations,
  });
  if (!persisted) {
    error.value =
      'Applied for this session only: the browser blocked saving the settings.';
    return;
  }
  error.value = '';
  saved.value = true;
}
</script>

<template>
  <PageSection>
    <template #title>Transaction Fees</template>

    <form
      class="space-y-3 p-2 @container"
      @submit.prevent="save"
      @input="saved = false"
    >
      <p class="text-xs">
        These settings apply to deployments and write methods. Leave a field
        empty to use the Studio default.
      </p>
      <div class="grid gap-3 @md:grid-cols-2">
        <label
          v-for="(label, field) in feeFields"
          :key="field"
          class="flex flex-col gap-1 text-xs"
        >
          {{ label }}
          <input
            v-model="settings[field]"
            :inputmode="field === 'rotations' ? 'text' : 'numeric'"
            :placeholder="field === 'rotations' ? 'e.g. 0, 1, 0' : 'Automatic'"
            class="input-style"
            :aria-describedby="
              field === 'rotations' ? 'fee-rotations-help' : undefined
            "
          />
          <span v-if="field === 'rotations'" id="fee-rotations-help">
            Separate counts with commas, starting with the initial round.
          </span>
        </label>
      </div>
      <FieldError v-if="error" role="alert">{{ error }}</FieldError>
      <div class="flex items-center gap-2">
        <Btn tiny type="submit">Save fee settings</Btn>
        <span v-if="saved" role="status" class="text-xs">Saved</span>
      </div>
    </form>
  </PageSection>
</template>
