"""Authentication headers for requests sent to data-manager."""

import logging
import os

logger = logging.getLogger(__name__)

_missing_token_warning_logged = False


def data_manager_auth_headers() -> dict[str, str]:
    """Return the gateway identity headers without ever exposing the token."""
    global _missing_token_warning_logged

    service_name = os.getenv("DM_SERVICE_NAME", "petrosa-tradeengine")
    token = os.getenv("DM_SERVICE_TOKEN")
    headers = {"X-Petrosa-Service": service_name}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif not _missing_token_warning_logged:
        logger.warning(
            "DM_SERVICE_TOKEN is unset; data-manager requests will use service identity only"
        )
        _missing_token_warning_logged = True
    return headers
