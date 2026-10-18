# Schedule API

Reference for the schedule endpoints of the Trane Home / Nexia mobile API, as observed with a
Trane XL824 on `www.tranehome.com` (October 2026). The same paths are served by
`www.mynexia.com` and `asairhome.com`.

## Authentication

All requests use the mobile API headers sent by the `nexia` library:

```
X-AppVersion: 6.0.0
X-AssociatedBrand: trane
X-MobileId: <mobile_id>
X-ApiKey: <api_key>
```

`mobile_id` and `api_key` are returned by `POST /mobile/accounts/sign_in`. A `302` response
indicates an expired key.

## Response envelope

Unless noted otherwise, responses have the form:

```json
{"success": true, "error": null, "result": { ... }}
```

On failure, `success` is `false` and `error` contains `{"code": <int>, "message": <string>}`.

## Endpoint discovery

Each zone object in `GET /mobile/houses/{house_id}` contains a feature named `schedule`. The
thermostat object contains an equivalent feature keyed to the thermostat's device identifier.

```json
{
  "name": "schedule",
  "collection_url": "https://www.mynexia.com/mobile/schedules?device_identifier=XxlZone-<zone_id>&house_id=<house_id>",
  "actions": {
    "get_active_schedule":  {"method": "POST", "href": "https://www.mynexia.com/mobile/thermostat_schedules/get_active_schedule?device_identifier=XxlZone-<zone_id>"},
    "set_active_schedule":  {"method": "POST", "href": "https://www.mynexia.com/mobile/thermostat_schedules/set_active_schedule?device_identifier=XxlZone-<zone_id>"},
    "get_default_schedule": {"href": "https://www.mynexia.com/mobile/thermostat_schedules/get_default_schedule?device_identifier=XxlZone-<zone_id>"},
    "enable_scheduling":    {"href": "https://www.mynexia.com/mobile/xxl_zones/<zone_id>/scheduling_enabled"}
  }
}
```

Links always use the `www.mynexia.com` host regardless of brand. The same path is valid on the
brand's host (for example `www.tranehome.com`).

## Schedule object

```json
{
  "name": "Summer",
  "temperature_scale": "f",
  "days": [
    {
      "name": "MON",
      "periods": [
        {"name": "Wake",    "time": 405,  "value": {"heat": 67, "cool": 73}},
        {"name": "Home",    "time": 570,  "value": {"heat": 68, "cool": 78}},
        {"name": "Evening", "time": 1065, "value": {"heat": 67, "cool": 74}},
        {"name": "Sleep",   "time": 1290, "value": {"heat": 66, "cool": 72}}
      ]
    }
  ],
  "_links": {"self": {"href": "https://www.mynexia.com/mobile/schedules/<schedule_id>?house_id=<house_id>"}}
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Schedule name |
| `temperature_scale` | string | `f` or `c` |
| `days[].name` | string | `MON`, `TUE`, `WED`, `THU`, `FRI`, `SAT`, `SUN` |
| `days[].periods[].name` | string | Period label |
| `days[].periods[].time` | integer | Start time in minutes after midnight (`405` = 06:45) |
| `days[].periods[].value.heat` | number | Heat setpoint |
| `days[].periods[].value.cool` | number | Cool setpoint |
| `_links.self.href` | string | Schedule URL; the last path segment is the schedule ID |

The number of periods per day varies; 1, 2 and 4 have been observed. Fahrenheit setpoints are
stored as integers. A value such as `64.0` is accepted and stored as a float, so whole-number
values should be sent as integers.

## Saved schedules

Saved schedules are stored in the account. These requests do not change the schedule the
thermostat is running.

| Operation | Request | Body | `result` |
|---|---|---|---|
| List | `GET {collection_url}` | | `{"items": [<schedule>, ...]}` |
| Read | `GET {self href}` | | `<schedule>` |
| Create | `POST {collection_url}` | `{"name", "temperature_scale", "days"}` | `<schedule>` without `_links` |
| Update | `PUT {self href}` | `{"name", "temperature_scale", "days"}` | `<schedule>` |

The create response does not include the new schedule's ID. List the collection to obtain it.

## Thermostat actions

`get_active_schedule` and `set_active_schedule` are asynchronous. Each returns a polling URL:

```json
{"success": true, "error": null,
 "result": {"polling_path": "https://www.mynexia.com/backstage/announcements/<token>"}}
```

`GET {polling_path}` with the same headers returns `null` until the thermostat responds
(typically within a few seconds), then:

```json
{"status": "success", "options": { ... }}
```

### get_active_schedule

Returns the schedule the thermostat is running.

- Request: `POST {get_active_schedule href}`, body `{}`. `GET` returns `404`.
- `options`: `{"temperature_scale": "f", "days": [...]}`

The response contains no schedule name or ID. To identify the active schedule, compare `days`
with the saved schedules.

### set_active_schedule

Sends a schedule to the thermostat.

- Request: `POST {set_active_schedule href}`, body `{"temperature_scale": "f", "days": [...]}`
- `options`: `{"message": "Schedule saved successfully."}`

The body carries the schedule contents. A body referencing a saved schedule, such as
`{"schedule_id": <id>}`, returns HTTP `500` with
`{"success": false, "error": {"code": 1, "message": "An error occurred."}}` and makes no change.

## Editing and applying a schedule

1. `GET {collection_url}` and select the schedule.
2. Modify `days`. Setpoints must fall within the thermostat's setpoint limits, and `cool - heat`
   must be at least the thermostat's deadband.
3. `PUT {self href}` to save the change.
4. `POST {set_active_schedule href}` with the modified `days`; poll until `status` is `success`.
5. `POST {get_active_schedule href}`; poll and compare the result with the submitted `days`.

## Untested endpoints

- `DELETE {self href}`
- `get_default_schedule`
- `enable_scheduling`
