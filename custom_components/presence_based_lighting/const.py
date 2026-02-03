"""Constants for Presence Based Lighting."""
# Base component constants
NAME = "Presence Based Lighting"
DOMAIN = "presence_based_lighting"
VERSION = "2.0.0"

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
CONF_OFF_DELAY = "off_delay"
CONF_CONTROLLED_ENTITIES = "controlled_entities"
CONF_ENTITY_ID = "entity_id"
CONF_PRESENCE_DETECTED_SERVICE = "presence_detected_service"
CONF_PRESENCE_DETECTED_STATE = "presence_detected_state"
CONF_PRESENCE_CLEARED_SERVICE = "presence_cleared_service"
CONF_PRESENCE_CLEARED_STATE = "presence_cleared_state"
CONF_RESPECTS_PRESENCE_ALLOWED = "respect_presence_allowed"
CONF_DISABLE_ON_EXTERNAL_CONTROL = "disable_on_external_control"
CONF_REQUIRE_OCCUPANCY_FOR_DETECTED = "require_occupancy_for_detected"
CONF_REQUIRE_VACANCY_FOR_CLEARED = "require_vacancy_for_cleared"
CONF_INITIAL_PRESENCE_ALLOWED = "initial_presence_allowed"
CONF_ENTITY_OFF_DELAY = "entity_off_delay"
CONF_AUTOMATION_MODE = "automation_mode"
CONF_USE_INTERCEPTOR = "use_interceptor"
CONF_MANUAL_DISABLE_STATES = "manual_disable_states"
CONF_RLC_TRACKING_ENTITY = "rlc_tracking_entity"  # Optional RLC sensor that tracks this entity's real state
CONF_PRESENCE_SENSOR_MAPPINGS = "presence_sensor_mappings"  # Maps presence sensors to their source entities
CONF_CLEARING_SENSOR_MAPPINGS = "clearing_sensor_mappings"  # Maps clearing sensors to their source entities
CONF_ACTIVATION_CONDITIONS = "activation_conditions"  # Optional binary_sensor/input_boolean entities that must ALL be on for lights to activate

# Auto re-enable configuration keys
CONF_AUTO_REENABLE_PRESENCE_SENSORS = "auto_reenable_presence_sensors"  # Presence sensors used for vacancy tracking
CONF_AUTO_REENABLE_VACANCY_THRESHOLD = "auto_reenable_vacancy_threshold"  # Percentage threshold for vacancy (0-100)
CONF_AUTO_REENABLE_START_TIME = "auto_reenable_start_time"  # Start of monitoring window (time string HH:MM:SS)
CONF_AUTO_REENABLE_END_TIME = "auto_reenable_end_time"  # End of monitoring window (time string HH:MM:SS)

# Automation mode values
AUTOMATION_MODE_AUTOMATIC = "automatic"
AUTOMATION_MODE_PRESENCE_LOCK = "presence_lock"

# Special value for no action
NO_ACTION = "none"

# Defaults
DEFAULT_OFF_DELAY = 30  # seconds
DEFAULT_DETECTED_SERVICE = "turn_on"
DEFAULT_CLEARED_SERVICE = "turn_off"
DEFAULT_DETECTED_STATE = "on"
DEFAULT_CLEARED_STATE = "off"
DEFAULT_RESPECTS_PRESENCE_ALLOWED = True
DEFAULT_DISABLE_ON_EXTERNAL = True
DEFAULT_INITIAL_PRESENCE_ALLOWED = True
DEFAULT_REQUIRE_OCCUPANCY_FOR_DETECTED = False
DEFAULT_REQUIRE_VACANCY_FOR_CLEARED = False
DEFAULT_AUTOMATION_MODE = AUTOMATION_MODE_AUTOMATIC
DEFAULT_USE_INTERCEPTOR = True  # Default to using interceptor when available
DEFAULT_MANUAL_DISABLE_STATES = []  # Empty list means no states disable automation by default

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
