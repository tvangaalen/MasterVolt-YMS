"""Process-wide priority gate for Windows Bluetooth discovery and connections."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager


class BluetoothCoordinator:
    """Serialize radio work with deterministic user-first priorities."""

    PRIORITY={
        "bms_control":0,
        "bms_manual_read":1,
        "bms_manual_connect":1,
        "bms":2,
        "bms_read":3,
        "balancer":4,
    }

    def __init__(self, required_bms=3, stable_seconds=0):
        self.condition=threading.Condition()
        self.required_bms=int(required_bms)
        self.stable_seconds=float(stable_seconds)
        self.connected_bms=set()
        self.refreshed_bms=set()
        self.stable_since=None
        self.active_kind=None
        self.waiters={kind:0 for kind in self.PRIORITY}

    def mark_bms(self,name,connected):
        with self.condition:
            if connected:self.connected_bms.add(name)
            else:
                self.connected_bms.discard(name)
                self.refreshed_bms.discard(name)
                self.stable_since=None
            self.condition.notify_all()

    def mark_bms_refreshed(self,name):
        with self.condition:
            if name in self.connected_bms:self.refreshed_bms.add(name)
            if len(self.connected_bms)>=self.required_bms and len(self.refreshed_bms)>=self.required_bms and self.stable_since is None:
                self.stable_since=time.monotonic()
            self.condition.notify_all()

    def bms_ready(self):
        with self.condition:
            return bool(
                len(self.connected_bms)>=self.required_bms
                and len(self.refreshed_bms)>=self.required_bms
                and self.stable_since is not None
                and time.monotonic()-self.stable_since>=self.stable_seconds
            )

    def acquire(self,kind,timeout):
        if kind not in self.PRIORITY:raise ValueError(f"Unknown Bluetooth queue kind: {kind}")
        deadline=time.monotonic()+max(0,float(timeout))
        with self.condition:
            self.waiters[kind]+=1
            try:
                while True:
                    priority=self.PRIORITY[kind]
                    higher_waiting=any(count and self.PRIORITY[other]<priority for other,count in self.waiters.items())
                    eligible=(
                        kind in {"bms_control","bms","bms_manual_connect"}
                        or (kind in {"bms_read","bms_manual_read"} and bool(self.connected_bms))
                        or (kind=="balancer" and self.bms_ready())
                    )
                    if self.active_kind is None and not higher_waiting and eligible:
                        self.active_kind=kind
                        return True
                    remaining=deadline-time.monotonic()
                    if remaining<=0:return False
                    self.condition.wait(min(.5,remaining))
            finally:
                self.waiters[kind]=max(0,self.waiters[kind]-1)
                self.condition.notify_all()

    def release(self,kind):
        with self.condition:
            if self.active_kind==kind:self.active_kind=None
            self.condition.notify_all()

    @contextmanager
    def lease(self,kind,timeout=30):
        granted=self.acquire(kind,timeout)
        try:yield granted
        finally:
            if granted:self.release(kind)

    def snapshot(self):
        with self.condition:
            stable_for=0 if self.stable_since is None else max(0,time.monotonic()-self.stable_since)
            waiting_bms=sum(self.waiters[k] for k in ("bms_control","bms_manual_read","bms_manual_connect","bms"))
            return {"active":self.active_kind,"waiting_bms":waiting_bms,"waiting":dict(self.waiters),"connected_bms":len(self.connected_bms),"refreshed_bms":len(self.refreshed_bms),"bms_stable_seconds":round(stable_for,1),"balancers_allowed":self.bms_ready()}


bluetooth_coordinator=BluetoothCoordinator()
