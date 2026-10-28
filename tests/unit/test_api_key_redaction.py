"""Unit tests for keeping path-supplied API keys out of logs and Sentry."""

import logging


from backend.protocol_rpc.api_key_redaction import (
    REDACTED,
    ApiKeyRedactingFilter,
    redact_api_keys,
    scrub_sentry_event,
)

FULL_KEY = "glk_" + "a1b2c3d4" * 8  # 64 hex chars


class TestRedaction:
    def test_full_key_is_redacted(self):
        assert FULL_KEY not in redact_api_keys(f"POST /api/{FULL_KEY} 200")

    def test_surrounding_text_is_preserved(self):
        assert (
            redact_api_keys(f"POST /api/{FULL_KEY} 200") == f"POST /api/{REDACTED} 200"
        )

    def test_partial_key_is_still_redacted(self):
        """A truncated key is still a secret; do not log it verbatim."""
        assert "glk_dead" not in redact_api_keys("path=/api/glk_deadbeef")

    def test_non_key_text_untouched(self):
        assert redact_api_keys("POST /api 200 OK") == "POST /api 200 OK"

    def test_multiple_keys_in_one_line(self):
        out = redact_api_keys(f"{FULL_KEY} and {FULL_KEY}")
        assert FULL_KEY not in out
        assert out.count(REDACTED) == 2


class TestLogFilter:
    def _record(self, msg, args=None):
        return logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=args,
            exc_info=None,
        )

    def test_redacts_message(self):
        record = self._record(f"GET /api/{FULL_KEY}")
        ApiKeyRedactingFilter().filter(record)
        assert FULL_KEY not in record.getMessage()

    def test_redacts_args(self):
        """uvicorn puts the request line in args, not msg — both must be scrubbed."""
        record = self._record(
            '%s - "%s %s" %d', ("1.2.3.4", "POST", f"/api/{FULL_KEY}", 200)
        )
        ApiKeyRedactingFilter().filter(record)
        assert FULL_KEY not in record.getMessage()
        assert REDACTED in record.getMessage()

    def test_non_string_args_survive(self):
        record = self._record("%s %d", ("ok", 200))
        assert ApiKeyRedactingFilter().filter(record) is True
        assert record.getMessage() == "ok 200"

    def test_filter_never_drops_records(self):
        assert ApiKeyRedactingFilter().filter(self._record("anything")) is True


class TestSentryScrubbing:
    def test_scrubs_nested_url(self):
        event = {
            "request": {"url": f"https://studio.genlayer.com/api/{FULL_KEY}"},
            "transaction": f"POST /api/{FULL_KEY}",
        }
        out = scrub_sentry_event(event)
        assert FULL_KEY not in str(out)

    def test_scrubs_inside_lists(self):
        event = {"breadcrumbs": [{"data": {"url": f"/api/{FULL_KEY}"}}]}
        assert FULL_KEY not in str(scrub_sentry_event(event))

    def test_preserves_structure_and_non_strings(self):
        event = {"level": "error", "extra": {"count": 3, "ok": True, "tags": ["a"]}}
        assert scrub_sentry_event(event) == event
