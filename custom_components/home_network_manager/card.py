"""The integration's Lovelace card: served by the integration itself, so installing it from HACS is enough.

The card asks `home_network_manager/devices` for each computer's entities by data point key (`cpu_usage`,
`memory_usage`, …), so it keeps working after an entity is renamed.
"""

from pathlib import Path
from typing import Any

import probatio

from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.loader import async_get_integration

from .const import DOMAIN

CARD_FILE = "network-manager-computer-card.js"
URL_BASE = f"/{DOMAIN}"


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Serve the card and load it on every dashboard; once per Home Assistant start."""
    path = Path(__file__).parent / "www" / CARD_FILE
    await hass.http.async_register_static_paths(
        [StaticPathConfig(f"{URL_BASE}/{CARD_FILE}", str(path), cache_headers=False)]
    )
    # The frontend is always loaded in a real install (after_dependencies orders it first); test hosts run without it.
    if "frontend" in hass.config.components:
        # The version in the URL makes browsers fetch an updated card after an update.
        version = (await async_get_integration(hass, DOMAIN)).version or "0"
        add_extra_js_url(hass, f"{URL_BASE}/{CARD_FILE}?v={version}")
    websocket_api.async_register_command(hass, ws_devices)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/devices",
        probatio.Optional("device_id"): str,
    }
)
@callback
def ws_devices(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return every computer (or one) with its entity ids by data point key."""
    connection.send_result(
        msg["id"], {"devices": computers(hass, msg.get("device_id"))}
    )


@callback
def computers(
    hass: HomeAssistant, device_id: str | None = None
) -> list[dict[str, Any]]:
    """List the integration's computers: device id, name, model and `{key: entity_id}`."""
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    result: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        instance = entry.unique_id or entry.entry_id
        # The snapshot says what each device is; the card shows computers only (not the reverse proxy).
        runtime = getattr(entry, "runtime_data", None)
        known: dict[str, Any] = runtime.client.devices if runtime is not None else {}
        for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
            if device_id is not None and device.id != device_id:
                continue
            uuid = next(
                (value for domain, value in device.identifiers if domain == DOMAIN),
                None,
            )
            # The server's own device has no data points of a computer.
            if uuid is None or uuid == instance:
                continue
            kind = known.get(uuid, {}).get("kind")
            if kind is not None and kind != "computer":
                continue
            prefix = f"{instance}_{uuid}_"
            keys = {
                entity.unique_id.removeprefix(prefix): entity.entity_id
                for entity in er.async_entries_for_device(entities, device.id)
                if entity.platform == DOMAIN and entity.unique_id.startswith(prefix)
            }
            result.append(
                {
                    "id": device.id,
                    "name": device.name_by_user or device.name,
                    "model": device.model,
                    "entities": keys,
                }
            )
    return sorted(result, key=lambda d: str(d["name"]).casefold())
