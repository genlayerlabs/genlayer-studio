# Unify Unit Test Workflows with Dynamic Mocking Control

## Summary

This PR refactors the unit test workflows to use a single, unified workflow with conditional logic that determines whether to use mocks based on PR approval status. The system now uses a single environment variable `TEST_WITH_MOCKS` to control all mocking behavior.

## Key Changes

### 🔄 Workflow Consolidation

**Before:**
- Two separate workflows: `unit-tests-pr.yml` and `unit-tests-pr-approved.yml`
- Manual configuration differences between workflows
- Duplicate job definitions

**After:**
- Single workflow: `unit-tests-pr.yml` with intelligent trigger detection
- Conditional logic determines mocking behavior
- DRY principle applied - no duplicate code

### 🎯 Unified Mocking Control

- **Single variable**: `TEST_WITH_MOCKS` controls all mocking (LLMs, web requests, external services)
- **Replaces**: `TEST_WITH_MOCK_LLMS` for better clarity and consistency
- **Applied to**: Both unit tests and integration tests for uniformity

### 🔧 Enhanced Test Fixtures

The `tests/unit/conftest.py` now provides comprehensive mocking:
- **LLM Providers**: OpenAI, Anthropic responses
- **Web Requests**: requests, urllib, aiohttp
- **WebDriver**: Selenium browser automation
- All controlled by the single `TEST_WITH_MOCKS` variable

## Behavior

### Regular PRs (opened, synchronized)
```bash
TEST_WITH_MOCKS=true
```
- ✅ All external services mocked
- ✅ Fast execution
- ✅ No API costs
- ✅ Deterministic results

### Approved PRs
```bash
TEST_WITH_MOCKS=false
```
- ❌ Real API calls to LLM providers
- ❌ Real HTTP/HTTPS requests
- ✅ Full integration validation
- ✅ Production-like testing

### Push to main
```bash
TEST_WITH_MOCKS=false  # or true, depending on requirements
```
- Runs with appropriate configuration
- Uploads to Codecov and SonarCloud

## Trigger Logic

The workflow now intelligently determines when to run:
1. **Pull Request Events**: opened, synchronized, labeled
2. **Pull Request Review**: approved
3. **Push to main**: for coverage reporting
4. **Manual dispatch**: for debugging

## Benefits

1. **Maintainability**: Single workflow to maintain instead of two
2. **Consistency**: Same variable name across all test types
3. **Flexibility**: Easy to adjust mocking behavior without code changes
4. **Cost Optimization**: Mocks for regular development, real APIs for validation
5. **Performance**: Faster feedback loop during development

## Files Changed

- **Deleted**: `.github/workflows/unit-tests-pr-approved.yml`
- **Modified**: `.github/workflows/unit-tests-pr.yml` - Complete rewrite with trigger logic
- **Modified**: `tests/unit/conftest.py` - Updated to use `TEST_WITH_MOCKS`
- **Modified**: `tests/unit/consensus/test_helpers.py` - Updated variable name
- **Modified**: `.env.example` - Changed to `TEST_WITH_MOCKS`
- **Modified**: Integration test files for consistency

## Testing

Run unit tests with mocking:
```bash
TEST_WITH_MOCKS=true pytest tests/unit/
```

Run unit tests without mocking:
```bash
TEST_WITH_MOCKS=false pytest tests/unit/
```

## Migration Notes

- The variable `TEST_WITH_MOCK_LLMS` has been renamed to `TEST_WITH_MOCKS`
- All existing configurations should be updated to use the new variable name
- The behavior remains the same, just with better naming consistency