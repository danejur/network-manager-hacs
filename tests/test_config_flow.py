"""Tests for the config flow."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_network_manager.client import normalize_url, socket_url
from custom_components.home_network_manager.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import INSTANCE_ID, KEY, FakeServer, unused_port


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Keep flow tests from starting the real client."""
    with patch(
        "custom_components.home_network_manager.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow(hass: HomeAssistant, fake_server: FakeServer) -> None:
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: f"{fake_server.url}/", CONF_API_KEY: f" {KEY} "}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home Network Manager"
    assert result["result"].unique_id == INSTANCE_ID
    assert result["data"] == {CONF_URL: fake_server.url, CONF_API_KEY: KEY}
    assert fake_server.hellos[-1]["key"] == KEY


async def test_user_flow_errors(hass: HomeAssistant, fake_server: FakeServer) -> None:
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: fake_server.url, CONF_API_KEY: "nm_ha_wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: f"http://127.0.0.1:{unused_port()}", CONF_API_KEY: KEY}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: "ftp://nm.example.com", CONF_API_KEY: KEY}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: fake_server.url, CONF_API_KEY: KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_same_server_twice_aborts_and_updates(
    hass: HomeAssistant, fake_server: FakeServer
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=INSTANCE_ID,
        data={CONF_URL: "http://old.example", CONF_API_KEY: "nm_ha_old"},
    )
    entry.add_to_hass(hass)

    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: fake_server.url, CONF_API_KEY: KEY}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {CONF_URL: fake_server.url, CONF_API_KEY: KEY}


async def test_reauth(hass: HomeAssistant, fake_server: FakeServer) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=INSTANCE_ID,
        data={CONF_URL: fake_server.url, CONF_API_KEY: "nm_ha_old"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "nm_ha_wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: KEY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == KEY


async def test_reconfigure(hass: HomeAssistant, fake_server: FakeServer) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=INSTANCE_ID,
        data={CONF_URL: "http://old.example", CONF_API_KEY: "nm_ha_old"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: fake_server.url, CONF_API_KEY: KEY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_URL: fake_server.url, CONF_API_KEY: KEY}


@pytest.mark.parametrize(
    ("entered", "expected"),
    [
        ("nm.example.com", "https://nm.example.com"),
        ("https://nm.example.com/", "https://nm.example.com"),
        ("http://192.168.1.10:8080", "http://192.168.1.10:8080"),
    ],
)
def test_normalize_url(entered: str, expected: str) -> None:
    assert normalize_url(entered) == expected


def test_socket_url() -> None:
    assert (
        str(socket_url("https://nm.example.com"))
        == "wss://nm.example.com/api/home-assistant/connect"
    )
    assert (
        str(socket_url("http://host:8080/nm"))
        == "ws://host:8080/nm/api/home-assistant/connect"
    )
