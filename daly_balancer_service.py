"""Read-only Bluetooth discovery and monitoring for DALY active balancers."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
import time
from datetime import datetime, timezone
from daly_bms_service import decode,decode_alarm_bytes
from bluetooth_coordinator import bluetooth_coordinator
from ble_events import ble_log

DEVICE_NAMES=("DL-BAL1","DL-BAL2","DL-BAL3")
NOTIFY_UUID="0000fff1-0000-1000-8000-00805f9b34fb"
WRITE_UUID="0000fff2-0000-1000-8000-00805f9b34fb"
COMMANDS=tuple(range(0x90,0x99))
STANDARD_NAMES={
    "00002a19-0000-1000-8000-00805f9b34fb":"Battery level",
    "00002a24-0000-1000-8000-00805f9b34fb":"Model number",
    "00002a25-0000-1000-8000-00805f9b34fb":"Serial number",
    "00002a26-0000-1000-8000-00805f9b34fb":"Firmware revision",
    "00002a27-0000-1000-8000-00805f9b34fb":"Hardware revision",
    "00002a29-0000-1000-8000-00805f9b34fb":"Manufacturer",
}

# Timing and robustness settings. Module constants so the self-test can shrink them.
INITIAL_DELAY=8.0             # let the three safety-critical BMS links settle before adding balancers
GATT_TIMEOUT=6.0              # every single GATT read or write: a hung Windows call must never hold the radio
CONNECT_TIMEOUT=18.0
NOTIFY_TIMEOUT=7.0
DISCONNECT_TIMEOUT=10.0
COMMAND_SPACING=.15
FRAME_WAIT=2.0                # how long to wait for the answers to one round of status requests
READ_ATTEMPTS=2               # a round is repeated once for the commands that were not answered
SETTLE_SECONDS=4.0            # Windows releases a finished GATT connection asynchronously
INCOMPLETE_RETRY_SECONDS=15.0
MAX_BACKOFF_SECONDS=300.0     # failed balancers are retried after retry*2^n seconds, at most this long
WATCHDOG_SECONDS=600.0        # no successful read for this long while the BMS links are healthy: rebuild the worker
CRASH_BACKOFF_BASE=5.0        # seconds before the supervisor restarts a crashed worker (doubles per crash, at most 60 s)
USE_CACHED_SERVICES=True      # Windows: skip GATT service discovery on every connect (the DALY layout never changes)


class BalancerDeferred(RuntimeError):
    """Balancer work intentionally postponed while the BMS has radio priority."""

class BalancerWorkerRestart(RuntimeError):
    """Raised by the watchdog to rebuild the balancer event loop and every Bluetooth object in it."""

def describe_value(raw:bytes):
    raw=bytes(raw)
    text=None
    try:
        decoded=raw.decode("utf-8").strip("\0\r\n ")
        if decoded and all(ch.isprintable() for ch in decoded):text=decoded
    except UnicodeDecodeError:
        pass
    return {
        "hex":raw.hex(" ").upper(),
        "text":text,
        "unsigned_le":int.from_bytes(raw,"little") if 0<len(raw)<=8 else None,
        "length":len(raw),
    }

def status_request(command:int)->bytes:
    """Build a DALY legacy read request; these commands do not change settings."""
    frame=bytearray((0xA5,0x40,command,0x08,0,0,0,0,0,0,0,0));frame.append(sum(frame)&0xFF);return bytes(frame)

class DalyBalancerService:
    """Poll three balancers one at a time (connect, read, disconnect) and expose every readable/notified GATT value.

    A measurement is only published (and only then gets a `captured_at`) once every status command was answered,
    so a half-read balancer can never end up in the history. Each balancer has its own schedule: healthy ones follow
    the refresh interval, failing ones back off exponentially instead of hammering the Windows Bluetooth stack."""

    def __init__(self):
        self.lock=threading.RLock();self.stop_event=threading.Event();self.refresh_event=threading.Event()
        self.thread=None;self.loop=None;self.clients={};self.ble_available=importlib.util.find_spec("bleak") is not None
        self.settings_getter=lambda:{"balancer_refresh_interval":30,"balancer_connection_retry_seconds":30}
        self.buffers={name:bytearray() for name in DEVICE_NAMES};self.decoded={name:{} for name in DEVICE_NAMES};self.known_devices={};self.connection_failures={name:0 for name in DEVICE_NAMES}
        self.static_values={}
        self.cycle_cursor=0
        self.single_queue=[]
        self.refresh_generation=0;self.completed_generation=0
        self.next_due={name:0.0 for name in DEVICE_NAMES};self.last_success={name:None for name in DEVICE_NAMES}
        self.last_progress=time.monotonic();self.watchdog_level=0;self.worker_restarts=0;self.restarts_without_success=0;self.wedged=False
        self.devices={name:{"name":name,"state":"not_read","values":[],"status":{},"error":None,"captured_at":None} for name in DEVICE_NAMES}

    def start(self,settings_getter=None):
        if self.thread and self.thread.is_alive():return
        if settings_getter:self.settings_getter=settings_getter
        self.stop_event.clear();self.thread=threading.Thread(target=self._thread_main,daemon=True,name="daly-balancers");self.thread.start()

    def stop(self):
        self.stop_event.set();self.refresh_event.set()
        if self.thread:self.thread.join(timeout=8)

    def refresh_all(self):
        self.refresh_generation+=1
        self.refresh_event.set()
        return {"accepted":True,"generation":self.refresh_generation}

    def refresh_one(self,name):
        """Queue a manual refresh of one balancer; the next cycle reads only that balancer."""
        if name not in DEVICE_NAMES:raise ValueError("Unknown balancer")
        if not self.ble_available:raise RuntimeError("Windows Bluetooth component unavailable")
        with self.lock:
            if name not in self.single_queue:self.single_queue.append(name)
            if self.devices[name].get("state") in ("not_read","connected","disconnected","error"):
                self.devices[name]={**self.devices[name],"state":"queued","error":None}
        self.refresh_event.set()
        return {"started":True,"device":name}

    def _requeue_manual(self,names):
        """Put unfinished manual refreshes back in front so a deferred attempt is retried."""
        with self.lock:
            for name in reversed(names):
                if name not in self.single_queue:self.single_queue.insert(0,name)
        self.refresh_event.set()

    def snapshot(self):
        settings=self.settings_getter();interval=float(settings["balancer_refresh_interval"]);now=time.monotonic()
        stale_after=max(180.0,4*interval)
        with self.lock:
            devices={}
            for name,value in self.devices.items():
                age=None if self.last_success[name] is None else now-self.last_success[name]
                devices[name]={**value,"connection_failures":self.connection_failures[name],"values":[dict(item) for item in value.get("values",[])],
                    "last_success_age_seconds":None if age is None else round(age,1),"stale":age is not None and age>stale_after,
                    "next_attempt_in_seconds":max(0.0,round(self.next_due[name]-now,1))}
            return {"ble_available":self.ble_available,"persistent_connections":False,"connection_mode":"sequential-read-disconnect","shared_discovery":True,
                "busy":self.completed_generation<self.refresh_generation,
                "refresh_generation":self.refresh_generation,"completed_generation":self.completed_generation,
                "refresh_interval_seconds":int(interval),
                "retry_interval_seconds":int(settings["balancer_connection_retry_seconds"]),"coordinator":bluetooth_coordinator.snapshot(),
                "wedged":self.wedged,"worker_restarts":self.worker_restarts,"watchdog_level":self.watchdog_level,
                "devices":devices}

    def _set(self,name,**updates):
        with self.lock:self.devices[name]={**self.devices[name],**updates}

    def _thread_main(self):
        """Supervisor: whatever goes wrong inside the Bluetooth loop, the balancers are rebuilt and polled again."""
        if not self.ble_available:
            for name in DEVICE_NAMES:self._set(name,state="error",error="Windows Bluetooth component unavailable")
            return
        crashes=0
        while not self.stop_event.is_set():
            try:
                asyncio.run(self._run())
                crashes=0
            except BalancerWorkerRestart as exc:
                self.restarts_without_success+=1;self.wedged=self.restarts_without_success>=3
                ble_log.log("balancers","worker_restart",f"{exc} (restart {self.restarts_without_success} without a successful read)",failure=True)
            except Exception as exc:
                crashes+=1
                for name in DEVICE_NAMES:self._set(name,state="error",error=f"Balancer worker crashed ({exc}); restarting automatically")
                ble_log.log("balancers","worker_crash",repr(exc),failure=True)
                self.stop_event.wait(min(60.0,CRASH_BACKOFF_BASE*2**min(crashes,4)))
                continue
            self.stop_event.wait(1.0)

    def _schedule(self,name,ok,incomplete=False,deferred=False):
        """Decide when this balancer is due again."""
        settings=self.settings_getter();interval=float(settings["balancer_refresh_interval"]);base=float(settings["balancer_connection_retry_seconds"])
        if deferred:delay=2.0
        elif ok:delay=interval
        elif incomplete:delay=INCOMPLETE_RETRY_SECONDS
        else:delay=min(MAX_BACKOFF_SECONDS,base*2**(min(max(1,self.connection_failures[name]),8)-1))
        self.next_due[name]=time.monotonic()+delay

    def _watchdog(self):
        """A balancer stall while the BMS links are healthy first forces a fresh scan, then a rebuild of the worker."""
        stalled=time.monotonic()-self.last_progress
        if stalled>=WATCHDOG_SECONDS:
            self.watchdog_level=2;self.worker_restarts+=1;self.last_progress=time.monotonic()
            raise BalancerWorkerRestart(f"no successful balancer read for {stalled:.0f} s while the BMS links are healthy")
        if stalled>=WATCHDOG_SECONDS/2 and self.watchdog_level<1:
            self.watchdog_level=1;self.known_devices.clear()
            for name in DEVICE_NAMES:self.connection_failures[name]=0
            ble_log.log("balancers","watchdog_rescan",f"no successful balancer read for {stalled:.0f} s; forgetting known devices",failure=True)

    async def _discover(self,names):
        """Find every missing balancer in one radio scan instead of three competing scans."""
        from bleak import BleakScanner
        if not names:return {}
        async with bluetooth_coordinator.alease("balancer",timeout=3) as granted:
            if not granted:raise BalancerDeferred("Waiting for BMS connection priority")
            with ble_log.timed("balancers","scan"):records=await BleakScanner.discover(timeout=12,return_adv=True)
        pairs=records.values() if isinstance(records,dict) else records
        matches={}
        for device,advertisement in pairs:
            visible=(getattr(advertisement,"local_name",None) or getattr(device,"name",None) or "")
            for name in names:
                if name.casefold() in visible.casefold():matches.setdefault(name,device)
        ble_log.log("balancers","scan",f"looked for {', '.join(names)}; found {', '.join(matches) or 'none'}")
        return matches

    async def _disconnect(self,name,client=None):
        target=client or self.clients.pop(name,None)
        if target:
            try:
                with ble_log.timed(name,"disconnect"):await asyncio.wait_for(target.disconnect(),timeout=DISCONNECT_TIMEOUT)
            except asyncio.TimeoutError:ble_log.log(name,"disconnect_timeout",f"disconnect did not finish within {DISCONNECT_TIMEOUT:g} s (possible leaked Windows Bluetooth handle)",failure=True)
            except BaseException:pass

    async def _connect(self,name,device):
        from bleak import BleakClient
        self._set(name,state="scanning",error=None)
        if not device:raise RuntimeError(f"{name} not found; retrying automatically")
        self.known_devices[name]=device
        self._set(name,state="connecting")
        def disconnected(disconnected_client,n=name):
            # A deliberate read-complete disconnect pops the client first and
            # must not replace the successful snapshot with an error state.
            if self.clients.get(n) is disconnected_client:
                self.clients.pop(n,None)
                self._set(n,state="disconnected",error="Bluetooth connection lost; retrying automatically")
        options={"timeout":15,"disconnected_callback":disconnected}
        if USE_CACHED_SERVICES and sys.platform=="win32":options["winrt"]={"use_cached_services":True}
        try:client=BleakClient(device,**options)
        except TypeError:
            options.pop("winrt",None);client=BleakClient(device,**options)   # a bleak without WinRT client arguments
        ble_log.log(name,"connect_try",quiet=True)
        try:
            with ble_log.timed(name,"connect"):await asyncio.wait_for(client.connect(),timeout=CONNECT_TIMEOUT)
            with ble_log.timed(name,"notify"):await asyncio.wait_for(client.start_notify(NOTIFY_UUID,lambda sender,data,n=name:self._notification(n,sender,data)),timeout=NOTIFY_TIMEOUT)
        except BaseException:
            await self._disconnect(name,client)
            raise
        self.clients[name]=client;self.connection_failures[name]=0
        self._set(name,state="connected",error=None)
        ble_log.log(name,"connect_ok",quiet=True)

    def _notification(self,name,sender,data):
        uuid=str(getattr(sender,"uuid",sender)).lower();value=describe_value(data)
        with self.lock:
            rows=self.devices[name].get("values",[]);found=False
            for row in rows:
                if row.get("uuid")==uuid:
                    frames=list(row.get("frames_hex",[]));frames.append(value["hex"]);row.update(value);row["frames_hex"]=frames[-50:];row["source"]="notification";found=True;break
            if not found:rows.append({"service":"—","uuid":uuid,"label":STANDARD_NAMES.get(uuid,"Unknown characteristic"),"properties":["notify"],"source":"notification","frames_hex":[value["hex"]],**value})
        if uuid==NOTIFY_UUID:self._consume_frames(name,data)

    def _consume_frames(self,name,data):
        """Reassemble 13-byte DALY UART frames. Nothing is published here: the status is built once the read is complete."""
        buffer=self.buffers[name];buffer.extend(data)
        while len(buffer)>=13:
            start=buffer.find(b"\xA5")
            if start<0:buffer.clear();return
            if start:del buffer[:start]
            if len(buffer)<13:return
            frame=bytes(buffer[:13]);del buffer[:13]
            if frame[3]!=8 or (sum(frame[:12])&0xFF)!=frame[12]:continue
            command,values=decode(frame)
            # Standalone DALY balancers reuse byte 3 of the 0x93 payload for
            # balance current in 0.01 A. Live frames 0x55 and 0x32 therefore
            # represent 0.85 A and 0.50 A. Do not use 0x90 here: that field is
            # pack current and legitimately remains zero while balancing.
            if command==0x93:values["balance_current_a"]=frame[7]/100
            with self.lock:self.decoded[name].setdefault(command,[]).append(values);self.decoded[name][command]=self.decoded[name][command][-16:]

    def _update_status(self,name):
        groups=self.decoded[name];summary=(groups.get(0x90) or [{}])[-1];limits=(groups.get(0x91) or [{}])[-1];temps=(groups.get(0x92) or [{}])[-1];activity=(groups.get(0x93) or [{}])[-1];meta=(groups.get(0x94) or [{}])[-1];balance=(groups.get(0x97) or [{}])[-1];alarm=(groups.get(0x98) or [{}])[-1]
        cell_count=meta.get("cell_count",0);cells=[]
        for group in sorted(groups.get(0x95,[]),key=lambda item:item.get("frame_number",0)):cells.extend(group.get("cell_voltages_mv",[]))
        if cell_count:cells=cells[:cell_count]
        temperatures=[]
        for group in sorted(groups.get(0x96,[]),key=lambda item:item.get("frame_number",0)):temperatures.extend(group.get("temperatures_c",[]))
        sensor_count=meta.get("temperature_sensor_count",0)
        if sensor_count:temperatures=temperatures[-sensor_count:]
        high=limits.get("highest_cell_mv");low=limits.get("lowest_cell_mv")
        if cells:high=max(cells);low=min(cells)
        balancing=[n for n in balance.get("balancing_cells",[]) if not cell_count or n<=cell_count]
        status={
            "balance_active":bool(balancing),"balance_position":balancing,
            "reported_current_a":activity.get("balance_current_a"),"pack_voltage_v":summary.get("pack_voltage_v"),
            "highest_cell_mv":high,"lowest_cell_mv":low,
            "average_cell_mv":sum(cells)/len(cells) if cells else None,
            "cell_delta_mv":high-low if high is not None and low is not None else None,
            "cells_mv":cells,"temperatures_c":temperatures,
            "cell_count":cell_count or len(cells) or None,"cycles":meta.get("charge_discharge_cycles"),
            "alarms":decode_alarm_bytes(alarm.get("alarm_bytes_hex","")),
            "valid_frame_count":sum(len(items) for items in groups.values()),
        }
        with self.lock:self.devices[name]["status"]=status;self.devices[name]["captured_at"]=datetime.now(timezone.utc).isoformat()

    async def _static_rows(self,name,client):
        """Device information (model, serial, GATT layout) never changes: read it once per balancer, not on every cycle."""
        if name in self.static_values:return self.static_values[name]
        rows=[];clean=True
        for service in client.services:
            for char in service.characteristics:
                properties=list(char.properties);base={"service":str(service.uuid).lower(),"uuid":str(char.uuid).lower(),"label":STANDARD_NAMES.get(str(char.uuid).lower(),char.description or "Unknown characteristic"),"properties":properties}
                if "read" in properties:
                    try:rows.append({**base,"source":"read",**describe_value(await asyncio.wait_for(client.read_gatt_char(char),timeout=GATT_TIMEOUT))})
                    except Exception as exc:
                        clean=False;rows.append({**base,"source":"read error","hex":"","text":None,"unsigned_le":None,"length":0,"error":str(exc) or type(exc).__name__})
                elif "notify" in properties or "indicate" in properties:
                    rows.append({**base,"source":"waiting for notification","hex":"","text":None,"unsigned_le":None,"length":0})
                else:
                    rows.append({**base,"source":"not readable","hex":"","text":None,"unsigned_le":None,"length":0})
        if clean:self.static_values[name]=rows
        return rows

    async def _wait_frames(self,name,commands):
        deadline=time.monotonic()+FRAME_WAIT
        while time.monotonic()<deadline:
            if all(command in self.decoded[name] for command in commands):return
            await asyncio.sleep(.02)

    async def _read(self,name):
        """Request every status command, repeat the unanswered ones once, publish only a complete status.
        Returns False (and publishes nothing) when commands stay unanswered."""
        client=self.clients[name]
        rows=await self._static_rows(name,client)
        self._set(name,state="connected",values=[dict(row) for row in rows],error=None)
        if not any(str(char.uuid).lower()==WRITE_UUID for service in client.services for char in service.characteristics):
            raise RuntimeError(f"{name} has no DALY command channel")
        # The FFF1 data channel stays silent until the app requests status.
        # Probe only DALY's documented read range; never send configuration writes.
        self.buffers[name].clear();self.decoded[name]={}
        pending=list(COMMANDS);started=time.monotonic()
        for attempt in range(1,READ_ATTEMPTS+1):
            for command in pending:
                await asyncio.wait_for(client.write_gatt_char(WRITE_UUID,status_request(command),response=False),timeout=GATT_TIMEOUT)
                await asyncio.sleep(COMMAND_SPACING)
            await self._wait_frames(name,pending)
            pending=[command for command in COMMANDS if command not in self.decoded[name]]
            if not pending:break
            ble_log.log(name,"read_retry",f"attempt {attempt}: no answer to {', '.join(f'0x{c:02X}' for c in pending)}")
        if pending:
            message=f"Incomplete status: no answer to {', '.join(f'0x{c:02X}' for c in pending)}"
            self._set(name,state="connected",error=message);ble_log.log(name,"read_incomplete",message,failure=True)
            ble_log.timing(name,"read",time.monotonic()-started,False)
            return False
        self._update_status(name)
        ble_log.timing(name,"read",time.monotonic()-started,True)
        ble_log.log(name,"read_ok",f"{sum(len(v) for v in self.decoded[name].values())} frames in {time.monotonic()-started:.1f} s",success=True,quiet=True)
        return True

    async def _disconnect_all(self):
        for name in list(self.clients):await self._disconnect(name)

    async def _idle(self):
        """Sleep until the next balancer is due, a manual/full refresh is requested, or the service stops."""
        deadline=min(self.next_due.values())
        while not self.stop_event.is_set() and time.monotonic()<deadline and not self.refresh_event.is_set():
            await asyncio.sleep(min(.25,max(.005,deadline-time.monotonic())))
        if self.refresh_event.is_set() and self.refresh_generation<=self.completed_generation and not self.single_queue:self.refresh_event.clear()

    async def _run(self):
        await asyncio.sleep(INITIAL_DELAY)
        self.last_progress=time.monotonic();was_ready=False
        try:
            while not self.stop_event.is_set():
                if not bluetooth_coordinator.bms_ready():
                    for name in DEVICE_NAMES:
                        self._set(name,state="waiting",error="Waiting for all BMS connections and initial readings")
                    self.last_progress=time.monotonic();was_ready=False      # waiting for the BMS links is not a balancer stall
                    await asyncio.sleep(1)
                    continue
                if not was_ready:self.last_progress=time.monotonic();was_ready=True   # the stall clock starts when the balancers may run
                for name in DEVICE_NAMES:
                    if self.devices[name].get("state")=="waiting":self._set(name,state="queued",error=None)
                self._watchdog()
                # Keep the three BMS sessions open, but sample balancers one at
                # a time. This avoids exceeding the practical Windows adapter
                # limit with six simultaneous DALY GATT links.
                with self.lock:manual=[n for n in self.single_queue if n in DEVICE_NAMES];self.single_queue.clear()
                full=self.refresh_generation>self.completed_generation
                if manual:
                    # Manual refresh of clicked balancer(s): they go first and
                    # nothing else is read in this cycle. It never completes a
                    # "Refresh all" generation.
                    cycle_generation=self.refresh_generation;cycle_complete=False
                    targets=manual
                else:
                    rotated=list(DEVICE_NAMES[self.cycle_cursor:]+DEVICE_NAMES[:self.cycle_cursor])
                    # A device that timed out must not remain disadvantaged in the
                    # third position. Failed devices go first; healthy devices rotate.
                    order=sorted(rotated,key=lambda name:(self.devices[name].get("state")!="error",-self.connection_failures[name]))
                    now=time.monotonic()
                    targets=order if full else [name for name in order if self.next_due[name]<=now]
                    if not targets:
                        await self._idle();continue
                    cycle_generation=self.refresh_generation;cycle_complete=full
                    self.cycle_cursor=(self.cycle_cursor+1)%len(DEVICE_NAMES)
                discover_names=[name for name in targets if name not in self.known_devices or self.connection_failures[name]>=2]
                for name in discover_names:
                    if self.connection_failures[name]>=2:self.known_devices.pop(name,None)
                    self._set(name,state="scanning",error=None)
                discovered={}
                if discover_names:
                    try:discovered=await self._discover(discover_names)
                    except BalancerDeferred as exc:
                        for name in discover_names:self._set(name,state="waiting",error=str(exc))
                        if manual:self._requeue_manual(manual)
                        await asyncio.sleep(1)
                        continue
                    except Exception as exc:
                        ble_log.log("balancers","scan_failed",repr(exc),failure=True)
                        for name in discover_names:self._set(name,state="error",error=f"Bluetooth scan failed: {exc}")
                for name in targets:
                    if self.stop_event.is_set():break
                    # A manual refresh request overtakes the rest of a running full cycle.
                    if not manual and self.single_queue:cycle_complete=False;break
                    device=discovered.get(name) or self.known_devices.get(name)
                    ok=incomplete=False;held_from=None;asked=time.monotonic()
                    try:
                        # One lease covers connect, notifications, read and all
                        # request frames. A BMS operation can no longer slip in
                        # between connect and read and leave a stale wait state.
                        async with bluetooth_coordinator.alease("balancer",timeout=8) as granted:
                            ble_log.timing(name,"radio_wait",time.monotonic()-asked,bool(granted))
                            if not granted:raise BalancerDeferred("Queued for Bluetooth radio")
                            held_from=time.monotonic()
                            await self._connect(name,device)
                            ok=await self._read(name)
                            incomplete=not ok
                    except BalancerDeferred as exc:
                        await self._disconnect(name)
                        self._set(name,state="queued",error=str(exc))
                        self._schedule(name,False,deferred=True)
                        cycle_complete=False
                        if manual:self._requeue_manual(manual[manual.index(name):])
                        break
                    except Exception as exc:
                        phase="read_failed" if name in self.clients else "connect_failed"     # the client is only registered after a successful connect
                        await self._disconnect(name);self.connection_failures[name]+=1
                        if self.connection_failures[name]>=2:self.known_devices.pop(name,None)
                        message=str(exc).strip() or f"{name} connection timed out; retrying automatically"
                        self._set(name,state="error",error=message)
                        after=f" (after {time.monotonic()-held_from:.1f} s on the radio)" if held_from else ""
                        ble_log.log(name,phase,f"{type(exc).__name__}: {message}{after}",failure=True)
                    finally:
                        if held_from:ble_log.timing(name,"radio_hold",time.monotonic()-held_from,ok)
                        await self._disconnect(name)
                    if ok:
                        self.last_success[name]=time.monotonic();self.last_progress=self.last_success[name]
                        self.watchdog_level=0;self.restarts_without_success=0;self.wedged=False
                    self._schedule(name,ok,incomplete=incomplete)
                    # Windows frequently releases a completed GATT connection
                    # asynchronously. Four seconds prevents the next balancer
                    # from colliding with that controller cleanup.
                    await asyncio.sleep(SETTLE_SECONDS)
                if cycle_complete:self.completed_generation=max(self.completed_generation,cycle_generation)
                if self.refresh_generation<=self.completed_generation and not self.single_queue:self.refresh_event.clear()
        finally:
            await self._disconnect_all()
