/**
 * Monaco Editor integration for GenVM Linter
 * This file provides linting functionality for GenLayer contracts in Monaco Editor
 */

/**
 * Lint GenVM code and update Monaco Editor markers
 * @param {Object} editor - Monaco editor instance
 * @param {Object} monaco - Monaco namespace
 * @param {string} apiEndpoint - JSON-RPC API endpoint (default: '/api')
 */
export async function lintGenVMCode(editor, monaco, apiEndpoint = '/api') {
    const code = editor.getValue();

    try {
        // Call the JSON-RPC endpoint
        const response = await fetch(apiEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jsonrpc: '2.0',
                method: 'sim_lintContract',
                params: {
                    source_code: code,
                    filename: 'contract.py'
                },
                id: Date.now()
            })
        });

        const data = await response.json();

        if (data.error) {
            console.error('Linting error:', data.error);
            return;
        }

        if (data.result) {
            // Convert linter results to Monaco markers
            const markers = data.result.results.map(err => ({
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
            monaco.editor.setModelMarkers(
                editor.getModel(),
                'genvm-linter',
                markers
            );

            // Log summary for debugging
            console.log('Linting complete:', data.result.summary);
        }
    } catch (error) {
        console.error('Failed to lint code:', error);
    }
}

/**
 * Setup automatic linting on content change with debouncing
 * @param {Object} editor - Monaco editor instance
 * @param {Object} monaco - Monaco namespace
 * @param {number} debounceMs - Debounce delay in milliseconds (default: 500)
 * @param {string} apiEndpoint - JSON-RPC API endpoint
 * @returns {Function} Cleanup function to stop linting
 */
export function setupAutoLinting(editor, monaco, debounceMs = 500, apiEndpoint = '/api') {
    let lintTimeout;

    // Lint on content change with debouncing
    const disposable = editor.onDidChangeModelContent(() => {
        clearTimeout(lintTimeout);
        lintTimeout = setTimeout(() => {
            lintGenVMCode(editor, monaco, apiEndpoint);
        }, debounceMs);
    });

    // Initial linting
    lintGenVMCode(editor, monaco, apiEndpoint);

    // Return cleanup function
    return () => {
        clearTimeout(lintTimeout);
        disposable.dispose();
        // Clear all markers when cleaning up
        monaco.editor.setModelMarkers(editor.getModel(), 'genvm-linter', []);
    };
}

/**
 * Example usage in your Monaco Editor setup:
 *
 * import { setupAutoLinting } from './utils/monaco-linter.js';
 *
 * // After creating your Monaco editor instance
 * const editor = monaco.editor.create(container, {
 *     value: initialCode,
 *     language: 'python',
 *     theme: 'vs-dark'
 * });
 *
 * // Setup auto-linting
 * const stopLinting = setupAutoLinting(editor, monaco);
 *
 * // Later, when disposing the editor
 * stopLinting();
 * editor.dispose();
 */