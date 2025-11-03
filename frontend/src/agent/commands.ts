import { storageContractSnippet, registryContractSnippet } from './snippets/contracts';
import { lintDeterminism } from './lints/determinismRules';

export type AgentCommand =
  | { id: 'insert.storage'; label: string }
  | { id: 'insert.registry'; label: string }
  | { id: 'lint.determinism'; label: string };

export const AgentCatalog: AgentCommand[] = [
  { id: 'insert.storage', label: 'Insert: Storage Contract' },
  { id: 'insert.registry', label: 'Insert: Registry Contract' },
  { id: 'lint.determinism', label: 'Lint: Determinism' },
];

export function runAgentCommand(
  cmd: AgentCommand['id'],
  ctx: {
    getCode: () => string;
    setCode: (code: string) => void;
    showDiagnostics?: (diags: ReturnType<typeof lintDeterminism>) => void;
  },
) {
  if (cmd === 'insert.storage') {
    ctx.setCode(storageContractSnippet());
    return;
  }
  if (cmd === 'insert.registry') {
    ctx.setCode(registryContractSnippet());
    return;
  }
  if (cmd === 'lint.determinism') {
    const diags = lintDeterminism(ctx.getCode());
    ctx.showDiagnostics?.(diags);
    return diags;
  }
}


