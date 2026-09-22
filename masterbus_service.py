import json,threading,time
from pathlib import Path
from masterbus_usb import MasterBusUsb
from masterbus_protocol import monitoring_request,encode_set_float,encode_set_boolean,encode_commit,decode_monitoring
from masterbus_registry import *
from masterbus_discovery import Discovery
from house_soc import float_decision
from masterbus_protocol import btm3_read_request, btm3_write_float, decode_btm3_value

class MasterBusService:
    def __init__(self):
        self.bus=MasterBusUsb()
        self.io_lock=threading.RLock()
        self.cache_lock=threading.RLock()
        self.settings_lock=threading.RLock()
        self.cache={}
        self.smart_maps={}
        self.discovery_status="not_started"
        self.stop_event=threading.Event()
        self.maps_file=Path(__file__).resolve().parent/"device_maps.json"
        # Alternator current and total house DC load cannot both be solved from
        # one instantaneous house-shunt balance. Learn total house DC load while
        # the alternator is stopped, then hold that baseline while it is running.
        # The displayed Other DC load is the learned total minus individually
        # measured Start charger, Bow charger and Engine ECU input currents.
        self.total_house_load_baseline_a = None
        self.total_house_load_baseline_ts = None
        self.control_maps_file=Path(__file__).resolve().parent/"control_maps.json"
        self.control_maps={}
        self._load_control_maps()
        self.mastershunt_config_file=Path(__file__).resolve().parent/"mastershunt_config_maps.json"
        self.mastershunt_config_maps={}
        self._load_mastershunt_config_maps()
        self.ac_support_enabled=None
        self.settings_file=Path(__file__).resolve().parent/"user_settings.json"
        self.settings={
            "default_ac_limit":15,
            "float_protection_enabled":True,
            "house_battery_soc":95.0,
            "bulk_resume_soc":90.0,
            "float_cell_trigger_mv":3500.0,
            "float_cell_resume_mv":3420.0,
            "warning_popup_seconds":8,
            "bms_refresh_interval":30,
            "bms_popup_seconds":3,
            "bms_connection_retry_seconds":5,
            "balancer_refresh_interval":30,
            "balancer_connection_retry_seconds":30,
            "history_retention_days":7,
        }
        self._load_settings()
        self.float_policy_status={
            "enabled":self.settings["float_protection_enabled"],
            "threshold_soc":self.settings["house_battery_soc"],
            "bulk_resume_soc":self.settings["bulk_resume_soc"],
            "cell_trigger_mv":self.settings["float_cell_trigger_mv"],
            "cell_resume_mv":self.settings["float_cell_resume_mv"],
            "active":False,"house_soc":None,"max_cell_mv":None,"max_cell_battery":None,
            "max_spread_mv":None,"max_spread_battery":None,"trigger":None,
            "sources":{},"event_id":0,"bulk_event_id":0,"float_latched":False,
            "server_side":True,"check_interval_seconds":3,"last_check_at":None,
        }
        self.float_policy_last_attempt={}

    def _load_settings(self):
        """Load validated persistent UI settings, keeping safe defaults."""
        try:
            raw=json.loads(self.settings_file.read_text(encoding="utf-8"))
            if not isinstance(raw,dict):return
            # Migrate the former current-delta setting to SOC hysteresis.
            if "bulk_resume_soc" not in raw:
                raw["bulk_resume_soc"]=max(0,float(raw.get("house_battery_soc",95))-5)
            raw.pop("bulk_resume_delta",None)
            self._validate_settings(raw,partial=True)
            self.settings.update({k:raw[k] for k in self.settings if k in raw})
        except Exception:
            pass

    @staticmethod
    def _validate_settings(values,partial=False):
        required={
            "default_ac_limit","float_protection_enabled",
            "house_battery_soc","bulk_resume_soc","float_cell_trigger_mv","float_cell_resume_mv","warning_popup_seconds","bms_refresh_interval","bms_popup_seconds","bms_connection_retry_seconds","balancer_refresh_interval","balancer_connection_retry_seconds","history_retention_days",
        }
        if not partial and set(values)!=required:
            raise ValueError("All settings fields are required")
        unknown=set(values)-required
        if unknown:raise ValueError("Unknown settings: "+", ".join(sorted(unknown)))
        if "default_ac_limit" in values:
            value=values["default_ac_limit"]
            if isinstance(value,bool) or int(value)!=value or not 3<=int(value)<=15:
                raise ValueError("Default AC limit must be a whole number from 3 to 15 A")
        if "float_protection_enabled" in values and not isinstance(values["float_protection_enabled"],bool):
            raise ValueError("Float protection must be on or off")
        if "house_battery_soc" in values:
            value=values["house_battery_soc"]
            if isinstance(value,bool) or not 50<=float(value)<=100:
                raise ValueError("Switch to Float SOC must be between 50 and 100%")
        if "bulk_resume_soc" in values:
            value=values["bulk_resume_soc"]
            if isinstance(value,bool) or not 0<=float(value)<=95:
                raise ValueError("Switch to Bulk SOC must be between 0 and 95%")
        if "house_battery_soc" in values and "bulk_resume_soc" in values:
            if float(values["bulk_resume_soc"])>float(values["house_battery_soc"])-5:
                raise ValueError("Switch to Bulk when SOC must be at least 5% lower than Switch to Float when SOC")
        if "float_cell_trigger_mv" in values:
            value=values["float_cell_trigger_mv"]
            if isinstance(value,bool) or not 3300<=float(value)<=3650:
                raise ValueError("Float cell-voltage trigger must be between 3300 and 3650 mV")
        if "float_cell_resume_mv" in values:
            value=values["float_cell_resume_mv"]
            if isinstance(value,bool) or not 3200<=float(value)<=3650:
                raise ValueError("Float cell-voltage resume level must be between 3200 and 3650 mV")
        if "float_cell_trigger_mv" in values and "float_cell_resume_mv" in values:
            if float(values["float_cell_resume_mv"])>float(values["float_cell_trigger_mv"])-30:
                raise ValueError("Float cell-voltage resume level must be at least 30 mV below the trigger")
        if "warning_popup_seconds" in values:
            value=values["warning_popup_seconds"]
            if isinstance(value,bool) or int(value)!=value or not 1<=int(value)<=60:
                raise ValueError("Warning pop-duration must be a whole number from 1 to 60 seconds")
        if "bms_refresh_interval" in values:
            value=values["bms_refresh_interval"]
            if isinstance(value,bool) or int(value)!=value or not 5<=int(value)<=300:
                raise ValueError("BMS refresh interval must be a whole number from 5 to 300 seconds")
        if "bms_popup_seconds" in values:
            value=values["bms_popup_seconds"]
            if isinstance(value,bool) or int(value)!=value or not 1<=int(value)<=60:
                raise ValueError("BMS pop-up duration must be a whole number from 1 to 60 seconds")
        if "bms_connection_retry_seconds" in values:
            value=values["bms_connection_retry_seconds"]
            if isinstance(value,bool) or int(value)!=value or not 1<=int(value)<=300:
                raise ValueError("BMS connection retry interval must be a whole number from 1 to 300 seconds")
        if "balancer_connection_retry_seconds" in values:
            value=values["balancer_connection_retry_seconds"]
            if isinstance(value,bool) or int(value)!=value or not 5<=int(value)<=300:
                raise ValueError("Balancer connection retry interval must be a whole number from 5 to 300 seconds")
        if "balancer_refresh_interval" in values:
            value=values["balancer_refresh_interval"]
            if isinstance(value,bool) or int(value)!=value or not 5<=int(value)<=300:
                raise ValueError("Balancer refresh interval must be a whole number from 5 to 300 seconds")
        if "history_retention_days" in values:
            value=values["history_retention_days"]
            if isinstance(value,bool) or int(value)!=value or not 1<=int(value)<=365:
                raise ValueError("Save history period must be a whole number from 1 to 365 days")

    def get_settings(self):
        with self.settings_lock:return dict(self.settings)

    def update_settings(self,values):
        self._validate_settings(values)
        normalized={
            "default_ac_limit":int(values["default_ac_limit"]),
            "float_protection_enabled":bool(values["float_protection_enabled"]),
            "house_battery_soc":float(values["house_battery_soc"]),
            "bulk_resume_soc":float(values["bulk_resume_soc"]),
            "float_cell_trigger_mv":float(values["float_cell_trigger_mv"]),
            "float_cell_resume_mv":float(values["float_cell_resume_mv"]),
            "warning_popup_seconds":int(values["warning_popup_seconds"]),
            "bms_refresh_interval":int(values["bms_refresh_interval"]),
            "bms_popup_seconds":int(values["bms_popup_seconds"]),
            "bms_connection_retry_seconds":int(values["bms_connection_retry_seconds"]),
            "balancer_refresh_interval":int(values["balancer_refresh_interval"]),
            "balancer_connection_retry_seconds":int(values["balancer_connection_retry_seconds"]),
            "history_retention_days":int(values["history_retention_days"]),
        }
        with self.settings_lock:
            temporary=self.settings_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(normalized,indent=2)+"\n",encoding="utf-8")
            temporary.replace(self.settings_file)
            self.settings=normalized
        self.float_policy_status["enabled"]=normalized["float_protection_enabled"]
        self.float_policy_status["threshold_soc"]=normalized["house_battery_soc"]
        self.float_policy_status["bulk_resume_soc"]=normalized["bulk_resume_soc"]
        return dict(normalized)

    def _load_mastershunt_config_maps(self):
        """Load previously discovered, name-verified MasterShunt config fields."""
        try:
            raw=json.loads(self.mastershunt_config_file.read_text(encoding="utf-8"))
            self.mastershunt_config_maps=raw if isinstance(raw,dict) else {}
        except Exception:
            self.mastershunt_config_maps={}

    def _discover_mastershunt_config(self):
        """Discover config fields by metadata name; never guess numeric IDs.

        MasterShunt firmware revisions do not necessarily expose configuration
        at identical indexes. Only fields whose device-provided names match a
        conservative allow-list are accepted. Existing UI type labels remain
        the fallback until both metadata and a usable value are available.
        """
        from masterbus_control_discovery import ControlDiscovery

        type_names={"battery type","battery technology","battery chemistry"}
        capacity_names={
            "battery capacity","nominal capacity","installed capacity",
            "bank capacity","capacity",
        }
        devices={
            "house":(HOUSE_SHUNT,DEVICE_INFO[HOUSE_SHUNT]["max_index"]),
            "start":(START_SHUNT,DEVICE_INFO[START_SHUNT]["max_index"]),
            "bow":(BOW_SHUNT,DEVICE_INFO[BOW_SHUNT]["max_index"]),
        }
        discovered={}
        discovery=Discovery(self)
        options=ControlDiscovery(self)
        for key,(addr,max_index) in devices.items():
            if self.stop_event.is_set():
                return
            try:
                schema=discovery.schema(addr,max_index)
            except Exception:
                continue
            item={"address":f"{addr:06X}"}
            for field in schema:
                name=" ".join((field.get("name") or "").lower().split())
                if name in type_names and "type_field" not in item:
                    item["type_field"]=int(field["index"])
                    item["type_name"]=field.get("name") or ""
                    try:
                        item["type_options"]=options.list_options(addr,int(field["index"]))
                    except Exception:
                        item["type_options"]=[]
                if name in capacity_names and "capacity_field" not in item:
                    item["capacity_field"]=int(field["index"])
                    item["capacity_name"]=field.get("name") or ""
                    item["capacity_unit"]=field.get("unit") or "A"
            if "type_field" in item or "capacity_field" in item:
                discovered[key]=item
        if discovered:
            self.mastershunt_config_maps=discovered
            try:
                self.mastershunt_config_file.write_text(
                    json.dumps(discovered,indent=2),encoding="utf-8"
                )
            except Exception:
                pass

    @staticmethod
    def _clean_battery_type(label):
        label=" ".join(str(label or "").strip().split())
        if not label or len(label)>24:
            return None
        # Device metadata is displayed in HTML; accept only a compact, benign
        # chemistry/type label (for example MLI, AGM or LiFePO4).
        allowed=set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 +-/().")
        return label if all(ch in allowed for ch in label) else None

    def mastershunt_config(self,key,addr,fallback_type):
        cfg=self.mastershunt_config_maps.get(key,{})
        battery_type=None
        capacity=None
        type_field=cfg.get("type_field")
        capacity_field=cfg.get("capacity_field")
        if type_field is not None:
            raw=self.cached(addr,int(type_field))
            if raw is not None:
                for option in cfg.get("type_options",[]):
                    try:
                        if abs(float(option.get("index"))-float(raw))<=.05:
                            battery_type=self._clean_battery_type(option.get("label"))
                            break
                    except (TypeError,ValueError):
                        continue
        if capacity_field is not None:
            raw=self.cached(addr,int(capacity_field))
            try:
                if raw is not None and 0<float(raw)<100000:
                    capacity=float(raw)
            except (TypeError,ValueError):
                pass
        return {
            "battery_type":battery_type or fallback_type,
            "capacity":capacity,
            "capacity_unit":cfg.get("capacity_unit") or "A",
            "config_discovered":bool(battery_type or capacity is not None),
        }

    def _load_control_maps(self):
        try:
            raw=json.loads(self.control_maps_file.read_text(encoding="utf-8"))
            self.control_maps=raw if isinstance(raw,dict) else {}
        except Exception:
            self.control_maps={}

    def control_capabilities(self):
        out = {}
        for name in ("charger_start", "charger_bow", "engine_ecu", "solar"):
            cfg = self.control_maps.get(name)
            if not cfg:
                out[name] = None
                continue

            item = dict(cfg)
            item["current"] = None
            item["raw_current"] = None

            try:
                addr = int(cfg["address"], 16) if isinstance(cfg["address"], str) else int(cfg["address"])
                field = int(cfg["field"])

                if cfg.get("protocol") == "btm3":
                    item["current"] = self._read_btm3(addr, field)
                else:
                    # Prefer background cache, then fall back to direct read.
                    raw = self.cached(addr, field)
                    if raw is None:
                        raw = self.read_field(addr, field, .55)

                    item["raw_current"] = raw

                    if cfg.get("type") == "dropdown":
                        if cfg.get("on_index") is not None and raw is not None:
                            item["current"] = abs(
                                float(raw) - float(cfg["on_index"])
                            ) <= 0.05
                    else:
                        item["current"] = raw
            except Exception:
                pass

            out[name] = item

        return out

    def _read_btm3(self,addr,field,timeout_s=.8):
        with self.io_lock:
            self.bus.drain()
            cid,p=btm3_read_request(addr,field)
            self.bus.send_frame(cid,p)
            alt=(addr|0x800000)&0xFFFFFF
            end=time.monotonic()+timeout_s
            while time.monotonic()<end:
                for frame in self.bus.read_frames(80):
                    if frame.can_class==0x0B and frame.address==alt:
                        v=decode_btm3_value(frame)
                        if v is not None:
                            return v
        raise TimeoutError(f"No Btm3 response {addr:06X}:{field}")

    def _set_mapped_control(self,name,enabled):
        cfg=self.control_maps.get(name)
        if not cfg or not cfg.get("verified"):
            raise RuntimeError(f"{name} ON/OFF control is not verified yet")

        addr=int(cfg["address"],16) if isinstance(cfg["address"],str) else int(cfg["address"])
        field=int(cfg["field"])
        proto=cfg.get("protocol","btm1")
        if cfg.get("type") == "dropdown":
            if cfg.get("on_index") is None or cfg.get("off_index") is None:
                raise RuntimeError(f"{name} dropdown semantics are not verified")
            target=float(cfg["on_index"] if enabled else cfg["off_index"])
        else:
            target=1.0 if enabled else 0.0

        if proto=="btm3":
            current=self._read_btm3(addr,field)
            if abs(current-target)<=.05:
                return {"changed":False,"value":enabled}
            with self.io_lock:
                self.bus.drain()
                cid,p=btm3_write_float(addr,field,target)
                self.bus.send_frame(cid,p)
            time.sleep(.35)
            actual=self._read_btm3(addr,field)
            if abs(actual-target)>.05:
                raise RuntimeError(f"{name} verify failed: expected {target}, got {actual}")
            return {"changed":True,"value":enabled,"raw_value":actual}

        current=self.read_field(addr,field)
        if abs(current-target)<=.05:
            return {"changed":False,"value":enabled}

        # Generic Btm1 boolean. Only use commit when explicitly configured.
        with self.io_lock:
            self.bus.drain()
            if cfg.get("type") == "dropdown":
                cid,p=encode_set_float(addr,field,target)
            else:
                cid,p=encode_set_boolean(addr,field,enabled)
            self.bus.send_frame(cid,p)
            if cfg.get("commit_field") is not None:
                time.sleep(.05)
                cid,p=encode_commit(addr,int(cfg["commit_field"]))
                self.bus.send_frame(cid,p)

        time.sleep(.35)
        actual=self.read_field(addr,field)
        if abs(actual-target)>.05:
            raise RuntimeError(f"{name} verify failed: expected {target}, got {actual}")
        return {"changed":True,"value":enabled,"raw_value":actual}

    def set_device_control(self,name,enabled):
        if name=="solar":
            return self.set_solar_enabled(enabled)
        if name not in ("charger_start","charger_bow","engine_ecu"):
            raise ValueError("Unknown device control")
        return self._set_mapped_control(name,enabled)

    def set_solar_enabled(self, enabled):
        """
        Send SCM Solar field 12 On/Off request.

        The SCM metadata exposes field 12 (On/Off) followed by field 13, an
        unnamed writable companion register. The previous implementation wrote
        field 12 only; hardware testing showed that OFF was not applied.

        Use the same write+companion-commit pattern already verified elsewhere
        on MasterBus: write field 12, then commit through field 13.

        ON readback remains permissive because with insufficient PV the SCM can
        legitimately return itself to OFF immediately. OFF readback is strict:
        when we request OFF, field 12 must actually become 0.
        """
        addr=SOLAR
        field=12
        commit_field=13
        target=1.0 if enabled else 0.0

        # Read first so the response can accurately report whether we changed it.
        before=None
        try:
            before=self.read_field(addr,field,.7)
        except Exception:
            pass

        with self.io_lock:
            self.bus.drain()
            cid,p=encode_set_boolean(addr,field,bool(enabled))
            self.bus.send_frame(cid,p)
            time.sleep(.05)
            cid,p=encode_commit(addr,commit_field)
            self.bus.send_frame(cid,p)

        # Give the SCM time to apply the request, then use a fresh read.
        time.sleep(.35)
        actual=None
        try:
            actual=self.read_field(addr,field,.9)
        except Exception:
            pass

        # OFF is deterministic and must verify.  ON may immediately self-revert
        # to OFF when there is not enough PV, so it remains a successful request.
        if not enabled:
            if actual is None:
                raise RuntimeError("solar OFF verify failed: no field 12 readback")
            if abs(float(actual)) > .05:
                raise RuntimeError(
                    f"solar OFF verify failed: expected 0.0, got {actual}"
                )

        return {
            "changed": before is None or abs(float(before)-target)>.05,
            "requested":bool(enabled),
            "requested_raw":target,
            "actual_raw":actual,
            "actual":None if actual is None else bool(float(actual)>=.5),
            "commit_field":commit_field,
            "note":(
                "SCM may immediately return to OFF when PV input is insufficient."
                if enabled and actual is not None and float(actual)<.5 else None
            ),
        }

    def set_alternator_enabled(self, enabled):
        """
        Control Alpha Pro Stop Charge.

        ON:
          Clear field 39 (Stop charge) = 0.0 and commit field 40 only.
          Do NOT request Bulk. This deliberately tests whether the Alpha Pro
          resumes charging automatically once the stopped state is removed.

        OFF:
          Set field 39 (Stop charge) = 1.0 and commit field 40.

        Alpha Pro field 5 is the actual charger state. Hardware verification
        established state 5 as "Stopped". Active charge states 1/2/3 are ON.
        """
        addr=ALTERNATOR

        with self.io_lock:
            self.bus.drain()
            target=0.0 if enabled else 1.0
            cid,p=encode_set_float(addr,39,target)
            self.bus.send_frame(cid,p)
            time.sleep(.05)
            cid,p=encode_commit(addr,40)
            self.bus.send_frame(cid,p)

        requested_mode="Clear Stop charge" if enabled else "Stop charge"
        deadline=time.monotonic()+3.0
        last_state=None
        while time.monotonic()<deadline:
            try:
                last_state=self.read_field(addr,5,.55)
                n=int(round(float(last_state)))
                # OFF is confirmed by Stopped. For ON, success means the Alpha
                # Pro itself has left Stopped and entered a normal charge stage.
                success=(n in (1,2,3)) if enabled else (n==5)
                if success:
                    return {
                        "changed":True,
                        "requested":bool(enabled),
                        "requested_mode":requested_mode,
                        "state_raw":last_state,
                        "state":self.alternator_charge_state_label(last_state),
                        "actual":bool(enabled),
                        "field":39,
                        "commit_field":40,
                    }
            except Exception:
                pass
            time.sleep(.20)

        label=self.alternator_charge_state_label(last_state)
        actual_on=(last_state is not None and int(round(float(last_state))) in (1,2,3))
        return {
            "changed":True,
            "requested":bool(enabled),
            "requested_mode":requested_mode,
            "state_raw":last_state,
            "state":label,
            "actual":actual_on,
            "field":39,
            "commit_field":40,
            "pending":actual_on != bool(enabled),
            "note":(
                "Stop Charge was cleared; no Bulk command was sent."
                if enabled else None
            ),
        }

    @staticmethod
    def _norm_option_label(label):
        return " ".join(
            (label or "")
            .strip()
            .lower()
            .replace("_", " ")
            .replace("-", " ")
            .split()
        )
    
    def _resolve_engine_ecu_power_control(self):
        """
        Enable the ECU web control only when MasterBus itself clearly exposes
        OFF and ON option labels for field 56 ('Power').
        """
        try:
            from masterbus_control_discovery import ControlDiscovery
    
            cd = ControlDiscovery(self)
            options = cd.list_options(YANMAR, 56, "btm1")
    
            if not options:
                return {
                    "verified": False,
                    "address": f"{YANMAR:06X}",
                    "field": 56,
                    "protocol": "btm1",
                    "name": "Power",
                    "type": "dropdown",
                    "options": [],
                    "reason": "No dropdown option strings returned by MasterBus",
                }
    
            off_words = {
                "off", "power off", "disabled", "disable", "standby", "stand by"
            }
            on_words = {
                "on", "power on", "enabled", "enable", "active", "run", "running"
            }
    
            off_idx = None
            on_idx = None
    
            for opt in options:
                normalized = self._norm_option_label(opt.get("label"))
                if normalized in off_words:
                    off_idx = opt["index"]
                if normalized in on_words:
                    on_idx = opt["index"]
    
            verified = (
                off_idx is not None
                and on_idx is not None
                and off_idx != on_idx
            )
    
            result = {
                "verified": verified,
                "address": f"{YANMAR:06X}",
                "field": 56,
                "protocol": "btm1",
                "name": "Power",
                "type": "dropdown",
                "options": options,
                "off_index": off_idx,
                "on_index": on_idx,
            }
    
            if not verified:
                result["reason"] = (
                    "Dropdown labels did not unambiguously identify OFF and ON"
                )
    
            return result
    
        except Exception as exc:
            return {
                "verified": False,
                "address": f"{YANMAR:06X}",
                "field": 56,
                "protocol": "btm1",
                "name": "Power",
                "type": "dropdown",
                "reason": str(exc),
            }
    
    def open(self):
        with self.io_lock:self.bus.open()

    def close(self):
        self.stop_event.set()
        with self.io_lock:self.bus.close()

    def read_field(self,addr,field,timeout_s=.55):
        with self.io_lock:
            self.bus.drain()
            cid,p=monitoring_request(addr,field,0)
            self.bus.send_frame(cid,p)
            end=time.monotonic()+timeout_s
            while time.monotonic()<end:
                for f in self.bus.read_frames(70):
                    d=decode_monitoring(f)
                    if d and f.address==addr and d[0]==field and d[1]==0:
                        self._put(addr,field,d[2])
                        return d[2]
        raise TimeoutError(f"No response {addr:06X}:{field}")

    def _put(self,addr,field,value):
        with self.cache_lock:self.cache[(addr,field)]={"value":value,"ts":time.time()}

    def cached(self,addr,field,max_age=20):
        with self.cache_lock:item=self.cache.get((addr,field))
        if not item:return None
        if time.time()-item["ts"]>max_age:return None
        return item["value"]

    def _wait(self,addr,field,target,timeout=4,tol=.05):
        end=time.monotonic()+timeout; hit=0; samples=[]; last=None
        while time.monotonic()<end:
            try:v=self.read_field(addr,field,.8)
            except:time.sleep(.1);continue
            samples.append(v);last=v
            if v is not None and abs(v-target)<=tol:
                hit+=1
                if hit>=2:return True,last,samples
            else:hit=0
            time.sleep(.12)
        return False,last,samples

    def _write_float(self,addr,field,value):
        with self.io_lock:
            self.bus.drain();cid,p=encode_set_float(addr,field,value);self.bus.send_frame(cid,p)

    def _write_bool(self,addr,field,commit,value):
        with self.io_lock:
            self.bus.drain();cid,p=encode_set_boolean(addr,field,value);self.bus.send_frame(cid,p)
            time.sleep(.05);cid,p=encode_commit(addr,commit);self.bus.send_frame(cid,p)

    def set_inverter(self,on):
        cur=self.read_field(COMBIMASTER,19); target=1.0 if on else 0.0
        if abs(cur-target)<=.05:return {"changed":False,"value":on}
        self._write_bool(COMBIMASTER,19,20,on);time.sleep(.35)
        ok,v,s=self._wait(COMBIMASTER,19,target)
        if not ok:raise RuntimeError(f"Inverter verify failed: {s}")
        return {"changed":True,"value":v>=.5,"samples":s}

    def set_charger(self,on):
        cur=self.read_field(COMBIMASTER,21); target=1.0 if on else 0.0
        if abs(cur-target)<=.05:return {"changed":False,"value":on}
        self._write_bool(COMBIMASTER,21,22,on);time.sleep(.35)
        ok,v,s=self._wait(COMBIMASTER,21,target)
        if not ok:raise RuntimeError(f"Charger verify failed: {s}")
        return {"changed":True,"value":v>=.5,"samples":s}

    def set_ac_limit(self,amps):
        if isinstance(amps,bool) or int(amps)!=amps or not 3<=int(amps)<=15:
            raise ValueError("AC input limit must be a whole number from 3 to 15 A")
        amps=int(amps)
        cur=self.read_field(COMBIMASTER,23)
        if abs(cur-amps)<=.05:return {"changed":False,"value":cur}
        self._write_float(COMBIMASTER,23,amps);time.sleep(.35)
        ok,v,s=self._wait(COMBIMASTER,23,amps)
        if not ok:raise RuntimeError(f"AC input limit verify failed: {s}")
        return {"changed":True,"value":v,"samples":s}

    def set_ac_support(self,enabled):
        """Set CombiMaster Btm3 field 11, metadata name 'AC IN support'."""
        target=1.0 if enabled else 0.0
        current=self._read_btm3(COMBIMASTER,11)
        self.ac_support_enabled=current
        if abs(float(current)-target)<=.05:
            return {"changed":False,"value":bool(enabled),"raw_value":current}
        with self.io_lock:
            self.bus.drain()
            cid,p=btm3_write_float(COMBIMASTER,11,target)
            self.bus.send_frame(cid,p)
        time.sleep(.35)
        samples=[]
        end=time.monotonic()+4.0
        while time.monotonic()<end:
            try:
                actual=self._read_btm3(COMBIMASTER,11)
                samples.append(actual)
                self.ac_support_enabled=actual
                if abs(float(actual)-target)<=.05:
                    return {
                        "changed":True,"value":bool(enabled),
                        "raw_value":actual,"samples":samples,
                    }
            except Exception:
                pass
            time.sleep(.15)
        raise RuntimeError(f"AC IN support verify failed: {samples}")

    def _force_float(self,name,addr,command_field,commit_field,state_field):
        """Issue an existing Float event command and verify charger state 3."""
        before=self.read_field(addr,state_field,.7)
        try:
            if int(round(float(before)))==3:
                return {"changed":False,"state":before}
        except (TypeError,ValueError):
            pass
        self._write_bool(addr,command_field,commit_field,True)
        time.sleep(.35)
        ok,value,samples=self._wait(addr,state_field,3.0,timeout=5,tol=.05)
        if not ok:
            raise RuntimeError(f"{name} Float verify failed: {samples}")
        return {"changed":True,"state":value,"samples":samples}

    def _force_bulk(self,name,addr,command_field,commit_field,state_field):
        """Issue a schema-verified Bulk event and verify charging state 1."""
        before=self.read_field(addr,state_field,.7)
        try:
            if int(round(float(before)))==1:return {"changed":False,"state":before}
        except (TypeError,ValueError):pass
        self._write_bool(addr,command_field,commit_field,True)
        time.sleep(.35)
        ok,value,samples=self._wait(addr,state_field,1.0,timeout=5,tol=.05)
        if not ok:raise RuntimeError(f"{name} Bulk verify failed: {samples}")
        return {"changed":True,"state":value,"samples":samples}

    def enforce_high_soc_float(self):
        """Keep active house charging sources in Float at the configured SoC."""
        sources=(
            # name, address, Float event/commit, Bulk event/commit, state field
            ("charger_house",COMBIMASTER,42,43,38,39,1),
            ("solar",SOLAR,18,19,14,15,3),
            ("alternator",ALTERNATOR,37,38,33,34,5),
        )
        while not self.stop_event.wait(3.0):
            settings=self.get_settings()
            self.float_policy_status["last_check_at"]=time.time()
            details=None
            try:
                if callable(getattr(self,"house_soc_details_getter",None)):
                    details=self.house_soc_details_getter();bms_soc=details["soc"]
                else:bms_soc=self.house_soc_getter() if callable(getattr(self,"house_soc_getter",None)) else None
            except Exception:
                details=None;bms_soc=None
            # Do not fall back to MasterShunt here: the Dashboard's authoritative
            # House SOC is DALY. Until DALY has produced a valid reading, Float
            # protection waits rather than acting on a conflicting SOC value.
            soc=bms_soc
            soc_source="daly_bms" if bms_soc is not None else "unavailable"
            try:soc_value=None if soc is None else float(soc)
            except (TypeError,ValueError):soc_value=None
            enabled=settings["float_protection_enabled"]
            threshold=settings["house_battery_soc"]
            bulk_threshold=settings["bulk_resume_soc"]
            cell_trigger_mv=settings["float_cell_trigger_mv"]
            cell_resume_mv=settings["float_cell_resume_mv"]
            max_cell_mv=None if details is None else details.get("max_cell_mv")
            # A SOC that is missing or older than the freshness limit (DALY link down) counts as unknown. While Float is
            # latched that holds the sources in Float (never resumes Bulk on old data); it never starts Float by itself.
            # Float also starts on a single cell reaching cell_trigger_mv: three parallel batteries do not necessarily
            # reach a high SOC together, and the pack-average SOC can badly lag one battery's own runaway cell (see
            # CHANGELOG 1.13.0). The cell spread is informational only (float_policy_status) and never forces Float.
            active,resume,held,trigger=float_decision(enabled,self.float_policy_status.get("float_latched"),soc_value,threshold,bulk_threshold,max_cell_mv,cell_trigger_mv,cell_resume_mv)
            if held:soc_source="stale_hold"
            self.float_policy_status["enabled"]=enabled
            self.float_policy_status["threshold_soc"]=threshold
            self.float_policy_status["bulk_resume_soc"]=bulk_threshold
            self.float_policy_status["cell_trigger_mv"]=cell_trigger_mv
            self.float_policy_status["cell_resume_mv"]=cell_resume_mv
            self.float_policy_status["house_soc"]=soc_value
            self.float_policy_status["soc_source"]=soc_source
            self.float_policy_status["soc_held"]=held
            self.float_policy_status["soc_fresh_batteries"]=None if details is None else details.get("fresh_batteries")
            self.float_policy_status["soc_age_seconds"]=None if details is None else details.get("youngest_age_seconds")
            self.float_policy_status["soc_stale"]=bool(details and soc_value is None and details.get("stale_batteries"))
            self.float_policy_status["last_known_soc"]=None if details is None else details.get("last_known_soc")
            self.float_policy_status["max_cell_mv"]=max_cell_mv
            self.float_policy_status["max_cell_battery"]=None if details is None else details.get("max_cell_battery")
            self.float_policy_status["max_spread_mv"]=None if details is None else details.get("max_spread_mv")
            self.float_policy_status["max_spread_battery"]=None if details is None else details.get("max_spread_battery")
            self.float_policy_status["active"]=active
            self.float_policy_status["trigger"]=trigger
            if not enabled:
                self.float_policy_status["sources"]={};self.float_policy_status["float_latched"]=False
            if not active:
                if resume:
                    changed_any=False
                    for name,addr,_float_field,_float_commit,bulk_field,bulk_commit,state_field in sources:
                        raw=self.cached(addr,state_field)
                        try:state=None if raw is None else int(round(float(raw)))
                        except (TypeError,ValueError):state=None
                        item={"state":state,"status":"inactive"}
                        if state==3:
                            try:
                                item["result"]=self._force_bulk(name,addr,bulk_field,bulk_commit,state_field)
                                item["status"]="bulk";changed_any=changed_any or bool(item["result"].get("changed"))
                            except Exception as exc:item["status"]="error";item["error"]=str(exc)
                        self.float_policy_status["sources"][name]=item
                    if changed_any:self.float_policy_status["bulk_event_id"]+=1
                    self.float_policy_status["float_latched"]=False
                continue
            self.float_policy_status["float_latched"]=True
            now=time.monotonic()
            changed_any=False
            for name,addr,command_field,commit_field,_bulk_field,_bulk_commit,state_field in sources:
                raw=self.cached(addr,state_field)
                try:state=None if raw is None else int(round(float(raw)))
                except (TypeError,ValueError):state=None
                item={"state":state,"status":"unavailable"}
                if state==3:
                    item["status"]="float"
                elif state not in (1,2):
                    # Do not turn an unavailable, stopped or night-time source
                    # on. It will be forced to Float as soon as it starts charging.
                    item["status"]="inactive"
                elif now-self.float_policy_last_attempt.get(name,0)<20.0:
                    item["status"]="retry_wait"
                else:
                    self.float_policy_last_attempt[name]=now
                    try:
                        item["result"]=self._force_float(
                            name,addr,command_field,commit_field,state_field
                        )
                        item["status"]="float"
                        changed_any=changed_any or bool(item["result"].get("changed"))
                    except Exception as exc:
                        item["status"]="error"
                        item["error"]=str(exc)
                self.float_policy_status["sources"][name]=item
            if changed_any:
                self.float_policy_status["event_id"]+=1

    def set_operating_mode(self,mode):
        """Apply a named onboard operating mode using verified controls only.

        Engine ECU is deliberately never switched off by a mode. Motor mode
        explicitly switches it on; Anchor and Marina leave its state unchanged.
        Every requested action is attempted and failures are reported together.
        """
        mode=str(mode or "").strip().lower()
        default_ac_limit=self.get_settings()["default_ac_limit"]
        actions={
            "motor":[
                ("inverter OFF",self.set_inverter,False),
                ("Engine ECU ON",lambda enabled:self.set_device_control("engine_ecu",enabled),True),
                ("Solar ON",lambda enabled:self.set_device_control("solar",enabled),True),
                ("Charger Start ON",lambda enabled:self.set_device_control("charger_start",enabled),True),
                ("Charger Bowthruster ON",lambda enabled:self.set_device_control("charger_bow",enabled),True),
                ("Alternator ON",self.set_alternator_enabled,True),
                ("Charger House OFF",self.set_charger,False),
            ],
            "anchor":[
                ("inverter OFF",self.set_inverter,False),
                ("Charger House OFF",self.set_charger,False),
                ("Alternator OFF",self.set_alternator_enabled,False),
                ("Solar ON",lambda enabled:self.set_device_control("solar",enabled),True),
                ("Charger Start OFF",lambda enabled:self.set_device_control("charger_start",enabled),False),
                ("Charger Bowthruster OFF",lambda enabled:self.set_device_control("charger_bow",enabled),False),
            ],
            "marina":[
                ("inverter ON",self.set_inverter,True),
                ("AC IN support ON",self.set_ac_support,True),
                (f"AC limit {default_ac_limit} A",self.set_ac_limit,default_ac_limit),
                ("Alternator OFF",self.set_alternator_enabled,False),
                ("Solar ON",lambda enabled:self.set_device_control("solar",enabled),True),
                ("Charger Start ON",lambda enabled:self.set_device_control("charger_start",enabled),True),
                ("Charger Bowthruster ON",lambda enabled:self.set_device_control("charger_bow",enabled),True),
                ("Charger House ON",self.set_charger,True),
            ],
            "sail":[
                ("inverter OFF",self.set_inverter,False),
                ("Charger House OFF",self.set_charger,False),
                ("Alternator OFF",self.set_alternator_enabled,False),
                ("Solar ON",lambda enabled:self.set_device_control("solar",enabled),True),
                ("Charger Start OFF",lambda enabled:self.set_device_control("charger_start",enabled),False),
                ("Charger Bowthruster OFF",lambda enabled:self.set_device_control("charger_bow",enabled),False),
                ("Engine ECU ON",lambda enabled:self.set_device_control("engine_ecu",enabled),True),
            ],
        }
        if mode not in actions:
            raise ValueError("Unknown mode; use motor, anchor, marina or sail")
        results=[]
        failures=[]
        for label,action,enabled in actions[mode]:
            try:
                results.append({"action":label,"result":action(enabled)})
            except Exception as exc:
                failures.append(f"{label}: {exc}")
        if failures:
            raise RuntimeError(
                f"{mode.title()} mode partially applied; " + "; ".join(failures)
            )
        return {"mode":mode,"ok":True,"actions":results}

    def _discover_map(self,addr,maxidx,role):
        schema=Discovery(self).schema(addr,maxidx)
        def best(unit,words,bad=()):
            ranked=[]
            for f in schema:
                n=f["name"].lower(); u=f["unit"].lower(); score=0
                if u==unit.lower():score+=8
                score+=sum(4 for w in words if w in n)
                score-=sum(7 for w in bad if w in n)
                if score>0:ranked.append((score,-f["index"],f["index"]))
            return max(ranked)[2] if ranked else None

        if role=="alternator":
            return {
                "voltage":best("V",["battery","output","voltage"],["set","limit"]),
                "current":best("A",["charge","alternator","output","battery"],["set","limit","field"]),
                "power":best("W",["power","charge","output"],["set","limit"]),
            }
        if role=="solar":
            return {
                "voltage":best("V",["solar","pv","battery","output","voltage"],["set","limit"]),
                "current":best("A",["solar","pv","charge","output","battery"],["set","limit"]),
                "power":best("W",["solar","pv","power","output"],["set","limit"]),
            }
        if role in ("charger_start","charger_bow"):
            return {
                "voltage":best("V",["battery","output","charge","voltage"],["input","ac","set","limit"]),
                "current":best("A",["battery","output","charge","current"],["input","ac","set","limit"]),
                "power":best("W",["output","charge","power"],["input","ac","set","limit"]),
            }
        return {
            "voltage":best("V",["battery","voltage","supply"],["set","limit"]),
            "current":best("A",["current","ecu","supply"],["set","limit"]),
            "power":best("W",["power","ecu"],["set","limit"]),
        }

    def _load_maps(self):
        try:
            raw=json.loads(self.maps_file.read_text(encoding="utf-8"))
            self.smart_maps={k:{kk:(None if vv is None else int(vv)) for kk,vv in v.items()} for k,v in raw.items()}
            return True
        except:return False

    def _save_maps(self):
        self.maps_file.write_text(json.dumps(self.smart_maps,indent=2),encoding="utf-8")

    def initialize_background(self):
        # v0.17: dashboard measurement fields are fully hard-coded from verified
        # device snapshots. Do not run heuristic schema discovery at startup.
        #
        # Discovery helpers remain available for explicit diagnostics/tools, but
        # they are no longer part of normal web-server measurement startup.
        self.discovery_status="fixed-map"
        threading.Thread(
            target=self._discover_mastershunt_config,
            daemon=True,
            name="mastershunt-config",
        ).start()
        threading.Thread(
            target=self.enforce_high_soc_float,
            daemon=True,
            name="high-soc-float-policy",
        ).start()
        self.refresh_loop()

    def poll_fields(self):
        fields={
            (COMBIMASTER,1),(COMBIMASTER,2),(COMBIMASTER,3),(COMBIMASTER,4),(COMBIMASTER,5),(COMBIMASTER,6),(COMBIMASTER,8),(COMBIMASTER,11),(COMBIMASTER,12),
            (COMBIMASTER,19),(COMBIMASTER,21),(COMBIMASTER,23),(COMBIMASTER,47),(COMBIMASTER,48),(COMBIMASTER,49),(COMBIMASTER,50),(COMBIMASTER,54),
            (HOUSE_SHUNT,0),(HOUSE_SHUNT,1),(HOUSE_SHUNT,2),(HOUSE_SHUNT,3),(HOUSE_SHUNT,5),
            (START_SHUNT,0),(START_SHUNT,1),(START_SHUNT,2),
            (BOW_SHUNT,0),(BOW_SHUNT,1),(BOW_SHUNT,2),

            # Verified Solar ChargeMaster monitoring fields.
            (SOLAR,3),(SOLAR,4),(SOLAR,5),(SOLAR,6),

            # Verified Alpha Pro monitoring fields used for alternator source.
            (ALTERNATOR,5),(ALTERNATOR,6),(ALTERNATOR,8),(ALTERNATOR,11),(ALTERNATOR,12),(ALTERNATOR,14),(ALTERNATOR,32),

            # Verified Mass Charger house-bus INPUT fields.
            (CHARGER_START,1),(CHARGER_START,4),(CHARGER_START,5),(CHARGER_START,6),(CHARGER_START,7),
            (CHARGER_BOW,1),(CHARGER_BOW,4),(CHARGER_BOW,5),(CHARGER_BOW,6),(CHARGER_BOW,7),

            # Verified Yanmar interface DC fields.
            (YANMAR,39),(YANMAR,40),(YANMAR,41),
        }
        # Poll mapped ON/OFF control fields too, so the UI status is immediate.
        for cfg in self.control_maps.values():
            if not cfg:
                continue
            try:
                addr=int(cfg["address"],16) if isinstance(cfg["address"],str) else int(cfg["address"])
                field=int(cfg["field"])
                if cfg.get("protocol","btm1")=="btm1":
                    fields.add((addr,field))
            except Exception:
                pass
        for key,addr in (("house",HOUSE_SHUNT),("start",START_SHUNT),("bow",BOW_SHUNT)):
            cfg=self.mastershunt_config_maps.get(key,{})
            for field_name in ("type_field","capacity_field"):
                try:fields.add((addr,int(cfg[field_name])))
                except (KeyError,TypeError,ValueError):pass

        return list(fields)

    def refresh_loop(self):
        while not self.stop_event.is_set():
            started=time.monotonic()
            for addr,field in self.poll_fields():
                if self.stop_event.is_set():break
                try:self.read_field(addr,field,.40)
                except:pass
                time.sleep(.015)
            try:
                self.ac_support_enabled=self._read_btm3(COMBIMASTER,11,.55)
            except Exception:
                pass
            elapsed=time.monotonic()-started
            if elapsed<.35:self.stop_event.wait(.35-elapsed)

    def smart_cached(self,name,addr,source=False):
        m=self.smart_maps.get(name,{})
        out={k:(self.cached(addr,f) if f is not None else None) for k,f in m.items()}
        v,a,p=out.get("voltage"),out.get("current"),out.get("power")
        if source:
            if a is not None:a=max(0.0,a);out["current"]=a
            if p is None and v is not None and a is not None:p=v*a
            if p is not None:out["power"]=max(0.0,p)
        else:
            if p is None and v is not None and a is not None:out["power"]=v*a
        return out

    def battery_cached(self,addr,label):
        v=self.cached(addr,1);a=self.cached(addr,2)
        return {"label":label,"soc":self.cached(addr,0),"voltage":v,"current":a,"power":None if v is None or a is None else v*a}

    @staticmethod
    def charger_state_label(raw, family="mass"):
        """Display-only decode of the existing MasterBus Charger state field.

        CombiMaster and Mass Charger use different numeric enum values:
          CombiMaster: 0 Off, 1 Bulk, 2 Absorption, 3 Float
          Mass Charger: 2 Bulk, 3 Absorption, 4 Float
        """
        if raw is None:
            return None
        try:
            n=int(round(float(raw)))
        except (TypeError,ValueError):
            return None
        if family == "combi":
            return {0:"Off",1:"Bulk",2:"Absorption",3:"Float"}.get(n, f"Unknown ({n})")
        return {0:"Off",2:"Bulk",3:"Absorption",4:"Float",5:"Constant voltage"}.get(n, f"Unknown ({n})")

    @staticmethod
    def source_charge_state_label(raw):
        """Decode the actual source charger-state enum.

        Used for:
          SCM Solar field 3  = Charge state
          Alpha Pro field 5  = Charger state

        These devices use the standard charger-state sequence:
          0 = Off/Standby
          1 = Bulk
          2 = Absorption
          3 = Float

        Any other raw value is shown explicitly rather than inferred.
        """
        if raw is None:
            return None
        try:
            n=int(round(float(raw)))
        except (TypeError,ValueError):
            return None
        return {0:"Off",1:"Bulk",2:"Absorption",3:"Float"}.get(n, f"Unknown ({n})")

    @staticmethod
    def alternator_charge_state_label(raw):
        """Decode Alpha Pro field 5. Hardware verified: 5 = Stopped."""
        if raw is None:
            return None
        try:
            n=int(round(float(raw)))
        except (TypeError,ValueError):
            return None
        return {0:"Off",1:"Bulk",2:"Absorption",3:"Float",5:"Stopped"}.get(n, f"Unknown ({n})")

    def energy(self):
        house=self.battery_cached(HOUSE_SHUNT,"House Battery");house["temperature"]=self.cached(HOUSE_SHUNT,5)
        start=self.battery_cached(START_SHUNT,"Start Battery");bow=self.battery_cached(BOW_SHUNT,"Bow Battery")
        house.update(self.mastershunt_config("house",HOUSE_SHUNT,"LiFePO4"))
        start.update(self.mastershunt_config("start",START_SHUNT,"AGM"))
        bow.update(self.mastershunt_config("bow",BOW_SHUNT,"AGM"))

        # ---- Shore Power (verified CombiMaster AC input) ----
        sv,sa=self.cached(COMBIMASTER,2),self.cached(COMBIMASTER,3)
        shore_frequency=self.cached(COMBIMASTER,4)
        if sa is not None:sa=max(0.0,sa)
        shore={
            "label":"Shore Power","kind":"AC","voltage":sv,"current":sa,
            "power":None if sv is None or sa is None else max(0.0,sv*sa),
            "input_frequency":shore_frequency,
            "connected":bool(sv is not None and float(sv)!=0.0),
        }

        # ---- Charger House (verified CombiMaster DC battery side) ----
        chv,cha_raw=self.cached(COMBIMASTER,11),self.cached(COMBIMASTER,12)
        cha=cha_raw
        if cha is not None:cha=max(0.0,cha)
        charger_house={
            "label":"Charger House","kind":"DC","voltage":chv,"current":cha,
            "power":None if chv is None or cha is None else max(0.0,chv*cha),
            "charge_state":self.charger_state_label(self.cached(COMBIMASTER,1), "combi"),
            "charge_state_raw":self.cached(COMBIMASTER,1),
        }

        # CombiMaster actual operating statuses. The configured inverter switch
        # alone does not make the inverter a DC load: only live Inverting or
        # Supporting status does.
        inverting_raw=self.cached(COMBIMASTER,47)
        supporting_raw=self.cached(COMBIMASTER,49)
        inverting_actual=bool(inverting_raw is not None and float(inverting_raw)>=.5)
        supporting_actual=bool(supporting_raw is not None and float(supporting_raw)>=.5)
        inverter_actual=inverting_actual or supporting_actual
        inverter_v=chv if inverter_actual and chv is not None else 0.0
        inverter_a=(
            abs(float(cha_raw))
            if inverter_actual and cha_raw is not None
            else 0.0
        )
        inverter_w=max(0.0,float(inverter_v)*inverter_a)
        inverter_load={
            "label":"Inverter","kind":"DC",
            "voltage":inverter_v,"current":inverter_a,"power":inverter_w,
            "enabled":bool(
                self.cached(COMBIMASTER,19) is not None
                and float(self.cached(COMBIMASTER,19))>=.5
            ),
            "inverting":inverting_actual,"supporting":supporting_actual,
            "actual":inverter_actual,"dc_current_raw":cha_raw,
        }

        # ---- Solar (verified snapshot fields) ----
        # Field 4 = Solar/PV panel voltage (diagnostic)
        # Field 5 = Charge current
        # Field 6 = Battery/output voltage (display)
        # Output watts = field 6 * field 5
        solar_pv_v=self.cached(SOLAR,4)
        solar_a=self.cached(SOLAR,5)
        solar_batt_v=self.cached(SOLAR,6)
        if solar_a is not None:solar_a=max(0.0,solar_a)
        solar_w=None if solar_batt_v is None or solar_a is None else max(0.0,solar_batt_v*solar_a)
        # Actual controller charge state from SCM Solar field 3.
        solar_state_raw=self.cached(SOLAR,3)
        solar_state=self.source_charge_state_label(solar_state_raw)
        solar={
            "label":"Solar","kind":"DC",
            "voltage":solar_batt_v,
            "panel_voltage":solar_pv_v,
            "current":solar_a,
            "power":solar_w,
            "output_voltage":solar_batt_v,
            "state":solar_state,
            "state_raw":solar_state_raw,
        }

        # ---- Loads measured independently ----
        #
        # Mass Chargers are loads on the 12 V house bus, therefore use their
        # INPUT fields, not their battery/output fields:
        #   field 4 = Input voltage
        #   field 5 = Input current
        def charger_input(addr,label):
            v=self.cached(addr,4)
            a=self.cached(addr,5)
            if a is not None:a=max(0.0,float(a))
            p=None if v is None or a is None else max(0.0,float(v)*a)
            raw_state=self.cached(addr,1)
            return {
                "label":label,"kind":"DC","voltage":v,"current":a,"power":p,
                "charge_state":self.charger_state_label(raw_state),
                "charge_state_raw":raw_state,
            }

        cs=charger_input(CHARGER_START,"Charger Start")
        cb=charger_input(CHARGER_BOW,"Charger Bow")

        # Yanmar interface:
        #   field 39 = DC Input voltage
        #   field 40 = DC Output voltage
        #   field 41 = DC Output current
        #
        # There is no direct input-current field. Estimate input current using
        # DC/DC efficiency. 85% is intentionally conservative at this very low
        # load, where conversion + interface overhead can be significant.
        ECU_EFFICIENCY=0.85
        ecu_in_v=self.cached(YANMAR,39)
        ecu_out_v=self.cached(YANMAR,40)
        ecu_out_a=self.cached(YANMAR,41)
        if ecu_out_a is not None:ecu_out_a=max(0.0,float(ecu_out_a))

        ecu_out_w=(
            None
            if ecu_out_v is None or ecu_out_a is None
            else max(0.0,float(ecu_out_v)*ecu_out_a)
        )
        ecu_in_w=(
            None
            if ecu_out_w is None
            else ecu_out_w/ECU_EFFICIENCY
        )
        ecu_in_a=(
            None
            if ecu_in_w is None or ecu_in_v in (None,0)
            else ecu_in_w/float(ecu_in_v)
        )

        ecu={
            "label":"Engine ECU power",
            "kind":"DC",
            "voltage":ecu_in_v,
            "current":ecu_in_a,
            "power":ecu_in_w,
            "output_voltage":ecu_out_v,
            "output_current":ecu_out_a,
            "output_power":ecu_out_w,
            "efficiency_estimate":ECU_EFFICIENCY,
        }

        # AC house loads.
        acv,acw=self.cached(COMBIMASTER,5),self.cached(COMBIMASTER,8)
        ac_frequency=self.cached(COMBIMASTER,6)
        if acw is not None:acw=max(0.0,acw)
        aca=None if acv in (None,0) or acw is None else acw/acv
        ac={
            "label":"AC Loads","kind":"AC","voltage":acv,"current":aca,
            "power":acw,"output_frequency":ac_frequency,
        }

        # ---- Alternator / Other DC: solve from the house-bus balance ----
        #
        # MasterShunt House convention:
        #   + current = charging INTO house battery
        #   - current = discharging FROM house battery
        #
        # The measured House MasterShunt current already reflects Start charger,
        # Bow charger, ECU and all other loads connected to the house battery bus.
        # Therefore those loads must NOT be added to the shunt current when
        # learning total house load. They are subtracted only to derive the
        # residual value displayed in the "Other DC Loads" tile.
        #
        # Alternator stopped:
        #   I_total_house_load = I_charger_house + I_solar - I_house_battery
        #   I_other_dc_display = I_total_house_load
        #                        - I_start - I_bow - I_ecu
        #
        # Alternator running:
        #   I_alternator = I_house_battery + I_total_house_load_baseline
        #                  - I_charger_house - I_solar
        #   I_other_dc_display = I_total_house_load_baseline
        #                        - I_start - I_bow - I_ecu
        alt_v=self.cached(ALTERNATOR,14)
        alt_shaft=self.cached(ALTERNATOR,11)
        engine_shaft=self.cached(ALTERNATOR,12)
        alt_temp=self.cached(ALTERNATOR,32)

        # Use the Alpha Pro's actual charge state to decide whether it is
        # electrically supplying the house bus. Shaft rotation only tells us
        # that the engine/alternator is turning; after Stop Charge the shafts
        # still turn while alternator output is zero.
        alt_state_raw=self.cached(ALTERNATOR,5)
        alt_state=self.alternator_charge_state_label(alt_state_raw)
        try:
            alt_state_n=None if alt_state_raw is None else int(round(float(alt_state_raw)))
        except (TypeError,ValueError):
            alt_state_n=None
        charging=alt_state_n in (1,2,3)
        stopped=alt_state_n in (0,5)

        shaft_running=False
        if alt_shaft is not None and alt_shaft>0.5:shaft_running=True
        if engine_shaft is not None and engine_shaft>0.5:shaft_running=True

        def amps(x):
            v=x.get("current")
            return 0.0 if v is None else max(0.0,float(v))

        # Alpha Pro field/excitation circuit is a separately displayed house-bus
        # load in v1.0.7. Subtract it from Other DC Loads so totals do not count
        # the same physical load twice.
        alt_load_v=self.cached(ALTERNATOR,6)
        alt_field_a=self.cached(ALTERNATOR,8)
        if alt_field_a is not None:alt_field_a=max(0.0,float(alt_field_a))
        alt_field_w=(
            None
            if alt_load_v is None or alt_field_a is None
            else max(0.0,float(alt_load_v)*alt_field_a)
        )

        known_load_a=(
            amps(cs)+amps(cb)+amps(ecu)+amps(inverter_load)
            +(0.0 if alt_field_a is None else alt_field_a)
        )
        batt_a=house.get("current")
        charger_house_a=0.0 if cha is None else max(0.0,float(cha))
        solar_source_a=0.0 if solar_a is None else max(0.0,float(solar_a))

        observed_total_house_load_a=None
        if stopped and batt_a is not None:
            observed_total_house_load_a=max(
                0.0,
                charger_house_a + solar_source_a - float(batt_a)
            )

            # Low-pass filter the independently solvable total load baseline.
            alpha=0.25
            if self.total_house_load_baseline_a is None:
                self.total_house_load_baseline_a=observed_total_house_load_a
            else:
                self.total_house_load_baseline_a=(
                    (1.0-alpha)*self.total_house_load_baseline_a
                    + alpha*observed_total_house_load_a
                )
            self.total_house_load_baseline_ts=time.time()

        total_house_load_a=(
            observed_total_house_load_a
            if stopped and observed_total_house_load_a is not None
            else self.total_house_load_baseline_a
        )

        other_a=None
        if total_house_load_a is not None:
            other_a=max(0.0, float(total_house_load_a) - known_load_a)

        alt_a=0.0
        alt_estimated=False
        if charging and batt_a is not None and total_house_load_a is not None:
            alt_a=max(
                0.0,
                float(batt_a) + float(total_house_load_a)
                - charger_house_a - solar_source_a
            )
            alt_estimated=True
        elif stopped:
            alt_a=0.0
            alt_estimated=False

        alt_w=None if alt_v is None else max(0.0,float(alt_v)*alt_a)
        alternator={
            "label":"Alternator","kind":"DC",
            "voltage":alt_v,
            "current":alt_a,
            "power":alt_w,
            "running":charging,
            "shaft_running":shaft_running,
            "estimated":alt_estimated,
            "alternator_shaft":alt_shaft,
            "engine_shaft":engine_shaft,
            "temperature":alt_temp,
            "state":alt_state,
            "control_on":(alt_state_raw is not None and int(round(float(alt_state_raw))) in (1,2,3)),
            "state_raw":alt_state_raw,
        }

        # Alpha Pro regulator electrical load, requested as a separate load:
        #   field 6 = Battery voltage
        #   field 8 = Field/excitation current
        # This is deliberately separate from the calculated alternator output
        # current shown in Sources.
        alternator_load={
            "label":"Alternator","kind":"DC",
            "voltage":alt_load_v,
            "current":alt_field_a,
            "power":alt_field_w,
            "control_on":charging,
            "state":alt_state,
            "state_raw":alt_state_raw,
        }

        sources={
            "shore":shore,
            "charger_house":charger_house,
            "alternator":alternator,
            "solar":solar,
        }

        # Other DC loads shown from the independently learned/observed baseline.
        otherv=house.get("voltage")
        otherp=None if other_a is None or otherv is None else max(0.0,float(otherv)*other_a)
        other={
            "label":"Other DC Loads","kind":"DC",
            "voltage":otherv,
            "current":other_a,
            "power":otherp,
            "estimated":charging,
        }

        loads={
            "house_ac":ac,
            "inverter":inverter_load,
            "charger_start":cs,
            "charger_bow":cb,
            "engine_ecu":ecu,
            "alternator_field":alternator_load,
            "other_dc":other,
        }

        def wp(x):
            return max(0.0,x.get("power") or 0.0)

        total_dc_input=wp(charger_house)+wp(alternator)+wp(solar)
        # The section header is explicitly Total DC load. AC Loads remains
        # visible, but its AC watts must not enter this total.
        total_output=sum(wp(x) for x in loads.values() if x.get("kind")=="DC")

        return {
            "sources":sources,
            "storage":{"house":house,"start":start,"bow":bow},
            "consumers":loads,
            "totals":{
                "dc_input_power":total_dc_input,
                "consumer_power":total_output,
                "ac_consumer_power":wp(ac),
            },
            "alternator_balance":{
                "running":charging,
                "shaft_running":shaft_running,
                "charge_state_raw":alt_state_raw,
                "charge_state":alt_state,
                "total_house_load_baseline_a":self.total_house_load_baseline_a,
                "total_house_load_observed_a":observed_total_house_load_a,
                "other_dc_display_a":other_a,
                "known_dc_load_a":known_load_a,
                "house_battery_a":batt_a,
                "charger_house_a":charger_house_a,
                "solar_a":solar_source_a,
                "alternator_a":alt_a,
            },
            "settings":self.get_settings(),
            "high_soc_float_policy":dict(self.float_policy_status),
            "combimaster":{
                "inverter_enabled":self.cached(COMBIMASTER,19),
                "charger_enabled":self.cached(COMBIMASTER,21),
                "ac_input_limit":self.cached(COMBIMASTER,23),
                "ac_support_enabled":self.ac_support_enabled,
                "inverting":inverting_raw,
                "charging":self.cached(COMBIMASTER,48),
                "supporting":supporting_raw,
                "ac_input_present":self.cached(COMBIMASTER,50),
                "alarms":self.cached(COMBIMASTER,54),
            },
            "discovery_status":self.discovery_status,
            "cache_items":len(self.cache),
            "controls":self.control_capabilities(),
            "usb":{
                "product":(self.bus.device_info or {}).get("product_string"),
                "serial":(self.bus.device_info or {}).get("serial_number"),
            },
        }
