"""Constants for Presence Based Lighting."""
# Base component constants
NAME = "Presence Based Lighting"
DOMAIN = "presence_based_lighting"
DOMAIN_DATA = f"{DOMAIN}_data"
VERSION = "1.0.0"

ATTRIBUTION = "Presence-based lighting automation with manual override"
ISSUE_URL = "https://github.com/sfenton/presence_based_lighting/issues"

# Icons
ICON = "mdi:lightbulb-auto"

# Platforms
SWITCH = "switch"
PLATFORMS = [SWITCH]

# Configuration keys
CONF_ROOM_NAME = "room_name"
CONF_LIGHT_ENTITIES = "light_entities"
CONF_PRESENCE_SENSORS = "presence_sensors"
CONF_OFF_DELAY = "off_delay"

# Defaults
DEFAULT_OFF_DELAY = 30  # seconds
DEFAULT_NAME = DOMAIN

# State attributes
ATTR_PRESENCE_ALLOWED = "presence_allowed"
ATTR_LIGHTS = "lights"
ATTR_SENSORS = "sensors"
ATTR_OFF_DELAY = "off_delay"
ATTR_ANY_OCCUPIED = "any_occupied"
ATTR_ANY_LIGHT_ON = "any_light_on"

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
