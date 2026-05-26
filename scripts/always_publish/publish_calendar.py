"""Next upload times and 3-day special logic for Always Publish v1."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any


def _parse_hhmm(s: str) -> time | None:
    raw = str(s or "").strip()
    if not raw or ":" not in raw:
        return None
    try:
        h, m = raw.split(":", 1)
        return time(hour=int(h), minute=int(m))
    except (TypeError, ValueError):
        return None


def _combine(d: date, t: time) -> datetime:
    return datetime.combine(d, t)


def is_special_long_day(d: date, every_n: int) -> bool:
    if every_n < 1:
        every_n = 3
    epoch = date(1970, 1, 1)
    return ((d - epoch).days % every_n) == 0


def next_slot_after(now: datetime, slot_time: time) -> datetime:
    candidate = _combine(now.date(), slot_time)
    if candidate <= now:
        candidate = _combine(now.date() + timedelta(days=1), slot_time)
    return candidate


def build_calendar(cfg: dict[str, Any], *, on_date: date | None = None, now: datetime | None = None) -> dict[str, Any]:
    d = on_date or date.today()
    now = now or datetime.now()
    every_n = int(cfg.get("long_special_every_days") or 3)
    long_daily = _parse_hhmm(str(cfg.get("long_daily_time") or "10:00")) or time(10, 0)
    long_special = _parse_hhmm(str(cfg.get("long_special_time") or "16:00")) or time(16, 0)
    shorts_times_raw = cfg.get("shorts_times") or ["09:30", "13:30", "17:30", "21:30"]
    shorts_times: list[time] = []
    for st in shorts_times_raw:
        t = _parse_hhmm(str(st))
        if t:
            shorts_times.append(t)

    special_today = is_special_long_day(d, every_n)
    long_jobs_today: list[dict[str, Any]] = [
        {
            "slot": "long_daily",
            "time": long_daily.strftime("%H:%M"),
            "content_hint": "driving_1h_music_or_ferry_1h_realsound",
        }
    ]
    if special_today:
        long_jobs_today.append(
            {
                "slot": "long_special",
                "time": long_special.strftime("%H:%M"),
                "content_hint": "long_3h_special",
                "every_n_days": every_n,
            }
        )

    next_long_daily = next_slot_after(now, long_daily).isoformat()
    next_long_special: str | None = None
    if special_today:
        ns = _combine(d, long_special)
        if ns > now:
            next_long_special = ns.isoformat()
        else:
            # find next special day
            probe = d + timedelta(days=1)
            for _ in range(every_n + 2):
                if is_special_long_day(probe, every_n):
                    next_long_special = _combine(probe, long_special).isoformat()
                    break
                probe += timedelta(days=1)
    else:
        probe = d
        for _ in range(every_n + 2):
            if is_special_long_day(probe, every_n):
                ns = _combine(probe, long_special)
                if ns > now:
                    next_long_special = ns.isoformat()
                    break
            probe += timedelta(days=1)

    next_shorts = [next_slot_after(now, t).isoformat() for t in shorts_times]

    return {
        "date": d.isoformat(),
        "special_long_day": special_today,
        "long_special_every_days": every_n,
        "long_jobs_today": long_jobs_today,
        "max_daily_long": int(cfg.get("max_daily_long") or 1),
        "max_daily_shorts": int(cfg.get("max_daily_shorts") or 4),
        "next_long_daily": next_long_daily,
        "next_long_special": next_long_special,
        "next_shorts_slots": next_shorts,
        "shorts_times": [t.strftime("%H:%M") for t in shorts_times],
    }
