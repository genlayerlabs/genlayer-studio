"""Unit tests for GenVM error code extraction."""

import pytest
from backend.node.genvm.error_codes import (
    GenVMErrorCode,
    extract_error_code,
    extract_error_code_from_timeout,
    LUA_CAUSE_TO_CODE,
    RATE_LIMIT_STATUSES,
)


class TestGenVMErrorCodeEnum:
    """Tests for GenVMErrorCode enum."""

    def test_error_codes_are_strings(self):
        """All error codes should be usable as strings."""
        assert GenVMErrorCode.LLM_RATE_LIMITED == "LLM_RATE_LIMITED"
        assert GenVMErrorCode.LLM_NO_PROVIDER == "LLM_NO_PROVIDER"
        assert GenVMErrorCode.GENVM_TIMEOUT == "GENVM_TIMEOUT"


class TestExtractErrorCode:
    """Tests for extract_error_code function."""

    def test_none_input_returns_none(self):
        """None input should return None."""
        assert extract_error_code(None) is None

    def test_string_input_without_patterns_returns_none(self):
        """String input without recognizable patterns returns None."""
        assert extract_error_code("some random error") is None

    def test_string_input_with_rate_limit(self):
        """String with 'rate limit' should return LLM_RATE_LIMITED."""
        assert (
            extract_error_code("rate limit exceeded") == GenVMErrorCode.LLM_RATE_LIMITED
        )

    def test_string_input_with_429(self):
        """String with '429' should return LLM_RATE_LIMITED."""
        assert extract_error_code("HTTP 429 error") == GenVMErrorCode.LLM_RATE_LIMITED

    def test_string_input_with_timeout(self):
        """String with 'timeout' should return GENVM_TIMEOUT."""
        assert extract_error_code("connection timeout") == GenVMErrorCode.GENVM_TIMEOUT

    def test_string_input_with_llm_timeout(self):
        """String with 'timeout' and 'llm' should return LLM_TIMEOUT."""
        assert extract_error_code("llm request timeout") == GenVMErrorCode.LLM_TIMEOUT

    def test_string_input_with_no_provider(self):
        """String with 'no provider' should return LLM_NO_PROVIDER."""
        assert (
            extract_error_code("no provider available")
            == GenVMErrorCode.LLM_NO_PROVIDER
        )

    def test_dict_with_no_provider_cause(self):
        """Dict with NO_PROVIDER_FOR_PROMPT cause should return LLM_NO_PROVIDER."""
        result_data = {
            "message": "error",
            "causes": ["NO_PROVIDER_FOR_PROMPT"],
            "fatal": True,
        }
        assert extract_error_code(result_data) == GenVMErrorCode.LLM_NO_PROVIDER

    def test_dict_with_status_429_in_ctx(self):
        """Dict with status 429 in ctx should return LLM_RATE_LIMITED."""
        result_data = {
            "message": "error",
            "causes": ["STATUS_NOT_OK"],
            "ctx": {"status": 429},
        }
        assert extract_error_code(result_data) == GenVMErrorCode.LLM_RATE_LIMITED

    def test_dict_with_status_503_in_ctx(self):
        """Dict with status 503 in ctx should return LLM_RATE_LIMITED."""
        result_data = {
            "message": "error",
            "causes": ["STATUS_NOT_OK"],
            "ctx": {"status": 503},
        }
        assert extract_error_code(result_data) == GenVMErrorCode.LLM_RATE_LIMITED

    def test_dict_with_status_not_ok_non_rate_limit(self):
        """Dict with STATUS_NOT_OK but non-rate-limit status should return LLM_PROVIDER_ERROR."""
        result_data = {
            "message": "error",
            "causes": ["STATUS_NOT_OK"],
            "ctx": {"status": 500},
        }
        assert extract_error_code(result_data) == GenVMErrorCode.LLM_PROVIDER_ERROR

    def test_dict_with_webpage_load_failed(self):
        """Dict with WEBPAGE_LOAD_FAILED cause should return WEB_REQUEST_FAILED."""
        result_data = {
            "message": "error",
            "causes": ["WEBPAGE_LOAD_FAILED"],
        }
        assert extract_error_code(result_data) == GenVMErrorCode.WEB_REQUEST_FAILED

    def test_dict_with_tld_forbidden(self):
        """Dict with TLD_FORBIDDEN cause should return WEB_TLD_FORBIDDEN."""
        result_data = {
            "message": "error",
            "causes": ["TLD_FORBIDDEN"],
        }
        assert extract_error_code(result_data) == GenVMErrorCode.WEB_TLD_FORBIDDEN

    def test_dict_with_rate_limit_in_body_error(self):
        """Dict with rate limit code in body.error should return LLM_RATE_LIMITED."""
        result_data = {
            "message": "error",
            "causes": ["STATUS_NOT_OK"],
            "ctx": {
                "status": 200,
                "body": {"error": {"code": 429, "message": "rate-limited upstream"}},
            },
        }
        assert extract_error_code(result_data) == GenVMErrorCode.LLM_RATE_LIMITED

    def test_real_world_lua_error_structure(self):
        """Test with a realistic Lua error structure from OpenRouter."""
        result_data = {
            "message": "all LLM providers failed",
            "causes": ["NO_PROVIDER_FOR_PROMPT"],
            "fatal": True,
            "ctx": {
                "status": 429,
                "body": {"error": {"code": 429, "message": "rate-limited upstream"}},
                "url": "https://openrouter.ai/api/v1/chat/completions",
            },
        }
        # Should detect rate limiting from status code
        assert extract_error_code(result_data) == GenVMErrorCode.LLM_RATE_LIMITED

    def test_invalid_api_key_in_message(self):
        """Test detection of invalid API key errors."""
        assert (
            extract_error_code("Invalid API key provided")
            == GenVMErrorCode.LLM_INVALID_API_KEY
        )
        assert (
            extract_error_code("authentication failed")
            == GenVMErrorCode.LLM_INVALID_API_KEY
        )

    def test_stderr_is_used_as_fallback(self):
        """Test that stderr is used when message doesn't contain patterns."""
        result_data = {"message": "unknown error"}
        stderr = "rate limit exceeded"
        assert (
            extract_error_code(result_data, stderr) == GenVMErrorCode.LLM_RATE_LIMITED
        )


class TestExtractErrorCodeFromTimeout:
    """Tests for extract_error_code_from_timeout function."""

    def test_none_error_returns_genvm_timeout(self):
        """None error should return GENVM_TIMEOUT."""
        assert extract_error_code_from_timeout(None) == GenVMErrorCode.GENVM_TIMEOUT

    def test_error_with_no_provider(self):
        """Error containing 'no_provider_for_prompt' should return LLM_NO_PROVIDER."""
        error = Exception("NO_PROVIDER_FOR_PROMPT: all providers failed")
        assert extract_error_code_from_timeout(error) == GenVMErrorCode.LLM_NO_PROVIDER

    def test_error_with_rate_limit(self):
        """Error containing 'rate limit' should return LLM_RATE_LIMITED."""
        error = Exception("rate limit exceeded")
        assert extract_error_code_from_timeout(error) == GenVMErrorCode.LLM_RATE_LIMITED

    def test_error_with_fatal_true(self):
        """Error containing 'fatal: true' should return LLM_PROVIDER_ERROR."""
        # The format matches Lua table string representation: fatal: true
        error = Exception("fatal: true, message: failed")
        assert (
            extract_error_code_from_timeout(error) == GenVMErrorCode.LLM_PROVIDER_ERROR
        )

    def test_error_with_status_not_ok(self):
        """Error containing 'status_not_ok' should return LLM_PROVIDER_ERROR."""
        error = Exception("STATUS_NOT_OK: server error")
        assert (
            extract_error_code_from_timeout(error) == GenVMErrorCode.LLM_PROVIDER_ERROR
        )

    def test_generic_error_returns_genvm_timeout(self):
        """Generic error without specific patterns should return GENVM_TIMEOUT."""
        error = Exception("some random error occurred")
        assert extract_error_code_from_timeout(error) == GenVMErrorCode.GENVM_TIMEOUT


class TestLuaCauseMapping:
    """Tests for LUA_CAUSE_TO_CODE mapping."""

    def test_all_expected_causes_are_mapped(self):
        """All expected Lua causes should be in the mapping."""
        expected_causes = [
            "NO_PROVIDER_FOR_PROMPT",
            "STATUS_NOT_OK",
            "WEBPAGE_LOAD_FAILED",
            "TLD_FORBIDDEN",
        ]
        for cause in expected_causes:
            assert cause in LUA_CAUSE_TO_CODE


class TestRateLimitStatuses:
    """Tests for RATE_LIMIT_STATUSES set."""

    def test_expected_statuses_are_included(self):
        """Expected rate limit HTTP statuses should be in the set."""
        assert 429 in RATE_LIMIT_STATUSES  # Too Many Requests
        assert 503 in RATE_LIMIT_STATUSES  # Service Unavailable
        assert 529 in RATE_LIMIT_STATUSES  # Site is overloaded

    def test_non_rate_limit_statuses_not_included(self):
        """Non-rate-limit statuses should not be in the set."""
        assert 200 not in RATE_LIMIT_STATUSES
        assert 400 not in RATE_LIMIT_STATUSES
        assert 500 not in RATE_LIMIT_STATUSES
