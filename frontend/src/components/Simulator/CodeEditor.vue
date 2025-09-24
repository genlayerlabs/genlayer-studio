<script setup lang="ts">
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';
// CRITICAL: Import hover contribution for hover functionality to work
import 'monaco-editor/esm/vs/editor/contrib/hover/browser/hoverContribution.js';
// Import workers for Monaco Editor to function properly in ESM/Vite
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker';
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker';
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker';
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';

import { ref, shallowRef, watch, computed, onMounted, onUnmounted } from 'vue';
import { useContractsStore, useUIStore } from '@/stores';
import { type ContractFile } from '@/types';
import pythonSyntax from '@/constants/pythonSyntax';
import { setupAutoLinting } from '@/utils/monacoLinter';

// Configure Monaco Environment for proper worker loading
(self as any).MonacoEnvironment = {
  globalAPI: true, // Enable global API for hover providers to work
  getWorker: (_: any, label: string) => {
    console.log('[Monaco] Loading worker for:', label);
    if (label === 'json') {
      return new jsonWorker();
    }
    if (label === 'css' || label === 'scss' || label === 'less') {
      return new cssWorker();
    }
    if (label === 'html' || label === 'handlebars' || label === 'razor') {
      return new htmlWorker();
    }
    if (label === 'typescript' || label === 'javascript') {
      return new tsWorker();
    }
    // Python and other languages use the default editor worker
    return new editorWorker();
  }
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
      sticky: true  // Keeps tooltip visible when moving mouse over it
    },
    // Ensure hover widgets display correctly
    fixedOverflowWidgets: true
  });
  editorRef.value.onDidChangeModelContent(() => {
    contractStore.updateContractFile(props.contract.id!, {
      content: editorRef.value?.getValue() || '',
      updatedAt: new Date().toISOString(),
    });
  });

  // Setup auto-linting with 500ms debounce for faster feedback
  console.log('[CodeEditor] Setting up GenVM linter...');
  stopLinting = setupAutoLinting(editorRef.value, monaco, 500);
}

onMounted(() => {
  initEditor();
});

onUnmounted(() => {
  if (stopLinting) {
    console.log('[CodeEditor] Cleaning up linter...');
    stopLinting();
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
