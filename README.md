# Presence Based Lighting

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

[![pre-commit][pre-commit-shield]][pre-commit]
[![Black][black-shield]][black]

[![hacs][hacsbadge]][hacs]
[![Project Maintenance][maintenance-shield]][user_profile]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

[![Discord][discord-shield]][discord]
[![Community Forum][forum-shield]][forum]

**Intelligent, metadata-driven presence automation with manual override support for Home Assistant.**

Drive lights, fans, or any switchable entity directly from HA service metadata. Presence-based actions stay in sync with manual control, and each controlled entity gets its own "Presence Allowed" toggle.

## Features

- ✨ **Automatic entity control** driven by presence sensors
- ⚙️ **Per-entity actions** – choose exactly which services/states to call when presence appears or clears (with `No Action` option)
- 🎯 **Smart manual override** – external control pauses automation until you re-enable it
- 🏠 **Multi-room + multi-entity** – configure multiple rooms, each with any number of controlled entities
- ⏱️ **Global or per-entity delays** – override turn-off timers per device when needed
- 🔧 **Completely UI-based** – no YAML, selectors are built-in to the config flow
- � **Presence Allowed switches** – each entity gets its own switch entity for dashboards or automations

## How It Works

**Automatic Mode (when enabled):**
- Lights turn **ON** when presence is detected
- Lights turn **OFF** after a configurable delay when room is unoccupied

**Manual Override:**
- Turn lights **OFF** manually → Automation disables itself
- Turn lights **ON** manually → Automation re-enables itself

Each controlled entity gets its own switch (`switch.<room>_presence_<entity>_presence_allowed`) so you can pause automation per device while keeping others running.

## Platforms

| Platform | Description                                                  |
| -------- | ------------------------------------------------------------ |
| `switch` | Enable/disable presence automation with state attributes    |

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Go to "Integrations"
3. Click the three dots in the top right and select "Custom repositories"
4. Add `https://github.com/sfenton/presence_based_lighting` as an Integration
5. Click "Install"
6. Restart Home Assistant

### Manual Installation

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`)
2. If you do not have a `custom_components` directory there, you need to create it
3. In the `custom_components` directory create a new folder called `presence_based_lighting`
4. Download _all_ the files from the `custom_components/presence_based_lighting/` directory in this repository
5. Place the files you downloaded in the new directory you created
6. Restart Home Assistant

## Configuration

**Configuration is done entirely in the UI:**

1. Go to **Settings** → **Devices & Services**
2. Click **"+ Add Integration"**
3. Search for **"Presence Based Lighting"**
4. Configure your room:
  - **Room Name**: e.g., "Living Room"
  - **Presence Sensors**: Binary sensors that indicate occupancy
  - **Global Turn-Off Delay**: Seconds to wait when presence clears
5. Add entities to control. For each entity:
  - Select the target entity
  - Pick services/states for presence detected/cleared (or `No Action`)
  - Decide whether the entity respects the toggle switch
  - Decide if external control should pause automation (manual turn-offs always pause actions until the entity is turned back on, even if the Presence Allowed switch is hidden)
  - Optionally set a per-entity off delay

You can add multiple room configurations - each operates independently.

## Usage Example

### Living Room Setup
```
Room Name: Living Room
Presence Sensors: binary_sensor.living_room_motion
Entities:
  - light.living_room_ceiling → `turn_on` / `turn_off`, 30s delay
  - fan.living_room_ceiling → `turn_on` / `turn_off`, 120s delay
```

This creates per-entity switches such as `switch.living_room_presence_light_living_room_ceiling_presence_allowed`.

### Switch Attributes

Each Presence Allowed switch includes:

- `controlled_entity`: The HA entity ID being automated
- `respect_presence_allowed`: Whether the entity honors the switch
- `disable_on_external_control`: Whether external control pauses automation. Manual turn-offs always pause actions until you manually turn the entity back on, even if the Presence Allowed switch is hidden.

### Use in Automations

```yaml
# Disable automation for a single lamp during movie time
- service: switch.turn_off
  target:
    entity_id: switch.living_room_presence_light_living_room_lamp_presence_allowed

# Re-enable after movie
- service: switch.turn_on
  target:
    entity_id: switch.living_room_presence_light_living_room_lamp_presence_allowed
```

## Contributions

Contributions are welcome! Please read the [Contribution guidelines](CONTRIBUTING.md)

## Credits

This project was generated from [@oncleben31](https://github.com/oncleben31)'s [Home Assistant Custom Component Cookiecutter](https://github.com/oncleben31/cookiecutter-homeassistant-custom-component) template.

Code template was mainly taken from [@Ludeeus](https://github.com/ludeeus)'s [integration_blueprint][integration_blueprint] template

---

[integration_blueprint]: https://github.com/custom-components/integration_blueprint
[black]: https://github.com/psf/black
[black-shield]: https://img.shields.io/badge/code%20style-black-000000.svg?style=for-the-badge
[buymecoffee]: https://www.buymeacoffee.com/sfenton
[buymecoffeebadge]: https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg?style=for-the-badge
[commits-shield]: https://img.shields.io/github/commit-activity/y/sfenton/presence_based_lighting.svg?style=for-the-badge
[commits]: https://github.com/sfenton/presence_based_lighting/commits/main
[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[discord]: https://discord.gg/Qa5fW2R
[discord-shield]: https://img.shields.io/discord/330944238910963714.svg?style=for-the-badge
[exampleimg]: example.png
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/sfenton/presence_based_lighting.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-%40sfenton-blue.svg?style=for-the-badge
[pre-commit]: https://github.com/pre-commit/pre-commit
[pre-commit-shield]: https://img.shields.io/badge/pre--commit-enabled-brightgreen?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/sfenton/presence_based_lighting.svg?style=for-the-badge
[releases]: https://github.com/sfenton/presence_based_lighting/releases
[user_profile]: https://github.com/sfenton
