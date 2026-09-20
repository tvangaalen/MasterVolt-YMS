# MasterVolt YMS

Windows-hosted FastAPI app (iPhone PWA frontend) that monitors and controls a real boat's Mastervolt electrical
system over the MasterBus USB Link, plus DALY BMS/balancers over Bluetooth. **It writes to live hardware** (inverter,
chargers, alternator regulator, Engine ECU, battery MOSFETs). Treat every control path as safety-critical.

Overview and layout: `README.md`. Release history: `CHANGELOG.md`. Field maps, protocol findings and the utility
script table: `docs/hardware-notes.md`. Read the hardware notes before touching any MasterBus or DALY code.

## Hard rules

- **Never guess a MasterBus field number, CAN opcode or DALY command.** Only use fields verified on hardware, in a device snapshot (`masterbus_snapshot.py`) or in a MasterAdjust capture. If a mapping is unknown, say so and propose a read-only inspection script instead of writing code that "tries" a field.
- **Never run anything that writes to hardware** (`charger_control_test.py`, `ecu_*_test.py`, `source_control_verify.py --confirm-write`, or the app's `/api/control/*` and `/api/bms/*` POST routes) unless the user explicitly asks in that turn. Prefer read-only scripts. Do not start the server: it takes exclusive ownership of the USB Link and the Bluetooth radio.
- **Engine ECU safety is fixed:** OFF always needs the in-app confirmation whose default is *Keep ECU ON*, and no operating mode may switch the ECU off. Do not weaken this.
- Preserve write-then-read-back verification on controls. Do not turn a verified write into fire-and-forget.
- Keep hardware-verified mappings, control sequences and safety logic unchanged unless the task is specifically about them, and say so when it is. Past changelog entries end with "mappings … unchanged" for a reason.
- Keep everything on a private LAN: the server binds only to an RFC1918 address or localhost. Never bind a public interface or add cloud/tunnel dependencies.
- Never commit or print `certs/*-key.pem`, `user_settings.json`, `backups/`, `data/*.sqlite3` or `captures/*.pcap` (all in `.gitignore`).

## Verifying changes

There is no hardware-free test suite for the MasterBus side. These run without hardware and should pass before you finish:

```powershell
py self_check.py                       # structure of MasterBusService / ControlDiscovery (required methods exist)
py bms_control_self_test.py            # DALY MOS control logic
py bluetooth_connection_self_test.py   # failure-isolated Bluetooth workers
```

`self_check.py` fails if a method is accidentally nested or removed (this happened twice — see changelog 0.13.1/0.13.2), so run it after any edit to `masterbus_service.py` or `masterbus_control_discovery.py`. For frontend edits, syntax-check the JavaScript in `static/index.html`. The frontend is a single ~150 KB file with inline JS/CSS.

Delete `__pycache__` folders after running Python here; the project lives in Google Drive.

## Conventions

- Python 3.12+, Windows, PowerShell. `py` launcher, not `python`. Paths must not be hardcoded (`$PSScriptRoot`, `Path(__file__)`).
- Match the surrounding style. `app.py` and the service modules use a dense style (semicolons, terse names) — don't reformat existing code.
- New MasterBus/DALY behaviour goes through the existing service classes (`MasterBusService`, `DalyBmsService`, `DalyBalancerService`) and the shared `io_lock` / Bluetooth coordinator. Never open a second HID or BLE connection from a new code path.
- Bluetooth work must go through `bluetooth_coordinator.py` using the existing priority order (user controls > manual refresh > auto reconnect > auto reads > balancers). Per-battery workers are failure-isolated; keep them so.
- Settings live in `masterbus_service.py` (`self.settings`, `_validate_settings`), `app.py` (`SettingsReq`) and the Settings page. Add a setting in all three, validate on client and server, and give it a default that keeps existing `user_settings.json` files working.

## Releasing

Every user-visible change bumps the version in **all three places** and adds a `CHANGELOG.md` entry (newest first, `### x.y.z` under the current minor heading):

1. `app.py` — `FastAPI(..., version="…")`
2. `static/index.html` — footer text (`… · v…`)
3. `static/service-worker.js` — `CACHE='mastervolt-v…-shell'` (a new cache name is what makes the iPhone PWA refresh)

Also update the version line at the top of `README.md`. Do not recreate per-release `README_*.md` / `CHANGELOG_v*.md` files; those live in `docs/archive/` for history only.

## Version control

- Git 2.55 (installed via winget; it may not be on the PATH of an already-open shell — use `C:\Program Files\Git\cmd\git.exe`). Branch `main`, repo-local identity set.
- The repository data lives **outside Google Drive** at `C:\Users\TvanG\.gitrepos\MasterVolt-YMS.git`; the project folder only holds a `.git` pointer file. Never move or delete that folder, and never run `git init` again in the project.
- History: `v1.8.8` = the original zip as delivered; then the docs consolidation; then `v1.8.10`. Tag each release (`git tag -a vX.Y.Z`) on the commit that bumps the version.
- Commit code and docs changes separately. Commit messages: imperative summary line, then what and why. Do not commit runtime data, certificates/keys, backups, captures or `user_settings.json` (see `.gitignore`); check `git status` before `git add -A`.
- There is no remote yet. Do not create or push to one (GitHub etc.) unless the user asks: the repo contains hardware protocol details of the boat's electrical system.
- The live folder `C:\Temp\mastervoltproject` is **not** a git repository. Commit in the source folder first, then deploy the changed files.

## Two folders: source vs live

- `G:\My Drive\Claude\MasterVolt-YMS` (Google Drive) is the **source** copy. Do not run the server from it: it holds only a stale snapshot of runtime state, and a large SQLite history should not live on Drive.
- `C:\Temp\mastervoltproject` is the **live** folder the boat PC runs from. It holds the real `user_settings.json` (Float 95% / Bulk 90%, 31-day retention), `data/history.sqlite3` (~380 MB), `backups/`, `certs/*-key.pem` and `data/bms_mos_encoding.json`.
- To deploy: copy only the changed code files (e.g. `app.py`, `daly_balancer_service.py`, `static/*`) from source to live, after backing up the live versions to a sibling folder such as `C:\Temp\mastervoltproject_pre_swap_<timestamp>`. Never overwrite live runtime state, and never run with a settings file that lacks `history_retention_days` against the live database (missing means the 7-day default and pruning).
- Before restarting, read-only check `/api/bms` (`control_status.busy` false) and `/api/energy` (`high_soc_float_policy`); a restart pauses server-side Float protection.
- The server was last started detached (hidden) with logs in `C:\Temp\mastervoltproject\logs\`. `start_mastervolt_server.cmd` is the normal launcher; it regenerates the server certificate for the current LAN address.

## Known quirks

- `run.ps1` binds `0.0.0.0` over plain HTTP; `start_mastervolt_server.cmd` is the HTTPS, private-address launcher.
- `VERIFIED_FIELD_MAP.txt` is the v0.17 map; `docs/hardware-notes.md` lists what changed since (e.g. Solar voltage is now field 6, House SOC is the DALY average).
- Alternator ON currently only clears Stop Charge (field 39 = 0 + commit 40) and expects the Alpha Pro to resume by itself — no Bulk request is sent.
