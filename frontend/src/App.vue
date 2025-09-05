<script setup lang="ts">
import { RouterView, useRouter, useRoute } from 'vue-router';
import Header from '@/components/Header.vue';
import Notification from '@/components/Notification.vue';
import TutorialContainer from '@/components/Tutorial/TutorialContainer.vue';
import { useUIStore } from '@/stores/ui';
import { onBeforeMount, onMounted, nextTick, watch } from 'vue';
import { useSetupStores } from '@/hooks';
import { useContractImport } from '@/composables/useContractImport';
import { notify } from '@kyvg/vue3-notification';

const uiStore = useUIStore();
const { setupStores } = useSetupStores();
const router = useRouter();
const route = useRoute();
const { importContract } = useContractImport();

onBeforeMount(() => {
  uiStore.initialize();
  setupStores();
});

// Import handler reused by onMounted and watcher
const applyContractImport = async (importContractAddress: string) => {
  if (!importContractAddress) {
    return;
  }

  if (route.name !== 'contracts') {
    await router.push({ name: 'contracts' });
  }

  notify({
    title: 'Importing contract',
    text: `Fetching contract at ${importContractAddress.slice(0, 10)}...`,
    type: 'info',
  });

  const result = await importContract(importContractAddress);

  const newQuery = { ...route.query };
  delete newQuery['import-contract'];
  await router.replace({
    name: route.name as string,
    query: newQuery,
  });

  if (!result.success) {
    console.error('Failed to import contract:', result.message);
  }
};

// Handle import-contract query parameter after component is mounted
onMounted(async () => {
  await nextTick();

  const param = route.query['import-contract'];
  const initialAddress = Array.isArray(param) ? param[0] : (param as string | undefined);
  if (initialAddress) {
    await applyContractImport(initialAddress);
  }
});

// Watch for changes to the import-contract query parameter
watch(
  () => route.query['import-contract'],
  async (newValue) => {
    const address = Array.isArray(newValue) ? newValue[0] : (newValue as string | undefined);
    if (address) {
      await applyContractImport(address);
    }
  },
);
</script>

<template>
  <TutorialContainer />
  <main class="flex h-screen w-full flex-col">
    <Header />
    <div class="flex" :style="{ height: 'calc(100vh - 53px)' }">
      <RouterView />
    </div>
  </main>
  <Notification />
</template>
