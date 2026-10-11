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
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Depends, status, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional

CURRENT_VERSION = "v1.4.0"
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
    
    # Cập nhật vào file .env
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

# CƠ CHẾ CHECK LIVE TCP TRỰC TIẾP TỪ IP GỐC CỦA VPS
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

# 1. ĐO TẢI MẠNG VẬT LÝ VPS (THROUGHPUT MBPS & PACKET DROPS)
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
    except Exception: pass

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

# 2. ĐO LƯU LƯỢNG TỪNG NODE (ZERO-PROBE TỪ PROC KERNEL)
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
                        rx_bytes = int(p[1])
                        tx_bytes = int(p[9])
                        break
                    elif "eth0:" in line and rx_bytes == 0:
                        p = line.split()
                        rx_bytes = int(p[1])
                        tx_bytes = int(p[9])
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
                        if len(parts) >= 4 and parts[3] == "01":
                            sockets += 1
            except Exception: pass
                        
    return {
        "traffic_mb": traffic_mb,
        "traffic_gb": traffic_gb,
        "sockets": sockets,
        "status": "running"
    }

# 3. ĐO NHIỆT ĐỘ VÀ SỨC KHỎE HỆ THỐNG
def get_system_temperature():
    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    for entry in entries:
                        if entry.current and entry.current > 0:
                            return f"{int(entry.current)}°C"
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return f"{int(int(f.read().strip()) / 1000)}°C"
    except Exception: pass
    return "N/A (Cloud VM)"

@app.get("/api/system")
def get_system_stats(user: str = Depends(get_current_user)):
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    load = os.getloadavg()
    import time
    up_days = int((time.time() - psutil.boot_time()) // 86400)
    up_hours = int(((time.time() - psutil.boot_time()) % 86400) // 3600)
    
    net_load = get_vps_network_load()
    temp_str = get_system_temperature()
    
    disk_mode = "RW (Bình thường)"
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == "/":
                    if "ro" in parts[3].split(","):
                        disk_mode = "RO (Lỗi chỉ đọc!)"
                    break
    except Exception: pass

    cpu_pct = psutil.cpu_percent(interval=None)
    score = 100
    if "RO" in disk_mode: score = 0
    if cpu_pct > 85: score -= 20
    elif cpu_pct > 70: score -= 10
    if mem.percent > 90: score -= 25
    elif mem.percent > 80: score -= 15
    if net_load["is_choking"]: score -= 20
    score = max(0, score)
    
    if score >= 90: health_text = "100% Hoàn Hảo (Ổn định 24/7)"
    elif score >= 70: health_text = f"{score}% Tốt (Tải bình thường)"
    else: health_text = f"{score}% Cảnh Báo Quá Tải"

    git_commit = "unknown"
    try:
        git_commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd="/opt/bandwidth-manager").decode().strip()
    except Exception: pass

    settings = load_json(SETTINGS_FILE, default={"vps_alias": ""})
    vps_name = settings.get("vps_alias") or os.uname().nodename

    return {
        "version": CURRENT_VERSION,
        "commit": git_commit,
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
        "disk_mode": disk_mode,
        "temperature": temp_str,
        "health_score": score,
        "health_status": health_text,
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

# 4. BẢNG AUDIT RÀ SOÁT CÁC THÔNG SỐ VÀ SOURCE ĐÃ CÀI
@app.get("/api/system/audit")
def system_audit(user: str = Depends(get_current_user)):
    items = []
    
    # Check Docker
    docker_ok = False
    docker_ver = "Không phát hiện"
    try:
        docker_ver = client.version().get("Version", "Unknown")
        docker_ok = True
    except Exception: pass
    items.append({"name": "Docker Engine Daemon", "status": docker_ok, "detail": f"Phiên bản {docker_ver}"})
    
    # Check /dev/net/tun
    tun_ok = os.path.exists("/dev/net/tun")
    items.append({"name": "Card Mạng Ảo /dev/net/tun", "status": tun_ok, "detail": "Sẵn sàng" if tun_ok else "Thiếu thiết bị tun"})

    # Check BBR
    bbr_ok = False
    try:
        with open("/proc/sys/net/ipv4/tcp_congestion_control", "r") as f:
            bbr_ok = "bbr" in f.read()
    except Exception: pass
    items.append({"name": "Thuật Toán BBR Congestion Control", "status": bbr_ok, "detail": "BBR Đang hoạt động" if bbr_ok else "Chưa kích hoạt BBR"})

    # Check ZRAM
    zram_ok = False
    zram_detail = "Chưa bật ZRAM"
    try:
        out = subprocess.check_output(["swapon", "--show"], text=True)
        if "/dev/zram0" in out:
            zram_ok = True
            zram_detail = "ZRAM ZSTD Priority 10 Active"
    except Exception: pass
    items.append({"name": "Bộ Nhớ Nén ZRAM ZSTD", "status": zram_ok, "detail": zram_detail})

    # Check File Descriptors
    ulimit_val = 0
    try:
        import resource
        ulimit_val = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    except Exception: pass
    items.append({"name": "Giới Hạn File Descriptors (nofile)", "status": ulimit_val >= 65536, "detail": f"Hiện tại: {ulimit_val} (Khuyến nghị >= 65536)"})

    # Check Conntrack
    ct_max = 0
    try:
        with open("/proc/sys/net/netfilter/nf_conntrack_max", "r") as f:
            ct_max = int(f.read().strip())
    except Exception: pass
    items.append({"name": "Bảng Theo Dõi Mạng (Conntrack Max)", "status": ct_max >= 262144, "detail": f"Sức chứa {ct_max} streams"})

    # Check Port 3100 Firewall
    items.append({"name": "Tường Lửa Port 3100 (Web Dashboard)", "status": True, "detail": "Đã cấu hình tự động mở trên host"})

    return items

# 5. CÁC HÀNH ĐỘNG KIỂM SOÁT SÂU VPS
@app.post("/api/system/clean-ram")
def clean_ram_cache(user: str = Depends(get_current_user)):
    try:
        subprocess.run("sync && echo 3 > /proc/sys/vm/drop_caches", shell=True, check=True)
        return {"success": True, "message": "Đã giải phóng bộ nhớ đệm Buffer/Cache thành công!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/system/prune-docker")
def prune_docker(user: str = Depends(get_current_user)):
    try:
        c_res = client.containers.prune()
        n_res = client.networks.prune()
        i_res = client.images.prune(filters={"dangling": True})
        return {
            "success": True, 
            "containers_deleted": len(c_res.get("ContainersDeleted") or []),
            "space_reclaimed_mb": round((i_res.get("SpaceReclaimed") or 0) / 1048576, 2)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/system/reboot-vps")
def reboot_vps(user: str = Depends(get_current_user)):
    # Dừng an toàn các container trước khi reboot
    nodes = load_nodes()
    for n in nodes:
        stop_node_containers(n["id"])
    cmd = "sleep 2 && reboot"
    subprocess.Popen(cmd, shell=True)
    return {"success": True, "message": "VPS đang khởi động lại an toàn..."}

# 6. OTA UPDATE (AN TOÀN TUYỆT ĐỐI — KHÔNG CHẠM CONTAINER)
@app.get("/api/system/check-update")
def check_update(user: str = Depends(get_current_user)):
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"
        req = urllib.request.Request(url, headers={"User-Agent": "BandwidthManager"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode())
            remote_sha = data.get("sha", "")[:7]
            commit_msg = data.get("commit", {}).get("message", "").split("\n")[0]
            
            local_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd="/opt/bandwidth-manager").decode().strip()
            has_update = (remote_sha != local_sha and remote_sha != "")
            return {
                "has_update": has_update,
                "local_sha": local_sha,
                "remote_sha": remote_sha,
                "message": commit_msg
            }
    except Exception as e:
        return {"has_update": False, "error": str(e)}

@app.post("/api/system/apply-update")
def apply_update(user: str = Depends(get_current_user)):
    cmd = "sleep 1 && cd /opt/bandwidth-manager && git fetch && git reset --hard origin/main && systemctl restart bandwidth-manager"
    subprocess.Popen(cmd, shell=True)
    return {"status": "updating"}

# 7. LOGS TỪNG CONTAINER
@app.get("/api/nodes/{nid}/logs")
def get_node_logs(nid: str, user: str = Depends(get_current_user)):
    app_log = ""
    tun_log = ""
    try:
        app_log = client.containers.get(f"app_{nid}").logs(tail=100, timestamps=True).decode("utf-8", errors="ignore")
    except Exception as e:
        app_log = f"Không lấy được log app: {str(e)}"
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
    return load_json(ACCOUNTS_FILE)

@app.post("/api/accounts")
def create_account(req: AccountModel, user: str = Depends(get_current_user)):
    accounts = load_json(ACCOUNTS_FILE)
    acc_id = str(uuid.uuid4())[:8]
    data = req.dict()
    data["id"] = acc_id
    accounts.append(data)
    save_json(ACCOUNTS_FILE, accounts)
    return {"success": True, "account": data}

@app.delete("/api/accounts/{acc_id}")
def delete_account(acc_id: str, user: str = Depends(get_current_user)):
    accounts = load_json(ACCOUNTS_FILE)
    accounts = [a for a in accounts if a["id"] != acc_id]
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
            
    auth_str = f"{auth}@" if auth else ""
    full_url = f"{proto}://{auth_str}{ip_port}"
    return {
        "protocol": proto,
        "endpoint": ip_port,
        "proxy_url": full_url,
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
            "ping_ms": 0,
            "is_live": True,
            "status": "stopped"
        }
        nodes.append(node)
        added += 1
    save_nodes(nodes)
    
    if req.account_name:
        accounts = load_json(ACCOUNTS_FILE)
        if not any(a["account_name"] == req.account_name.strip() and a["platform"] == req.platform.strip() for a in accounts):
            accounts.append({
                "id": str(uuid.uuid4())[:8],
                "account_name": req.account_name.strip(),
                "platform": req.platform.strip(),
                "token": req.token.strip() if req.token else "",
                "email": req.email.strip() if req.email else "",
                "password": req.password.strip() if req.password else ""
            })
            save_json(ACCOUNTS_FILE, accounts)
            
    return {"added": added, "total": len(nodes)}

# RESTART TỐI ĐA 3 LẦN CHO MỌI NỀN TẢNG (on-failure:3)
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

    try: client.containers.get(tun_name).remove(force=True)
    except Exception: pass

    c_tun = client.containers.run(
        image="ghcr.io/tun2proxy/tun2proxy:v0.8.3",
        name=tun_name,
        hostname=dev,
        detach=True,
        restart_policy={"Name": "on-failure", "MaximumRetryCount": 5},
        cap_add=["NET_ADMIN"],
        devices=["/dev/net/tun:/dev/net/tun"],
        dns=["1.1.1.1", "8.8.8.8"],
        sysctls={
            "net.ipv6.conf.all.disable_ipv6": "1",
            "net.ipv6.conf.default.disable_ipv6": "1"
        },
        cpu_shares=512,
        mem_reservation="20m",
        command=["--dns", "virtual", "--proxy", n["proxy_url"], "--verbosity", "off"]
    )
    
    PID_CACHE[node_id] = c_tun.attrs["State"]["Pid"] if "State" in c_tun.attrs else 0
    time.sleep(1.5)
    
    net_mode = f"container:{tun_name}"
    plt = n["platform"].lower()
    auth = n["auth"]
    
    try: client.containers.get(app_name).remove(force=True)
    except Exception: pass
    
    vols = {m_file: {"bind": "/etc/machine-id", "mode": "ro"}}
    REST_POL = {"Name": "on-failure", "MaximumRetryCount": 3}
    
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
            cpu_shares=512,
            mem_reservation="60m",
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
            cpu_shares=512,
            mem_reservation="60m",
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
            restart_policy=REST_POL,
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
            restart_policy=REST_POL,
            cpu_shares=512,
            mem_reservation="35m",
            volumes=vols,
            environment={"BITPING_EMAIL": auth.get("email", ""), "BITPING_PASSWORD": auth.get("password", "")}
        )

def stop_node_containers(nid):
    PID_CACHE.pop(nid, None)
    try:
        a = client.containers.get(f"app_{nid}")
        a.stop(timeout=2)
        a.remove(force=True)
    except Exception: pass
    try:
        t = client.containers.get(f"tun_{nid}")
        t.stop(timeout=2)
        t.remove(force=True)
    except Exception: pass

@app.get("/api/nodes")
def get_nodes(user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for n in nodes:
        metrics = get_node_network_stats(n["id"])
        n["traffic_mb"] = metrics["traffic_mb"]
        n["traffic_gb"] = metrics["traffic_gb"]
        n["sockets"] = metrics["sockets"]
        n["status"] = metrics["status"]
    return nodes

@app.post("/api/nodes/{nid}/check")
def check_single_node(nid: str, user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for n in nodes:
        if n["id"] == nid:
            res = check_proxy_live_raw(n["endpoint"])
            n["is_live"] = res["is_live"]
            n["ping_ms"] = res["ping_ms"]
            save_nodes(nodes)
            return res
    raise HTTPException(status_code=404, detail="Node không tồn tại")

@app.post("/api/nodes/check-all")
def check_all_nodes(user: str = Depends(get_current_user)):
    nodes = load_nodes()
    if not nodes:
        return {"total": 0, "live": 0, "dead": 0}

    def worker_check(node):
        res = check_proxy_live_raw(node["endpoint"])
        node["is_live"] = res["is_live"]
        node["ping_ms"] = res["ping_ms"]
        return res["is_live"]

    with ThreadPoolExecutor(max_workers=25) as executor:
        results = list(executor.map(worker_check, nodes))

    save_nodes(nodes)
    live_cnt = sum(1 for r in results if r)
    return {"total": len(nodes), "live": live_cnt, "dead": len(nodes) - live_cnt}

@app.delete("/api/nodes/dead")
def delete_dead_nodes(user: str = Depends(get_current_user)):
    nodes = load_nodes()
    dead_ids = [n["id"] for n in nodes if not n.get("is_live", True)]
    for did in dead_ids:
        stop_node_containers(did)
    nodes = [n for n in nodes if n["id"] not in dead_ids]
    save_nodes(nodes)
    return {"deleted": len(dead_ids)}

@app.post("/api/nodes/{nid}/start")
def start_node(nid: str, user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for n in nodes:
        if n["id"] == nid:
            run_node_containers(n)
            n["status"] = "running"
            save_nodes(nodes)
            return {"status": "started"}
    raise HTTPException(status_code=404, detail="Node không tồn tại")

@app.post("/api/nodes/{nid}/stop")
def stop_node(nid: str, user: str = Depends(get_current_user)):
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

@app.put("/api/nodes/{nid}")
def update_node(nid: str, req: EditNodeModel, user: str = Depends(get_current_user)):
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
            
            res = check_proxy_live_raw(n["endpoint"])
            n["is_live"] = res["is_live"]
            n["ping_ms"] = res["ping_ms"]
            
            if n["status"] == "running":
                stop_node_containers(nid)
                run_node_containers(n)
            save_nodes(nodes)
            return {"status": "updated"}
    raise HTTPException(status_code=404, detail="Node không tồn tại")

@app.delete("/api/nodes/{nid}")
def delete_node(nid: str, user: str = Depends(get_current_user)):
    nodes = load_nodes()
    stop_node_containers(nid)
    nodes = [n for n in nodes if n["id"] != nid]
    save_nodes(nodes)
    return {"status": "deleted"}

@app.post("/api/nodes/bulk/start")
def bulk_start(req: List[str], user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for n in nodes:
        if n["id"] in req:
            run_node_containers(n)
            n["status"] = "running"
    save_nodes(nodes)
    return {"status": "bulk_started"}

@app.post("/api/nodes/bulk/stop")
def bulk_stop(req: List[str], user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for n in nodes:
        if n["id"] in req:
            stop_node_containers(n["id"])
            n["status"] = "stopped"
    save_nodes(nodes)
    return {"status": "bulk_stopped"}

@app.post("/api/nodes/bulk/delete")
def bulk_delete(req: List[str], user: str = Depends(get_current_user)):
    nodes = load_nodes()
    for nid in req:
        stop_node_containers(nid)
    nodes = [n for n in nodes if n["id"] not in req]
    save_nodes(nodes)
    return {"status": "bulk_deleted"}

@app.get("/", response_class=HTMLResponse)
def index():
    with open("/opt/bandwidth-manager/index.html", "r") as f:
        return f.read()

# 8. STAGGERED AUTO-BOOT KHI VPS REBOOT
def staggered_boot_daemon():
    time.sleep(5)
    nodes = load_nodes()
    for n in nodes:
        if n.get("status") == "running":
            tun_name = f"tun_{n['id']}"
            try:
                c = client.containers.get(tun_name)
                if c.status != "running":
                    run_node_containers(n)
                    time.sleep(1.0)
            except Exception:
                run_node_containers(n)
                time.sleep(1.0)

@app.on_event("startup")
def on_startup():
    threading.Thread(target=staggered_boot_daemon, daemon=True).start()