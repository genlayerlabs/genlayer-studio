<script setup lang="ts">
import { useConfig } from '@/hooks';
import MainTitle from '@/components/Simulator/MainTitle.vue';
import ProviderSection from '@/components/Simulator/settings/ProviderSection.vue';
import ConsensusSection from '@/components/Simulator/settings/ConsensusSection.vue';
import ConsensusFeeSection from '@/components/Simulator/settings/ConsensusFeeSection.vue';
import { useNetworkStore } from '@/stores/network';
import SimulatorSection from '@/components/Simulator/settings/SimulatorSection.vue';
import ApiKeySection from '@/components/Simulator/settings/ApiKeySection.vue';

const { canUpdateProviders } = useConfig();
const networkStore = useNetworkStore();
</script>

<template>
  <div class="flex max-h-[93vh] w-full flex-col overflow-y-auto">
    <MainTitle data-testid="settings-page-title">Settings</MainTitle>

    <ApiKeySection />
    <SimulatorSection />
    <ConsensusSection />
    <ConsensusFeeSection v-if="networkStore.isStudio" />
    <ProviderSection v-if="canUpdateProviders" />
  </div>
</template>
