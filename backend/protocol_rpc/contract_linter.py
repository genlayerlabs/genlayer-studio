"""
Contract linting service for GenVM contracts.
Provides validation and linting for GenLayer smart contracts.
"""

import re
from typing import Dict, List, Any, Optional
from flask_jsonrpc.exceptions import JSONRPCError


# Pattern to match the Depends header with "latest" or "test" versions
# Matches: # { "Depends": "py-genlayer:latest" } or # { "Depends": "py-genlayer:test" }
_INVALID_VERSION_PATTERN = re.compile(
    r'#\s*\{\s*"Depends"\s*:\s*"py-genlayer:(latest|test)"\s*\}'
)


class ContractLinter:
    """Service for linting GenVM contract source code."""

    def __init__(self):
        """Initialize the ContractLinter."""
        pass

    def _check_invalid_runner_version(
        self, source_code: str, filename: str
    ) -> List[Dict[str, Any]]:
        """
        Check for invalid runner versions (latest, test) in the contract header.

        Args:
            source_code: Python source code to check
            filename: Filename for error reporting

        Returns:
            List of linting issues found
        """
        issues = []
        lines = source_code.split("\n")

        for line_num, line in enumerate(lines, start=1):
            match = _INVALID_VERSION_PATTERN.search(line)
            if match:
                version = match.group(1)
                issues.append(
                    {
                        "rule_id": "INVALID_RUNNER_VERSION",
                        "message": f'The runner version "{version}" is not allowed. Use a fixed version hash instead.',
                        "severity": "error",
                        "line": line_num,
                        "column": match.start() + 1,
                        "filename": filename,
                        "suggestion": 'Use a fixed version hash, e.g.: # { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }',
                    }
                )

        return issues

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
            print(f"[LINTER] Found {len(results)} issues from genvm_linter")

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

            # Add custom linting rules
            custom_issues = self._check_invalid_runner_version(source_code, filename)
            for issue in custom_issues:
                severity_counts[issue["severity"]] += 1
                results_json.append(issue)

            print(
                f"[LINTER] Total issues: {len(results_json)} ({len(custom_issues)} from custom rules)"
            )

            return {
                "results": results_json,
                "summary": {"total": len(results_json), "by_severity": severity_counts},
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
