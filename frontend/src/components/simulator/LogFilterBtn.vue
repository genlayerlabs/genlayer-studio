<script setup lang="ts">
import { computed } from 'vue';
import {
  CircleX,
  CheckCircle,
  Info,
  ArrowDownUp,
  HardDrive,
  Vote,
  FileText,
  UserPen,
  UserSearch,
} from 'lucide-vue-next';

const props = defineProps<{
  active: boolean;
  icon: string;
}>();

const icon = computed(() => {
  switch (props.icon) {
    case 'RPC':
      return ArrowDownUp;
    case 'GenVM':
      return HardDrive;
    case 'Consensus':
      return Vote;
    case 'Contract':
      return FileText;
    case 'error':
      return CircleX;
    case 'info':
      return Info;
    case 'success':
      return CheckCircle;
    case 'Leader':
      return UserPen;
    case 'Validator':
      return UserSearch;
    default:
      return null;
  }
});
</script>

<template>
  <button
    class="flex flex-row items-center gap-1 rounded border border-gray-300 px-1 py-0.5 font-mono text-[10px] font-semibold dark:border-zinc-500"
    :class="[
      active && 'bg-white hover:opacity-90 dark:bg-zinc-700',
      !active &&
        '!text-gray-400 opacity-40 hover:opacity-60 dark:border-zinc-700 dark:bg-zinc-800',
    ]"
  >
    <component :is="icon" :size="13" />
    <slot />
  </button>
</template>
