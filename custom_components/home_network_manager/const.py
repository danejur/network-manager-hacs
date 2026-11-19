"""Constants for the Home Network Manager integration."""

from typing import Final

DOMAIN: Final = "home_network_manager"

# The API's socket (protocol v1): hello with the key, then welcome, snapshots and pings.
WS_PATH: Final = "/api/home-assistant/connect"
PROTOCOL_VERSION: Final = 1
CLOSE_AUTH_FAILED: Final = 4401
CLOSE_PROTOCOL_TOO_OLD: Final = 4426


# Snapshot sensor platforms.
PLATFORM_SENSOR: Final = "sensor"
PLATFORM_BINARY_SENSOR: Final = "binary_sensor"


def signal_update(entry_id: str) -> str:
    """Return the dispatcher signal for a snapshot or a connection change of one entry."""
    return f"{DOMAIN}_update_{entry_id}"
