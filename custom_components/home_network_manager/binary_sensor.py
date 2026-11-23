"""Binary sensors for the Home Network Manager integration."""

from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import JsonDict, NetworkManagerConfigEntry
from .const import PLATFORM_BINARY_SENSOR
from .entity import (
    NetworkManagerEntity,
    NetworkManagerServerEntity,
    async_add_snapshot_entities,
    enum_value,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the server's connection sensor and every device's binary sensors as they arrive."""
    async_add_entities([ConnectedSensor(entry, "connected")])
    async_add_snapshot_entities(
        hass,
        entry,
        PLATFORM_BINARY_SENSOR,
        NetworkManagerBinarySensor,
        async_add_entities,
    )


class NetworkManagerBinarySensor(NetworkManagerEntity, BinarySensorEntity):
    """A yes/no data point: online, running, reboot required, …."""

    @override
    def _describe(self, sensor: JsonDict) -> None:
        super()._describe(sensor)
        self._attr_device_class = enum_value(
            BinarySensorDeviceClass, sensor.get("deviceClass")
        )

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the latest value."""
        sensor = self.sensor
        value = sensor["value"] if sensor is not None else None
        return value if isinstance(value, bool) else None


class ConnectedSensor(NetworkManagerServerEntity, BinarySensorEntity):
    """Whether the socket to the server is up; never unavailable itself."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    @override
    def is_on(self) -> bool:
        """Return True while connected."""
        return self._client.connected
