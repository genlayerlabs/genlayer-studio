<script setup lang="ts">
import { nextTick, ref, watch, computed, type ComputedRef } from 'vue';
import { useNodeStore, useUIStore } from '@/stores';
import JsonViewer from '@/components/JsonViewer/json-viewer.vue';
import GhostBtn from '../global/GhostBtn.vue';
import EmptyListPlaceholder from './EmptyListPlaceholder.vue';
import { Ban, SearchIcon, X } from 'lucide-vue-next';
import LogFilterBtn from '@/components/Simulator/LogFilterBtn.vue';
import TextInput from '../global/inputs/TextInput.vue';
import { useScroll } from '@vueuse/core';
import { useTemplateRef } from 'vue';
import type { NodeLog } from '@/types';
import { splitTopLevelLines } from './parsers/splitTopLevelLines';

const nodeStore = useNodeStore();
const uiStore = useUIStore();
const scrollContainer = useTemplateRef<HTMLElement>('scrollContainer');
const { y, isScrolling, arrivedState, directions } = useScroll(scrollContainer);

type ColorMapType = {
  [key: string]: string;
};

const colorMap: ComputedRef<ColorMapType> = computed(() => ({
  info: 'text-blue-400',
  error: 'text-red-400',
  warning: 'text-yellow-500',
  success: 'text-green-500',
}));

const wasAtBottom = ref(false);

watch(
  () => nodeStore.logs.length,
  () => {
    nextTick(() => {
      if (!wasAtBottom.value && scrollContainer.value) {
        scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight;
      }
    });
  },
);

watch(y, () => {
  if (directions.top) {
    wasAtBottom.value = true;
  } else if (arrivedState.bottom) {
    wasAtBottom.value = false;
  }
});

const scopes = ref(['RPC', 'GenVM', 'Consensus', 'Contract']);
const statuses = ref(['info', 'success', 'error']);

const selectedScopes = ref(scopes.value);
const selectedStatuses = ref(['info', 'success', 'error']);

const toggleCategory = (category: string) => {
  if (selectedScopes.value.includes(category)) {
    selectedScopes.value = selectedScopes.value.filter((c) => c !== category);
  } else {
    selectedScopes.value.push(category);
  }
};

const toggleStatus = (status: string) => {
  if (selectedStatuses.value.includes(status)) {
    selectedStatuses.value = selectedStatuses.value.filter((s) => s !== status);
  } else {
    selectedStatuses.value.push(status);
  }
};

// Helper function to parse stdout logs into individual contract print entries
const parseStdoutLogs = (log: NodeLog): NodeLog[] => {
  if (
    !log.data?.stdout ||
    typeof log.data.stdout !== 'string' ||
    !log.data.stdout.trim()
  ) {
    return [log];
  }

  const logs: NodeLog[] = [log];
  const normalizedStdout = log.data.stdout.replace(/\r\n?/g, '\n');
  for (const chunk of splitTopLevelLines(normalizedStdout)) {
    logs.push({
      scope: 'Contract',
      name: 'print',
      type: 'info',
      message: chunk,
      data: null,
    });
  }
  return logs;
};

const filteredLogs = computed(() => {
  return nodeStore.logs.flatMap(parseStdoutLogs).filter((log) => {
    const categoryMatch =
      selectedScopes.value.length === 0 ||
      selectedScopes.value.includes(log.scope);

    const statusMatch =
      selectedStatuses.value.length === 0 ||
      selectedStatuses.value.includes(log.type);

    const searchLower = nodeStore.searchFilter.toLowerCase();
    const searchMatch =
      log.message.toLowerCase().includes(searchLower) ||
      log.scope.toLowerCase().includes(searchLower) ||
      log.name.toLowerCase().includes(searchLower) ||
      (log.data &&
        JSON.stringify(log.data).toLowerCase().includes(searchLower));

    return categoryMatch && statusMatch && searchMatch;
  });
});

const isolateCategory = (category: string) => {
  if (
    selectedScopes.value.includes(category) &&
    selectedScopes.value.length == 1
  ) {
    selectedScopes.value = scopes.value;
  } else {
    selectedScopes.value = [category];
  }
};

const isAnyFilterActive = computed(() => {
  return (
    nodeStore.searchFilter.length > 0 ||
    selectedScopes.value.length !== scopes.value.length ||
    selectedStatuses.value.length !== statuses.value.length
  );
});

const resetFilters = () => {
  nodeStore.searchFilter = '';
  selectedScopes.value = scopes.value;
  selectedStatuses.value = statuses.value;
};
</script>

<template>
  <div class="flex h-full w-full flex-col">
    <div
      class="flex flex-row items-center gap-1 border-b bg-white px-2 py-1 dark:border-b-zinc-700 dark:bg-zinc-800"
    >
      <span class="text-sm">Logs</span>

      <div class="flex grow flex-row items-center gap-2">
        <div class="grow"></div>

        <div class="relative flex max-w-[300px] grow">
          <div
            class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-1"
          >
            <SearchIcon class="h-3 w-3 text-gray-500" aria-hidden="true" />
          </div>

          <TextInput
            id="searchLogs"
            name="searchLogs"
            type="text"
            v-model="nodeStore.searchFilter"
            placeholder="Filter by hash, method, etc."
            class="rounded py-1 pl-5 pr-5 text-xs"
          />

          <div
            v-if="nodeStore.searchFilter.length > 0"
            @click="nodeStore.searchFilter = ''"
            class="absolute inset-y-0 right-0 flex cursor-pointer items-center p-1 opacity-50 hover:opacity-100"
          >
            <X class="h-3 w-3 text-gray-300" aria-hidden="true" />
          </div>
        </div>

        <div class="flex flex-row gap-1">
          <LogFilterBtn
            v-for="scope in scopes"
            :key="scope"
            :active="selectedScopes.includes(scope)"
            :icon="scope"
            @click="toggleCategory(scope)"
          >
            {{ scope }}
          </LogFilterBtn>
        </div>

        <div class="flex flex-row gap-1">
          <LogFilterBtn
            v-for="status in statuses"
            :key="status"
            :active="selectedStatuses.includes(status)"
            :icon="status"
            @click="toggleStatus(status)"
            :class="colorMap[status]"
          >
            {{ status }}
          </LogFilterBtn>
        </div>

        <GhostBtn
          @click="nodeStore.clearLogs"
          v-tooltip="{ content: 'Clear Logs', placement: 'top' }"
          class="opacity-50"
        >
          <Ban :size="14" />
        </GhostBtn>
      </div>
    </div>

    <div
      id="tutorial-node-output"
      class="flex h-full w-full cursor-text flex-col overflow-y-auto bg-slate-50 dark:bg-zinc-900"
    >
      <div
        v-show="filteredLogs.length > 0"
        class="relative flex flex-col overflow-y-auto scroll-smooth"
        ref="scrollContainer"
      >
        <div
          v-for="({ scope, type, message, data }, index) in filteredLogs"
          :key="index"
          class="flex flex-row border-b border-gray-200 px-1 py-1 font-mono text-[10px] first-line:items-center hover:bg-white dark:border-zinc-800 dark:hover:bg-zinc-800"
        >
          <div class="flex flex-row items-start gap-1">
            <button
              class="rounded border bg-white px-[3px] py-[1px] dark:border-zinc-700 dark:bg-zinc-800"
              @click="isolateCategory(scope)"
            >
              {{ scope }}
            </button>

            <pre :class="colorMap[type]">{{ message }}</pre>

            <!-- Show stderr directly for GenVM errors -->
            <pre
              class="ml-2 whitespace-pre-wrap rounded bg-red-50 p-2 font-mono text-[10px] text-red-400 dark:bg-red-900/20"
              v-if="
                data && scope === 'GenVM' && type === 'error' && data.stderr
              "
              >{{ data.stderr }}</pre
            >
            <!-- Show JSON viewer for other data -->
            <JsonViewer
              class="ml-2"
              v-else-if="data"
              :value="data"
              :theme="uiStore.mode === 'light' ? 'light' : 'dark'"
              :expand="false"
              sort
            />
          </div>
        </div>
      </div>

      <div
        v-show="filteredLogs.length < 1"
        class="grid h-full w-full place-items-center"
      >
        <div>
          <EmptyListPlaceholder>
            No logs found.
            <button
              v-if="isAnyFilterActive"
              class="text-center underline"
              @click="resetFilters"
            >
              Reset filters
            </button>
          </EmptyListPlaceholder>
        </div>
      </div>
    </div>
  </div>
</template>

<style>
.jv-code {
  padding: 0 !important;
}

.logs-small-text {
  font-size: 0.5rem;
}
</style>
