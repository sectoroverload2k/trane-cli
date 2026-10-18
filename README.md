# trane-cli

Command-line client for Trane Home, Nexia and Asair thermostats. Supports status, setpoint,
mode, fan, hold and preset control, runtime history, event logs, and creating, editing and
applying schedules.

Schedule support is not available in the [`nexia`](https://github.com/hvaclibs/nexia) library;
this project implements it directly against the mobile API (see
[docs/schedule-api.md](docs/schedule-api.md)).

Tested with a Trane XL824. The XL824 exposes no local API; all requests go to the Trane Home
cloud service used by the mobile app.

## Requirements

- Python 3.11 or later
- A Trane Home, Nexia or Asair account

## Installation

```sh
git clone https://github.com/sectoroverload2k/trane-cli.git
cd trane-cli
python3 -m venv .venv
.venv/bin/pip install -e .
ln -s "$PWD/.venv/bin/trane" ~/.local/bin/trane
```

## Authentication

```sh
trane login [--user EMAIL] [--password-stdin] [--brand trane|nexia|asair] [--house-id N]
```

Prompts for the account email and password, signs in, and writes the returned `mobile_id`,
API key, house ID and brand to `~/.trane_api.json` with mode `0600`. The password is not stored.
When the API key expires, commands exit with `API key was rejected (expired?). Run: trane login`.

| Option | Description |
|---|---|
| `--user EMAIL` | Account email. Prompted if omitted. |
| `--password-stdin` | Read the password from standard input instead of prompting. |
| `--brand` | Account brand. Default: `trane`, or the brand saved by the previous login. |
| `--house-id N` | House to use when the account has more than one. Default: the first house. |

Example for non-interactive use:

```sh
pass show trane | trane login --user user@example.com --password-stdin
```

## Usage

```
trane [--json] [--thermostat NAME|ID] [--zone NAME|ID] COMMAND ...
```

`--json` produces machine-readable output. `--thermostat` and `--zone` select a device; the
default is the first thermostat and its first zone.

| Command | Description |
|---|---|
| `status` | Indoor/outdoor temperature, humidity, setpoints, mode, hold state, system status |
| `list` | Thermostats and zones |
| `set TEMP` | Set the setpoint for the current mode |
| `set --heat N --cool N [--hold]` | Set heat and/or cool setpoints; `--hold` also sets a permanent hold |
| `mode auto\|heat\|cool\|off` | Set the system mode |
| `fan auto\|on\|circulate` | Set the fan mode |
| `hold` | Permanent hold at the current setpoints |
| `resume` | Return to the schedule |
| `preset NAME` | Select a preset (e.g. Home, Away, Sleep) |
| `runtime [daily\|monthly] [--no-kwh]` | Runtime hours per stage |
| `events [-n N]` | Thermostat event log (default 20 entries) |
| `raw` | Raw device JSON |
| `schedule ...` | Schedule management (below) |

Commands that change state print the resulting `status` afterwards.

## Schedules

| Command | Description |
|---|---|
| `schedule list` | Saved schedules. `*` marks the schedule whose contents match the thermostat's active schedule. |
| `schedule show [NAME\|ID]` | A saved schedule, or the thermostat's active schedule if no name is given. |
| `schedule set NAME\|ID [edit options]` | Edit a saved schedule. |
| `schedule copy SOURCE NEW_NAME [edit options]` | Create a schedule from an existing one, with optional edits. |
| `schedule apply NAME\|ID` | Send a saved schedule to the thermostat and verify it. |
| `schedule backup` | Write all saved schedules and the active schedule to a backup file. |

Edit options:

| Option | Description |
|---|---|
| `--days SPEC` | `all` (default), `weekdays`, `weekend`, or a comma-separated list such as `mon,wed,sat` |
| `--period NAMES` | Comma-separated period names, case-insensitive. Default: all periods. |
| `--time TIME` | New start time, e.g. `6:45am`, `7pm`, `18:30`. Requires `--period`. |
| `--heat N` | Heat setpoint |
| `--cool N` | Cool setpoint |
| `--apply` | Apply the result to the thermostat after saving |

Examples:

```sh
# Heat setpoint 65 for the Wake, Home and Evening periods, Monday to Friday
trane schedule set "Default" --days weekdays --period Wake,Home,Evening --heat 65

# Weekend Wake period at 8:00 AM, applied immediately
trane schedule set "Default" --days weekend --period Wake --time 8am --apply

# New schedule based on "Default" with heat 64 in every period, applied immediately
trane schedule copy "Default" "Away week" --heat 64 --apply

# Switch the thermostat to a saved schedule
trane schedule apply "Summer"
```

### Behavior

- Saved schedules are stored in the account. `set` and `copy` change only the saved copy; the
  thermostat continues to run its current schedule until `apply` or `--apply` is used.
- `apply` sends the schedule, waits for the thermostat to acknowledge it, reads the active
  schedule back, and exits non-zero if it differs from what was sent.
- The thermostat reports its active schedule by contents only, without a name or ID. `list` and
  `show` identify it by comparing contents with the saved schedules.
- Before any write, all saved schedules and the active schedule are written to
  `~/.local/share/trane-cli/backups/schedules-YYYYMMDD-HHMMSS.json`.
- Setpoints are validated against the thermostat's setpoint limits and heat/cool deadband
  before any request is sent. Unknown day or period names are rejected.
- Whole-number setpoints are sent as integers.

### Limitations

Deleting and renaming schedules, and adding or removing periods, are not implemented.

## Energy estimates

The API reports runtime hours per stage, not energy use. `runtime` adds an `est_kwh` column when
rated power per stage is configured in `~/.config/trane-cli.json`:

```json
{"kw": {"cool": 3.5, "heat_pump": 3.2, "heat_aux": 10.0}}
```

## Files

| Path | Contents | Environment override |
|---|---|---|
| `~/.trane_api.json` | Username, mobile ID, API key, house ID, brand (mode `0600`) | `TRANE_CREDS` |
| `~/.trane_device_uuid.conf` | Device UUID registered with the API | `TRANE_UUID_FILE` |
| `~/.local/share/trane-cli/backups/` | Schedule backups | `TRANE_BACKUPS` |
| `~/.config/trane-cli.json` | Rated kW per stage (optional) | |

These files contain account data and are excluded by `.gitignore`.

## Development

```sh
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

`src/trane_cli/schedules.py` depends only on an authenticated `nexia.home.NexiaHome` instance and
has no dependency on the CLI.

## License

MIT
