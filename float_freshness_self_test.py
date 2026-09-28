"""Non-hardware self-test for Float protection: cell-voltage freshness and the trigger.

Part 1 tests house_soc.py (age of each DALY reading, average of fresh readings, cell-voltage stats, decision table).
Part 2 runs the real MasterBusService.enforce_high_soc_float loop with stubbed hardware access: no USB, no writes.
Part 3 checks validation of the cell-voltage settings (range, minimum gap to the resume level) and that the SOC
trigger and warning-duration setting, both removed in 1.17.0, are rejected as new input but still tolerated in an
older user_settings.json.
"""
import json
import shutil
import sys
import tempfile
import time
import types
from datetime import datetime, timezone
from pathlib import Path

try:
    import hid  # noqa: F401  (the USB HID module is only present on the boat PC)
except ImportError:
    sys.modules["hid"] = types.ModuleType("hid")

import house_soc as hs
from masterbus_service import MasterBusService

NOW = 1_800_000_000.0


def stamp(age):
    return datetime.fromtimestamp(NOW - age, timezone.utc).isoformat()


def bank(*items):
    return {f"BATTERY {i + 1}": {"state_of_charge_percent": soc, "captured_at": stamp(age)} for i, (soc, age) in enumerate(items)}


def cell_bank(*items):
    """items: (cells_mv list, age). captured_at only - state_of_charge_percent deliberately omitted (house_soc()
    and cell_voltage_stats() must work independently of each other, from the same battery dict)."""
    return {f"BATTERY {i + 1}": {"cells_mv": cells, "captured_at": stamp(age)} for i, (cells, age) in enumerate(items)}


def part1():
    limit = hs.max_age_seconds(30)
    assert limit == 120 and hs.max_age_seconds(60) == 240 and hs.max_age_seconds("x") == 120 and hs.max_age_seconds(5) == 120

    d = hs.house_soc(bank((96, 5), (98, 10), (94, 20)), NOW, limit)
    assert abs(d["soc"] - 96) < 1e-9 and d["fresh_batteries"] == 3 and d["stale_batteries"] == 0 and d["youngest_age_seconds"] == 5

    d = hs.house_soc(bank((96, 5), (98, 900), (94, 20)), NOW, limit)          # one battery silent for 15 minutes
    assert abs(d["soc"] - 95) < 1e-9 and d["fresh_batteries"] == 2 and d["stale_batteries"] == 1
    assert abs(d["last_known_soc"] - 96) < 1e-9

    d = hs.house_soc(bank((96, 500), (98, 900), (94, 700)), NOW, limit)       # everything old: no current SOC at all
    assert d["soc"] is None and d["fresh_batteries"] == 0 and d["stale_batteries"] == 3 and abs(d["last_known_soc"] - 96) < 1e-9

    b = bank((96, 5), (98, 5), (94, 5))
    b["BATTERY 2"]["captured_at"] = None                                       # no timestamp = age unknown = not fresh
    b["BATTERY 3"]["captured_at"] = "garbage"
    d = hs.house_soc(b, NOW, limit)
    assert d["soc"] == 96 and d["stale_batteries"] == 2
    b = bank((96, 5), (150, 5), (-3, 5))                                       # out-of-range values are ignored
    assert hs.house_soc(b, NOW, limit)["soc"] == 96
    assert hs.house_soc({}, NOW, limit)["soc"] is None
    naive = {"BATTERY 1": {"state_of_charge_percent": 90, "captured_at": datetime.fromtimestamp(NOW - 10, timezone.utc).replace(tzinfo=None).isoformat()}}
    assert hs.house_soc(naive, NOW, limit)["fresh_batteries"] == 1, "timestamps without a zone are treated as UTC"
    future = bank((90, -30))
    assert hs.house_soc(future, NOW, limit)["fresh_batteries"] == 1, "a small clock difference must not make a reading stale"
    print("Freshness and averaging of the house SOC: OK")

    # ---- cell_voltage_stats(): the highest single cell and the worst per-battery spread, from the same freshness gate
    banks = cell_bank(([3380, 3395, 3388, 3390], 5), ([3900, 3382, 3379, 3376], 10), ([3370, 3365, 3372, 3368], 20))
    d = hs.cell_voltage_stats(banks, NOW, limit)                               # reproduces the reported incident: one runaway cell
    assert d["max_cell_mv"] == 3900 and d["max_cell_battery"] == "BATTERY 2"
    assert d["max_spread_mv"] == 3900 - 3376 and d["max_spread_battery"] == "BATTERY 2"

    stale = cell_bank(([3900, 3382, 3379, 3376], 900), ([3370, 3365, 3372, 3368], 10))   # the hot battery's link is down
    d = hs.cell_voltage_stats(stale, NOW, limit)
    assert d["max_cell_mv"] == 3372 and d["max_cell_battery"] == "BATTERY 2", "a stale battery must not contribute its (possibly dangerous) last reading"

    assert hs.cell_voltage_stats({}, NOW, limit) == {"max_cell_mv": None, "max_cell_battery": None, "max_spread_mv": None, "max_spread_battery": None}
    pre_computed = {"BATTERY 1": {"cells_mv": [3400, 3410], "cell_spread_mv": 999, "captured_at": stamp(5)}}   # trust an existing cell_spread_mv field
    assert hs.cell_voltage_stats(pre_computed, NOW, limit)["max_spread_mv"] == 999
    print("cell_voltage_stats(): highest cell and worst spread, stale batteries excluded: OK")

    # ---- float_decision(): (active, resume, held, trigger) - purely from the highest cell voltage; the SOC
    # trigger was removed in 1.17.0 (see CHANGELOG), so the SOC average plays no part in this decision any more.
    f = hs.float_decision                                                      # (enabled, latched, max_cell_mv, cell_trigger_mv, cell_resume_mv)
    assert f(True, False, 3550, 3500, 3420) == (True, False, False, "cell_voltage"), "a cell at or above the trigger forces Float"
    assert f(True, False, 3400, 3500, 3420) == (False, False, False, None), "a normal cell (below the trigger) does not force Float"
    assert f(True, True, 3450, 3500, 3420) == (False, False, False, None), "between the trigger and the resume level nothing is forced and Bulk is not resumed"
    assert f(True, True, 3400, 3500, 3420) == (False, True, False, None), "every cell at or below the resume level ends Float"
    assert f(True, True, None, 3500, 3420) == (True, False, True, "held"), "unknown cell reading while latched: hold Float, never resume Bulk"
    assert f(True, False, None, 3500, 3420) == (False, False, False, None), "an unknown cell reading must not start Float protection"
    assert f(False, True, None, 3500, 3420) == (False, False, False, None) and f(False, True, 3600, 3500, 3420) == (False, False, False, None)
    print("Cell-voltage Float trigger: starts, resumes, and holds on an unknown reading: OK")


class Clock:
    """Stands in for stop_event: every 3 s wait becomes a few milliseconds and the loop ends after `ticks`."""

    def __init__(self, ticks, hook=None):
        self.ticks, self.count, self.hook = ticks, 0, hook

    def wait(self, seconds):
        self.count += 1
        if self.hook:
            self.hook(self.count)
        time.sleep(0.002)
        return self.count > self.ticks

    def set(self):
        self.count = 10 ** 9


def run_loop(cell_at_tick, ticks, state=3, details=True, tick_action=None, soc_at_tick=None):
    """Run the real loop. cell_at_tick(n) -> (max_cell_mv or None, battery name); this alone drives the decision
    since 1.17.0. soc_at_tick(n) -> SOC percent, purely informational (defaults to a constant 70%, deliberately
    far below the old 95% threshold, to prove the SOC average no longer affects Float protection at all).
    Returns the service and the writes."""
    service = MasterBusService()
    service.settings.update({"float_protection_enabled": True})
    service.get_settings = lambda: dict(service.settings)
    writes, current = [], {"n": 0, "state": state}
    service.cached = lambda addr, field: current["state"]

    def force_float(name, addr, command_field, commit_field, state_field):
        writes.append(("float", name))
        return {"changed": True, "state": 3}

    def force_bulk(name, addr, command_field, commit_field, state_field):
        writes.append(("bulk", name))
        return {"changed": True, "state": 1}

    service._force_float, service._force_bulk = force_float, force_bulk
    seen = []

    def hook(n):
        current["n"] = n
        if tick_action:
            tick_action(n, service, current)
        status = dict(service.float_policy_status)
        seen.append((n, status.get("soc_source"), status.get("float_latched"), status.get("soc_held")))

    soc_fn = soc_at_tick or (lambda n: 70.0)

    def getter():
        return soc_fn(current["n"])

    def detail_getter():
        soc = soc_fn(current["n"])
        max_cell_mv, battery = cell_at_tick(current["n"])
        stale = max_cell_mv is None
        return {
            "soc": soc, "fresh_batteries": 0 if stale else 3, "stale_batteries": 3 if stale else 0,
            "youngest_age_seconds": 30.0 if stale else 4.0, "last_known_soc": 96.0,
            "max_cell_mv": max_cell_mv, "max_cell_battery": battery,
        }

    service.house_soc_getter = getter
    if details:
        service.house_soc_details_getter = detail_getter
    service.stop_event = Clock(ticks, hook)
    service.set_state = lambda s: current.__setitem__("state", s)
    service.enforce_high_soc_float()
    return service, writes, seen, current


def part2():
    # a) a fresh cell at or above the trigger: Float is forced on a charging source
    s, writes, _, _ = run_loop(lambda n: (3550, "BATTERY 1"), 4, state=1)
    assert ("float", "charger_house") in writes and s.float_policy_status["soc_source"] == "daly_bms" and not s.float_policy_status["soc_held"]
    assert s.float_policy_status["trigger"] == "cell_voltage"

    # b) latched, then the cell reading goes stale while a source is charging again: Float is still forced, Bulk is not resumed
    def stale_after_three(n):
        return (3550, "BATTERY 1") if n <= 3 else (None, None)
    def source_restarts(n, service, current):                                   # the solar charger starts a new charge cycle in the hold
        if n == 6:
            current["state"] = 1
            service.float_policy_last_attempt.clear()
    s, writes, seen, _ = run_loop(stale_after_three, 12, state=3, tick_action=source_restarts)
    st = s.float_policy_status
    assert st["soc_source"] == "stale_hold" and st["soc_held"] and st["float_latched"] and st["active"], st
    assert ("float", "charger_house") in writes, "a source that restarts while the cell reading is unknown must be put back in Float"
    assert not [w for w in writes if w[0] == "bulk"], "Bulk must never be resumed on stale data"

    # c) latched, the cell reading returns cooled down: Bulk resumes on fresh data only
    def recover(n):
        return (3550, "BATTERY 1") if n <= 2 else ((None, None) if n <= 6 else (3400, "BATTERY 1"))
    s, writes, _, current = run_loop(recover, 12, state=3)
    assert ("bulk", "charger_house") in writes and not s.float_policy_status["float_latched"] and s.float_policy_status["soc_source"] == "daly_bms"

    # d) never latched (for example just after a restart) and no fresh cell reading: nothing is started
    s, writes, _, _ = run_loop(lambda n: (None, None), 6, state=1)
    assert not writes and not s.float_policy_status["float_latched"] and s.float_policy_status["max_cell_mv"] is None

    # e) no details getter at all (a service that cannot report cell voltage): Float can never be told to start,
    #    since it no longer has a plain-SOC fallback to trigger on (that was removed with the SOC trigger)
    s, writes, _, _ = run_loop(lambda n: (3550, "BATTERY 1"), 4, state=1, details=False)
    assert not writes and s.float_policy_status["max_cell_mv"] is None

    # f) protection disabled: never writes, latch cleared
    def disabled_run():
        service = MasterBusService()
        service.settings.update({"float_protection_enabled": False})
        service.get_settings = lambda: dict(service.settings)
        service.cached = lambda addr, field: 1
        calls = []
        service._force_float = lambda *a: calls.append("float")
        service.house_soc_getter = lambda: 99.0
        service.stop_event = Clock(4)
        service.enforce_high_soc_float()
        return calls, service.float_policy_status
    calls, status = disabled_run()
    assert not calls and not status["float_latched"]
    print("Float loop: a fresh hot cell acts, a stale cell holds Float without resuming Bulk, an unknown cell never starts anything: OK")

    # g) the reported failure mode this was built for (CHANGELOG 1.13.0): the pack-average SOC stays well under
    #    the old 95% threshold the whole time, but one battery's cell reaches the trigger - Float must engage on
    #    that alone. Since 1.17.0 the SOC average cannot trigger Float at all any more, so this also proves that.
    def hot_cell(n):
        return (3400, None) if n <= 2 else (3550, "BATTERY 1")
    s, writes, _, _ = run_loop(hot_cell, 6, state=1, soc_at_tick=lambda n: 70.0)
    st = s.float_policy_status
    assert st["house_soc"] == 70.0 and st["active"] and st["trigger"] == "cell_voltage", st
    assert ("float", "charger_house") in writes, "Float must engage on a hot cell even while the SOC average stays low the whole time"
    assert st["max_cell_mv"] == 3550 and st["max_cell_battery"] == "BATTERY 1"

    # h) latched on a hot cell: Bulk must not resume while the cell stays above the resume level, even though the
    #    (now irrelevant) SOC average is low the whole time
    def stays_hot(n):
        return (3550, "BATTERY 1")
    s, writes, _, _ = run_loop(stays_hot, 6, state=3, soc_at_tick=lambda n: 80.0)
    assert not [w for w in writes if w[0] == "bulk"], "must not resume to Bulk while a cell is still above the resume level"
    assert s.float_policy_status["float_latched"] and s.float_policy_status["trigger"] == "cell_voltage"

    # i) ... and does resume once the cell has cooled down too
    def cools_down(n):
        return (3550, "BATTERY 1") if n <= 3 else (3400, "BATTERY 1")
    s, writes, _, _ = run_loop(cools_down, 8, state=3)
    assert ("bulk", "charger_house") in writes and not s.float_policy_status["float_latched"]

    # j) the cell reading is exactly what latched Float; if it goes stale, hold rather than resume blind
    def cell_goes_stale(n):
        return (3550, "BATTERY 1") if n <= 2 else (None, None)
    s, writes, _, _ = run_loop(cell_goes_stale, 6, state=3)
    assert s.float_policy_status["float_latched"] and s.float_policy_status["trigger"] == "held", s.float_policy_status
    assert not [w for w in writes if w[0] == "bulk"], "must not resume while the cell reading that latched Float has gone stale"
    print("Cell-voltage trigger wired into the real loop: engages, blocks resume, holds on a stale cell reading: OK")


def part3():
    ok = {"float_cell_trigger_mv": 3500.0, "float_cell_resume_mv": 3420.0}
    MasterBusService._validate_settings(ok, partial=True)                      # must not raise
    for bad in ({"float_cell_trigger_mv": 3000.0}, {"float_cell_trigger_mv": 3700.0}, {"float_cell_resume_mv": 3100.0}, {"float_cell_resume_mv": 3700.0}):
        try:
            MasterBusService._validate_settings(bad, partial=True)
        except ValueError:
            continue
        raise AssertionError(f"expected a ValueError for {bad}")
    try:
        MasterBusService._validate_settings({"float_cell_trigger_mv": 3500.0, "float_cell_resume_mv": 3480.0}, partial=True)   # only a 20 mV gap
    except ValueError:
        pass
    else:
        raise AssertionError("expected a ValueError for a resume level too close to the trigger")
    print("Cell-voltage settings validation (3300-3650 mV range, minimum 30 mV gap to the resume level): OK")

    # the SOC trigger and its warning-duration setting were removed in 1.17.0: they must be rejected as unknown
    # settings, but an older user_settings.json that still has them must keep loading (see _load_settings()).
    for removed in ("house_battery_soc", "bulk_resume_soc", "warning_popup_seconds"):
        try:
            MasterBusService._validate_settings({removed: 1}, partial=True)
        except ValueError:
            continue
        raise AssertionError(f"expected {removed} to be rejected as an unknown setting")
    service = MasterBusService()
    tmp_dir = tempfile.mkdtemp()
    service.settings_file = Path(tmp_dir) / "user_settings.json"
    service.settings_file.write_text(json.dumps({
        "default_ac_limit": 12, "float_protection_enabled": True,
        "house_battery_soc": 95.0, "bulk_resume_soc": 90.0,
        "float_cell_trigger_mv": 3500.0, "float_cell_resume_mv": 3420.0, "warning_popup_seconds": 8,
        "bms_refresh_interval": 30, "bms_popup_seconds": 3, "bms_connection_retry_seconds": 5,
        "balancer_refresh_interval": 30, "balancer_connection_retry_seconds": 30, "history_retention_days": 7,
    }), encoding="utf-8")
    service._load_settings()
    assert "house_battery_soc" not in service.settings and "warning_popup_seconds" not in service.settings
    assert service.settings["float_cell_trigger_mv"] == 3500.0
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("An older user_settings.json with the removed SOC trigger and warning duration still loads: OK")


if __name__ == "__main__":
    part1()
    part2()
    part3()
    print("All Float freshness and cell-voltage checks: OK")
