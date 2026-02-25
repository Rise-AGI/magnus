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


class _ServerError(Exception):
    """5xx — transient, worth retrying. Internal use only."""
    pass
