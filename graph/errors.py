"""Shared Graph API error handling and input validation helpers."""

from __future__ import annotations

import logging
import re

from msgraph.generated.models.o_data_errors.o_data_error import ODataError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OData error wrapping
# ---------------------------------------------------------------------------

#: Maximum number of pagination pages to follow (safety valve against cyclic nextLinks)
MAX_PAGES = 200

#: Maximum allowed value for user-supplied `limit` parameters
MAX_LIMIT = 500


def wrap_odata_error(exc: ODataError) -> RuntimeError:
    """Convert an ODataError into a RuntimeError with a readable message.

    Logs the error before re-raising for systemd journal diagnostics.
    """
    try:
        code = exc.error.code if exc.error else "unknown"
        msg = exc.error.message if exc.error else str(exc)
    except AttributeError:
        code = "unknown"
        msg = str(exc)
    logger.warning("Graph API error %s: %s", code, msg)
    return RuntimeError(f"Graph API error {code}: {msg}")


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

_GRAPH_ID_RE = re.compile(r"^[A-Za-z0-9_\-=+/]{1,512}$")


def validate_graph_id(value: str, label: str) -> None:
    """Validate that a Graph API resource ID has an expected format.

    Raises RuntimeError if the value is empty or contains unexpected characters.
    """
    if not value or not _GRAPH_ID_RE.match(value):
        raise RuntimeError(f"Invalid {label} format: expected non-empty alphanumeric ID")


def escape_odata_string(value: str) -> str:
    """Escape a string value for use inside OData single-quoted string literals.

    OData escapes single quotes by doubling them.
    """
    return value.replace("'", "''")


def clamp_limit(limit: int) -> int:
    """Clamp a user-supplied limit to a safe range [1, MAX_LIMIT]."""
    return max(1, min(limit, MAX_LIMIT))
