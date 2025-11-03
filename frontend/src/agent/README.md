# AI Agent (Studio)

This module provides:
- Determinism linter utilities for GenLayer contracts
- Monaco adapter to display diagnostics
- Contextual contract snippets
- Simple command catalog and runner

Integration sketch (example):

```ts
import * as monaco from 'monaco-editor';
import { setDeterminismMarkers, AgentCatalog, runAgentCommand } from '@/agent';

editor.onDidChangeModelContent(() => {
  const model = editor.getModel();
  if (model) setDeterminismMarkers(monaco, model);
});

function runCmd(id: string) {
  runAgentCommand(id as any, {
    getCode: () => editor.getValue(),
    setCode: (code) => editor.setValue(code),
  });
}
```

No existing files were modified; this folder is additive.

