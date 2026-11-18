<template>
  <div class="flex flex-row items-center justify-between gap-2 p-2">
    <h3 class="text-xl leading-none">
      <slot />
    </h3>

    <div v-if="$slots.actions" class="flex flex-row items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
