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
- Never commit or print `certs/*-key.pem`, `user_settings.json`, `backups/`, `data/*.sqlite3`, `captures/*.pcap` or generated `reports/` (all in `.gitignore`).
- `battery_health.py` only reads the history database (opened `mode=ro`) and the BMS backups; keep it that way. Never point analysis tools at the live database with anything but a read-only connection.
- The Reports tab runs `battery_health.py` through `report_service.py` in a **separate low-priority process**. Do not import it into the server process or run analyses in a server thread: the GIL would slow the MasterBus/Bluetooth threads and Float protection. Deploying the Reports tab means copying `app.py`, `report_service.py`, `battery_health.py`, `static/index.html` and `static/service-worker.js` to the live folder.

## Verifying changes

There is no hardware-free test suite for the MasterBus side. These run without hardware and should pass before you finish:

```powershell
py self_check.py                       # structure of MasterBusService / ControlDiscovery (required methods exist)
py bms_control_self_test.py            # DALY MOS control logic
py bluetooth_connection_self_test.py   # failure-isolated Bluetooth workers
py bluetooth_coordinator_self_test.py  # Bluetooth priority gate: tokens, max-hold, FIFO, degraded mode
py ble_events_self_test.py             # Bluetooth event log: counters, timing statistics, slow-connect logging
py daly_bms_worker_self_test.py        # BMS monitoring workers on a simulated bleak (~25 s)
py daly_balancer_self_test.py          # balancer service on a simulated bleak: time-outs, back-off, watchdog (~20 s)
py float_freshness_self_test.py        # Float's SOC/cell-voltage dual trigger and stale-data handling (stubbed hardware)
py battery_health_self_test.py         # battery_health.py analysis on synthetic data
py report_service_self_test.py         # Reports-tab job runner (separate process, one at a time, errors, timeout)
py history_service_self_test.py        # History pie totals (HistoryService.contributions): energy, gaps, alarms, hourly totals
```

`self_check.py` fails if a method is accidentally nested or removed (this happened twice — see changelog 0.13.1/0.13.2), so run it after any edit to `masterbus_service.py` or `masterbus_control_discovery.py`. For frontend edits, syntax-check the JavaScript in `static/index.html`. The frontend is a single ~150 KB file with inline JS/CSS.

**CSS edits:** the stylesheet is a few very long lines. When removing or rewriting rules with a script, remove the *whole* rule including its selector prefix (for example `html[data-theme="light"] .x{…}`), then compare the set of rules before and after (parse `selector{declarations}` from `<style>` in the old and new file) and confirm only the intended rules changed. A dangling prefix once silently turned `.voltage-cell{position:relative}` into a light-theme-only rule and moved the cell dots (changelog 1.11.0).

Delete `__pycache__` folders after running Python here; the project lives in Google Drive.

## Conventions

- Python 3.12+, Windows, PowerShell. `py` launcher, not `python`. Paths must not be hardcoded (`$PSScriptRoot`, `Path(__file__)`).
- Match the surrounding style. `app.py` and the service modules use a dense style (semicolons, terse names) — don't reformat existing code.
- **iPhone touch rules for the frontend.** The pages switch on a horizontal swipe (handler near the end of `index.html`). Any control that is dragged or scrolled sideways (sliders, scrollable tables, carousels) must be an `input`/`select`/`textarea` or sit inside an element with class `no-swipe`, otherwise dragging it changes page. Touch targets should be at least ~40 px. The browser preview cannot drag range inputs in its mobile emulation (it sends mouse events); check them at desktop width and test the swipe guard with dispatched `TouchEvent`s.
- History totals (the donut charts) come from `HistoryService.contributions()` on the server, which integrates the full-resolution in-memory caches. The chart data sent to the browser is a *sample* (2,500 dashboard points, 1,200 per battery) meant for drawing only: never add up totals from it in the browser. Keep the calculation light (hourly totals are memoised; no per-request loops over the whole history).
- Stacked History charts are drawn as areas by `drawStackedHistoryChart` (with `smoothPoints`), not as bars: no per-bar gaps or alpha, so no stripes. Keep bands opaque on the offscreen layer and composite once.
- New MasterBus/DALY behaviour goes through the existing service classes (`MasterBusService`, `DalyBmsService`, `DalyBalancerService`) and the shared `io_lock` / Bluetooth coordinator. Never open a second HID or BLE connection from a new code path.
- Bluetooth work must go through `bluetooth_coordinator.py` using the existing priority order (user controls > manual refresh > auto reconnect > auto reads > balancers). Per-battery workers are failure-isolated; keep them so.
- **Every Windows BLE call needs a hard deadline** (`asyncio.wait_for` around `connect`, `start_notify`, GATT writes, `disconnect`): a hung WinRT call otherwise freezes a whole worker loop. A client that connected but failed later must be disconnected. `acquire()` returns a token that `release()` must be given, and a lease that outlives its max-hold time is taken back. Log failures with `ble_events.ble_log` (`/api/bluetooth-events`). New Bluetooth logic is tested against the simulated `bleak` in `ble_fakes.py`, never with real hardware.
- **Float protection only trusts fresh data** (`house_soc.py`). Do not read `state_of_charge_percent` or `cells_mv` from the BMS snapshot directly for control decisions: use `available_house_bms_soc` / `house_bms_soc_details` in `app.py`. Float has two independent triggers, SOC average and highest single cell voltage (either is enough to start Float; both must clear, with hysteresis, to resume Bulk) - see `house_soc.float_decision`. No MasterShunt fallback without the user's approval.
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
- Remote: `origin` = https://github.com/tvangaalen/MasterVolt-YMS (**public**, `main` plus the `v*` tags). Push only when the user asks or after they approved a release; never force-push or rewrite pushed history. Everything committed is public, so re-check `git status` and the staged diff for keys, certificates, settings, hardware serials and personal data before every push.
- GitHub CLI (`gh`, per-user winget install; path `C:\Users\TvanG\AppData\Local\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe`) is signed in as `tvangaalen`. This repo's local `credential.helper` points at it, so plain `git push` works from the project folder. Auth changes and account settings are the user's to make.
- License: MIT (`LICENSE`, copyright holder "TvanG"). Do not change the license or copyright holder without asking.
- **Keep the user's real email out of git.** Commits use the GitHub no-reply address (set as this repo's local `user.email`); never put the user's personal or company email address, or other personal data, in commits, files or tags.
- The live folder `C:\Temp\mastervoltproject` is **not** a git repository. Commit in the source folder first, then deploy the changed files.

## Two folders: source vs live

- `G:\My Drive\Claude\MasterVolt-YMS` (Google Drive) is the **source** copy. Do not run the server from it: it holds only a stale snapshot of runtime state, and a large SQLite history should not live on Drive.
- When Google Drive (`G:`) is not available, work in the independent clone (a normal git checkout of the GitHub repository, for example under the user's `Claude working folder`). Commit and push from there; the Drive copy and the external `.gitrepos` repository catch up later by pulling from GitHub, so never edit the same file in two copies at once.
- `C:\Temp\mastervoltproject` is the **live** folder the boat PC runs from. It holds the real `user_settings.json` (Float 95% / Bulk 90%, 31-day retention), `data/history.sqlite3` (~380 MB), `backups/`, `certs/*-key.pem` and `data/bms_mos_encoding.json`.
- To deploy: copy only the changed code files (e.g. `app.py`, `daly_balancer_service.py`, `static/*`) from source to live, after backing up the live versions to a sibling folder such as `C:\Temp\mastervoltproject_pre_swap_<timestamp>`. Never overwrite live runtime state, and never run with a settings file that lacks `history_retention_days` against the live database (missing means the 7-day default and pruning).
- Before restarting, read-only check `/api/bms` (`control_status.busy` false) and `/api/energy` (`high_soc_float_policy`); a restart pauses server-side Float protection.
- The server was last started detached (hidden) with logs in `C:\Temp\mastervoltproject\logs\`. `start_mastervolt_server.cmd` is the normal launcher; it regenerates the server certificate for the current LAN address.

## Known quirks

- `run.ps1` binds `0.0.0.0` over plain HTTP; `start_mastervolt_server.cmd` is the HTTPS, private-address launcher.
- `VERIFIED_FIELD_MAP.txt` is the v0.17 map; `docs/hardware-notes.md` lists what changed since (e.g. Solar voltage is now field 6, House SOC is the DALY average).
- Alternator ON currently only clears Stop Charge (field 39 = 0 + commit 40) and expects the Alpha Pro to resume by itself — no Bulk request is sent.
