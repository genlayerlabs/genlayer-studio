<template>
  <div>
    <div
      class="flex w-full flex-row items-center justify-between truncate bg-gray-100 p-2 dark:dark:bg-zinc-700"
    >
      <h4
        class="text-md flex flex-row items-center gap-1 truncate dark:text-zinc-300"
      >
        <slot name="title" />
      </h4>

      <slot name="actions" />
    </div>

    <div class="flex w-full flex-col gap-2 p-2">
      <slot />
    </div>
  </div>
</template>
