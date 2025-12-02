"""Constants for Presence Based Lighting."""
# Base component constants
NAME = "Presence Based Lighting"
DOMAIN = "presence_based_lighting"
VERSION = "2.0.0"

ISSUE_URL = "https://github.com/sfenton/presence_based_lighting/issues"

# Icons
ICON = "mdi:lightbulb-auto"

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
