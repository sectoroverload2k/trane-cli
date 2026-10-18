#!/usr/bin/env python3
"""trane - read and control a Trane Home (Nexia) thermostat from the command line.

Uses the `nexia` library (what Home Assistant uses) with the mobile_id/api_key saved by
`trane login` in ~/.trane_api.json, so the password is only needed when the key expires.
"""
import argparse, asyncio, json, os, sys, time

import aiohttp
from nexia.const import BRAND_ASAIR, BRAND_NEXIA, BRAND_TRANE
from nexia.home import LoginFailedException, NexiaHome

from . import schedules as sch

CREDS = os.path.expanduser(os.environ.get("TRANE_CREDS", "~/.trane_api.json"))
UUID_FILE = os.path.expanduser(os.environ.get("TRANE_UUID_FILE", "~/.trane_device_uuid.conf"))
CONFIG = os.path.expanduser("~/.config/trane-cli.json")  # optional: {"kw": {"cool": 3.5, ...}}
BACKUPS = os.path.expanduser(os.environ.get("TRANE_BACKUPS", "~/.local/share/trane-cli/backups"))
BRANDS = [BRAND_TRANE, BRAND_NEXIA, BRAND_ASAIR]


class KeyExpired(Exception):
    pass


class Home(NexiaHome):
    """NexiaHome that reuses the saved key and refuses to silently re-login without a password."""

    async def login(self):
        if not self.password:
            raise KeyExpired("API key was rejected (expired?). Run: trane login")
        await super().login()


def load_creds():
    try:
        with open(CREDS) as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit("No saved key. Run: trane login")


def save_creds(c):
    fd = os.open(CREDS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(c, f, indent=2)


def load_config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


async def connect(session):
    c = load_creds()
    home = Home(session, house_id=c["house_id"], username=c["username"],
                brand=c.get("brand", BRAND_TRANE), state_file=UUID_FILE)
    home.mobile_id, home.api_key = c["mobile_id"], c["api_key"]
    await home.update()
    return home


def pick(home, thermostat=None, zone=None):
    ts = home.thermostats
    t = next((x for x in ts if thermostat in (None, x.get_name(), str(x.thermostat_id))), None)
    if not t:
        sys.exit(f"No thermostat {thermostat!r}; have {[x.get_name() for x in ts]}")
    zs = [t.get_zone_by_id(i) for i in t.get_zone_ids()]
    z = next((x for x in zs if zone in (None, x.get_name(), str(x.zone_id))), None)
    if not z:
        sys.exit(f"No zone {zone!r}; have {[x.get_name() for x in zs]}")
    return t, z


def snapshot(t, z):
    rh = t.get_relative_humidity() if t.has_relative_humidity() else None
    return {
        "thermostat": t.get_name(),
        "model": t.get_model(),
        "zone": z.get_name(),
        "unit": t.get_unit(),
        "indoor_temp": z.get_temperature(),
        "indoor_humidity_pct": round(rh * 100) if rh is not None else None,
        "outdoor_temp": t.get_outdoor_temperature() if t.has_outdoor_temperature() else None,
        "system_status": t.get_system_status(),
        "zone_status": z.get_status(),
        "mode": z.get_requested_mode(),
        "heat_setpoint": z.get_heating_setpoint(),
        "cool_setpoint": z.get_cooling_setpoint(),
        "hold": z.get_setpoint_status(),
        "preset": z.get_preset(),
        "fan_mode": t.get_fan_mode(),
        "blower_active": t.is_blower_active(),
    }


def show(data, as_json):
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    elif isinstance(data, dict):
        w = max(len(k) for k in data)
        for k, v in data.items():
            print(f"{k:<{w}}  {v}")
    else:
        print(data)


async def get_json(home, url):
    async with await home._get_url(url if url.startswith("http") else home.root_url + url) as r:
        return await r.json()


def device_json(home, t):
    return next(d for d in home.devices_json if d.get("id") == t.thermostat_id)


async def runtime(home, t, report, kw):
    feat = next(f for f in device_json(home, t)["features"] if f["name"] == "runtime_history")
    key = "get_runtime_history" if report == "daily" else "get_monthly_runtime_history"
    # The feature links point at mynexia.com; the same path works on the brand host.
    path = feat["actions"][key]["href"].split(".com", 1)[1]
    chart = (await get_json(home, path))["result"]["chart"]
    cats = chart["xAxis"]["categories"]
    rows = []
    for i, label in enumerate(cats):
        row = {"period": label}
        for s in chart["series"]:
            if any(s["data"]):
                row[s["state"]] = s["data"][i]
        row["total_hours"] = round(sum(v for k, v in row.items() if k != "period"), 1)
        if kw:
            row["est_kwh"] = round(sum(row.get(k, 0) * w for k, w in kw.items()), 1)
        rows.append(row)
    return rows


def print_table(rows):
    cols = list(dict.fromkeys(k for r in rows for k in r))
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


async def cmd_login(args):
    import getpass
    old = load_creds() if os.path.exists(CREDS) else {}
    user = args.user or input(f"Trane Home email [{old.get('username', '')}]: ").strip() \
        or old.get("username")
    if not user:
        sys.exit("An email address is required")
    pw = sys.stdin.readline().rstrip("\n") if args.password_stdin else getpass.getpass("Password: ")
    if not pw:
        sys.exit("A password is required")
    brand = args.brand or old.get("brand", BRAND_TRANE)
    async with aiohttp.ClientSession() as s:
        home = Home(s, house_id=args.house_id, username=user, password=pw, brand=brand,
                    state_file=UUID_FILE)
        try:
            await home.login()
        except (LoginFailedException, ValueError, aiohttp.ClientResponseError) as e:
            sys.exit(f"Login failed: {str(e).splitlines()[0]}")
        await home.update()
        save_creds({"username": user, "mobile_id": home.mobile_id, "api_key": home.api_key,
                    "house_id": home.house_id, "brand": brand})
        print(f"Logged in to house {home.get_name()!r} (id {home.house_id}); key saved to "
              f"{CREDS}. Thermostats: {[t.get_name() for t in home.thermostats]}")


def zone_json(home, t, z):
    return next(x for x in device_json(home, t)["zones"] if x["id"] == z.zone_id)


def backup(items, active):
    os.makedirs(BACKUPS, mode=0o700, exist_ok=True)
    path = os.path.join(BACKUPS, time.strftime("schedules-%Y%m%d-%H%M%S.json"))
    with open(path, "w") as f:
        json.dump({"saved": items, "active": active}, f, indent=1)
    return path


def print_schedule(days, unit):
    """One row per day; periods as 'Name time heat/cool'."""
    for d in days:
        cells = [f"{p['name']} {sch.fmt_time(p['time'])} "
                 f"{p['value'].get('heat', '-')}/{p['value'].get('cool', '-')}"
                 for p in d["periods"]]
        print(f"{d['name']}  " + "  |  ".join(cells))
    print(f"(heat/cool, °{unit})")


async def take_backup(api):
    print(f"Backed up current schedules to {backup(await api.list(), await api.active())}")


async def cmd_schedule(args, home, t, z):
    api = sch.Schedules(home, zone_json(home, t, z))
    limits, deadband = t.get_setpoint_limits(), t.get_deadband()
    act = args.action

    if act == "list":
        items = await api.list()
        active, matches = await api.which_active(items)
        rows = [{"name": i["name"], "id": sch.schedule_id(i), "active": i in matches} for i in items]
        if args.json:
            return show({"schedules": rows, "active_matches_saved": bool(matches)}, True)
        for r in rows:
            print(f"{'*' if r['active'] else ' '} {r['name']}  (id {r['id']})")
        if not matches:
            print("\n(the thermostat is running a schedule that matches none of these)")
        else:
            print("\n* = what the thermostat is running (matched by contents)")
        return

    if act == "show":
        if args.name:
            item = await api.find(args.name)
            days, title = item["days"], f"{item['name']} (id {sch.schedule_id(item)})"
        else:
            active, matches = await api.which_active()
            days = active["days"]
            title = "Active on thermostat" + (
                f" = {', '.join(repr(m['name']) for m in matches)}" if matches else "")
        if args.json:
            return show({"name": title, "days": days}, True)
        print(title)
        return print_schedule(days, t.get_unit())

    if act == "backup":
        items = await api.list()
        return print(f"Saved {len(items)} schedules and the active one to "
                     f"{backup(items, await api.active())}")

    if act in ("set", "copy"):
        if act == "copy":
            src = await api.find(args.source)
            target = {"name": args.name, "temperature_scale": src["temperature_scale"],
                      "days": src["days"]}
        else:
            target = await api.find(args.name)
        days, n = target["days"], 0
        if any(v is not None for v in (args.time, args.heat, args.cool)):
            periods = [p.strip() for p in args.period.split(",")] if args.period else None
            if args.time and not periods:
                sys.exit("--time needs --period (otherwise every period would start at once)")
            days, n = sch.edit_days(days, day_names=sch.parse_days(args.days), periods=periods,
                                    time=sch.parse_time(args.time) if args.time else None,
                                    heat=args.heat, cool=args.cool)
        elif act == "set":
            sys.exit("Nothing to change: give --heat, --cool and/or --time")
        sch.validate_days(days, limits=limits, deadband=deadband)
        if act == "set" and n == 0 and not args.apply:
            return print(f"{target['name']!r} already has those values; nothing to save")
        target["days"] = days
        await take_backup(api)
        if act == "copy":
            target = await api.create(args.name, days, target["temperature_scale"])
            print(f"Created {args.name!r} (id {sch.schedule_id(target)})"
                  + (f", {n} periods changed" if n else ""))
        elif n:
            await api.save(target)
            print(f"Saved {target['name']!r}: {n} period(s) changed")
        else:
            print(f"{target['name']!r} already has those values; nothing to save")
        if not args.apply:
            print("Not applied - the thermostat is still running its current schedule. "
                  f"Run: trane schedule apply {target['name']!r}")
            return print_schedule(target["days"], t.get_unit())

    if act == "apply":
        target = await api.find(args.name)
        await take_backup(api)

    sch.validate_days(target["days"], limits=limits, deadband=deadband)
    print(f"Applying {target['name']!r} to the thermostat...")
    await api.apply(target)
    print("Thermostat confirmed; it is now running:")
    print_schedule((await api.active())["days"], t.get_unit())


async def run(args):
    if args.cmd == "login":
        return await cmd_login(args)
    async with aiohttp.ClientSession() as s:
        home = await connect(s)
        t, z = pick(home, args.thermostat, args.zone)

        if args.cmd == "schedule":
            return await cmd_schedule(args, home, t, z)

        if args.cmd == "status":
            return show(snapshot(t, z), args.json)

        if args.cmd == "list":
            out = [{"thermostat": x.get_name(), "id": x.thermostat_id, "model": x.get_model(),
                    "zones": [x.get_zone_by_id(i).get_name() for i in x.get_zone_ids()]}
                   for x in home.thermostats]
            return show(out if args.json else "\n".join(map(str, out)), args.json)

        if args.cmd == "set":
            if args.temp is None and args.heat is None and args.cool is None:
                sys.exit("Give a temperature, or --heat and/or --cool")
            lo, hi = t.get_setpoint_limits()
            for v in (args.temp, args.heat, args.cool):
                if v is not None and not lo <= v <= hi:
                    sys.exit(f"{v} is outside the thermostat's limits {lo}-{hi}")
            await z.set_heat_cool_temp(heat_temperature=args.heat, cool_temperature=args.cool,
                                       set_temperature=args.temp)
            if args.hold:
                await z.set_permanent_hold()
        elif args.cmd == "mode":
            await z.set_mode(args.mode.upper())
        elif args.cmd == "fan":
            modes = t.get_fan_modes()
            match = next((m for m in modes if m.lower() == args.fan.lower()), None)
            if not match:
                sys.exit(f"Fan mode must be one of {modes}")
            await t.set_fan_mode(match)
        elif args.cmd == "hold":
            await z.set_permanent_hold()
        elif args.cmd == "resume":
            await z.call_return_to_schedule()
        elif args.cmd == "preset":
            presets = z.get_presets()
            match = next((p for p in presets if p.lower() == args.preset.lower()), None)
            if not match:
                sys.exit(f"Preset must be one of {presets}")
            await z.set_preset(match)
        elif args.cmd == "runtime":
            kw = load_config().get("kw") if not args.no_kwh else None
            rows = await runtime(home, t, args.report, kw)
            if args.json:
                return show(rows, True)
            print_table(rows)
            if not kw and not args.no_kwh:
                print(f"\n(hours only - Trane reports runtime, not energy. Add rated kW per "
                      f"stage to {CONFIG} to get est_kwh.)")
            return
        elif args.cmd == "events":
            url = device_json(home, t)["_links"]["nexia:history"]["href"]
            ev = (await get_json(home, url.split(".com", 1)[1]))["result"][: args.n]
            if args.json:
                return show(ev, True)
            for e in ev:
                print(f"{e['occurred_at']}  {e['description']}")
            return
        elif args.cmd == "raw":
            return print(json.dumps(device_json(home, t), indent=1))

        # after a change, re-read and show the resulting state
        await home.update()
        t, z = pick(home, args.thermostat, args.zone)
        show(snapshot(t, z), args.json)


def main():
    p = argparse.ArgumentParser(prog="trane", description=__doc__.splitlines()[0])
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--thermostat", help="thermostat name or id (default: first)")
    p.add_argument("--zone", help="zone name or id (default: first)")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("status", help="temps, humidity, setpoints, mode, what it's doing")
    sp.add_parser("list", help="list thermostats and zones")
    a = sp.add_parser("set", help="set temperature (e.g. 'trane set 72' or --heat 68 --cool 76)")
    a.add_argument("temp", type=float, nargs="?", help="target for the current mode")
    a.add_argument("--heat", type=float)
    a.add_argument("--cool", type=float)
    a.add_argument("--hold", action="store_true", help="also put on permanent hold")
    a = sp.add_parser("mode", help="set system mode")
    a.add_argument("mode", type=str.upper, choices=["AUTO", "HEAT", "COOL", "OFF"])
    a = sp.add_parser("fan", help="set fan mode (auto/on/circulate)")
    a.add_argument("fan")
    sp.add_parser("hold", help="permanent hold at current setpoints")
    sp.add_parser("resume", help="return to schedule")
    a = sp.add_parser("preset", help="set preset (Home/Away/Sleep...)")
    a.add_argument("preset")
    a = sp.add_parser("runtime", help="heating/cooling runtime hours (and est. kWh)")
    a.add_argument("report", nargs="?", choices=["daily", "monthly"], default="daily")
    a.add_argument("--no-kwh", action="store_true")
    a = sp.add_parser("events", help="recent thermostat event log")
    a.add_argument("-n", type=int, default=20)
    sp.add_parser("raw", help="dump the raw device JSON")
    a = sp.add_parser("login", help="log in and save a new API key (prompts for the password)")
    a.add_argument("--user", help="account email (prompted if omitted)")
    a.add_argument("--password-stdin", action="store_true",
                   help="read the password from stdin instead of prompting")
    a.add_argument("--brand", choices=BRANDS, help="app/brand the account belongs to "
                   "(default: trane, or what was saved last time)")
    a.add_argument("--house-id", type=int, help="pick a house if the account has several")

    a = sp.add_parser("schedule", help="list, show, edit, create and apply schedules")
    ss = a.add_subparsers(dest="action", required=True)
    ss.add_parser("list", help="saved schedules, marking the one the thermostat is running")
    b = ss.add_parser("show", help="show a schedule (default: what the thermostat is running)")
    b.add_argument("name", nargs="?", help="saved schedule name or id")
    ss.add_parser("backup", help="save all schedules to a JSON file")

    def edit_opts(b):
        b.add_argument("--days", help="weekdays, weekend, all (default) or e.g. mon,wed,sat")
        b.add_argument("--period", help="period name(s), e.g. Wake or Wake,Home,Evening "
                       "(default: all periods)")
        b.add_argument("--time", help="new start time for the period, e.g. 6:45am")
        b.add_argument("--heat", type=float, help="heat setpoint")
        b.add_argument("--cool", type=float, help="cool setpoint")
        b.add_argument("--apply", action="store_true",
                       help="also send it to the thermostat and confirm")
    b = ss.add_parser("set", help="edit a saved schedule")
    b.add_argument("name", help="saved schedule name or id")
    edit_opts(b)
    b = ss.add_parser("copy", help="create a new schedule from an existing one, optionally edited")
    b.add_argument("source", help="schedule to copy (name or id)")
    b.add_argument("name", help="name for the new schedule")
    edit_opts(b)
    b = ss.add_parser("apply", help="send a saved schedule to the thermostat and confirm")
    b.add_argument("name", help="saved schedule name or id")

    args = p.parse_args()
    try:
        asyncio.run(run(args))
    except (KeyExpired, sch.ScheduleError) as e:
        sys.exit(str(e))
    except aiohttp.ClientResponseError as e:
        sys.exit(f"API error {e.status}: {e.message}")


if __name__ == "__main__":
    main()
