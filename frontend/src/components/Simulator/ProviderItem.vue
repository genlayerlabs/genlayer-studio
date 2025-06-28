<script setup lang="ts">
import { type ProviderModel } from '@/types';
import {
  CheckCircleIcon,
  PencilSquareIcon,
  TrashIcon,
  ExclamationTriangleIcon,
} from '@heroicons/vue/16/solid';
import ProviderModal from '@/components/Simulator/ProviderModal.vue';
import { ref } from 'vue';
import { useNodeStore } from '@/stores';
import { notify } from '@kyvg/vue3-notification';

const nodeStore = useNodeStore();

const isUpdateModalMopen = ref(false);
const showConfirmDelete = ref(false);

const props = defineProps<{
  provider: ProviderModel;
}>();

async function handleDeleteProvider() {
  try {
    await nodeStore.deleteProvider(props.provider.id);
    notify({
      title: `Deleted ${props.provider.model}`,
      type: 'success',
    });
  } catch (error) {
    console.error(error);
    notify({
      title: 'Error',
      text: (error as Error)?.message || 'Error deleting provider',
      type: 'error',
    });
  }
}
</script>

<template>
  <div
    data-testid="provider-item"
    class="group flex cursor-pointer flex-row items-center justify-between gap-2 bg-slate-100 p-2 hover:bg-slate-200 dark:bg-gray-700 dark:hover:bg-gray-600"
    @click="isUpdateModalMopen = true"
    @mouseleave="showConfirmDelete = false"
  >
    <div class="flex grow flex-row truncate">
      <span
        class="truncate text-sm font-medium"
        data-testid="provider-item-model"
      >
        {{ provider.model }}
      </span>

      <span
        v-if="!provider.is_default"
        class="ml-2 text-xs font-medium text-gray-500"
        data-testid="provider-item-default"
      >
        (Custom)
      </span>

      <ExclamationTriangleIcon
        v-if="!provider.is_available || !provider.is_model_available"
        v-tooltip="'Configuration error'"
        class="h-5 w-5 shrink-0 p-[2px] text-yellow-500"
      />
    </div>

    <div class="hidden flex-row gap-1 group-hover:flex">
      <button
        @click.stop="isUpdateModalMopen = true"
        v-tooltip="'Update Preset'"
      >
        <PencilSquareIcon
          class="h-5 w-5 p-[2px] text-slate-400 transition-colors hover:text-slate-800 active:scale-90 dark:hover:text-white"
        />
      </button>

      <Transition mode="out-in">
        <button
          v-if="!showConfirmDelete"
          data-testid="provider-item-delete"
          @click.stop="showConfirmDelete = true"
          v-tooltip="'Delete Preset'"
        >
          <TrashIcon
            class="h-5 w-5 p-[2px] text-slate-400 transition-colors hover:text-slate-800 active:scale-90 dark:hover:text-white"
          />
        </button>

        <button
          v-else
          data-testid="provider-item-confirm-delete"
          @click.stop="handleDeleteProvider"
          v-tooltip="'Confirm deletion'"
        >
          <CheckCircleIcon
            class="h-5 w-5 p-[2px] text-red-500 transition-colors hover:text-red-400 active:scale-90"
          />
        </button>
      </Transition>
    </div>
  </div>

  <ProviderModal
    :provider="provider"
    :open="isUpdateModalMopen"
    @close="isUpdateModalMopen = false"
  />
</template>

<style scoped>
.v-enter-active,
.v-leave-active {
  transition: all 0.08s ease;
}
.v-enter-from {
  opacity: 0;
  transform: scale(0.8) rotate(45deg);
}
.v-leave-to {
  opacity: 0;
  transform: scale(0.8) rotate(-45deg);
}
</style>
