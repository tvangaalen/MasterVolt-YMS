"""Bluetooth event log and per-device counters for the DALY BMS and balancer links.

The measurement history only records *successful* readings, so connection trouble left no trace. Every
connect, read, timeout and watchdog action is logged here: to a rotating file (logs/bluetooth.log), to a small
in-memory ring buffer and into per-device counters that the API exposes (/api/bluetooth-events).
Logging must never disturb the Bluetooth code, so every failure to log is swallowed.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


class BleEventLog:
    def __init__(self,path=None,keep=300):
        self.lock=threading.Lock();self.events=deque(maxlen=keep);self.counters={};self._logger=None
        if path:self.configure(path)

    def configure(self,path):
        try:
            path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
            logger=logging.getLogger("mastervolt.bluetooth");logger.setLevel(logging.INFO);logger.propagate=False
            for handler in list(logger.handlers):logger.removeHandler(handler)
            handler=RotatingFileHandler(path,maxBytes=1_000_000,backupCount=3,encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"));logger.addHandler(handler);self._logger=logger
        except Exception:self._logger=None

    def _counter(self,device):
        return self.counters.setdefault(device,{"events":{},"failures":{},"consecutive_failures":0,"last_success_epoch":None,"last_error":None,"last_error_epoch":None})

    def log(self,device,kind,detail="",success=False,failure=False,quiet=False):
        """Record one event. `success` resets the failure streak; `failure` extends it.
        `quiet` events (the routine successes, thousands per day) only update the counters, not the ring buffer or the file."""
        now=time.time();entry={"time":datetime.fromtimestamp(now,timezone.utc).isoformat(timespec="seconds"),"device":device,"kind":kind,"detail":str(detail)[:300]}
        with self.lock:
            counter=self._counter(device);counter["events"][kind]=counter["events"].get(kind,0)+1
            if success:counter["consecutive_failures"]=0;counter["last_success_epoch"]=now
            if failure:
                counter["consecutive_failures"]+=1;counter["failures"][kind]=counter["failures"].get(kind,0)+1
                counter["last_error"]=entry["detail"] or kind;counter["last_error_epoch"]=now
            if not quiet:self.events.append(entry)
        if self._logger and not quiet:
            try:self._logger.info("%s %s%s",device,kind,f" | {entry['detail']}" if entry["detail"] else "")
            except Exception:pass

    def snapshot(self,limit=100):
        with self.lock:
            return {"counters":{device:{**counter,"events":dict(counter["events"]),"failures":dict(counter["failures"])} for device,counter in self.counters.items()},
                "events":list(self.events)[-max(1,min(int(limit),300)):]}


ble_log=BleEventLog()
