<script setup lang="ts">
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';
// Ensure suggest/snippet features and commands are registered in ESM build
import 'monaco-editor/esm/vs/editor/contrib/suggest/browser/suggestController';
import 'monaco-editor/esm/vs/editor/contrib/snippet/browser/snippetController2';
import { ref, shallowRef, watch, computed, onMounted } from 'vue';
import { useContractsStore, useUIStore } from '@/stores';
import { type ContractFile } from '@/types';
import pythonSyntax from '@/constants/pythonSyntax';
import { initAIAgent } from '@/agent/auto';
import { registerAIAgentProviders } from '@/agent';

const uiStore = useUIStore();
const contractStore = useContractsStore();
const props = defineProps<{
  contract: ContractFile;
}>();

const editorElement = ref<HTMLDivElement | null>(null);
const containerElement = ref<HTMLElement | null | undefined>(null);
const editorRef = shallowRef<monaco.editor.IStandaloneCodeEditor | null>(null);
const theme = computed(() => (uiStore.mode === 'light' ? 'vs' : 'vs-dark'));

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
    quickSuggestions: { other: true, comments: true, strings: true },
    suggestOnTriggerCharacters: true,
  });
  editorRef.value.onDidChangeModelContent(() => {
    contractStore.updateContractFile(props.contract.id!, {
      content: editorRef.value?.getValue() || '',
      updatedAt: new Date().toISOString(),
    });
  });

  // Trigger suggestions when typing '#'
  editorRef.value.onDidType?.((text) => {
    const model = editorRef.value?.getModel();
    const pos = editorRef.value?.getPosition();
    if (!model || !pos) return;
    const line = model.getLineContent(pos.lineNumber).slice(0, pos.column - 1).trim();
    const shouldTrigger =
      text === '#' ||
      (text === '.' && /@gl$/.test(line)) ||
      /^from$/i.test(line) ||
      /^import$/i.test(line) ||
      /^class$/i.test(line) ||
      /^def$/i.test(line) ||
      line.endsWith('@');
    if (shouldTrigger) {
      editorRef.value?.trigger('ai-agent', 'editor.action.triggerSuggest', {});
    }
  });

  // Initialize AI Agent for auto scaffold and determinism linting (safe)
  try {
    if (editorRef.value) {
      initAIAgent({ editor: editorRef.value, monaco, autoScaffold: false });
      // Register completions & hovers once per mount
      registerAIAgentProviders(monaco);
    }
  } catch (e) {
    // swallow init errors
  }
}

onMounted(() => {
  initEditor();
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
