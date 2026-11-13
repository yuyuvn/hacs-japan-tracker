"""Config + options flow: pick the to-do list that holds tracking numbers."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AUTO_COMPLETE,
    CONF_SCAN_INTERVAL,
    CONF_TODO_ENTITY,
    CONF_WRITE_STATUS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

TODO_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="todo"))


def _settings_schema(defaults: dict[str, Any]) -> dict:
    return {
        vol.Required(CONF_TODO_ENTITY, default=defaults.get(CONF_TODO_ENTITY, vol.UNDEFINED)): TODO_SELECTOR,
        vol.Required(CONF_AUTO_COMPLETE, default=defaults.get(CONF_AUTO_COMPLETE, True)): bool,
        vol.Required(CONF_WRITE_STATUS, default=defaults.get(CONF_WRITE_STATUS, True)): bool,
        vol.Required(
            CONF_SCAN_INTERVAL, default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        ): vol.All(vol.Coerce(int), vol.Range(min=15, max=1440)),
    }


class JapanTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input[CONF_TODO_ENTITY]
            await self.async_set_unique_id(entity_id)
            self._abort_if_unique_id_configured()
            if self.hass.states.get(entity_id) is None:
                errors[CONF_TODO_ENTITY] = "list_not_found"
            else:
                state = self.hass.states.get(entity_id)
                title = state.attributes.get("friendly_name", entity_id)
                return self.async_create_entry(title=f"Parcels: {title}", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(_settings_schema(user_input or {})), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return JapanTrackerOptionsFlow()


class JapanTrackerOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=vol.Schema(_settings_schema(current)))
