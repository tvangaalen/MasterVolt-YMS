import ast
from pathlib import Path

root = Path(__file__).resolve().parent

checks = {
    "masterbus_service.py": {
        "MasterBusService": {
            "open",
            "close",
            "read_field",
            "energy",
            "set_device_control",
            "_resolve_engine_ecu_power_control",
            "mastershunt_config",
            "_discover_mastershunt_config",
            "set_operating_mode",
            "set_ac_support",
            "enforce_high_soc_float",
            "_force_float",
        }
    },
    "masterbus_control_discovery.py": {
        "ControlDiscovery": {
            "_request",
            "_meta",
            "_meta_with_extra",
            "_string",
            "fields",
            "list_options",
            "control_candidates",
        }
    },
}

for filename, class_checks in checks.items():
    tree = ast.parse((root / filename).read_text(encoding="utf-8"))

    for class_name, required in class_checks.items():
        cls = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef)
                and node.name == class_name
            ),
            None,
        )

        if cls is None:
            raise SystemExit(f"FAILED - {class_name} not found in {filename}")

        methods = {
            node.name
            for node in cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        missing = sorted(required - methods)

        if missing:
            raise SystemExit(
                f"FAILED - {class_name} missing methods: "
                + ", ".join(missing)
            )

        print(
            f"{class_name} structure: OK "
            f"({len(required)} required methods checked)"
        )

print("All structural checks: OK")
