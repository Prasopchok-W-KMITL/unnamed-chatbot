class ProviderUnavailable(Exception):
    """Reason is a locally assigned code, never provider content."""
    def __init__(self, reason='provider_unavailable', *, status_code=None, retry_after=None):
        self.reason = reason
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(reason)


class EvidenceError(ValueError):
    def __init__(self, reason='invalid_evidence'):
        self.reason = reason
        super().__init__(reason)
