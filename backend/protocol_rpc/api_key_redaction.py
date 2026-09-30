"""Keep path-supplied API keys out of logs and error reports.

API keys can be passed as a path segment (`/api/glk_...`) because the EVM
toolchain only accepts a URL. The cost of that convenience is that the key
travels somewhere URLs habitually get written down: uvicorn's access log,
Sentry events and traces, proxy logs, browser history.

We cannot do anything about logs outside this process, but anything this
process emits is ours to scrub. Without that, enabling path keys would be a
downgrade on the header-only design rather than an improvement — a key in
CloudWatch or in a third-party error tracker is a leaked key.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

# Matches a full key (`glk_` + 64 hex) and also partial/malformed ones, so a
# truncated or mistyped key still gets scrubbed rather than logged verbatim.
_API_KEY_RE = re.compile(r"glk_[0-9a-fA-F]{4,}")

REDACTED = "glk_REDACTED"


def redact_api_keys(value: str) -> str:
    return _API_KEY_RE.sub(REDACTED, value)


class ApiKeyRedactingFilter(logging.Filter):
    """Scrubs API keys from log records, including uvicorn's access log.

    uvicorn formats the request line via record.args rather than the message,
    so both have to be rewritten.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "glk_" in record.msg:
            record.msg = redact_api_keys(record.msg)

        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_api_keys(a) if isinstance(a, str) else a for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: redact_api_keys(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        return True


def install_log_redaction() -> None:
    """Attach the filter to the loggers that render request paths."""
    log_filter = ApiKeyRedactingFilter()
    for name in ("uvicorn.access", "uvicorn.error", "gunicorn.access"):
        logging.getLogger(name).addFilter(log_filter)
    # Root catches application logs that interpolate a URL.
    logging.getLogger().addFilter(log_filter)


def _scrub(node: Any) -> Any:
    if isinstance(node, str):
        return redact_api_keys(node)
    if isinstance(node, dict):
        return {k: _scrub(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub(v) for v in node]
    return node


def scrub_sentry_event(event: dict, _hint: Optional[dict] = None) -> dict:
    """before_send / before_send_transaction hook.

    Sentry is configured with send_default_pii=True and full trace sampling, so
    the request URL reaches it on every transaction, not just on errors. Scrub
    the whole event rather than known fields — the URL is echoed into
    `request.url`, the transaction name, breadcrumbs and span descriptions, and
    missing one of those defeats the point.
    """
    return _scrub(event)
