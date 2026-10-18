import pytest

from trane_cli import schedules as sch


def day(name, *periods):
    return {"name": name, "periods": [
        {"name": n, "time": t, "value": {"heat": h, "cool": c}} for n, t, h, c in periods]}


WEEK = [day(d, ("Wake", 405, 67, 73), ("Home", 570, 68, 78), ("Sleep", 1380, 64, 70))
        for d in sch.DAYS]


@pytest.mark.parametrize("text,minutes", [
    ("6:45am", 405), ("6:45 AM", 405), ("12am", 0), ("12:30pm", 750), ("7pm", 1140),
    ("18:45", 1125), ("0:00", 0), ("23:59", 1439),
])
def test_parse_time(text, minutes):
    assert sch.parse_time(text) == minutes


@pytest.mark.parametrize("bad", ["25:00", "13pm", "6:60", "noon", ""])
def test_parse_time_rejects(bad):
    with pytest.raises(sch.ScheduleError):
        sch.parse_time(bad)


def test_fmt_time_round_trip():
    for m in (0, 405, 720, 1065, 1439):
        assert sch.parse_time(sch.fmt_time(m)) == m


def test_parse_days():
    assert sch.parse_days("weekdays") == ["MON", "TUE", "WED", "THU", "FRI"]
    assert sch.parse_days("sat,Monday") == ["MON", "SAT"]
    assert sch.parse_days(None) == sch.DAYS
    with pytest.raises(sch.ScheduleError):
        sch.parse_days("funday")


def test_edit_days_only_touches_selection():
    new, n = sch.edit_days(WEEK, day_names=["SAT", "SUN"], periods=["wake", "Home"], heat=64)
    assert n == 4
    assert WEEK[5]["periods"][0]["value"]["heat"] == 67  # input untouched
    sat = new[5]["periods"]
    assert [p["value"]["heat"] for p in sat] == [64, 64, 64]
    assert new[0] == WEEK[0]
    assert sat[0]["value"]["cool"] == 73


def test_edit_days_time_keeps_order():
    new, _ = sch.edit_days(WEEK, day_names=["MON"], periods=["Wake"], time=600)
    assert [p["name"] for p in new[0]["periods"]] == ["Home", "Wake", "Sleep"]


def test_edit_days_unknown_period():
    with pytest.raises(sch.ScheduleError):
        sch.edit_days(WEEK, day_names=["MON"], periods=["Lunch"], heat=60)


def test_validate_days():
    sch.validate_days(WEEK, limits=(55, 99), deadband=3)
    tight, _ = sch.edit_days(WEEK, day_names=["MON"], periods=["Sleep"], heat=68)
    with pytest.raises(sch.ScheduleError, match="deadband"):
        sch.validate_days(tight, deadband=3)
    with pytest.raises(sch.ScheduleError, match="limits"):
        sch.validate_days(sch.edit_days(WEEK, day_names=["MON"], heat=50)[0], limits=(55, 99))
    dup, _ = sch.edit_days(WEEK, day_names=["MON"], periods=["Home"], time=405)
    with pytest.raises(sch.ScheduleError, match="same time"):
        sch.validate_days(dup)


def test_same_days_ignores_names_and_order():
    renamed = [dict(d, periods=[dict(p, name=p["name"].upper()) for p in reversed(d["periods"])])
               for d in WEEK]
    assert sch.same_days(WEEK, renamed)
    assert not sch.same_days(WEEK, sch.edit_days(WEEK, day_names=["SUN"], cool=80)[0])


def test_schedule_id():
    item = {"_links": {"self": {"href": "https://www.mynexia.com/mobile/schedules/123456?house_id=1"}}}
    assert sch.schedule_id(item) == "123456"


def test_edit_days_counts_real_changes_and_stores_ints():
    new, n = sch.edit_days(WEEK, day_names=["MON"], heat=64.0)
    assert n == 2  # Sleep was already 64
    assert all(type(p["value"]["heat"]) is int for p in new[0]["periods"])
    _, n = sch.edit_days(new, day_names=["MON"], heat=64)
    assert n == 0
