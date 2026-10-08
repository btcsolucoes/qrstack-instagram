"""Conservative local policy, not a published limit for Instagram's private API."""
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import time


PUBLISH_WINDOW_SECONDS = 24 * 60 * 60
REQUEST_BUFFER_SECONDS = 5


def retry_after_seconds(headers, *, now=None):
    """Parse Retry-After without logging response data; never shorten its delay."""
    value = headers.get("Retry-After") if headers else None
    if not isinstance(value, str) or len(value) > 128:
        return 0
    value = value.strip()
    if value.isdecimal():
        return int(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, math.ceil(date.timestamp() - (time.time() if now is None else now)))
    except (TypeError, ValueError, OverflowError):
        return 0
