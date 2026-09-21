"""Non-hardware self-test for HistoryService.contributions() (the History-page pie charts).

Synthetic hour with known powers, so every expected energy can be worked out by hand:
- Solar 100 W all hour, alternator 50 W for the first 30 min, charger house 0 W.
- Inverter 20 W and other DC 10 W all hour.
- 10 minutes (minute 40-50) of missing dashboard data: must be excluded, not interpolated.
- Battery 1 discharges 10 A at 13.2 V (132 W), Battery 2 charges 5 A (66 W), Battery 3 is idle
  with three separate alarm episodes (6 alarm samples).
"""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from history_service import HistoryService

BASE = datetime.now(timezone.utc).replace(microsecond=123456) - timedelta(hours=2)


def close(actual, expected, tolerance, label):
    assert abs(actual - expected) <= tolerance, f"{label}: {actual:.3f} != {expected:.3f} (+/-{tolerance})"


def main():
    with tempfile.TemporaryDirectory(prefix="hist svc ") as tmp:               # folder name with a space, like Google Drive
        history = HistoryService(Path(tmp) / "history.sqlite3")
        for second in range(0, 3601, 10):
            if 2400 < second < 3000:
                continue                                                       # dashboard data missing for minutes 40-50
            stamp = (BASE + timedelta(seconds=second)).isoformat()
            history.record("dashboard", {
                "sources": {"shore": {"power": 0}, "charger_house": {"power": 0.0},
                            "alternator": {"power": 50.0 if second <= 1800 else 0.0}, "solar": {"power": 100.0}},
                "consumers": {"inverter": {"power": 20.0}, "charger_start": {"power": None}, "charger_bow": {"power": 0.0},
                              "engine_ecu": {"power": 1.0}, "alternator_field": {"power": 0.0}, "other_dc": {"power": 10.0}}},
                captured_at=stamp)
        for second in range(0, 3601, 30):
            stamp = (BASE + timedelta(seconds=second)).isoformat()
            index = second // 30
            for name, current, alarms in (("BATTERY 1", -10.0, []), ("BATTERY 2", 5.0, []),
                                          ("BATTERY 3", 0.0, ["Cell voltage high – level 2"] if index in (10, 11, 30, 50, 51, 52) else [])):
                history.record("bms", {"state": "connected", "battery": name, "captured_at": stamp, "pack_voltage_v": 13.2,
                                       "current_a": current, "cells_mv": [3300, 3300, 3300, 3300], "alarms": alarms,
                                       "remaining_capacity_ah": 200.0, "cell_spread_mv": 0, "charge_mosfet_on": True, "discharge_mosfet_on": True},
                               device=name, captured_at=stamp)

        whole = history.contributions(BASE.isoformat(), (BASE + timedelta(hours=1)).isoformat())
        source = {row["key"]: row for row in whole["sources"]}
        # 3000 s of coverage (the 600 s gap is excluded), so solar = 100 W * 3000 s
        close(whole["sources_covered_seconds"], 3000, 1, "dashboard coverage")
        close(source["solar"]["wh"], 100 * 3000 / 3600, 0.01, "solar Wh")
        close(source["solar"]["avg_w"], 100, 0.01, "solar average W")
        close(source["alternator"]["wh"], 50 * 1800 / 3600 + 50 / 2 * 10 / 3600, 0.01, "alternator Wh (first 30 min)")
        assert source["charger_house"]["wh"] == 0
        consumer = {row["key"]: row for row in whole["consumers"]}
        close(consumer["inverter"]["wh"], 20 * 3000 / 3600, 0.01, "inverter Wh")
        close(consumer["other_dc"]["wh"], 10 * 3000 / 3600, 0.01, "other DC Wh")
        assert consumer["charger_start"]["wh"] == 0, "missing power (None) must count as 0"
        print("Sources and consumers: energy, average power and the excluded 10 min gap: OK")

        battery = {row["key"]: row for row in whole["batteries"]}
        close(battery["BATTERY 1"]["discharge_wh"], 132, 0.05, "battery 1 delivered Wh")
        close(battery["BATTERY 1"]["charge_wh"], 0, 0.01, "battery 1 received Wh")
        close(battery["BATTERY 2"]["charge_wh"], 66, 0.05, "battery 2 received Wh")
        close(battery["BATTERY 2"]["discharge_wh"], 0, 0.01, "battery 2 delivered Wh")
        close(battery["BATTERY 1"]["avg_discharge_w"], 132, 0.1, "battery 1 average W")
        print("Batteries: delivered and received energy from the cell-voltage sum: OK")

        alarm = {row["key"]: row for row in whole["alarms"]}
        assert (alarm["BATTERY 3"]["episodes"], alarm["BATTERY 3"]["samples"]) == (3, 6), alarm["BATTERY 3"]
        assert alarm["BATTERY 1"]["episodes"] == 0 and alarm["BATTERY 2"]["samples"] == 0
        print("Alarm episodes (3) and samples (6) per battery: OK")

        half = history.contributions(BASE.isoformat(), (BASE + timedelta(minutes=30)).isoformat())
        close({row["key"]: row for row in half["batteries"]}["BATTERY 1"]["discharge_wh"], 66, 0.1, "first 30 min")
        assert {row["key"]: row for row in half["alarms"]}["BATTERY 3"]["episodes"] == 3       # all three are within the first 30 min
        ten = history.contributions(BASE.isoformat(), (BASE + timedelta(minutes=10)).isoformat())
        assert {row["key"]: row for row in ten["alarms"]}["BATTERY 3"]["episodes"] == 1        # only the episode at samples 10-11 is inside
        close(half["span_seconds"], 1800, 0.01, "span")
        z = history.contributions((BASE + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), (BASE + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.999Z"))
        close({row["key"]: row for row in z["batteries"]}["BATTERY 1"]["discharge_wh"], 66, 0.1, "second 30 min (Z timestamps)")
        print("Sub-periods and 'Z' timestamps: OK")

        for bad in (("garbage", BASE.isoformat()), (BASE.isoformat(), "nope"), ((BASE + timedelta(hours=1)).isoformat(), BASE.isoformat())):
            try:
                history.contributions(*bad)
                raise AssertionError(f"accepted {bad}")
            except ValueError:
                pass
        empty = history.contributions((BASE + timedelta(days=5)).isoformat(), (BASE + timedelta(days=6)).isoformat())
        assert empty["sources_covered_seconds"] == 0 and all(row["wh"] == 0 for row in empty["sources"])
        print("Invalid input rejected, empty period returns zeros: OK")

    # Whole-hour totals must add up exactly to a direct calculation, at any start/end, over several hours.
    with tempfile.TemporaryDirectory(prefix="hist hours ") as tmp:
        history = HistoryService(Path(tmp) / "history.sqlite3")
        origin = (datetime.now(timezone.utc) - timedelta(hours=9)).replace(minute=17, second=3, microsecond=250000)
        for step in range(0, 6 * 3600 + 1, 20):
            second = step
            stamp = (origin + timedelta(seconds=second)).isoformat()
            wave = 1 + (second // 600) % 5
            history.record("dashboard", {"sources": {"charger_house": {"power": 10.0 * wave}, "alternator": {"power": 0.0}, "solar": {"power": 3.0 * (second % 900) / 9}},
                                          "consumers": {"inverter": {"power": 7.0 * wave}, "other_dc": {"power": 40.0}}}, captured_at=stamp)
            if second % 30 == 0 and not 9000 < second < 9600:                     # a Bluetooth gap of 10 minutes
                for name, sign in (("BATTERY 1", -1), ("BATTERY 2", 1), ("BATTERY 3", -1)):
                    history.record("bms", {"state": "connected", "battery": name, "captured_at": stamp, "cells_mv": [3320] * 4,
                                           "current_a": sign * (2.0 + wave), "alarms": ["x"] if second % 1800 in (0, 30) else []},
                                   device=name, captured_at=stamp)
        history.refresh_chart_cache()
        index = history._index()
        checked = 0
        for begin_min, length_min in ((0, 360), (7, 251), (61, 60), (30, 45), (100, 5), (0, 61), (119, 122), (59, 2)):
            a = origin + timedelta(minutes=begin_min, seconds=11)
            b = a + timedelta(minutes=length_min)
            composed = history.contributions(a.isoformat(), b.isoformat())
            energy, covered, batteries = history._raw_energy(index, a.timestamp(), b.timestamp())
            keys = ("charger_house", "alternator", "solar", "inverter", "charger_start", "charger_bow", "engine_ecu", "alternator_field", "other_dc")
            got = {row["key"]: row["wh"] for row in composed["sources"] + composed["consumers"]}
            for key, wh in zip(keys, energy):
                close(got[key], wh, 1e-9, f"{key} composed vs direct ({begin_min}+{length_min} min)")
            for row in composed["batteries"]:
                out, into, cover = batteries[row["key"]]
                close(row["discharge_wh"], out, 1e-9, "battery out composed vs direct")
                close(row["charge_wh"], into, 1e-9, "battery in composed vs direct")
                close(row["covered_seconds"], cover, 1e-9, "battery coverage composed vs direct")
            close(composed["sources_covered_seconds"], covered, 1e-9, "dashboard coverage composed vs direct")
            checked += 1
        assert index["hours"], "finished hours were not remembered"
        assert history._index() is index, "the index was rebuilt although the data did not change"
        print(f"Hourly totals add up exactly to the direct calculation ({checked} periods, {len(index['hours'])} hours remembered): OK")
    print("All history contribution checks: OK")


if __name__ == "__main__":
    main()
