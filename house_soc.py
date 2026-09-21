"""House state of charge from the three DALY BMSes, with a freshness check.

The BMS workers keep the last complete status of every battery, also when the Bluetooth link is down. Float protection
must not treat such an old value as the current state of charge, so every reading carries its age here.
"""
from datetime import datetime, timezone

BATTERIES = ("BATTERY 1", "BATTERY 2", "BATTERY 3")
MIN_MAX_AGE_SECONDS = 120.0
AGE_FACTOR = 4          # a reading counts as fresh for this many refresh intervals (but at least MIN_MAX_AGE_SECONDS)


def max_age_seconds(refresh_interval):
    try:
        interval = float(refresh_interval)
    except (TypeError, ValueError):
        interval = 30.0
    return max(MIN_MAX_AGE_SECONDS, AGE_FACTOR * interval)


def reading_age(value, now):
    """Seconds since the status was captured, or None when the timestamp is missing or unreadable."""
    stamp = value.get("captured_at")
    if not stamp:
        return None
    try:
        captured = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    return max(0.0, now - captured.timestamp())


def house_soc(batteries, now, max_age):
    """Average of every valid *fresh* SOC. `soc` is None when no battery has a fresh reading.

    `last_known_soc` is the average of the valid but too old readings; it is informational and is never used to start
    or stop charging.
    """
    fresh, stale, ages = [], [], {}
    for name in BATTERIES:
        value = batteries.get(name, {})
        try:
            soc = float(value.get("state_of_charge_percent"))
        except (TypeError, ValueError):
            continue
        if not 0 <= soc <= 100:
            continue
        age = reading_age(value, now)
        ages[name] = None if age is None else round(age, 1)
        (fresh if age is not None and age <= max_age else stale).append(soc)
    average = lambda values: sum(values) / len(values) if values else None
    known = [age for age in ages.values() if age is not None]
    return {
        "soc": average(fresh),
        "fresh_batteries": len(fresh),
        "stale_batteries": len(stale),
        "last_known_soc": average(fresh + stale),
        "youngest_age_seconds": min(known) if known else None,
        "oldest_age_seconds": max(known) if known else None,
        "max_age_seconds": max_age,
        "ages": ages,
    }


def float_decision(enabled, latched, soc, threshold, bulk_threshold):
    """(active, resume, held): what Float protection does with the current SOC.

    `held` means the SOC is unknown or too old while Float is latched: the sources stay under Float control and Bulk
    is never resumed on old data. Resuming Bulk needs a fresh SOC at or below the resume threshold.
    """
    resume = bool(enabled and latched and soc is not None and soc <= bulk_threshold)
    active = bool(enabled and soc is not None and soc >= threshold and not resume)
    held = bool(enabled and latched and soc is None)
    return active or held, resume, held
