<script setup lang="ts">
import { useConfig } from '@/hooks';
import MainTitle from '@/components/simulator/MainTitle.vue';
import ProviderSection from '@/components/simulator/settings/ProviderSection.vue';
import ConsensusSection from '@/components/simulator/settings/ConsensusSection.vue';
import SimulatorSection from '@/components/simulator/settings/SimulatorSection.vue';
import ApiKeySection from '@/components/simulator/settings/ApiKeySection.vue';

const { canUpdateProviders } = useConfig();
</script>

<template>
  <div class="flex max-h-[93vh] w-full flex-col overflow-y-auto">
    <MainTitle data-testid="settings-page-title">Settings</MainTitle>

    <ApiKeySection />
    <SimulatorSection />
    <ConsensusSection />
    <ProviderSection v-if="canUpdateProviders" />
  </div>
</template>
