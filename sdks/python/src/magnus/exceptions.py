# sdks/python/src/magnus/exceptions.py


class MagnusError(Exception):
    pass


class AuthenticationError(MagnusError):
    pass


class ResourceNotFoundError(MagnusError):
    pass


class ExecutionError(MagnusError):
    pass
