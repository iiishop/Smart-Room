"""Exception classes for Home Assistant client."""


class HAError(Exception):
    """Base exception for all HA client errors."""
    pass


class HAConnectionError(HAError):
    """Network connection failure."""
    pass


class HAAuthError(HAError):
    """Authentication failure (401/403)."""
    pass


class HAResponseError(HAError):
    """Response parsing failure."""
    pass


class HAServiceError(HAError):
    """Service call failure."""
    pass
