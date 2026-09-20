"""Durable local measurement history for reporting and analysis."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


class HistoryService:
    def __init__(self, path: Path):
        self.path=Path(path);self.lock=threading.RLock();self._bms_chart_cache=None;self._dashboard_chart_cache=None
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS measurements (id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL, source TEXT NOT NULL, device TEXT, payload TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_measurements_time ON measurements(captured_at DESC)")

    @contextmanager
    def _connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def record(self,source:str,payload,device:str|None=None,captured_at:str|None=None):
        stamp=captured_at or datetime.now(timezone.utc).isoformat()
        encoded=json.dumps(payload,separators=(",",":"),ensure_ascii=False,default=str)
        with self.lock,self._connect() as db:db.execute("INSERT INTO measurements(captured_at,source,device,payload) VALUES(?,?,?,?)",(stamp,source,device,encoded))

    def latest(self,limit:int=100):
        limit=max(1,min(int(limit),1000))
        with self.lock,self._connect() as db:
            rows=db.execute("SELECT id,captured_at,source,device,payload FROM measurements ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
        return [{"id":row[0],"captured_at":row[1],"source":row[2],"device":row[3],"values":json.loads(row[4])} for row in rows]

    def count(self):
        with self.lock,self._connect() as db:return db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]

    def bms_series(self,after_id:int=0):
        """Return compact, chronological BMS samples for incremental chart updates."""
        after=max(0,int(after_id))
        with self.lock,self._connect() as db:
            boundary=db.execute("SELECT MIN(id),MAX(id) FROM measurements WHERE source='bms'").fetchone()
            rows=db.execute("SELECT id,captured_at,device,payload FROM measurements WHERE source='bms' AND id>? ORDER BY id",(after,)).fetchall()
        points=[]
        for record_id,captured_at,device,encoded in rows:
            try:
                payload=json.loads(encoded)
                battery=str(device or payload.get("battery") or "").upper()
                if battery not in ("BATTERY 1","BATTERY 2","BATTERY 3"):continue
                points.append({
                    "id":record_id,"captured_at":captured_at,"battery":battery,
                    "voltage":payload.get("pack_voltage_v"),
                    "current":payload.get("current_a"),
                    "remaining":payload.get("remaining_capacity_ah"),
                    "cells":payload.get("cells_mv") or [],
                    "cell_spread":payload.get("cell_spread_mv"),
                    "alarms":payload.get("alarms") or [],
                    "charge_mos":payload.get("charge_mosfet_on"),
                    "discharge_mos":payload.get("discharge_mosfet_on"),
                })
            except (TypeError,ValueError,json.JSONDecodeError):
                continue
        return {"earliest_id":boundary[0] or 0,"latest_id":boundary[1] or 0,"points":points}

    def dashboard_series(self,after_id:int=0):
        """Return compact Dashboard samples used by the Sources and Loads charts."""
        after=max(0,int(after_id))
        with self.lock,self._connect() as db:
            boundary=db.execute("SELECT MIN(id),MAX(id) FROM measurements WHERE source='dashboard'").fetchone()
            rows=db.execute("SELECT id,captured_at,payload FROM measurements WHERE source='dashboard' AND id>? ORDER BY id",(after,)).fetchall()
        points=[]
        for record_id,captured_at,encoded in rows:
            try:
                payload=json.loads(encoded);sources=payload.get("sources") or {};loads=payload.get("consumers") or {}
                shore=sources.get("shore") or {};house=sources.get("charger_house") or {};alternator=sources.get("alternator") or {};solar=sources.get("solar") or {}
                inverter=loads.get("inverter") or {};ac=loads.get("house_ac") or {}
                source_keys=("shore","charger_house","alternator","solar")
                load_keys=("inverter","charger_start","charger_bow","engine_ecu","alternator_field","other_dc")
                points.append({"id":record_id,"captured_at":captured_at,
                    "source_power":[(sources.get(key) or {}).get("power") for key in source_keys[1:]],
                    "source_current":[(sources.get(key) or {}).get("current") for key in source_keys[1:]],
                    "shore_voltage":shore.get("voltage"),"shore_connected":shore.get("connected"),
                    "solar_panel_voltage":solar.get("panel_voltage"),"alternator_temperature":alternator.get("temperature"),
                    "alternator_running":alternator.get("running"),
                    "load_power":[(loads.get(key) or {}).get("power") for key in load_keys],
                    "load_current":[(loads.get(key) or {}).get("current") for key in load_keys],
                    "ac_power":ac.get("power"),"ac_frequency":ac.get("output_frequency"),
                    "inverting":inverter.get("inverting"),"supporting":inverter.get("supporting")})
            except (TypeError,ValueError,json.JSONDecodeError):continue
        return {"earliest_id":boundary[0] or 0,"latest_id":boundary[1] or 0,"points":points}

    def refresh_chart_cache(self):
        """Synchronize normalized chart data with SQLite and keep it server-side."""
        with self.lock:
            bms_after=self._bms_chart_cache[-1]["id"] if self._bms_chart_cache else 0
            dashboard_after=self._dashboard_chart_cache[-1]["id"] if self._dashboard_chart_cache else 0
            bms=self.bms_series(bms_after);dashboard=self.dashboard_series(dashboard_after)
            if self._bms_chart_cache is None:self._bms_chart_cache=[]
            if self._dashboard_chart_cache is None:self._dashboard_chart_cache=[]
            self._bms_chart_cache.extend(bms["points"]);self._dashboard_chart_cache.extend(dashboard["points"])
            self._bms_chart_cache=[p for p in self._bms_chart_cache if p["id"]>=bms["earliest_id"]]
            self._dashboard_chart_cache=[p for p in self._dashboard_chart_cache if p["id"]>=dashboard["earliest_id"]]
            return len(bms["points"])+len(dashboard["points"])

    def chart_data(self,hours:float,refresh:bool=False):
        with self.lock:
            if refresh or self._bms_chart_cache is None or self._dashboard_chart_cache is None:self.refresh_chart_cache()
            all_points=self._bms_chart_cache+self._dashboard_chart_cache
            latest=max((p["captured_at"] for p in all_points),default=datetime.now(timezone.utc).isoformat())
            cutoff=(datetime.fromisoformat(latest)-timedelta(hours=max(.25,float(hours)))).isoformat()
            bms_full=[p for p in self._bms_chart_cache if p["captured_at"]>=cutoff];dashboard_full=[p for p in self._dashboard_chart_cache if p["captured_at"]>=cutoff]
            def sample(points,maximum):
                if len(points)<=maximum:return points
                step=(len(points)-1)/(maximum-1)
                return [points[round(index*step)] for index in range(maximum)]
            def sample_bms(points,maximum):
                """Keep uniform detail plus every alarm neighbourhood and MOS transition."""
                if len(points)<=maximum:return points
                selected={point["id"]:point for point in sample(points,maximum)}
                previous_charge=previous_discharge=None
                for index,point in enumerate(points):
                    transition=(previous_charge is not None and point.get("charge_mos")!=previous_charge) or (previous_discharge is not None and point.get("discharge_mos")!=previous_discharge)
                    if point.get("alarms") or transition:
                        for nearby in points[max(0,index-2):min(len(points),index+3)]:selected[nearby["id"]]=nearby
                    previous_charge=point.get("charge_mos");previous_discharge=point.get("discharge_mos")
                return sorted(selected.values(),key=lambda point:point["id"])
            # Sample each battery independently while retaining every safety event.
            bms=[]
            for battery in ("BATTERY 1","BATTERY 2","BATTERY 3"):bms.extend(sample_bms([p for p in bms_full if p["battery"]==battery],1200))
            bms.sort(key=lambda p:p["id"]);dashboard=sample(dashboard_full,2500)
            return {"bms":bms,"dashboard":dashboard,"bms_count":len(bms_full),"dashboard_count":len(dashboard_full),"latest":latest,"cutoff":cutoff}

    def prune(self,retention_days:int):
        """Delete measurements older than the configured rolling retention period."""
        days=max(1,min(int(retention_days),365))
        cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
        with self.lock,self._connect() as db:
            cursor=db.execute("DELETE FROM measurements WHERE captured_at < ?",(cutoff,))
            return cursor.rowcount

    def json_lines(self):
        with self.lock,self._connect() as db:rows=db.execute("SELECT id,captured_at,source,device,payload FROM measurements ORDER BY id").fetchall()
        for row in rows:
            yield json.dumps({"id":row[0],"captured_at":row[1],"source":row[2],"device":row[3],"values":json.loads(row[4])},ensure_ascii=False)+"\n"
