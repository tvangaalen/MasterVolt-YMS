# Changelog

All notable changes to MasterVolt YMS (the Mastervolt Energy web app). Newest first.

This file consolidates the former per-release `CHANGELOG_v*.md` and `README_*.md` notes; the originals are kept
in [`docs/archive/`](docs/archive/). Versions before 1.0.0 were documented only in the per-fix READMEs, so their
entries are summaries. There are no release notes for 1.3.12.

Unless an entry says otherwise, every release also bumps the application version, footer and service-worker cache.

---

## 1.9 — Reports

### 1.9.1
- History charts: when a legend item is selected to filter a stacked chart, the **smooth line** is now still drawn, following the filtered data (previously it only appeared in the *Total* view). Applies to the Remaining, Load, Sources and Loads charts; the dashed average line is unchanged.

### 1.9.0
- New **Reports** tab on the History page with a **Battery health report** button. It asks how many days of data to analyse (1–365, default 7), runs the analysis on the server and shows the result on the page: verdict per battery, cell connection resistance, current sharing, alarms, balancers and usage. The status labels are colour-coded (OK / ATTENTION / CRITICAL) and wide tables scroll sideways on a phone.
- The analysis runs in a separate low-priority process (`report_service.py`), so it cannot slow down the Bluetooth/MasterBus threads or Float protection. Only one report runs at a time; the latest result is kept in memory until the server restarts and is shown again when the tab is reopened. New endpoints: `POST /api/reports/battery-health` (body `{"days": N}`) and `GET /api/reports/battery-health`.
- History time-range controls (slider, *Last 4 hrs*, *Update*) are hidden on the Reports tab.
- **`battery_health.py`** (also usable from the command line) is a read-only battery health report built on the stored measurement history: verdict per battery, per-cell connection resistance from load steps, current sharing between the parallel batteries, cell/voltage exposure, alarms and MOSFET episodes (separating your own switching from BMS protection), Bluetooth gaps, cycle counters, balancers, Start/Bow batteries and usage. `--split` compares the periods before and after a change (for example a re-tightened busbar). Includes `battery_health_self_test.py` (synthetic data, no hardware). Reports go to `reports/`, which is git-ignored.

## 1.8 — History range and polish

### 1.8.10
- Tapping a balancer column title on the Balance page (e.g. **BALANCER 1**) refreshes only that balancer. It uses the same Bluetooth coordinator as before, but the clicked balancer goes first and is the only one read in that manual cycle; it also overtakes the rest of a running full cycle. A deferred attempt is retried. New endpoint `POST /api/balancers/{1-3}/refresh`.
- The **Inverter state** History chart has a fixed axis with only three ticks — Off (0), Inverting (1), Supporting (2) — instead of five identical `1` labels, and draws as a step line. `drawHistoryChart` accepts an optional fixed-axis argument.

### 1.8.9
- Fixed **Set all SOC** staying greyed out: the enable logic still referenced the three SOC buttons removed in 1.8.8 (`bmsAll100`, `bmsAllSocCharge`, `bmsAllSocDischarge`). It now enables `bmsAllSoc` as soon as all three batteries are connected or being read. The unused hidden placeholder buttons were removed.

### 1.8.8
- One **Set SOC** button per battery and one **Set all SOC** button, opening a dialog: charge-derived SOC, discharge-derived SOC, 100%, or cancel. The queued Bluetooth write and validation flow is unchanged.
- Fixed the iPhone bottom-navigation safe-area layout on Balance and Settings; navigation glyphs replaced with consistent line icons.
- Sources history chart renamed **Charge current (A)**.

### 1.8.7
- History slider thumbs are dedicated handles rendered above the track instead of browser-painted thumbs.
- BMS and Balance voltages are centred while keeping exact decimal alignment; cell status dots sit in a fixed marker area.

### 1.8.6
- Both History slider handles stay visible in the Light UI.
- Dashboard, BMS and Balance voltages align at the decimal point; status dots no longer shift values.
- Added a **Last 4 hrs** History button.

### 1.8.5
- Left History slider handle clearly visible in the Light UI. Balance voltages use three decimals.

### 1.8.4
- All BMS voltages use three decimals.

### 1.8.3
- History axes show 24-hour `HH:mm` for periods of 48 h or less, `DD/MM` for longer periods.
- **Update** moves the selected window to the newest measurement, preserving its duration.

### 1.8.2
- Replaced *Zoom (hrs)* with a shared two-handle time-range slider below the History tabs, filtering every tab.
- `Load (A)` renamed `Charge/discharge (A)`; horizontal-axis dates use `DD/MM`.
- Selecting a single BMS cell switches the voltage axis to 2.5–4.0 V (Total stays 0–15 V).

### 1.8.1
- Alarm occurrence legend filters by All / Battery 1 / 2 / 3, applied to all alarm charts, titles and totals.

### 1.8.0
- New **Alarms** History tab: stacked alarm timeline per battery (sample and episode counts), Cell Max-difference trend with alarm markers, current trends with alarm markers, and Charge/Discharge MOS OFF consequence markers.
- Server-side BMS chart cache now includes decoded alarms and both MOS states.

## 1.7 — Server-side history cache

### 1.7.5
- Battery cell-voltage charts: fixed axes (0–15 V left, 0–400 mV right); removed the average-voltage line.

### 1.7.4
- *Total* view draws cumulative lines (Cell 1, 1+2, 1+2+3, 1+2+3+4); selecting a cell shows only that cell's raw voltage. Max difference stays visible.

### 1.7.3
- Cell-voltage charts became smooth lines with an interactive Total / Cell 1–4 legend and a red Max-difference line on a right-hand mV axis.

### 1.7.2
- 24-hour time labels; dashed average lines on power/current charts; BMS cell-voltage data and cell spread added to the server-side chart data.

### 1.7.1
- Time-axis labels follow the selected range. Charging and DC-load current charts are stacked bars. Legends are interactive (select a component to isolate it; Total restores).

### 1.7.0
- Graph cache moved from browser IndexedDB to a shared **server-side cache** rebuilt from SQLite on restart; the server sends a bounded chronological sample per zoom window.
- Added *Zoom (hrs)* (later replaced in 1.8.2). History tabs reordered to Sources, Storage, Loads; Sources is the default.
- Swipe navigation extended across Dashboard, BMS, Balance, History and Settings.
- Generated power limited to DC sources (Charger House, Alternator, Solar); Shore Power AC voltage replaces Shore frequency; Storage voltage split per battery; Loads output-frequency graph removed.

## 1.6
### 1.6.0
- All proposed Sources and Loads history charts implemented from retained Dashboard measurements (stacked generated power, per-source charge current, Solar panel voltage, Alternator temperature; stacked DC consumption, per-consumer current, AC power, Inverter state).
- Browser cache extended to Dashboard history; **Update** retrieves BMS and Dashboard data incrementally. Time axes show dates only.

## 1.5
### 1.5.1
- History tabs: Storage, Sources, Loads. Storage charts reordered to Remaining, Load, Voltage; Remaining and Load are stacked columns with a smooth total line.

### 1.5.0
- History replaced the raw record list with time-series charts for BMS voltage, current and remaining capacity (Battery 1–3 plus calculated total), covering the full retained history.
- Persistent IndexedDB chart cache in the browser; **Update** fetches only measurements since the previous update. Incremental BMS history API added.

## 1.4 — Balancers, Bluetooth coordination, history

### 1.4.30
- All discharge gets the in-button queued/waiting/progress flow (confirmation kept); its pop-up removed.
- Discharge ON always available for a connected battery; Discharge OFF only at zero or negative current.
- Clicking a battery summary card refreshes only that battery (new endpoint and worker trigger).
- All charge / All discharge write only to batteries whose MOS state differs from the target.

### 1.4.29
- Charge OFF is available from 0.0 A upward; only negative current blocks it.

### 1.4.28
- All charge shows Queued / Waiting for Bluetooth / Action in progress in the button; queue pop-up removed. Individual Charge controls are disabled during an All-charge action.
- Charge ON always available for a connected battery with Charge MOS off; Charge OFF only with charging current.

### 1.4.27
- Only the clicked individual BMS button is disabled; further actions queue (`Queued`) and run sequentially, showing `Waiting for Bluetooth` then `Action in progress`.

### 1.4.26
- Removed confirmation dialogs from individual-battery BMS controls (all-battery controls keep theirs). Failures still produce a persistent pop-up.

### 1.4.25
- All charge / All discharge became state-aware ON/OFF toggles; the wide buttons below the matrix were removed. Successful controls no longer show a completion pop-up.

### 1.4.24
- Charge/Discharge controls use a compact power icon (state kept in colour, tooltip and accessible label).
- A battery's Charge control is disabled while its current is negative; all-battery Charge needs all three connected with non-negative current.

### 1.4.23
- Fixed the adaptive MOS retry: if the learned payload does not change the requested state, the second attempt uses the opposite payload. A Charge retry never sends a Discharge command.

### 1.4.22
- Detects per battery, and separately for Charge and Discharge, whether payload `1` means ON or OFF (firmware variants differ). An encoding is accepted only after read-back and remembered in `data/bms_mos_encoding.json`.

### 1.4.21
- MOS Charge/Discharge writes are verified by read-back, retried once, and report the actual states on failure. Changing one MOS preserves the other (restoring and verifying it if the unit changes both).

### 1.4.20
- Fixed a race after a successful BMS verification that could end in a watchdog failure. Bluetooth queue waits run outside the battery worker loop; automatic reads pause briefly during a user setting.

### 1.4.19
- Fixed a false *BMS update failed* after verification completed: the 25 s per-battery watchdog was shorter than a valid write plus nine verification reads. It is now a 120 s last-resort guard.

### 1.4.18
- Deterministic Bluetooth **priority queue**: user controls, manual BMS refresh/reconnect, automatic reconnect, automatic BMS reads, balancer traffic.
- Setting writes and verification retry once. `Set all` errors name the failed, completed and untouched batteries. Failure dialogs stay until OK. BMS status exposes queue and manual-refresh progress.

### 1.4.17
- 8 s timeout on every BMS write and 4 s on each verification request; progress names `sending setting`, `waiting for BMS confirmation` and each verification read; failures name the battery, operation and timeout.

### 1.4.16
- `Set all` reserves the Bluetooth radio once for the whole three-battery batch, with server-side progress (`Updating Battery 1/3` …).

### 1.4.15
- Dashboard column widths rebalanced; lightning icon restored next to the title.
- BMS and Balancer **Refresh all** are generation-based, so every battery/balancer must complete the request. BMS cards show their real sequential *Updating* state.
- SOC - Charging / SOC - Discharging moved into STATUS. User writes register high priority immediately.
- Added persistent *Balancer refresh interval* (default 30 s). Removed the History Export button.

### 1.4.14
- Replaced *Set SOC accurate* with separate **Set SOC - charge** and **Set SOC - discharge** (plus all-battery versions), using the same curves as the measurement rows. BMS setting changes hold the radio for the whole write, delay and verification.

### 1.4.13
- Fixed stale *Waiting for BMS* states. A balancer holds one radio lease across connect, subscribe, request and read; contention shows as `Queued`.

### 1.4.12
- Coordinated scheduling for BMS connections, reads and balancer traffic; BMS reads serialised. Balancer readiness now requires all three batteries connected and read once, replacing the fixed 10 s delay.

### 1.4.11
- One process-wide **Bluetooth coordinator** for the three BMS and three balancers: BMS connections take priority and are serialised; balancers wait until the BMS links are stable and pause when one drops.
- Separate retry settings (BMS 5 s, balancers 30 s). Added a read-only coordinator diagnostics endpoint.

### 1.4.10
- Removed the fixed Balancer 1→2→3 polling order: a timed-out balancer goes first next cycle, healthy ones rotate. Post-disconnect settling raised from 2 s to 4 s.

### 1.4.9
- Default BMS refresh interval 30 s (saved settings unchanged).
- Balancers reuse the last known Windows Bluetooth identity, share one 12 s discovery scan, explicitly disconnect failed/stale GATT clients, and are polled one at a time with a settling gap. Subscriptions limited to DALY's FFF1 channel.

### 1.4.8
- Removed the derived SOC from the *Average* voltage row; every voltage-derived `Set SOC` now uses the *SOC - Discharging* curve (fixed 100% stays 100%).
- Float protection confirmed as a **server-side** policy checked every 3 s without a browser. Changing *Switch to Float when SOC* re-initialises *Switch to Bulk when SOC* five points lower.
- Added *Save history period* (default 7 days, rolling deletion). BMS refresh and control operations serialised per battery.

### 1.4.7
- Added **SOC - Charging** and **SOC - Discharging** rows: bounded linear interpolation of average cell voltage against LiFePO4 reference curves (Renogy four-cell tables). Comparison indicators only; they do not replace the DALY coulomb-counted SOC.

### 1.4.6
- Every active DALY `0x98` alarm bit decoded to text, one per line.
- Directional swipe navigation Dashboard ↔ BMS ↔ Balance.
- Durable **SQLite measurement history**: Dashboard snapshot every 10 s plus each new BMS/balancer reading; History page with recent records and JSON Lines export.
- Disconnected balancers are prioritised and retried every 5 s; stale cached identities are cleared after repeated failures.

### 1.4.5
- Balance: removed Minimum/Maximum rows, *Difference* → *Max difference*. BMS: *Cell average* → *Average*, *Cell spread* → *Max difference*.

### 1.4.4
- Balance current now uses the balancer's own current byte in response `0x93` with the observed 0.01 A scale (`0x55` = 0.85 A); the unrelated `0x90` pack current is no longer shown.

### 1.4.3
- Expanded balancer diagnostics stay open across refreshes, remembered per balancer.

### 1.4.2
- Balance page rebuilt as a compact four-column matrix matching BMS, with red/blue dots for highest/lowest cell; raw Bluetooth diagnostics in collapsed sections.

### 1.4.1
- Balancers (`DL-BAL1`–`3`) monitored directly over their own FFF1 channel (no BMS UART link assumed). Frames are reassembled and validated; semantic cards for status, current, position, statistics, temperatures, cell count, cycles and cell voltages. Device names match by prefix/substring. Read-only requests only.

### 1.4.0
- New **Balance** page for `DL-BAL1`–`3` with staggered persistent Bluetooth connections, per-characteristic GATT values, retained raw frames for protocol mapping, manual and automatic refresh.

## 1.3 — Persistent BMS Bluetooth links

### 1.3.18
- BMS page heading **BMS House**; higher-contrast dial needle and hub in the Light UI.

### 1.3.17
- Heading *BMS House Battery*. The backend *Set SOC accurate* curve now matches the displayed Cell-average curve (3.60 V charging endpoint, same interpolation and rounding).

### 1.3.16
- Dashboard House SOC is the average of all valid DALY SOC readings (one is enough). Start and Bow SOC come only from their own MasterShunts.
- Thresholds renamed **Switch to Float when SOC** / **Switch to Bulk when SOC**; the current-based Bulk delta became an absolute SOC threshold with at least 5 points of hysteresis, validated in client and server with an explanatory pop-up.

### 1.3.15
- Float protection uses the same live DALY House SOC as the Dashboard and waits while it is unavailable, preventing a conflicting MasterShunt value from triggering Float during Bluetooth start-up. The SOC source is reported in the policy status.
- Settings inputs aligned in one column; larger table and button fonts.

### 1.3.14
- Four equal-width Measurement columns, compressed rows and controls, uniform one-pixel grid-gap separators.

### 1.3.13
- Replaced the shared DALY event loop with **three failure-isolated workers** (one Windows thread and asyncio loop per battery), started in order with one-second staggering. A stall on one battery no longer blocks the others. Manual *Updating all batteries* has a 12 s server-controlled maximum.

### 1.3.11
- Aligned *All charge ON* / *All discharge ON* with their rows; OFF variants on a full-width bottom row.
- Bulk-resume delta became an absolute discharge current in amperes (later replaced in 1.3.16). Float settings indented with a guide line; all Measurement separators restored.
- Longer DALY discovery and pauses between sequential connections; Battery 3 ahead of Battery 2 so a stalled Battery 2 handshake cannot starve it.

### 1.3.10
- Dashboard Storage SOC uses the live DALY SOC. Separate *All charge ON/OFF* and *All discharge ON/OFF* controls with high-contrast Light UI styles.

### 1.3.9
- Removed the redundant SOC gauge row and connection states; larger summary values; all-battery actions in the left Controls column; guarded *All charge OFF* / *All discharge OFF* with backups and confirmation.

### 1.3.8
- "Concept A" styling: compact battery summary cards and grouped comparison sections. Immediate *Updating…* feedback, latest refresh time and interval shown.
- Hard deadlines around BLE connect and notification setup so a hung Windows connection cannot block refreshes.

### 1.3.7
- Removed the Charge/Discharge MOS rows. Added configurable Float-to-Bulk hysteresis and an informational Bulk pop-up. Bidirectional Dashboard ↔ BMS touch navigation. Better BLE reconnects by caching discovered devices and avoiding scans while GATT connections are open.

### 1.3.6
- Sequential connection of missing batteries for Windows BLE reliability. Voltage-derived SOC beside Cell average with individual and *Set all* accurate-SOC controls. Natural formatting of balancing cells; Dashboard → BMS left-swipe.

### 1.3.5
- Space between the SOC gauge and its value; remaining capacity rounded to whole Ah.

### 1.3.4
- Semicircular analog SOC dial (red/amber/green ranges, pointer). Fixed DALY balancing bit order (Bit0 = Cell 1 … Bit47 = Cell 48) and filtered against the reported cell count.
- Added *Connection retry interval* (default 5 s, 1–300 s); an unexpected disconnect wakes the reconnect loop immediately.

### 1.3.3
- Analog SOC dials, *Cell average* row, red/blue dots for highest/lowest cell, styled in-app dialogs replacing browser confirms. Added *BMS pop-up duration* (default 3 s, 1–60 s).

### 1.3.2
- BMS page rebuilt as a comparison matrix (shared label column, one column per battery) with SOC gauges, per-battery **Set SOC 100%**, Charge ON/OFF and Discharge ON/OFF. Every control confirms, saves a timestamped pre-change backup, applies the DALY command and refreshes all batteries.
- DALY write commands: SOC `0x21` (100.0% = `1000`), Charge MOS `0xDA`, Discharge MOS `0xD9`, legacy `0xA5` framing with write source byte `0x80`. Protection-threshold registers stay inaccessible.

### 1.3.1
- BMS overview as three side-by-side battery columns with per-row alignment and one row per cell voltage.

### 1.3.0
- **Persistent BLE connections** to BATTERY 1–3, refreshed concurrently with automatic reconnect. Added *BMS refresh interval* (5–300 s). Read-only.

## 1.2
### 1.2.0
- **Dashboard** rename (was Overview) and a functional **BMS** page for `BATTERY 1`–`3` using the DALY legacy `0xA5` BLE protocol: pack voltage, current, SOC, temperatures, capacity, cell voltages, spread, MOS states, balancing, alarms, cycles. Sequential reads, isolated failures, read-only. Added `bleak` to requirements.

## 1.1
### 1.1.0
- Functional **Settings** page persisted atomically in `user_settings.json`: default AC limit (3–15 A, used by Marina mode), Float protection ON/OFF, House SOC threshold (50–100%), warning pop-up duration (1–60 s). Defaults 15 A, ON, 95%, 8 s.

## 1.0 — First production release

### 1.0.22
- Removed the fixed *Type:* header from Storage tiles (subtitle is e.g. `MLI 960A`).

### 1.0.21
- House Battery Amp and Watt readings blink red at or below −100 A (not for Start or Bow); Dark and Light styles.

### 1.0.20
- Visible labels shortened: *Charger Bowthruster* → *Charger Bow*, *Bowthruster Battery* → *Bow Battery*. Internal keys unchanged.

### 1.0.19
- Frequency labels replaced by `f` (e.g. `f 50hz`).

### 1.0.18
- **Local HTTPS** on port 8000 with a private Mastervolt CA and renewable server certificate (localhost, computer name, local IPv4 addresses), automatic trust for the current Windows user, `install_pc_certificate.cmd`, a one-time iPhone CA installer on port 8001, and `/local-ca.cer`. Bound only to an RFC1918 LAN address (or localhost); no public exposure. See [docs/local-https.md](docs/local-https.md).

### 1.0.17
- High-contrast **Light UI** for sunlight, Dark stays default; toggle next to the clock; choice stored on the device and applied before first paint; theme colour follows.

### 1.0.16
- The Float information dialog auto-closes after eight seconds; OK closes it immediately.

### 1.0.15
- The *Supporting* status blinks grey/red while AC support is active; *Inverting* stays steady.

### 1.0.14
- A valid AC-limit entry clears the highlighted mode button. Float message punctuation corrected.

### 1.0.13
- High-SOC Float threshold 98% → 95%. One-time information dialog when the policy actually switches Charger House, Alternator or Solar to Float, driven by a server-side event counter.

### 1.0.12
- AC limit restricted to whole numbers 3–15 in UI and server with the message *Enter a value between 3 and 15*. AC Loads shows green-bullet Active/Inactive; output frequency styled as grey secondary text.

### 1.0.11
- SUP blinks green/translucent red while CombiMaster field 49 reports actual *Supporting*. New **Inverter** tile (battery-side V/A/W from CombiMaster fields 11/12, zero unless field 47 *Inverting* or 49 *Supporting* is active); `INV` control moved there. Inverter DC current is subtracted from Other DC Loads to avoid double counting.

### 1.0.10
- Automatic **high-SOC Float policy** (House ≥ 98%): active Charger House, Solar and Alternator are forced to Float using CombiMaster 42/43, Solar 18/19 and Alpha Pro 37/38, verified via charger-state 3 with a 20 s retry cooldown. Inactive sources are not switched on. Status exposed as `high_soc_float_policy` in `/api/energy`.

### 1.0.9
- `SUP` replaces the Shore Power `INV` control: CombiMaster Btm3 field 11 *AC IN support* (writable 0/1, write with read-back). Marina mode also enables the inverter, AC IN support and sets 15 A.

### 1.0.8
- Total DC load excludes AC Loads. Added **Sail mode** (Anchor plus Engine ECU ON). Shore Power shows connection state and input frequency (CombiMaster field 4); AC Loads shows output frequency (field 6). System OK and clock moved into the header row.

### 1.0.7
- **Alternator** load tile from Alpha Pro fields 6 (battery voltage) and 8 (field current); included in Total DC load and subtracted from Other DC Loads first. Switching the alternator off from either tile needs a confirmation dialog. Solar shows `PV ###V`.

### 1.0.6
- Fixed the AC-limit field losing focus: the one-second refresh no longer replaces the focused input, keeping the iOS keyboard open.

### 1.0.5
- iPhone-friendly numeric AC-limit input, validated in UI and server. *Already active* dialog for a pressed active mode. SOC 100 drawn without `%`.

### 1.0.4
- Compact AC-limit input; mode progress modal 6 s; active mode button turns green; operating any tile control clears the active mode.

### 1.0.3
- Fixed AC-limit input width; label moved below it. Added the mode progress modal (*Setting Motor/Anchor/Marina mode*).

### 1.0.2
- Added **Motor**, **Anchor** and **Marina** modes built from existing verified controls; Anchor and Marina never switch Engine ECU power off. Shore Power AC-limit label placement; Engine ECU state indicator.

### 1.0.1
- Storage tiles read battery type and capacity from MasterShunt metadata when safely identifiable (`Type: XXX NNNA`, LiFePO4/AGM fallback). AC limit moved into the Shore Power tile; inverter control moved right and labelled `INV`; redundant standalone controls removed.

### 1.0.0
- First production release. Storage sublabels: House `LiFePO4`, Start `AGM`, Bowthruster `AGM`. No mapping, calculation, control or safety changes from 0.18.17.

---

# Pre-1.0 development (0.x)

Hardware-verification work: reverse-engineering MasterBus fields, the Engine ECU and Alpha Pro controls, and the dashboard maths. Field-level detail is in [docs/hardware-notes.md](docs/hardware-notes.md).

## 0.18 — Product pictures, UI redesign, alternator control

### 0.18.17
- ECU shutdown warning: *Switching off the Engine ECU will STOP the engine immediately if it is running!* Slightly less opaque/blurred backdrop. Alternator tile shows Alpha Pro temperature (field 32). Tile label *Engine ECU power*.

### 0.18.16
- Removed the boxed S/S/L section initials; SOURCES, STORAGE and LOADS labels share one aligned icon column.

### 0.18.15
- Tightened AC/DC → voltage → amps → watts spacing; more separation before storage SoC. `(House)` removed from AC Loads / Other DC Loads; `AC limit A` → *Shorepower AC limit (amps)*. Version shown in the footer.

### 0.18.14
- Removed inter-column gaps between AC/DC, voltage, current and watts. Storage header shows signed House power as Charging / Discharging / Idle.

### 0.18.13
- Engine ECU OFF uses a Mastervolt-styled in-app safety dialog; **Keep ECU ON** is the focused default, and Escape or tapping outside also cancels.

### 0.18.12
- Larger iPhone typography, fixed aligned columns with tabular numerals, circular SoC rings, signed watts in Storage, horizontal inverter/AC-limit layout.
- Engine ECU OFF requires explicit confirmation (cancelling leaves it ON).

### 0.18.11
- DC-balance solver decides by Alpha Pro **field 5** (actual charge state), not shaft rotation: states 1/2/3 → calculate alternator current; 0/5 → current is zero and the house-load baseline is learned, even if the shafts still turn.

### 0.18.10
- Alternator / Other DC balance rewritten around a low-pass-filtered (α 0.25) total-house-load baseline (see hardware notes). Alternator **ON** now only clears Stop Charge (field 39 = 0 + commit 40), to test whether the Alpha Pro resumes automatically.

### 0.18.9
- Alpha Pro state `5` decoded as **Stopped**. Alternator ON first clears the latched Stop Charge, waits, then sends Bulk (field 33 + commit 34); OFF verifies state 5.

### 0.18.8
- Alternator current = signed MasterShunt House A + all DC load A, clamped at 0. Alternator gets normal ON/OFF: ON = field 33 (Bulk) + commit 34, OFF = field 39 (Stop charge) + commit 40. Removed the one-way Float/OFF action.

### 0.18.7
- Visual redesign to the approved dark charcoal / teal / green mockup: teal SVG pictograms replace emoji, larger bold device names, compact second-line status, compact green SoC bar.

### 0.18.6
- Compact iPhone dashboard: compressed header and rows (45 px), smaller icons and typography, shorter bottom navigation, desktop-width tuning.

### 0.18.5
- Solar tile shows SCM field 6 (battery/output voltage) as *Voltage*; field 4 kept as `panel_voltage`.

### 0.18.4
- Solar OFF fixed: write field 12, wait 50 ms, send the commit token to field 13, verify OFF by read-back. ON stays permissive because the SCM may legitimately fall back to OFF with insufficient PV.

### 0.18.3
- **Baseline reset** to 0.17.10 (including the Mass Charger state-0 fix). The product-picture work from 0.18.0–0.18.2 is deliberately *not* in this line at this point; numbering continues from here.

### 0.18.2
- Product images for the remaining tiles (batteries, Charger Start/Bow, Engine ECU, AC Loads, Other DC), cached once and shared.

### 0.18.1
- Worked around the Mastervolt image CDN's HTTP 403 for Python's downloader: browser-style headers with Mastervolt Referer, `curl.exe` fallback, image validation. `py cache_product_images.py --force` retries manually.

### 0.18.0
- Mastervolt product photos on the Shore Power, Charger House, Alternator and Solar tiles, downloaded once into `static/products/` (never hotlinked) and cached by the service worker.

## 0.17 — Verified fixed map and source states

### 0.17.10
- Mass Charger field 1 raw `0` displays *Off* (fixes `Unknown (0)` on Charger Bowthruster).

### 0.17.9
- Solar ON no longer returns HTTP 409 at night: dedicated `set_solar_enabled()` sends field 12 and treats an immediate OFF read-back as normal.

### 0.17.8
- Alpha Pro tile got a one-way OFF button sending Float (fields 37/38) and watching field 5 for state 3. Superseded in 0.18.8.

### 0.17.7
- Solar field 12 (`On/Off`) control enabled, with the same behaviour noted in 0.17.9.

### 0.17.6
- Charger ON/OFF moved to the Charger House tile; Solar and Alternator controls placed (disabled until verified). *Total Output* → *Total DC load*.

### 0.17.5
- Solar and Alternator show the **actual** charger-state fields (Solar field 3, Alpha Pro field 5: 0 Off, 1 Bulk, 2 Absorption, 3 Float; anything else `Unknown (n)`) instead of derived states.

### 0.17.4
- Derived Solar (Charging/Idle) and Alternator (Stopped/Charging/Running) state indicators. Superseded by 0.17.5.

### 0.17.3
- Mass Charger field 1 raw `5` displays *Constant voltage*.

### 0.17.2
- Fixed Charger House state decoding: CombiMaster uses 0 Off / 1 Bulk / 2 Absorption / 3 Float, Mass Charger uses 2 / 3 / 4.

### 0.17.1
- Display-only *State: Bulk / Absorption / Float* on Charger House, Start and Bowthruster.

### 0.17.0
- **Fixed verified measurement map** replaces heuristic field selection (which had used Solar field 4 as both voltage and power, treated Alpha Pro field 21 as alternator current, misread Mass Charger outputs as house-bus load and picked wrong Yanmar fields). Discovery code remains for diagnostics only.

## 0.16
### 0.16.1
- Charger Start/Bow house-bus load = input voltage × input current (fields 4/5; 6/7 are output). Engine ECU input current estimated from output power at 85% efficiency (fields 39/40/41).

### 0.16.0
- Alternator current derived from the house DC balance because the Alpha Pro exposes no trustworthy output-current field, using the shaft signals (fields 11/12) to break the circular dependency with *Other DC Loads*. Solar W = field 6 × field 5.

## 0.15
### 0.15.5
- `masterbus_snapshot.py` uses each device's known `max_index` instead of scanning 256 fields.

### 0.15.4
- Rebuilt `masterbus_snapshot.py` against the current `MasterBusService` + `ControlDiscovery` (it still imported a removed `MasterBusDiscovery` class).

### 0.15.3
- Added `field_audit.py`, which prints every field number the application currently uses, including persisted dynamic mappings from `device_maps.json`.

### 0.15.2
- Suppress the benign Windows `ConnectionResetError` (WinError 10054) logged when an iPhone Safari/PWA socket is closed. Only that case is filtered.

### 0.15.1
- **Engine ECU web control fixed**: a leftover v0.13 start-up routine re-discovered Yanmar field 56 and overwrote the verified field-43 mapping. The overwrite was removed; the ECU stays mapped to field 43.

### 0.15.0
- Engine ECU ON/OFF mapped to **field 43** (`Mac/Magic On`, OFF 0.0 / ON 1.0, no commit) from the MasterAdjust USB capture.

## 0.14
### 0.14.2
- Added `ecu_commit_test.py` to test whether field 56 needs a commit token on companion field 57 (the earlier failure only proved it was omitted).

### 0.14.1
- Engine ECU web write disabled again after the generic field-56 dropdown write failed; added USBPcap capture tooling (`ecu_capture_session.ps1`, `find_masterbus_usb.ps1`).

## 0.13 and earlier
### 0.13.2
- Rebuilt `masterbus_control_discovery.py` (methods had been generated at module scope); `self_check.py` now checks both classes.

### 0.13.1
- Fixed an indentation error that left `MasterBusService` without `open()`; added `self_check.py`.

### 0.11.1
- Added read-only `control_inspect.py` (Charger Start/Bow fields 60–67, Yanmar fields 54–58).
