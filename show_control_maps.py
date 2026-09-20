from masterbus_service import MasterBusService

service = MasterBusService()

print("Loaded control mappings:")
print()

for name, cfg in service.control_maps.items():
    print(name)
    if cfg is None:
        print("  None")
        continue

    print(f"  verified     : {cfg.get('verified')}")
    print(f"  address      : {cfg.get('address')}")
    print(f"  field        : {cfg.get('field')}")
    print(f"  name         : {cfg.get('name')}")
    print(f"  protocol     : {cfg.get('protocol')}")
    print(f"  type         : {cfg.get('type')}")
    print(f"  commit_field : {cfg.get('commit_field')}")
    print()
