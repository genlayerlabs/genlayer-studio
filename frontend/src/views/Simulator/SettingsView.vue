<script setup lang="ts">
import { useConfig } from '@/hooks';
import MainTitle from '@/components/Simulator/MainTitle.vue';
import ProviderSection from '@/components/Simulator/settings/ProviderSection.vue';
import ConsensusSection from '@/components/Simulator/settings/ConsensusSection.vue';
import SimulatorSection from '@/components/Simulator/settings/SimulatorSection.vue';

const { canUpdateProviders } = useConfig();
</script>

<template>
  <div class="flex max-h-[93vh] w-full flex-col overflow-y-auto">
    <MainTitle data-testid="settings-page-title">Settings</MainTitle>

    <SimulatorSection />
    <ConsensusSection />
    <ProviderSection v-if="canUpdateProviders" />
  </div>
</template>
