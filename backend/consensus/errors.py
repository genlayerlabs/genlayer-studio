class NoValidatorsAvailableError(Exception):
    """Raised when no validators are available to process a transaction."""


class FinalizationStateError(RuntimeError):
    """An agreed transaction lacks a verifiable post-state; preserve its decision."""
