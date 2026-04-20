"""
Unit tests for ContractLinter custom rules.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestInvalidRunnerVersion:
    """Tests for the INVALID_RUNNER_VERSION custom linting rule."""

    def test_detects_latest_version(self):
        """Should detect 'latest' as an invalid runner version."""
        from backend.protocol_rpc.contract_linter import ContractLinter

        linter = ContractLinter()
        source_code = '# { "Depends": "py-genlayer:latest" }\nfrom genlayer import *'

        issues = linter._check_invalid_runner_version(source_code, "test.py")

        assert len(issues) == 1
        assert issues[0]["rule_id"] == "INVALID_RUNNER_VERSION"
        assert issues[0]["severity"] == "error"
        assert issues[0]["line"] == 1
        assert "latest" in issues[0]["message"]
        assert issues[0]["suggestion"] is not None

    def test_detects_test_version(self):
        """Should detect 'test' as an invalid runner version."""
        from backend.protocol_rpc.contract_linter import ContractLinter

        linter = ContractLinter()
        source_code = '# { "Depends": "py-genlayer:test" }\nfrom genlayer import *'

        issues = linter._check_invalid_runner_version(source_code, "test.py")

        assert len(issues) == 1
        assert issues[0]["rule_id"] == "INVALID_RUNNER_VERSION"
        assert issues[0]["severity"] == "error"
        assert "test" in issues[0]["message"]

    def test_allows_valid_hash_version(self):
        """Should allow valid hash versions."""
        from backend.protocol_rpc.contract_linter import ContractLinter

        linter = ContractLinter()
        source_code = '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }\nfrom genlayer import *'

        issues = linter._check_invalid_runner_version(source_code, "test.py")

        assert len(issues) == 0

    def test_no_issues_without_depends_header(self):
        """Should not report issues if there's no Depends header."""
        from backend.protocol_rpc.contract_linter import ContractLinter

        linter = ContractLinter()
        source_code = "# Just a comment\nfrom genlayer import *"

        issues = linter._check_invalid_runner_version(source_code, "test.py")

        assert len(issues) == 0

    def test_handles_whitespace_variations(self):
        """Should detect invalid versions with different whitespace."""
        from backend.protocol_rpc.contract_linter import ContractLinter

        linter = ContractLinter()

        # Extra spaces
        source_code = '#  {  "Depends"  :  "py-genlayer:latest"  }'
        issues = linter._check_invalid_runner_version(source_code, "test.py")
        assert len(issues) == 1

    def test_correct_line_number_for_multiline(self):
        """Should report correct line number when header is not on first line."""
        from backend.protocol_rpc.contract_linter import ContractLinter

        linter = ContractLinter()
        source_code = (
            '# v0.1.0\n# { "Depends": "py-genlayer:test" }\nfrom genlayer import *'
        )

        issues = linter._check_invalid_runner_version(source_code, "test.py")

        assert len(issues) == 1
        assert issues[0]["line"] == 2

    @patch("genvm_linter.linter.GenVMLinter", autospec=True)
    def test_lint_contract_includes_custom_rules(self, mock_genvm_linter_class):
        """Should include custom rule issues in lint_contract output."""
        from backend.protocol_rpc.contract_linter import ContractLinter

        # Mock the external genvm_linter to return no issues
        mock_linter = MagicMock()
        mock_linter.lint_source.return_value = []
        mock_genvm_linter_class.return_value = mock_linter

        linter = ContractLinter()
        source_code = '# { "Depends": "py-genlayer:latest" }\nfrom genlayer import *'

        result = linter.lint_contract(source_code, "test.py")

        assert result["summary"]["total"] == 1
        assert result["summary"]["by_severity"]["error"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["rule_id"] == "INVALID_RUNNER_VERSION"


class TestSharedVersionValidation:
    """Tests for the shared check_invalid_runner_version function.

    This function is used by both the linter and the contract upgrade endpoint,
    so testing it ensures both features are covered.
    """

    def test_detects_latest_version(self):
        """Should detect 'latest' as an invalid runner version."""
        from backend.protocol_rpc.contract_linter import check_invalid_runner_version

        new_code = '# { "Depends": "py-genlayer:latest" }\nfrom genlayer import *'

        has_invalid, version = check_invalid_runner_version(new_code)

        assert has_invalid is True
        assert version == "latest"

    def test_detects_test_version(self):
        """Should detect 'test' as an invalid runner version."""
        from backend.protocol_rpc.contract_linter import check_invalid_runner_version

        new_code = '# { "Depends": "py-genlayer:test" }\nfrom genlayer import *'

        has_invalid, version = check_invalid_runner_version(new_code)

        assert has_invalid is True
        assert version == "test"

    def test_allows_valid_hash_version(self):
        """Should allow valid hash versions."""
        from backend.protocol_rpc.contract_linter import check_invalid_runner_version

        new_code = '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }\nfrom genlayer import *'

        has_invalid, version = check_invalid_runner_version(new_code)

        assert has_invalid is False
        assert version is None

    def test_allows_no_depends_header(self):
        """Should allow code without a Depends header."""
        from backend.protocol_rpc.contract_linter import check_invalid_runner_version

        new_code = "# Just a comment\nfrom genlayer import *"

        has_invalid, version = check_invalid_runner_version(new_code)

        assert has_invalid is False
        assert version is None

    def test_error_message_format(self):
        """Error message should contain the version and example hash."""
        from backend.protocol_rpc.contract_linter import INVALID_VERSION_ERROR_MESSAGE

        message = INVALID_VERSION_ERROR_MESSAGE.format(version="latest")

        assert "latest" in message
        assert "not allowed" in message
        assert "1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" in message
