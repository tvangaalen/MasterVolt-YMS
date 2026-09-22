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


def cell_voltage_stats(batteries, now, max_age):
    """Highest single cell (mV) and worst per-battery spread (mV), across every *fresh* battery.

    Three parallel house batteries do not necessarily reach a high state of charge together: one battery's
    coulomb-counted SOC can already read 100% - with one of its cells already inside DALY's own high-voltage
    alarm band - while the average of all three (house_soc() above) is still far below the Float threshold.
    Analysis of the stored history (see CHANGELOG 1.13.0) found exactly this: 44 cell-voltage/MOSFET-cutoff
    events in 10 days, most while the average House SOC was 58-91%, i.e. well under the 95% Float trigger.
    This is what the cell-voltage Float trigger protects against; `max_spread_mv` is informational only
    (shown in the UI, never forces Float) since it can fire on a brief, harmless imbalance mid-charge.
    """
    max_cell = max_cell_battery = max_spread = max_spread_battery = None
    for name in BATTERIES:
        value = batteries.get(name, {})
        age = reading_age(value, now)
        if age is None or age > max_age:
            continue
        cells = value.get("cells_mv") or []
        if not cells:
            continue
        cell = max(cells)
        if max_cell is None or cell > max_cell:
            max_cell, max_cell_battery = cell, name
        spread = value.get("cell_spread_mv")
        if spread is None and len(cells) > 1:
            spread = max(cells) - min(cells)
        if spread is not None and (max_spread is None or spread > max_spread):
            max_spread, max_spread_battery = spread, name
    return {
        "max_cell_mv": max_cell,
        "max_cell_battery": max_cell_battery,
        "max_spread_mv": max_spread,
        "max_spread_battery": max_spread_battery,
    }


def float_decision(enabled, latched, soc, threshold, bulk_threshold, max_cell_mv=None, cell_trigger_mv=None, cell_resume_mv=None):
    """(active, resume, held, trigger): what Float protection does with the current SOC and highest cell voltage.

    Float starts when EITHER the average SOC reaches `threshold` OR the highest single cell (of any battery)
    reaches `cell_trigger_mv` - a single divergent cell can enter the danger zone long before the pack average
    does. Bulk resumes only once BOTH are back within their safe range; if either is unknown (stale link) while
    latched, Float is held rather than resumed blind, and an unknown value never starts Float by itself.
    `trigger` explains why Float is active: "soc", "cell_voltage", "soc+cell_voltage", "held" or None.
    """
    soc_hot = soc is not None and soc >= threshold
    cell_hot = max_cell_mv is not None and cell_trigger_mv is not None and max_cell_mv >= cell_trigger_mv
    resume = bool(
        enabled and latched and soc is not None and soc <= bulk_threshold
        and (cell_trigger_mv is None or (max_cell_mv is not None and max_cell_mv <= cell_resume_mv))
    )
    unknown = soc is None or (cell_trigger_mv is not None and max_cell_mv is None)
    held = bool(enabled and latched and unknown and not resume)
    active = bool(enabled and not resume and (soc_hot or cell_hot or held))
    trigger = None
    if active:
        trigger = "held" if held else "soc+cell_voltage" if soc_hot and cell_hot else "cell_voltage" if cell_hot else "soc"
    return active, resume, held, trigger
