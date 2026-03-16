# backend/protocol_rpc/logging_config.py
"""
Logging configuration for uvicorn access logs.
Filters out health check endpoints to reduce log noise.

Routes logs to stdout/stderr based on severity for proper GCP Cloud Logging:
- DEBUG/INFO/WARNING → stdout (GCP labels as INFO/default severity)
- ERROR/CRITICAL → stderr (GCP labels as ERROR severity)
"""

import logging
import os


class HealthCheckFilter(logging.Filter):
    """Filter out health check requests from access logs."""

    # Endpoints to filter (no logging for these paths)
    FILTERED_PATHS = {"/health", "/ready", "/status", "/api"}

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False to suppress the log record, True to allow it."""
        # uvicorn access logs have the request path in the message
        message = record.getMessage()

        # Check if this is an access log for a filtered path
        for path in self.FILTERED_PATHS:
            # Match patterns like: GET /health HTTP/1.1
            if f'"{path} ' in message or f" {path} " in message:
                return False

        return True


class InfoAndBelowFilter(logging.Filter):
    """Filter to only allow DEBUG, INFO, and WARNING logs (below ERROR)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR


class ErrorAndAboveFilter(logging.Filter):
    """Filter to only allow ERROR and CRITICAL logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.ERROR


def get_uvicorn_log_config() -> dict:
    """Get a uvicorn-compatible logging configuration dict.

    This can be passed to uvicorn.run(log_config=...) for more
    comprehensive logging control.

    Routes logs to stdout/stderr based on severity for proper GCP Cloud Logging:
    - DEBUG/INFO/WARNING → stdout (GCP labels as INFO/default severity)
    - ERROR/CRITICAL → stderr (GCP labels as ERROR severity)
    """
    log_level = os.getenv("LOG_LEVEL", "info").upper()

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "health_check_filter": {
                "()": HealthCheckFilter,
            },
            "info_and_below": {
                "()": InfoAndBelowFilter,
            },
            "error_and_above": {
                "()": ErrorAndAboveFilter,
            },
        },
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
                "use_colors": None,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            # Non-error logs to stdout (GCP labels as INFO/default)
            "default_stdout": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "filters": ["info_and_below"],
            },
            # Error logs to stderr (GCP labels as ERROR)
            "default_stderr": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "filters": ["error_and_above"],
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "filters": ["health_check_filter"],
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["default_stdout", "default_stderr"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.error": {
                "level": log_level,
                "handlers": ["default_stdout", "default_stderr"],
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access"],
                "level": log_level,
                "propagate": False,
            },
        },
    }
