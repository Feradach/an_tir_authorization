import json
import logging
from django.utils import timezone

security_logger = logging.getLogger("security.events")


def log_security_event(event_type, **fields):
    """
    Emit structured JSON security event.
    """

    event = {
        "event": event_type,
        "timestamp": timezone.now().isoformat(),
        **fields,
    }

    security_logger.warning(json.dumps(event))
