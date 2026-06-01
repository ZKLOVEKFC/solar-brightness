---
name: solar-brightness
description: Install and configure solar-brightness — adaptive external monitor brightness based on solar elevation + weather, for macOS Apple Silicon with DDC/CI monitors.
---

# solar-brightness

Adaptive external monitor brightness based on **solar elevation angle** and **real-time weather** — no ambient light sensor needed.

## Quick Install

```bash
git clone https://github.com/zklovekfc/solar-brightness.git
cd solar-brightness && bash install.sh
```

## Verify monitor DDC/CI support

```bash
brew install m1ddc
m1ddc display list
m1ddc display 1 get luminance
```

If this returns a brightness value (0-100), DDC/CI is supported. If not, check that your monitor has DDC/CI enabled in its OSD menu.

## How it works

1. **Auto-locate** via IP geolocation (or manual lat/lon config)
2. **Calculate solar elevation angle** using Spencer 1971 formula
3. **Fetch current weather** (cloud cover, WMO weather code) from Open-Meteo free API
4. **Compute target brightness**: `base(太陽高度角) × weather_correction(云量,天气)` → clipped to per-display hardware range
5. **Gradually adjust** via `m1ddc` DDC/CI, with max step size limits for imperceptible transitions
6. Runs every **5 minutes** via `launchd`, starts at login

## Configuration

Config file: `~/.config/solar-brightness/config.yaml`

Key parameters to tweak:
- `night_min` / `day_max` — brightness range
- `weather_effect` — 0 = ignore weather, 1 = fully weather-dependent
- `max_step_up` / `max_step_down` — transition speed
- Per-display: `min_pct`, `max_pct`, `offset`

## Common commands

```bash
python3 solar-brightness.py --status     # Show current state
python3 solar-brightness.py --once       # Run once
python3 solar-brightness.py --install    # Install launchd service
python3 solar-brightness.py --uninstall  # Remove launchd service
launchctl start com.solar-brightness     # Trigger immediately
tail -f ~/.config/solar-brightness/solar-brightness.log  # Watch logs
```

## Troubleshooting

**"No DDC/CI controllable displays found"**: The HDMI port on some Macs doesn't expose DDC/CI. Try USB-C or DisplayPort. Also verify DDC/CI is enabled in your monitor's OSD.

**Location shows wrong city**: The proxy may be routing traffic. Set `location.mode: manual` and provide your `lat`/`lon`.

**Brightness changes too aggressively**: Increase `max_step_down` or decrease `max_step_up` in config.

**Weather not updating**: Check network connectivity to `api.open-meteo.com`. Weather gracefully degrades — brightness still works without it.
