"""Fixtures for Home Network Manager tests: a fake server speaking protocol v1."""

import asyncio
from collections.abc import AsyncGenerator, Callable
import copy
import socket
from typing import Any

from aiohttp import WSCloseCode, web
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_network_manager import client as client_module
from custom_components.home_network_manager.client import NetworkManagerClient
from custom_components.home_network_manager.const import DOMAIN
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant

KEY = "nm_ha_s3cr3t"
INSTANCE_ID = "5b0c2d8e-3f43-4c39-9a51-2f3e7f0b9c11"
PC_ID = "0d6f2c1e-0000-4000-8000-000000000001"
HOST_ID = "0d6f2c1e-0000-4000-8000-000000000002"


def computer(
    device_id: str = PC_ID, name: str = "Gaming PC", cpu: float | None = 12.5
) -> dict[str, Any]:
    """Return a computer as the API describes it."""
    sensors: list[dict[str, Any]] = [
        {
            "key": "state",
            "name": "State",
            "platform": "sensor",
            "value": "online",
            "deviceClass": "enum",
            "options": [
                "online",
                "sleeping",
                "offline",
                "pending_enrollment",
                "revoked",
            ],
        },
        {
            "key": "online",
            "name": "Online",
            "platform": "binary_sensor",
            "value": True,
            "deviceClass": "connectivity",
        },
        {
            "key": "connected",
            "name": "Connected",
            "platform": "binary_sensor",
            "value": True,
            "deviceClass": "connectivity",
            "entityCategory": "diagnostic",
        },
        {
            "key": "memory_used",
            "name": "Memory used",
            "platform": "sensor",
            "value": 8589934592,
            "unit": "B",
            "suggestedUnit": "GiB",
            "deviceClass": "data_size",
            "stateClass": "measurement",
            "precision": 1,
        },
        {
            "key": "last_boot",
            "name": "Last boot",
            "platform": "sensor",
            "value": "2026-11-17T11:00:00+00:00",
            "deviceClass": "timestamp",
        },
        {
            "key": "kernel",
            "name": "Kernel",
            "platform": "sensor",
            "value": "7.3.3",
            "entityCategory": "diagnostic",
        },
        {
            "key": "updates",
            "name": "Pending updates",
            "platform": "sensor",
            "value": 3,
            "stateClass": "measurement",
            "attributes": {
                "packages": ["linux", "mesa", "yay"],
                "more": 0,
                "error": None,
            },
        },
    ]
    if cpu is not None:
        sensors.append(
            {
                "key": "cpu_usage",
                "name": "CPU usage",
                "platform": "sensor",
                "value": cpu,
                "unit": "%",
                "stateClass": "measurement",
                "precision": 0,
            }
        )
    return {
        "id": device_id,
        "kind": "computer",
        "name": name,
        "model": "Desktop",
        "swVersion": "CachyOS",
        "configurationPath": f"/devices/{device_id}",
        "sensors": sensors,
    }


def default_devices() -> list[dict[str, Any]]:
    """Return two computers, one of them a Proxmox host."""
    host = computer(HOST_ID, "pve", cpu=3.0)
    host["model"] = "Server"
    return [computer(), host]


class FakeServer:
    """An aiohttp server on an ephemeral localhost port that acts like the API."""

    def __init__(self) -> None:
        """Initialize with two computers."""
        self.key = KEY
        self.devices = default_devices()
        self.clients: list[web.WebSocketResponse] = []
        self.connections = 0
        self.hellos: list[dict[str, Any]] = []
        self.port = 0
        self._runner: web.AppRunner | None = None

    @property
    def url(self) -> str:
        """Return the server URL as a user enters it."""
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        """Start listening on 127.0.0.1 with an ephemeral port."""
        app = web.Application()
        app.router.add_get("/api/home-assistant/connect", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        await web.SockSite(self._runner, sock).start()

    async def stop(self) -> None:
        """Close all clients and the server."""
        await self.drop_clients()
        if self._runner is not None:
            await self._runner.cleanup()

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        hello = await ws.receive_json()
        self.hellos.append(hello)
        if hello.get("type") != "hello" or hello.get("key") != self.key:
            await ws.close(code=4401, message=b"invalid key")
            return ws
        self.connections += 1
        await ws.send_json(
            {
                "type": "welcome",
                "protocol": 1,
                "instanceId": INSTANCE_ID,
                "name": "Home Network Manager",
                "pushIntervalSeconds": 30,
            }
        )
        await self.send_snapshot(ws)
        self.clients.append(ws)
        try:
            async for _ in ws:
                pass  # Home Assistant sends nothing after hello
        finally:
            self.clients.remove(ws)
        return ws

    async def send_snapshot(self, ws: web.WebSocketResponse) -> None:
        """Send the current devices to one client."""
        await ws.send_json(
            {
                "type": "snapshot",
                "sentAtUtc": "2026-11-17T12:00:00+00:00",
                "devices": copy.deepcopy(self.devices),
            }
        )

    async def push(self) -> None:
        """Send a snapshot to every connected client."""
        for ws in list(self.clients):
            await self.send_snapshot(ws)

    async def drop_clients(self, code: int = WSCloseCode.GOING_AWAY) -> None:
        """Close every client connection from the server side."""
        for ws in list(self.clients):
            await ws.close(code=code)


async def wait_for(
    hass: HomeAssistant, predicate: Callable[[], bool], timeout: float = 5.0
) -> None:
    """Wait until the predicate holds, then let Home Assistant settle."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)
    await hass.async_block_till_done()


def get_client(entry: MockConfigEntry) -> NetworkManagerClient:
    """Return the client of a loaded entry."""
    client: NetworkManagerClient = entry.runtime_data.client
    return client


async def push_and_wait(
    hass: HomeAssistant, server: FakeServer, entry: MockConfigEntry
) -> None:
    """Push a snapshot and wait until the client has processed it."""
    client = get_client(entry)
    received = client.snapshots_received
    await server.push()
    await wait_for(hass, lambda: client.snapshots_received > received)


def unused_port() -> int:
    """Return a localhost port with nothing listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
def fast_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink reconnect timing so tests do not wait for real backoff."""
    monkeypatch.setattr(client_module, "BACKOFF_INITIAL", 0.01)
    monkeypatch.setattr(client_module, "BACKOFF_MAX", 0.05)
    monkeypatch.setattr(client_module, "HELLO_TIMEOUT", 2.0)


@pytest.fixture
async def fake_server(socket_enabled: None) -> AsyncGenerator[FakeServer]:
    """Run a fake server for the duration of the test."""
    server = FakeServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
def config_entry(hass: HomeAssistant, fake_server: FakeServer) -> MockConfigEntry:
    """Return a config entry pointing at the fake server."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home Network Manager",
        unique_id=INSTANCE_ID,
        data={CONF_URL: fake_server.url, CONF_API_KEY: KEY},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_server: FakeServer
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration and wait for the first snapshot."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = get_client(config_entry)
    await wait_for(hass, lambda: client.connected and client.snapshots_received > 0)
    yield config_entry
    # Unload while the server still runs so the client task ends cleanly.
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
