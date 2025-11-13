"""Adds config flow for Presence Based Lighting."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import selector

from .const import (
    CONF_ROOM_NAME,
    CONF_LIGHT_ENTITIES,
    CONF_PRESENCE_SENSORS,
    CONF_OFF_DELAY,
    DEFAULT_OFF_DELAY,
    DOMAIN,
)


class PresenceBasedLightingFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for presence_based_lighting."""

    VERSION = 1

    def __init__(self):
        """Initialize."""
        self._errors = {}

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        self._errors = {}

        if user_input is not None:
            # Validate that entities exist
            await self.async_set_unique_id(user_input[CONF_ROOM_NAME])
            self._abort_if_unique_id_configured()
            
            return self.async_create_entry(
                title=user_input[CONF_ROOM_NAME], 
                data=user_input
            )

        return await self._show_config_form(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PresenceBasedLightingOptionsFlowHandler(config_entry)

    async def _show_config_form(self, user_input):
        """Show the configuration form to edit location data."""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROOM_NAME): str,
                    vol.Required(CONF_LIGHT_ENTITIES): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="light",
                            multiple=True,
                        )
                    ),
                    vol.Required(CONF_PRESENCE_SENSORS): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="binary_sensor",
                            device_class="occupancy",
                            multiple=True,
                        )
                    ),
                    vol.Optional(
                        CONF_OFF_DELAY, default=DEFAULT_OFF_DELAY
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
                }
            ),
            errors=self._errors,
        )


class PresenceBasedLightingOptionsFlowHandler(config_entries.OptionsFlow):
    """Config flow options handler for presence_based_lighting."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LIGHT_ENTITIES,
                        default=self.config_entry.data.get(CONF_LIGHT_ENTITIES, []),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="light",
                            multiple=True,
                        )
                    ),
                    vol.Required(
                        CONF_PRESENCE_SENSORS,
                        default=self.config_entry.data.get(CONF_PRESENCE_SENSORS, []),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="binary_sensor",
                            device_class="occupancy",
                            multiple=True,
                        )
                    ),
                    vol.Required(
                        CONF_OFF_DELAY,
                        default=self.config_entry.data.get(CONF_OFF_DELAY, DEFAULT_OFF_DELAY),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
                }
            ),
        )
