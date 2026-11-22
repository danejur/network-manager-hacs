"""Tests for diagnostics."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_network_manager.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .conftest import KEY


async def test_diagnostics_redact_the_key(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diagnostics["entry"]["data"][CONF_API_KEY] == "**REDACTED**"
    assert KEY not in str(diagnostics)
    assert diagnostics["connection"]["connected"] is True
    assert len(diagnostics["devices"]) == 2
