"""Read, edit, create and apply thermostat schedules through the Nexia/Trane Home mobile API.

The `nexia` library doesn't cover schedules, so this talks to the endpoints the mobile app
uses, reusing a logged-in `NexiaHome` for auth (its helpers re-login on a 302).

How the API behaves (see docs/schedule-api.md):
  * A zone's "schedule" feature links the saved-schedule collection and two thermostat actions.
  * Saved schedules live in the cloud. Creating (POST) or editing (PUT) one does NOT change
    what the thermostat runs - it has to be applied with set_active_schedule.
  * get/set_active_schedule are asynchronous: they return a polling_path that answers
    `null` until the thermostat replies.
  * The active schedule comes back as contents only (no name/ID); match it by contents.
"""
from __future__ import annotations

import asyncio
import copy
import re
from typing import Any

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
DAY_GROUPS = {
    "all": DAYS,
    "weekdays": DAYS[:5],
    "weekend": DAYS[5:],
}


class ScheduleError(Exception):
    pass


# --- pure helpers (no network) ---------------------------------------------------------

def fmt_time(minutes: int) -> str:
    """405 -> '6:45 AM'."""
    h, m = divmod(int(minutes), 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def parse_time(text: str) -> int:
    """'6:45am', '6:45 PM', '18:30', '7pm' -> minutes after midnight."""
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*", text, re.I)
    if not m:
        raise ScheduleError(f"Can't read time {text!r} (try 6:45am or 18:45)")
    h, mins, ampm = int(m[1]), int(m[2] or 0), (m[3] or "").lower()[:1]
    if ampm:
        if not 1 <= h <= 12:
            raise ScheduleError(f"Bad hour in {text!r}")
        h = h % 12 + (12 if ampm == "p" else 0)
    if not (0 <= h < 24 and 0 <= mins < 60):
        raise ScheduleError(f"Bad time {text!r}")
    return h * 60 + mins


def whole(v: float) -> float | int:
    """72.0 -> 72, so Fahrenheit setpoints are stored the way the app stores them."""
    return int(v) if float(v).is_integer() else v


def parse_days(spec: str | None) -> list[str]:
    """'weekdays', 'weekend', 'all', or a comma list like 'mon,wed,sat'."""
    if not spec:
        return list(DAYS)
    out: list[str] = []
    for part in spec.split(","):
        p = part.strip().lower()
        if p in DAY_GROUPS:
            out += DAY_GROUPS[p]
        elif p[:3].upper() in DAYS:
            out.append(p[:3].upper())
        else:
            raise ScheduleError(f"Unknown day {part!r}; use mon..sun, weekdays, weekend, all")
    return [d for d in DAYS if d in out]


def edit_days(days: list[dict], *, day_names: list[str], periods: list[str] | None = None,
              time: int | None = None, heat: float | None = None,
              cool: float | None = None) -> tuple[list[dict], int]:
    """Return (new days, number of periods whose values changed). `periods` matches names case-insensitively;
    None means every period on the chosen days. Does not modify the input."""
    days = copy.deepcopy(days)
    wanted = {p.lower() for p in periods} if periods else None
    changed = 0
    seen: set[str] = set()
    for d in days:
        if d["name"] not in day_names:
            continue
        for p in d["periods"]:
            if wanted is not None and p["name"].lower() not in wanted:
                continue
            seen.add(p["name"].lower())
            before = copy.deepcopy(p)
            if time is not None:
                p["time"] = time
            if heat is not None:
                p["value"]["heat"] = whole(heat)
            if cool is not None:
                p["value"]["cool"] = whole(cool)
            changed += p != before
        d["periods"].sort(key=lambda p: p["time"])
    if wanted is not None and wanted - seen:
        raise ScheduleError(f"No period named {sorted(wanted - seen)} on {', '.join(day_names)}")
    return days, changed


def validate_days(days: list[dict], *, limits: tuple[float, float] | None = None,
                  deadband: float | None = None) -> None:
    """Raise ScheduleError if any setpoint is out of range or heat/cool are too close."""
    for d in days:
        times = [p["time"] for p in d["periods"]]
        if len(times) != len(set(times)):
            raise ScheduleError(f"{d['name']}: two periods start at the same time")
        for p in d["periods"]:
            heat, cool = p["value"].get("heat"), p["value"].get("cool")
            where = f"{d['name']} {p['name']}"
            if limits:
                for v in (heat, cool):
                    if v is not None and not limits[0] <= v <= limits[1]:
                        raise ScheduleError(f"{where}: {v} is outside limits {limits[0]}-{limits[1]}")
            if deadband is not None and heat is not None and cool is not None \
                    and cool - heat < deadband:
                raise ScheduleError(f"{where}: heat {heat} / cool {cool} are closer than the "
                                    f"thermostat's {deadband}° deadband")


def clean_days(days: list[dict]) -> list[dict]:
    """Copy of days with whole-number setpoints sent as ints."""
    days = copy.deepcopy(days)
    for d in days:
        for p in d["periods"]:
            p["value"] = {k: whole(v) if isinstance(v, (int, float)) else v
                          for k, v in p["value"].items()}
    return days


def same_days(a: list[dict], b: list[dict]) -> bool:
    """Compare schedules by what the thermostat would do (day, start time, setpoints)."""
    def norm(days):
        return {d["name"]: sorted((p["time"], p["value"].get("heat"), p["value"].get("cool"))
                                  for p in d["periods"]) for d in days}
    return norm(a) == norm(b)


def schedule_id(item: dict) -> str:
    """ID from a saved schedule's self link (.../mobile/schedules/123456?house_id=...)."""
    return item["_links"]["self"]["href"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]


# --- API ------------------------------------------------------------------------------

class Schedules:
    """Schedules for one zone. `zone_json` is the zone's entry from the house/device JSON."""

    def __init__(self, home, zone_json: dict, poll_timeout: float = 60):
        self.home = home
        feat = next((f for f in zone_json.get("features", []) if f.get("name") == "schedule"), None)
        if not feat:
            raise ScheduleError("This zone has no schedule feature")
        self.collection_url = self._url(feat["collection_url"])
        self.actions = {k: self._url(v["href"]) for k, v in feat.get("actions", {}).items()}
        self.poll_timeout = poll_timeout

    def _url(self, href: str) -> str:
        # Links point at www.mynexia.com; the brand's own host serves the same paths.
        path = re.sub(r"^https?://[^/]+", "", href)
        return self.home.root_url + path

    async def _json(self, resp) -> Any:
        async with resp:
            data = await resp.json(content_type=None)
        if isinstance(data, dict) and data.get("success") is False:
            raise ScheduleError(f"API error: {data.get('error')}")
        return data

    async def _poll(self, data: dict) -> dict:
        """Wait for the thermostat's answer to an asynchronous action."""
        path = (data.get("result") or {}).get("polling_path")
        if not path:
            raise ScheduleError(f"Expected a polling_path, got {data!r}")
        url = self._url(path)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.poll_timeout
        while loop.time() < deadline:
            await asyncio.sleep(2)
            ans = await self._json(await self.home._get_url(url))
            if ans:
                if ans.get("status") != "success":
                    raise ScheduleError(f"Thermostat reported: {ans}")
                return ans.get("options") or {}
        raise ScheduleError(f"Thermostat didn't answer within {self.poll_timeout:.0f}s")

    async def list(self) -> list[dict]:
        data = await self._json(await self.home._get_url(self.collection_url))
        return data["result"]["items"]

    async def find(self, name_or_id: str) -> dict:
        items = await self.list()
        hit = [i for i in items if name_or_id in (i["name"], schedule_id(i))] or \
              [i for i in items if i["name"].lower() == name_or_id.lower()]
        if len(hit) != 1:
            names = ", ".join(repr(i["name"]) for i in items)
            raise ScheduleError(f"{'No' if not hit else 'More than one'} schedule {name_or_id!r}; "
                                f"have {names}")
        return hit[0]

    async def create(self, name: str, days: list[dict], temperature_scale: str) -> dict:
        if any(i["name"] == name for i in await self.list()):
            raise ScheduleError(f"A schedule named {name!r} already exists")
        body = {"name": name, "temperature_scale": temperature_scale, "days": clean_days(days)}
        await self._json(await self.home.post_url(self.collection_url, body))
        return await self.find(name)  # the create response has no ID; re-read to get the link

    async def save(self, item: dict) -> dict:
        """Save an edited schedule (cloud only; call apply() to send it to the thermostat)."""
        body = {"name": item["name"], "temperature_scale": item["temperature_scale"],
                "days": clean_days(item["days"])}
        data = await self._json(await self.home.put_url(self._url(item["_links"]["self"]["href"]),
                                                        body))
        return data["result"]

    async def active(self) -> dict:
        """What the thermostat is running: {"temperature_scale", "days"}."""
        data = await self._json(await self.home.post_url(self.actions["get_active_schedule"], {}))
        return await self._poll(data)

    async def apply(self, item: dict, verify: bool = True) -> None:
        """Send a schedule to the thermostat, then read it back to confirm."""
        body = {"temperature_scale": item["temperature_scale"], "days": clean_days(item["days"])}
        data = await self._json(await self.home.post_url(self.actions["set_active_schedule"], body))
        await self._poll(data)
        if verify and not same_days((await self.active())["days"], item["days"]):
            raise ScheduleError("Thermostat accepted the schedule but reads back different values")

    async def which_active(self, items: list[dict] | None = None) -> tuple[dict, list[dict]]:
        """(active schedule contents, saved schedules whose contents match it)."""
        act = await self.active()
        items = items if items is not None else await self.list()
        return act, [i for i in items if same_days(i["days"], act["days"])]
