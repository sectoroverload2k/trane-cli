# Trane Home / Nexia schedule API

Notes from working this out against a Trane XL824 on `www.tranehome.com` (October 2026). The same
paths exist on `www.mynexia.com` and `asairhome.com`. All requests use the mobile API headers the
`nexia` library already sends:

```
X-AppVersion: 6.0.0
X-AssociatedBrand: trane
X-MobileId: <mobile_id from login>
X-ApiKey: <api_key from login>
```

A `302` means the key expired; log in again.

## Where the links come from

Each zone in `GET /mobile/houses/{house_id}` (and the thermostat itself) has a feature named
`schedule`:

```json
{
  "name": "schedule",
  "collection_url": "https://www.mynexia.com/mobile/schedules?device_identifier=XxlZone-<zone_id>&house_id=<house_id>",
  "actions": {
    "get_active_schedule":  {"method": "POST", "href": ".../mobile/thermostat_schedules/get_active_schedule?device_identifier=XxlZone-<zone_id>"},
    "set_active_schedule":  {"method": "POST", "href": ".../mobile/thermostat_schedules/set_active_schedule?device_identifier=XxlZone-<zone_id>"},
    "get_default_schedule": {"href": ".../mobile/thermostat_schedules/get_default_schedule?device_identifier=XxlZone-<zone_id>"},
    "enable_scheduling":    {"href": ".../mobile/xxl_zones/<zone_id>/scheduling_enabled"}
  }
}
```

Links always point at `www.mynexia.com`; the same path works on the brand's own host.

## Schedule format

```json
{
  "name": "Summer",
  "temperature_scale": "f",
  "days": [
    {"name": "MON", "periods": [
      {"name": "Wake",    "time": 405,  "value": {"heat": 67, "cool": 73}},
      {"name": "Home",    "time": 570,  "value": {"heat": 68, "cool": 78}},
      {"name": "Evening", "time": 1065, "value": {"heat": 67, "cool": 74}},
      {"name": "Sleep",   "time": 1290, "value": {"heat": 66, "cool": 72}}
    ]},
    ...  "TUE" .. "SUN"
  ],
  "_links": {"self": {"href": "https://www.mynexia.com/mobile/schedules/<schedule_id>?house_id=<house_id>"}}
}
```

- `time` is minutes after midnight (405 = 6:45 AM).
- Days can have different numbers of periods (seen: 1, 2 and 4).
- Fahrenheit setpoints are stored as integers. The server accepts `64.0` and stores it as a float,
  so send integers.

## Saved schedules (cloud only)

| | Request | Body | Response `result` |
|---|---|---|---|
| List | `GET {collection_url}` | | `{"items": [schedule, ...]}` |
| Read one | `GET {self href}` | | schedule |
| Create | `POST {collection_url}` | `{"name", "temperature_scale", "days"}` | the schedule, **without** `_links`; list again to get its ID |
| Update | `PUT {self href}` | `{"name", "temperature_scale", "days"}` | the saved schedule |

Responses are wrapped as `{"success": true, "error": null, "result": ...}`.

**None of these change the thermostat.** After a PUT the thermostat keeps running its old
values until the schedule is applied.

Not yet tried: `DELETE {self href}` (presumably deletes), `get_default_schedule`,
`enable_scheduling`.

## Thermostat (asynchronous)

Both actions return immediately with a polling path:

```json
{"success": true, "error": null,
 "result": {"polling_path": "https://www.mynexia.com/backstage/announcements/<token>"}}
```

`GET` the polling path (with the same headers). It returns `null` until the thermostat answers,
usually within a few seconds, then:

```json
{"status": "success", "options": { ... }}
```

### Read what the thermostat is running

`POST get_active_schedule` with body `{}`. `GET` (as opposed to POST) returns 404.

`options` = `{"temperature_scale": "f", "days": [...]}`, with no name or ID, so match it to a
saved schedule by contents.

### Apply a schedule

`POST set_active_schedule` with body `{"temperature_scale": "f", "days": [...]}` (the contents,
not a reference). `options` = `{"message": "Schedule saved successfully."}`.

A body of `{"schedule_id": <id>}` returns HTTP 500 `{"success": false, "error": {"code": 1,
"message": "An error occurred."}}` and changes nothing.

Read the schedule back afterwards with `get_active_schedule` to confirm it took.

## Typical edit

1. `GET {collection_url}`, pick the schedule, back it up.
2. Change `days`; check heat/cool against the thermostat's setpoint limits and deadband.
3. `PUT {self href}` to save it.
4. `POST set_active_schedule` with the new `days`, then poll.
5. `POST get_active_schedule`, poll, compare.
