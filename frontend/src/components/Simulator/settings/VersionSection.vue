<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRpcClient } from '@/hooks';
import PageSection from '@/components/Simulator/PageSection.vue';

const rpcClient = useRpcClient();
const studioVersion = ref('');
const genvmVersion = ref('');

onMounted(async () => {
  try {
    studioVersion.value = await rpcClient.getStudioVersion();
    genvmVersion.value = await rpcClient.getGenVMVersion();
  } catch (error) {
    console.error('Error fetching versions:', error);
  }
});
</script>

<template>
  <PageSection>
    <template #title>Version</template>

    <div class="p-1">
      <div class="text-sm">
        <div>Studio: {{ studioVersion || '0.0.0' }}</div>
        <div>GenVM: {{ genvmVersion || '0.0.0' }}</div>
      </div>
    </div>
  </PageSection>
</template>
