from contextlib import asynccontextmanager
from pathlib import Path
import asyncio, threading
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse,StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from masterbus_service import MasterBusService
from daly_bms_service import DalyBmsService
from daly_balancer_service import DalyBalancerService
from history_service import HistoryService
from bluetooth_coordinator import bluetooth_coordinator
from report_service import BatteryHealthReports

BASE=Path(__file__).resolve().parent
STATIC=BASE/"static"
CERTS=BASE/"certs"
service=MasterBusService()
bms_service=DalyBmsService()
balancer_service=DalyBalancerService()
history_service=HistoryService(BASE/"data"/"history.sqlite3")
battery_reports=BatteryHealthReports(BASE)
history_stop=threading.Event()

def _history_loop():
    """Log dashboard every 10 seconds and each new Bluetooth measurement once."""
    last_bms={};last_balancers={}
    while not history_stop.wait(10):
        try:
            energy_data=service.energy();soc=available_house_bms_soc()
            if "house" in energy_data.get("storage",{}):energy_data["storage"]["house"]["soc"]=soc
            history_service.record("dashboard",energy_data)
        except Exception:pass
        try:history_service.prune(service.get_settings()["history_retention_days"])
        except Exception:pass
        try:
            for name,value in bms_service.snapshot().get("batteries",{}).items():
                stamp=value.get("captured_at")
                if stamp and stamp!=last_bms.get(name):history_service.record("bms",value,name,stamp);last_bms[name]=stamp
        except Exception:pass
        try:
            for name,value in balancer_service.snapshot().get("devices",{}).items():
                stamp=value.get("captured_at")
                if stamp and stamp!=last_balancers.get(name):
                    history_service.record("balancer",{"state":value.get("state"),"status":value.get("status",{}),"error":value.get("error")},name,stamp);last_balancers[name]=stamp
        except Exception:pass

# Float protection and Dashboard use the same authoritative DALY House SOC.
# Protection waits until the first valid DALY reading instead of acting on a
# conflicting MasterShunt value during Bluetooth startup.
def available_house_bms_soc():
    """Average every valid DALY SOC; the three BMSes form one house bank."""
    batteries=bms_service.snapshot().get("batteries",{})
    values=[]
    for name in ("BATTERY 1","BATTERY 2","BATTERY 3"):
        value=batteries.get(name,{}).get("state_of_charge_percent")
        try:value=float(value)
        except (TypeError,ValueError):continue
        if 0<=value<=100:values.append(value)
    return sum(values)/len(values) if values else None

service.house_soc_getter=available_house_bms_soc


def _is_benign_client_disconnect(exc):
    """
    iOS/Safari/PWA may reset an HTTP socket when the app is closed.
    On Windows' Proactor event loop this can surface as WinError 10054
    during transport cleanup. It is harmless and should not pollute logs.
    """
    if isinstance(exc, ConnectionResetError):
        winerror = getattr(exc, "winerror", None)
        errno = getattr(exc, "errno", None)

        if winerror == 10054:
            return True

        # Windows can expose WSAECONNRESET as errno 10054 as well.
        if errno == 10054:
            return True

        if "10054" in str(exc):
            return True

    return False


def _install_asyncio_exception_filter():
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handler(loop, context):
        exc = context.get("exception")

        if _is_benign_client_disconnect(exc):
            return

        # Preserve normal asyncio/Uvicorn error handling for everything else.
        if previous_handler is not None:
            previous_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)

@asynccontextmanager
async def lifespan(app):
    _install_asyncio_exception_filter()

    try:
        await asyncio.to_thread(service.open)
        threading.Thread(target=service.initialize_background,daemon=True,name="masterbus-cache").start()
    except Exception as e:
        print("WARNING:",e)
    try:
        bms_service.start(service.get_settings)
    except Exception as e:
        print("BMS WARNING:",e)
    try:balancer_service.start(service.get_settings)
    except Exception as e:print("BALANCER WARNING:",e)
    history_stop.clear();history_thread=threading.Thread(target=_history_loop,daemon=True,name="measurement-history");history_thread.start()
    threading.Thread(target=history_service.refresh_chart_cache,daemon=True,name="history-chart-cache").start()
    yield
    history_stop.set();history_thread.join(timeout=3)
    try: await asyncio.to_thread(service.close)
    except: pass
    try: await asyncio.to_thread(bms_service.stop)
    except: pass
    try: await asyncio.to_thread(balancer_service.stop)
    except: pass

app=FastAPI(title="Mastervolt Energy",version="1.10.0",lifespan=lifespan)
app.add_middleware(GZipMiddleware,minimum_size=1000)
app.mount("/static",StaticFiles(directory=STATIC),name="static")

class BoolReq(BaseModel): enabled:bool
class LimitReq(BaseModel): amps:int=Field(ge=3,le=15)
class ModeReq(BaseModel): mode:str
class SettingsReq(BaseModel):
    default_ac_limit:int=Field(ge=3,le=15)
    float_protection_enabled:bool
    house_battery_soc:float=Field(ge=50,le=100)
    bulk_resume_soc:float=Field(ge=0,le=95)
    warning_popup_seconds:int=Field(ge=1,le=60)
    bms_refresh_interval:int=Field(ge=5,le=300)
    bms_popup_seconds:int=Field(ge=1,le=60)
    bms_connection_retry_seconds:int=Field(ge=1,le=300)
    balancer_refresh_interval:int=Field(ge=5,le=300)
    balancer_connection_retry_seconds:int=Field(ge=5,le=300)
    history_retention_days:int=Field(ge=1,le=365)

@app.get("/")
def root(): return FileResponse(STATIC/"index.html")

@app.get("/manifest.webmanifest")
def manifest(): return FileResponse(STATIC/"manifest.webmanifest",media_type="application/manifest+json")

@app.get("/service-worker.js")
def sw(): return FileResponse(STATIC/"service-worker.js",media_type="application/javascript")

@app.get("/local-ca.cer")
def local_ca():
    certificate=CERTS/"Mastervolt-Local-CA.cer"
    if not certificate.exists():
        raise HTTPException(404,detail="Local CA certificate has not been generated")
    return FileResponse(
        certificate,media_type="application/pkix-cert",
        filename="Mastervolt-Local-CA.cer",
    )

@app.get("/api/energy")
async def energy():
    try:
        data=service.energy()
        soc=available_house_bms_soc()
        if "house" in data.get("storage",{}):
            data["storage"]["house"]["soc"]=soc
            data["storage"]["house"]["soc_source"]="daly_bms_average" if soc is not None else "daly_bms_unavailable"
        return data
    except Exception as e:raise HTTPException(503,detail=str(e))

@app.get("/api/settings")
def settings(): return service.get_settings()

@app.post("/api/settings")
def save_settings(req:SettingsReq):
    try:return service.update_settings(req.model_dump())
    except ValueError as e:raise HTTPException(400,detail=str(e))
    except Exception as e:raise HTTPException(500,detail=str(e))

@app.get("/api/bms")
def bms(): return bms_service.snapshot()

@app.get("/api/balancers")
def balancers(): return balancer_service.snapshot()

@app.get("/api/bluetooth-coordinator")
def bluetooth_status():return bluetooth_coordinator.snapshot()

@app.get("/api/history")
def history(limit:int=100):return {"count":history_service.count(),"retention_days":service.get_settings()["history_retention_days"],"records":history_service.latest(limit)}

@app.get("/api/history/bms-series")
def history_bms_series(after_id:int=0):
    result=history_service.bms_series(after_id)
    result["retention_days"]=service.get_settings()["history_retention_days"]
    return result

@app.get("/api/history/dashboard-series")
def history_dashboard_series(after_id:int=0):
    result=history_service.dashboard_series(after_id)
    result["retention_days"]=service.get_settings()["history_retention_days"]
    return result

def _chart_data(hours:float|None,refresh:bool):
    maximum=float(service.get_settings()["history_retention_days"]*24)
    zoom=maximum if hours is None else float(hours)
    if zoom<.25 or zoom>maximum:raise HTTPException(400,detail=f"Zoom must be between 0.25 and {maximum:g} hours")
    result=history_service.chart_data(zoom,refresh)
    result.update({"zoom_hours":zoom,"max_hours":maximum,"server_cached":True})
    return result

@app.get("/api/history/chart-data")
def history_chart_data(hours:float|None=None):return _chart_data(hours,False)

@app.post("/api/history/chart-data/update")
def update_history_chart_data(hours:float|None=None):return _chart_data(hours,True)

@app.get("/api/history/export")
def history_export():return StreamingResponse(history_service.json_lines(),media_type="application/x-ndjson",headers={"Content-Disposition":"attachment; filename=mastervolt-history.jsonl"})

class ReportReq(BaseModel): days:int=Field(ge=1,le=365)

@app.post("/api/reports/battery-health")
def start_battery_report(req:ReportReq):
    try:return battery_reports.start(req.days)
    except RuntimeError as e:raise HTTPException(409,detail=str(e))

@app.get("/api/reports/battery-health")
def battery_report_status(): return battery_reports.status()

@app.post("/api/balancers/refresh")
def refresh_balancers(): return balancer_service.refresh_all()

@app.post("/api/balancers/{balancer_id}/refresh")
def refresh_one_balancer(balancer_id:int):
    if balancer_id not in (1,2,3):raise HTTPException(404,detail="Unknown balancer")
    try:return balancer_service.refresh_one(f"DL-BAL{balancer_id}")
    except Exception as e:raise HTTPException(503,detail=str(e).strip() or "Balancer refresh unavailable")

@app.post("/api/bms/refresh")
def refresh_bms(): return bms_service.refresh_all()

@app.post("/api/bms/{battery_id}/refresh")
def refresh_one_bms(battery_id:int): return bms_service.refresh_one(_bms_name(battery_id))

def _bms_name(battery_id:int):
    if battery_id not in (1,2,3):raise HTTPException(404,detail="Unknown battery")
    return f"BATTERY {battery_id}"

def _bms_error(exc:Exception):
    return str(exc).strip() or f"{type(exc).__name__} during Bluetooth control"

@app.post("/api/bms/{battery_id}/set-soc-100")
async def bms_set_soc_100(battery_id:int):
    try:return await asyncio.to_thread(bms_service.control,_bms_name(battery_id),"set_soc_100")
    except ValueError as e:raise HTTPException(400,detail=str(e))
    except Exception as e:raise HTTPException(503,detail=_bms_error(e))

@app.post("/api/bms/{battery_id}/set-soc-{mode}")
async def bms_set_soc_curve(battery_id:int,mode:str):
    action={"charge":"set_soc_charge","discharge":"set_soc_discharge"}.get(mode)
    if not action:raise HTTPException(404,detail="Unknown SOC curve")
    try:return await asyncio.to_thread(bms_service.control,_bms_name(battery_id),action)
    except ValueError as e:raise HTTPException(400,detail=str(e))
    except Exception as e:raise HTTPException(503,detail=_bms_error(e))

@app.post("/api/bms/set-all-soc/{mode}")
async def bms_set_all_soc(mode:str):
    action={"100":"set_soc_100","charge":"set_soc_charge","discharge":"set_soc_discharge"}.get(mode)
    if not action:raise HTTPException(404,detail="Unknown SOC mode")
    try:return await asyncio.to_thread(bms_service.control_all,action)
    except ValueError as e:raise HTTPException(400,detail=str(e))
    except Exception as e:raise HTTPException(503,detail=_bms_error(e))

@app.post("/api/bms/set-all/{action}")
async def bms_set_all(action:str):
    mapped={"charge-on":"charge_on","charge-off":"charge_off","discharge-on":"discharge_on","discharge-off":"discharge_off"}.get(action)
    if not mapped:raise HTTPException(404,detail="Unknown all-battery action")
    try:return await asyncio.to_thread(bms_service.control_all,mapped)
    except ValueError as e:raise HTTPException(400,detail=str(e))
    except Exception as e:raise HTTPException(503,detail=_bms_error(e))

@app.post("/api/bms/{battery_id}/charge")
async def bms_charge(battery_id:int,req:BoolReq):
    try:return await asyncio.to_thread(bms_service.control,_bms_name(battery_id),"charge",req.enabled)
    except ValueError as e:raise HTTPException(400,detail=str(e))
    except Exception as e:raise HTTPException(503,detail=_bms_error(e))

@app.post("/api/bms/{battery_id}/discharge")
async def bms_discharge(battery_id:int,req:BoolReq):
    try:return await asyncio.to_thread(bms_service.control,_bms_name(battery_id),"discharge",req.enabled)
    except ValueError as e:raise HTTPException(400,detail=str(e))
    except Exception as e:raise HTTPException(503,detail=_bms_error(e))

@app.post("/api/control/inverter")
async def inverter(req:BoolReq):
    try:return await asyncio.to_thread(service.set_inverter,req.enabled)
    except Exception as e:raise HTTPException(500,detail=str(e))

@app.post("/api/control/charger")
async def charger(req:BoolReq):
    try:return await asyncio.to_thread(service.set_charger,req.enabled)
    except Exception as e:raise HTTPException(500,detail=str(e))

@app.post("/api/control/ac-limit")
async def limit(req:LimitReq):
    try:return await asyncio.to_thread(service.set_ac_limit,req.amps)
    except ValueError as e:raise HTTPException(400,detail=str(e))
    except Exception as e:raise HTTPException(500,detail=str(e))

@app.post("/api/control/ac-support")
async def ac_support(req:BoolReq):
    try:return await asyncio.to_thread(service.set_ac_support,req.enabled)
    except Exception as e:raise HTTPException(409,detail=str(e))

@app.post("/api/control/mode")
async def operating_mode(req:ModeReq):
    try:return await asyncio.to_thread(service.set_operating_mode,req.mode)
    except ValueError as e:raise HTTPException(400,detail=str(e))
    except Exception as e:raise HTTPException(409,detail=str(e))


@app.post("/api/control/device/{name}")
async def device_control(name: str, req: BoolReq):
    try:
        return await asyncio.to_thread(service.set_device_control, name, req.enabled)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(409, detail=str(e))


@app.post("/api/control/alternator")
async def alternator_control(req: BoolReq):
    try:
        return await asyncio.to_thread(service.set_alternator_enabled, req.enabled)
    except Exception as e:
        raise HTTPException(409, detail=str(e))
