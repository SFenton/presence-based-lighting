"""Adds config flow for Presence Based Lighting."""
from __future__ import annotations

import copy
import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback, HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import selector

_LOGGER = logging.getLogger(__name__)

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
	NO_ACTION,
)

STEP_USER = "user"
STEP_SELECT_ENTITY = "select_entity"
STEP_CONFIGURE_ENTITY = "configure_entity"
STEP_CONTROLLED_ENTITIES = "controlled_entities"
STEP_MANAGE_ENTITIES = "manage_entities"
FIELD_ADD_ANOTHER = "add_another"
FIELD_SKIP_ENTITY = "skip_entity"
FIELD_MANAGE_ACTION = "entity_action"

ACTION_ADD_ENTITY = "add"
ACTION_FINISH = "finish"

# Common services by domain
DOMAIN_SERVICES = {
	"light": ["turn_on", "turn_off", "toggle"],
	"switch": ["turn_on", "turn_off", "toggle"],
	"fan": ["turn_on", "turn_off", "toggle"],
	"climate": ["turn_on", "turn_off", "set_hvac_mode"],
	"media_player": ["turn_on", "turn_off", "media_play", "media_pause"],
	"cover": ["open_cover", "close_cover", "stop_cover"],
	"lock": ["lock", "unlock"],
	"scene": ["turn_on"],
	"script": ["turn_on"],
}

# Common states by domain
DOMAIN_STATES = {
	"light": ["on", "off"],
	"switch": ["on", "off"],
	"fan": ["on", "off"],
	"climate": ["heat", "cool", "heat_cool", "auto", "off"],
	"media_player": ["playing", "paused", "idle", "off"],
	"cover": ["open", "closed", "opening", "closing"],
	"lock": ["locked", "unlocked"],
	"scene": ["on"],
	"script": ["on"],
}


def _required_field(key: str, default=None):
	"""Helper to create required field with optional default."""
	if default is None:
		return vol.Required(key)
	return vol.Required(key, default=default)


def _get_entity_domain(entity_id: str) -> str:
	"""Extract domain from entity_id."""
	return entity_id.split(".")[0] if "." in entity_id else ""


def _get_services_for_entity(hass: HomeAssistant, entity_id: str) -> list[selector.SelectOptionDict]:
	"""Get available services for an entity with NO_ACTION option."""
	domain = _get_entity_domain(entity_id)
	
	# Start with NO_ACTION
	options = [
		selector.SelectOptionDict(value=NO_ACTION, label="No Action")
	]
	
	# Get services from domain mapping
	if domain in DOMAIN_SERVICES:
		for service in DOMAIN_SERVICES[domain]:
			options.append(
				selector.SelectOptionDict(
					value=service,
					label=service.replace("_", " ").title()
				)
			)
	
	# Try to get additional services from hass.services
	if hass and hass.services.has_service(domain, "turn_on"):
		# Domain has services registered, we can use our predefined list
		pass
	
	return options


def _get_states_for_entity(entity_id: str) -> list[selector.SelectOptionDict]:
	"""Get available states for an entity."""
	domain = _get_entity_domain(entity_id)
	
	options = []
	if domain in DOMAIN_STATES:
		for state in DOMAIN_STATES[domain]:
			options.append(
				selector.SelectOptionDict(
					value=state,
					label=state.replace("_", " ").title()
				)
			)
	
	# If no predefined states, provide common ones
	if not options:
		options = [
			selector.SelectOptionDict(value="on", label="On"),
			selector.SelectOptionDict(value="off", label="Off"),
		]
	
	return options


def _get_entity_name(hass: HomeAssistant, entity_id: str) -> str:
	"""Get friendly name for entity."""
	if hass and (state := hass.states.get(entity_id)):
		return state.attributes.get("friendly_name", entity_id)
	return entity_id


class PresenceBasedLightingFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
	"""Config flow for presence_based_lighting."""

	VERSION = 2

	def __init__(self):
		"""Initialize."""
		self._errors: dict[str, str] = {}
		self._base_data: dict = {}
		self._controlled_entities: list[dict] = []
		self._selected_entity_id: str | None = None
		self._current_entity_config: dict = {}

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
			self._selected_entity_id = None
			return await self.async_step_select_entity()

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

	async def async_step_select_entity(self, user_input=None):
		"""Step to select which entity to control."""
		self._errors = {}

		if user_input is not None:
			try:
				entity_id = cv.entity_id(user_input[CONF_ENTITY_ID])
				self._selected_entity_id = entity_id
				self._current_entity_config = {CONF_ENTITY_ID: entity_id}
				return await self.async_step_configure_entity()
			except vol.Invalid:
				self._errors = {CONF_ENTITY_ID: "invalid_entity"}

		return self.async_show_form(
			step_id=STEP_SELECT_ENTITY,
			data_schema=vol.Schema(
				{
					vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
						selector.EntitySelectorConfig(multiple=False)
					),
				}
			),
			errors=self._errors,
			description_placeholders={
				"added": str(len(self._controlled_entities)),
			},
		)

	async def async_step_configure_entity(self, user_input=None):
		"""Step to configure the selected entity's services and behavior."""
		self._errors = {}
		editing = self._editing_index is not None

		if user_input is not None:
			updated_config = {
				CONF_ENTITY_ID: self._selected_entity_id,
				CONF_PRESENCE_DETECTED_SERVICE: user_input[CONF_PRESENCE_DETECTED_SERVICE],
				CONF_PRESENCE_DETECTED_STATE: user_input[CONF_PRESENCE_DETECTED_STATE],
				CONF_PRESENCE_CLEARED_SERVICE: user_input[CONF_PRESENCE_CLEARED_SERVICE],
				CONF_PRESENCE_CLEARED_STATE: user_input[CONF_PRESENCE_CLEARED_STATE],
				CONF_RESPECTS_PRESENCE_ALLOWED: user_input[CONF_RESPECTS_PRESENCE_ALLOWED],
				CONF_DISABLE_ON_EXTERNAL_CONTROL: user_input[CONF_DISABLE_ON_EXTERNAL_CONTROL],
				CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
			}

			entity_off_delay = user_input.get(CONF_ENTITY_OFF_DELAY)
			if entity_off_delay is not None:
				updated_config[CONF_ENTITY_OFF_DELAY] = entity_off_delay
			elif CONF_ENTITY_OFF_DELAY in self._current_entity_config:
				updated_config.pop(CONF_ENTITY_OFF_DELAY, None)

			if editing and self._editing_index is not None:
				self._controlled_entities[self._editing_index] = updated_config
			else:
				self._controlled_entities.append(updated_config)

			self._current_entity_config = {}
			self._selected_entity_id = None
			self._editing_index = None
			return await self.async_step_manage_entities()

		entity_id = self._selected_entity_id
		entity_name = _get_entity_name(self.hass, entity_id)
		service_options = _get_services_for_entity(self.hass, entity_id)
		state_options = _get_states_for_entity(entity_id)
		defaults = {
			CONF_PRESENCE_DETECTED_SERVICE: self._current_entity_config.get(
				CONF_PRESENCE_DETECTED_SERVICE, DEFAULT_DETECTED_SERVICE
			),
			CONF_PRESENCE_DETECTED_STATE: self._current_entity_config.get(
				CONF_PRESENCE_DETECTED_STATE, DEFAULT_DETECTED_STATE
			),
			CONF_PRESENCE_CLEARED_SERVICE: self._current_entity_config.get(
				CONF_PRESENCE_CLEARED_SERVICE, DEFAULT_CLEARED_SERVICE
			),
			CONF_PRESENCE_CLEARED_STATE: self._current_entity_config.get(
				CONF_PRESENCE_CLEARED_STATE, DEFAULT_CLEARED_STATE
			),
			CONF_RESPECTS_PRESENCE_ALLOWED: self._current_entity_config.get(
				CONF_RESPECTS_PRESENCE_ALLOWED, DEFAULT_RESPECTS_PRESENCE_ALLOWED
			),
			CONF_DISABLE_ON_EXTERNAL_CONTROL: self._current_entity_config.get(
				CONF_DISABLE_ON_EXTERNAL_CONTROL, DEFAULT_DISABLE_ON_EXTERNAL
			),
		}
		entity_delay_default = self._current_entity_config.get(CONF_ENTITY_OFF_DELAY)
		delay_field = vol.Optional(CONF_ENTITY_OFF_DELAY)
		if entity_delay_default is not None:
			delay_field = vol.Optional(CONF_ENTITY_OFF_DELAY, default=entity_delay_default)

		return self.async_show_form(
			step_id=STEP_CONFIGURE_ENTITY,
			data_schema=vol.Schema(
				{
					vol.Required(
						CONF_PRESENCE_DETECTED_SERVICE,
						default=defaults[CONF_PRESENCE_DETECTED_SERVICE],
					): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=service_options,
							mode=selector.SelectSelectorMode.DROPDOWN,
						)
					),
					vol.Required(
						CONF_PRESENCE_DETECTED_STATE,
						default=defaults[CONF_PRESENCE_DETECTED_STATE],
					): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=state_options,
							mode=selector.SelectSelectorMode.DROPDOWN,
							custom_value=True,
						)
					),
					vol.Required(
						CONF_PRESENCE_CLEARED_SERVICE,
						default=defaults[CONF_PRESENCE_CLEARED_SERVICE],
					): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=service_options,
							mode=selector.SelectSelectorMode.DROPDOWN,
						)
					),
					vol.Required(
						CONF_PRESENCE_CLEARED_STATE,
						default=defaults[CONF_PRESENCE_CLEARED_STATE],
					): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=state_options,
							mode=selector.SelectSelectorMode.DROPDOWN,
							custom_value=True,
						)
					),
					vol.Required(
						CONF_RESPECTS_PRESENCE_ALLOWED,
						default=defaults[CONF_RESPECTS_PRESENCE_ALLOWED],
					): selector.BooleanSelector(),
					vol.Required(
						CONF_DISABLE_ON_EXTERNAL_CONTROL,
						default=defaults[CONF_DISABLE_ON_EXTERNAL_CONTROL],
					): selector.BooleanSelector(),
					delay_field: vol.All(vol.Coerce(int), vol.Range(min=0)),
				}
			),
			errors=self._errors,
			description_placeholders={
				"entity_name": entity_name,
			},
		)

	async def async_step_add_another(self, user_input=None):
		"""Ask if user wants to add another entity."""
		if user_input is not None:
			if user_input.get("add_another", False):
				return await self.async_step_select_entity()
			
			# User is done adding entities
			if not self._controlled_entities:
				self._errors = {"base": "no_controlled_entities"}
				return await self.async_step_select_entity()

			data = {
				**self._base_data,
				CONF_CONTROLLED_ENTITIES: self._controlled_entities,
			}
			return self.async_create_entry(
				title=self._base_data[CONF_ROOM_NAME],
				data=data,
			)

		return self.async_show_form(
			step_id="add_another",
			data_schema=vol.Schema(
				{
					vol.Required("add_another", default=False): selector.BooleanSelector(),
				}
			),
			description_placeholders={
				"added": str(len(self._controlled_entities)),
			},
		)

	@staticmethod
	@callback
	def async_get_options_flow(config_entry):
		"""Get the options flow for this handler."""
		return PresenceBasedLightingOptionsFlowHandler(config_entry)


class PresenceBasedLightingOptionsFlowHandler(config_entries.OptionsFlow):
	"""Config flow options handler for presence_based_lighting."""

	def __init__(self, config_entry):
		"""Initialize options flow.
		
		Note: config_entry parameter is for test compatibility.
		In production, self.config_entry is automatically set by OptionsFlow.
		"""
		_LOGGER.debug("OptionsFlow.__init__ starting")
		self._errors: dict[str, str] = {}
		# Store config_entry for test environment (where it's passed as parameter)
		# In production HA, self.config_entry is automatically available as a property
		# Check for private attribute to avoid triggering property during init
		if not hasattr(self, '_config_entry'):
			self._config_entry = config_entry
		
		_LOGGER.debug("Loading config_entry.data: %s", config_entry.data)
		self._base_data = {
			CONF_ROOM_NAME: config_entry.data[CONF_ROOM_NAME],
			CONF_PRESENCE_SENSORS: config_entry.data.get(CONF_PRESENCE_SENSORS, []),
			CONF_OFF_DELAY: config_entry.data.get(CONF_OFF_DELAY, DEFAULT_OFF_DELAY),
		}
		# Load existing entities from config entry
		existing_entities = config_entry.data.get(CONF_CONTROLLED_ENTITIES, [])
		_LOGGER.debug("Loading existing entities: %s", existing_entities)
		self._controlled_entities: list[dict] = list(existing_entities)
		self._selected_entity_id: str | None = None
		self._current_entity_config: dict = {}
		self._editing_index: int | None = None
		_LOGGER.debug("OptionsFlow.__init__ complete. Loaded %d entities", len(self._controlled_entities))
	
	@property
	def config_entry(self):
		"""Return config entry (for test compatibility)."""
		# In tests, _config_entry is set in __init__
		# In production HA, super().config_entry is automatically available
		if hasattr(self, '_config_entry'):
			return self._config_entry
		return super().config_entry

	async def _finalize_and_reload(self):
		"""Persist options changes back to the config entry."""
		_LOGGER.debug("Finalizing options update for %s", self.config_entry.entry_id)
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

	async def async_step_manage_entities(self, user_input=None):
		"""Display and manage existing controlled entities."""
		self._errors = {}
		if user_input is not None:
			action = user_input.get(FIELD_MANAGE_ACTION)
			if action == ACTION_ADD_ENTITY:
				self._editing_index = None
				self._selected_entity_id = None
				self._current_entity_config = {}
				return await self.async_step_select_entity()
			if action == ACTION_FINISH:
				if not self._controlled_entities:
					self._errors = {"base": "no_controlled_entities"}
				else:
					return await self._finalize_and_reload()
			if isinstance(action, str) and action.startswith("edit:"):
				idx = int(action.split(":", 1)[1])
				if 0 <= idx < len(self._controlled_entities):
					self._editing_index = idx
					self._current_entity_config = copy.deepcopy(self._controlled_entities[idx])
					self._selected_entity_id = self._current_entity_config[CONF_ENTITY_ID]
					return await self.async_step_configure_entity()
			if isinstance(action, str) and action.startswith("delete:"):
				idx = int(action.split(":", 1)[1])
				if 0 <= idx < len(self._controlled_entities):
					removed = self._controlled_entities.pop(idx)
					_LOGGER.debug("Removed entity from options: %s", removed)
					return await self.async_step_manage_entities()

		options = []
		for idx, entity in enumerate(self._controlled_entities):
			label = self._format_entity_label(entity)
			options.append(
				selector.SelectOptionDict(value=f"edit:{idx}", label=f"Edit {label}")
			)
			options.append(
				selector.SelectOptionDict(value=f"delete:{idx}", label=f"Delete {label}")
			)

		options.append(
			selector.SelectOptionDict(value=ACTION_ADD_ENTITY, label="Add Entity")
		)
		options.append(
			selector.SelectOptionDict(value=ACTION_FINISH, label="Save & Reload")
		)

		description_lines = self._entity_summary_lines()
		description = "\n".join(description_lines) if description_lines else "No entities configured yet."

		return self.async_show_form(
			step_id=STEP_MANAGE_ENTITIES,
			data_schema=vol.Schema(
				{
					vol.Required(FIELD_MANAGE_ACTION): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=options,
							mode=selector.SelectSelectorMode.LIST,
						)
					)
				}
			),
			description_placeholders={
				"room": self._base_data[CONF_ROOM_NAME],
				"entity_list": description,
			},
			errors=self._errors,
		)

	def _format_entity_label(self, entity: dict) -> str:
		entity_id = entity.get(CONF_ENTITY_ID, "")
		friendly = _get_entity_name(self.hass, entity_id)
		return f"{friendly} ({entity_id})"

	def _entity_summary_lines(self) -> list[str]:
		lines: list[str] = []
		for idx, entity in enumerate(self._controlled_entities, start=1):
			entity_id = entity.get(CONF_ENTITY_ID, "")
			friendly = _get_entity_name(self.hass, entity_id)
			detected_service = entity.get(CONF_PRESENCE_DETECTED_SERVICE, DEFAULT_DETECTED_SERVICE)
			cleared_service = entity.get(CONF_PRESENCE_CLEARED_SERVICE, DEFAULT_CLEARED_SERVICE)
			lines.append(
				f"{idx}. {friendly} ({entity_id})\n   ↳ Detected: {detected_service}, Cleared: {cleared_service}"
			)
		return lines

	async def async_step_init(self, user_input=None):
		"""Manage shared configuration values (OptionsFlow)."""
		"""Manage shared configuration values."""
		_LOGGER.debug("async_step_init called with user_input: %s", user_input)
		self._errors = {}

		if user_input is not None:
			_LOGGER.debug("Processing user input, updating base_data")
			self._base_data[CONF_PRESENCE_SENSORS] = user_input[CONF_PRESENCE_SENSORS]
			self._base_data[CONF_OFF_DELAY] = user_input[CONF_OFF_DELAY]
			self._selected_entity_id = None
			_LOGGER.debug(
				"Transitioning to manage_entities step. Current entities: %d",
				len(self._controlled_entities),
			)
			return await self.async_step_manage_entities()

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

	async def async_step_select_entity(self, user_input=None):
		"""Step to select which entity to control."""
		self._errors = {}

		if user_input is not None:
			try:
				entity_id = cv.entity_id(user_input[CONF_ENTITY_ID])
				self._selected_entity_id = entity_id
				self._current_entity_config = {CONF_ENTITY_ID: entity_id}
				return await self.async_step_configure_entity()
			except vol.Invalid:
				self._errors = {CONF_ENTITY_ID: "invalid_entity"}

		return self.async_show_form(
			step_id=STEP_SELECT_ENTITY,
			data_schema=vol.Schema(
				{
					vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
						selector.EntitySelectorConfig(multiple=False)
					),
				}
			),
			errors=self._errors,
			description_placeholders={
				"added": str(len(self._controlled_entities)),
			},
		)

	async def async_step_configure_entity(self, user_input=None):
		"""Step to configure the selected entity's services and behavior."""
		self._errors = {}
		editing = self._editing_index is not None

		if user_input is not None:
			updated_config = {
				CONF_ENTITY_ID: self._selected_entity_id,
				CONF_PRESENCE_DETECTED_SERVICE: user_input[CONF_PRESENCE_DETECTED_SERVICE],
				CONF_PRESENCE_DETECTED_STATE: user_input[CONF_PRESENCE_DETECTED_STATE],
				CONF_PRESENCE_CLEARED_SERVICE: user_input[CONF_PRESENCE_CLEARED_SERVICE],
				CONF_PRESENCE_CLEARED_STATE: user_input[CONF_PRESENCE_CLEARED_STATE],
				CONF_RESPECTS_PRESENCE_ALLOWED: user_input[CONF_RESPECTS_PRESENCE_ALLOWED],
				CONF_DISABLE_ON_EXTERNAL_CONTROL: user_input[CONF_DISABLE_ON_EXTERNAL_CONTROL],
				CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
			}

			entity_off_delay = user_input.get(CONF_ENTITY_OFF_DELAY)
			if entity_off_delay is not None:
				updated_config[CONF_ENTITY_OFF_DELAY] = entity_off_delay
			elif CONF_ENTITY_OFF_DELAY in self._current_entity_config:
				updated_config.pop(CONF_ENTITY_OFF_DELAY, None)

			if editing and self._editing_index is not None:
				self._controlled_entities[self._editing_index] = updated_config
			else:
				self._controlled_entities.append(updated_config)

			self._current_entity_config = {}
			self._selected_entity_id = None
			self._editing_index = None
			return await self.async_step_manage_entities()

		entity_id = self._selected_entity_id
		entity_name = _get_entity_name(self.hass, entity_id)
		service_options = _get_services_for_entity(self.hass, entity_id)
		state_options = _get_states_for_entity(entity_id)
		defaults = {
			CONF_PRESENCE_DETECTED_SERVICE: self._current_entity_config.get(
				CONF_PRESENCE_DETECTED_SERVICE, DEFAULT_DETECTED_SERVICE
			),
			CONF_PRESENCE_DETECTED_STATE: self._current_entity_config.get(
				CONF_PRESENCE_DETECTED_STATE, DEFAULT_DETECTED_STATE
			),
			CONF_PRESENCE_CLEARED_SERVICE: self._current_entity_config.get(
				CONF_PRESENCE_CLEARED_SERVICE, DEFAULT_CLEARED_SERVICE
			),
			CONF_PRESENCE_CLEARED_STATE: self._current_entity_config.get(
				CONF_PRESENCE_CLEARED_STATE, DEFAULT_CLEARED_STATE
			),
			CONF_RESPECTS_PRESENCE_ALLOWED: self._current_entity_config.get(
				CONF_RESPECTS_PRESENCE_ALLOWED, DEFAULT_RESPECTS_PRESENCE_ALLOWED
			),
			CONF_DISABLE_ON_EXTERNAL_CONTROL: self._current_entity_config.get(
				CONF_DISABLE_ON_EXTERNAL_CONTROL, DEFAULT_DISABLE_ON_EXTERNAL
			),
		}
		entity_delay_default = self._current_entity_config.get(CONF_ENTITY_OFF_DELAY)
		delay_field = vol.Optional(CONF_ENTITY_OFF_DELAY)
		if entity_delay_default is not None:
			delay_field = vol.Optional(CONF_ENTITY_OFF_DELAY, default=entity_delay_default)

		return self.async_show_form(
			step_id=STEP_CONFIGURE_ENTITY,
			data_schema=vol.Schema(
				{
					vol.Required(
						CONF_PRESENCE_DETECTED_SERVICE,
						default=defaults[CONF_PRESENCE_DETECTED_SERVICE],
					): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=service_options,
							mode=selector.SelectSelectorMode.DROPDOWN,
						)
					),
					vol.Required(
						CONF_PRESENCE_DETECTED_STATE,
						default=defaults[CONF_PRESENCE_DETECTED_STATE],
					): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=state_options,
							mode=selector.SelectSelectorMode.DROPDOWN,
							custom_value=True,
						)
					),
					vol.Required(
						CONF_PRESENCE_CLEARED_SERVICE,
						default=defaults[CONF_PRESENCE_CLEARED_SERVICE],
					): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=service_options,
							mode=selector.SelectSelectorMode.DROPDOWN,
						)
					),
					vol.Required(
						CONF_PRESENCE_CLEARED_STATE,
						default=defaults[CONF_PRESENCE_CLEARED_STATE],
					): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=state_options,
							mode=selector.SelectSelectorMode.DROPDOWN,
							custom_value=True,
						)
					),
					vol.Required(
						CONF_RESPECTS_PRESENCE_ALLOWED,
						default=defaults[CONF_RESPECTS_PRESENCE_ALLOWED],
					): selector.BooleanSelector(),
					vol.Required(
						CONF_DISABLE_ON_EXTERNAL_CONTROL,
						default=defaults[CONF_DISABLE_ON_EXTERNAL_CONTROL],
					): selector.BooleanSelector(),
					delay_field: vol.All(vol.Coerce(int), vol.Range(min=0)),
				}
			),
			errors=self._errors,
			description_placeholders={
				"entity_name": entity_name,
			},
		)

	async def async_step_add_another(self, user_input=None):
		"""Ask if user wants to add another entity."""
		_LOGGER.debug("async_step_add_another called with user_input: %s", user_input)
		if user_input is not None:
			if user_input.get("add_another", False):
				_LOGGER.debug("User wants to add another entity")
				return await self.async_step_select_entity()
			
			# User is done adding entities
			_LOGGER.debug("User finished adding entities. Total entities: %d", len(self._controlled_entities))
			if not self._controlled_entities:
				_LOGGER.warning("No controlled entities found, showing error")
				self._errors = {"base": "no_controlled_entities"}
				return await self.async_step_select_entity()

			_LOGGER.debug("Building new_data to update config entry")
			_LOGGER.debug("  config_entry.data: %s", self.config_entry.data)
			_LOGGER.debug("  _base_data: %s", self._base_data)
			_LOGGER.debug("  _controlled_entities: %s", self._controlled_entities)
			
			new_data = {
				**self.config_entry.data,
				CONF_PRESENCE_SENSORS: self._base_data[CONF_PRESENCE_SENSORS],
				CONF_OFF_DELAY: self._base_data[CONF_OFF_DELAY],
				CONF_CONTROLLED_ENTITIES: self._controlled_entities,
			}
			_LOGGER.debug("new_data constructed: %s", new_data)
			_LOGGER.debug("Calling async_update_entry...")
			self.hass.config_entries.async_update_entry(
				self.config_entry,
				data=new_data,
			)
			_LOGGER.debug("async_update_entry completed, creating entry")
			return self.async_create_entry(title="", data={})

		return self.async_show_form(
			step_id="add_another",
			data_schema=vol.Schema(
				{
					vol.Required("add_another", default=False): selector.BooleanSelector(),
				}
			),
			description_placeholders={
				"added": str(len(self._controlled_entities)),
			},
		)
