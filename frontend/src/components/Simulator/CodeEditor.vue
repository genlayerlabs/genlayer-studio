<script setup lang="ts">
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';
// Import core commands for basic editor functionality
import 'monaco-editor/esm/vs/editor/browser/coreCommands.js';
// Import hover contribution for hover functionality to work
import 'monaco-editor/esm/vs/editor/contrib/hover/browser/hoverContribution.js';
// Import suggest contribution for autocomplete functionality
import 'monaco-editor/esm/vs/editor/contrib/suggest/browser/suggestController.js';
// Import base editor worker for Monaco to function
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';

import { ref, shallowRef, watch, computed, onMounted, onUnmounted } from 'vue';
import { useContractsStore, useUIStore } from '@/stores';
import { type ContractFile } from '@/types';
import pythonSyntax from '@/constants/pythonSyntax';
import { setupAutoLinting } from '@/services/monacoLinter';
import { setupGenVMAutocomplete } from '@/services/monaco/monacoAutocomplete';

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

// Track global language registration to avoid duplicates (using window object for true global state)
const PYTHON_REGISTERED_KEY = '__monaco_python_lang_registered__';

function initEditor() {
  containerElement.value = editorElement.value?.parentElement;

  // Register Python language and autocomplete only once globally across all editor instances
  if (!(window as any)[PYTHON_REGISTERED_KEY]) {
    monaco.languages.register({ id: 'python' });
    monaco.languages.setMonarchTokensProvider('python', pythonSyntax);
    setupGenVMAutocomplete(monaco);
    (window as any)[PYTHON_REGISTERED_KEY] = true;
  }

  // Now create the editor
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
    // Autocomplete configuration optimized for GenVM
    quickSuggestions: true,
    wordBasedSuggestions: 'off', // Disable to avoid conflicts with GenVM completions
    snippetSuggestions: 'inline',
    suggestOnTriggerCharacters: true,
    acceptSuggestionOnEnter: 'on',
    tabCompletion: 'on',
    suggest: {
      insertMode: 'replace',
      filterGraceful: true,
      localityBonus: true,
      shareSuggestSelections: false,
      showWords: false,
      showSnippets: true,
      showClasses: true,
      showFunctions: true,
      showModules: true,
    },
  });

  editorRef.value.onDidChangeModelContent(() => {
    contractStore.updateContractFile(props.contract.id!, {
      content: editorRef.value?.getValue() || '',
      updatedAt: new Date().toISOString(),
    });
  });

  // Setup auto-linting with 300ms debounce for faster feedback
  stopLinting = setupAutoLinting(editorRef.value, monaco, 300);

  // Add keyboard shortcuts for triggering suggestions
  editorRef.value.addAction({
    id: 'trigger-genvm-suggest',
    label: 'Trigger GenVM Suggestions',
    keybindings: [
      monaco.KeyMod.Alt | monaco.KeyCode.Space, // Alt+Space
      monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyI, // Cmd+I on Mac, Ctrl+I on Windows
    ],
    run: (editor) => {
      editor.trigger('genvm', 'editor.action.triggerSuggest', {});
    },
  });
}

onMounted(() => {
  initEditor();
});

onUnmounted(() => {
  if (stopLinting) {
    stopLinting();
  }
  // Dispose only the editor instance, not the global autocomplete provider
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
