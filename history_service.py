"""Durable local measurement history for reporting and analysis."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from bisect import bisect_left
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

    BATTERIES = ("BATTERY 1", "BATTERY 2", "BATTERY 3")
    MAX_GAP_DASHBOARD = 120.0     # seconds; longer gaps (server down) are not integrated
    MAX_GAP_BMS = 180.0           # seconds; longer gaps (Bluetooth down) are not integrated

    @staticmethod
    def _utc(value):
        try:moment=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        except ValueError:raise ValueError("start and end must be ISO 8601 timestamps")
        return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)

    @staticmethod
    def _watts(point):
        cells=point.get("cells") or [];voltage=sum(cells)/1000 if len(cells)>=2 and all(isinstance(cell,(int,float)) for cell in cells) else point.get("voltage")
        current=point.get("current")
        return voltage*current if isinstance(voltage,(int,float)) and isinstance(current,(int,float)) else None

    @staticmethod
    def _vector(point):
        """Nine non-negative channel powers: 3 sources (charger house, alternator, solar) + 6 DC consumers."""
        out=[]
        for field,count in (("source_power",3),("load_power",6)):
            values=point.get(field) or []
            out.extend(max(0.0,float(values[index] or 0.0)) if index<len(values) else 0.0 for index in range(count))
        return out

    def _index(self):
        """Timestamp arrays, battery power and alarm indexes over the full-resolution caches.
        Rebuilt only when the caches change; hourly energy totals are remembered inside it."""
        dash=self._dashboard_chart_cache;bms=self._bms_chart_cache
        signature=(len(dash),dash[0]["id"] if dash else 0,dash[-1]["id"] if dash else 0,len(bms),bms[0]["id"] if bms else 0,bms[-1]["id"] if bms else 0)
        cached=getattr(self,"_contribution_index",None)
        if cached and cached["signature"]==signature:return cached
        stamp=lambda point:datetime.fromisoformat(point["captured_at"]).timestamp()
        timed=sorted(((stamp(point),point) for point in dash),key=lambda item:item[0])
        index={"signature":signature,"dash_t":[t for t,_ in timed],"dash":[point for _,point in timed],"battery":{},"alarm_samples":{},"alarm_starts":{},"hours":{}}
        for name in self.BATTERIES:
            items=sorted(((stamp(point),point) for point in bms if point.get("battery")==name),key=lambda item:item[0])
            index["battery"][name]={"t":[t for t,_ in items],"w":[self._watts(point) for _,point in items]}
            samples=[];starts=[];previous=False
            for t,point in items:
                active=bool(point.get("alarms"))
                if active:
                    samples.append(t)
                    if not previous:starts.append(t)
                previous=active
            index["alarm_samples"][name]=samples;index["alarm_starts"][name]=starts
        latest=[index["dash_t"][-1]] if index["dash_t"] else []
        latest+=[data["t"][-1] for data in index["battery"].values() if data["t"]]
        index["latest"]=max(latest,default=0.0)
        self._contribution_index=index
        return index

    def _raw_energy(self,index,a,b):
        """Energy of every sample pair whose first sample lies in [a, b) (so parts add up exactly).
        Returns (channel Wh x9, dashboard covered seconds, {battery: [out Wh, in Wh, covered s]})."""
        times=index["dash_t"];points=index["dash"];low=bisect_left(times,a);high=min(bisect_left(times,b),len(points)-1)
        energy=[0.0]*9;covered=0.0
        if low<high:
            previous=self._vector(points[low])
            for i in range(low,high):
                following=self._vector(points[i+1]);dt=times[i+1]-times[i]
                if 0<dt<=self.MAX_GAP_DASHBOARD:
                    covered+=dt;factor=dt/7200.0
                    for k in range(9):energy[k]+=(previous[k]+following[k])*factor
                previous=following
        batteries={}
        for name in self.BATTERIES:
            data=index["battery"][name];bt=data["t"];bw=data["w"];out=into=cover=0.0
            first=bisect_left(bt,a);last=min(bisect_left(bt,b),len(bt)-1)
            for i in range(first,last):
                w1,w2=bw[i],bw[i+1];dt=bt[i+1]-bt[i]
                if 0<dt<=self.MAX_GAP_BMS and w1 is not None and w2 is not None:
                    cover+=dt;power=(w1+w2)/2
                    if power>=0:into+=power*dt/3600
                    else:out+=-power*dt/3600
            batteries[name]=[out,into,cover]
        return energy,covered,batteries

    def _hour_energy(self,index,hour):
        """Hourly totals are remembered once the hour is finished (10 minutes of margin for late samples)."""
        if hour in index["hours"]:return index["hours"][hour]
        result=self._raw_energy(index,hour*3600.0,(hour+1)*3600.0)
        if (hour+1)*3600.0<=index["latest"]-600:index["hours"][hour]=result
        return result

    def contributions(self,start,end):
        """Totals over a period from the full-resolution caches (not the sampled chart data).

        Energy is the time integral of power between consecutive samples; pairs further apart than the
        maximum gap (server or Bluetooth down) are left out and reported as missing coverage.
        sources/consumers: Wh per channel. batteries: Wh delivered (discharge) and received (charge).
        alarms: alarm episodes (a run of alarm samples) and alarm samples per battery.
        Whole finished hours come from remembered hourly totals; only the two edges are computed."""
        first,last=self._utc(start),self._utc(end)
        if last<=first:raise ValueError("end must be after start")
        with self.lock:
            if self._bms_chart_cache is None or self._dashboard_chart_cache is None:self.refresh_chart_cache()
            index=self._index()
        a,b=first.timestamp(),last.timestamp()
        h0,h1=math.ceil(a/3600),math.floor(b/3600)
        parts=[self._raw_energy(index,a,h0*3600.0),*(self._hour_energy(index,hour) for hour in range(h0,h1)),self._raw_energy(index,h1*3600.0,b)] if h1>h0 else [self._raw_energy(index,a,b)]
        energy=[sum(part[0][k] for part in parts) for k in range(9)];covered=sum(part[1] for part in parts)
        rows=lambda keys,values:[{"key":key,"wh":wh,"avg_w":wh/(covered/3600) if covered else 0.0} for key,wh in zip(keys,values)]
        batteries=[];alarms=[]
        for name in self.BATTERIES:
            out=sum(part[2][name][0] for part in parts);into=sum(part[2][name][1] for part in parts);cover=sum(part[2][name][2] for part in parts)
            batteries.append({"key":name,"discharge_wh":out,"charge_wh":into,"covered_seconds":cover,"avg_discharge_w":out/(cover/3600) if cover else 0.0})
            count=lambda times:bisect_left(times,b)-bisect_left(times,a)
            alarms.append({"key":name,"episodes":count(index["alarm_starts"][name]),"samples":count(index["alarm_samples"][name])})
        return {"start":first.isoformat(),"end":last.isoformat(),"span_seconds":b-a,
            "sources":rows(("charger_house","alternator","solar"),energy[:3]),"sources_covered_seconds":covered,
            "consumers":rows(("inverter","charger_start","charger_bow","engine_ecu","alternator_field","other_dc"),energy[3:]),"consumers_covered_seconds":covered,
            "batteries":batteries,"alarms":alarms}

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
