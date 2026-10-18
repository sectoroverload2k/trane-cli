# trane-cli

Command-line control of a Trane Home (Nexia / American Standard) thermostat — including
**schedule editing**, which the Trane Home app supports but the [`nexia`](https://github.com/hvaclibs/nexia)
library (used by Home Assistant) does not yet.

Built and tested against a Trane **XL824**. The thermostat has no local API, so everything goes
through the Trane Home cloud API, the same one the mobile app uses.

## Install

Requires Python 3.11+.

```sh
git clone https://github.com/sectoroverload2k/trane-cli.git
cd trane-cli
python3 -m venv .venv
.venv/bin/pip install -e .
ln -s "$PWD/.venv/bin/trane" ~/.local/bin/trane   # or anywhere on your PATH
```

## Log in

```sh
trane login
```

Prompts for your Trane Home email and password, then saves the API key the server hands back
to `~/.trane_api.json` (mode 600). Your password is **not** stored; it's only needed again when the
key expires (the CLI will tell you to run `trane login`).

| Option | |
|---|---|
| `--user EMAIL` | skip the email prompt |
| `--password-stdin` | read the password from stdin, e.g. `pass show trane \| trane login --user me@example.com --password-stdin` |
| `--brand trane\|nexia\|asair` | for Nexia or Asair accounts (default `trane`) |
| `--house-id N` | pick a house if the account has more than one (default: the first) |

## Everyday commands

```sh
trane status                 # temps, humidity, setpoints, mode, what the system is doing
trane set 70                 # target for the current mode
trane set --heat 66 --cool 76 --hold
trane mode heat              # auto | heat | cool | off
trane fan auto               # auto | on | circulate
trane hold                   # permanent hold at current setpoints
trane resume                 # back to the schedule
trane preset Away
trane runtime monthly        # runtime hours per stage (see "Energy" below)
trane events -n 50           # thermostat event log
trane raw                    # raw device JSON
```

Add `--json` before the command for machine-readable output, and `--thermostat` / `--zone` to pick
a device when you have more than one.

## Schedules

```sh
trane schedule list                     # saved schedules; * marks what the thermostat runs
trane schedule show                     # what the thermostat is running right now
trane schedule show "Winter"            # a saved schedule
trane schedule backup                   # save everything to a JSON file
```

Edit a saved schedule (`--days` defaults to all days, `--period` to all periods):

```sh
# heat 65 for Wake/Home/Evening on weekdays
trane schedule set "My schedule" --days weekdays --period Wake,Home,Evening --heat 65

# move Saturday and Sunday wake-up to 8:00 am and apply it right away
trane schedule set "My schedule" --days weekend --period Wake --time 8am --apply
```

Create a new schedule from an existing one, with or without changes:

```sh
trane schedule copy "My schedule" "Cold week" --heat 64 --apply
```

Send a saved schedule to the thermostat:

```sh
trane schedule apply "Summer"
```

**Saving is not applying.** Saved schedules live in your Trane Home account. Editing or
creating one doesn't change what the thermostat runs until it's applied (`--apply` or
`trane schedule apply`). Applying always reads the schedule back from the thermostat and fails
loudly if it doesn't match.

Safety rails:

- Every command that writes first backs up all saved schedules plus the active one to
  `~/.local/share/trane-cli/backups/`.
- Setpoints are checked against the thermostat's limits and its heat/cool deadband before
  anything is sent.
- Day/period names are validated; a typo is an error, not a silent no-op.

Not supported yet: deleting or renaming schedules, adding or removing periods. The thermostat
reports the active schedule by contents only, so `list` marks a saved schedule active when its
contents match.

How the underlying API works is written up in [docs/schedule-api.md](docs/schedule-api.md).

## Energy

The API reports runtime hours per stage, not energy. To get an estimated kWh column from
`trane runtime`, put each stage's rated power draw in `~/.config/trane-cli.json`:

```json
{"kw": {"cool": 3.5, "heat_pump": 3.2, "heat_aux": 10.0}}
```

## Files

| Path | What | Override |
|---|---|---|
| `~/.trane_api.json` | username, mobile_id, API key, house_id, brand (mode 600) | `TRANE_CREDS` |
| `~/.trane_device_uuid.conf` | device UUID this CLI registers as | `TRANE_UUID_FILE` |
| `~/.local/share/trane-cli/backups/` | schedule backups | `TRANE_BACKUPS` |
| `~/.config/trane-cli.json` | optional kW ratings | |

None of these belong in git; `.gitignore` excludes them.

## Development

```sh
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

`src/trane_cli/schedules.py` has no CLI dependencies (it takes a logged-in `nexia.home.NexiaHome`),
so it can be contributed upstream to `nexia`.

## License

MIT
