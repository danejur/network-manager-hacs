"""Tests for the Lovelace card: the served file and the entity lookup command."""

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import (
    ClientSessionGenerator,
    WebSocketGenerator,
)

from custom_components.home_network_manager.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import HOST_ID, PC_ID


async def test_the_card_is_served(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client: ClientSessionGenerator,
) -> None:
    client = await hass_client()
    response = await client.get(f"/{DOMAIN}/network-manager-computer-card.js")

    assert response.status == 200
    body = await response.text()
    assert "customElements.define(CARD" in body
    assert f"{DOMAIN}/devices" in body


async def test_devices_lists_each_computer_with_its_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/devices"})
    result = (await client.receive_json())["result"]["devices"]

    # Computers only, sorted by name; the server's own device is not one.
    assert [device["name"] for device in result] == ["Gaming PC", "pve"]
    pc = result[0]
    assert pc["model"] == "Desktop"
    assert pc["entities"]["cpu_usage"] == "sensor.gaming_pc_cpu_usage"
    assert pc["entities"]["online"] == "binary_sensor.gaming_pc_online"
    assert pc["entities"]["state"] == "sensor.gaming_pc_state"


async def test_devices_filters_to_one_and_follows_renames(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    registry = dr.async_get(hass)
    host = registry.async_get_device_by_identifier(
        (DOMAIN, HOST_ID), init_integration.entry_id
    )
    assert host is not None
    registry.async_update_device(host.id, name_by_user="Proxmox")

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/devices", "device_id": host.id})
    result = (await client.receive_json())["result"]["devices"]

    assert len(result) == 1
    assert result[0]["name"] == "Proxmox"
    assert result[0]["id"] == host.id
    assert PC_ID not in str(result)
