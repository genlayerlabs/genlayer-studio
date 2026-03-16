<script setup lang="ts">
import { DocumentCheckIcon } from '@heroicons/vue/16/solid';
import { type ContractFile } from '@/types';
import { HomeIcon } from '@heroicons/vue/24/solid';
import { X } from 'lucide-vue-next';
import { onMounted, ref, watch } from 'vue';

const props = defineProps<{
  contract?: ContractFile;
  isHomeTab?: boolean;
  isActive: boolean;
}>();

const emit = defineEmits(['closeContract', 'selectContract']);

const tab = ref<HTMLElement | null>(null);

const tryScrollToTab = () => {
  if (props.isActive && !props.isHomeTab) {
    tab.value?.scrollIntoView();
  }
};

onMounted(tryScrollToTab);
watch(() => props.isActive, tryScrollToTab);
</script>

<template>
  <div
    ref="tab"
    :class="[
      'group relative flex items-center border-r border-r-gray-200 font-semibold dark:border-r-zinc-900',
      !isActive &&
        'bg-gray-100 text-neutral-500 hover:bg-gray-50 dark:bg-zinc-800 hover:dark:bg-zinc-700',
      isActive &&
        'bg-white text-primary hover:bg-white dark:bg-zinc-600 dark:text-white hover:dark:bg-zinc-600',
    ]"
  >
    <button
      v-if="isHomeTab"
      class="flex items-center p-2"
      @click="emit('selectContract')"
    >
      <HomeIcon class="fill-primary' mx-2 h-4 w-4 dark:fill-white" />
    </button>

    <template v-else>
      <button
        class="flex flex-nowrap items-center gap-1 whitespace-nowrap p-2 pr-8"
        @click="emit('selectContract')"
        @click.middle="emit('closeContract')"
      >
        <DocumentCheckIcon
          class="h-4 w-4"
          :class="{ 'fill-primary dark:fill-white': isActive }"
        />
        {{ contract?.name }}
      </button>

      <button
        :class="[
          'absolute right-2 rounded p-[3px] transition-colors hover:bg-gray-200 dark:hover:bg-zinc-500',
          isActive && 'opacity-50 hover:opacity-100',
          !isActive &&
            'opacity-0 hover:!opacity-100 group-hover:opacity-70 dark:hover:text-gray-300',
        ]"
        @click="emit('closeContract')"
        @click.middle="emit('closeContract')"
      >
        <X :size="12" :stroke-width="3" />
      </button>
    </template>

    <div
      v-if="isActive"
      class="absolute bottom-0 h-[2px] w-full bg-primary dark:bg-accent"
    />
  </div>
</template>
