"""Explicit control signals for durable discovery execution."""


class DiscoveryError(RuntimeError):
    """The discovery cannot continue without surfacing an error."""


class DiscoveryStop(DiscoveryError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


class OutcomeUnknown(DiscoveryError):
    """A billed request may have succeeded; never resubmit it automatically."""


class BatchPending(DiscoveryError):
    """A submitted batch is still processing; release the worker until the next poll."""
