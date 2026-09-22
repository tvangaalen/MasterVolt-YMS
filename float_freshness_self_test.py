"""Non-hardware self-test for Float protection: SOC/cell-voltage freshness and the dual trigger.

Part 1 tests house_soc.py (age of each DALY reading, average of fresh readings, cell-voltage stats, decision table).
Part 2 runs the real MasterBusService.enforce_high_soc_float loop with stubbed hardware access: no USB, no writes.
Part 3 checks validation of the new cell-voltage settings (range, minimum gap to the resume level).
"""
import sys
import time
import types
from datetime import datetime, timezone

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

    # ---- float_decision(): (active, resume, held, trigger) - SOC-only behaviour must be unchanged
    f = hs.float_decision                                                      # (enabled, latched, soc, threshold, bulk)
    assert f(True, False, 96, 95, 90) == (True, False, False, "soc")
    assert f(True, False, 80, 95, 90) == (False, False, False, None)
    assert f(True, True, 93, 95, 90) == (False, False, False, None), "unchanged: between the two thresholds nothing is forced and Bulk is not resumed"
    assert f(True, True, 89, 95, 90) == (False, True, False, None), "a fresh SOC at or below the resume threshold ends Float"
    assert f(True, True, None, 95, 90) == (True, False, True, "held"), "unknown SOC while latched: hold Float, never resume Bulk"
    assert f(True, False, None, 95, 90) == (False, False, False, None), "unknown SOC must not start Float protection"
    assert f(False, True, None, 95, 90) == (False, False, False, None) and f(False, True, 99, 95, 90) == (False, False, False, None)

    # ---- the cell-voltage trigger: what CHANGELOG 1.13.0 was for - a low pack-average SOC must not mask a hot cell
    assert f(True, False, 70, 95, 90, 3550, 3500, 3420) == (True, False, False, "cell_voltage"), "one hot cell must force Float even with a low SOC average"
    assert f(True, False, 96, 95, 90, 3600, 3500, 3420) == (True, False, False, "soc+cell_voltage"), "both triggers can fire together"
    assert f(True, False, 70, 95, 90, 3400, 3500, 3420) == (False, False, False, None), "a normal cell (below the trigger) does not force Float"
    # resume needs the cell to have cooled down too, even when the SOC alone would already allow it
    assert f(True, True, 85, 95, 90, 3550, 3500, 3420) == (True, False, False, "cell_voltage"), "Bulk must not resume while a cell is still above the resume level"
    assert f(True, True, 85, 95, 90, 3400, 3500, 3420) == (False, True, False, None), "resumes once both the SOC and every cell are back within range"
    # the cell reading is exactly what latched Float; if it goes stale, hold rather than resume on the SOC alone
    assert f(True, True, 85, 95, 90, None, 3500, 3420) == (True, False, True, "held"), "a known, low SOC must not resume Bulk while the deciding cell reading is unknown"
    print("Cell-voltage Float trigger: fires despite a low SOC average, blocks resume, held when the cell reading is unknown: OK")


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


def run_loop(soc_at_tick, ticks, state=3, details=True, tick_action=None, cell_at_tick=None):
    """Run the real loop. soc_at_tick(n) -> (soc or None, stale_batteries); cell_at_tick(n) -> (max_cell_mv or None,
    battery name), optional. Returns the service and the writes."""
    service = MasterBusService()
    service.settings.update({"float_protection_enabled": True, "house_battery_soc": 95, "bulk_resume_soc": 90})
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

    def getter():
        return soc_at_tick(current["n"])[0]

    def detail_getter():
        soc, stale = soc_at_tick(current["n"])
        d = {"soc": soc, "fresh_batteries": 0 if soc is None else 3, "stale_batteries": stale, "youngest_age_seconds": 30.0 if soc is None else 4.0, "last_known_soc": 96.0}
        if cell_at_tick:
            max_cell_mv, battery = cell_at_tick(current["n"])
        else:
            # In the real app cell voltages come from the very same fresh/stale battery reading as SOC (see
            # house_bms_soc_details()), so a scenario that does not care about cell voltage still needs a normal,
            # non-alarming value whenever SOC is known - and None (unknown), same as SOC, once SOC goes stale.
            max_cell_mv, battery = (None, None) if soc is None else (3390, None)
        d["max_cell_mv"], d["max_cell_battery"] = max_cell_mv, battery
        return d

    service.house_soc_getter = getter
    if details:
        service.house_soc_details_getter = detail_getter
    service.stop_event = Clock(ticks, hook)
    service.set_state = lambda s: current.__setitem__("state", s)
    service.enforce_high_soc_float()
    return service, writes, seen, current


def part2():
    # a) fresh SOC above the threshold: Float is forced on a charging source
    s, writes, _, _ = run_loop(lambda n: (96.0, 0), 4, state=1)
    assert ("float", "charger_house") in writes and s.float_policy_status["soc_source"] == "daly_bms" and not s.float_policy_status["soc_held"]
    assert s.float_policy_status["trigger"] == "soc"

    # b) latched, then the DALY data goes stale while a source is charging again: Float is still forced, Bulk is not resumed
    def stale_after_three(n):
        return (96.0, 0) if n <= 3 else (None, 3)
    def source_restarts(n, service, current):                                   # the solar charger starts a new charge cycle in the hold
        if n == 6:
            current["state"] = 1
            service.float_policy_last_attempt.clear()
    s, writes, seen, _ = run_loop(stale_after_three, 12, state=3, tick_action=source_restarts)
    st = s.float_policy_status
    assert st["soc_source"] == "stale_hold" and st["soc_held"] and st["float_latched"] and st["active"], st
    assert st["soc_stale"] and st["soc_fresh_batteries"] == 0 and st["last_known_soc"] == 96.0 and st["house_soc"] is None
    assert ("float", "charger_house") in writes, "a source that restarts while the SOC is unknown must be put back in Float"
    assert not [w for w in writes if w[0] == "bulk"], "Bulk must never be resumed on stale data"

    # c) latched, DALY returns with a low SOC: Bulk resumes on fresh data only
    def recover(n):
        return (96.0, 0) if n <= 2 else ((None, 3) if n <= 6 else (85.0, 0))
    s, writes, _, current = run_loop(recover, 12, state=3)
    assert ("bulk", "charger_house") in writes and not s.float_policy_status["float_latched"] and s.float_policy_status["soc_source"] == "daly_bms"

    # d) never latched (for example just after a restart) and no fresh SOC: nothing is started
    s, writes, _, _ = run_loop(lambda n: (None, 3), 6, state=1)
    assert not writes and not s.float_policy_status["float_latched"] and s.float_policy_status["soc_source"] == "unavailable"
    assert s.float_policy_status["soc_stale"]

    # e) an old getter without details still works (unchanged behaviour)
    s, writes, _, _ = run_loop(lambda n: (96.0, 0), 4, state=1, details=False)
    assert ("float", "charger_house") in writes and s.float_policy_status["soc_fresh_batteries"] is None

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
    print("Float loop: fresh SOC acts, stale SOC holds Float without resuming Bulk, unknown SOC never starts anything: OK")

    # g) the reported failure mode: the pack-average SOC stays well under the threshold the whole time, but one
    #    battery's cell reaches the (default) cell-voltage trigger - Float must engage on that alone
    def low_average(n):
        return (70.0, 0)
    def hot_cell(n):
        return (3400, None) if n <= 2 else (3550, "BATTERY 1")
    s, writes, _, _ = run_loop(low_average, 6, state=1, cell_at_tick=hot_cell)
    st = s.float_policy_status
    assert st["house_soc"] == 70.0 and st["active"] and st["trigger"] == "cell_voltage", st
    assert ("float", "charger_house") in writes, "Float must engage on a hot cell even while the SOC average is far below its own threshold"
    assert st["max_cell_mv"] == 3550 and st["max_cell_battery"] == "BATTERY 1"

    # h) latched on a hot cell: Bulk must not resume while the cell stays above the resume level, even though the
    #    SOC average is already below the Bulk-resume threshold the whole time
    def low_soc(n):
        return (80.0, 0)
    def stays_hot(n):
        return (3550, "BATTERY 1")
    s, writes, _, _ = run_loop(low_soc, 6, state=3, cell_at_tick=stays_hot)
    assert not [w for w in writes if w[0] == "bulk"], "must not resume to Bulk while a cell is still above the resume level, even with a low SOC average"
    assert s.float_policy_status["float_latched"] and s.float_policy_status["trigger"] == "cell_voltage"

    # i) ... and does resume once the cell has cooled down too
    def cools_down(n):
        return (3550, "BATTERY 1") if n <= 3 else (3400, "BATTERY 1")
    s, writes, _, _ = run_loop(low_soc, 8, state=3, cell_at_tick=cools_down)
    assert ("bulk", "charger_house") in writes and not s.float_policy_status["float_latched"]

    # j) the cell reading is exactly what latched Float; if it goes stale, hold rather than resume on the SOC alone
    def known_low_soc(n):
        return (80.0, 0)
    def cell_goes_stale(n):
        return (3550, "BATTERY 1") if n <= 2 else (None, None)
    s, writes, _, _ = run_loop(known_low_soc, 6, state=3, cell_at_tick=cell_goes_stale)
    assert s.float_policy_status["float_latched"] and s.float_policy_status["trigger"] == "held", s.float_policy_status
    assert not [w for w in writes if w[0] == "bulk"], "must not resume while the cell reading that latched Float has gone stale, even with a known, low SOC"
    print("Cell-voltage trigger wired into the real loop: engages despite a low SOC, blocks resume, holds on a stale cell reading: OK")


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


if __name__ == "__main__":
    part1()
    part2()
    part3()
    print("All Float freshness and cell-voltage checks: OK")
