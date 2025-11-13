# Quick Start Guide

## What You'll Get

After setting up Presence Based Lighting, you'll have intelligent room automation that:
- ✅ Turns lights on when you enter
- ✅ Turns lights off when you leave (after a delay)
- ✅ Respects manual control (manual off = automation pauses)
- ✅ Works independently for each room

## Prerequisites

Before you start, make sure you have:
- ✅ Occupancy/motion sensors (binary_sensor with device_class: occupancy)
- ✅ Lights or light groups you want to automate
- ✅ Home Assistant running (any recent version)

## Installation Steps

### 1. Install the Integration

**Option A: HACS (Recommended)**
1. Open HACS → Integrations
2. Click ⋮ → Custom repositories
3. Add: `https://github.com/sfenton/presence_based_lighting`
4. Install "Presence Based Lighting"
5. Restart Home Assistant

**Option B: Manual**
1. Download this repository
2. Copy `custom_components/presence_based_lighting` to your HA config
3. Restart Home Assistant

### 2. Add Your First Room

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for **"Presence Based Lighting"**
4. Fill in the form:

   **Example: Living Room**
   ```
   Room Name: Living Room
   Lights to Control: 
     - light.living_room_ceiling
     - light.living_room_lamp
   Presence Sensors:
     - binary_sensor.living_room_motion
   Turn Off Delay: 30 (seconds)
   ```

5. Click **Submit**

### 3. Verify It Works

You should now have a new switch entity:
- `switch.living_room_presence_automation`

**Test it:**
1. Make sure the switch is ON
2. Trigger your motion sensor
3. Lights should turn on automatically
4. Leave the room (motion clears)
5. After 30 seconds, lights should turn off

### 4. Add More Rooms (Optional)

Repeat step 2 for each room you want to automate:
- Bedroom
- Office  
- Kitchen
- Bathroom
- etc.

Each room gets its own independent automation and switch entity.

## Common Configurations

### Home Office
```
Room Name: Office
Lights: light.desk_lamp, light.overhead
Sensors: binary_sensor.office_motion
Delay: 120 seconds (2 minutes - for focused work)
```

### Bathroom
```
Room Name: Bathroom
Lights: light.bathroom
Sensors: binary_sensor.bathroom_motion
Delay: 60 seconds (1 minute - quick trips)
```

### Bedroom
```
Room Name: Bedroom
Lights: light.bedroom_ceiling, light.bedside_lamps
Sensors: binary_sensor.bedroom_motion
Delay: 300 seconds (5 minutes - getting ready)
```

### Multi-Sensor Room
```
Room Name: Living Room
Lights: light.living_room_group
Sensors: 
  - binary_sensor.living_room_motion_1
  - binary_sensor.living_room_motion_2
  - binary_sensor.hallway_motion
Delay: 30 seconds
```
*(Lights stay on if ANY sensor detects motion)*

## Using the Switch

### In Lovelace Dashboard

Add to your dashboard:

```yaml
type: entities
entities:
  - entity: switch.living_room_presence_automation
    name: Living Room Auto-Lights
  - entity: switch.office_presence_automation
    name: Office Auto-Lights
  - entity: switch.bedroom_presence_automation
    name: Bedroom Auto-Lights
```

### In Automations

Disable automation temporarily:

```yaml
automation:
  - alias: "Movie Time - Disable Living Room Automation"
    trigger:
      - platform: state
        entity_id: media_player.tv
        to: "playing"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.living_room_presence_automation
```

Re-enable when done:

```yaml
automation:
  - alias: "Movie Done - Enable Living Room Automation"
    trigger:
      - platform: state
        entity_id: media_player.tv
        to: "idle"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.living_room_presence_automation
```

## Manual Override Behavior

Understanding how manual control works:

| You Do                    | Integration Does                              |
|---------------------------|-----------------------------------------------|
| Turn lights OFF manually  | Automation disables (switch turns OFF)        |
| Turn lights ON manually   | Automation re-enables (switch turns ON)       |
| Toggle the switch OFF     | Automation stops (lights stay as-is)          |
| Toggle the switch ON      | Automation resumes (evaluates current state)  |

## Troubleshooting

### Lights don't turn on automatically
- ✅ Check the switch is ON: `switch.<room>_presence_automation`
- ✅ Verify your sensors are working (check their state in Developer Tools)
- ✅ Check Home Assistant logs for errors

### Lights turn off too quickly
- Increase the "Turn Off Delay" in the integration options
- Go to Settings → Devices & Services → Presence Based Lighting → Configure

### Lights don't turn off
- Verify ALL your presence sensors eventually go to "off"
- Check that lights are included in the configuration
- Make sure the switch is ON

### Manual control doesn't work as expected
- Remember: Manual OFF disables automation
- If lights are controlled by another automation, use the switch to control presence automation instead

## Advanced: Multiple Lights

You can control individual lights OR groups:

**Option 1: Individual Lights**
```
Lights: light.lamp_1, light.lamp_2, light.lamp_3
```
All turn on/off together

**Option 2: Use Light Groups**
```
First, create a group in configuration.yaml:
light:
  - platform: group
    name: Living Room All Lights
    entities:
      - light.ceiling
      - light.lamp_1
      - light.lamp_2

Then in the integration:
Lights: light.living_room_all_lights
```

## Next Steps

- Add more rooms as needed
- Adjust delays based on usage patterns
- Use the switch entities in your own automations
- Monitor state attributes for insights

## Need Help?

- 📖 See [README.md](README.md) for full documentation
- 🔄 Migrating from blueprint? See [MIGRATION.md](MIGRATION.md)
- 🐛 Found a bug? [Open an issue](https://github.com/sfenton/presence_based_lighting/issues)
