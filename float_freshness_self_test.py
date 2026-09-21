"""Non-hardware self-test for the freshness check of the house SOC used by Float protection.

Part 1 tests house_soc.py (age of each DALY reading, average of fresh readings, decision table).
Part 2 runs the real MasterBusService.enforce_high_soc_float loop with stubbed hardware access: no USB, no writes.
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

    f = hs.float_decision                                                      # (enabled, latched, soc, threshold, bulk)
    assert f(True, False, 96, 95, 90) == (True, False, False)
    assert f(True, False, 80, 95, 90) == (False, False, False)
    assert f(True, True, 93, 95, 90) == (False, False, False), "unchanged: between the two thresholds nothing is forced and Bulk is not resumed"
    assert f(True, True, 89, 95, 90) == (False, True, False), "a fresh SOC at or below the resume threshold ends Float"
    assert f(True, True, None, 95, 90) == (True, False, True), "unknown SOC while latched: hold Float, never resume Bulk"
    assert f(True, False, None, 95, 90) == (False, False, False), "unknown SOC must not start Float protection"
    assert f(False, True, None, 95, 90) == (False, False, False) and f(False, True, 99, 95, 90) == (False, False, False)
    print("Freshness of the house SOC and the Float decision table: OK")


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


def run_loop(soc_at_tick, ticks, state=3, details=True, tick_action=None):
    """Run the real loop. soc_at_tick(n) -> (soc or None, stale_batteries). Returns the service and the writes."""
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
        return {"soc": soc, "fresh_batteries": 0 if soc is None else 3, "stale_batteries": stale, "youngest_age_seconds": 30.0 if soc is None else 4.0, "last_known_soc": 96.0}

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


if __name__ == "__main__":
    part1()
    part2()
    print("All Float freshness checks: OK")
