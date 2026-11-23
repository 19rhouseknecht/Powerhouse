"""Open times for a job: site-review appointments and work days, worked out from the schedule that is already booked.

The rules (the office's): the crew starts between 7 and 8 AM and the day ends at 4 PM, Monday to Friday, unless the job's
description says otherwise. The AI only reads the description for exceptions (it fills a `rules` dict); every time offered
here is found in the real schedule by this module, never made up.
"""
from datetime import date, datetime, time, timedelta

DEFAULT_RULES = {
    "start_from": "07:00",        # the earliest start of the day
    "start_to": "08:00",          # the latest start of a work day
    "day_end": "16:00",           # everything is done by then
    "weekdays": [0, 1, 2, 3, 4],  # Monday = 0
    "not_before": "",             # YYYY-MM-DD: nothing before this day
    "work_days": 0,               # how many days the work takes (0 = use the form's number)
    "review_minutes": 60,         # how long a site review takes
    "why": "",                    # what the description changed, in a sentence
}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LOOK_AHEAD_DAYS = 60


def _clock(value, fallback):
    try:
        hours, minutes = (int(part) for part in str(value).split(":")[:2])
        return time(min(max(hours, 0), 23), min(max(minutes, 0), 59))
    except (TypeError, ValueError):
        return _clock(fallback, "07:00") if fallback != value else time(7, 0)


def clean_rules(raw=None):
    """The rules with every value checked; anything missing or wrong is the office's default."""
    rules = dict(DEFAULT_RULES)
    for key, value in (raw or {}).items():
        if key in rules and value not in (None, ""):
            rules[key] = value
    start_from = _clock(rules["start_from"], DEFAULT_RULES["start_from"])
    start_to = _clock(rules["start_to"], DEFAULT_RULES["start_to"])
    day_end = _clock(rules["day_end"], DEFAULT_RULES["day_end"])
    if start_to < start_from:
        start_to = start_from
    if day_end <= start_from:
        day_end = _clock(DEFAULT_RULES["day_end"], "16:00")
    weekdays = sorted({int(d) for d in rules["weekdays"] if str(d).lstrip("-").isdigit() and 0 <= int(d) <= 6}) if isinstance(rules["weekdays"], (list, tuple)) else []
    try:
        not_before = date.fromisoformat(str(rules["not_before"])) if rules["not_before"] else None
    except ValueError:
        not_before = None
    try:
        work_days = max(0, min(int(float(rules["work_days"] or 0)), 60))
    except (TypeError, ValueError):
        work_days = 0
    try:
        review_minutes = max(15, min(int(float(rules["review_minutes"] or 60)), 240))
    except (TypeError, ValueError):
        review_minutes = 60
    return {"start_from": start_from, "start_to": start_to, "day_end": day_end, "weekdays": weekdays or DEFAULT_RULES["weekdays"],
            "not_before": not_before, "work_days": work_days, "review_minutes": review_minutes, "why": str(rules["why"] or "")[:300]}


def _days(first, rules):
    day = first
    for _ in range(LOOK_AHEAD_DAYS * 2):
        if day.weekday() in rules["weekdays"]:
            yield day
        day += timedelta(days=1)


def _ampm(clock):
    return datetime.combine(date.today(), clock).strftime("%-I:%M %p")


def review_slots(rules, appointments, busy_days, today, count=3, step_minutes=60):
    """The first open site-review times: inside the working day, not over another appointment of the same person and not
    on a day they are out on a job. appointments: [(start datetime, end datetime)]. One option per day, so there's a choice."""
    first = max(today + timedelta(days=1), rules["not_before"] or today)
    length = timedelta(minutes=rules["review_minutes"])
    found = []
    for day in _days(first, rules):
        if day in busy_days:
            continue
        start = datetime.combine(day, rules["start_from"])
        last = datetime.combine(day, rules["day_end"]) - length
        while start <= last:
            end = start + length
            if not any(start < other_end and other_start < end for other_start, other_end in appointments):
                found.append({"kind": "review", "start": start.strftime("%Y-%m-%dT%H:%M"),
                              "label": f"{DAY_NAMES[day.weekday()]} {day:%b} {day.day}, {_ampm(start.time())}",
                              "detail": f"{rules['review_minutes']} min site review"})
                break
            start += timedelta(minutes=step_minutes)
        if len(found) >= count:
            break
    return found


def work_slots(rules, busy_days, today, days_needed, count=3):
    """The first runs of working days with nothing else booked for the crew: start between start_from and start_to,
    done by day_end."""
    days_needed = max(int(days_needed or 1), 1)
    first = max(today + timedelta(days=1), rules["not_before"] or today)
    open_days = [day for day in _days(first, rules) if day not in busy_days]
    working = list(_days(first, rules))
    found, used = [], set()
    for start in open_days:
        if start in used:
            continue
        run = working[working.index(start):working.index(start) + days_needed]
        if len(run) < days_needed or any(day in busy_days for day in run):
            continue
        end = run[-1]
        found.append({"kind": "work", "date": start.isoformat(), "days": days_needed, "time": rules["start_from"].strftime("%H:%M"),
                      "label": f"{DAY_NAMES[start.weekday()]} {start:%b} {start.day}" + (f" – {DAY_NAMES[end.weekday()]} {end:%b} {end.day}" if end != start else ""),
                      "detail": f"{days_needed} day{'s' if days_needed != 1 else ''}, start {_ampm(rules['start_from'])}–{_ampm(rules['start_to'])}, done by {_ampm(rules['day_end'])}"})
        used.update(run)
        if len(found) >= count:
            break
    return found
