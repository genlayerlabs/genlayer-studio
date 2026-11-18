<template>
  <component
    :is="as"
    class="inline-block rounded-md bg-slate-400 px-1 py-0.5 text-xs font-semibold text-white dark:bg-gray-200 dark:text-slate-800"
  >
    <slot />
  </component>
</template>

<script setup>
defineProps({
  as: {
    type: String,
    default: 'div',
  },
});
</script>
