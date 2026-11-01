# sdks/python/src/magnus/exceptions.py


class MagnusError(Exception):
    pass


class APIError(MagnusError):
    """Non-success HTTP response from the Magnus API.

    Attributes:
        status_code: HTTP status code.
        detail: Error detail from the server response body.
    """

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class AuthenticationError(APIError):
    def __init__(self, detail: str):
        super().__init__(401, detail)


class ForbiddenError(APIError):
    def __init__(self, detail: str):
        super().__init__(403, detail)


class ResourceNotFoundError(APIError):
    def __init__(self, detail: str):
        super().__init__(404, detail)


class ConflictError(APIError):
    def __init__(self, detail: str):
        super().__init__(409, detail)


class ExecutionError(MagnusError):
    pass


class UploadOutcomeUnknown(MagnusError):
    """An upload may have committed; replaying it could create a second receipt."""


class SubmissionOutcomeUnknown(MagnusError):
    """No conclusive reply; use the key to look up acceptance before resubmitting."""

    def __init__(
        self,
        idempotency_key: str,
    ):
        self.idempotency_key = idempotency_key
        super().__init__(
            "Submission outcome is unknown. Check the accepted submission with key "
            f"{idempotency_key} before creating another Job.",
        )


class _ServerError(MagnusError):
    """5xx — transient, worth retrying. Internal marker for retry loops;
    not exported. Inherits MagnusError so that if a retry loop exhausts
    its budget and re-raises, `except MagnusError` still catches it."""
    pass
