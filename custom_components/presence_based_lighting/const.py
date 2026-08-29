"""Constants for Presence Based Lighting."""
# Base component constants
NAME = "Presence Based Lighting"
DOMAIN = "presence_based_lighting"
VERSION = "2.4.3"

ISSUE_URL = "https://github.com/sfenton/presence_based_lighting/issues"

# Icons
ICON = "mdi:lightbulb-auto"
ICON_AUTO_REENABLE = "mdi:autorenew"

# Platforms
SWITCH = "switch"
PLATFORMS = [SWITCH]

# Configuration keys
CONF_ROOM_NAME = "room_name"
CONF_PRESENCE_SENSORS = "presence_sensors"
CONF_CLEARING_SENSORS = "clearing_sensors"
CONF_CLEARING_SENSORS_AUTO_DISCOVERED = "clearing_sensors_auto_discovered"
CONF_VACANCY_AUTHORITY_SENSORS = "vacancy_authority_sensors"
CONF_VACANCY_AUTHORITY_AUTO_DISCOVERED = "vacancy_authority_auto_discovered"
CONF_OFF_DELAY = "off_delay"
CONF_CONTROLLED_ENTITIES = "controlled_entities"
CONF_ENTITY_ID = "entity_id"
CONF_PRESENCE_DETECTED_SERVICE = "presence_detected_service"
CONF_PRESENCE_DETECTED_BRIGHTNESS_PCT = "presence_detected_brightness_pct"
CONF_PRESENCE_DETECTED_TRANSITION = "presence_detected_transition"
CONF_PRESENCE_DETECTED_STATE = "presence_detected_state"
CONF_PRESENCE_CLEARED_SERVICE = "presence_cleared_service"
CONF_PRESENCE_CLEARED_TRANSITION = "presence_cleared_transition"
CONF_PRESENCE_CLEARED_STATE = "presence_cleared_state"
CONF_RESPECTS_PRESENCE_ALLOWED = "respect_presence_allowed"
CONF_DISABLE_ON_EXTERNAL_CONTROL = "disable_on_external_control"
CONF_REQUIRE_OCCUPANCY_FOR_DETECTED = "require_occupancy_for_detected"
CONF_REQUIRE_VACANCY_FOR_CLEARED = "require_vacancy_for_cleared"
CONF_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE = "presence_lock_respects_manual_override"
CONF_INITIAL_PRESENCE_ALLOWED = "initial_presence_allowed"
CONF_ENTITY_OFF_DELAY = "entity_off_delay"
CONF_AUTOMATION_MODE = "automation_mode"
CONF_USE_INTERCEPTOR = "use_interceptor"
CONF_NORMALIZE_EXTERNAL_PLAIN_ON = "normalize_external_plain_on"
CONF_MANUAL_DISABLE_STATES = "manual_disable_states"
CONF_RLC_TRACKING_ENTITY = "rlc_tracking_entity"  # Optional RLC sensor that tracks this entity's real state
CONF_PRESENCE_SENSOR_MAPPINGS = "presence_sensor_mappings"  # Maps presence sensors to their source entities
CONF_CLEARING_SENSOR_MAPPINGS = "clearing_sensor_mappings"  # Maps clearing sensors to their source entities
CONF_ACTIVATION_CONDITIONS = "activation_conditions"  # Optional binary_sensor/input_boolean entities that must ALL be on for lights to activate
CONF_ACTIVATION_CATCHUP_MODE = "activation_catchup_mode"

# External override configuration keys
# An "external override" is a fact about a *controlled entity*, not about one
# config entry. Every entry controlling the entity consults the same record so
# paired room profiles cannot resurrect a light another profile just released.
CONF_HONOR_EXTERNAL_OVERRIDE = "honor_external_override"  # Per-entity: consult entity-scoped overrides recorded by sibling entries
CONF_UNKNOWN_SOURCE_POLICY = "unknown_source_policy"  # Policy for external commands we cannot attribute to a known source
CONF_BULK_COMMAND_POLICY = "bulk_command_policy"  # pause | rearm_after_clear for confirmed whole-home commands
CONF_QUIETED_MAX_AGE = "quieted_max_age"  # Seconds before a quieted hold is considered stale
CONF_QUIETED_MAX_AGE_ACTION = "quieted_max_age_action"  # diagnostic | pause | arm

# Domain-wide bulk ("all lights off") detection keys
CONF_HOMEKIT_BATCH_MODE = "homekit_batch_mode"  # off | observe | enforce
CONF_BATCH_WINDOW_MS = "batch_window_ms"  # Grouping window for same-service HomeKit commands
CONF_BATCH_RETAIN_SECONDS = "batch_retain_seconds"  # How long a context->batch mapping stays resolvable
CONF_BATCH_MIN_DISTINCT_ENTITIES = "batch_min_distinct_entities"  # Distinct managed target entities required to call it a batch

# Auto re-enable configuration keys
CONF_AUTO_REENABLE_PRESENCE_SENSORS = "auto_reenable_presence_sensors"  # Presence sensors used for vacancy tracking
CONF_AUTO_REENABLE_VACANCY_THRESHOLD = "auto_reenable_vacancy_threshold"  # Percentage threshold for vacancy (0-100)
CONF_AUTO_REENABLE_START_TIME = "auto_reenable_start_time"  # Start of monitoring window (time string HH:MM:SS)
CONF_AUTO_REENABLE_END_TIME = "auto_reenable_end_time"  # End of monitoring window (time string HH:MM:SS)

# Automation mode values
AUTOMATION_MODE_AUTOMATIC = "automatic"
AUTOMATION_MODE_PRESENCE_LOCK = "presence_lock"

# Activation-gate catch-up policies
ACTIVATION_CATCHUP_ANY_TRIGGER = "any_trigger"
ACTIVATION_CATCHUP_CLEARING_AUTHORITY = "clearing_authority"
ACTIVATION_CATCHUP_NONE = "none"

# External override policies
# PAUSE keeps today's semantics: automation stays suspended until the user
# resumes it (or the controlled entity leaves its manual-disable states).
# REARM_AFTER_CLEAR ("quieted") honours a whole-home off without stranding the
# room: the light stays dark until the room actually goes vacant, and only a
# fresh rising presence edge after that vacancy may turn it back on.
EXTERNAL_POLICY_PAUSE = "pause"
EXTERNAL_POLICY_REARM_AFTER_CLEAR = "rearm_after_clear"
EXTERNAL_POLICY_IGNORE = "ignore"

# Quieted max-age actions
QUIETED_MAX_AGE_ACTION_DIAGNOSTIC = "diagnostic"
QUIETED_MAX_AGE_ACTION_PAUSE = "pause"
QUIETED_MAX_AGE_ACTION_ARM = "arm"

# Sources an external command can be attributed to
SOURCE_UNKNOWN = "unknown"
SOURCE_HOMEKIT_SINGLE = "homekit_single"
SOURCE_HOMEKIT_BATCH = "homekit_batch"
SOURCE_ADMIN = "admin"

# Administrative state-control values
AUTOMATION_CONTROL_STATE_ON = "on"
AUTOMATION_CONTROL_STATE_OFF = "off"
AUTOMATION_CONTROL_STATE_PAUSED = "paused"
AUTOMATION_CONTROL_STATE_QUIETED = "quieted"
AUTOMATION_CONTROL_STATE_ACTIVE = "active"
AUTOMATION_CONTROL_STATES = {
	AUTOMATION_CONTROL_STATE_ON,
	AUTOMATION_CONTROL_STATE_OFF,
	AUTOMATION_CONTROL_STATE_PAUSED,
	AUTOMATION_CONTROL_STATE_QUIETED,
	AUTOMATION_CONTROL_STATE_ACTIVE,
}

# HomeKit batch detection modes
BATCH_MODE_OFF = "off"
BATCH_MODE_OBSERVE = "observe"
BATCH_MODE_ENFORCE = "enforce"

# Home Assistant event fired by the HomeKit bridge immediately before it issues
# the service call, sharing the same context object as that call.
EVENT_HOMEKIT_STATE_CHANGE = "homekit_state_change"
# Diagnostic event emitted for every external command classification decision.
EVENT_COMMAND_INTENT = "presence_based_lighting_command_intent"

# Special value for no action
NO_ACTION = "none"

# Defaults
DEFAULT_OFF_DELAY = 30  # seconds
DEFAULT_DETECTED_SERVICE = "turn_on"
DEFAULT_PRESENCE_DETECTED_BRIGHTNESS_PCT = 100
DEFAULT_PRESENCE_DETECTED_TRANSITION = 1.0  # seconds
DEFAULT_CLEARED_SERVICE = "turn_off"
DEFAULT_PRESENCE_CLEARED_TRANSITION = 1.0  # seconds
DEFAULT_DETECTED_STATE = "on"
DEFAULT_CLEARED_STATE = "off"
DEFAULT_RESPECTS_PRESENCE_ALLOWED = True
DEFAULT_DISABLE_ON_EXTERNAL = True
DEFAULT_INITIAL_PRESENCE_ALLOWED = True
DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED = False
DEFAULT_REQUIRE_VACANCY_FOR_CLEARED = False
DEFAULT_PRESENCE_LOCK_RESPECTS_MANUAL_OVERRIDE = True
DEFAULT_AUTOMATION_MODE = AUTOMATION_MODE_AUTOMATIC
DEFAULT_USE_INTERCEPTOR = True  # Default to using interceptor when available
DEFAULT_NORMALIZE_EXTERNAL_PLAIN_ON = True
DEFAULT_MANUAL_DISABLE_STATES = ["off"]  # Manual off pauses automation by default
DEFAULT_ACTIVATION_CATCHUP_MODE = ACTIVATION_CATCHUP_ANY_TRIGGER

# External override defaults.
# UNKNOWN deliberately stays PAUSE: wall switches on direct-relay devices reach
# Home Assistant as untraceable state changes, and rearming them automatically
# would silently defeat a physical off.
DEFAULT_HONOR_EXTERNAL_OVERRIDE = True
DEFAULT_UNKNOWN_SOURCE_POLICY = EXTERNAL_POLICY_PAUSE
DEFAULT_BULK_COMMAND_POLICY = EXTERNAL_POLICY_REARM_AFTER_CLEAR
DEFAULT_QUIETED_MAX_AGE = 14400  # 4 hours before stale-hold diagnostics
DEFAULT_QUIETED_MAX_AGE_ACTION = QUIETED_MAX_AGE_ACTION_DIAGNOSTIC

# Bulk detection defaults. Observed native "all lights off" bursts carried 15
# commands in 21.46 ms and 16 commands in 25.39 ms, while a single-room HomeKit
# off is exactly one command, so 250 ms leaves ~31x margin over the widest
# measured intra-burst gap (7.9 ms) with no realistic false-positive path.
DEFAULT_HOMEKIT_BATCH_MODE = BATCH_MODE_ENFORCE
DEFAULT_BATCH_WINDOW_MS = 250
DEFAULT_BATCH_RETAIN_SECONDS = 10.0
DEFAULT_BATCH_MIN_DISTINCT_ENTITIES = 8

# Auto re-enable defaults
DEFAULT_AUTO_REENABLE_START_TIME = "00:00:00"  # Midnight
DEFAULT_AUTO_REENABLE_END_TIME = "05:00:00"  # 5 AM
DEFAULT_AUTO_REENABLE_VACANCY_THRESHOLD = 80  # 80% empty threshold

# File logging (optional)
CONF_FILE_LOGGING_ENABLED = "file_logging_enabled"
DEFAULT_FILE_LOGGING_ENABLED = False

# Hard kill-switch for file logging.
#
# The integration previously enabled file logging unconditionally from runtime code.
# Keep this single constant so we can re-enable easily without touching call sites.
ENABLE_FILE_LOGGING = False
FILE_LOG_NAME = "presence_based_lighting_debug.log"
FILE_LOG_MAX_LINES = 10_000

# State attributes

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
