"""Config flow for the Home Network Manager integration."""

from collections.abc import Mapping
import logging
from typing import Any, override

import probatio

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import (
    CannotConnect,
    InvalidAuth,
    JsonDict,
    async_validate_connection,
    normalize_url,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
_KEY_SCHEMA = probatio.Schema({probatio.Required(CONF_API_KEY): _KEY_SELECTOR})
_USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_URL): _URL_SELECTOR,
        probatio.Required(CONF_API_KEY): _KEY_SELECTOR,
    }
)


async def _async_try_connect(
    flow: ConfigFlow, url: str, key: str
) -> tuple[JsonDict | None, dict[str, str]]:
    """Return the server's welcome, or form errors."""
    try:
        welcome = await async_validate_connection(flow.hass, url, key)
    except InvalidAuth:
        return None, {"base": "invalid_auth"}
    except CannotConnect as err:
        _LOGGER.debug("Cannot connect to Home Network Manager at %s: %s", url, err)
        return None, {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unexpected error validating Home Network Manager")
        return None, {"base": "unknown"}
    return welcome, {}


def _parse(user_input: dict[str, Any]) -> tuple[str | None, str]:
    """Return the normalized URL (None when invalid) and the key."""
    key: str = user_input[CONF_API_KEY].strip()
    try:
        return normalize_url(user_input[CONF_URL]), key
    except ValueError:
        return None, key


def _title(welcome: JsonDict) -> str:
    name = welcome.get("name")
    return name if isinstance(name, str) and name else "Home Network Manager"


class NetworkManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow: manual setup, reauth and reconfigure."""

    VERSION = 1

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a server by URL and key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url, key = _parse(user_input)
            if url is None:
                errors[CONF_URL] = "invalid_url"
            else:
                welcome, errors = await _async_try_connect(self, url, key)
                if welcome is not None:
                    await self.async_set_unique_id(welcome["instanceId"])
                    self._abort_if_unique_id_configured(
                        updates={CONF_URL: url, CONF_API_KEY: key}
                    )
                    return self.async_create_entry(
                        title=_title(welcome), data={CONF_URL: url, CONF_API_KEY: key}
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth after the server rejected the key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            key: str = user_input[CONF_API_KEY].strip()
            welcome, errors = await _async_try_connect(self, entry.data[CONF_URL], key)
            if welcome is not None:
                await self.async_set_unique_id(welcome["instanceId"])
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_KEY_SCHEMA,
            errors=errors,
            description_placeholders={"name": entry.title, "url": entry.data[CONF_URL]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the server URL or the key."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url, key = _parse(user_input)
            if url is None:
                errors[CONF_URL] = "invalid_url"
            else:
                welcome, errors = await _async_try_connect(self, url, key)
                if welcome is not None:
                    await self.async_set_unique_id(welcome["instanceId"])
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_URL: url, CONF_API_KEY: key}
                    )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _USER_SCHEMA, user_input or {CONF_URL: entry.data[CONF_URL]}
            ),
            errors=errors,
        )
