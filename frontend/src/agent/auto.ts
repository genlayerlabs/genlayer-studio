import { setDeterminismMarkers } from '@/agent/lints/monacoAdapter';
import { storageContractSnippet } from '@/agent/snippets/contracts';

type Params = {
  editor: any;
  monaco: any;
  autoScaffold?: boolean;
};

function debounce<T extends (...args: any[]) => void>(fn: T, ms = 200) {
  let t: any;
  return (...args: Parameters<T>) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export function initAIAgent({ editor, monaco, autoScaffold = false }: Params) {
  // eslint-disable-next-line no-console
  console.info('[AI Agent] init starting');
  const model = editor.getModel();
  if (!model) return;

  const current = model.getValue();
  // eslint-disable-next-line no-console
  console.info('[AI Agent] model length', current.length);
  if (autoScaffold && current.trim().length === 0) {
    const scaffold = storageContractSnippet();
    editor.executeEdits('ai-agent-init', [
      { range: model.getFullModelRange(), text: scaffold, forceMoveMarkers: true },
    ]);
    editor.setPosition({ lineNumber: 1, column: 1 });
    // eslint-disable-next-line no-console
    console.info('[AI Agent] scaffold inserted');
  }

  const runLint = debounce(() => setDeterminismMarkers(monaco, model), 150);
  runLint();
  // eslint-disable-next-line no-console
  console.info('[AI Agent] lint scheduled');
  const disposable = editor.onDidChangeModelContent(runLint);
  editor.onDidDispose(() => disposable.dispose());
}


