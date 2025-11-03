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
  const model = editor.getModel();
  if (!model) return;

  const current = model.getValue();
  if (autoScaffold && current.trim().length === 0) {
    const scaffold = storageContractSnippet();
    editor.executeEdits('ai-agent-init', [
      { range: model.getFullModelRange(), text: scaffold, forceMoveMarkers: true },
    ]);
    editor.setPosition({ lineNumber: 1, column: 1 });
  }

  const runLint = debounce(() => setDeterminismMarkers(monaco, model), 150);
  runLint();
  const disposable = editor.onDidChangeModelContent(runLint);
  editor.onDidDispose(() => disposable.dispose());
}


