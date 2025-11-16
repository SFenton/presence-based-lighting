"""Adds config flow for Presence Based Lighting."""
from __future__ import annotations

import copy
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import selector

from .const import (
	CONF_CONTROLLED_ENTITIES,
	CONF_DISABLE_ON_EXTERNAL_CONTROL,
	CONF_ENTITY_ID,
	CONF_ENTITY_OFF_DELAY,
	CONF_INITIAL_PRESENCE_ALLOWED,
	CONF_OFF_DELAY,
	CONF_PRESENCE_CLEARED_SERVICE,
	CONF_PRESENCE_CLEARED_STATE,
	CONF_PRESENCE_DETECTED_SERVICE,
	CONF_PRESENCE_DETECTED_STATE,
	CONF_PRESENCE_SENSORS,
	CONF_RESPECTS_PRESENCE_ALLOWED,
	CONF_ROOM_NAME,
	DEFAULT_CLEARED_SERVICE,
	DEFAULT_CLEARED_STATE,
	DEFAULT_DETECTED_SERVICE,
	DEFAULT_DETECTED_STATE,
	DEFAULT_DISABLE_ON_EXTERNAL,
	DEFAULT_INITIAL_PRESENCE_ALLOWED,
	DEFAULT_OFF_DELAY,
	DEFAULT_RESPECTS_PRESENCE_ALLOWED,
	DOMAIN,
)

STEP_USER = "user"
STEP_CONTROLLED_ENTITIES = "controlled_entities"
FIELD_ADD_ANOTHER = "add_another"
FIELD_SKIP_ENTITY = "skip_entity"


def _required_field(key: str, default=None):
    if default is None:
        return vol.Required(key)
    return vol.Required(key, default=default)


class PresenceBasedLightingFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for presence_based_lighting."""

    VERSION = 2

    def __init__(self):
        """Initialize."""
        self._errors: dict[str, str] = {}
        self._base_data: dict = {}
        self._controlled_entities: list[dict] = []
        self._pending_entity_default: dict | None = None
        self._entity_defaults_queue: list[dict] = []
        self._active_entity_default = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step configured by the user."""
        self._errors = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_ROOM_NAME])
            self._abort_if_unique_id_configured()

            self._base_data = {
                CONF_ROOM_NAME: user_input[CONF_ROOM_NAME],
                CONF_PRESENCE_SENSORS: user_input[CONF_PRESENCE_SENSORS],
                CONF_OFF_DELAY: user_input[CONF_OFF_DELAY],
            }
            self._controlled_entities = []
            self._entity_defaults_queue = []
            self._pending_entity_default = None
            return await self.async_step_controlled_entities()

        return self.async_show_form(
            step_id=STEP_USER,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROOM_NAME): str,
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

    async def async_step_controlled_entities(self, user_input=None):
        """Collect controlled entity configuration entries."""
        if user_input is not None:
            self._errors = {}
            skip = user_input.get(FIELD_SKIP_ENTITY, False)

            if not skip:
                entity_config = self._extract_entity_config(user_input)
                if entity_config is None:
                    # Validation produced errors
                    return self._show_entity_form(user_input)
                self._controlled_entities.append(entity_config)

            # Reset pending default after successful handling
            if self._pending_entity_default is not None:
                self._pending_entity_default = None
            self._active_entity_default = None

            add_more = user_input.get(FIELD_ADD_ANOTHER, False)
            if add_more or self._entity_defaults_queue:
                return self._show_entity_form()

            if not self._controlled_entities:
                self._errors = {"base": "no_controlled_entities"}
                return self._show_entity_form(user_input)

            data = {
                **self._base_data,
                CONF_CONTROLLED_ENTITIES: self._controlled_entities,
            }
            return self.async_create_entry(
                title=self._base_data[CONF_ROOM_NAME],
                data=data,
            )

        return self._show_entity_form()

    def _extract_entity_config(self, user_input):
        try:
            entity_id = cv.entity_id(user_input[CONF_ENTITY_ID])
        except vol.Invalid:
            self._errors = {CONF_ENTITY_ID: "invalid_entity"}
            return None

        detected_service = user_input[CONF_PRESENCE_DETECTED_SERVICE]
        cleared_service = user_input[CONF_PRESENCE_CLEARED_SERVICE]
        detected_state = user_input[CONF_PRESENCE_DETECTED_STATE]
        cleared_state = user_input[CONF_PRESENCE_CLEARED_STATE]
        active_defaults = self._active_entity_default or {}

        entity_config = {
            CONF_ENTITY_ID: entity_id,
            CONF_PRESENCE_DETECTED_SERVICE: detected_service,
            CONF_PRESENCE_CLEARED_SERVICE: cleared_service,
            CONF_PRESENCE_DETECTED_STATE: detected_state,
            CONF_PRESENCE_CLEARED_STATE: cleared_state,
            CONF_RESPECTS_PRESENCE_ALLOWED: user_input[CONF_RESPECTS_PRESENCE_ALLOWED],
            CONF_DISABLE_ON_EXTERNAL_CONTROL: user_input[CONF_DISABLE_ON_EXTERNAL_CONTROL],
            CONF_INITIAL_PRESENCE_ALLOWED: active_defaults.get(
                CONF_INITIAL_PRESENCE_ALLOWED, DEFAULT_INITIAL_PRESENCE_ALLOWED
            ),
        }
        
        # Optional per-entity timeout (overrides global if set)
        entity_off_delay = user_input.get(CONF_ENTITY_OFF_DELAY)
        if entity_off_delay is not None:
            entity_config[CONF_ENTITY_OFF_DELAY] = entity_off_delay
        
        return entity_config

    def _entity_default(self):
        if self._pending_entity_default is not None:
            self._active_entity_default = self._pending_entity_default
            return self._pending_entity_default
        if self._entity_defaults_queue:
            self._pending_entity_default = self._entity_defaults_queue.pop(0)
            self._active_entity_default = self._pending_entity_default
            return self._pending_entity_default
        self._active_entity_default = {}
        return {}

    def _show_entity_form(self, user_input=None):
        defaults = self._entity_default()
        add_more_default = bool(self._entity_defaults_queue)

        schema_dict = {
            _required_field(CONF_ENTITY_ID, defaults.get(CONF_ENTITY_ID)): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=False)
            ),
            _required_field(
                CONF_PRESENCE_DETECTED_SERVICE,
                defaults.get(CONF_PRESENCE_DETECTED_SERVICE, DEFAULT_DETECTED_SERVICE),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            _required_field(
                CONF_PRESENCE_DETECTED_STATE,
                defaults.get(CONF_PRESENCE_DETECTED_STATE, DEFAULT_DETECTED_STATE),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            _required_field(
                CONF_PRESENCE_CLEARED_SERVICE,
                defaults.get(CONF_PRESENCE_CLEARED_SERVICE, DEFAULT_CLEARED_SERVICE),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            _required_field(
                CONF_PRESENCE_CLEARED_STATE,
                defaults.get(CONF_PRESENCE_CLEARED_STATE, DEFAULT_CLEARED_STATE),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_RESPECTS_PRESENCE_ALLOWED,
                default=defaults.get(
                    CONF_RESPECTS_PRESENCE_ALLOWED, DEFAULT_RESPECTS_PRESENCE_ALLOWED
                ),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_DISABLE_ON_EXTERNAL_CONTROL,
                default=defaults.get(
                    CONF_DISABLE_ON_EXTERNAL_CONTROL, DEFAULT_DISABLE_ON_EXTERNAL
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_ENTITY_OFF_DELAY,
                default=defaults.get(CONF_ENTITY_OFF_DELAY),
            ): vol.All(vol.Coerce(int), vol.Range(min=0)),
            vol.Optional(FIELD_SKIP_ENTITY, default=False): selector.BooleanSelector(),
            vol.Optional(FIELD_ADD_ANOTHER, default=add_more_default): selector.BooleanSelector(),
        }

        return self.async_show_form(
            step_id=STEP_CONTROLLED_ENTITIES,
            data_schema=vol.Schema(schema_dict),
            errors=self._errors,
            description_placeholders={
                "added": str(len(self._controlled_entities)),
                "remaining_defaults": str(len(self._entity_defaults_queue)),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PresenceBasedLightingOptionsFlowHandler(config_entry)


class PresenceBasedLightingOptionsFlowHandler(config_entries.OptionsFlow):
    """Config flow options handler for presence_based_lighting."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry
        self._errors: dict[str, str] = {}
        self._base_data = {
            CONF_ROOM_NAME: config_entry.data[CONF_ROOM_NAME],
            CONF_PRESENCE_SENSORS: config_entry.data.get(CONF_PRESENCE_SENSORS, []),
            CONF_OFF_DELAY: config_entry.data.get(CONF_OFF_DELAY, DEFAULT_OFF_DELAY),
        }
        self._controlled_entities: list[dict] = []
        self._entity_defaults_queue = copy.deepcopy(
            config_entry.data.get(CONF_CONTROLLED_ENTITIES, [])
        )
        self._pending_entity_default: dict | None = None
        self._active_entity_default = None

    async def async_step_init(self, user_input=None):
        """Manage shared configuration values."""
        self._errors = {}

        if user_input is not None:
            self._base_data[CONF_PRESENCE_SENSORS] = user_input[CONF_PRESENCE_SENSORS]
            self._base_data[CONF_OFF_DELAY] = user_input[CONF_OFF_DELAY]
            self._entity_defaults_queue = copy.deepcopy(
                self.config_entry.data.get(CONF_CONTROLLED_ENTITIES, [])
            )
            self._pending_entity_default = None
            self._controlled_entities = []
            return await self.async_step_entities()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PRESENCE_SENSORS,
                        default=self._base_data[CONF_PRESENCE_SENSORS],
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="binary_sensor",
                            device_class="occupancy",
                            multiple=True,
                        )
                    ),
                    vol.Required(
                        CONF_OFF_DELAY,
                        default=self._base_data[CONF_OFF_DELAY],
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
                }
            ),
            description_placeholders={"room": self._base_data[CONF_ROOM_NAME]},
        )

    async def async_step_entities(self, user_input=None):
        """Reuse entity step for options flow."""
        if user_input is not None:
            self._errors = {}
            skip = user_input.get(FIELD_SKIP_ENTITY, False)

            if not skip:
                entity_config = self._extract_entity_config(user_input)
                if entity_config is None:
                    return self._show_entity_form(user_input)
                self._controlled_entities.append(entity_config)

            if self._pending_entity_default is not None:
                self._pending_entity_default = None
            self._active_entity_default = None

            add_more = user_input.get(FIELD_ADD_ANOTHER, False)
            if add_more or self._entity_defaults_queue:
                return self._show_entity_form()

            if not self._controlled_entities:
                self._errors = {"base": "no_controlled_entities"}
                return self._show_entity_form(user_input)

            new_data = {
                **self.config_entry.data,
                CONF_PRESENCE_SENSORS: self._base_data[CONF_PRESENCE_SENSORS],
                CONF_OFF_DELAY: self._base_data[CONF_OFF_DELAY],
                CONF_CONTROLLED_ENTITIES: self._controlled_entities,
            }
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=new_data,
            )
            return self.async_create_entry(title="", data={})

        return self._show_entity_form()

    def _extract_entity_config(self, user_input):
        try:
            entity_id = cv.entity_id(user_input[CONF_ENTITY_ID])
        except vol.Invalid:
            self._errors = {CONF_ENTITY_ID: "invalid_entity"}
            return None

        detected_service = user_input[CONF_PRESENCE_DETECTED_SERVICE]
        cleared_service = user_input[CONF_PRESENCE_CLEARED_SERVICE]
        detected_state = user_input[CONF_PRESENCE_DETECTED_STATE]
        cleared_state = user_input[CONF_PRESENCE_CLEARED_STATE]
        active_defaults = self._active_entity_default or {}

        entity_config = {
            CONF_ENTITY_ID: entity_id,
            CONF_PRESENCE_DETECTED_SERVICE: detected_service,
            CONF_PRESENCE_CLEARED_SERVICE: cleared_service,
            CONF_PRESENCE_DETECTED_STATE: detected_state,
            CONF_PRESENCE_CLEARED_STATE: cleared_state,
            CONF_RESPECTS_PRESENCE_ALLOWED: user_input[CONF_RESPECTS_PRESENCE_ALLOWED],
            CONF_DISABLE_ON_EXTERNAL_CONTROL: user_input[CONF_DISABLE_ON_EXTERNAL_CONTROL],
            CONF_INITIAL_PRESENCE_ALLOWED: active_defaults.get(
                CONF_INITIAL_PRESENCE_ALLOWED, DEFAULT_INITIAL_PRESENCE_ALLOWED
            ),
        }
        
        # Optional per-entity timeout (overrides global if set)
        entity_off_delay = user_input.get(CONF_ENTITY_OFF_DELAY)
        if entity_off_delay is not None:
            entity_config[CONF_ENTITY_OFF_DELAY] = entity_off_delay
        
        return entity_config

    def _entity_default(self):
        if self._pending_entity_default is not None:
            self._active_entity_default = self._pending_entity_default
            return self._pending_entity_default
        if self._entity_defaults_queue:
            self._pending_entity_default = self._entity_defaults_queue.pop(0)
            self._active_entity_default = self._pending_entity_default
            return self._pending_entity_default
        self._active_entity_default = {}
        return {}

    def _show_entity_form(self, user_input=None):
        defaults = self._entity_default()
        add_more_default = bool(self._entity_defaults_queue)

        schema_dict = {
            _required_field(CONF_ENTITY_ID, defaults.get(CONF_ENTITY_ID)): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=False)
            ),
            _required_field(
                CONF_PRESENCE_DETECTED_SERVICE,
                defaults.get(CONF_PRESENCE_DETECTED_SERVICE, DEFAULT_DETECTED_SERVICE),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            _required_field(
                CONF_PRESENCE_DETECTED_STATE,
                defaults.get(CONF_PRESENCE_DETECTED_STATE, DEFAULT_DETECTED_STATE),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            _required_field(
                CONF_PRESENCE_CLEARED_SERVICE,
                defaults.get(CONF_PRESENCE_CLEARED_SERVICE, DEFAULT_CLEARED_SERVICE),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            _required_field(
                CONF_PRESENCE_CLEARED_STATE,
                defaults.get(CONF_PRESENCE_CLEARED_STATE, DEFAULT_CLEARED_STATE),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_RESPECTS_PRESENCE_ALLOWED,
                default=defaults.get(
                    CONF_RESPECTS_PRESENCE_ALLOWED, DEFAULT_RESPECTS_PRESENCE_ALLOWED
                ),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_DISABLE_ON_EXTERNAL_CONTROL,
                default=defaults.get(
                    CONF_DISABLE_ON_EXTERNAL_CONTROL, DEFAULT_DISABLE_ON_EXTERNAL
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_ENTITY_OFF_DELAY,
                default=defaults.get(CONF_ENTITY_OFF_DELAY),
            ): vol.All(vol.Coerce(int), vol.Range(min=0)),
            vol.Optional(FIELD_SKIP_ENTITY, default=False): selector.BooleanSelector(),
            vol.Optional(FIELD_ADD_ANOTHER, default=add_more_default): selector.BooleanSelector(),
        }

        return self.async_show_form(
            step_id=STEP_CONTROLLED_ENTITIES,
            data_schema=vol.Schema(schema_dict),
            errors=self._errors,
            description_placeholders={
                "added": str(len(self._controlled_entities)),
                "remaining_defaults": str(len(self._entity_defaults_queue)),
            },
        )
