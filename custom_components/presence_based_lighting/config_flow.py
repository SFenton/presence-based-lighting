"""Adds config flow for Presence Based Lighting."""
from __future__ import annotations

import copy
import logging
from datetime import timedelta
from typing import NamedTuple

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback, HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.components.recorder import history
from homeassistant.helpers import selector, entity_registry as er
from homeassistant.util import dt as dt_util, slugify

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
STEP_MANAGE_ENTITIES = "manage_entities"
STEP_CHOOSE_EDIT_ENTITY = "choose_edit_entity"
STEP_DELETE_ENTITIES = "delete_entities"
FIELD_LANDING_ACTION = "landing_action"
FIELD_EDIT_ENTITY = "entity_to_edit"
FIELD_DELETE_ENTITIES = "entities_to_delete"
FIELD_PRESENCE_DETECTED_STATE_CUSTOM = "presence_detected_state_custom"
FIELD_PRESENCE_CLEARED_STATE_CUSTOM = "presence_cleared_state_custom"

ACTION_ADD_ENTITY = "add"
ACTION_NO_ACTION = "no_action"
ACTION_EDIT_ENTITY = "edit"
ACTION_DELETE_ENTITIES = "delete"

STATE_OPTION_CUSTOM = "__presence_based_lighting_custom_state__"
CUSTOM_LABEL = "Custom"
UI_CUSTOM_DETECTED_KEY = "_ui_detected_state_custom"
UI_CUSTOM_CLEARED_KEY = "_ui_cleared_state_custom"

RECORDER_LOOKBACK = timedelta(days=14)
RECORDER_STATE_LIMIT = 25


def _get_entity_domain(entity_id: str) -> str:
	"""Extract domain from entity_id."""
	return entity_id.split(".")[0] if "." in entity_id else ""


class ServiceOptionsUnavailable(Exception):
	"""Raised when no HA service metadata is available for an entity."""


async def _get_services_for_entity(hass: HomeAssistant, entity_id: str) -> list[selector.SelectOptionDict]:
	"""Build action dropdown options using HA service metadata."""
	if not hass or not getattr(hass, "services", None):
		raise ServiceOptionsUnavailable(f"No service registry available for {entity_id}")

	domain = _get_entity_domain(entity_id)
	options: list[selector.SelectOptionDict] = [
		selector.SelectOptionDict(value=NO_ACTION, label="No Action")
	]

	services_catalog: dict[str, dict] = {}
	services_metadata: dict[str, dict] = {}
	registry = hass.services

	get_services = getattr(registry, "async_services", None)
	if callable(get_services):
		try:
			services_catalog = get_services() or {}
		except Exception as err:  # pragma: no cover - HA internals
			_LOGGER.debug("Failed to enumerate services: %s", err)

	get_desc = getattr(registry, "async_get_all_descriptions", None)
	if callable(get_desc):
		try:
			services_metadata = await get_desc() or {}
		except Exception as err:  # pragma: no cover - HA internals
			_LOGGER.debug("Failed to fetch service descriptions: %s", err)

	available_services: set[str] = set()
	if domain in services_catalog:
		available_services.update(services_catalog[domain].keys())
	if domain in services_metadata:
		available_services.update(services_metadata[domain].keys())

	if not available_services:
		raise ServiceOptionsUnavailable(f"No services available for {entity_id}")

	metadata_for_domain = services_metadata.get(domain, {})
	for service_name in sorted(available_services):
		label = _format_action_option_label(service_name, metadata_for_domain.get(service_name))
		options.append(
			selector.SelectOptionDict(
				value=service_name,
				label=label,
			)
		)

	return options


def _format_action_option_label(service_name: str, metadata: dict | None) -> str:
	"""Format an action label showing icon, title, and description when available."""
	metadata = metadata or {}
	icon = metadata.get("icon")
	name = metadata.get("name") or service_name.replace("_", " ").title()
	description = metadata.get("description")
	parts: list[str] = []
	if icon:
		parts.append(f"[{icon}]")
	parts.append(name)
	label = " ".join(parts)
	if description:
		label = f"{label} – {description}"
	return label


def _get_entity_name(hass: HomeAssistant, entity_id: str) -> str:
	"""Get friendly name for entity."""
	if hass and (state := hass.states.get(entity_id)):
		return state.attributes.get("friendly_name", entity_id)
	return entity_id


def _presence_switch_unique_id(entry_id: str | None, entity_id: str | None) -> str | None:
	"""Build the unique_id used by per-entity presence switches."""
	if not entry_id or not entity_id or "." not in entity_id:
		return None
	object_id = entity_id.split(".", 1)[1]
	sanitized = slugify(object_id)
	if not sanitized:
		sanitized = object_id.replace(".", "_")
	return f"{entry_id}_{sanitized}_presence_allowed"


class StateOptionResult(NamedTuple):
	"""State dropdown metadata including provenance."""

	options: list[selector.SelectOptionDict]
	from_hass: bool
	ha_values: set[str]


class StateFieldDefaults(NamedTuple):
	"""State selector defaults and UI hints."""

	selector_default: str
	custom_default: str | None
	show_custom_input: bool


def _format_state_option_label(value: str) -> str:
	"""Build a human friendly label for a state value."""
	if not value:
		return "(empty)"
	pretty = value.replace("_", " ").replace("-", " ").strip()
	return pretty.title() if pretty else value


async def _async_get_history_states(
	hass: HomeAssistant | None,
	entity_id: str | None,
	*,
	lookback: timedelta = RECORDER_LOOKBACK,
	max_states: int = RECORDER_STATE_LIMIT,
) -> list[str]:
	"""Fetch prior recorder states for an entity, ordered by newest first."""
	if not hass or not entity_id:
		return []
	async_executor = getattr(hass, "async_add_executor_job", None)
	if async_executor is None:
		return []
	end_time = dt_util.utcnow()
	start_time = end_time - lookback

	def _fetch_history() -> list[str]:  # pragma: no cover - executed in executor
		try:
			history_dict = history.get_significant_states(
				hass,
				start_time,
				end_time=end_time,
				entity_ids=[entity_id],
				include_start_time_state=True,
				significant_changes_only=False,
				minimal_response=True,
			)
		except Exception as err:
			_LOGGER.debug("Recorder history lookup failed for %s: %s", entity_id, err)
			return []
		states = history_dict.get(entity_id, [])
		values: list[str] = []
		for state in states:
			value = getattr(state, "state", None)
			if isinstance(value, str) and value:
				values.append(value)
		return values

	try:
		executor_job = async_executor(_fetch_history)
	except Exception as err:
		_LOGGER.debug("Recorder executor scheduling failed for %s: %s", entity_id, err)
		return []
	if not hasattr(executor_job, "__await__"):
		return []
	try:
		history_values = await executor_job
	except Exception as err:
		_LOGGER.debug("Recorder executor job failed for %s: %s", entity_id, err)
		return []

	unique: list[str] = []
	for value in history_values:
		normalized = str(value).strip()
		if not normalized or normalized in unique:
			continue
		unique.append(normalized)
		if len(unique) >= max_states:
			break
	return unique



async def _build_state_option_dicts(
	hass: HomeAssistant | None,
	entity_id: str | None,
	ensure_values: list[str],
) -> StateOptionResult:
	"""Return state option metadata sourced from Home Assistant when available."""
	if not entity_id:
		entity_id = ""
	state_candidates: list[str] = []
	ha_candidates: list[str] = []
	state_obj = None
	if hass and entity_id:
		state_obj = hass.states.get(entity_id)
	if state_obj:
		attr_options = state_obj.attributes.get("options")
		if isinstance(attr_options, (list, tuple, set)):
			for option in attr_options:
				option_str = str(option)
				if option_str:
					state_candidates.append(option_str)
					ha_candidates.append(option_str)
		state_value = getattr(state_obj, "state", None)
		if isinstance(state_value, str) and state_value:
			state_candidates.append(state_value)
			ha_candidates.append(state_value)
	if hass and entity_id:
		history_states = await _async_get_history_states(hass, entity_id)
		for hist_state in history_states:
			state_candidates.append(hist_state)
			ha_candidates.append(hist_state)
	for required in ensure_values:
		if required:
			state_candidates.append(str(required))
	unique_states: list[str] = []
	for candidate in state_candidates:
		candidate = str(candidate).strip()
		if not candidate:
			continue
		if candidate not in unique_states:
			unique_states.append(candidate)
	options = [
		selector.SelectOptionDict(value=value, label=_format_state_option_label(value))
		for value in unique_states
	]
	return StateOptionResult(options=options, from_hass=bool(ha_candidates), ha_values=set(ha_candidates))


def _resolve_custom_state_selection(
	selection: str,
	custom_value: str | None,
	*,
	ui_state: dict[str, str | None],
	ui_key: str,
) -> tuple[str | None, bool]:
	"""Return normalized state value and whether a custom entry is missing."""
	selection = str(selection)
	if selection != STATE_OPTION_CUSTOM:
		ui_state.pop(ui_key, None)
		return selection, False
	custom_value = (custom_value or "").strip()
	ui_state[ui_key] = custom_value
	if not custom_value:
		return None, True
	return custom_value, False


def _state_field_defaults(
	stored_value: str,
	*,
	ha_values: set[str],
	allowed_defaults: set[str] | None = None,
	ui_state: dict[str, str | None],
	ui_key: str,
) -> StateFieldDefaults:
	"""Determine dropdown default and custom textbox default for a state field."""
	use_custom = ui_key in ui_state
	custom_default = ui_state.get(ui_key)
	allowed_defaults = allowed_defaults or set()
	if not use_custom and stored_value:
		if stored_value not in ha_values and stored_value not in allowed_defaults:
			use_custom = True
			custom_default = stored_value
	if use_custom:
		return StateFieldDefaults(STATE_OPTION_CUSTOM, custom_default or stored_value or "", True)
	return StateFieldDefaults(stored_value, None, False)


class _EntityManagementMixin:
	"""Shared helpers for presenting entity summaries in flows."""

	def _format_entity_label(self, entity: dict) -> str:
		entity_id = entity.get(CONF_ENTITY_ID, "")
		friendly = _get_entity_name(self.hass, entity_id)
		return f"{friendly} ({entity_id})" if entity_id else friendly

	def _entity_cards_description(self) -> str:
		"""Render a textual summary of configured entities."""
		entities = getattr(self, "_controlled_entities", [])
		if not entities:
			return "• No entities configured yet."

		cards: list[str] = []
		for idx, entity in enumerate(entities, start=1):
			entity_id = entity.get(CONF_ENTITY_ID, "")
			friendly = _get_entity_name(self.hass, entity_id)
			detected_service = entity.get(CONF_PRESENCE_DETECTED_SERVICE, DEFAULT_DETECTED_SERVICE)
			detected_state = entity.get(CONF_PRESENCE_DETECTED_STATE, DEFAULT_DETECTED_STATE)
			cleared_service = entity.get(CONF_PRESENCE_CLEARED_SERVICE, DEFAULT_CLEARED_SERVICE)
			cleared_state = entity.get(CONF_PRESENCE_CLEARED_STATE, DEFAULT_CLEARED_STATE)
			respects_toggle = entity.get(CONF_RESPECTS_PRESENCE_ALLOWED, DEFAULT_RESPECTS_PRESENCE_ALLOWED)
			disable_on_manual = entity.get(CONF_DISABLE_ON_EXTERNAL_CONTROL, DEFAULT_DISABLE_ON_EXTERNAL)
			entity_off_delay = entity.get(CONF_ENTITY_OFF_DELAY)

			lines = [
				f"{idx}. {friendly} ({entity_id})",
				f"   ↳ Detected: {detected_service} → {detected_state}",
				f"   ↳ Cleared: {cleared_service} → {cleared_state}",
			]
			if not respects_toggle:
				lines.append("   ↳ Presence toggle disabled")
			if disable_on_manual:
				lines.append("   ↳ Disables when manually controlled")
			if entity_off_delay is not None:
				lines.append(f"   ↳ Uses {entity_off_delay}s off delay")
			cards.append("\n".join(lines))

		return "\n\n".join(cards)


class PresenceBasedLightingFlowHandler(_EntityManagementMixin, config_entries.ConfigFlow, domain=DOMAIN):
	"""Config flow for presence_based_lighting."""

	VERSION = 2

	def __init__(self):
		"""Initialize."""
		self._errors: dict[str, str] = {}
		self._base_data: dict = {}
		self._controlled_entities: list[dict] = []
		self._selected_entity_id: str | None = None
		self._current_entity_config: dict = {}
		self._editing_index: int | None = None
		self._finalize_after_configure: bool = False
		self._custom_state_ui: dict[str, str | None] = {}

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
				self._custom_state_ui = {}
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

		entity_id = self._selected_entity_id
		entity_name = _get_entity_name(self.hass, entity_id)
		try:
			service_options = await _get_services_for_entity(self.hass, entity_id)
		except ServiceOptionsUnavailable:
			self._errors = {"base": "no_services_available"}
			return await self.async_step_select_entity()
		defaults = self._current_entity_config.copy()
		defaults.setdefault(CONF_PRESENCE_DETECTED_SERVICE, DEFAULT_DETECTED_SERVICE)
		defaults.setdefault(CONF_PRESENCE_DETECTED_STATE, DEFAULT_DETECTED_STATE)
		defaults.setdefault(CONF_PRESENCE_CLEARED_SERVICE, DEFAULT_CLEARED_SERVICE)
		defaults.setdefault(CONF_PRESENCE_CLEARED_STATE, DEFAULT_CLEARED_STATE)
		defaults.setdefault(CONF_RESPECTS_PRESENCE_ALLOWED, DEFAULT_RESPECTS_PRESENCE_ALLOWED)
		defaults.setdefault(CONF_DISABLE_ON_EXTERNAL_CONTROL, DEFAULT_DISABLE_ON_EXTERNAL)
		entity_delay_default = self._current_entity_config.get(CONF_ENTITY_OFF_DELAY)
		delay_field = vol.Optional(CONF_ENTITY_OFF_DELAY)
		if entity_delay_default is not None:
			delay_field = vol.Optional(CONF_ENTITY_OFF_DELAY, default=entity_delay_default)

		state_option_source = await _build_state_option_dicts(
			self.hass,
			entity_id,
			[
				defaults[CONF_PRESENCE_DETECTED_STATE],
				defaults[CONF_PRESENCE_CLEARED_STATE],
			],
		)
		use_dropdown = bool(state_option_source.from_hass and state_option_source.options)

		if user_input is not None:
			entity_off_delay = user_input.get(CONF_ENTITY_OFF_DELAY)
			resolved_detected_state = str(user_input[CONF_PRESENCE_DETECTED_STATE]).strip()
			resolved_cleared_state = str(user_input[CONF_PRESENCE_CLEARED_STATE]).strip()

			if use_dropdown:
				resolved_detected_state, detected_missing = _resolve_custom_state_selection(
					resolved_detected_state,
					user_input.get(FIELD_PRESENCE_DETECTED_STATE_CUSTOM),
					ui_state=self._custom_state_ui,
					ui_key=UI_CUSTOM_DETECTED_KEY,
				)
				resolved_cleared_state, cleared_missing = _resolve_custom_state_selection(
					resolved_cleared_state,
					user_input.get(FIELD_PRESENCE_CLEARED_STATE_CUSTOM),
					ui_state=self._custom_state_ui,
					ui_key=UI_CUSTOM_CLEARED_KEY,
				)
				if detected_missing:
					self._errors[FIELD_PRESENCE_DETECTED_STATE_CUSTOM] = "custom_state_required"
				if cleared_missing:
					self._errors[FIELD_PRESENCE_CLEARED_STATE_CUSTOM] = "custom_state_required"
			else:
				self._custom_state_ui.pop(UI_CUSTOM_DETECTED_KEY, None)
				self._custom_state_ui.pop(UI_CUSTOM_CLEARED_KEY, None)

			if not self._errors:
				updated_config = {
					CONF_ENTITY_ID: self._selected_entity_id,
					CONF_PRESENCE_DETECTED_SERVICE: user_input[CONF_PRESENCE_DETECTED_SERVICE],
					CONF_PRESENCE_DETECTED_STATE: resolved_detected_state,
					CONF_PRESENCE_CLEARED_SERVICE: user_input[CONF_PRESENCE_CLEARED_SERVICE],
					CONF_PRESENCE_CLEARED_STATE: resolved_cleared_state,
					CONF_RESPECTS_PRESENCE_ALLOWED: user_input[CONF_RESPECTS_PRESENCE_ALLOWED],
					CONF_DISABLE_ON_EXTERNAL_CONTROL: user_input[CONF_DISABLE_ON_EXTERNAL_CONTROL],
					CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
				}

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
				self._custom_state_ui = {}
				if getattr(self, "_finalize_after_configure", False) and hasattr(self, "_finalize_and_reload"):
					self._finalize_after_configure = False
					return await self._finalize_and_reload()
				return await self.async_step_manage_entities()

		if use_dropdown:
			state_options = list(state_option_source.options)
			state_options.append(selector.SelectOptionDict(value=STATE_OPTION_CUSTOM, label=CUSTOM_LABEL))
			detected_defaults = _state_field_defaults(
				defaults[CONF_PRESENCE_DETECTED_STATE],
				ha_values=state_option_source.ha_values,
				allowed_defaults={DEFAULT_DETECTED_STATE},
				ui_state=self._custom_state_ui,
				ui_key=UI_CUSTOM_DETECTED_KEY,
			)
			cleared_defaults = _state_field_defaults(
				defaults[CONF_PRESENCE_CLEARED_STATE],
				ha_values=state_option_source.ha_values,
				allowed_defaults={DEFAULT_CLEARED_STATE},
				ui_state=self._custom_state_ui,
				ui_key=UI_CUSTOM_CLEARED_KEY,
			)
			detected_state_field = selector.SelectSelector(
				selector.SelectSelectorConfig(
					options=state_options,
					mode=selector.SelectSelectorMode.DROPDOWN,
				)
			)
			cleared_state_field = selector.SelectSelector(
				selector.SelectSelectorConfig(
					options=state_options,
					mode=selector.SelectSelectorMode.DROPDOWN,
				)
			)
			detected_default = detected_defaults.selector_default
			cleared_default = cleared_defaults.selector_default
		else:
			detected_state_field = str
			cleared_state_field = str
			detected_defaults = StateFieldDefaults(defaults[CONF_PRESENCE_DETECTED_STATE], None, False)
			cleared_defaults = StateFieldDefaults(defaults[CONF_PRESENCE_CLEARED_STATE], None, False)
			detected_default = detected_defaults.selector_default
			cleared_default = cleared_defaults.selector_default

		schema_fields: dict = {
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
				default=detected_default,
			): detected_state_field,
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
				default=cleared_default,
			): cleared_state_field,
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

		if use_dropdown:
			if detected_defaults.show_custom_input:
				detected_custom_field = vol.Optional(FIELD_PRESENCE_DETECTED_STATE_CUSTOM)
				if detected_defaults.custom_default is not None:
					detected_custom_field = vol.Optional(
						FIELD_PRESENCE_DETECTED_STATE_CUSTOM,
						default=detected_defaults.custom_default,
					)
				schema_fields[detected_custom_field] = selector.TextSelector(
					selector.TextSelectorConfig(
						type=selector.TextSelectorType.TEXT,
						multiline=False,
					)
				)
			if cleared_defaults.show_custom_input:
				cleared_custom_field = vol.Optional(FIELD_PRESENCE_CLEARED_STATE_CUSTOM)
				if cleared_defaults.custom_default is not None:
					cleared_custom_field = vol.Optional(
						FIELD_PRESENCE_CLEARED_STATE_CUSTOM,
						default=cleared_defaults.custom_default,
					)
				schema_fields[cleared_custom_field] = selector.TextSelector(
					selector.TextSelectorConfig(
						type=selector.TextSelectorType.TEXT,
						multiline=False,
					)
				)

		return self.async_show_form(
			step_id=STEP_CONFIGURE_ENTITY,
			data_schema=vol.Schema(schema_fields),
			errors=self._errors,
			description_placeholders={
				"entity_name": entity_name,
			},
		)

	@staticmethod
	@callback
	def async_get_options_flow(config_entry):
		"""Get the options flow for this handler."""
		return PresenceBasedLightingOptionsFlowHandler(config_entry)

	def _create_entry_payload(self) -> dict:
		return {
			CONF_ROOM_NAME: self._base_data[CONF_ROOM_NAME],
			CONF_PRESENCE_SENSORS: self._base_data.get(CONF_PRESENCE_SENSORS, []),
			CONF_OFF_DELAY: self._base_data.get(CONF_OFF_DELAY, DEFAULT_OFF_DELAY),
			CONF_CONTROLLED_ENTITIES: self._controlled_entities,
		}

	async def async_step_manage_entities(self, user_input=None):
		"""Landing step for managing entities before creating the entry."""
		self._errors = {}
		if user_input is not None:
			action = user_input.get(FIELD_LANDING_ACTION)
			if action == ACTION_NO_ACTION:
				if not self._controlled_entities:
					self._errors = {"base": "no_controlled_entities"}
				else:
					return self.async_create_entry(
						title=self._base_data[CONF_ROOM_NAME],
						data=self._create_entry_payload(),
					)
			elif action == ACTION_ADD_ENTITY:
				self._selected_entity_id = None
				self._current_entity_config = {}
				self._editing_index = None
				return await self.async_step_select_entity()
			elif action == ACTION_EDIT_ENTITY:
				if not self._controlled_entities:
					self._errors = {"base": "no_controlled_entities"}
				else:
					return await self.async_step_choose_edit_entity()
			elif action == ACTION_DELETE_ENTITIES:
				if not self._controlled_entities:
					self._errors = {"base": "no_controlled_entities"}
				else:
					return await self.async_step_delete_entities()

		options = [
			selector.SelectOptionDict(value=ACTION_NO_ACTION, label="Submit pending changes"),
			selector.SelectOptionDict(value=ACTION_ADD_ENTITY, label="Add entity"),
			selector.SelectOptionDict(value=ACTION_EDIT_ENTITY, label="Edit an entity"),
			selector.SelectOptionDict(value=ACTION_DELETE_ENTITIES, label="Delete entities"),
		]

		return self.async_show_form(
			step_id=STEP_MANAGE_ENTITIES,
			data_schema=vol.Schema(
				{
					vol.Required(FIELD_LANDING_ACTION, default=ACTION_NO_ACTION): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=options,
							mode=selector.SelectSelectorMode.DROPDOWN,
						)
					)
				}
			),
			description_placeholders={
				"room": self._base_data.get(CONF_ROOM_NAME, ""),
				"entity_cards": self._entity_cards_description(),
				"entity_count": str(len(self._controlled_entities)),
			},
			errors=self._errors,
		)

	async def async_step_choose_edit_entity(self, user_input=None):
		"""Allow the user to pick which entity to edit during setup."""
		self._errors = {}
		if not self._controlled_entities:
			self._errors = {"base": "no_controlled_entities"}
			return await self.async_step_manage_entities()

		if user_input is not None:
			selection = user_input.get(FIELD_EDIT_ENTITY)
			try:
				idx = int(selection)
			except (TypeError, ValueError):
				self._errors = {FIELD_EDIT_ENTITY: "invalid_entity"}
			else:
				if 0 <= idx < len(self._controlled_entities):
					self._editing_index = idx
					self._current_entity_config = copy.deepcopy(self._controlled_entities[idx])
					self._selected_entity_id = self._current_entity_config[CONF_ENTITY_ID]
					self._custom_state_ui = {}
					return await self.async_step_configure_entity()
				self._errors = {FIELD_EDIT_ENTITY: "invalid_entity"}

		options: list[selector.SelectOptionDict] = [
			selector.SelectOptionDict(value=str(idx), label=self._format_entity_label(entity))
			for idx, entity in enumerate(self._controlled_entities)
		]

		return self.async_show_form(
			step_id=STEP_CHOOSE_EDIT_ENTITY,
			data_schema=vol.Schema(
				{
					vol.Required(FIELD_EDIT_ENTITY): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=options,
							mode=selector.SelectSelectorMode.DROPDOWN,
						)
					)
				}
			),
			description_placeholders={
				"entity_cards": self._entity_cards_description(),
				"entity_count": str(len(self._controlled_entities)),
			},
			errors=self._errors,
		)

	async def async_step_delete_entities(self, user_input=None):
		"""Allow deleting entities before finishing setup."""
		self._errors = {}
		if not self._controlled_entities:
			self._errors = {"base": "no_controlled_entities"}
			return await self.async_step_manage_entities()

		if user_input is not None:
			selected = user_input.get(FIELD_DELETE_ENTITIES, []) or []
			if not selected:
				self._errors = {FIELD_DELETE_ENTITIES: "select_entities_to_delete"}
			else:
				indices: list[int] = []
				for raw in selected:
					try:
						indices.append(int(raw))
					except (TypeError, ValueError):
						continue
				if indices:
					for idx in sorted(set(indices), reverse=True):
						if 0 <= idx < len(self._controlled_entities):
							self._controlled_entities.pop(idx)
					return await self.async_step_manage_entities()
				self._errors = {FIELD_DELETE_ENTITIES: "select_entities_to_delete"}

		options = [
			selector.SelectOptionDict(value=str(idx), label=self._format_entity_label(entity))
			for idx, entity in enumerate(self._controlled_entities)
		]

		return self.async_show_form(
			step_id=STEP_DELETE_ENTITIES,
			data_schema=vol.Schema(
				{
					vol.Required(FIELD_DELETE_ENTITIES): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=options,
							multiple=True,
							mode=selector.SelectSelectorMode.LIST,
						)
					)
				}
			),
			description_placeholders={
				"entity_cards": self._entity_cards_description(),
				"entity_count": str(len(self._controlled_entities)),
			},
			errors=self._errors,
		)


class PresenceBasedLightingOptionsFlowHandler(_EntityManagementMixin, config_entries.OptionsFlow):
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
		self._finalize_after_configure: bool = False
		self._custom_state_ui: dict[str, str | None] = {}
		_LOGGER.debug("OptionsFlow.__init__ complete. Loaded %d entities", len(self._controlled_entities))
	
	@property
	def config_entry(self):
		"""Return config entry (for test compatibility)."""
		# In tests, _config_entry is set in __init__
		# In production HA, super().config_entry is automatically available
		if hasattr(self, '_config_entry'):
			return self._config_entry
		return super().config_entry

	def _cleanup_presence_switch(self, entity_id: str | None) -> None:
		"""Remove the per-entity presence switch tied to a controlled entity."""
		if not self.hass or not entity_id:
			return
		unique_id = _presence_switch_unique_id(self.config_entry.entry_id, entity_id)
		if not unique_id:
			return
		registry = er.async_get(self.hass)
		existing_entity_id = registry.async_get_entity_id("switch", DOMAIN, unique_id)
		if existing_entity_id:
			_LOGGER.debug("Removing presence switch %s for entity %s", existing_entity_id, entity_id)
			registry.async_remove(existing_entity_id)

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
		"""Landing step for managing entities."""
		self._errors = {}
		if user_input is not None:
			action = user_input.get(FIELD_LANDING_ACTION)
			if action == ACTION_NO_ACTION:
				if not self._controlled_entities:
					self._errors = {"base": "no_controlled_entities"}
				else:
					return await self._finalize_and_reload()
			elif action == ACTION_EDIT_ENTITY:
				if not self._controlled_entities:
					self._errors = {"base": "no_controlled_entities"}
				else:
					return await self.async_step_choose_edit_entity()
			elif action == ACTION_DELETE_ENTITIES:
				if not self._controlled_entities:
					self._errors = {"base": "no_controlled_entities"}
				else:
					return await self.async_step_delete_entities()
			elif action == ACTION_ADD_ENTITY:
				self._editing_index = None
				self._selected_entity_id = None
				self._current_entity_config = {}
				self._finalize_after_configure = True
				return await self.async_step_select_entity()

		options = [
			selector.SelectOptionDict(value=ACTION_NO_ACTION, label="Submit pending changes"),
			selector.SelectOptionDict(value=ACTION_ADD_ENTITY, label="Add entity"),
			selector.SelectOptionDict(value=ACTION_EDIT_ENTITY, label="Edit an entity"),
			selector.SelectOptionDict(value=ACTION_DELETE_ENTITIES, label="Delete entities"),
		]

		return self.async_show_form(
			step_id=STEP_MANAGE_ENTITIES,
			data_schema=vol.Schema(
				{
					vol.Required(FIELD_LANDING_ACTION, default=ACTION_NO_ACTION): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=options,
							mode=selector.SelectSelectorMode.DROPDOWN,
						)
					)
				}
			),
			description_placeholders={
				"room": self._base_data[CONF_ROOM_NAME],
				"entity_cards": self._entity_cards_description(),
				"entity_count": str(len(self._controlled_entities)),
			},
			errors=self._errors,
		)

	async def async_step_choose_edit_entity(self, user_input=None):
		"""Let the user pick which entity to edit."""
		self._errors = {}
		if not self._controlled_entities:
			self._errors = {"base": "no_controlled_entities"}
			return await self.async_step_manage_entities()

		if user_input is not None:
			selection = user_input.get(FIELD_EDIT_ENTITY)
			try:
				idx = int(selection)
			except (TypeError, ValueError):
				self._errors = {FIELD_EDIT_ENTITY: "invalid_entity"}
			else:
				if 0 <= idx < len(self._controlled_entities):
					self._editing_index = idx
					self._current_entity_config = copy.deepcopy(self._controlled_entities[idx])
					self._selected_entity_id = self._current_entity_config[CONF_ENTITY_ID]
					self._finalize_after_configure = True
					self._custom_state_ui = {}
					return await self.async_step_configure_entity()
				self._errors = {FIELD_EDIT_ENTITY: "invalid_entity"}

		options: list[selector.SelectOptionDict] = [
			selector.SelectOptionDict(value=str(idx), label=self._format_entity_label(entity))
			for idx, entity in enumerate(self._controlled_entities)
		]

		return self.async_show_form(
			step_id=STEP_CHOOSE_EDIT_ENTITY,
			data_schema=vol.Schema(
				{
					vol.Required(FIELD_EDIT_ENTITY): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=options,
							mode=selector.SelectSelectorMode.DROPDOWN,
						)
					)
				}
			),
			description_placeholders={
				"entity_cards": self._entity_cards_description(),
				"entity_count": str(len(self._controlled_entities)),
			},
			errors=self._errors,
		)

	async def async_step_delete_entities(self, user_input=None):
		"""Allow the user to select entities to delete."""
		self._errors = {}
		if not self._controlled_entities:
			self._errors = {"base": "no_controlled_entities"}
			return await self.async_step_manage_entities()

		if user_input is not None:
			selected = user_input.get(FIELD_DELETE_ENTITIES, []) or []
			if not selected:
				self._errors = {FIELD_DELETE_ENTITIES: "select_entities_to_delete"}
			else:
				indices: list[int] = []
				for raw in selected:
					try:
						indices.append(int(raw))
					except (TypeError, ValueError):
						continue
				if not indices:
					self._errors = {FIELD_DELETE_ENTITIES: "select_entities_to_delete"}
				else:
					for idx in sorted(set(indices), reverse=True):
						if 0 <= idx < len(self._controlled_entities):
							removed = self._controlled_entities.pop(idx)
							entity_id = removed.get(CONF_ENTITY_ID)
							_LOGGER.debug("Removed entity %s during delete flow", removed)
							self._cleanup_presence_switch(entity_id)
					return await self._finalize_and_reload()

		options = [
			selector.SelectOptionDict(value=str(idx), label=self._format_entity_label(entity))
			for idx, entity in enumerate(self._controlled_entities)
		]

		return self.async_show_form(
			step_id=STEP_DELETE_ENTITIES,
			data_schema=vol.Schema(
				{
					vol.Required(FIELD_DELETE_ENTITIES): selector.SelectSelector(
						selector.SelectSelectorConfig(
							options=options,
							multiple=True,
							mode=selector.SelectSelectorMode.LIST,
						)
					)
				}
			),
			description_placeholders={
				"entity_cards": self._entity_cards_description(),
				"entity_count": str(len(self._controlled_entities)),
			},
			errors=self._errors,
		)

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
				self._custom_state_ui = {}
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

		entity_id = self._selected_entity_id
		entity_name = _get_entity_name(self.hass, entity_id)
		try:
			service_options = await _get_services_for_entity(self.hass, entity_id)
		except ServiceOptionsUnavailable:
			self._errors = {"base": "no_services_available"}
			return await self.async_step_select_entity()
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

		state_option_source = await _build_state_option_dicts(
			self.hass,
			entity_id,
			[
				defaults[CONF_PRESENCE_DETECTED_STATE],
				defaults[CONF_PRESENCE_CLEARED_STATE],
			],
		)
		use_dropdown = bool(state_option_source.from_hass and state_option_source.options)

		if user_input is not None:
			entity_off_delay = user_input.get(CONF_ENTITY_OFF_DELAY)
			resolved_detected_state = str(user_input[CONF_PRESENCE_DETECTED_STATE]).strip()
			resolved_cleared_state = str(user_input[CONF_PRESENCE_CLEARED_STATE]).strip()

			if use_dropdown:
				resolved_detected_state, detected_missing = _resolve_custom_state_selection(
					resolved_detected_state,
					user_input.get(FIELD_PRESENCE_DETECTED_STATE_CUSTOM),
					ui_state=self._custom_state_ui,
					ui_key=UI_CUSTOM_DETECTED_KEY,
				)
				resolved_cleared_state, cleared_missing = _resolve_custom_state_selection(
					resolved_cleared_state,
					user_input.get(FIELD_PRESENCE_CLEARED_STATE_CUSTOM),
					ui_state=self._custom_state_ui,
					ui_key=UI_CUSTOM_CLEARED_KEY,
				)
				if detected_missing:
					self._errors[FIELD_PRESENCE_DETECTED_STATE_CUSTOM] = "custom_state_required"
				if cleared_missing:
					self._errors[FIELD_PRESENCE_CLEARED_STATE_CUSTOM] = "custom_state_required"
			else:
				self._custom_state_ui.pop(UI_CUSTOM_DETECTED_KEY, None)
				self._custom_state_ui.pop(UI_CUSTOM_CLEARED_KEY, None)

			if not self._errors:
				updated_config = {
					CONF_ENTITY_ID: self._selected_entity_id,
					CONF_PRESENCE_DETECTED_SERVICE: user_input[CONF_PRESENCE_DETECTED_SERVICE],
					CONF_PRESENCE_DETECTED_STATE: resolved_detected_state,
					CONF_PRESENCE_CLEARED_SERVICE: user_input[CONF_PRESENCE_CLEARED_SERVICE],
					CONF_PRESENCE_CLEARED_STATE: resolved_cleared_state,
					CONF_RESPECTS_PRESENCE_ALLOWED: user_input[CONF_RESPECTS_PRESENCE_ALLOWED],
					CONF_DISABLE_ON_EXTERNAL_CONTROL: user_input[CONF_DISABLE_ON_EXTERNAL_CONTROL],
					CONF_INITIAL_PRESENCE_ALLOWED: DEFAULT_INITIAL_PRESENCE_ALLOWED,
				}

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
				self._custom_state_ui = {}
				if getattr(self, "_finalize_after_configure", False) and hasattr(self, "_finalize_and_reload"):
					self._finalize_after_configure = False
					return await self._finalize_and_reload()
				return await self.async_step_manage_entities()

		if use_dropdown:
			state_options = list(state_option_source.options)
			state_options.append(selector.SelectOptionDict(value=STATE_OPTION_CUSTOM, label=CUSTOM_LABEL))
			detected_defaults = _state_field_defaults(
				defaults[CONF_PRESENCE_DETECTED_STATE],
				ha_values=state_option_source.ha_values,
				allowed_defaults={DEFAULT_DETECTED_STATE},
				ui_state=self._custom_state_ui,
				ui_key=UI_CUSTOM_DETECTED_KEY,
			)
			cleared_defaults = _state_field_defaults(
				defaults[CONF_PRESENCE_CLEARED_STATE],
				ha_values=state_option_source.ha_values,
				allowed_defaults={DEFAULT_CLEARED_STATE},
				ui_state=self._custom_state_ui,
				ui_key=UI_CUSTOM_CLEARED_KEY,
			)
			detected_state_field = selector.SelectSelector(
				selector.SelectSelectorConfig(
					options=state_options,
					mode=selector.SelectSelectorMode.DROPDOWN,
				)
			)
			cleared_state_field = selector.SelectSelector(
				selector.SelectSelectorConfig(
					options=state_options,
					mode=selector.SelectSelectorMode.DROPDOWN,
				)
			)
			detected_default = detected_defaults.selector_default
			cleared_default = cleared_defaults.selector_default
		else:
			detected_state_field = str
			cleared_state_field = str
			detected_defaults = StateFieldDefaults(defaults[CONF_PRESENCE_DETECTED_STATE], None, False)
			cleared_defaults = StateFieldDefaults(defaults[CONF_PRESENCE_CLEARED_STATE], None, False)
			detected_default = detected_defaults.selector_default
			cleared_default = cleared_defaults.selector_default

		schema_fields: dict = {
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
				default=detected_default,
			): detected_state_field,
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
				default=cleared_default,
			): cleared_state_field,
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

		if use_dropdown:
			if detected_defaults.show_custom_input:
				detected_custom_field = vol.Optional(FIELD_PRESENCE_DETECTED_STATE_CUSTOM)
				if detected_defaults.custom_default is not None:
					detected_custom_field = vol.Optional(
						FIELD_PRESENCE_DETECTED_STATE_CUSTOM,
						default=detected_defaults.custom_default,
					)
				schema_fields[detected_custom_field] = selector.TextSelector(
					selector.TextSelectorConfig(
						type=selector.TextSelectorType.TEXT,
						multiline=False,
					)
				)
			if cleared_defaults.show_custom_input:
				cleared_custom_field = vol.Optional(FIELD_PRESENCE_CLEARED_STATE_CUSTOM)
				if cleared_defaults.custom_default is not None:
					cleared_custom_field = vol.Optional(
						FIELD_PRESENCE_CLEARED_STATE_CUSTOM,
						default=cleared_defaults.custom_default,
					)
				schema_fields[cleared_custom_field] = selector.TextSelector(
					selector.TextSelectorConfig(
						type=selector.TextSelectorType.TEXT,
						multiline=False,
					)
				)

		return self.async_show_form(
			step_id=STEP_CONFIGURE_ENTITY,
			data_schema=vol.Schema(schema_fields),
			errors=self._errors,
			description_placeholders={
				"entity_name": entity_name,
			},
		)
