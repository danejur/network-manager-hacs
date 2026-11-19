"""Sensors for the Home Network Manager integration."""

from datetime import datetime
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .client import JsonDict, NetworkManagerConfigEntry
from .const import PLATFORM_SENSOR
from .entity import (
    NetworkManagerEntity,
    NetworkManagerServerEntity,
    _Known,
    async_add_snapshot_entities,
    enum_value,
)

# Entities only read client state; nothing to parallelize.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the server's own sensor and every device's sensors as they arrive."""
    async_add_entities([LastUpdateSensor(entry, "last_update")])
    async_add_snapshot_entities(
        hass, entry, PLATFORM_SENSOR, NetworkManagerSensor, async_add_entities
    )


class NetworkManagerSensor(NetworkManagerEntity, SensorEntity):
    """A data point with a value: a number, a text, a state or a time."""

    def __init__(
        self,
        entry: NetworkManagerConfigEntry,
        device_id: str,
        sensor: JsonDict,
        known: _Known,
    ) -> None:
        """Initialize the sensor; the suggested unit and precision apply once, at registration."""
        super().__init__(entry, device_id, sensor, known)
        suggested = sensor.get("suggestedUnit")
        self._attr_suggested_unit_of_measurement = (
            suggested if isinstance(suggested, str) else None
        )
        precision = sensor.get("precision")
        self._attr_suggested_display_precision = (
            precision if isinstance(precision, int) else None
        )

    @override
    def _describe(self, sensor: JsonDict) -> None:
        super()._describe(sensor)
        self._attr_device_class = enum_value(
            SensorDeviceClass, sensor.get("deviceClass")
        )
        self._attr_state_class = enum_value(SensorStateClass, sensor.get("stateClass"))
        unit = sensor.get("unit")
        self._attr_native_unit_of_measurement = unit if isinstance(unit, str) else None
        options = sensor.get("options")
        self._attr_options = (
            [str(option) for option in options] if isinstance(options, list) else None
        )

    @property
    @override
    def native_value(self) -> StateType | datetime:
        """Return the latest value, parsed for timestamps."""
        sensor = self.sensor
        if sensor is None:
            return None
        value = sensor["value"]
        if self._attr_device_class is SensorDeviceClass.TIMESTAMP:
            return dt_util.parse_datetime(value) if isinstance(value, str) else None
        if isinstance(value, bool):
            # A sensor never holds a bool; the server sends those as binary sensors.
            return str(value).lower()
        if isinstance(value, int | float | str):
            return value
        return None


class LastUpdateSensor(NetworkManagerServerEntity, SensorEntity):
    """When the last snapshot arrived."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the time of the last snapshot."""
        return self._client.last_snapshot
