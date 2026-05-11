class HAError(Exception):
    pass


class HAConnectionError(HAError):
    pass


class HAAuthError(HAError):
    pass


class HAResponseError(HAError):
    pass


class HAServiceError(HAError):
    pass
