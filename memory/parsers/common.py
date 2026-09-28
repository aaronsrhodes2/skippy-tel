"""Helpers shared by every parser."""
from __future__ import annotations

from datetime import datetime, timezone


def parse_ts(value) -> datetime | None:
    """ISO-8601 string or unix seconds → aware UTC datetime; None if unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
