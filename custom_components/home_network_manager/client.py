"""WebSocket client for the Home Network Manager API (protocol v1).

Home Assistant connects out to `/api/home-assistant/connect` and sends `hello`
with the key. The API answers `welcome`, then pushes a full `snapshot` of every
computer at once and every push interval, with a `ping` every
15 s in between. It closes with 4401 when the key is wrong, replaced or removed.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import random
from typing import Any, Literal, NoReturn

import aiohttp
from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import (
    CLOSE_AUTH_FAILED,
    CLOSE_PROTOCOL_TOO_OLD,
    DOMAIN,
    PROTOCOL_VERSION,
    WS_PATH,
    signal_update,
)

_LOGGER = logging.getLogger(__name__)

type JsonDict = dict[str, Any]

# Timing knobs are module attributes read at call time so tests can patch them.
BACKOFF_INITIAL = 1.0
BACKOFF_MAX = 60.0
BACKOFF_JITTER = 0.2  # fraction of the delay removed at random
HELLO_TIMEOUT = 10.0  # connect, hello and welcome together
INACTIVITY_TIMEOUT = 45.0  # the API pings every 15 s
WS_HEARTBEAT = 30.0

# decode_text=True (the default) makes text frames arrive as str.
type _WebSocket = aiohttp.ClientWebSocketResponse[Literal[True]]

_CLOSED_TYPES = frozenset(
    {
        aiohttp.WSMsgType.CLOSE,
        aiohttp.WSMsgType.CLOSING,
        aiohttp.WSMsgType.CLOSED,
        aiohttp.WSMsgType.ERROR,
    }
)


class NetworkManagerError(Exception):
    """Base error for the client."""


class CannotConnect(NetworkManagerError):
    """The server is unreachable or broke the protocol."""


class InvalidAuth(NetworkManagerError):
    """The server rejected the key (close code 4401)."""


def normalize_url(url: str) -> str:
    """Return the server URL with a scheme and without a trailing slash.

    Raises ValueError for anything that is not an http(s) URL with a host.
    """
    url = url.strip()
    if "://" not in url:
        url = f"https://{url}"
    parsed = URL(url)
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise ValueError(url)
    return str(parsed.with_query(None).with_fragment(None)).rstrip("/")


def socket_url(url: str) -> URL:
    """Return the WebSocket URL of a server URL; a path prefix is kept."""
    base = URL(url)
    return base.with_scheme("wss" if base.scheme == "https" else "ws").with_path(
        base.path.rstrip("/") + WS_PATH
    )


def _decode(msg: aiohttp.WSMessage) -> JsonDict | None:
    """Return a protocol message from a text frame, or None if it is not one."""
    if msg.type is not aiohttp.WSMsgType.TEXT:
        return None
    try:
        data = json.loads(msg.data)
    except ValueError:
        _LOGGER.debug("Ignoring non-JSON frame")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        _LOGGER.debug("Ignoring frame without a message type")
        return None
    return data


def _raise_for_close(ws: _WebSocket) -> NoReturn:
    code = ws.close_code
    if code == CLOSE_AUTH_FAILED:
        raise InvalidAuth
    if code == CLOSE_PROTOCOL_TOO_OLD:
        raise CannotConnect("The server needs a newer version of this integration")
    raise CannotConnect(f"Server closed the connection ({code})")


async def _async_open(
    session: aiohttp.ClientSession,
    url: str,
    key: str,
    version: str,
    *,
    heartbeat: float | None,
) -> tuple[_WebSocket, JsonDict]:
    """Open the socket, send hello and return it with the server's welcome."""
    try:
        ws = await session.ws_connect(socket_url(url), heartbeat=heartbeat)
    except aiohttp.WSServerHandshakeError as err:
        raise CannotConnect(f"Server refused the upgrade: HTTP {err.status}") from None
    except (aiohttp.ClientError, OSError) as err:
        raise CannotConnect(str(err) or type(err).__name__) from err

    try:
        await ws.send_json(
            {
                "type": "hello",
                "key": key,
                "protocol": PROTOCOL_VERSION,
                "version": version,
            }
        )
        welcome = await _async_receive_welcome(ws)
    except BaseException:
        await ws.close()
        raise
    return ws, welcome


async def _async_receive_welcome(ws: _WebSocket) -> JsonDict:
    msg = await ws.receive()
    if msg.type in _CLOSED_TYPES:
        _raise_for_close(ws)
    welcome = _decode(msg)
    if (
        welcome is None
        or welcome["type"] != "welcome"
        or not isinstance(welcome.get("instanceId"), str)
    ):
        raise CannotConnect("First frame from the server was not a valid welcome")
    return welcome


async def _async_version(hass: HomeAssistant) -> str:
    return str((await async_get_integration(hass, DOMAIN)).version or "unknown")


async def async_validate_connection(
    hass: HomeAssistant, url: str, key: str
) -> JsonDict:
    """Connect once, return the server's welcome and disconnect.

    Raises InvalidAuth on 4401 and CannotConnect on anything else, including no
    welcome within HELLO_TIMEOUT.
    """
    session = async_get_clientsession(hass)
    version = await _async_version(hass)
    try:
        async with asyncio.timeout(HELLO_TIMEOUT):
            # One-shot probe: no heartbeat, which would only leave a timer behind.
            ws, welcome = await _async_open(session, url, key, version, heartbeat=None)
            await ws.close()
            return welcome
    except TimeoutError as err:
        raise CannotConnect("Timed out waiting for the welcome") from err


def _parse_devices(snapshot: JsonDict) -> dict[str, JsonDict] | None:
    """Index a snapshot's devices by id and their sensors by key; None if malformed."""
    devices = snapshot.get("devices")
    if not isinstance(devices, list):
        return None
    result: dict[str, JsonDict] = {}
    for device in devices:
        if (
            not isinstance(device, dict)
            or not isinstance(device.get("id"), str)
            or not isinstance(device.get("name"), str)
            or not isinstance(device.get("sensors"), list)
        ):
            continue
        sensors = {
            sensor["key"]: sensor
            for sensor in device["sensors"]
            if isinstance(sensor, dict)
            and isinstance(sensor.get("key"), str)
            and isinstance(sensor.get("name"), str)
            and "value" in sensor
        }
        result[device["id"]] = {**device, "sensors": sensors}
    return result


class NetworkManagerClient:
    """Keeps one socket to the server open and mirrors its latest snapshot.

    Runs as a config entry background task. Each snapshot replaces `devices`,
    syncs the device registry (new, renamed and deleted devices) and notifies
    the platforms through the dispatcher.
    """

    def __init__(self, hass: HomeAssistant, entry: NetworkManagerConfigEntry) -> None:
        """Initialize the client from the config entry."""
        self._hass = hass
        self._entry = entry
        self.instance_id: str = entry.unique_id or entry.entry_id
        self.url: str = entry.data[CONF_URL]
        self._key: str = entry.data[CONF_API_KEY]
        self.connected = False
        self.devices: dict[str, JsonDict] = {}
        self.last_snapshot: datetime | None = None
        self.push_interval: int | None = None
        self.failed_attempts = 0
        self.snapshots_received = 0
        self._signal_update = signal_update(entry.entry_id)
        self._outage_logged = False

    async def async_run(self) -> None:
        """Connect and reconnect forever, until canceled or the key is rejected."""
        version = await _async_version(self._hass)
        while True:
            try:
                await self._async_connect_and_listen(version)
            except InvalidAuth:
                break
            except TimeoutError:
                self._log_outage("no data received within the timeout")
            except (CannotConnect, aiohttp.ClientError, OSError) as err:
                self._log_outage(str(err) or type(err).__name__)
            finally:
                # Entities go unavailable before the sleep; also runs on cancellation.
                self._set_connected(False)
            await asyncio.sleep(self._next_backoff())
        # Retrying with a rejected key is pointless; wait for the user.
        _LOGGER.error(
            "Home Network Manager at %s rejected the key; reauthentication required",
            self.url,
        )
        self._entry.async_start_reauth(self._hass)

    def _next_backoff(self) -> float:
        """Return 1, 2, 4 … BACKOFF_MAX seconds, minus random jitter."""
        delay = min(BACKOFF_MAX, BACKOFF_INITIAL * 2.0 ** min(self.failed_attempts, 16))
        self.failed_attempts += 1
        # Jitter only spreads reconnects; not security relevant.
        jitter: float = random.uniform(0, delay * BACKOFF_JITTER)  # noqa: S311
        return delay - jitter

    def _log_outage(self, reason: str) -> None:
        # Log the first failure of an outage only; retries are debug noise.
        if self._outage_logged:
            _LOGGER.debug("Home Network Manager still unavailable: %s", reason)
            return
        self._outage_logged = True
        _LOGGER.warning(
            "Home Network Manager at %s unavailable (%s); retrying", self.url, reason
        )

    async def _async_connect_and_listen(self, version: str) -> None:
        session = async_get_clientsession(self._hass)
        async with asyncio.timeout(HELLO_TIMEOUT):
            ws, welcome = await _async_open(
                session, self.url, self._key, version, heartbeat=WS_HEARTBEAT
            )
        try:
            self._on_welcome(welcome)
            while True:
                # Raises TimeoutError after INACTIVITY_TIMEOUT without any frame.
                msg = await ws.receive(timeout=INACTIVITY_TIMEOUT)
                if msg.type in _CLOSED_TYPES:
                    _raise_for_close(ws)
                data = _decode(msg)
                if data is not None and data["type"] == "snapshot":
                    self._on_snapshot(data)
        finally:
            await ws.close()

    @callback
    def _set_connected(self, connected: bool) -> None:
        if connected == self.connected:
            return
        self.connected = connected
        async_dispatcher_send(self._hass, self._signal_update)

    @callback
    def _on_welcome(self, welcome: JsonDict) -> None:
        if self._outage_logged:
            _LOGGER.info("Home Network Manager at %s is back", self.url)
            self._outage_logged = False
        self.failed_attempts = 0
        if welcome.get("protocol") != PROTOCOL_VERSION:
            _LOGGER.warning(
                "Home Network Manager speaks protocol %s, expected %s",
                welcome.get("protocol"),
                PROTOCOL_VERSION,
            )
        if welcome["instanceId"] != self.instance_id:
            _LOGGER.warning(
                "Home Network Manager at %s reports id %s, configured for %s",
                self.url,
                welcome["instanceId"],
                self.instance_id,
            )
        interval = welcome.get("pushIntervalSeconds")
        self.push_interval = interval if isinstance(interval, int) else None
        self._set_connected(True)

    @callback
    def _on_snapshot(self, snapshot: JsonDict) -> None:
        devices = _parse_devices(snapshot)
        if devices is None:
            _LOGGER.debug("Ignoring a snapshot without a device list")
            return
        self.snapshots_received += 1
        self.devices = devices
        sent = snapshot.get("sentAtUtc")
        self.last_snapshot = (
            dt_util.parse_datetime(sent) if isinstance(sent, str) else None
        ) or dt_util.utcnow()
        self._sync_devices()
        async_dispatcher_send(self._hass, self._signal_update)

    @callback
    def _sync_devices(self) -> None:
        """Create or update every device in the snapshot and remove the deleted ones."""
        registry = dr.async_get(self._hass)
        base = URL(self.url)
        # Every computer hangs off the server's own device.
        server = registry.async_get_device_by_identifier(
            (DOMAIN, self.instance_id), self._entry.entry_id
        )
        for device in self.devices.values():
            path = device.get("configurationPath")
            registry.async_get_or_create(
                config_entry_id=self._entry.entry_id,
                identifiers={(DOMAIN, device["id"])},
                name=device["name"],
                model=device.get("model"),
                sw_version=device.get("swVersion"),
                via_device_id=server.id if server is not None else None,
                configuration_url=str(base.with_path(base.path.rstrip("/") + path))
                if isinstance(path, str)
                else None,
            )

        # The snapshot is the full list: a device missing from it was deleted.
        keep = {*self.devices, self.instance_id}
        for entry in dr.async_entries_for_config_entry(registry, self._entry.entry_id):
            ids = {value for domain, value in entry.identifiers if domain == DOMAIN}
            if ids and not ids & keep:
                registry.async_remove_device(entry.id)


@dataclass
class NetworkManagerRuntimeData:
    """Runtime data stored in the config entry."""

    client: NetworkManagerClient


type NetworkManagerConfigEntry = ConfigEntry[NetworkManagerRuntimeData]
