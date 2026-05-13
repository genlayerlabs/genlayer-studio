<script setup lang="ts">
import { computed } from 'vue';
import { XCircleIcon } from '@heroicons/vue/20/solid';
import { ChevronRight } from 'lucide-vue-next';
import CopyTextButton from '@/components/global/CopyTextButton.vue';
import { parseGenVMError } from '@/utils/genvmErrors';

const props = defineProps<{
  /** Error from a `useQuery` / `try-catch` block. May be Error, string, or any payload. */
  error: unknown;
}>();

const friendly = computed(() => parseGenVMError(props.error));
</script>

<template>
  <div
    class="rounded-md bg-red-500 bg-opacity-10 p-3 ring-1 ring-red-500/20"
    data-testid="friendly-error"
  >
    <div class="flex gap-3">
      <XCircleIcon
        class="mt-0.5 h-5 w-5 flex-shrink-0 text-red-500"
        aria-hidden="true"
      />
      <div class="min-w-0 flex-1">
        <p class="text-sm font-semibold text-red-700 dark:text-red-300">
          {{ friendly.title }}
        </p>
        <p
          class="mt-1 text-sm text-red-700/90 dark:text-red-200/90"
          data-testid="friendly-error-explanation"
        >
          {{ friendly.explanation }}
        </p>

        <pre
          v-if="friendly.fix"
          class="mt-3 overflow-x-auto whitespace-pre-wrap break-words rounded border border-red-500/20 bg-red-500/5 p-2 font-mono text-xs text-red-900 dark:text-red-100"
          data-testid="friendly-error-fix"
          >{{ friendly.fix }}</pre
        >

        <details class="group mt-3">
          <summary
            class="flex cursor-pointer select-none items-center gap-1 text-xs font-medium text-red-600/80 hover:text-red-700 dark:text-red-300/80 dark:hover:text-red-200"
          >
            <ChevronRight
              class="h-3.5 w-3.5 transition-transform group-open:rotate-90"
            />
            Show technical details
          </summary>
          <div
            class="mt-2 flex items-start gap-2 rounded border border-red-500/20 bg-black/30 p-2"
          >
            <pre
              class="min-w-0 flex-1 overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-snug text-gray-200"
              data-testid="friendly-error-raw"
              >{{ friendly.raw }}</pre
            >
            <CopyTextButton :text="friendly.raw" />
          </div>
        </details>
      </div>
    </div>
  </div>
</template>
