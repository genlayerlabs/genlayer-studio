<script setup lang="ts">
import { type Component } from 'vue';

defineProps<{
  title: string;
  icon: Component;
}>();
</script>

<template>
  <div
    class="items-left flex flex-col gap-2 rounded-md border border-gray-300 bg-slate-100 p-2 dark:border-0 dark:bg-gray-700 dark:bg-opacity-50"
  >
    <component :is="icon" class="h-6 w-6" />

    <h3 class="text-sm font-semibold">
      {{ title }}
    </h3>

    <p class="text-xs">
      <slot />
    </p>
  </div>
</template>
