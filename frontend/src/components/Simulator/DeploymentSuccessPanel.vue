<script setup lang="ts">
import { computed } from 'vue';
import { getExplorerUrl } from '@/utils/explorerUrl';
import CopyTextButton from '@/components/global/CopyTextButton.vue';
import { ExternalLink, CheckCircle2, Zap, X } from 'lucide-vue-next';

const props = defineProps<{
  contractAddress: string;
  txHash: string;
}>();

const emit = defineEmits(['close', 'interact']);

const explorerUrl = computed(() => getExplorerUrl());
</script>

<template>
  <div
    class="relative mb-4 flex flex-col overflow-hidden rounded-xl border border-emerald-200 bg-gradient-to-b from-emerald-50/50 to-white shadow-sm dark:border-emerald-900/30 dark:from-emerald-950/20 dark:to-zinc-900"
    data-testid="deployment-success-panel"
  >
    <button
      @click="emit('close')"
      class="absolute right-3 top-3 text-emerald-600/50 hover:text-emerald-600 dark:text-emerald-400/50 dark:hover:text-emerald-400"
      aria-label="Close success panel"
    >
      <X class="h-4 w-4" />
    </button>

    <div class="flex items-center gap-2 border-b border-emerald-100 bg-emerald-50 px-4 py-3 dark:border-emerald-900/30 dark:bg-emerald-900/10">
      <CheckCircle2 class="h-5 w-5 text-emerald-500 dark:text-emerald-400" />
      <h3 class="font-semibold text-emerald-800 dark:text-emerald-300">Contract Deployed Successfully</h3>
    </div>

    <div class="flex flex-col gap-3 p-4">
      <div class="flex flex-col gap-1">
        <span class="text-[10px] font-medium uppercase tracking-wider text-slate-500 dark:text-zinc-400">Contract Address</span>
        <div class="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-zinc-700 dark:bg-zinc-800/50">
          <span class="font-mono text-xs text-slate-700 break-all dark:text-zinc-300">{{ contractAddress }}</span>
          <CopyTextButton :text="contractAddress" class="ml-2" />
        </div>
      </div>

      <div class="flex flex-col gap-1">
        <span class="text-[10px] font-medium uppercase tracking-wider text-slate-500 dark:text-zinc-400">Transaction Hash</span>
        <div class="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-zinc-700 dark:bg-zinc-800/50">
          <span class="font-mono text-xs text-slate-700 break-all dark:text-zinc-300">{{ txHash }}</span>
          <CopyTextButton :text="txHash" class="ml-2" />
        </div>
      </div>

      <div class="mt-2 flex flex-row flex-wrap gap-2">
        <a
          v-if="explorerUrl"
          :href="`${explorerUrl}/tx/${txHash}`"
          target="_blank"
          class="inline-flex items-center gap-1.5 rounded-lg bg-white px-4 py-2 text-xs font-medium text-slate-700 shadow-sm ring-1 ring-inset ring-slate-300 transition-colors hover:bg-slate-50 dark:bg-zinc-800 dark:text-zinc-200 dark:ring-zinc-600 dark:hover:bg-zinc-700"
        >
          <ExternalLink class="h-3.5 w-3.5" />
          View on Explorer
        </a>
        <button
          @click="emit('interact')"
          class="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-4 py-2 text-xs font-medium text-white shadow-sm transition-colors hover:bg-emerald-600 dark:bg-emerald-600 dark:hover:bg-emerald-500"
        >
          <Zap class="h-3.5 w-3.5" />
          Interact Now
        </button>
      </div>

      <div class="mt-1 text-[11px] text-slate-500 dark:text-zinc-400">
        ℹ Save your contract address — you'll need it to call this contract from outside the Studio.
      </div>
    </div>
  </div>
</template>
