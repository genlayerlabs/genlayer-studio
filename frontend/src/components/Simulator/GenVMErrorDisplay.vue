<script setup lang="ts">
import { ref, computed } from 'vue';
import { parseGenvmError, type FriendlyError } from '@/utils/genvmErrors';
import { useClipboard } from '@vueuse/core';
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Clipboard,
  ClipboardCheck,
  Lightbulb,
  Wrench,
} from 'lucide-vue-next';

const props = defineProps<{
  /** The raw error text from GenVM / the backend */
  rawError: string;
}>();

const friendlyError = computed<FriendlyError>(() =>
  parseGenvmError(props.rawError),
);

const showRawDetails = ref(false);

const {
  copy: copyRaw,
  copied: rawCopied,
  isSupported: clipboardSupported,
} = useClipboard({ source: computed(() => props.rawError) });
</script>

<template>
  <div
    class="genvm-error-display rounded-lg border border-red-200 bg-red-50 dark:border-red-800/60 dark:bg-red-950/30"
    data-testid="genvm-error-display"
  >
    <!-- Error title + icon -->
    <div class="flex items-start gap-2.5 p-3 pb-2">
      <AlertTriangle
        class="mt-0.5 h-4 w-4 shrink-0 text-red-500 dark:text-red-400"
      />
      <div class="min-w-0 flex-1">
        <div
          class="text-sm font-semibold text-red-700 dark:text-red-300"
          data-testid="genvm-error-title"
        >
          {{ friendlyError.title }}
        </div>
        <p
          class="mt-1 text-xs leading-relaxed text-red-600/90 dark:text-red-300/80"
        >
          {{ friendlyError.reason }}
        </p>
      </div>
    </div>

    <!-- Fix suggestion -->
    <div
      class="mx-3 mb-2 flex items-start gap-2 rounded-md border border-amber-200/80 bg-amber-50 p-2.5 dark:border-amber-700/40 dark:bg-amber-950/20"
    >
      <Lightbulb
        class="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400"
      />
      <div class="min-w-0 flex-1">
        <div
          class="mb-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400"
        >
          How to fix
        </div>
        <pre
          class="whitespace-pre-wrap break-words text-xs leading-relaxed text-amber-800/90 dark:text-amber-300/80"
          data-testid="genvm-error-fix"
          >{{ friendlyError.fix }}</pre
        >
      </div>
    </div>

    <!-- Toggle raw details -->
    <div class="border-t border-red-200/60 dark:border-red-800/40">
      <button
        class="flex w-full items-center gap-1.5 px-3 py-2 text-[11px] font-medium text-red-500/80 transition-colors hover:text-red-600 dark:text-red-400/70 dark:hover:text-red-300"
        data-testid="genvm-error-toggle-details"
        @click="showRawDetails = !showRawDetails"
      >
        <Wrench class="h-3 w-3" />
        Technical Details
        <ChevronDown v-if="!showRawDetails" class="h-3 w-3" />
        <ChevronUp v-else class="h-3 w-3" />
      </button>

      <div v-if="showRawDetails" class="px-3 pb-3">
        <div class="flex items-center justify-between pb-1">
          <span
            class="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500"
          >
            Raw Error
          </span>
          <button
            v-if="clipboardSupported"
            @click.stop="copyRaw(rawError)"
            class="flex items-center gap-1 text-[10px] text-gray-400 transition-colors hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
            data-testid="genvm-error-copy-raw"
          >
            <template v-if="rawCopied">
              <ClipboardCheck class="h-3 w-3" />
              Copied!
            </template>
            <template v-else>
              <Clipboard class="h-3 w-3" />
              Copy
            </template>
          </button>
        </div>
        <pre
          class="max-h-[200px] overflow-auto whitespace-pre-wrap break-all rounded bg-gray-100 p-2 text-[10px] text-gray-600 dark:bg-zinc-900 dark:text-gray-400"
          data-testid="genvm-error-raw"
          >{{ rawError }}</pre
        >
      </div>
    </div>
  </div>
</template>
