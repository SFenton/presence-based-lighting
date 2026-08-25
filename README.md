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
- 🌅 **Single-stage light activation** – PBL and intercepted plain light turn-ons target a configurable brightness and transition before dispatch
- 🌙 **Smooth light clearing** – presence-driven light turn-offs use a configurable transition
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

| Platform | Description                                              |
| -------- | -------------------------------------------------------- |
| `switch` | Enable/disable presence automation with state attributes |

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

### Optional pre-dispatch normalization

Install [Hass Interceptor](https://github.com/SFenton/hass-interceptor) and add
`hass_interceptor:` to `configuration.yaml` to normalize external plain
`light.turn_on` calls before they reach the device. PBL continues to operate
without it, but manual HomeKit/HA turn-ons then retain their original service
data.

## Configuration

**Configuration is done entirely in the UI:**

1. Go to **Settings** → **Devices & Services**
2. Click **"+ Add Integration"**
3. Search for **"Presence Based Lighting"**
4. Configure your room:

- **Room Name**: e.g., "Living Room"
- **Trigger Sensors**: Fast motion or presence sensors that should turn entities on
- **Clearing Sensors**: Sensors that must all be clear before entities turn off. When configured, these are the clearing authority; trigger-only sensors can turn entities on but do not veto a clear. PBL auto-fills an exact room-level Area Occupancy Detection / Real Last Changed occupancy status when one exists, so raw trigger sensors can turn lights on quickly without being trusted to clear the room. Leave empty to use the trigger sensors for both activation and clearing.
- **Global Turn-Off Delay**: Seconds to wait when presence clears

5. Add entities to control. For each entity:

- Select the target entity
- Pick services/states for presence detected/cleared (or `No Action`)
- Decide whether the entity respects the toggle switch
- Decide if external control should pause automation (manual turn-offs always pause actions until the entity is turned back on, even if the Presence Allowed switch is hidden)
- For lights, optionally adjust the turn-on brightness (100% by default) and transition (1 second by default)
- For lights, optionally adjust the turn-off transition (1 second by default)
- Optionally set a per-entity off delay

Activation-gated entries can also choose how existing occupancy is handled when
their gate opens:

- **Any occupied trigger** preserves the original catch-up behavior.
- **Clearing authority occupied** only catches up when the room's authoritative
  clearing sensor is occupied; trigger-only prelighting still works on a fresh
  rising edge after the gate is open.
- **Fresh trigger only** never catches up from already-active sensors.

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
- `automation_quieted` / `quieted`: Whether the entity is holding dark after a whole-home command
- `automation_suppressed` / `suppression_kind`: Unified PAUSED-or-QUIETED status for downstream consumers
- `bulk_command_policy`: Whether confirmed whole-home commands pause or quiet this entity
- `external_override_policy` / `external_override_source` / `external_override_reason`: Why automation is currently suppressed
- `external_override_batch_id` / `external_override_batch_size`: The detected bulk command, if any
- `rearm_latched` / `rearm_latched_at` / `rearm_armed_by`: Whether and why re-entry became eligible
- `external_override_at` / `external_override_expires_at`: When the hold started and when it becomes stale
- `quieted_max_age_action` / `quieted_max_age_reached_at`: Configured and applied stale-hold handling
- `unknown_source_count`: How many external commands could not be attributed to a known source
- `homekit_batch_mode`: The active bulk-detection kill-switch mode

## Whole-Home ("All Lights Off") Commands

A native HomeKit/Siri "turn off all the lights" is not one command. Home Assistant's
HomeKit bridge creates a **fresh context for every accessory**, so the house sees N
unrelated single-entity `turn_off` calls. Treating each one as manual control paused
every room indefinitely.

This integration groups same-service HomeKit commands that arrive close together and
treats a large burst as a whole-home command rather than N manual overrides.

### Per-entity bulk policy

Each controlled entity chooses what a confirmed whole-home command means:

- `rearm_after_clear` enters `quieted`.
- `pause` stays dark until an explicit resume.

This lets sleep-sensitive rooms fail dark while hallways and other rooms can rearm.
The policy applies even when the managed entity was already off when the batch arrived;
redundant singleton offs remain no-ops.

For a `quieted` entity:

- Entering the state emits no service call.
- Reconciliation and Presence Lock are both suppressed, so an occupied room cannot
  bounce the light straight back on.
- When the room genuinely becomes vacant, a **rearm latch** is armed. This only
  records that vacancy happened; the entity stays dark.
- The **next rising presence edge after that vacancy** releases the hold and normal
  presence automation resumes.
- Reaching `quieted_max_age` is diagnostic-only by default. Legacy latch arming and
  fail-dark conversion to `paused` remain explicit options.

Single-accessory HomeKit offs, wall switches and any command that cannot be attributed
to a bulk burst still `pause` exactly as before.

### Settings

Per config entry:

| Setting                       | Default   | Purpose                                                          |
| ----------------------------- | --------- | ---------------------------------------------------------------- |
| `homekit_batch_mode`          | `enforce` | Kill switch: `off`, `observe` (classify and log only), `enforce` |
| `batch_window_ms`             | `250`     | Grouping window for same-service HomeKit commands                |
| `batch_retain_seconds`        | `10`      | How long a context stays resolvable to its batch                 |
| `batch_min_distinct_entities` | `8`       | Distinct target entities required to call a burst a bulk command |

The observer is domain-wide but settings are per entry, so values are reduced
deterministically (independently of entry setup order): **mode** takes the least
behaviour-changing value (`off` beats `observe` beats `enforce`), **window** takes the
smallest, **min distinct entities** takes the largest, and **retention** takes the
longest. One entry set to `off` therefore disables bulk detection for the whole house;
set it per entry only if that is what you want.

The shared `homekit_state_change` listener is reference counted: it attaches when the
first entry loads and detaches only when the last entry unloads or when the effective
mode becomes `off`.

Per controlled entity:

| Setting                   | Default             | Purpose                                                                    |
| ------------------------- | ------------------- | -------------------------------------------------------------------------- |
| `honor_external_override` | `true`              | Consult entity-scoped overrides recorded by sibling entries                |
| `unknown_source_policy`   | `pause`             | Policy for external commands that cannot be attributed                     |
| `bulk_command_policy`     | `rearm_after_clear` | `rearm_after_clear` (Quieted) or `pause` for confirmed whole-home commands |
| `quieted_max_age`         | `14400`             | Seconds before a quieted hold is marked stale                              |
| `quieted_max_age_action`  | `diagnostic`        | `diagnostic`, `pause`, or legacy `arm`                                     |

### Entity-scoped overrides and paired profiles

An override caused by an external action on a controlled entity is a fact about **that
entity**, not about one config entry. Paired profiles (for example a room and its
"…(other room lights off)" counterpart) control the same light behind opposing
activation gates, so an override recorded by whichever profile was active is visible to
the other. Without this, flipping the activation gate hands control to a profile that
never saw the override and it immediately resurrects the light.

Explicit per-entry controls — the Presence Allowed switch and `pause_automation`
targeting a specific PBL switch — remain entry-local. Set `honor_external_override` to
`false` on an entity to restore the old entry-local behaviour.

### Scheduled auto re-enable

When the configured vacancy threshold is met at the end of the auto-reenable
window, the reset clears shared non-admin `pause` overrides before resuming and
reconciling the loaded room profiles. Physical wall-switch, single-accessory
HomeKit, and batch-derived pauses therefore remain fail-dark until the scheduled
reset, then release consistently across paired profiles.

Admin-created pauses remain in place until an explicit admin resume. Quieted
`rearm_after_clear` holds also keep their vacancy-and-next-presence lifecycle.

### Administrative state service

`presence_based_lighting.set_automation_state` accepts a PBL switch target and one of:

- `on`: enable Presence Allowed, clearing admin-created suppression
- `off`: disable Presence Allowed
- `paused`: apply an entity-scoped indefinite pause
- `quieted`: apply an entity-scoped rearm-after-clear hold
- `active`: force-clear local and entity-scoped suppression, then reconcile

### Escape hatch

```yaml
# Clear every pause and every quieted hold across all rooms
- service: presence_based_lighting.resume_all_automation
```

### Diagnostics

Every classification decision fires a `presence_based_lighting_command_intent` event
with `entry_id`, `room`, `entity_id`, `source`, `policy`, `reason`, `batch_id`,
`batch_size` and `batch_mode`. Run with `homekit_batch_mode: observe` first if you want
to confirm classification against your own house before enabling enforcement.

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
