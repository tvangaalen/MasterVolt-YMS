from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


SERVICE_NAME_HINTS = (
    "finish test",
    "finish setup",
    "set low point",
    "set high point",
    "shunt resistance",
    "measured current",
    "measured voltage",
    "pga",
    "gain uleft",
    "gain uright",
    "ser.nr",
    "result",
)

SAFE_CONFIG_HINTS = (
    "battery capacity",
    "battery type",
    "nominal voltage",
    "language",
)


def classify_field(name: str | None, writable: bool | None) -> str:
    n = (name or "").strip().lower()

    if any(h in n for h in SERVICE_NAME_HINTS):
        return "service"

    if writable:
        if any(h in n for h in SAFE_CONFIG_HINTS):
            return "configuration"
        return "configuration-review"

    return "monitoring"


def format_time_value(value: float | None) -> str:
    if value is None:
        return "—"

    # MasterBus devices observed so far appear to expose Time values as HHMMSS
    # encoded numerically, not as seconds. Preserve raw if it does not fit.
    try:
        iv = int(round(value))
        s = f"{iv:06d}"
        hh, mm, ss = int(s[:-4]), int(s[-4:-2]), int(s[-2:])
        if 0 <= hh <= 999 and 0 <= mm < 60 and 0 <= ss < 60:
            return f"{hh:02d}:{mm:02d}:{ss:02d}"
    except Exception:
        pass

    return f"{value:g}"


def format_date_value(value: float | None) -> str:
    if value is None:
        return "—"

    # Date encoding is not yet fully established. Show raw deliberately.
    return f"{value:g} (raw date)"


def format_field_value(viz: str | None, value: float | None, unit: str | None) -> str:
    if value is None:
        return "—"

    unit = unit or ""

    if viz in ("CheckBox", "Eventable"):
        return "On" if value != 0 else "Off"

    if viz == "Time":
        return format_time_value(value)

    if viz == "Date":
        return format_date_value(value)

    if viz == "DropDown":
        return f"option {int(round(value))}"

    if viz == "DeviceList":
        return f"device option {int(round(value))}"

    if viz == "Text":
        return "(text field; value transport not decoded yet)"

    if abs(value) >= 1000:
        body = f"{value:,.1f}"
    elif abs(value) >= 100:
        body = f"{value:.1f}"
    elif abs(value) >= 10:
        body = f"{value:.2f}"
    else:
        body = f"{value:.3f}"

    return f"{body} {unit}".strip()
