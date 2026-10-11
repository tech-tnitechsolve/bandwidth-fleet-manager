import os
import psutil
import docker
import json
import uuid
import hashlib
import time
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="Bandwidth Fleet Manager Pro Engine")
security = HTTPBasic()
client = docker.from_env()

DATA_FILE = "/opt/bandwidth-manager/data/nodes.json"
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")

last_net_time = 0.0
last_rx_bytes = 0
last_tx_bytes = 0

PID_CACHE = {}

def load_nodes():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r") as f:
        try: return json.load(f)
        except: return []

def save_nodes(nodes):
    with open(DATA_FILE, "w") as f:
        json.dump(nodes, f, indent=2)

def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != ADMIN_USER or credentials.password != ADMIN_PASS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản hoặc mật khẩu không chính xác",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# 1. ĐO TẢI MẠNG VẬT LÝ VPS (THROUGHPUT MBPS & PACKET DROPS)
def get_vps_network_load():
    global last_net_time, last_rx_bytes, last_tx_bytes
    
    default_iface = "eth0"
    try:
        with open("/proc/net/route", "r") as f:
            for line in f.readlines()[1:]:
                fields = line.strip().split()
                if fields[1] == "00000000":
                    default_iface = fields[0]
                    break
    except: pass

    current_rx = 0
    current_tx = 0
    rx_drops = 0
    tx_drops = 0
    
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f:
                if default_iface in line:
                    p = line.split()
                    current_rx = int(p[1])
                    rx_drops = int(p[4])
                    current_tx = int(p[9])
                    tx_drops = int(p[12])
                    break
    except: pass

    now = time.time()
    dt = now - last_net_time if last_net_time > 0 else 1.0
    dt = max(dt, 0.5)
    
    if last_net_time > 0 and dt > 0:
        rx_speed_mbps = round(((current_rx - last_rx_bytes) * 8) / (dt * 1000000), 2)
        tx_speed_mbps = round(((current_tx - last_tx_bytes) * 8) / (dt * 1000000), 2)
    else:
        rx_speed_mbps = 0.0
        tx_speed_mbps = 0.0

    last_net_time = now
    last_rx_bytes = current_rx
    last_tx_bytes = current_tx

    conntrack_count = 0
    conntrack_max = 524288
    try:
        with open("/proc/sys/net/netfilter/nf_conntrack_count", "r") as f:
            conntrack_count = int(f.read().strip())
        with open("/proc/sys/net/netfilter/nf_conntrack_max", "r") as f:
            conntrack_max = int(f.read().strip())
    except: pass

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

# 2. ĐO LƯU LƯỢNG TỪNG NODE (SIÊU TỐC VỚI PID CACHE / ZERO-PROBE)
def get_node_network_stats(node_id):
    tun_name = f"tun_{node_id}"
    pid = PID_CACHE.get(node_id, 0)
    
    if pid <= 0 or not os.path.exists(f"/proc/{pid}"):
        try:
            c = client.containers.get(tun_name)
            if c.status != "running":
                PID_CACHE.pop(node_id, None)
                return {"traffic_mb": 0.0, "traffic_gb": 0.0, "sockets": 0, "status": "stopped"}
            pid = c.attrs['State']['Pid']
            PID_CACHE[node_id] = pid
        except:
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
                        rx_bytes = int(p[1])
                        tx_bytes = int(p[9])
                        break
                    elif "eth0:" in line and rx_bytes == 0:
                        p = line.split()
                        rx_bytes = int(p[1])
                        tx_bytes = int(p[9])
        except: pass
                    
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
                        if len(parts) >= 4 and parts[3] == "01":
                            sockets += 1
            except: pass
                        
    return {
        "traffic_mb": traffic_mb,
        "traffic_gb": traffic_gb,
        "sockets": sockets,
        "status": "running"
    }

@app.get("/api/system", dependencies=[Depends(authenticate)])
def get_system_stats():
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    load = os.getloadavg()
    import time
    up_days = int((time.time() - psutil.boot_time()) // 86400)
    up_hours = int(((time.time() - psutil.boot_time()) % 86400) // 3600)
    
    return {
        "hostname": os.uname().nodename,
        "uptime": f"{up_days}d {up_hours}h",
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_cores": psutil.cpu_count(),
        "load_avg": f"{load[0]:.2f} / {load[1]:.2f}",
        "ram_used_mb": int(mem.used / 1048576),
        "ram_total_mb": int(mem.total / 1048576),
        "ram_free_mb": int(mem.available / 1048576),
        "ram_percent": mem.percent,
        "swap_used_mb": int(swap.used / 1048576),
        "swap_total_mb": int(swap.total / 1048576),
        "swap_percent": swap.percent,
        "disk_used_mb": int(disk.used / 1048576),
        "disk_total_mb": int(disk.total / 1048576),
        "disk_percent": disk.percent,
        "network": get_vps_network_load()
    }

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
    line = line.replace('\r', '').strip()
    if not line or line.startswith("#"):
        return None
    
    device_name = ""
    if "|" in line:
        line, device_name = line.split("|", 1)
        device_name = device_name.strip()
    elif "#" in line and not line.startswith("#"):
        line, device_name = line.split("#", 1)
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
    elif rest.count(":") == 4 and not device_name:
        p = rest.split(":")
        ip_port = f"{p[0]}:{p[1]}"
        auth = f"{p[2]}:{p[3]}"
        device_name = p[4]
    elif rest.count(":") == 3:
        p = rest.split(":")
        ip_port = f"{p[0]}:{p[1]}"
        auth = f"{p[2]}:{p[3]}"
    else:
        ip_port = rest
        
    if not device_name:
        if prefix:
            device_name = f"{prefix}{current_idx}"
        else:
            device_name = hashlib.md5(f"{ip_port}_{auth}".encode()).hexdigest()[:8]
            
    full_url = f"{proto}://{auth + '@' if auth else ''}{ip_port}"
    return {
        "protocol": proto,
        "endpoint": ip_port,
        "proxy_url": full_url,
        "device_name": device_name
    }

@app.post("/api/nodes/import", dependencies=[Depends(authenticate)])
def import_nodes(req: ImportBatchModel):
    nodes = load_nodes()
    lines = req.proxies_raw.strip().split("\n")
    start_idx = len(nodes) + 1
    added = 0
    for i, line in enumerate(lines):
        parsed = parse_proxy_line(line, req.prefix, start_idx + i)
        if not parsed:
            continue
        node_id = str(uuid.uuid4())[:8]
        node = {
            "id": node_id,
            "account_name": req.account_name.strip(),
            "platform": req.platform.strip(),
            "provider": req.provider.strip(),
            "device_name": parsed["device_name"],
            "protocol": parsed["protocol"],
            "endpoint": parsed["endpoint"],
            "proxy_url": parsed["proxy_url"],
            "auth": {
                "token": req.token.strip() if req.token else "",
                "email": req.email.strip() if req.email else "",
                "password": req.password.strip() if req.password else ""
            },
            "status": "stopped"
        }
        nodes.append(node)
        added += 1
    save_nodes(nodes)
    return {"added": added, "total": len(nodes)}

# KHỞI CHẠY CONTAINER: BẢO VỆ DANH TÍNH & THAM SỐ ARRAY AN TOÀN
def run_node_containers(n):
    tun_name = f"tun_{n['id']}"
    app_name = f"app_{n['id']}"
    dev = n["device_name"]
    node_id = n["id"]
    
    # 1. Tạo file machine-id độc lập chống clone detection
    m_dir = f"/opt/bandwidth-manager/data/meta_{node_id}"
    os.makedirs(m_dir, exist_ok=True)
    m_file = f"{m_dir}/machine-id"
    if not os.path.exists(m_file):
        with open(m_file, "w") as f:
            f.write(uuid.uuid4().hex + "\n")

    # 2. Khởi chạy TUN Container
    try: client.containers.get(tun_name).remove(force=True); except: pass
    c_tun = client.containers.run(
        image="ghcr.io/tun2proxy/tun2proxy:v0.8.3",
        name=tun_name,
        hostname=dev,
        detach=True,
        restart_policy={"Name": "always"},
        cap_add=["NET_ADMIN"],
        devices=["/dev/net/tun:/dev/net/tun"],
        dns=["1.1.1.1", "8.8.8.8"],
        sysctls={
            "net.ipv6.conf.all.disable_ipv6": "1",
            "net.ipv6.conf.default.disable_ipv6": "1"
        },
        cpu_shares=512,
        mem_reservation="20m",
        command=["--dns", "virtual", "--proxy", n['proxy_url'], "--verbosity", "off"]
    )
    
    PID_CACHE[node_id] = c_tun.attrs['State']['Pid'] if 'State' in c_tun.attrs else 0

    # Độ trễ 1.5s bảo vệ đường truyền bắt tay xong mới bật worker
    time.sleep(1.5)
    
    # 3. Khởi chạy Worker Platform
    net_mode = f"container:{tun_name}"
    plt = n["platform"].lower()
    auth = n["auth"]
    
    try: client.containers.get(app_name).remove(force=True); except: pass
    
    vols = {m_file: {"bind": "/etc/machine-id", "mode": "ro"}}
    
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
            restart_policy={"Name": "unless-stopped"},
            cpu_shares=512,
            mem_reservation="60m",
            volumes=vols,
            command=["start", "accept", "--device-name", dev, "--token", auth.get('token', '')]
        )
    elif plt == "honeygain":
        client.containers.run(
            image="honeygain/honeygain:0.6.6",
            name=app_name,
            hostname=dev,
            detach=True,
            network_mode=net_mode,
            restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
            cpu_shares=512,
            mem_reservation="60m",
            volumes=vols,
            command=["-tou-accept", "-email", auth.get('email', ''), "-pass", auth.get('password', ''), "-device", dev]
        )
    elif plt == "pawns":
        client.containers.run(
            image="iproyal/pawns-cli:latest",
            name=app_name,
            hostname=dev,
            detach=True,
            network_mode=net_mode,
            restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
            cpu_shares=512,
            mem_reservation="60m",
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
            restart_policy={"Name": "unless-stopped"},
            cpu_shares=512,
            mem_reservation="100m",
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
            restart_policy={"Name": "unless-stopped"},
            cpu_shares=512,
            mem_reservation="35m",
            volumes=vols,
            environment={"BITPING_EMAIL": auth.get("email", ""), "BITPING_PASSWORD": auth.get("password", "")}
        )

# DỪNG AN TOÀN: APP TRƯỚC, TUN SAU
def stop_node_containers(nid):
    PID_CACHE.pop(nid, None)
    try:
        a = client.containers.get(f"app_{nid}")
        a.stop(timeout=2)
        a.remove(force=True)
    except: pass
    try:
        t = client.containers.get(f"tun_{nid}")
        t.stop(timeout=2)
        t.remove(force=True)
    except: pass

@app.get("/api/nodes", dependencies=[Depends(authenticate)])
def get_nodes():
    nodes = load_nodes()
    for n in nodes:
        metrics = get_node_network_stats(n["id"])
        n["traffic_mb"] = metrics["traffic_mb"]
        n["traffic_gb"] = metrics["traffic_gb"]
        n["sockets"] = metrics["sockets"]
        n["status"] = metrics["status"]
    return nodes

@app.post("/api/nodes/{nid}/start", dependencies=[Depends(authenticate)])
def start_node(nid: str):
    nodes = load_nodes()
    for n in nodes:
        if n["id"] == nid:
            run_node_containers(n)
            n["status"] = "running"
            save_nodes(nodes)
            return {"status": "started"}
    raise HTTPException(status_code=404, detail="Node không tồn tại")

@app.post("/api/nodes/{nid}/stop", dependencies=[Depends(authenticate)])
def stop_node(nid: str):
    nodes = load_nodes()
    for n in nodes:
        if n["id"] == nid:
            stop_node_containers(nid)
            n["status"] = "stopped"
            save_nodes(nodes)
            return {"status": "stopped"}
    raise HTTPException(status_code=404, detail="Node không tồn tại")

class EditNodeModel(BaseModel):
    proxy_url: str
    device_name: Optional[str] = None
    account_name: Optional[str] = None
    provider: Optional[str] = None

@app.put("/api/nodes/{nid}", dependencies=[Depends(authenticate)])
def update_node(nid: str, req: EditNodeModel):
    nodes = load_nodes()
    for n in nodes:
        if n["id"] == nid:
            parsed = parse_proxy_line(req.proxy_url, "", 0)
            if not parsed:
                raise HTTPException(status_code=400, detail="Proxy không hợp lệ")
            n["proxy_url"] = parsed["proxy_url"]
            n["protocol"] = parsed["protocol"]
            n["endpoint"] = parsed["endpoint"]
            if req.device_name: n["device_name"] = req.device_name
            if req.account_name: n["account_name"] = req.account_name
            if req.provider: n["provider"] = req.provider
            
            if n["status"] == "running":
                stop_node_containers(nid)
                run_node_containers(n)
            save_nodes(nodes)
            return {"status": "updated"}
    raise HTTPException(status_code=404, detail="Node không tồn tại")

@app.delete("/api/nodes/{nid}", dependencies=[Depends(authenticate)])
def delete_node(nid: str):
    nodes = load_nodes()
    stop_node_containers(nid)
    nodes = [n for n in nodes if n["id"] != nid]
    save_nodes(nodes)
    return {"status": "deleted"}

@app.post("/api/nodes/bulk/start", dependencies=[Depends(authenticate)])
def bulk_start(req: List[str]):
    nodes = load_nodes()
    for n in nodes:
        if n["id"] in req:
            run_node_containers(n)
            n["status"] = "running"
    save_nodes(nodes)
    return {"status": "bulk_started"}

@app.post("/api/nodes/bulk/stop", dependencies=[Depends(authenticate)])
def bulk_stop(req: List[str]):
    nodes = load_nodes()
    for n in nodes:
        if n["id"] in req:
            stop_node_containers(n["id"])
            n["status"] = "stopped"
    save_nodes(nodes)
    return {"status": "bulk_stopped"}

@app.get("/", response_class=HTMLResponse)
def index():
    with open("/opt/bandwidth-manager/index.html", "r") as f:
        return f.read()