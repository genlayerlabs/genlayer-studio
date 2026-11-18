<script setup lang="ts">
import { PlayIcon } from '@heroicons/vue/24/solid';
import ContractTab from '@/components/simulator/ContractTab.vue';
import { useContractsStore } from '@/stores';
import { computed } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { useSortable } from '@vueuse/integrations/useSortable';
import { ref, nextTick } from 'vue';

const store = useContractsStore();
const router = useRouter();
const route = useRoute();

const handleRunDebug = () => {
  router.push({ name: 'run-debug' });
};

const setCurrentContractTab = (id?: string) => {
  store.setCurrentContractId(id);
};

const handleCloseContract = (id?: string) => {
  store.closeFile(id || '');
};

const contracts = computed(() => {
  return store.contracts.filter((contract) =>
    store.openedFiles.includes(contract.id || ''),
  );
});

const sortableContainer = ref<HTMLElement | null>(null);

useSortable(sortableContainer, contracts.value, {
  animation: 150,
  onUpdate: (e: any) => {
    nextTick(() => {
      store.moveOpenedFile(e.oldIndex, e.newIndex);
    });
  },
});

const handleHorizontalScroll = (event: WheelEvent) => {
  if (!event.shiftKey && event.currentTarget instanceof HTMLElement) {
    event.preventDefault();
    event.currentTarget.scrollLeft += event.deltaY;
  }
};

const showHome = computed(() => store.currentContractId === '');
</script>

<template>
  <nav
    class="flex items-stretch justify-between border-b text-sm dark:border-zinc-700"
  >
    <div
      class="no-scrollbar flex items-stretch justify-start overflow-x-auto"
      @wheel.stop="handleHorizontalScroll"
    >
      <ContractTab
        id="tutorial-welcome"
        :isHomeTab="true"
        :isActive="showHome"
        @selectContract="setCurrentContractTab('')"
      />

      <div ref="sortableContainer" class="flex flex-row">
        <ContractTab
          v-for="contract in store.openedContracts"
          :key="contract.id"
          :contract="contract"
          class="contract-item"
          :id="`contract-item-${contract.id}`"
          :isActive="contract.id === store.currentContractId"
          @closeContract="handleCloseContract(contract.id)"
          @selectContract="setCurrentContractTab(contract.id)"
        />
      </div>
    </div>

    <div>
      <Btn
        v-if="route.name !== 'run-debug' && store.currentContractId"
        class="m-1 flex items-center !p-1"
        @click="handleRunDebug"
        v-tooltip="'Run and Debug'"
        :icon="PlayIcon"
      />
    </div>
  </nav>
</template>
