"""Entities fed by the server's snapshots.

The server describes every data point completely (name, unit, device class,
state class, category), so entities are created from the snapshot itself: a
data point new to a device becomes a new entity on the next push, without an
integration release.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Any, override

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import JsonDict, NetworkManagerClient, NetworkManagerConfigEntry
from .const import DOMAIN, signal_update

type _Known = set[tuple[str, str]]


def enum_value[E: StrEnum](enum: type[E], value: object) -> E | None:
    """Return the enum member for a server string, or None for one this Home Assistant doesn't know."""
    if not isinstance(value, str):
        return None
    try:
        return enum(value)
    except ValueError:
        return None


class NetworkManagerEntity(Entity):
    """One data point of one computer or container."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: NetworkManagerConfigEntry,
        device_id: str,
        sensor: JsonDict,
        known: _Known,
    ) -> None:
        """Initialize the entity from its first snapshot description."""
        self._entry = entry
        self._client: NetworkManagerClient = entry.runtime_data.client
        self._device_id = device_id
        self._key: str = sensor["key"]
        self._known = known
        self._attr_unique_id = f"{self._client.instance_id}_{device_id}_{self._key}"
        # Identifiers only: the client creates (and names) the device.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})
        self._describe(sensor)

    @property
    def sensor(self) -> JsonDict | None:
        """Return this data point in the latest snapshot, if it is there."""
        device = self._client.devices.get(self._device_id)
        if device is None:
            return None
        sensor: JsonDict | None = device["sensors"].get(self._key)
        return sensor

    @property
    @override
    def available(self) -> bool:
        """Return True while connected and the server has a value for it."""
        return self._client.connected and self.sensor is not None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the data point's extra values, such as package names."""
        sensor = self.sensor
        attributes = sensor.get("attributes") if sensor is not None else None
        return attributes if isinstance(attributes, dict) else None

    def _describe(self, sensor: JsonDict) -> None:
        """Apply what the server says about the data point (it may rename one)."""
        self._attr_name = sensor["name"]
        icon = sensor.get("icon")
        self._attr_icon = icon if isinstance(icon, str) else None
        self._attr_entity_category = enum_value(
            EntityCategory, sensor.get("entityCategory")
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to client updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self._entry.entry_id), self._handle_update
            )
        )

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Forget the entity, so it is created again if its device returns."""
        self._known.discard((self._device_id, self._key))
        await super().async_will_remove_from_hass()

    @callback
    def _handle_update(self) -> None:
        if (sensor := self.sensor) is not None:
            self._describe(sensor)
        self.async_write_ha_state()


@callback
def async_add_snapshot_entities(
    hass: HomeAssistant,
    entry: NetworkManagerConfigEntry,
    platform: str,
    factory: Callable[[NetworkManagerConfigEntry, str, JsonDict, _Known], Entity],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add an entity for every data point of `platform` in each snapshot that isn't one yet."""
    client = entry.runtime_data.client
    known: _Known = set()

    @callback
    def add_new() -> None:
        new: list[Entity] = []
        for device_id, device in client.devices.items():
            for key, sensor in device["sensors"].items():
                if (
                    sensor.get("platform", "sensor") != platform
                    or (device_id, key) in known
                ):
                    continue
                known.add((device_id, key))
                new.append(factory(entry, device_id, sensor, known))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_update(entry.entry_id), add_new)
    )


class NetworkManagerServerEntity(Entity):
    """An entity of the server itself (the hub device)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: NetworkManagerConfigEntry, key: str) -> None:
        """Initialize the entity."""
        self._entry = entry
        self._client: NetworkManagerClient = entry.runtime_data.client
        self._attr_unique_id = f"{self._client.instance_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._client.instance_id)}
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to client updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_update(self._entry.entry_id),
                self.async_write_ha_state,
            )
        )
