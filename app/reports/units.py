"""Human-friendly display units: 36382971 bps → 36.38 Mbit/s, 1450 µs → 1.45 ms, 0.96 logical → 96 %.

One unit is chosen per measurement for the whole report, from the typical magnitude of its
values, so every object of that measurement is shown in the same unit and stays comparable.
"""

# MultiProbe unit (lower-case) -> (family, how many base units one unit is)
_FAMILY = {
    "bps": ("bitrate", 1), "bit/s": ("bitrate", 1), "kbps": ("bitrate", 1e3),
    "µs": ("time", 1), "us": ("time", 1), "ms": ("time", 1e3), "s": ("time", 1e6),
    "byte": ("size", 1), "bytes": ("size", 1), "mb": ("size", 1024 ** 2),
}

# family -> display steps, largest first: (base units per display unit, label)
_STEPS = {
    "bitrate": [(1e9, "Gbit/s"), (1e6, "Mbit/s"), (1e3, "kbit/s"), (1, "bit/s")],
    "time": [(1e6, "s"), (1e3, "ms"), (1, "µs")],
    "size": [(1024 ** 3, "GB"), (1024 ** 2, "MB"), (1024, "KB"), (1, "B")],
}

LOGICAL = {"logical"}  # 0/1 values: the average is the share of time the flag was set


def choose(unit: str, magnitude: float | None) -> tuple[str, float]:
    """(display label, multiplier): display value = raw value × multiplier."""
    key = (unit or "").strip().lower()
    if key in LOGICAL:
        return "%", 100.0
    if key not in _FAMILY:
        return unit or "", 1.0
    family, base = _FAMILY[key]
    steps = _STEPS[family]
    if magnitude is None or magnitude == 0:
        # No data: keep a sensible unit of the same family instead of the raw one.
        size, label = next((s for s in steps if s[0] == base), steps[-1])
        return label, base / size
    value = abs(magnitude) * base
    size, label = next(((s, lbl) for s, lbl in steps if value >= s), steps[-1])
    return label, base / size
