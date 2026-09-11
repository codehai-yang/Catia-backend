"""Business status codes for API responses."""

from enum import IntEnum


class StatusCode(IntEnum):
    """Unified business status codes.

    Values are used both as the ``code`` field in the response body and as the
    HTTP ``status_code``.
    """

    SUCCESS = 200
    VALIDATION_ERROR = 10100
    CATIA_UNAVAILABLE = 10101
    INTERNAL_ERROR = 10102