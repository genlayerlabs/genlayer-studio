<template>
  <div>
    <div class="relative mb-2 flex flex-row items-center gap-2">
      <div
        class="flex flex-row items-center gap-2 whitespace-nowrap font-semibold"
      >
        <slot name="title" />
      </div>

      <div class="w-full border-t border-gray-300 dark:border-gray-500" />
    </div>

    <slot />
  </div>
</template>
