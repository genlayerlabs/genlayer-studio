"""
Contract linting service for GenVM contracts.
Provides validation and linting for GenLayer smart contracts.
"""

from typing import Dict, List, Any, Optional
from flask_jsonrpc.exceptions import JSONRPCError


class ContractLinter:
    """Service for linting GenVM contract source code."""

    def __init__(self):
        """Initialize the ContractLinter."""
        pass

    def lint_contract(
        self, source_code: str, filename: str = "contract.py"
    ) -> Dict[str, Any]:
        """
        Lint GenVM contract source code.

        Args:
            source_code: Python source code to lint
            filename: Optional filename for error reporting

        Returns:
            dict with 'results' array and 'summary' object containing:
            - results: List of linting issues found
            - summary: Summary statistics including total and by severity

        Raises:
            JSONRPCError: If linting fails
        """
        print(
            f"[LINTER] Called with filename: {filename}, code length: {len(source_code)}"
        )

        try:
            from genvm_linter.linter import GenVMLinter
            from genvm_linter.rules import Severity

            linter = GenVMLinter()
            results = linter.lint_source(source_code, filename)
            print(f"[LINTER] Found {len(results)} issues")

            # Convert results to JSON-serializable format
            results_json = []
            severity_counts = {"error": 0, "warning": 0, "info": 0}

            for result in results:
                severity = result.severity.value
                severity_counts[severity] += 1
                results_json.append(
                    {
                        "rule_id": result.rule_id,
                        "message": result.message,
                        "severity": severity,
                        "line": result.line,
                        "column": result.column,
                        "filename": result.filename,
                        "suggestion": result.suggestion,
                    }
                )

            return {
                "results": results_json,
                "summary": {"total": len(results), "by_severity": severity_counts},
            }
        except ImportError as e:
            print(f"[LINTER] Import error: {e}")
            raise JSONRPCError(
                code=-32000,
                message="GenVM linter not available. Please ensure genvm-linter is installed.",
                data={"error": str(e)},
            )
        except Exception as e:
            print(f"[LINTER] Unexpected error: {e}")
            raise JSONRPCError(
                code=-32000, message=f"Linting failed: {str(e)}", data={}
            )
