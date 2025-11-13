# Migration from Blueprint to Integration

## Overview

The Presence Based Lighting functionality has been migrated from a Home Assistant automation blueprint to a full custom integration. This provides better state management, eliminates the need for helper entities, and supports multiple room configurations.

## Key Changes

### What's Different

1. **No More Input Booleans Required**: The integration stores the enabled/disabled state internally for each room configuration.

2. **Multi-Instance Support**: You can now configure multiple rooms (Living Room, Office, Bedroom, etc.) as separate integration entries.

3. **Switch Entity**: Each configured room gets a switch entity that controls whether the presence automation is enabled:
   - `switch.<room_name>_presence_automation`

4. **State Attributes**: The switch entity exposes useful attributes:
   - `lights`: List of controlled lights
   - `sensors`: List of presence sensors
   - `off_delay`: Configured delay in seconds
   - `any_occupied`: Whether any sensor detects occupancy
   - `any_light_on`: Whether any light is currently on

### Same Behavior

The core automation logic remains the same:
- ✅ Lights turn on when presence is detected
- ✅ Lights turn off after configured delay when room becomes unoccupied
- ✅ Manual override: Turning lights off disables automation
- ✅ Manual override: Turning lights on re-enables automation
- ✅ Smart handling of edge cases (lights on in empty room, etc.)

## Installation

1. Copy the `custom_components/presence_based_lighting` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant
3. Go to **Settings** → **Devices & Services** → **Add Integration**
4. Search for "Presence Based Lighting"

## Configuration

For each room you want to automate:

1. Add a new integration instance
2. Provide:
   - **Room Name**: e.g., "Living Room", "Office"
   - **Lights to Control**: Select one or more lights or light groups
   - **Presence Sensors**: Select one or more occupancy sensors
   - **Turn Off Delay**: Seconds to wait before turning off (default: 30)

## Example Setup

### Living Room
- Room Name: `Living Room`
- Lights: `light.living_room_ceiling`, `light.living_room_lamp`
- Sensors: `binary_sensor.living_room_motion`
- Delay: `30` seconds

This creates: `switch.living_room_presence_automation`

### Office
- Room Name: `Office`
- Lights: `light.office_desk`, `light.office_overhead`
- Sensors: `binary_sensor.office_motion_1`, `binary_sensor.office_motion_2`
- Delay: `120` seconds

This creates: `switch.office_presence_automation`

## Usage

### Normal Operation

The automation runs automatically when the switch is on. It will:
- Turn lights on when presence is detected
- Turn lights off after the delay when the room is empty

### Manual Control

- **Turn lights off manually**: Automation disables itself (switch turns off)
- **Turn lights on manually**: Automation re-enables itself (switch turns on)
- **Toggle the switch**: Manually enable/disable the automation

### In Automations

You can control the presence automation from other automations:

```yaml
# Disable presence automation during movie time
- service: switch.turn_off
  target:
    entity_id: switch.living_room_presence_automation

# Re-enable when done
- service: switch.turn_on
  target:
    entity_id: switch.living_room_presence_automation
```

### In Scripts

```yaml
# Temporary disable for cleaning
script:
  start_cleaning:
    sequence:
      - service: switch.turn_off
        target:
          entity_id: switch.living_room_presence_automation
      - service: vacuum.start
        target:
          entity_id: vacuum.living_room
```

## Advanced Features

### Options Flow

You can reconfigure each room instance:
1. Go to the integration in **Settings** → **Devices & Services**
2. Click **Configure** on any room entry
3. Update lights, sensors, or delay settings

### State Monitoring

Monitor automation state in dashboards:

```yaml
type: entities
entities:
  - entity: switch.living_room_presence_automation
    name: Living Room Auto-Lights
  - type: attribute
    entity: switch.living_room_presence_automation
    attribute: any_occupied
    name: Room Occupied
  - type: attribute
    entity: switch.living_room_presence_automation
    attribute: any_light_on
    name: Lights On
```

## Troubleshooting

### Automation Not Working

1. Check that the switch entity is **ON**
2. Verify your presence sensors are reporting state changes
3. Check Home Assistant logs for errors

### Multiple Rooms Interfering

Each integration entry is completely independent. Make sure you haven't assigned the same lights to multiple room configurations.

### Manual Override Not Detected

The integration detects manual changes by checking the context of state changes. If you're triggering lights through another automation, it may be detected as "automated" rather than manual. Consider using the switch entity to control the automation instead.

## Migration from Blueprint

If you're currently using the blueprint:

1. **Note your current settings** for each room (lights, sensors, delays)
2. **Install and configure the integration** with the same settings
3. **Test the integration** to ensure it works as expected
4. **Disable or delete the blueprint automations** for those rooms
5. **Remove the input_boolean helpers** (no longer needed)

## Support

For issues or questions:
- GitHub Issues: https://github.com/sfenton/presence_based_lighting/issues
- Discussions: https://github.com/sfenton/presence_based_lighting/discussions
