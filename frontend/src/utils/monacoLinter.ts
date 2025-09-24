/**
 * Monaco Editor integration for GenVM Linter (TypeScript version)
 * This file provides linting functionality for GenLayer contracts in Monaco Editor
 */

import * as Monaco from 'monaco-editor/esm/vs/editor/editor.api';
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

// Store current markers (for potential future use)
let currentMarkers: Monaco.editor.IMarkerData[] = [];

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
      params: [code, 'contract.py']  // Pass as positional arguments in array
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
      const model = editor.getModel();
      const markers: Monaco.editor.IMarkerData[] = results.map(err => {
        // Calculate better end column based on actual line content
        const line = model?.getLineContent(err.line) || '';
        const tokenMatch = line.slice(Math.max(0, err.column - 1)).match(/^[\w\.]+/);
        const endColumn = err.column + (tokenMatch?.[0]?.length || 10);

        const severityIcon = err.severity === 'error' ? '❌' :
                             err.severity === 'warning' ? '⚠️' : 'ℹ️';

        return {
          severity: err.severity === 'error' ?
            Monaco.MarkerSeverity.Error :
            err.severity === 'warning' ?
            Monaco.MarkerSeverity.Warning :
            Monaco.MarkerSeverity.Info,
          startLineNumber: err.line,
          startColumn: err.column,
          endLineNumber: err.line,
          endColumn: endColumn,
          message: err.message, // Clean message without suggestion
          code: `${err.rule_id}`,
          source: 'GenLayer Linter',
          // Add relatedInformation for better hover display
          relatedInformation: err.suggestion && model ? [{
            startLineNumber: err.line,
            startColumn: err.column,
            endLineNumber: err.line,
            endColumn: endColumn,
            message: `💡 ${err.suggestion}`,
            resource: model.uri
          }] : []
        };
      });

      // Set markers on the model - Monaco will handle hover automatically
      if (model) {
        // Use Monaco's native marker system which includes built-in hover
        monaco.editor.setModelMarkers(model, 'genvm-linter', markers);

        // Store for our custom hover provider as backup
        currentMarkers = markers;

        // Log status summary
        const errorCount = markers.filter(m => m.severity === Monaco.MarkerSeverity.Error).length;
        const warningCount = markers.filter(m => m.severity === Monaco.MarkerSeverity.Warning).length;
        const infoCount = markers.filter(m => m.severity === Monaco.MarkerSeverity.Info).length;

        console.log(
          `[Monaco Linter] ✅ Linting complete: ` +
          `${errorCount > 0 ? `❌ ${errorCount} errors ` : ''}` +
          `${warningCount > 0 ? `⚠️ ${warningCount} warnings ` : ''}` +
          `${infoCount > 0 ? `ℹ️ ${infoCount} info ` : ''}` +
          `${markers.length === 0 ? '✨ No issues found!' : ''}`
        );
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
  debounceMs: number = 500
): () => void {
  let lintTimeout: NodeJS.Timeout;
  console.log('[Monaco Linter] Setting up auto-linting with', debounceMs, 'ms debounce');
  console.log('[Monaco Linter] Using Monaco built-in hover for markers');

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
    currentMarkers = [];
  };
}