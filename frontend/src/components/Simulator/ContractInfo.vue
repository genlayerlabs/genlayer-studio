<script setup lang="ts">
import PageSection from '@/components/Simulator/PageSection.vue';
import { CheckCircleIcon } from '@heroicons/vue/24/outline';
import { ArrowPathIcon } from '@heroicons/vue/20/solid';
import EmptyListPlaceholder from '@/components/Simulator/EmptyListPlaceholder.vue';
import { useNodeStore, useUIStore } from '@/stores';
import { useContractQueries, useShortAddress } from '@/hooks';
import { UploadIcon } from 'lucide-vue-next';

const nodeStore = useNodeStore();
const { shorten } = useShortAddress();

defineProps<{
  showNewDeploymentButton: boolean;
}>();

const emit = defineEmits(['openDeployment']);
const { isDeployed, address, contract, upgradeContract, isUpgrading } =
  useContractQueries();
const uiStore = useUIStore();

const upgradeTooltip = `
<div style="text-align: left; max-width: 240px;">
  <div style="margin-bottom: 8px; opacity: 0.85;">
    Replaces deployed code with current editor code.
  </div>
  <div style="display: flex; align-items: flex-start; gap: 6px; color: #6ee7b7; margin-bottom: 4px;">
    <span>✓</span>
    <span>Safe for logic changes</span>
  </div>
  <div style="display: flex; align-items: flex-start; gap: 6px; color: #fcd34d; margin-bottom: 4px;">
    <span>⚠</span>
    <span>Changing field names/types can corrupt or lose data</span>
  </div>
  <div style="display: flex; align-items: flex-start; gap: 6px; color: #f87171;">
    <span>✕</span>
    <span>Irreversible</span>
  </div>
</div>
`;
</script>

<template>
  <PageSection>
    <template #title
      >Contract
      <div data-testid="current-contract-name" class="opacity-50">
        {{ contract?.name }}
      </div></template
    >

    <div
      v-if="isDeployed"
      data-testid="deployed-contract-info"
      class="flex flex-row items-center gap-1 text-xs"
    >
      <CheckCircleIcon class="h-4 w-4 shrink-0 text-emerald-400" />

      Deployed at

      <div class="font-semibold">
        {{ shorten(address) }}
      </div>

      <CopyTextButton :text="address" />
    </div>

    <EmptyListPlaceholder v-else>Not deployed yet.</EmptyListPlaceholder>

    <Alert
      warning
      v-if="
        !uiStore.showTutorial &&
        !nodeStore.isLoadingValidatorData &&
        !nodeStore.hasAtLeastOneValidator
      "
    >
      You need at least one validator before you can deploy or interact with a
      contract.

      <RouterLink :to="{ name: 'validators' }"
        ><Btn secondary tiny class="mt-1">Go to validators</Btn></RouterLink
      >
    </Alert>

    <div
      v-else-if="showNewDeploymentButton"
      class="flex flex-row flex-wrap items-center gap-2"
    >
      <Btn
        secondary
        tiny
        class="inline-flex w-auto shrink grow-0"
        @click="emit('openDeployment')"
        :icon="UploadIcon"
      >
        Deploy new instance
      </Btn>

      <Btn
        v-if="isDeployed"
        v-tooltip="{ content: upgradeTooltip, html: true }"
        secondary
        tiny
        class="inline-flex w-auto shrink grow-0"
        :disabled="isUpgrading"
        @click="upgradeContract"
        :icon="ArrowPathIcon"
      >
        {{ isUpgrading ? 'Upgrading...' : 'Upgrade code' }}
      </Btn>
    </div>
  </PageSection>
</template>
