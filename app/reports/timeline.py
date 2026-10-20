"""Layout for the problem timeline: tick marks and span positions as percentages of the period."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from .availability import AvailabilityRow
from .export import fmt_duration

_STEPS = [
    timedelta(minutes=m) for m in (1, 5, 10, 15, 30)
] + [timedelta(hours=h) for h in (1, 2, 3, 6, 12)] + [timedelta(days=d) for d in (1, 2, 7, 14)]
MAX_TICKS = 8
MIN_WIDTH_PCT = 0.15  # very short problems stay visible


@dataclass
class Tick:
    pct: float
    label: str


@dataclass
class Bar:
    left: float
    width: float
    level: str
    tip: str


@dataclass
class TimelineRow:
    row: AvailabilityRow
    bars: list[Bar]


def _ticks(begin: datetime, end: datetime) -> list[Tick]:
    total = (end - begin).total_seconds()
    step = next((s for s in _STEPS if total / s.total_seconds() <= MAX_TICKS), _STEPS[-1])
    # First tick on a round local time (midnight for day steps).
    day_start = begin.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (begin - day_start).total_seconds()
    sec = step.total_seconds()
    first = day_start + timedelta(seconds=-(-elapsed // sec) * sec) if sec < 86400 else (
        day_start if day_start >= begin else day_start + timedelta(days=1)
    )
    ticks, t, last_day = [], first, None
    while t <= end:
        if sec >= 86400:
            label = t.strftime("%d.%m")
        elif t.date() != last_day:
            label = t.strftime("%d.%m %H:%M")
        else:
            label = t.strftime("%H:%M")
        last_day = t.date()
        ticks.append(Tick(round(100 * (t - begin).total_seconds() / total, 3), label))
        t += step
    return ticks


def build(rows: list[AvailabilityRow], begin: datetime, end: datetime, t) -> tuple[list[Tick], list[TimelineRow]]:
    total = max((end - begin).total_seconds(), 1)
    out = []
    for r in rows:
        bars = []
        for s in r.spans:
            left = 100 * (s.begin - begin).total_seconds() / total
            width = max(100 * (s.end - s.begin).total_seconds() / total, MIN_WIDTH_PCT)
            label = t("tl_outage") if s.level == "outage" else t("tl_degraded")
            tip = (
                f"{r.name}\n{label}: {s.begin:%d.%m %H:%M:%S} – {s.end:%d.%m %H:%M:%S} "
                f"({fmt_duration(int((s.end - s.begin).total_seconds()))})\n" + ", ".join(s.alarms)
            )
            bars.append(Bar(round(left, 3), round(min(width, 100 - left), 3), s.level, tip))
        out.append(TimelineRow(r, bars))
    return _ticks(begin, end), out
