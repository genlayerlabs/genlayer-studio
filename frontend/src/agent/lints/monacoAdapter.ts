import { lintDeterminism } from './determinismRules';

// Use loose typings to be compatible with both ESM and CJS Monaco builds
export function setDeterminismMarkers(
  monaco: any,
  model: any,
  owner = 'determinism-lint',
) {
  if (!monaco?.editor || !model) return;
  const diags = lintDeterminism(model.getValue());
  const markers = diags.map((d) => ({
    severity: monaco.MarkerSeverity?.Error ?? 8,
    message: d.message,
    startLineNumber: d.line + 1,
    startColumn: d.col + 1,
    endLineNumber: d.line + 1,
    endColumn: d.col + 2,
  }));
  monaco.editor.setModelMarkers(model, owner, markers);
}


