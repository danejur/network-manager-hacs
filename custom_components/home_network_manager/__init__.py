"""The Home Network Manager integration.

Every computer the network manager tracks becomes a device,
with a sensor per data point. The server pushes the full list over a WebSocket
that Home Assistant opens with a key from Admin → Settings → Home Assistant.
"""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .client import (
    NetworkManagerClient,
    NetworkManagerConfigEntry,
    NetworkManagerRuntimeData,
)
from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

# Data points the server no longer sends. A missing value otherwise only reads unavailable, so these are removed.
RETIRED_KEYS: tuple[str, ...] = ("last_seen",)


async def async_setup_entry(
    hass: HomeAssistant, entry: NetworkManagerConfigEntry
) -> bool:
    """Set up a server from a config entry.

    Setup never waits for the server: entities appear with the first snapshot,
    and the client keeps reconnecting in the background.
    """
    client = NetworkManagerClient(hass, entry)
    _remove_retired_entities(hass, entry)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, client.instance_id)},
        model="Network Manager",
        name=entry.title,
        configuration_url=client.url,
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    entry.runtime_data = NetworkManagerRuntimeData(client)

    # Forward first so the platforms listen before the first snapshot arrives.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(
        hass, client.async_run(), f"{DOMAIN} client {client.instance_id}"
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: NetworkManagerConfigEntry
) -> bool:
    """Unload a config entry; the client task is canceled with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: NetworkManagerConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing a device the server no longer reports; it would come back otherwise."""
    client = entry.runtime_data.client
    return not any(
        domain == DOMAIN and (value in client.devices or value == client.instance_id)
        for domain, value in device.identifiers
    )


def _remove_retired_entities(
    hass: HomeAssistant, entry: NetworkManagerConfigEntry
) -> None:
    """Remove the entities of data points the server stopped sending (Last seen became Connected)."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id.endswith(tuple(f"_{key}" for key in RETIRED_KEYS)):
            registry.async_remove(entity.entity_id)
