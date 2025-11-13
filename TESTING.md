# Testing Setup Documentation

## Virtual Environment

This project uses a Python virtual environment to isolate test dependencies.

### Setup Steps

1. **Create virtual environment** (one time):
   ```bash
   python3 -m venv venv
   ```

2. **Activate the virtual environment**:
   ```bash
   source venv/bin/activate
   ```

3. **Install test dependencies**:
   ```bash
   pip install -r requirements_test.txt
   ```

4. **Run tests**:
   ```bash
   pytest tests/
   ```

5. **Deactivate when done**:
   ```bash
   deactivate
   ```

### Directory Structure

```
/presence_based_lighting/
├── venv/                          # Virtual environment (gitignored)
├── custom_components/
│   └── presence_based_lighting/   # Integration code
├── tests/                         # Test files
│   ├── __init__.py
│   ├── conftest.py               # Fixtures and test utilities
│   ├── test_p0_critical.py       # P0 critical tests
│   ├── test_p1_occupancy.py      # Basic occupancy tests
│   ├── test_p1_manual_control.py # Manual control tests
│   ├── test_p1_switch.py         # Switch toggle tests
│   └── test_p1_multi_entity.py   # Multi-sensor/light tests
├── requirements_test.txt          # Test dependencies
└── TEST_PLAN.md                  # Comprehensive test plan

```

### Test Dependencies

- **pytest**: Test framework
- **pytest-asyncio**: Async test support

We're using a minimal set of dependencies to avoid complex Home Assistant installation. Tests mock the Home Assistant components directly.

### Running Specific Tests

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_p0_critical.py

# Run specific test
pytest tests/test_p0_critical.py::TestP0Critical::test_1_1_1_occupancy_detected_lights_turn_on

# Run with verbose output
pytest tests/ -v

# Run with output captured
pytest tests/ -s
```

### Expected Test Count

- **P0 Critical**: 6 tests (must pass)
- **P1 High Priority**: ~35 tests
- **Total implemented so far**: ~41 tests

### Notes

- Virtual environment directory (`venv/`) should be added to `.gitignore`
- Tests are written to validate expected behavior WITHOUT looking at the implementation
- This allows us to verify if the implementation matches the specification
