<script setup lang="ts">
import { ExclamationTriangleIcon } from '@heroicons/vue/20/solid';
import { useConnectionStatusStore } from '@/stores/connectionStatus';
import { storeToRefs } from 'pinia';

const connectionStatusStore = useConnectionStatusStore();
const { isConnected } = storeToRefs(connectionStatusStore);
</script>

<template>
  <Transition
    enter-active-class="transition-all duration-300 ease-out"
    enter-from-class="opacity-0 -translate-y-full"
    enter-to-class="opacity-100 translate-y-0"
    leave-active-class="transition-all duration-200 ease-in"
    leave-from-class="opacity-100 translate-y-0"
    leave-to-class="opacity-0 -translate-y-full"
  >
    <div
      v-if="!isConnected"
      class="flex items-center justify-center gap-2 bg-amber-500 px-4 py-2 text-sm font-medium text-white dark:bg-amber-600"
    >
      <ExclamationTriangleIcon class="h-5 w-5" aria-hidden="true" />
      <span>Connection lost. Attempting to reconnect...</span>
    </div>
  </Transition>
</template>
