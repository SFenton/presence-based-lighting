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

**Intelligent presence-based lighting automation with manual override support for Home Assistant.**

Automates lights based on occupancy sensors while respecting manual control. When you manually turn lights off, the automation disables itself. When you turn them back on, automation resumes.

## Features

- ✨ **Automatic light control** based on presence sensors
- 🎯 **Smart manual override** - respects your manual changes
- 🏠 **Multi-room support** - configure as many rooms as needed
- ⏱️ **Configurable delays** - set how long to wait before turning off
- 🔧 **Easy configuration** - full UI-based setup
- 📊 **Rich state attributes** - monitor occupancy and light status

## How It Works

**Automatic Mode (when enabled):**
- Lights turn **ON** when presence is detected
- Lights turn **OFF** after a configurable delay when room is unoccupied

**Manual Override:**
- Turn lights **OFF** manually → Automation disables itself
- Turn lights **ON** manually → Automation re-enables itself

Each room gets a switch entity (`switch.<room>_presence_automation`) to enable/disable automation.

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
   - **Lights to Control**: Select lights or groups
   - **Presence Sensors**: Select occupancy sensors
   - **Turn Off Delay**: Seconds to wait (default: 30)

You can add multiple room configurations - each operates independently.

## Usage Example

### Living Room Setup
```
Room Name: Living Room
Lights: light.living_room_ceiling, light.living_room_lamp
Sensors: binary_sensor.living_room_motion
Delay: 30 seconds
```

This creates: `switch.living_room_presence_automation`

### State Attributes

The switch entity exposes useful information:

- `lights`: List of controlled lights
- `sensors`: List of presence sensors  
- `off_delay`: Configured delay in seconds
- `any_occupied`: Current occupancy status
- `any_light_on`: Current light status

### Use in Automations

```yaml
# Disable during movie time
- service: switch.turn_off
  target:
    entity_id: switch.living_room_presence_automation

# Re-enable after movie
- service: switch.turn_on
  target:
    entity_id: switch.living_room_presence_automation
```

## Migration from Blueprint

Previously using the presence.yaml blueprint? See [MIGRATION.md](MIGRATION.md) for upgrade instructions.

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
