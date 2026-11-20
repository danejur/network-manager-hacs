"""Diagnostics for the Home Network Manager integration."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .client import NetworkManagerConfigEntry

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NetworkManagerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    client = entry.runtime_data.client
    return {
        "entry": {
            "title": entry.title,
            "unique_id": entry.unique_id,
            "data": async_redact_data(entry.data, TO_REDACT),
        },
        "connection": {
            "connected": client.connected,
            "url": client.url,
            "push_interval": client.push_interval,
            "failed_attempts": client.failed_attempts,
            "snapshots_received": client.snapshots_received,
            "last_snapshot": client.last_snapshot.isoformat()
            if client.last_snapshot
            else None,
        },
        "devices": list(client.devices.values()),
    }
