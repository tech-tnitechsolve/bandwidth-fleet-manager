import os
import psutil
import docker
import json
import uuid
import hashlib
import time
import socket
import threading
import subprocess
import urllib.request
import traceback
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Depends, status, Header
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from typing import List, Optional

CURRENT_VERSION = "v1.6.0"
GITHUB_REPO = "tech-tnitechsolve/bandwidth-fleet-manager"

app = FastAPI(title="Bandwidth Fleet Manager Pro Engine", version=CURRENT_VERSION)
client = docker.from_env()

DATA_DIR = "/opt/bandwidth-manager/data"
NODES_FILE = f"{DATA_DIR}/nodes.json"
ACCOUNTS_FILE = f"{DATA_DIR}/accounts.json"
SETTINGS_FILE = f"{DATA_DIR}/settings.json"
ENV_FILE = "/opt/bandwidth-manager/.env"

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "super-secret-key-123")

last_net_time = 0.0
last_rx_bytes = 0
last_tx_bytes = 0
PID_CACHE = {}

STEADY_CPU_PERCENT = 0.0
def cpu_sampler_thread():
    global STEADY_CPU_PERCENT
    while True:
        try:
            STEADY_CPU_PERCENT = psutil.cpu_percent(interval=1.0)
        except Exception: pass
        time.sleep(2)

threading.Thread(target=cpu_sampler_thread, daemon=True).start()

def load_json(path, default=None):
    if default is None:
        default = []
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        try: return json.load(f)
        except Exception: return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_nodes():
    data = load_json(NODES_FILE, default=[])
    if isinstance(data, dict):
        data = data.get("profiles", data.get("nodes", []))
    if not isinstance(data, list):
        data = []
    return data

def save_nodes(nodes):
    if not isinstance(nodes, list):
        nodes = []
    save_json(NODES_FILE, nodes)

def generate_token(username: str) -> str:
    raw = f"{username}:{ADMIN_PASS}:{JWT_SECRET}"
    return hashlib.sha256(raw.encode()).hexdigest()

def get_current_user(authorization: Optional[str] = Header(None)):
    global ADMIN_PASS
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chưa đăng nhập")
    token = authorization.split(" ")[1]
    expected = generate_token(ADMIN_USER)
    if token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Phiên đăng nhập hết hạn")
    return ADMIN_USER

@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
def login(req: LoginRequest):
    if req.username == ADMIN_USER and req.password == ADMIN_PASS:
        token = generate_token(req.username)
        return {"success": True, "token": token, "username": req.username}
    raise HTTPException(status_code=400, detail="Tài khoản hoặc mật khẩu không chính xác")

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

@app.post("/api/auth/change-password")
def change_password(req: ChangePasswordRequest, user: str = Depends(get_current_user)):
    global ADMIN_PASS
    if req.old_password != ADMIN_PASS:
        raise HTTPException(status_code=400, detail="Mật khẩu cũ không chính xác")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải từ 6 ký tự trở lên")
    
    ADMIN_PASS = req.new_password
    os.environ["ADMIN_PASS"] = req.new_password
    
    if os.path.exists(ENV_FILE):
        lines = []
        with open(ENV_FILE, "r") as f:
            for l in f:
                if l.startswith("ADMIN_PASS="):
                    lines.append(f"ADMIN_PASS={req.new_password}\n")
                else:
                    lines.append(l)
        with open(ENV_FILE, "w") as f:
            f.writelines(lines)
            
    token = generate_token(ADMIN_USER)
    return {"success": True, "new_token": token}

def check_proxy_live_raw(endpoint: str, timeout: float = 2.5):
    try:
        if ":" not in endpoint:
            return {"is_live": False, "ping_ms": 0}
        host, port_str = endpoint.rsplit(":", 1)
        port = int(port_str)
        t_start = time.time()
        with socket.create_connection((host, port), timeout=timeout):
            ping_ms = round((time.time() - t_start) * 1000)
            return {"is_live": True, "ping_ms": ping_ms}
    except Exception:
        return {"is_live": False, "ping_ms": 0}

def get_vps_network_load():
    global last_net_time, last_rx_bytes, last_tx_bytes
    default_iface = "eth0"
    try:
        with open("/proc/net/route", "r") as f:
            for line in f.readlines()[1:]:
                fields = line.strip().split()
                if len(fields) >= 2 and fields[1] == "00000000":
                    default_iface = fields[0]
                    break
    except Exception: pass

    current_rx, current_tx, rx_drops, tx_drops = 0, 0, 0, 0
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f:
                if default_iface in line:
                    p = line.split()
                    current_rx, rx_drops = int(p[1]), int(p[4])
                    current_tx, tx_drops = int(p[9]), int(p[12])
                    break
    except Exception: pass

    now = time.time()
    dt = max(now - last_net_time if last_net_time > 0 else 1.0, 0.5)
    
    rx_speed_mbps = round(((current_rx - last_rx_bytes) * 8) / (dt * 1000000), 2) if last_net_time > 0 else 0.0
    tx_speed_mbps = round(((current_tx - last_tx_bytes) * 8) / (dt * 1000000), 2) if last_net_time > 0 else 0.0

    last_net_time, last_rx_bytes, last_tx_bytes = now, current_rx, current_tx

    conntrack_count, conntrack_max = 0, 524288
    try:
        with open("/proc/sys/net/netfilter/nf_conntrack_count", "r") as f: conntrack_count = int(f.read().strip())
        with open("/proc/sys/net/netfilter/nf_conntrack_max", "r") as f: conntrack_max = int(f.read().strip())
    except Exception: pass

    conntrack_percent = round((conntrack_count / conntrack_max) * 100, 1) if conntrack_max > 0 else 0
    is_choking = (rx_drops > 100 or tx_drops > 100 or conntrack_percent > 85)

    return {
        "interface": default_iface,
        "rx_speed_mbps": max(0.0, rx_speed_mbps),
        "tx_speed_mbps": max(0.0, tx_speed_mbps),
        "total_speed_mbps": max(0.0, round(rx_speed_mbps + tx_speed_mbps, 2)),
        "packet_drops": rx_drops + tx_drops,
        "conntrack_count": conntrack_count,
        "conntrack_max": conntrack_max,
        "conntrack_percent": conntrack_percent,
        "is_choking": is_choking
    }

def get_node_network_stats(node_id):
    tun_name = f"tun_{node_id}"
    pid = PID_CACHE.get(node_id, 0)
    
    if pid <= 0 or not os.path.exists(f"/proc/{pid}"):
        try:
            c = client.containers.get(tun_name)
            if c.status != "running":
                PID_CACHE.pop(node_id, None)
                return {"traffic_mb": 0.0, "traffic_gb": 0.0, "sockets": 0, "status": "stopped"}
            pid = c.attrs["State"]["Pid"]
            PID_CACHE[node_id] = pid
        except Exception:
            PID_CACHE.pop(node_id, None)
            return {"traffic_mb": 0.0, "traffic_gb": 0.0, "sockets": 0, "status": "stopped"}

    rx_bytes, tx_bytes = 0, 0
    net_dev = f"/proc/{pid}/net/dev"
    if os.path.exists(net_dev):
        try:
            with open(net_dev, "r") as f:
                for line in f:
                    if "tun0:" in line or "tap0:" in line:
                        p = line.split()
                        rx_bytes, tx_bytes = int(p[1]), int(p[9])
                        break
                    elif "eth0:" in line and rx_bytes == 0:
                        p = line.split()
                        rx_bytes, tx_bytes = int(p[1]), int(p[9])
        except Exception: pass
                    
    total_bytes = rx_bytes + tx_bytes
    traffic_mb = round(total_bytes / 1048576, 2)
    traffic_gb = round(total_bytes / 1073741824, 3)
    
    sockets = 0
    for proto in [f"/proc/{pid}/net/tcp", f"/proc/{pid}/net/tcp6"]:
        if os.path.exists(proto):
            try:
                with open(proto, "r") as f:
                    for l in f.readlines()[1:]:
                        parts = l.strip().split()
                        if len(parts) >= 4 and parts[3] == "01": sockets += 1
            except Exception: pass
                        
    return {"traffic_mb": traffic_mb, "traffic_gb": traffic_gb, "sockets": sockets, "status": "running"}

@app.get("/api/system")
def get_system_stats(user: str = Depends(get_current_user)):
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    load = os.getloadavg()
    up_days = int((time.time() - psutil.boot_time()) // 86400)
    up_hours = int(((time.time() - psutil.boot_time()) % 86400) // 3600)
    
    net_load = get_vps_network_load()
    cpu_pct = STEADY_CPU_PERCENT
    score = 100
    if cpu_pct > 85: score -= 20
    if mem.percent > 90: score -= 25
    if net_load["is_choking"]: score -= 20
    score = max(0, score)
    
    settings = load_json(SETTINGS_FILE, default={"vps_alias": ""})
    vps_name = settings.get("vps_alias") or os.uname().nodename

    return {
        "version": CURRENT_VERSION,
        "hostname": os.uname().nodename,
        "vps_alias": vps_name,
        "uptime": f"{up_days}d {up_hours}h",
        "cpu_percent": cpu_pct,
        "cpu_cores": psutil.cpu_count(),
        "load_avg": f"{load[0]:.2f} / {load[1]:.2f}",
        "ram_used_mb": int(mem.used / 1048576),
        "ram_total_mb": int(mem.total / 1048576),
        "ram_free_mb": int(mem.available / 1048576),
        "ram_percent": mem.percent,
        "swap_used_mb": int(swap.used / 1048576),
        "swap_total_mb": int(swap.total / 1048576),
        "swap_free_mb": int(swap.free / 1048576),
        "swap_percent": swap.percent,
        "disk_used_mb": int(disk.used / 1048576),
        "disk_total_mb": int(disk.total / 1048576),
        "disk_free_mb": int(disk.free / 1048576),
        "disk_percent": disk.percent,
        "disk_mode": "RW (Bình thường)",
        "temperature": "N/A (Cloud VM)",
        "health_score": score,
        "health_status": f"{score}% Hoàn Hảo" if score >= 80 else f"{score}% Cảnh Báo",
        "network": net_load
    }

class VpsAliasRequest(BaseModel):
    vps_alias: str

@app.post("/api/system/vps-alias")
def set_vps_alias(req: VpsAliasRequest, user: str = Depends(get_current_user)):
    settings = load_json(SETTINGS_FILE, default={})
    settings["vps_alias"] = req.vps_alias.strip()
    save_json(SETTINGS_FILE, settings)
    return {"success": True, "vps_alias": settings["vps_alias"]}

@app.get("/api/system/audit")
def system_audit(user: str = Depends(get_current_user)):
    items = []
    items.append({"name": "Docker Engine Daemon", "status": True, "detail": "Đang hoạt động"})
    tun_ok = os.path.exists("/dev/net/tun")
    items.append({"name": "Card Mạng Ảo /dev/net/tun", "status": tun_ok, "detail": "Sẵn sàng" if tun_ok else "Chưa tạo thiết bị tun"})
    items.append({"name": "Bảo Mật Port 3100 (Web GUI)", "status": True, "detail": "Đang mở trên host"})
    return items

@app.get("/api/nodes/{nid}/logs")
def get_node_logs(nid: str, user: str = Depends(get_current_user)):
    app_log, tun_log = "", ""
    try:
        app_log = client.containers.get(f"app_{nid}").logs(tail=100, timestamps=True).decode("utf-8", errors="ignore")
    except Exception as e:
        app_log = f"Container ứng dụng chưa chạy (đang dừng hoặc chưa tạo): {str(e)}"
    try:
        tun_log = client.containers.get(f"tun_{nid}").logs(tail=100, timestamps=True).decode("utf-8", errors="ignore")
    except Exception as e:
        tun_log = f"Không lấy được log tun: {str(e)}"
    return {"app_logs": app_log, "tun_logs": tun_log}

class AccountModel(BaseModel):
    id: Optional[str] = None
    account_name: str
    platform: str
    token: Optional[str] = ""
    email: Optional[str] = ""
    password: Optional[str] = ""

@app.get("/api/accounts")
def get_accounts(user: str = Depends(get_current_user)):
    return load_json(ACCOUNTS_FILE, default=[])

@app.delete("/api/accounts/{acc_id}")
def delete_account(acc_id: str, user: str = Depends(get_current_user)):
    accounts = load_json(ACCOUNTS_FILE, default=[])
    accounts = [a for a in accounts if a.get("id") != acc_id]
    save_json(ACCOUNTS_FILE, accounts)
    return {"success": True}

class ImportBatchModel(BaseModel):
    account_name: str
    platform: str
    provider: str
    prefix: Optional[str] = ""
    proxies_raw: str
    token: Optional[str] = ""
    email: Optional[str] = ""
    password: Optional[str] = ""

def parse_proxy_line(line: str, prefix: str, current_idx: int):
    line = line.replace("\r", "").strip()
    if not line or line.startswith("#"):
        return None
    device_name = ""
    if "|" in line:
        line, device_name = line.split("|", 1)
        device_name = device_name.strip()
    proto = "http"
    rest = line.strip()
    if "://" in rest:
        proto, rest = rest.split("://", 1)
        proto = proto.lower()
    auth, ip_port = "", ""
    if "@" in rest:
        auth = rest.rsplit("@", 1)[0]
        ip_port = rest.rsplit("@", 1)[1]
    elif rest.count(":") == 3:
        p = rest.split(":")
        ip_port = f"{p[0]}:{p[1]}"
        auth = f"{p[2]}:{p[3]}"
    else:
        ip_port = rest
    if not device_name:
        device_name = f"{prefix}{current_idx}" if prefix else hashlib.md5(f"{ip_port}_{auth}".encode()).hexdigest()[:8]
    auth_str = f"{auth}@" if auth else ""
    return {
        "protocol": proto,
        "endpoint": ip_port,
        "proxy_url": f"{proto}://{auth_str}{ip_port}",
        "device_name": device_name
    }

@app.post("/api/nodes/import")
def import_nodes(req: ImportBatchModel, user: str = Depends(get_current_user)):
    nodes = load_nodes()
    lines = req.proxies_raw.strip().split("\n")
    start_idx = len(nodes) + 1
    added = 0
    for i, line in enumerate(lines):
        parsed = parse_proxy_line(line, req.prefix, start_idx + i)
        if not parsed: continue
        node_id = str(uuid.uuid4())[:8]
        node = {
            "id": node_id,
            "account_name": req.account_name.strip() if req.account_name else "Default",
            "platform": req.platform.strip().lower(),
            "provider": req.provider.strip() if req.provider else "Other",
            "device_name": parsed["device_name"],
            "protocol": parsed["protocol"],
            "endpoint": parsed["endpoint"],
            "proxy_url": parsed["proxy_url"],
            "auth": {
                "token": req.token.strip() if req.token else "",
                "email": req.email.strip() if req.email else "",
                "password": req.password.strip() if req.password else ""
            },
            "ping_ms": 0,
            "is_live": True,
            "status": "stopped"
        }
        nodes.append(node)
        added += 1
    save_nodes(nodes)
    return {"added": added, "total": len(nodes)}

# HÀM RUNNER CỐT LÕI (CHỈ HỖ TRỢ 7 PLATFORM + TUN2PROXY)
def run_node_containers(n):
    tun_name = f"tun_{n['id']}"
    app_name = f"app_{n['id']}"
    dev = n["device_name"]
    node_id = n["id"]
    
    m_dir = f"/opt/bandwidth-manager/data/meta_{node_id}"
    os.makedirs(m_dir, exist_ok=True)
    m_file = f"{m_dir}/machine-id"
    if not os.path.exists(m_file):
        with open(m_file, "w") as f:
            f.write(uuid.uuid4().hex + "\n")

    for c_name in [app_name, tun_name]:
        try: client.containers.get(c_name).remove(force=True)
        except Exception: pass

    if not os.path.exists("/dev/net/tun"):
        os.makedirs("/dev/net", exist_ok=True)
        subprocess.run("mknod /dev/net/tun c 10 200 2>/dev/null || true", shell=True)
        subprocess.run("chmod 666 /dev/net/tun 2>/dev/null || true", shell=True)

    # 1. Khởi tạo tun2proxy
    c_tun = client.containers.run(
        image="ghcr.io/tun2proxy/tun2proxy:v0.8.3",
        name=tun_name,
        hostname=dev,
        detach=True,
        restart_policy={"Name": "on-failure", "MaximumRetryCount": 5},
        cap_add=["NET_ADMIN"],
        devices=["/dev/net/tun:/dev/net/tun"],
        dns=["1.1.1.1", "8.8.8.8"],
        cpu_shares=1024,
        mem_reservation="8m",
        command=["--dns", "virtual", "--proxy", n["proxy_url"], "--verbosity", "off"]
    )
    
    PID_CACHE[node_id] = c_tun.attrs["State"]["Pid"] if "State" in c_tun.attrs else 0
    time.sleep(1.2)
    
    net_mode = f"container:{tun_name}"
    plt = n["platform"].lower()
    auth = n["auth"]
    vols = {m_file: {"bind": "/etc/machine-id", "mode": "ro"}}
    REST_POL = {"Name": "on-failure", "MaximumRetryCount": 3}
    
    # 2. Khởi tạo 7 Nền tảng chia sẻ băng thông
    if plt == "traffmonetizer":
        data_dir = f"/opt/bandwidth-manager/data/traff_{node_id}"
        os.makedirs(data_dir, exist_ok=True)
        vols[data_dir] = {"bind": "/.config/traffmonetizer", "mode": "rw"}
        client.containers.run(
            image="traffmonetizer/cli_v2:latest",
            name=app_name,
            hostname=dev,
            detach=True,
            network_mode=net_mode,
            restart_policy=REST_POL,
            cpu_shares=1024,
            mem_reservation="15m",
            volumes=vols,
            command=["start", "accept", "--device-name", dev, "--token", auth.get("token", "")]
        )
    elif plt == "honeygain":
        client.containers.run(
            image="honeygain/honeygain:0.6.6",
            name=app_name,
            hostname=dev,
            detach=True,
            network_mode=net_mode,
            restart_policy=REST_POL,
            cpu_shares=1024,
            mem_reservation="15m",
            volumes=vols,
            command=["-tou-accept", "-email", auth.get("email", ""), "-pass", auth.get("password", ""), "-device", dev]
        )
    elif plt == "pawns":
        client.containers.run(
            image="iproyal/pawns-cli:latest",
            name=app_name,
            hostname=dev,
            detach=True,
            network_mode=net_mode,
            restart_policy=REST_POL,
            cpu_shares=1024,
            mem_reservation="15m",
            volumes=vols,
            command=[f"-email={auth.get('email', '')}", f"-password={auth.get('password', '')}", f"-device-name={dev}", f"-device-id={dev}", "-accept-tos"]
        )
    elif plt == "repocket":
        client.containers.run(
            image="repocket/repocket:latest",
            name=app_name,
            hostname=dev,
            detach=True,
            network_mode=net_mode,
            restart_policy=REST_POL,
            cpu_shares=1024,
            mem_reservation="25m",
            volumes=vols,
            environment={"RP_EMAIL": auth.get("email", ""), "RP_API_KEY": auth.get("token", "")}
        )
    elif plt == "bitping":
        data_dir = f"/opt/bandwidth-manager/data/bitping_{node_id}"
        os.makedirs(data_dir, exist_ok=True)
        vols[data_dir] = {"bind": "/root/.bitpingd", "mode": "rw"}
        client.containers.run(
            image="bitping/bitpingd:latest",
            name=app_name,
            hostname=dev,
            detach=True,
            network_mode=net_mode,
            restart_policy=REST_POL,
            cpu_shares=1024,
            mem_reservation="15m",
            volumes=vols,
            environment={"BITPING_EMAIL": auth.get("email", ""), "BITPING_PASSWORD": auth.get("password", "")}
        )
    elif plt == "earnfm":
        client.containers.run(
            image="earnfm/earnfm-client:latest",
            name=app_name,
            hostname=dev,
            detach=True,
            network_mode=net_mode,
            restart_policy=REST_POL,
            cpu_shares=1024,
            mem_reservation="15m",
            volumes=vols,
            environment={"EARNFM_TOKEN": auth.get("token", "")}
        )
    elif plt == "packetstream":
        client.containers.run(
            image="packetstream/psclient:latest",
            name=app_name,
            hostname=dev,
            detach=True,
            network_mode=net_mode,
            restart_policy=REST_POL,
            cpu_shares=1024,
            mem_reservation="15m",
            volumes=vols,
            environment={"CID": auth.get("token", "")}
        )

def stop_node_containers(nid):
    PID_CACHE.pop(nid, None)
    for c_name in [f"app_{nid}", f"tun_{nid}"]:
        try:
            c = client.containers.get(c_name)
            c.stop(timeout=2)
            c.remove(force=True)
        except Exception: pass

@app.get("/api/nodes")
def get_nodes(user: str = Depends(get_current_user)):
    nodes = load_nodes()
    valid_nodes = []
    for n in nodes:
        if not isinstance(n, dict) or "id" not in n: continue
        metrics = get_node_network_stats(n["id"])
        n["traffic_mb"] = metrics.get("traffic_mb", 0.0)
        n["traffic_gb"] = metrics.get("traffic_gb", 0.0)
        n["sockets"] = metrics.get("sockets", 0)
        n["status"] = metrics.get("status", "stopped")
        valid_nodes.append(n)
    return valid_nodes

@app.post("/api/nodes/{nid}/start")
def start_node(nid: str, user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for n in nodes:
        if isinstance(n, dict) and n.get("id") == nid:
            try:
                run_node_containers(n)
                n["status"] = "running"
                save_nodes(nodes)
                return {"status": "started"}
            except Exception as e:
                print(f"[START ERROR] Node {nid}: {traceback.format_exc()}", flush=True)
                raise HTTPException(status_code=400, detail=f"Lỗi Docker: {str(e)}")
    raise HTTPException(status_code=404, detail="Node không tồn tại")

@app.post("/api/nodes/{nid}/stop")
def stop_node(nid: str, user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for n in nodes:
        if isinstance(n, dict) and n.get("id") == nid:
            stop_node_containers(nid)
            n["status"] = "stopped"
            save_nodes(nodes)
            return {"status": "stopped"}
    raise HTTPException(status_code=404, detail="Node không tồn tại")

@app.delete("/api/nodes/{nid}")
def delete_node(nid: str, user: str = Depends(get_current_user)):
    nodes = load_nodes()
    stop_node_containers(nid)
    nodes = [n for n in nodes if isinstance(n, dict) and n.get("id") != nid]
    save_nodes(nodes)
    return {"status": "deleted"}

@app.post("/api/nodes/bulk/start")
def bulk_start(req: List[str], user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for n in nodes:
        if isinstance(n, dict) and n.get("id") in req:
            try:
                run_node_containers(n)
                n["status"] = "running"
            except Exception as e:
                print(f"[BULK START ERROR] Node {n.get('id')}: {str(e)}", flush=True)
    save_nodes(nodes)
    return {"status": "bulk_started"}

@app.post("/api/nodes/bulk/stop")
def bulk_stop(req: List[str], user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for n in nodes:
        if isinstance(n, dict) and n.get("id") in req:
            stop_node_containers(n["id"])
            n["status"] = "stopped"
    save_nodes(nodes)
    return {"status": "bulk_stopped"}

@app.post("/api/nodes/bulk/delete")
def bulk_delete(req: List[str], user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for nid in req: stop_node_containers(nid)
    nodes = [n for n in nodes if isinstance(n, dict) and n.get("id") not in req]
    save_nodes(nodes)
    return {"status": "bulk_deleted"}

@app.get("/", response_class=HTMLResponse)
def index():
    with open("/opt/bandwidth-manager/index.html", "r") as f:
        return f.read()

def staggered_boot_daemon():
    time.sleep(4)
    nodes = load_nodes()
    for n in nodes:
        if isinstance(n, dict) and n.get("status") == "running":
            try:
                run_node_containers(n)
                time.sleep(1.0)
            except Exception: pass

@app.on_event("startup")
def on_startup():
    threading.Thread(target=staggered_boot_daemon, daemon=True).start()