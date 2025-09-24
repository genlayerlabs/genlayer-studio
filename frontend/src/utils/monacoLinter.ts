/**
 * Monaco Editor integration for GenVM Linter (TypeScript version)
 * This file provides linting functionality for GenLayer contracts in Monaco Editor
 */

import type * as Monaco from 'monaco-editor/esm/vs/editor/editor.api';
import { RpcClient } from '@/clients/rpc';
import type { JsonRPCResponse } from '@/types';

interface LintResult {
  rule_id: string;
  message: string;
  severity: 'error' | 'warning' | 'info';
  line: number;
  column: number;
  filename: string;
  suggestion: string | null;
}

interface LintResponse {
  results: LintResult[];
  summary: {
    total: number;
    by_severity: {
      error: number;
      warning: number;
      info: number;
    };
  };
}

/**
 * Lint GenVM code and update Monaco Editor markers
 */
export async function lintGenVMCode(
  editor: Monaco.editor.IStandaloneCodeEditor,
  monaco: typeof Monaco
) {
  const code = editor.getValue();
  console.log('[Monaco Linter] Starting lint, code length:', code.length);

  try {
    const rpcClient = new RpcClient();
    const response: JsonRPCResponse<LintResponse> = await rpcClient.call({
      method: 'sim_lintContract',
      params: {
        source_code: code,
        filename: 'contract.py'
      }
    });

    console.log('[Monaco Linter] Response received:', response);

    if (response.error) {
      console.error('[Monaco Linter] Error from server:', response.error);
      return;
    }

    if (response.result) {
      const { results, summary } = response.result;
      console.log(`[Monaco Linter] Processing ${summary.total} issues`);

      // Convert linter results to Monaco markers
      const markers: Monaco.editor.IMarkerData[] = results.map(err => ({
        severity: err.severity === 'error' ?
          monaco.MarkerSeverity.Error :
          err.severity === 'warning' ?
          monaco.MarkerSeverity.Warning :
          monaco.MarkerSeverity.Info,
        startLineNumber: err.line,
        startColumn: err.column,
        endLineNumber: err.line,
        endColumn: err.column + 10, // Approximate end column
        message: err.suggestion ?
          `${err.message}\n💡 ${err.suggestion}` :
          err.message,
        code: err.rule_id,
        source: 'GenVM Linter'
      }));

      // Set markers on the model
      const model = editor.getModel();
      if (model) {
        monaco.editor.setModelMarkers(model, 'genvm-linter', markers);
        console.log(`[Monaco Linter] Set ${markers.length} markers`);
      }
    }
  } catch (error) {
    console.error('[Monaco Linter] Failed to lint code:', error);
  }
}

/**
 * Setup automatic linting on content change with debouncing
 */
export function setupAutoLinting(
  editor: Monaco.editor.IStandaloneCodeEditor,
  monaco: typeof Monaco,
  debounceMs: number = 1000
): () => void {
  let lintTimeout: NodeJS.Timeout;
  console.log('[Monaco Linter] Setting up auto-linting with', debounceMs, 'ms debounce');

  // Lint on content change with debouncing
  const disposable = editor.onDidChangeModelContent(() => {
    clearTimeout(lintTimeout);
    lintTimeout = setTimeout(() => {
      console.log('[Monaco Linter] Content changed, triggering lint...');
      lintGenVMCode(editor, monaco);
    }, debounceMs);
  });

  // Initial linting
  console.log('[Monaco Linter] Performing initial lint...');
  lintGenVMCode(editor, monaco);

  // Return cleanup function
  return () => {
    console.log('[Monaco Linter] Cleaning up linter...');
    clearTimeout(lintTimeout);
    disposable.dispose();
    // Clear all markers when cleaning up
    const model = editor.getModel();
    if (model) {
      monaco.editor.setModelMarkers(model, 'genvm-linter', []);
    }
  };
}