<script setup lang="ts">
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';
// Import hover contribution for hover functionality to work
import 'monaco-editor/esm/vs/editor/contrib/hover/browser/hoverContribution.js';
// Import base editor worker for Monaco to function
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';

import { ref, shallowRef, watch, computed, onMounted, onUnmounted } from 'vue';
import { useContractsStore, useUIStore } from '@/stores';
import { type ContractFile } from '@/types';
import pythonSyntax from '@/constants/pythonSyntax';
import { setupAutoLinting } from '@/services/monacoLinter';

// Configure Monaco Environment for Python editor
(self as any).MonacoEnvironment = {
  globalAPI: true, // Enable global API for hover providers to work
  getWorker: () => {
    // Python uses the default editor worker
    return new editorWorker();
  },
};

const uiStore = useUIStore();
const contractStore = useContractsStore();
const props = defineProps<{
  contract: ContractFile;
}>();

const editorElement = ref<HTMLDivElement | null>(null);
const containerElement = ref<HTMLElement | null | undefined>(null);
const editorRef = shallowRef<monaco.editor.IStandaloneCodeEditor | null>(null);
const theme = computed(() => (uiStore.mode === 'light' ? 'vs' : 'vs-dark'));
let stopLinting: (() => void) | null = null;

function initEditor() {
  containerElement.value = editorElement.value?.parentElement;
  monaco.languages.register({ id: 'python' });
  monaco.languages.setMonarchTokensProvider('python', pythonSyntax);
  editorRef.value = monaco.editor.create(editorElement.value!, {
    value: props.contract.content || '',
    language: 'python',
    theme: theme.value,
    automaticLayout: true,
    formatOnPaste: true,
    formatOnType: true,
    // Enable hover explicitly for marker tooltips
    hover: {
      enabled: true,
      delay: 500,
      sticky: true, // Keeps tooltip visible when moving mouse over it
    },
    // Ensure hover widgets display correctly
    fixedOverflowWidgets: true,
  });
  editorRef.value.onDidChangeModelContent(() => {
    contractStore.updateContractFile(props.contract.id!, {
      content: editorRef.value?.getValue() || '',
      updatedAt: new Date().toISOString(),
    });
  });

  // Setup auto-linting with 300ms debounce for faster feedback
  stopLinting = setupAutoLinting(editorRef.value, monaco, 300);
}

onMounted(() => {
  initEditor();
});

onUnmounted(() => {
  if (stopLinting) {
    stopLinting();
  }
  if (editorRef.value) {
    editorRef.value.dispose();
    editorRef.value = null;
  }
});

watch(
  () => uiStore.mode,
  (newValue) => {
    if (editorRef.value)
      editorRef.value.updateOptions({
        theme: newValue === 'light' ? 'vs' : 'vs-dark',
      });
  },
);
</script>

<template>
  <div ref="editorElement" class="h-full w-full"></div>
</template>
