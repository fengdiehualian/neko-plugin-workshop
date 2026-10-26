
# Open Rails Copilot Plugin

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A real-time safety monitoring plugin for **Open Rails** train simulator that integrates with **N.E.K.O AI companion** to provide intelligent alerts and coaching during operation.

## Features Overview

| Capability | Description |
|------------|-------------|
| 36 Event Detection | Monitors speed limits, signals, braking, power management, coupling, doors, pantograph, and environmental conditions |
| Scene-Aware Intelligence | Automatically adjusts alert strategy based on station stop, shunting, and mainline scenarios |
| Smart Deduplication | Cooldown + global throttling + priority preemption to prevent alert fatigue |
| Circuit Breaker | Automatic protection when push delivery fails |
| Field Validation Toolkit | Built-in tools for data collection, configuration tuning, and performance analysis |
| Zero Configuration | Works out-of-box with sensible defaults; advanced users can fine-tune via `core/constants.py` |

## Architecture

```
./
├── plugin.toml              # Plugin manifest (id=openrails_copilot)
├── __init__.py              # Main plugin entry: lifecycle + polling loop
├── core/                    # Modular monitoring pipeline
│   ├── snapshot.py              # Data acquisition from Open Rails API
│   ├── detector.py              # 36 event detection rules
│   ├── arbiter.py               # Alert arbitration: deduplication + scene awareness
│   ├── push_sender.py           # Message delivery to N.E.K.O AI
│   ├── safety_guard.py          # Circuit breaker for push failures
│   ├── event_catalog.py         # Event metadata catalog
│   └── constants.py             # Tunable parameters (thresholds, cooldowns, limits)
├── docs/                    # Comprehensive documentation
│   ├── QUICKSTART.md            # 5-minute quick start guide
│   ├── FIELD_TEST_QUICKSTART.md # Field validation quickstart
│   ├── TUNING_GUIDE.md          # Parameter tuning guide (5 categories + 6 scenarios)
│   ├── ROADMAP.md               # Validation roadmap (5 phases, 6-8 days)
│   ├── CHEAT_SHEET.md           # Quick reference card
│   ├── CONFIGURATION.md         # Configuration reference
│   ├── EVENT_CATALOG.md         # All 36 detectable events
│   └── UNDETECTABLE_EVENTS.md   # API limitations (41 events)
├── examples/                # Example scripts
│   ├── monitor_demo.py          # Standalone monitoring demo
│   ├── quick_status.py          # Quick status check
│   └── probe_or_display.py      # API data inspection
├── tests/                   # Test suite
│   ├── test_plugin.py           # Plugin integration test
│   ├── smoke_core.py            # Core module smoke test
│   └── simple_test.py           # Simple connection test
├── field_test.py            # ⭐ Field validation tool
├── config_manager.py        # ⭐ Configuration management tool
├── .github/workflows/       # CI/CD workflows
│   ├── verify.yml               # Plugin verification
│   └── release.yml              # Automated release
└── pyproject.toml           # Python project metadata
```

## Quick Start

### 1. Installation

Clone or extract this repository into N.E.K.O's plugin directory:

```bash
# Place plugin in N.E.K.O plugin directory
cp -r openrails_copilot /path/to/N.E.K.O/plugin/plugins/
```

### 2. Launch Open Rails

Start Open Rails and begin any activity (loading, driving, or menu idle). The plugin will automatically connect to `http://127.0.0.1:2150`.

### 3. Start Monitoring

The plugin runs in background polling mode (1-second interval). When safety events are detected, alerts are automatically pushed to N.E.K.O AI companion:

```text
Example alerts:
- "Speed 95 km/h, limit 80 km/h, please reduce speed"
- "Approaching red signal at 500m, prepare to stop"
- "Emergency brake applied, check for obstacles"
```

## Plugin Entries

| Entry ID | Description |
|----------|-------------|
| `monitor` | **Core**: Background monitoring (auto-start on plugin load) |
| `status` | Get current monitoring status and statistics |
| `toggle` | Start/stop monitoring manually |
| `test_push` | Send test message to verify N.E.K.O AI connection |

## Field Validation & Tuning

The plugin includes a complete validation toolkit to optimize alert parameters based on real driving data:

```bash
# Run 30-minute field test
python field_test.py --duration 1800

# Review results
cat field_test_summary.md

# Save current configuration
python config_manager.py save baseline

# After tuning, compare configurations
python config_manager.py diff baseline tuned_v1
```

For detailed validation workflow, see:
- **Quick start**: `docs/FIELD_TEST_QUICKSTART.md`
- **Tuning guide**: `docs/TUNING_GUIDE.md`
- **Full roadmap**: `docs/ROADMAP.md`

## Event Coverage

**36 Detectable Events** across 8 categories:
- Speed & Signals (8 events): Speed violations, signal approaches, SPAD prevention
- Braking (4 events): Emergency brake, insufficient braking, brake failure
- Power & Traction (6 events): Overspeed reverser, throttle on downgrade, wheelslip
- Coupling & Consist (4 events): Coupler overload, breakaway, consist integrity
- Doors & Passengers (3 events): Doors open while moving, premature departure
- Pantograph & Electrical (3 events): Pantograph state mismatches
- Environmental (4 events): Severe weather, hot bearing detection
- Operational (4 events): Fuel/water low, overfill, sander depletion

**41 API-Limited Events** documented in `docs/UNDETECTABLE_EVENTS.md`.

## Configuration

The plugin uses sensible defaults that work out-of-box. Advanced users can tune parameters in `core/constants.py`:

```python
# Example: Adjust speed violation threshold
SPEED_OVER_THRESHOLD = 5  # km/h over limit before alert

# Example: Adjust signal approach distance
SIGNAL_APPROACH_RANGE = 800  # meters before signal
```

For comprehensive parameter reference, see `docs/CONFIGURATION.md`.

## Testing

```bash
# Run unit tests
python -m pytest tests/ -v

# Smoke test core modules
python tests/smoke_core.py

# Test API connection
python tests/simple_test.py
```

## Known Limitations

- **API Dependency**: Requires Open Rails API server running on port 2150 (default)
- **Polling Latency**: 1-second polling interval may miss very brief events
- **API Coverage**: 41 events are undetectable due to API limitations (see `docs/UNDETECTABLE_EVENTS.md`)
- **Single Instance**: Monitors one Open Rails instance per N.E.K.O session

## Changelog

### v1.0.0 (Initial Release)
- **[FEAT]** 36 detectable safety events across 8 categories
- **[FEAT]** Scene-aware alert arbitration (station/shunting/mainline)
- **[FEAT]** Smart deduplication: cooldown + throttling + preemption
- **[FEAT]** Circuit breaker protection for push failures
- **[FEAT]** Field validation toolkit: `field_test.py` + `config_manager.py`
- **[DOCS]** Complete documentation: 11 markdown files covering quickstart, tuning, validation

## License

MIT License - see [LICENSE](LICENSE) for details
