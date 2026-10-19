"""Timing inventory for passive digital captures. Does not assume NX-M is SPI."""
import csv
import math
from collections import Counter
from statistics import median


def inventory(path, time_column="Time [s]"):
    last_time, last_values = None, {}
    transitions, intervals = Counter(), {}
    edge_times = {}
    rows = 0
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if time_column not in (reader.fieldnames or []):
            raise ValueError(f"Missing timestamp column {time_column!r}")
        channels = [name for name in reader.fieldnames if name != time_column]
        if not channels:
            raise ValueError("Capture has no digital channels")
        for row in reader:
            timestamp = float(row[time_column])
            if not math.isfinite(timestamp):
                raise ValueError("Timestamps must be finite")
            if last_time is not None and timestamp < last_time:
                raise ValueError("Timestamps must be non-decreasing")
            for channel in channels:
                value = row[channel].strip()
                if value not in ("0", "1"):
                    raise ValueError(f"Non-binary digital value on {channel}")
                if channel in last_values and last_values[channel] != value:
                    transitions[channel] += 1
                    if channel in edge_times:
                        intervals.setdefault(channel, []).append(timestamp - edge_times[channel])
                    edge_times[channel] = timestamp
                last_values[channel] = value
            last_time = timestamp
            rows += 1
    if not rows:
        raise ValueError("Empty capture")
    return {
        "schema_version": 1, "rows": rows, "protocol": "unidentified",
        "nx_m_pinout_confirmed": False,
        "channels": {name: {"transitions": transitions[name],
                            "minimum_edge_interval_s": min(intervals[name]) if intervals.get(name) else None,
                            "median_edge_interval_s": median(intervals[name]) if intervals.get(name) else None}
                     for name in channels},
    }
