"""Tests for devices and entities created from snapshots."""

from pathlib import Path

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_network_manager.const import DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.loader import async_get_integration

from .conftest import (
    HOST_ID,
    INSTANCE_ID,
    PC_ID,
    FakeServer,
    computer,
    get_client,
    push_and_wait,
    wait_for,
)


def _entity_id(hass: HomeAssistant, platform: str, device_id: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{INSTANCE_ID}_{device_id}_{key}"
    )
    assert entity_id is not None, f"no {platform} for {key}"
    return entity_id


async def test_every_device_is_a_device_with_its_sensors(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    devices = dr.async_get(hass)
    pc = devices.async_get_device_by_identifier(
        (DOMAIN, PC_ID), init_integration.entry_id
    )
    assert pc is not None
    assert pc.name == "Gaming PC"
    assert pc.model == "Desktop"
    assert pc.sw_version == "CachyOS"
    assert pc.configuration_url is not None
    assert pc.configuration_url.endswith(f"/devices/{PC_ID}")

    server = devices.async_get_device_by_identifier(
        (DOMAIN, INSTANCE_ID), init_integration.entry_id
    )
    assert server is not None
    assert pc.via_device_id == server.id

    host = devices.async_get_device_by_identifier(
        (DOMAIN, HOST_ID), init_integration.entry_id
    )
    assert host is not None
    assert host.via_device_id == server.id

    cpu = hass.states.get(_entity_id(hass, "sensor", PC_ID, "cpu_usage"))
    assert cpu is not None
    assert cpu.state == "12.5"
    assert cpu.attributes["unit_of_measurement"] == "%"
    assert cpu.entity_id == "sensor.gaming_pc_cpu_usage"

    memory = hass.states.get(_entity_id(hass, "sensor", PC_ID, "memory_used"))
    assert memory is not None
    assert memory.attributes["unit_of_measurement"] == "GiB"
    assert float(memory.state) == 8.0

    boot = hass.states.get(_entity_id(hass, "sensor", PC_ID, "last_boot"))
    assert boot is not None
    assert boot.state == "2026-11-17T11:00:00+00:00"

    state = hass.states.get(_entity_id(hass, "sensor", PC_ID, "state"))
    assert state is not None
    assert state.state == "online"
    assert "pending_enrollment" in state.attributes["options"]

    updates = hass.states.get(_entity_id(hass, "sensor", PC_ID, "updates"))
    assert updates is not None
    assert updates.attributes["packages"] == ["linux", "mesa", "yay"]

    kernel = er.async_get(hass).async_get(_entity_id(hass, "sensor", PC_ID, "kernel"))
    assert kernel is not None
    assert kernel.entity_category is er.EntityCategory.DIAGNOSTIC

    online = hass.states.get(_entity_id(hass, "binary_sensor", PC_ID, "online"))
    assert online is not None
    assert online.state == STATE_ON

    connected = hass.states.get("binary_sensor.home_network_manager_connected")
    assert connected is not None
    assert connected.state == STATE_ON


async def test_snapshots_update_add_and_drop_values(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_server: FakeServer
) -> None:
    cpu_id = _entity_id(hass, "sensor", PC_ID, "cpu_usage")

    fake_server.devices[0] = computer(cpu=50.0)
    await push_and_wait(hass, fake_server, init_integration)
    state = hass.states.get(cpu_id)
    assert state is not None
    assert state.state == "50.0"

    # Left out (the computer went offline): unavailable, not removed.
    fake_server.devices[0] = computer(cpu=None)
    await push_and_wait(hass, fake_server, init_integration)
    state = hass.states.get(cpu_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    # A new data point becomes a new entity, and a rename reaches the device.
    fake_server.devices[0] = computer(name="Gaming rig")
    fake_server.devices[0]["sensors"].append(
        {
            "key": "gpu_0_usage",
            "name": "GPU usage",
            "platform": "sensor",
            "value": 40,
            "unit": "%",
        }
    )
    await push_and_wait(hass, fake_server, init_integration)
    assert _entity_id(hass, "sensor", PC_ID, "gpu_0_usage")
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, PC_ID), init_integration.entry_id
    )
    assert device is not None
    assert device.name == "Gaming rig"


async def test_a_deleted_device_is_removed(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_server: FakeServer
) -> None:
    cpu_id = _entity_id(hass, "sensor", PC_ID, "cpu_usage")

    fake_server.devices = [d for d in fake_server.devices if d["id"] != PC_ID]
    await push_and_wait(hass, fake_server, init_integration)

    assert (
        dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, PC_ID), init_integration.entry_id
        )
        is None
    )
    assert er.async_get(hass).async_get(cpu_id) is None
    assert hass.states.get(cpu_id) is None

    # It comes back with the same id: device and entities again.
    fake_server.devices.append(computer())
    await push_and_wait(hass, fake_server, init_integration)
    assert (
        dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, PC_ID), init_integration.entry_id
        )
        is not None
    )
    state = hass.states.get(cpu_id)
    assert state is not None
    assert state.state == "12.5"


async def test_a_lost_connection_makes_everything_unavailable_until_back(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_server: FakeServer
) -> None:
    client = get_client(init_integration)
    cpu_id = _entity_id(hass, "sensor", PC_ID, "cpu_usage")
    connections = fake_server.connections

    await fake_server.drop_clients()
    await wait_for(hass, lambda: not client.connected)
    state = hass.states.get(cpu_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    connected = hass.states.get("binary_sensor.home_network_manager_connected")
    assert connected is not None
    assert connected.state == "off"

    await wait_for(
        hass, lambda: fake_server.connections > connections and client.connected
    )
    await wait_for(hass, lambda: hass.states.get(cpu_id).state == "12.5")  # type: ignore[union-attr]
    assert fake_server.hellos[-1]["protocol"] == 1


async def test_a_replaced_key_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_server: FakeServer
) -> None:
    client = get_client(init_integration)
    fake_server.key = "nm_ha_new"
    await fake_server.drop_clients(code=4401)

    def reauth_started() -> bool:
        return any(
            flow["context"]["source"] == SOURCE_REAUTH
            for flow in hass.config_entries.flow.async_progress()
        )

    await wait_for(hass, reauth_started)
    assert not client.connected


async def test_the_integration_ships_its_icon(hass: HomeAssistant) -> None:
    integration = await async_get_integration(hass, DOMAIN)
    assert integration.has_branding
    brand = Path(integration.file_path) / "brand"
    assert (brand / "icon.png").is_file()
    assert (brand / "icon@2x.png").is_file()


async def test_a_retired_last_seen_entity_is_removed(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_server: FakeServer
) -> None:
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{INSTANCE_ID}_{PC_ID}_last_seen",
        config_entry=config_entry,
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(stale.entity_id) is None
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
