# Comprehensive Test Plan - Presence Based Lighting

## Test Matrix Overview

### State Variables
- **Lights**: ON | OFF
- **Occupancy**: OCCUPIED | CLEAR
- **Automation Switch**: ENABLED | DISABLED
- **Change Source**: AUTOMATION | MANUAL | OTHER_AUTOMATION
- **Pending Timer**: ACTIVE | NONE

### Events to Test
- Occupancy sensor transitions (ON→OFF, OFF→ON)
- Light state transitions (ON→OFF, OFF→ON)
- Automation switch transitions (ON→OFF, OFF→ON)
- Timer expiration
- Configuration changes

---

## 1. Basic Occupancy Detection Tests

### 1.1 Automation Enabled - Lights Off - No Occupancy
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 1.1.1 | Lights: OFF<br>Occupancy: CLEAR<br>Switch: ON | Occupancy → OCCUPIED | Lights turn ON<br>No timer active |
| 1.1.2 | Lights: OFF<br>Occupancy: CLEAR<br>Switch: ON | Occupancy stays CLEAR | Lights stay OFF<br>No timer active |

### 1.2 Automation Enabled - Lights On - Occupancy Present
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 1.2.1 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | Occupancy stays OCCUPIED | Lights stay ON<br>No timer active |
| 1.2.2 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON<br>Timer: ACTIVE | Occupancy stays OCCUPIED | Lights stay ON<br>Timer continues |

### 1.3 Automation Enabled - Occupancy Clears
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 1.3.1 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | Occupancy → CLEAR | Timer starts (30s default)<br>Lights stay ON (for now) |
| 1.3.2 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | Occupancy → CLEAR<br>Wait 30s | Timer expires<br>Lights turn OFF |
| 1.3.3 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON<br>Timer: ACTIVE (15s elapsed) | Occupancy → OCCUPIED | Timer cancelled<br>Lights stay ON |

### 1.4 Automation Disabled - Occupancy Changes
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 1.4.1 | Lights: OFF<br>Occupancy: CLEAR<br>Switch: OFF | Occupancy → OCCUPIED | Lights stay OFF<br>No automation response |
| 1.4.2 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: OFF | Occupancy → CLEAR | Lights stay ON<br>No timer starts |
| 1.4.3 | Lights: ON<br>Occupancy: CLEAR<br>Switch: OFF | Wait indefinitely | Lights stay ON<br>No automation action |

---

## 2. Manual Light Control Tests

### 2.1 Manual Light OFF (while automation enabled)
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 2.1.1 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | User turns lights OFF | Lights turn OFF<br>Switch → DISABLED<br>Automation stops |
| 2.1.2 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON<br>Timer: ACTIVE | User turns lights OFF | Lights turn OFF<br>Switch → DISABLED<br>Timer cancelled |
| 2.1.3 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON | User turns lights OFF | Lights turn OFF<br>Switch → DISABLED |

### 2.2 Manual Light ON (while automation enabled)
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 2.2.1 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: ON | User turns lights ON | Lights turn ON<br>Switch stays ON<br>Normal automation |
| 2.2.2 | Lights: OFF<br>Occupancy: CLEAR<br>Switch: ON | User turns lights ON | Lights turn ON<br>Switch stays ON<br>Timer starts (30s) |
| 2.2.3 | Lights: OFF<br>Occupancy: CLEAR<br>Switch: ON | User turns lights ON<br>Wait 30s | Timer expires<br>Lights turn OFF |
| 2.2.4 | Lights: OFF<br>Occupancy: CLEAR<br>Switch: ON | User turns lights ON<br>Wait 15s<br>Occupancy → OCCUPIED | Timer cancelled<br>Lights stay ON |

### 2.3 Manual Light Control (while automation disabled)
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 2.3.1 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: OFF | User turns lights ON | Lights turn ON<br>Switch → ENABLED |
| 2.3.2 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: OFF | User turns lights OFF | Lights turn OFF<br>Switch → DISABLED |
| 2.3.3 | Lights: ON<br>Occupancy: CLEAR<br>Switch: OFF | User turns lights ON | Lights stay ON<br>Switch → ENABLED<br>Timer starts |

---

## 3. Automation Switch Toggle Tests

### 3.1 Disabling Automation Switch
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 3.1.1 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | User toggles switch OFF | Switch → DISABLED<br>Lights stay ON<br>Automation stops |
| 3.1.2 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON<br>Timer: ACTIVE | User toggles switch OFF | Switch → DISABLED<br>Timer cancelled<br>Lights stay ON |
| 3.1.3 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: ON | User toggles switch OFF | Switch → DISABLED<br>Lights stay OFF |

### 3.2 Enabling Automation Switch
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 3.2.1 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: OFF | User toggles switch ON | Switch → ENABLED<br>Lights turn ON |
| 3.2.2 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: OFF | User toggles switch ON | Switch → ENABLED<br>Lights stay ON<br>Normal automation |
| 3.2.3 | Lights: ON<br>Occupancy: CLEAR<br>Switch: OFF | User toggles switch ON | Switch → ENABLED<br>Timer starts (30s)<br>Lights stay ON |
| 3.2.4 | Lights: ON<br>Occupancy: CLEAR<br>Switch: OFF | User toggles switch ON<br>Wait 30s | Timer expires<br>Lights turn OFF |
| 3.2.5 | Lights: OFF<br>Occupancy: CLEAR<br>Switch: OFF | User toggles switch ON | Switch → ENABLED<br>Lights stay OFF<br>No timer |

---

## 4. Automation-Triggered Light Changes

### 4.1 Lights Changed by This Automation
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 4.1.1 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: ON | Automation turns lights ON | Lights turn ON<br>Switch stays ON |
| 4.1.2 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON<br>Timer: expires | Automation turns lights OFF | Lights turn OFF<br>Switch stays ON |
| 4.1.3 | Lights: ON (by automation)<br>Occupancy: OCCUPIED<br>Switch: ON | Occupancy → CLEAR<br>Wait 30s | Timer expires<br>Lights turn OFF<br>Switch stays ON |

### 4.2 Lights Changed by Other Automation
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 4.2.1 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | Other automation turns lights OFF | Lights turn OFF<br>Switch → DISABLED<br>(Treated as manual) |
| 4.2.2 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: ON | Other automation turns lights ON | Lights turn ON<br>Switch stays ON |
| 4.2.3 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON<br>Timer: ACTIVE | Other automation turns lights OFF | Lights turn OFF<br>Switch → DISABLED<br>Timer cancelled |

---

## 5. Multi-Sensor Scenarios

### 5.1 Multiple Presence Sensors
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 5.1.1 | Lights: OFF<br>Sensor1: CLEAR<br>Sensor2: CLEAR<br>Switch: ON | Sensor1 → OCCUPIED | Lights turn ON |
| 5.1.2 | Lights: ON<br>Sensor1: OCCUPIED<br>Sensor2: OCCUPIED<br>Switch: ON | Sensor1 → CLEAR<br>Sensor2 stays OCCUPIED | Lights stay ON<br>No timer |
| 5.1.3 | Lights: ON<br>Sensor1: OCCUPIED<br>Sensor2: CLEAR<br>Switch: ON | Sensor1 → CLEAR | Timer starts (30s) |
| 5.1.4 | Lights: ON<br>Sensor1: CLEAR<br>Sensor2: CLEAR<br>Switch: ON<br>Timer: ACTIVE (15s) | Sensor2 → OCCUPIED | Timer cancelled<br>Lights stay ON |

### 5.2 Multiple Light Entities
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 5.2.1 | Light1: OFF<br>Light2: OFF<br>Occupancy: CLEAR<br>Switch: ON | Occupancy → OCCUPIED | Both lights turn ON |
| 5.2.2 | Light1: ON<br>Light2: ON<br>Occupancy: OCCUPIED<br>Switch: ON | Occupancy → CLEAR<br>Wait 30s | Both lights turn OFF |
| 5.2.3 | Light1: ON<br>Light2: ON<br>Occupancy: OCCUPIED<br>Switch: ON | User turns off Light1 only | Light1 → OFF<br>Switch → DISABLED<br>(Partial change treated as manual) |
| 5.2.4 | Light1: OFF<br>Light2: ON<br>Occupancy: OCCUPIED<br>Switch: ON | User turns off Light2 | Light2 → OFF<br>Switch → DISABLED |

---

## 6. Timer Edge Cases

### 6.1 Timer Interruptions
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 6.1.1 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON<br>Timer: ACTIVE (10s) | Occupancy → OCCUPIED | Timer cancelled<br>Lights stay ON |
| 6.1.2 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON<br>Timer: ACTIVE (10s) | User toggles switch OFF | Timer cancelled<br>Lights stay ON<br>Switch → DISABLED |
| 6.1.3 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON<br>Timer: ACTIVE (10s) | User manually turns lights OFF | Timer cancelled<br>Lights → OFF<br>Switch → DISABLED |

### 6.2 Timer with Different Delays
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 6.2.1 | Delay: 0s<br>Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | Occupancy → CLEAR | Lights turn OFF immediately<br>No timer wait |
| 6.2.2 | Delay: 300s<br>Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | Occupancy → CLEAR<br>Wait 299s<br>Occupancy → OCCUPIED | Timer cancelled at 299s<br>Lights stay ON |
| 6.2.3 | Delay: 60s<br>Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | Occupancy → CLEAR<br>Wait 60s | Lights turn OFF after 60s |

---

## 7. Rapid State Change Tests

### 7.1 Rapid Occupancy Changes
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 7.1.1 | Lights: OFF<br>Occupancy: CLEAR<br>Switch: ON | Occupancy → OCCUPIED<br>Immediately → CLEAR | Lights turn ON<br>Timer starts immediately |
| 7.1.2 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON<br>Timer: ACTIVE (1s) | Occupancy → OCCUPIED<br>→ CLEAR<br>→ OCCUPIED | Timer cancelled<br>Timer cancelled again<br>Lights stay ON |
| 7.1.3 | Lights: OFF<br>Occupancy: CLEAR<br>Switch: ON | Occupancy → OCCUPIED (5x rapid) | Lights turn ON once<br>Stay ON |

### 7.2 Rapid Manual Control
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 7.2.1 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: ON | User: ON → OFF → ON → OFF | Final: Lights OFF<br>Switch DISABLED |
| 7.2.2 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON | User: OFF → ON<br>(within 1 second) | Switch: DISABLED → ENABLED<br>Timer starts |

### 7.3 Rapid Switch Toggling
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 7.3.1 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | Toggle: OFF → ON → OFF → ON | Final: Switch ON<br>Lights stay ON |
| 7.3.2 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: OFF | Toggle: ON → OFF → ON | Final: Switch ON<br>Lights turn ON |

---

## 8. Context Detection Tests

### 8.1 Distinguishing Manual vs. Automation
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 8.1.1 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: ON | This automation turns lights ON | Treated as automation<br>Switch stays ON |
| 8.1.2 | Lights: ON<br>Occupancy: CLEAR<br>Switch: ON | This automation turns lights OFF | Treated as automation<br>Switch stays ON |
| 8.1.3 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: ON | User UI: turns lights ON | Treated as manual<br>Switch stays ON |
| 8.1.4 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | User UI: turns lights OFF | Treated as manual<br>Switch → DISABLED |
| 8.1.5 | Lights: OFF<br>Occupancy: OCCUPIED<br>Switch: ON | Other automation: turns lights ON | Context has parent_id<br>Switch stays ON |
| 8.1.6 | Lights: ON<br>Occupancy: OCCUPIED<br>Switch: ON | Other automation: turns lights OFF | Context has parent_id<br>Switch → DISABLED |

---

## 9. Integration Lifecycle Tests

### 9.1 Entry Setup/Teardown
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 9.1.1 | Integration not loaded | Add integration | Entry created<br>Switch entity created<br>Listeners registered |
| 9.1.2 | Integration loaded<br>Lights: ON<br>Occupancy: OCCUPIED | Remove integration | Listeners removed<br>Lights stay ON<br>Entity removed |
| 9.1.3 | Integration loaded<br>Timer: ACTIVE | Remove integration | Timer cancelled<br>Listeners removed |

### 9.2 Configuration Updates
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 9.2.1 | Running with Light1, Light2<br>Delay: 30s | Update: add Light3 | All 3 lights controlled<br>Automation continues |
| 9.2.2 | Running with Sensor1<br>Delay: 30s | Update: add Sensor2 | Both sensors monitored<br>Automation continues |
| 9.2.3 | Running<br>Delay: 30s<br>Timer: ACTIVE (15s) | Update: Delay → 60s | Existing timer uses old value<br>New timers use 60s |
| 9.2.4 | Running<br>Delay: 30s | Update: Delay → 0s | Future timers turn off immediately |

### 9.3 Home Assistant Restart
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 9.3.1 | Switch: ON<br>Lights: ON<br>Occupancy: OCCUPIED | HA restarts | Switch defaults to ON<br>Automation resumes |
| 9.3.2 | Switch: OFF<br>Lights: ON<br>Occupancy: OCCUPIED | HA restarts | Switch defaults to ON<br>Lights stay as-is |
| 9.3.3 | Timer: ACTIVE (15s remaining) | HA restarts | Timer lost<br>Automation re-evaluates state |

---

## 10. Error & Recovery Tests

### 10.1 Missing Entities
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 10.1.1 | Light entity deleted<br>Switch: ON | Occupancy → OCCUPIED | Error logged<br>Service call fails gracefully |
| 10.1.2 | Sensor entity deleted<br>Switch: ON | Check occupancy | Error logged<br>Treats as "no occupancy" |
| 10.1.3 | Light entity unavailable<br>Switch: ON | Automation tries to turn on | Service call fails<br>No crash |

### 10.2 Invalid States
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 10.2.1 | Light state: "unavailable"<br>Switch: ON | Occupancy → OCCUPIED | Attempts to turn on<br>May fail gracefully |
| 10.2.2 | Sensor state: "unknown"<br>Switch: ON | Check occupancy | Treated as CLEAR |
| 10.2.3 | Sensor state: null<br>Switch: ON | Check occupancy | Treated as CLEAR |

---

## 11. Multiple Integration Instances

### 11.1 Independent Rooms
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 11.1.1 | Living Room: ON<br>Bedroom: OFF | LR Occupancy → CLEAR | LR timer starts<br>Bedroom unaffected |
| 11.1.2 | Living Room: Switch ON<br>Bedroom: Switch ON | Toggle LR switch OFF | LR disabled<br>Bedroom still active |
| 11.1.3 | Living Room: using Light1<br>Bedroom: using Light2 | LR Occupancy → OCCUPIED | Light1 turns ON<br>Light2 unaffected |

### 11.2 Overlapping Entities (Anti-Pattern)
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 11.2.1 | LR controls Light1<br>BR controls Light1 | LR Occupancy → OCCUPIED | LR turns Light1 ON<br>BR may also respond |
| 11.2.2 | LR controls Light1<br>BR controls Light1<br>Both switches: ON | User turns Light1 OFF | Both switches → DISABLED |

---

## 12. Attribute Verification Tests

### 12.1 State Attributes
| # | Initial State | Event | Expected Outcome |
|---|--------------|-------|------------------|
| 12.1.1 | Lights: OFF<br>Occupancy: CLEAR | Check attributes | `any_light_on: false`<br>`any_occupied: false` |
| 12.1.2 | Lights: ON<br>Occupancy: OCCUPIED | Check attributes | `any_light_on: true`<br>`any_occupied: true` |
| 12.1.3 | Light1: ON<br>Light2: OFF<br>Sensor1: OCCUPIED<br>Sensor2: CLEAR | Check attributes | `any_light_on: true`<br>`any_occupied: true` |
| 12.1.4 | Configuration updated | Check attributes | `lights`, `sensors`, `off_delay` reflect new config |

---

## Test Execution Strategy

### Priority Levels

**P0 - Critical (Must Pass)**
- Tests 1.1.1, 1.3.2, 2.1.1, 2.2.2, 3.2.1, 3.2.3
- Basic on/off, timer expiration, manual override

**P1 - High (Should Pass)**
- All section 1, 2, 3 tests
- Multi-sensor/light scenarios (5.1.1-5.2.2)
- Context detection (8.1.1-8.1.6)

**P2 - Medium (Important)**
- Timer edge cases (6.1, 6.2)
- Rapid state changes (7.1, 7.2)
- Lifecycle tests (9.1, 9.2)

**P3 - Low (Nice to Have)**
- Error handling (10.1, 10.2)
- Multi-instance (11.1)
- Attribute verification (12.1)

### Test Implementation Approach

1. **Unit Tests**: Individual coordinator methods
2. **Integration Tests**: Full automation flow with mocked HA
3. **System Tests**: Real Home Assistant test instance
4. **Manual Tests**: Real hardware validation

### Coverage Goals

- **Code Coverage**: >90%
- **Branch Coverage**: >85%
- **Scenario Coverage**: 100% of test matrix
- **Edge Case Coverage**: All identified scenarios

---

## Expected Test Count Summary

| Category | Test Count |
|----------|------------|
| Basic Occupancy | 11 |
| Manual Light Control | 14 |
| Switch Toggle | 8 |
| Automation Triggers | 6 |
| Multi-Sensor/Light | 9 |
| Timer Edge Cases | 9 |
| Rapid Changes | 8 |
| Context Detection | 6 |
| Lifecycle | 12 |
| Error Handling | 6 |
| Multi-Instance | 5 |
| Attributes | 4 |
| **TOTAL** | **98 tests** |

---

## Notes for Test Implementation

### Mocking Strategy
- Mock `hass.states.get()` for entity states
- Mock `hass.services.async_call()` for light control
- Mock `async_track_state_change_event()` for event listeners
- Mock time/delays for timer tests

### Test Data Fixtures
```python
FIXTURE_ENTRY_DATA = {
    CONF_ROOM_NAME: "Living Room",
    CONF_PRESENCE_SENSORS: ["binary_sensor.living_room_motion"],
    CONF_OFF_DELAY: 30,
    CONF_CONTROLLED_ENTITIES: [
        {
            CONF_ENTITY_ID: "light.living_room",
            CONF_PRESENCE_DETECTED_SERVICE: DEFAULT_DETECTED_SERVICE,
            CONF_PRESENCE_CLEARED_SERVICE: DEFAULT_CLEARED_SERVICE,
            CONF_PRESENCE_DETECTED_STATE: DEFAULT_DETECTED_STATE,
            CONF_PRESENCE_CLEARED_STATE: DEFAULT_CLEARED_STATE,
            CONF_RESPECTS_PRESENCE_ALLOWED: True,
            CONF_DISABLE_ON_EXTERNAL_CONTROL: True,
            CONF_INITIAL_PRESENCE_ALLOWED: True,
        }
    ],
}
```

### Assertion Helpers
- `assert_lights_on(entities)`
- `assert_lights_off(entities)`
- `assert_switch_enabled()`
- `assert_switch_disabled()`
- `assert_timer_active()`
- `assert_timer_cancelled()`

### Time Simulation
- Use `async_fire_time_changed()` for timer expiration
- Use `freezegun` for time-dependent tests
