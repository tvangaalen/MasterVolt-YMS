from masterbus_service import MasterBusService
from masterbus_registry import (
    COMBIMASTER,
    SOLAR,
    ALTERNATOR,
    YANMAR,
    CHARGER_START,
    CHARGER_BOW,
    HOUSE_SHUNT,
    START_SHUNT,
    BOW_SHUNT,
)

DEVICES = {
    COMBIMASTER: "CMR Charge house",
    SOLAR: "SCM Solar",
    ALTERNATOR: "APR Alpha Pro MB",
    YANMAR: "INT Yanmar ECU",
    CHARGER_START: "MAC Charge start",
    CHARGER_BOW: "MAC Charge bow",
    HOUSE_SHUNT: "MSH House",
    START_SHUNT: "MSH Start",
    BOW_SHUNT: "MSH Bow",
}

def show_field(label, addr, field, unit_hint=""):
    suffix = f" {unit_hint}" if unit_hint else ""
    print(f"  {label:<16}: device={DEVICES[addr]} [{addr:06X}] field={field}{suffix}")

def show_smart_map(service, section_label, name, addr):
    print(section_label)
    mapping = service.smart_maps.get(name) or {}
    if not mapping:
        print("  No mapping loaded")
        return

    for key in ("voltage", "current", "power"):
        field = mapping.get(key)
        if field is None:
            print(f"  {key:<16}: —")
        else:
            print(f"  {key:<16}: device={DEVICES[addr]} [{addr:06X}] field={field}")

def main():
    service = MasterBusService()

    # Load persisted dynamic maps without opening USB if possible.
    loaded = service._load_maps()

    print("Mastervolt dashboard field audit")
    print("=" * 78)
    print(f"Persisted device_maps.json loaded: {loaded}")
    print()

    print("SOURCES")
    print("-" * 78)

    print("Shore Power")
    show_field("Voltage", COMBIMASTER, 2, "V")
    show_field("Current", COMBIMASTER, 3, "A")
    show_field("Frequency", COMBIMASTER, 4, "Hz")
    print("  Watts            : calculated = field 2 * field 3")
    print()

    print("Charger House")
    show_field("Voltage", COMBIMASTER, 11, "V")
    show_field("Current", COMBIMASTER, 12, "A")
    print("  Watts            : calculated = field 11 * field 12, clamped >= 0")
    print()

    print("Alternator")
    show_field("Voltage", ALTERNATOR, 14, "V")
    show_field("Alt shaft", ALTERNATOR, 11)
    show_field("Engine shaft", ALTERNATOR, 12)
    show_field("Temperature", ALTERNATOR, 32, "C")
    print("  Current          : calculated from house DC bus balance")
    print("  Watts            : field 14 * calculated alternator current")
    print("  Current formula  : House battery A + known DC load A + learned Other DC Load A")
    print("                     - Charger House A - Solar Charge A")
    print("  OFF calibration  : Other DC Load A is learned while fields 11/12 indicate stopped")
    print()

    print("Solar")
    show_field("PV voltage", SOLAR, 4, "V")
    show_field("Charge current", SOLAR, 5, "A")
    show_field("Battery voltage", SOLAR, 6, "V")
    print("  Watts            : calculated = field 6 * field 5")
    print()

    print("Total DC Input")
    print("  Formula          : Charger House W + derived Alternator W + Solar W")
    print("  Shore Power      : excluded from Total DC Input")
    print()

    print("STORAGE")
    print("-" * 78)

    for title, addr in (
        ("House Battery", HOUSE_SHUNT),
        ("Start Battery", START_SHUNT),
        ("Bowthruster Battery", BOW_SHUNT),
    ):
        print(title)
        show_field("State of Charge", addr, 0, "%")
        show_field("Voltage", addr, 1, "V")
        show_field("Current", addr, 2, "A")
        print("  Watts            : calculated = field 1 * field 2")
        print()

    print("LOADS")
    print("-" * 78)

    print("Inverter")
    show_field("DC voltage", COMBIMASTER, 11, "V")
    show_field("DC current", COMBIMASTER, 12, "A")
    show_field("Inverting status", COMBIMASTER, 47)
    show_field("Supporting status", COMBIMASTER, 49)
    print("  Active only      : field 47 or field 49 >= 0.5")
    print("  Watts            : abs(field 11 * field 12) while active")
    print("  Other DC Loads   : subtract active inverter DC current")
    print()

    print("AC Loads (House)")
    show_field("Voltage", COMBIMASTER, 5, "V")
    show_field("Frequency", COMBIMASTER, 6, "Hz")
    show_field("Power", COMBIMASTER, 8, "W")
    print("  Current          : calculated = field 8 / field 5")
    print()

    print("Charger Start")
    show_field("Input voltage", CHARGER_START, 4, "V")
    show_field("Input current", CHARGER_START, 5, "A")
    show_field("Output voltage", CHARGER_START, 6, "V")
    show_field("Output current", CHARGER_START, 7, "A")
    print("  Load watts       : calculated = field 4 * field 5")
    print("  Control          : field 64 On/Standby")
    print()

    print("Charger Bowthruster")
    show_field("Input voltage", CHARGER_BOW, 4, "V")
    show_field("Input current", CHARGER_BOW, 5, "A")
    show_field("Output voltage", CHARGER_BOW, 6, "V")
    show_field("Output current", CHARGER_BOW, 7, "A")
    print("  Load watts       : calculated = field 4 * field 5")
    print("  Control          : field 64 On/Standby")
    print()

    print("Engine ECU")
    show_field("DC input voltage", YANMAR, 39, "V")
    show_field("DC output voltage", YANMAR, 40, "V")
    show_field("DC output current", YANMAR, 41, "A")
    print("  Output watts     : field 40 * field 41")
    print("  Input watts      : output watts / 0.85 estimated efficiency")
    print("  Input current    : input watts / field 39")
    print("  Control          : field 43 Mac/Magic On")
    print()

    print("Other DC Loads (House)")
    show_field("House Voltage", HOUSE_SHUNT, 1, "V")
    show_field("House Current", HOUSE_SHUNT, 2, "A")
    print("  House batt W     : field 1 * field 2")
    print("  Current          : observed from bus balance while alternator is OFF")
    print("  OFF formula      : Charger House A + Solar A - House Battery A")
    print("                     - Charger Start A - Charger Bow A - Engine ECU A")
    print("  Engine running   : latest filtered OFF-baseline is retained")
    print("  Watts            : House Battery V * Other DC Loads A")
    print()

    print("CONTROL FIELDS")
    print("-" * 78)

    print("CombiMaster Inverter")
    show_field("Control", COMBIMASTER, 19)
    print("  Commit field     : 20")
    print()

    print("CombiMaster Charger")
    show_field("Control", COMBIMASTER, 21)
    print("  Commit field     : 22")
    print()

    print("CombiMaster AC input limit")
    show_field("Control", COMBIMASTER, 23, "A")
    print()

    print("CombiMaster AC IN support")
    print("  Control          : Btm3 field 11 (0=Off, 1=On)")
    print("  Metadata name    : AC IN support")
    print()

    print("Charger Start")
    show_field("Control", CHARGER_START, 64)
    print("  Semantics        : 0=Standby, 1=On")
    print()

    print("Charger Bowthruster")
    show_field("Control", CHARGER_BOW, 64)
    print("  Semantics        : 0=Standby, 1=On")
    print()

    print("Engine ECU")
    show_field("Control", YANMAR, 43)
    print("  Semantics        : 0=Off, 1=On")
    print()

    print("=" * 78)
    print("Audit complete.")

if __name__ == "__main__":
    main()
