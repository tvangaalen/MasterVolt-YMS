# MasterVolt YMS

**Mastervolt Energy v1.8.10** — a private web app (installable iPhone PWA) that monitors and controls a boat's
Mastervolt electrical system and its DALY battery management, from a Windows PC on the boat's LAN.

- **MasterBus** over the Mastervolt USB Link: CombiMaster (shore power, inverter, charger), Solar ChargeMaster,
  Alpha Pro alternator regulator, Mass chargers, MasterShunts and the Yanmar engine ECU interface.
- **DALY Bluetooth** for the three house-battery BMS units (`BATTERY 1`–`3`) and the three balancers (`DL-BAL1`–`3`).
- **FastAPI** backend, single-page frontend in `static/index.html`, SQLite measurement history, local HTTPS.

Release history: [CHANGELOG.md](CHANGELOG.md). Protocol findings and field maps: [docs/hardware-notes.md](docs/hardware-notes.md).

## Pages

| Page | What it does |
|---|---|
| **Dashboard** | Sources, Storage and Loads tiles with live V/A/W; ON/OFF controls; Motor / Anchor / Sail / Marina modes; shore-power AC limit; `INV` and `SUP`; Engine ECU power with a safety confirmation |
| **BMS** | Side-by-side House battery matrix: SOC, voltages (3 decimals), cell voltages, temperatures, alarms, MOS state, voltage-derived SOC; Charge/Discharge controls and Set SOC (per battery and all) |
| **Balance** | Same layout for the three balancers (tap a balancer's column title to refresh only that balancer), plus collapsed raw Bluetooth diagnostics |
| **History** | Time-range slider and charts for Sources, Storage, Loads and Alarms, served from a server-side cache |
| **Settings** | Default AC limit, Float protection thresholds, refresh/retry intervals, pop-up durations, history retention |

Swipe left/right moves between pages on touch devices. A Light UI (for sunlight) and Dark UI are available from the header.

## Requirements

- Windows with the **Mastervolt USB Link** attached (HID access via `hidapi`) and a Bluetooth adapter for DALY (`bleak`).
- Python 3.12 or newer (`py` launcher). OpenSSL for the certificate setup (found on `PATH` or in the FireDaemon OpenSSL folder).
- An iPhone or browser on the same private LAN.

```powershell
py -m pip install -r requirements.txt
```

## Run

**Normal use — HTTPS** (needed for the iPhone PWA):

```powershell
.\start_mastervolt_server.cmd
```

This creates or reuses the private *Mastervolt Local CA*, issues a server certificate for localhost, the computer name
and the current LAN addresses, trusts the CA for the current Windows user, installs `bleak` if missing, and starts
Uvicorn on `https://<private-LAN-address>:8000`, bound only to that private address. First-time certificate setup for the PC and
iPhone is in [docs/local-https.md](docs/local-https.md).

**Plain HTTP for development** (binds all interfaces, so use only on a trusted network):

```powershell
.\run.ps1
```

The server needs exclusive use of the MasterBus USB Link, so close MasterAdjust and other Python MasterBus tools first.

Before the first start after changing any control code, check the mappings:

```powershell
py show_control_maps.py
```

`engine_ecu` must show `verified True`, address `3AE394`, field `43`.

## Safety model

- Only hardware-verified MasterBus fields are used; nothing is guessed at run time.
- Engine ECU OFF needs an explicit confirmation whose default is *Keep ECU ON*, and no operating mode ever switches it off.
- Controls write, then read back and verify; DALY writes are queued through one Bluetooth priority queue, with a pre-change backup written to `backups/`.
- **High-SOC Float protection** runs on the server every 3 s (default: Float at 95% House SOC, back to Bulk at 90%).
- The server listens only on a private RFC1918 address (or localhost). Nothing is published to the internet.

## Project layout

```text
app.py                     FastAPI app, routes, history loop, lifespan
masterbus_*.py             MasterBus USB protocol, service, control, discovery, registry, presentation
daly_bms_service.py        DALY BMS Bluetooth (persistent per-battery workers, MOS/SOC control)
daly_balancer_service.py   DALY balancer Bluetooth (read-only)
bluetooth_coordinator.py   Process-wide Bluetooth priority queue
history_service.py         SQLite history and server-side chart cache
static/                    index.html (SPA), PWA manifest + service worker, icons, cached product photos
control_maps.json, device_maps.json, mastershunt_config_maps.json   Verified device/control mappings
schemas/                   Cached MasterBus device schemas
VERIFIED_FIELD_MAP.txt     Fixed measurement map (see docs/hardware-notes.md for later changes)
certs/, setup_local_https.ps1, install_*_certificate.cmd            Local HTTPS
docs/                      Hardware notes, HTTPS guide, archived per-release notes
```

Runtime data (not for version control): `data/history.sqlite3`, `backups/`, `captures/*.pcap`, `user_settings.json`,
`certs/*-key.pem`. See `.gitignore`.

Diagnostic and test scripts (snapshots, discovery, ECU/charger tests, self-checks) are listed in
[docs/hardware-notes.md](docs/hardware-notes.md#utility-scripts). The quick structural check needs no hardware:

```powershell
py self_check.py
```

## Battery health report

`battery_health.py` analyses the stored history (read-only, safe while the server runs) and prints a Markdown report:
a verdict per battery, cell connection resistance, current sharing between the parallel batteries, cell/voltage
exposure, alarms, Bluetooth reliability, balancers, Start/Bow batteries and usage.

```powershell
py battery_health.py --project C:\Temp\mastervoltproject                       # print the report
py battery_health.py --project C:\Temp\mastervoltproject --days 7 --save       # last 7 days, also saved to reports\
py battery_health.py --project C:\Temp\mastervoltproject --split "2026-09-20 12:40"   # before/after a change you made
```

`--split` judges the period after the moment you specify (local time), for example after re-tightening a busbar.
Reports are written to `reports\` and are not committed to git. Thresholds are practical heuristics, not manufacturer
limits; see the top of the script. `py battery_health_self_test.py` checks the analysis on synthetic data, without hardware.

## License and disclaimer

Released under the [MIT License](LICENSE). This is an independent project, not affiliated with or endorsed by Mastervolt
or DALY; product names belong to their owners. It sends commands to real electrical equipment (chargers, inverter,
alternator regulator, engine ECU, battery MOSFETs) using reverse-engineered protocols. Use it at your own risk and verify
every control on your own installation.

## API

Interactive docs are at `/docs` while the server runs. Main endpoints: `GET /api/energy`, `GET|POST /api/settings`,
`GET /api/bms`, `GET /api/balancers`, `GET /api/bluetooth-coordinator`, `GET /api/history*`, and control routes under
`/api/control/*` and `/api/bms/*`.
