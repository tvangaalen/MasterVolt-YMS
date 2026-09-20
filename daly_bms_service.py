"""DALY legacy 0xA5 BLE status and guarded MOS/SOC controls."""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from bluetooth_coordinator import bluetooth_coordinator

NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
COMMANDS = range(0x90, 0x99)
DEVICE_NAMES = ("BATTERY 1", "BATTERY 2", "BATTERY 3")
CONTROL_WATCHDOG_SECONDS = 120
SOC_DISCHARGING_POINTS = ((2500,0),(3000,10),(3200,20),(3220,30),(3250,40),(3260,50),(3270,60),(3300,70),(3320,80),(3350,90),(3400,100))
SOC_CHARGING_POINTS = ((2750,0),(3000,10),(3100,20),(3200,30),(3250,40),(3300,50),(3350,60),(3400,70),(3450,80),(3500,90),(3600,100))


def soc_from_average_mv(cell_mv: float,points=SOC_DISCHARGING_POINTS) -> float:
    """Interpolate SOC using the selected charging or discharging curve."""
    if cell_mv <= points[0][0]: return 0.0
    if cell_mv >= points[-1][0]: return 100.0
    for (low_mv,low_soc),(high_mv,high_soc) in zip(points,points[1:]):
        if cell_mv <= high_mv:
            return low_soc+(cell_mv-low_mv)*(high_soc-low_soc)/(high_mv-low_mv)
    return 100.0


def request(command: int) -> bytes:
    frame = bytearray((0xA5, 0x40, command, 0x08, 0, 0, 0, 0, 0, 0, 0, 0))
    frame.append(sum(frame) & 0xFF)
    return bytes(frame)


def write_frame(command: int,data: bytes) -> bytes:
    """Build the confirmed DALY write frame used by the supplied BMS project."""
    payload=data.ljust(8,b"\0")[:8]
    frame=bytearray((0xA5,0x80,command,0x08))+bytearray(payload)
    frame.append(sum(frame)&0xFF)
    return bytes(frame)


def u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "big")


def decode(frame: bytes) -> tuple[int, dict]:
    command, data = frame[2], frame[4:12]
    values: dict = {}
    if command == 0x90:
        values = {
            "pack_voltage_v": u16(data, 0) / 10,
            "acquisition_voltage_v": u16(data, 2) / 10,
            "current_a": (u16(data, 4) - 30000) / 10,
            "state_of_charge_percent": u16(data, 6) / 10,
        }
    elif command == 0x91:
        values = {
            "highest_cell_mv": u16(data, 0), "highest_cell_number": data[2],
            "lowest_cell_mv": u16(data, 3), "lowest_cell_number": data[5],
        }
    elif command == 0x92:
        values = {
            "highest_temperature_c": data[0] - 40,
            "lowest_temperature_c": data[2] - 40,
        }
    elif command == 0x93:
        values = {
            "mosfet_status": data[0], "charge_mosfet_on": bool(data[1]),
            "discharge_mosfet_on": bool(data[2]),
            "remaining_capacity_mah": int.from_bytes(data[4:8], "big"),
        }
    elif command == 0x94:
        values = {
            "cell_count": data[0], "temperature_sensor_count": data[1],
            "charger_connected": bool(data[2]), "load_connected": bool(data[3]),
            "charge_discharge_cycles": u16(data, 5),
        }
    elif command == 0x95:
        values = {
            "frame_number": data[0],
            "cell_voltages_mv": [u16(data, 1), u16(data, 3), u16(data, 5)],
        }
    elif command == 0x96:
        values = {
            "frame_number": data[0],
            "temperatures_c": [value - 40 for value in data[1:8]],
        }
    elif command == 0x97:
        # DALY defines payload Bit0 as Cell 1 through Bit47 as Cell 48.
        # The first received byte therefore contains cells 1-8 (little-endian
        # bit significance across the six-byte field).
        bits = int.from_bytes(data[:6], "little")
        values = {
            "balancing_cells": [n + 1 for n in range(48) if bits & (1 << n)],
            "balancing_raw_hex":data[:6].hex(" ").upper(),
        }
    elif command == 0x98:
        values = {"alarm_bytes_hex": data.hex(" ").upper()}
    return command, values


ALARM_BITS = (
    ("Cell voltage high – level 1", "Cell voltage high – level 2", "Cell voltage low – level 1", "Cell voltage low – level 2", "Pack voltage high – level 1", "Pack voltage high – level 2", "Pack voltage low – level 1", "Pack voltage low – level 2"),
    ("Charge temperature high – level 1", "Charge temperature high – level 2", "Charge temperature low – level 1", "Charge temperature low – level 2", "Discharge temperature high – level 1", "Discharge temperature high – level 2", "Discharge temperature low – level 1", "Discharge temperature low – level 2"),
    ("Charge overcurrent – level 1", "Charge overcurrent – level 2", "Discharge overcurrent – level 1", "Discharge overcurrent – level 2", "SOC high – level 1", "SOC high – level 2", "SOC low – level 1", "SOC low – level 2"),
    ("Cell voltage difference – level 1", "Cell voltage difference – level 2", "Temperature difference – level 1", "Temperature difference – level 2", None, None, None, None),
    ("Charge MOS temperature high", "Discharge MOS temperature high", "Charge MOS temperature sensor error", "Discharge MOS temperature sensor error", "Charge MOS adhesion error", "Discharge MOS adhesion error", None, None),
    ("Charge MOS open-circuit error", "Discharge MOS open-circuit error", "AFE acquisition module error", "Voltage sensor module error", "Temperature sensor module error", "EEPROM error", "RTC error", "Precharge failure"),
    ("Current module fault", "Pack-voltage detection fault", "Short-circuit protection fault", "Charging forbidden by low voltage", None, None, None, None),
)


def decode_alarm_bytes(alarm_hex: str) -> list[str]:
    """Return every active DALY 0x98 alarm as a separate readable label."""
    try:data=bytes.fromhex(alarm_hex or "")
    except ValueError:return [f"Unknown alarm value: {alarm_hex}"] if alarm_hex else []
    alarms=[]
    for byte_index,labels in enumerate(ALARM_BITS):
        value=data[byte_index] if byte_index<len(data) else 0
        for bit,label in enumerate(labels):
            if label and value&(1<<bit):alarms.append(label)
    if len(data)>7 and data[7]:alarms.append(f"Fault code {data[7]}")
    return alarms


def snapshot_from_frames(name_fragment: str,device_name: str,frames: list[bytes]) -> dict:
    by_command: dict[int,list[dict]] = {}
    for frame in frames:
        command,values=decode(frame)
        by_command.setdefault(command,[]).append(values)
    summary=(by_command.get(0x90) or [{}])[0]
    range_values=(by_command.get(0x91) or [{}])[0]
    mosfet=(by_command.get(0x93) or [{}])[0]
    status=(by_command.get(0x94) or [{}])[0]
    if not summary or not status:
        raise RuntimeError(f"{name_fragment} returned incomplete DALY status data")
    cells=[]
    for group in by_command.get(0x95,[]):cells.extend(group.get("cell_voltages_mv",[]))
    temperatures=[]
    for group in by_command.get(0x96,[]):temperatures.extend(group.get("temperatures_c",[]))
    cells=cells[:status.get("cell_count",0)]
    temperatures=temperatures[:status.get("temperature_sensor_count",0)]
    alarm_hex=(by_command.get(0x98) or [{}])[0].get("alarm_bytes_hex","")
    balance_data=(by_command.get(0x97) or [{}])[0]
    balancing=[cell for cell in balance_data.get("balancing_cells",[]) if cell<=status.get("cell_count",0)]
    spread_mv=None
    if cells:spread_mv=max(cells)-min(cells)
    elif range_values.get("highest_cell_mv") is not None:
        spread_mv=range_values["highest_cell_mv"]-range_values.get("lowest_cell_mv",0)
    return {
        "state":"connected","battery":name_fragment,
        "device_name":device_name or name_fragment,
        "captured_at":datetime.now(timezone.utc).isoformat(),
        "pack_voltage_v":summary.get("pack_voltage_v"),
        "current_a":summary.get("current_a"),
        "state_of_charge_percent":summary.get("state_of_charge_percent"),
        "remaining_capacity_ah":None if mosfet.get("remaining_capacity_mah") is None else mosfet["remaining_capacity_mah"]/1000,
        "temperatures_c":temperatures,
        "cells_mv":cells,"cell_spread_mv":spread_mv,
        "charge_mosfet_on":mosfet.get("charge_mosfet_on"),
        "discharge_mosfet_on":mosfet.get("discharge_mosfet_on"),
        "balancing_cells":balancing,
        "balancing_raw_hex":balance_data.get("balancing_raw_hex"),
        "alarms":decode_alarm_bytes(alarm_hex),
        "cycles":status.get("charge_discharge_cycles"),
        "valid_frame_count":len(frames),
    }


class _LegacyDalyBmsService:
    def __init__(self):
        self.lock=threading.RLock()
        self.busy=False
        self.refresh_started_at=None
        self.batteries={name:{"state":"not_read","battery":name} for name in DEVICE_NAMES}
        self._thread=None
        self._stop_requested=threading.Event()
        self._refresh_requested=threading.Event()
        self._settings_getter=lambda:{"bms_refresh_interval":10,"bms_connection_retry_seconds":5}
        self._clients={}
        self._streams={}
        self._frames={}
        self._device_names={}
        self._known_devices={}
        self._connection_failures={name:0 for name in DEVICE_NAMES}
        self._loop=None
        self._operation_lock=None
        self.backup_dir=Path(__file__).resolve().parent/"backups"

    def start(self,settings_getter):
        with self.lock:
            if self._thread and self._thread.is_alive():return
            self._settings_getter=settings_getter
            self._stop_requested.clear()
            self._thread=threading.Thread(target=self._thread_main,daemon=True,name="daly-bms-persistent")
            self._thread.start()

    def stop(self):
        self._stop_requested.set();self._refresh_requested.set()
        thread=self._thread
        if thread and thread.is_alive():thread.join(timeout=12)

    def _thread_main(self):
        try:asyncio.run(self._run())
        except Exception as exc:
            with self.lock:
                for name in DEVICE_NAMES:
                    previous=self.batteries.get(name,{})
                    self.batteries[name]={**previous,"battery":name,"state":"error","error":str(exc)}
                self.busy=False

    def snapshot(self):
        with self.lock:
            clients=list(self._clients.values())
            return {
                "busy":self.busy,"refresh_started_at":self.refresh_started_at,
                "batteries":{name:dict(value) for name,value in self.batteries.items()},
                "read_only":False,"controls_available":True,
                "ble_available":importlib.util.find_spec("bleak") is not None,
                "persistent_connections":True,
                "connected_count":sum(1 for client in clients if client.is_connected),
                "refresh_interval_seconds":int(self._settings_getter()["bms_refresh_interval"]),
                "connection_retry_interval_seconds":int(self._settings_getter()["bms_connection_retry_seconds"]),
            }

    def refresh_all(self):
        self._refresh_requested.set()
        return {"started":True,"parallel":True}

    def control(self,name:str,action:str,enabled:bool|None=None):
        if name not in DEVICE_NAMES:raise ValueError("Unknown battery")
        if action not in {"set_soc_100","set_soc_accurate","charge","discharge"}:raise ValueError("Unknown BMS action")
        with self.lock:
            client=self._clients.get(name);latest=dict(self.batteries.get(name,{}))
            if not client or not client.is_connected or latest.get("state")!="connected":
                raise RuntimeError(f"{name} must be connected and refreshed before changing it")
            if action=="set_soc_accurate" and not latest.get("cells_mv"):
                raise RuntimeError(f"{name} has no cell-voltage data")
            if self.busy:raise RuntimeError("A BMS update is already in progress")
            self.backup_dir.mkdir(exist_ok=True)
            stamp=datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path=self.backup_dir/f'{name.lower().replace(" ","_")}_backup_{stamp}.json'
            path.write_text(json.dumps(latest,indent=2)+"\n",encoding="utf-8")
            self.busy=True
        if not self._loop or not self._loop.is_running():
            with self.lock:self.busy=False
            raise RuntimeError("BMS background connection is not running")
        future=asyncio.run_coroutine_threadsafe(self._perform_control(name,action,enabled),self._loop)
        return future.result(timeout=20)

    def control_all(self,action:str):
        if action not in {"set_soc_100","set_soc_accurate","charge_on","charge_off","discharge_on","discharge_off"}:raise ValueError("Unknown BMS action")
        with self.lock:
            for name in DEVICE_NAMES:
                client=self._clients.get(name);latest=self.batteries.get(name,{})
                if not client or not client.is_connected or latest.get("state")!="connected":
                    raise RuntimeError("All three batteries must be connected before changing all SOC values")
                if action=="set_soc_accurate" and not latest.get("cells_mv"):
                    raise RuntimeError(f"{name} has no cell-voltage data")
            if self.busy:raise RuntimeError("A BMS update is already in progress")
            self.backup_dir.mkdir(exist_ok=True)
            stamp=datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            for name in DEVICE_NAMES:
                path=self.backup_dir/f'{name.lower().replace(" ","_")}_backup_{stamp}.json'
                path.write_text(json.dumps(self.batteries[name],indent=2)+"\n",encoding="utf-8")
            self.busy=True
        future=asyncio.run_coroutine_threadsafe(self._perform_control_all(action),self._loop)
        return future.result(timeout=40)

    async def _run(self):
        if importlib.util.find_spec("bleak") is None:
            raise RuntimeError("Windows Bluetooth component 'bleak' is unavailable")
        self._loop=asyncio.get_running_loop();self._operation_lock=asyncio.Lock()
        while not self._stop_requested.is_set():
            self._refresh_requested.clear()
            try:
                # Always refresh healthy sessions before attempting a missing
                # connection. A troublesome BMS must not starve good devices.
                await self._refresh_connected()
                connected_new=await self._connect_missing()
                if connected_new:await self._refresh_connected()
            except Exception as exc:
                with self.lock:
                    for name in DEVICE_NAMES:
                        if name not in self._clients:
                            previous=self.batteries.get(name,{})
                            self.batteries[name]={**previous,"battery":name,"state":"error","error":str(exc)}
            settings=self._settings_getter()
            missing=any(name not in self._clients or not self._clients[name].is_connected for name in DEVICE_NAMES)
            interval=int(settings["bms_connection_retry_seconds"] if missing else settings["bms_refresh_interval"])
            interval=max(1,min(300,interval))
            for _ in range(interval*2):
                if self._stop_requested.is_set() or self._refresh_requested.is_set():break
                await asyncio.sleep(.5)
        await self._disconnect_all()
        self._loop=None

    async def _perform_control(self,name,action,enabled):
        latest=self.batteries.get(name,{})
        cells=latest.get("cells_mv") or []
        accurate_soc=soc_from_average_mv(sum(cells)/len(cells)) if cells else None
        commands={
            "set_soc_100":(0x21,b"\0"*6+(1000).to_bytes(2,"big")),
            "set_soc_accurate":(0x21,b"\0"*6+int(round((accurate_soc or 0)*10)).to_bytes(2,"big")),
            "charge":(0xDA,b"\x01" if enabled else b"\x00"),
            "discharge":(0xD9,b"\x01" if enabled else b"\x00"),
        }
        if action=="set_soc_accurate" and accurate_soc is None:raise RuntimeError(f"{name} has no cell-voltage data")
        command,payload=commands[action]
        try:
            async with self._operation_lock:
                client=self._clients.get(name)
                if not client or not client.is_connected:raise RuntimeError(f"{name} Bluetooth connection was lost")
                await client.write_gatt_char(WRITE_UUID,write_frame(command,payload),response=False)
                await asyncio.sleep(1)
            await self._refresh_connected()
            return {"changed":True,"battery":name,"action":action,"enabled":enabled,"refresh_complete":True}
        except Exception:
            with self.lock:self.busy=False
            raise

    async def _perform_control_all(self,action):
        try:
            async with self._operation_lock:
                results={}
                for name in DEVICE_NAMES:
                    client=self._clients[name];cells=self.batteries[name].get("cells_mv") or []
                    if action=="set_soc_accurate" and not cells:raise RuntimeError(f"{name} has no cell-voltage data")
                    if action in {"set_soc_100","set_soc_accurate"}:
                        soc=100.0 if action=="set_soc_100" else soc_from_average_mv(sum(cells)/len(cells))
                        command=0x21;payload=b"\0"*6+int(round(soc*10)).to_bytes(2,"big");results[name]=round(soc,1)
                    else:
                        charge_action=action.startswith("charge_")
                        enabled=action.endswith("_on")
                        command=0xDA if charge_action else 0xD9;payload=b"\x01" if enabled else b"\x00";results[name]=enabled
                    await client.write_gatt_char(WRITE_UUID,write_frame(command,payload),response=False)
                    await asyncio.sleep(.5)
                await asyncio.sleep(1)
            await self._refresh_connected()
            return {"changed":True,"action":action,"soc_percent":results,"refresh_complete":True}
        except Exception:
            with self.lock:self.busy=False
            raise

    def _set_connection_state(self,name,state,error=None,**values):
        """Update connection state without discarding the last good reading."""
        with self.lock:
            previous=self.batteries.get(name,{})
            self.batteries[name]={**previous,**values,"battery":name,"state":state,"error":error}

    async def _disconnect_quietly(self,client):
        """Disconnect without ever blocking the connection queue."""
        try:
            task=asyncio.create_task(client.disconnect())
            done,_pending=await asyncio.wait({task},timeout=3)
            if task not in done:task.cancel()
            elif not task.cancelled():task.exception()
        except BaseException:
            pass

    async def _connect_with_deadline(self,client,name,timeout_seconds=12):
        """Bound a WinRT connect even when Bleak cancellation itself stalls."""
        task=asyncio.create_task(client.connect())
        done,_pending=await asyncio.wait({task},timeout=timeout_seconds)
        if task in done:
            return task.result()

        # Do not await cancellation: some Windows WinRT calls do not acknowledge
        # it promptly. Advancing the queue is more important than waiting for a
        # wedged handshake. If it completes late, immediately close that client.
        task.cancel()
        def close_late(completed):
            try:
                if not completed.cancelled():completed.exception()
            except BaseException:
                pass
            loop=self._loop
            if getattr(client,"is_connected",False) and loop and not loop.is_closed():
                loop.create_task(self._disconnect_quietly(client))
        task.add_done_callback(close_late)
        raise TimeoutError(f"{name} connection timed out after {timeout_seconds} seconds; retrying automatically")

    async def _discover_missing(self,missing,scanner):
        """Resolve all missing names in one scan, reusing healthy cached devices."""
        scan_names=[name for name in missing if name not in self._known_devices or self._connection_failures.get(name,0)>=3]
        matches={name:self._known_devices[name] for name in missing if name in self._known_devices and name not in scan_names}
        if not scan_names:return matches
        discovered=await scanner.discover(timeout=12,return_adv=True)
        records=list(discovered.values()) if isinstance(discovered,dict) else [(device,None) for device in discovered]
        for name in scan_names:
            for device,advertisement in records:
                visible_name=(getattr(advertisement,"local_name",None) or getattr(device,"name",None) or "")
                if name.casefold() in visible_name.casefold():
                    matches[name]=(device,visible_name)
                    self._known_devices[name]=(device,visible_name)
                    self._connection_failures[name]=0
                    break
        return matches

    async def _connect_one(self,name,match,client_class):
        """Open one persistent GATT session; never block the next battery forever."""
        if not match:
            self._connection_failures[name]=self._connection_failures.get(name,0)+1
            self._set_connection_state(name,"error",f"{name} was not found. Wake it and close the DALY phone app.")
            return False
        device,visible_name=match
        self._set_connection_state(name,"connecting",None,device_name=visible_name)

        def disconnected(_client):
            with self.lock:
                if self._clients.get(name) is _client:self._clients.pop(name,None)
            self._set_connection_state(name,"disconnected","Bluetooth connection lost; reconnecting automatically")
            self._refresh_requested.set()

        client=client_class(device,timeout=12,disconnected_callback=disconnected)
        try:
            await self._connect_with_deadline(client,name,12)
            notify_task=asyncio.create_task(client.start_notify(NOTIFY_UUID,self._notification_handler(name)))
            done,_pending=await asyncio.wait({notify_task},timeout=6)
            if notify_task not in done:
                notify_task.cancel()
                raise TimeoutError(f"{name} notification setup timed out; retrying automatically")
            notify_task.result()
        except Exception as exc:
            self._connection_failures[name]=self._connection_failures.get(name,0)+1
            await self._disconnect_quietly(client)
            self._set_connection_state(name,"error",str(exc))
            return False

        self._streams[name]=bytearray();self._frames[name]=[];self._device_names[name]=visible_name
        with self.lock:self._clients[name]=client
        self._connection_failures[name]=0
        self._set_connection_state(name,"connected",None)
        return True

    async def _connect_missing(self):
        """Connect missing batteries strictly 1, 2, 3 with a short settling pause."""
        missing=[name for name in DEVICE_NAMES if name not in self._clients or not self._clients[name].is_connected]
        if not missing:return False
        from bleak import BleakClient,BleakScanner
        for name in missing:self._set_connection_state(name,"scanning",None)
        matches=await self._discover_missing(missing,BleakScanner)
        connected_new=False
        for index,name in enumerate(missing):
            connected_new=await self._connect_one(name,matches.get(name),BleakClient) or connected_new
            if index<len(missing)-1:await asyncio.sleep(1.0)
        return connected_new

    def _notification_handler(self,name):
        def notified(_sender,payload):
            stream=self._streams.setdefault(name,bytearray());stream.extend(payload)
            while len(stream)>=13:
                try:start=stream.index(0xA5)
                except ValueError:stream.clear();return
                if start:del stream[:start]
                if len(stream)<13:return
                frame=bytes(stream[:13])
                if frame[3]==8 and (sum(frame[:12])&0xFF)==frame[12]:
                    self._frames.setdefault(name,[]).append(frame);del stream[:13]
                else:del stream[0]
        return notified

    async def _refresh_connected(self):
        async with self._operation_lock:
            await self._refresh_connected_unlocked()

    async def _refresh_connected_unlocked(self):
        connected=[name for name,client in self._clients.items() if client.is_connected]
        if not connected:return
        with self.lock:
            self.busy=True;self.refresh_started_at=datetime.now(timezone.utc).isoformat()
            for name in connected:
                previous=self.batteries.get(name,{})
                self.batteries[name]={**previous,"battery":name,"state":"reading","error":None}
        async def refresh_one(name):
            client=self._clients[name];self._frames[name]=[]
            try:
                for command in COMMANDS:
                    await client.write_gatt_char(WRITE_UUID,request(command),response=False)
                    await asyncio.sleep(.25)
                await asyncio.sleep(1)
                value=snapshot_from_frames(name,self._device_names.get(name,name),list(self._frames[name]))
            except Exception as exc:
                with self.lock:
                    previous=self.batteries.get(name,{})
                    self.batteries[name]={**previous,"battery":name,"state":"error","error":str(exc)}
                try:await client.disconnect()
                except Exception:pass
            else:
                with self.lock:self.batteries[name]=value
        try:await asyncio.gather(*(refresh_one(name) for name in connected))
        finally:
            with self.lock:self.busy=False

    async def _disconnect_all(self):
        with self.lock:
            clients=list(self._clients.values());self._clients={}
        await asyncio.gather(*(client.disconnect() for client in clients),return_exceptions=True)


class _BatteryWorker:
    """One isolated Windows/Bleak event loop for one DALY battery."""
    def __init__(self,service,name,index):
        self.service=service;self.name=name;self.index=index
        self.thread=None;self.loop=None;self.client=None;self.device=None;self.device_name=name
        self.stop_event=threading.Event();self.refresh_event=threading.Event()
        self.stream=bytearray();self.frames=[];self.last_refresh=0.0
        self.completed_generation=0;self.connection_failures=0;self.operation_lock=None

    def start(self):
        self.stop_event.clear()
        self.thread=threading.Thread(target=self._thread_main,daemon=True,name=f"daly-{self.name.lower().replace(' ','-')}")
        self.thread.start()

    def stop(self):
        self.stop_event.set();self.refresh_event.set()
        if self.loop and self.loop.is_running():self.loop.call_soon_threadsafe(lambda:None)
        if self.thread and self.thread.is_alive():self.thread.join(timeout=4)

    def _thread_main(self):
        try:asyncio.run(self._run())
        except BaseException as exc:self.service._set_state(self.name,"error",f"Worker stopped: {exc}")

    def _notified(self,_sender,payload):
        self.stream.extend(payload)
        while len(self.stream)>=13:
            try:start=self.stream.index(0xA5)
            except ValueError:self.stream.clear();return
            if start:del self.stream[:start]
            if len(self.stream)<13:return
            frame=bytes(self.stream[:13])
            if frame[3]==8 and (sum(frame[:12])&0xFF)==frame[12]:self.frames.append(frame);del self.stream[:13]
            else:del self.stream[0]

    async def _disconnect(self):
        client=self.client;self.client=None
        bluetooth_coordinator.mark_bms(self.name,False)
        if client:
            try:await asyncio.wait_for(client.disconnect(),timeout=3)
            except BaseException:pass

    async def _connect(self,queue_kind="bms"):
        from bleak import BleakClient,BleakScanner
        self.service._set_state(self.name,"scanning",None)
        # Normally only one worker scans/connects. If Windows wedges that worker,
        # the lease expires so it cannot starve the remaining batteries.
        # Coordinator.acquire is deliberately blocking. Run it outside this
        # worker's event loop so completed BLE coroutines can always publish
        # their result back to the calling HTTP thread.
        owns_slot=await asyncio.to_thread(
            bluetooth_coordinator.acquire,
            queue_kind,
            35 if queue_kind=="bms_manual_connect" else 25,
        )
        if not owns_slot:
            self.service._set_state(self.name,"waiting","Waiting for Bluetooth connection slot")
            return False
        try:
            self.service._wait_connection_pause()
            if self.device is None or self.connection_failures>=3:
                found=await BleakScanner.find_device_by_filter(
                    lambda device,advertisement:self.name.casefold() in (
                        getattr(advertisement,"local_name",None) or getattr(device,"name",None) or ""
                    ).casefold(),timeout=10
                )
                if found is None:raise RuntimeError(f"{self.name} was not found. Wake it and close the DALY phone app.")
                self.device=found;self.device_name=getattr(found,"name",None) or self.name;self.connection_failures=0
            self.service._set_state(self.name,"connecting",None,device_name=self.device_name)
            def disconnected(client):
                if self.client is client:self.client=None
                bluetooth_coordinator.mark_bms(self.name,False)
                self.service._set_state(self.name,"disconnected","Bluetooth connection lost; reconnecting automatically")
                self.refresh_event.set()
            client=BleakClient(self.device,timeout=12,disconnected_callback=disconnected)
            await client.connect()
            await client.start_notify(NOTIFY_UUID,self._notified)
            self.client=client;self.connection_failures=0
            bluetooth_coordinator.mark_bms(self.name,True)
            self.service._set_state(self.name,"connected",None,device_name=self.device_name)
            return True
        except BaseException as exc:
            self.connection_failures+=1
            await self._disconnect()
            self.service._set_state(self.name,"error",str(exc) or f"{self.name} connection failed")
            return False
        finally:
            self.service._record_connection_attempt()
            if owns_slot:bluetooth_coordinator.release(queue_kind)

    async def _refresh(self,generation=0,manual=False):
        async with self.operation_lock:
            return await self._refresh_unlocked(generation,manual=manual)

    async def _refresh_unlocked(self,generation=0,radio_owned=False,raise_on_error=False,manual=False):
        if not self.client or not self.client.is_connected:
            self.completed_generation=max(self.completed_generation,generation);return False
        queue_kind="bms_manual_read" if manual else "bms_read"
        owns_slot=radio_owned or await asyncio.to_thread(
            bluetooth_coordinator.acquire,
            queue_kind,
            35 if manual else 2,
        )
        if not owns_slot:
            self.refresh_event.set()
            return False
        try:
            self.service._set_state(self.name,"reading",None)
            self.frames=[];self.stream=bytearray()
            for index,command in enumerate(COMMANDS,1):
                if radio_owned:self.service._control_progress(f"verifying values ({index}/9)")
                attempts=2 if radio_owned or manual else 1
                for attempt in range(1,attempts+1):
                    try:
                        await asyncio.wait_for(self.client.write_gatt_char(WRITE_UUID,request(command),response=False),timeout=4)
                        break
                    except asyncio.TimeoutError:
                        if attempt>=attempts:raise
                        if radio_owned:self.service._control_progress(f"verification {index}/9 timed out; retrying")
                        await asyncio.sleep(.3)
                await asyncio.sleep(.25)
            await asyncio.sleep(1)
            value=snapshot_from_frames(self.name,self.device_name,list(self.frames))
            self.service._set_snapshot(self.name,value)
            bluetooth_coordinator.mark_bms_refreshed(self.name)
            self.last_refresh=time.monotonic();return True
        except BaseException as exc:
            message=f"{self.name} verification read failed: {str(exc).strip() or type(exc).__name__}"
            self.service._set_state(self.name,"error",message)
            await self._disconnect()
            if raise_on_error:raise RuntimeError(message) from exc
            return False
        finally:
            if not radio_owned:bluetooth_coordinator.release(queue_kind)
            self.completed_generation=max(self.completed_generation,generation)

    async def control(self,action,enabled):
        async with self.operation_lock:
            latest=self.service._battery(self.name);cells=latest.get("cells_mv") or []
            average=sum(cells)/len(cells) if cells else None
            soc_charge=soc_from_average_mv(average,SOC_CHARGING_POINTS) if average is not None else None
            soc_discharge=soc_from_average_mv(average,SOC_DISCHARGING_POINTS) if average is not None else None
            commands={
                "set_soc_100":(0x21,b"\0"*6+(1000).to_bytes(2,"big")),
                "set_soc_charge":(0x21,b"\0"*6+int(round((soc_charge or 0)*10)).to_bytes(2,"big")),
                "set_soc_discharge":(0x21,b"\0"*6+int(round((soc_discharge or 0)*10)).to_bytes(2,"big")),
                "charge":(0xDA,b"\x01" if enabled else b"\x00"),
                "discharge":(0xD9,b"\x01" if enabled else b"\x00"),
            }
            if not self.client or not self.client.is_connected:raise RuntimeError(f"{self.name} Bluetooth connection was lost")
            if action in {"set_soc_charge","set_soc_discharge"} and average is None:raise RuntimeError(f"{self.name} has no cell-voltage data")
            command,payload=commands[action]
            if not self.client or not self.client.is_connected:raise RuntimeError(f"{self.name} Bluetooth connection was lost")

            # MOS commands need state verification, not merely a successful BLE
            # write. Some DALY units accept a frame without applying it and this
            # hardware has also shown the other MOS changing as a side effect.
            # Preserve the unrelated MOS and explicitly restore it if necessary.
            if action in {"charge","discharge"}:
                target_field=f"{action}_mosfet_on"
                other_action="discharge" if action=="charge" else "charge"
                other_field=f"{other_action}_mosfet_on"
                desired=bool(enabled)
                original_other=latest.get(other_field)
                if original_other not in (True,False):
                    raise RuntimeError(f"{self.name} has no verified {other_action} MOS state")
                last_state={}
                learned_encoding=self.service._mos_encoding(self.name,action)
                for operation_attempt in (1,2):
                    # DALY firmware families disagree on whether payload 1 means
                    # ON or OFF. Use the learned per-device mapping; while it is
                    # unknown, probe both encodings and keep only a verified one.
                    preferred_encoding=True if learned_encoding is None else learned_encoding
                    one_means_on=preferred_encoding if operation_attempt==1 else not preferred_encoding
                    wire_value=desired if one_means_on else not desired
                    wire_payload=b"\x01" if wire_value else b"\x00"
                    self.service._control_progress(f"setting {action} {'ON' if desired else 'OFF'} (verification {operation_attempt}/2)")
                    try:
                        await asyncio.wait_for(self.client.write_gatt_char(WRITE_UUID,write_frame(command,wire_payload),response=False),timeout=8)
                    except asyncio.TimeoutError:
                        if operation_attempt==2:raise RuntimeError(f"{self.name} did not accept the {action} setting: Bluetooth write timed out")
                        continue
                    self.service._control_progress(f"verifying {action} MOS state")
                    await asyncio.sleep(1)
                    await self._refresh_unlocked(radio_owned=True,raise_on_error=True)
                    last_state=self.service._battery(self.name)

                    if last_state.get(target_field)==desired and last_state.get(other_field)!=original_other:
                        self.service._control_progress(f"restoring unchanged {other_action} MOS state")
                        restore_command=commands[other_action][0]
                        restore_encoding=self.service._mos_encoding(self.name,other_action)
                        for restore_attempt in (1,2):
                            restore_preferred=True if restore_encoding is None else restore_encoding
                            restore_one_means_on=restore_preferred if restore_attempt==1 else not restore_preferred
                            restore_wire=original_other if restore_one_means_on else not original_other
                            restore_payload=b"\x01" if restore_wire else b"\x00"
                            await asyncio.wait_for(self.client.write_gatt_char(WRITE_UUID,write_frame(restore_command,restore_payload),response=False),timeout=8)
                            await asyncio.sleep(1)
                            await self._refresh_unlocked(radio_owned=True,raise_on_error=True)
                            last_state=self.service._battery(self.name)
                            if last_state.get(other_field)==original_other:
                                self.service._remember_mos_encoding(self.name,other_action,restore_one_means_on)
                                break

                    target_ok=last_state.get(target_field)==desired
                    other_ok=last_state.get(other_field)==original_other
                    if target_ok and other_ok:
                        self.service._remember_mos_encoding(self.name,action,one_means_on)
                        self.service._control_progress("verification complete")
                        await asyncio.sleep(0)
                        return {"changed":True,"battery":self.name,"action":action,"enabled":desired,"verified":True}
                    if operation_attempt==1:
                        self.service._control_progress("requested MOS state not confirmed; retrying")

                reported_target=last_state.get(target_field)
                reported_other=last_state.get(other_field)
                raise RuntimeError(
                    f"{self.name} did not confirm {action} {'ON' if desired else 'OFF'} after 2 attempts "
                    f"(reported charge={'ON' if last_state.get('charge_mosfet_on') else 'OFF'}, "
                    f"discharge={'ON' if last_state.get('discharge_mosfet_on') else 'OFF'}; "
                    f"target={reported_target}, preserved={reported_other})"
                )

            for attempt in (1,2):
                self.service._control_progress(f"sending setting (attempt {attempt}/2)")
                try:
                    await asyncio.wait_for(self.client.write_gatt_char(WRITE_UUID,write_frame(command,payload),response=False),timeout=8)
                    break
                except asyncio.TimeoutError as exc:
                    if attempt==2:raise RuntimeError(f"{self.name} did not accept the setting after 2 attempts: Bluetooth write timed out") from exc
                    await asyncio.sleep(.5)
            self.service._control_progress("waiting for BMS confirmation")
            await asyncio.sleep(1)
            await self._refresh_unlocked(radio_owned=True,raise_on_error=True)
            self.service._control_progress("verification complete")
            # Explicitly yield once so run_coroutine_threadsafe can deliver the
            # successful result before any background work is considered.
            await asyncio.sleep(0)
            selected={"set_soc_charge":soc_charge,"set_soc_discharge":soc_discharge}.get(action)
            return {"changed":True,"battery":self.name,"action":action,"enabled":enabled,"soc_percent":selected}

    async def _run(self):
        self.loop=asyncio.get_running_loop();self.operation_lock=asyncio.Lock();await asyncio.sleep(self.index)
        while not self.stop_event.is_set():
            # A user control owns the shared Bluetooth radio until its result
            # has reached the HTTP thread. Starting a background refresh here
            # used to create a circular wait immediately after verification.
            if self.service._control_busy():
                await asyncio.sleep(.1)
                continue
            requested=self.refresh_event.is_set();self.refresh_event.clear()
            generation=self.service._refresh_generation
            manual=generation>self.completed_generation
            if not self.client or not self.client.is_connected:
                await self._connect("bms_manual_connect" if manual else "bms")
                if self.client and self.client.is_connected:await self._refresh(generation,manual=manual)
                else:self.completed_generation=max(self.completed_generation,generation)
            else:
                interval=max(2,int(self.service._settings_getter()["bms_refresh_interval"]))
                if requested or time.monotonic()-self.last_refresh>=interval:await self._refresh(generation,manual=manual)
            retry=max(1,int(self.service._settings_getter()["bms_connection_retry_seconds"]))
            wait=.25 if self.client and self.client.is_connected else retry
            for _ in range(max(1,int(wait*4))):
                if self.stop_event.is_set() or self.refresh_event.is_set():break
                await asyncio.sleep(.25)
        await self._disconnect();self.loop=None


class DalyBmsService:
    """Coordinator for three failure-isolated persistent DALY workers."""
    def __init__(self):
        self.lock=threading.RLock();self.batteries={name:{"state":"not_read","battery":name} for name in DEVICE_NAMES}
        self._settings_getter=lambda:{"bms_refresh_interval":30,"bms_connection_retry_seconds":5}
        self._workers={};self._connection_slot=threading.Lock();self._connection_timing_lock=threading.Lock()
        self._last_connection_attempt=0.0;self._refresh_generation=0;self._manual_deadline=0.0
        self.control_status={"busy":False,"phase":"idle","message":"","current":0,"total":0}
        self.backup_dir=Path(__file__).resolve().parent/"backups"
        self._mos_encoding_path=Path(__file__).resolve().parent/"data"/"bms_mos_encoding.json"
        try:self._mos_encodings=json.loads(self._mos_encoding_path.read_text(encoding="utf-8"))
        except (OSError,ValueError,TypeError):self._mos_encodings={}

    def _set_state(self,name,state,error=None,**values):
        with self.lock:self.batteries[name]={**self.batteries.get(name,{}),**values,"battery":name,"state":state,"error":error}

    def _set_snapshot(self,name,value):
        with self.lock:self.batteries[name]=dict(value)

    def _battery(self,name):
        with self.lock:return dict(self.batteries.get(name,{}))

    def _mos_encoding(self,name,action):
        with self.lock:
            value=self._mos_encodings.get(name,{}).get(action)
            return value if value in (True,False) else None

    def _remember_mos_encoding(self,name,action,one_means_on):
        with self.lock:
            self._mos_encodings.setdefault(name,{})[action]=bool(one_means_on)
            value=json.dumps(self._mos_encodings,indent=2,sort_keys=True)+"\n"
        self._mos_encoding_path.parent.mkdir(exist_ok=True)
        temporary=self._mos_encoding_path.with_suffix(".tmp")
        temporary.write_text(value,encoding="utf-8")
        temporary.replace(self._mos_encoding_path)

    def _wait_connection_pause(self):
        with self._connection_timing_lock:delay=max(0.0,1.0-(time.monotonic()-self._last_connection_attempt))
        if delay:time.sleep(delay)

    def _record_connection_attempt(self):
        with self._connection_timing_lock:self._last_connection_attempt=time.monotonic()

    def start(self,settings_getter):
        if importlib.util.find_spec("bleak") is None:
            for name in DEVICE_NAMES:self._set_state(name,"error","Windows Bluetooth component 'bleak' is unavailable")
            return
        self._settings_getter=settings_getter
        with self.lock:
            if self._workers:return
            self._workers={name:_BatteryWorker(self,name,index) for index,name in enumerate(DEVICE_NAMES)}
            workers=list(self._workers.values())
        for worker in workers:worker.start()

    def stop(self):
        with self.lock:workers=list(self._workers.values());self._workers={}
        for worker in workers:worker.stop()

    def snapshot(self):
        with self.lock:
            workers=dict(self._workers);batteries={name:dict(value) for name,value in self.batteries.items()};control_status=dict(self.control_status)
        pending=bool(workers) and any(w.completed_generation<self._refresh_generation for w in workers.values())
        busy=pending
        pending_names=[name for name,worker in workers.items() if worker.completed_generation<self._refresh_generation]
        refresh_status={"generation":self._refresh_generation,"completed":len(workers)-len(pending_names),"total":len(workers),"pending":pending_names}
        return {"busy":busy,"refresh_started_at":None,"refresh_status":refresh_status,"control_status":control_status,"queue":bluetooth_coordinator.snapshot(),"batteries":batteries,"read_only":False,"controls_available":True,
            "ble_available":importlib.util.find_spec("bleak") is not None,"persistent_connections":True,
            "connected_count":sum(1 for w in workers.values() if w.client and w.client.is_connected),
            "refresh_interval_seconds":int(self._settings_getter()["bms_refresh_interval"]),
            "connection_retry_interval_seconds":int(self._settings_getter()["bms_connection_retry_seconds"])}

    def refresh_all(self):
        self._refresh_generation+=1;self._manual_deadline=time.monotonic()+60
        for worker in list(self._workers.values()):worker.refresh_event.set()
        return {"started":True,"parallel":True}

    def refresh_one(self,name):
        if name not in DEVICE_NAMES:raise ValueError("Unknown battery")
        worker=self._workers.get(name)
        if not worker:raise RuntimeError(f"{name} worker is unavailable")
        worker.refresh_event.set()
        return {"started":True,"battery":name}

    def _backup(self,names):
        self.backup_dir.mkdir(exist_ok=True);stamp=datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        for name in names:
            path=self.backup_dir/f'{name.lower().replace(" ","_")}_backup_{stamp}.json'
            path.write_text(json.dumps(self._battery(name),indent=2)+"\n",encoding="utf-8")

    def _set_control_status(self,**updates):
        with self.lock:self.control_status={**self.control_status,**updates}

    def _control_progress(self,detail):
        with self.lock:
            current=int(self.control_status.get("current") or 1);total=int(self.control_status.get("total") or 1)
            label=f"Battery {current}/{total}" if total>1 else next((name.title() for name in DEVICE_NAMES if name.lower() in str(self.control_status.get("message","")).lower()),"Battery")
            self.control_status={**self.control_status,"phase":"updating","message":f"{label}: {detail}"}

    def _control_busy(self):
        with self.lock:return bool(self.control_status.get("busy"))

    def _run_control(self,name,action,enabled=None):
        worker=self._workers.get(name);latest=self._battery(name)
        if not worker or not worker.loop or not worker.client or not worker.client.is_connected or latest.get("state") not in {"connected","reading"}:
            raise RuntimeError(f"{name} must be connected and refreshed before changing it")
        future=asyncio.run_coroutine_threadsafe(worker.control(action,enabled),worker.loop)
        # Every BLE write/read already has its own short timeout and retry.
        # This outer watchdog is only a deadlock guard and must accommodate
        # all nine verification requests including their retry allowance.
        try:return future.result(timeout=CONTROL_WATCHDOG_SECONDS)
        except concurrent.futures.TimeoutError as exc:
            with self.lock:last_phase=self.control_status.get("message") or "unknown phase"
            future.cancel()
            try:future.result(timeout=3)
            except BaseException:pass
            disconnect=asyncio.run_coroutine_threadsafe(worker._disconnect(),worker.loop)
            try:disconnect.result(timeout=5)
            except BaseException:pass
            raise RuntimeError(f"{name} control watchdog timed out after {CONTROL_WATCHDOG_SECONDS} seconds during: {last_phase}; the Bluetooth operation was cancelled") from exc

    def control(self,name,action,enabled=None):
        if name not in DEVICE_NAMES:raise ValueError("Unknown battery")
        if action not in {"set_soc_100","set_soc_charge","set_soc_discharge","charge","discharge"}:raise ValueError("Unknown BMS action")
        self._backup([name])
        self._set_control_status(busy=True,phase="waiting",message="Waiting for Bluetooth queue to become available",current=0,total=1)
        owns_radio=False
        try:
            owns_radio=bluetooth_coordinator.acquire("bms_control",timeout=35)
            if not owns_radio:raise RuntimeError("Bluetooth queue did not become available in time")
            self._set_control_status(phase="updating",message=f"Updating {name} (1/1)",current=1)
            return self._run_control(name,action,enabled)
        finally:
            if owns_radio:bluetooth_coordinator.release("bms_control")
            self._set_control_status(busy=False,phase="idle",message="",current=0,total=0)

    def control_all(self,action):
        if action not in {"set_soc_100","set_soc_charge","set_soc_discharge","charge_on","charge_off","discharge_on","discharge_off"}:raise ValueError("Unknown BMS action")
        targets=list(DEVICE_NAMES)
        if action.startswith("charge_"):
            desired=action.endswith("_on");targets=[name for name in DEVICE_NAMES if self._battery(name).get("charge_mosfet_on") is not desired]
        elif action.startswith("discharge_"):
            desired=action.endswith("_on");targets=[name for name in DEVICE_NAMES if self._battery(name).get("discharge_mosfet_on") is not desired]
        if not targets:return {"changed":False,"action":action,"results":{},"refresh_complete":True,"targets":[]}
        for name in targets:
            worker=self._workers.get(name)
            if not worker or not worker.client or not worker.client.is_connected or self._battery(name).get("state") not in {"connected","reading"}:raise RuntimeError("All three batteries must be connected before changing all values")
        self._backup(targets);results={};owns_radio=False;total=len(targets)
        self._set_control_status(busy=True,phase="waiting",message="Waiting for Bluetooth queue to become available",current=0,total=total)
        try:
            owns_radio=bluetooth_coordinator.acquire("bms_control",timeout=35)
            if not owns_radio:raise RuntimeError("Bluetooth queue did not become available in time")
            completed=[]
            for index,name in enumerate(targets,1):
                try:
                    self._set_control_status(phase="updating",message=f"Updating Battery {index}/{total}",current=index)
                    if action.startswith("charge_"):single="charge";enabled=action.endswith("_on")
                    elif action.startswith("discharge_"):single="discharge";enabled=action.endswith("_on")
                    else:single=action;enabled=None
                    results[name]=self._run_control(name,single,enabled);completed.append(name)
                except Exception as exc:
                    remaining=list(targets[index:])
                    done=", ".join(completed) if completed else "none"
                    untouched=", ".join(remaining) if remaining else "none"
                    raise RuntimeError(f"Set all stopped at Battery {index}/{total} ({name}): {str(exc).strip() or type(exc).__name__}. Completed before failure: {done}. Not changed: {untouched}") from exc
            return {"changed":True,"action":action,"results":results,"refresh_complete":True,"targets":targets}
        finally:
            if owns_radio:bluetooth_coordinator.release("bms_control")
            self._set_control_status(busy=False,phase="idle",message="",current=0,total=0)
