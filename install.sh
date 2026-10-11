#!/usr/bin/env bash
#============================================================================
#  install.sh - 1-Click All-in-One Engine (Hardware Tuning + Fleet Manager)
#  Tích hợp: Swapfile, ZRAM ZSTD, BBR, KSM, RPS, Anti-Leak & Web Dashboard
#============================================================================
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "[XX] Cần chạy quyền root: sudo bash $0"; exit 1; }

APP_DIR="/opt/bandwidth-manager"
PORT=3100
ADMIN_USER="admin"

echo "=========================================================================="
echo "    BẢO MẬT WEB DASHBOARD QUẢN TRỊ (BANDWIDTH FLEET MANAGER PRO)"
echo "=========================================================================="
ADMIN_PASS=""
while true; do
  read -s -p ">> Nhập MẬT KHẨU quản trị mới cho tài khoản 'admin': " P1
  echo ""
  if [[ -z "$P1" ]] || [[ ${#P1} -lt 6 ]]; then
    echo "[!] Mật khẩu không được để trống và phải có ít nhất 6 ký tự. Nhập lại!"
    continue
  fi
  read -s -p ">> Xác nhận lại mật khẩu: " P2
  echo ""
  if [[ "$P1" != "$P2" ]]; then
    echo "[!] Mật khẩu không khớp. Vui lòng nhập lại!"
  else
    ADMIN_PASS="$P1"
    break
  fi
done

echo -e "\n[1/6] Nhận diện phần cứng & Cấu hình Swapfile tối ưu..."
MEM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
CPU=$(nproc 2>/dev/null || echo 1)
DISK_TOTAL_MB=$(df -m / | awk 'NR==2 {print $2}')

if (( MEM_MB >= 9000 )) || (( DISK_TOTAL_MB <= 25000 )); then
  TARGET_SWAP_MB=1024
elif (( MEM_MB <= 2500 )); then
  TARGET_SWAP_MB=1024
elif (( MEM_MB <= 5000 )); then
  TARGET_SWAP_MB=1536
else
  TARGET_SWAP_MB=2048
fi

CURR_DISK_SWAP_MB=$(swapon --show=NAME,SIZE --bytes 2>/dev/null | awk '/swapfile/{print int($2/1024/1024)}' || echo 0)
if (( CURR_DISK_SWAP_MB != TARGET_SWAP_MB )); then
  if (( CURR_DISK_SWAP_MB == 0 )); then
    echo "Khởi tạo Swapfile ${TARGET_SWAP_MB}MB..."
    fallocate -l "${TARGET_SWAP_MB}M" /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count="$TARGET_SWAP_MB" status=none
    chmod 600 /swapfile
    if mkswap /swapfile >/dev/null 2>&1 && swapon -p 0 /swapfile 2>/dev/null; then
      grep -q "^/swapfile" /etc/fstab || echo "/swapfile none swap sw,pri=0 0 0" >> /etc/fstab
    fi
  fi
fi

echo "[2/6] Kích hoạt ZRAM ZSTD nén RAM (Ưu tiên 10) & Kernel Modules..."
mkdir -p /etc/modules-load.d
cat > /etc/modules-load.d/fleet-modules.conf <<'EOF_MODS'
zram
tcp_bbr
br_netfilter
nf_conntrack
tun
EOF_MODS

modprobe zram num_devices=1 2>/dev/null || true
modprobe tcp_bbr 2>/dev/null || true
modprobe br_netfilter 2>/dev/null || true
modprobe nf_conntrack 2>/dev/null || true
modprobe tun 2>/dev/null || true

if [[ ! -c /dev/net/tun ]]; then
  mkdir -p /dev/net 2>/dev/null || true
  mknod /dev/net/tun c 10 200 2>/dev/null || true
  chmod 600 /dev/net/tun 2>/dev/null || true
fi

cat > /usr/local/bin/fleet-init-zram.sh <<'EOF_ZRAM_SH'
#!/usr/bin/env bash
MEM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
ZRAM_BYTES=$(( MEM_MB * 1024 * 1024 ))
modprobe zram num_devices=1 2>/dev/null || true
if [[ ! -b /dev/zram0 ]] && [[ -f /sys/class/zram-control/hot_add ]]; then
  cat /sys/class/zram-control/hot_add >/dev/null 2>&1 || true
fi
if [[ -b /dev/zram0 ]]; then
  if ! swapon --show 2>/dev/null | grep -q "/dev/zram0"; then
    if grep -q "zstd" /sys/block/zram0/comp_algorithm 2>/dev/null; then
      echo zstd > /sys/block/zram0/comp_algorithm 2>/dev/null || true
    fi
    echo "$ZRAM_BYTES" > /sys/block/zram0/disksize 2>/dev/null || true
    mkswap /dev/zram0 >/dev/null 2>&1
    swapon -p 10 /dev/zram0 2>/dev/null || true
  fi
fi
EOF_ZRAM_SH
chmod +x /usr/local/bin/fleet-init-zram.sh
/usr/local/bin/fleet-init-zram.sh

if [[ -f /sys/kernel/mm/ksm/run ]]; then
  echo 1 > /sys/kernel/mm/ksm/run 2>/dev/null || true
  echo 1000 > /sys/kernel/mm/ksm/sleep_millisecs 2>/dev/null || true
  echo 300 > /sys/kernel/mm/ksm/pages_to_scan 2>/dev/null || true
fi

RPS_MASK=$(printf "%x" $(( (1 << CPU) - 1 )) 2>/dev/null || echo "f")
for f in /sys/class/net/*/queues/rx-*/rps_cpus; do
  echo "$RPS_MASK" > "$f" 2>/dev/null || true
done

echo "[3/6] Tối ưu hóa Kernel Sysctl & Chống rò rỉ IPv6..."
ulimit -n 1048576 2>/dev/null || true

cat << 'EOF_GAI' > /etc/gai.conf
precedence ::ffff:0:0/96  100
precedence ::/0           40
precedence 2002::/16      30
precedence ::/96          20
precedence ::1/128        50
EOF_GAI

cat > /etc/sysctl.d/99-fleet-core.conf << 'EOF_SYSCTL'
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.ip_forward = 1
vm.swappiness = 60
vm.vfs_cache_pressure = 100
vm.dirty_background_ratio = 5
vm.dirty_ratio = 15
fs.file-max = 2097152
fs.nr_open = 2097152
kernel.pid_max = 4194304
net.core.somaxconn = 65535
net.core.netdev_max_backlog = 65535
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.ipv4.tcp_max_syn_backlog = 32768
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 30
net.ipv4.tcp_keepalive_time = 180
net.ipv4.tcp_keepalive_intvl = 15
net.ipv4.tcp_keepalive_probes = 5
net.ipv4.tcp_slow_start_after_idle = 0
net.netfilter.nf_conntrack_max = 524288
net.netfilter.nf_conntrack_tcp_timeout_established = 600
net.netfilter.nf_conntrack_tcp_timeout_close_wait = 30
net.netfilter.nf_conntrack_tcp_timeout_fin_wait = 30
EOF_SYSCTL
sysctl --system >/dev/null 2>&1 || true

echo "[4/6] Cài đặt Docker & Khống chế Log tối đa 2MB..."
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

mkdir -p /etc/docker
cat > /etc/docker/daemon.json << 'EOF_DOCKER'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "2m", "max-file": "2" },
  "live-restore": true,
  "userland-proxy": false,
  "default-ulimits": {
    "nofile": { "Name": "nofile", "Hard": 65536, "Soft": 65536 }
  }
}
EOF_DOCKER
systemctl daemon-reload && systemctl reload docker 2>/dev/null || systemctl restart docker

echo "[5/6] Mở cổng Firewall tường lửa (${PORT})..."
iptables -I INPUT -p tcp --dport ${PORT} -j ACCEPT 2>/dev/null || true
iptables -P FORWARD ACCEPT 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then
  ufw allow ${PORT}/tcp >/dev/null 2>&1 || true
fi

echo "[6/6] Cài đặt Backend Python & Tạo Systemd Daemon..."
apt-get update -y -qq
apt-get install -y -qq python3 python3-pip python3-venv curl jq

mkdir -p "$APP_DIR/data"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install fastapi uvicorn psutil docker jinja2 pydantic -q

cat << EOF_ENV > "$APP_DIR/.env"
ADMIN_USER=${ADMIN_USER}
ADMIN_PASS=${ADMIN_PASS}
EOF_ENV
chmod 600 "$APP_DIR/.env"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/app.py" ]]; then
  cp "$SCRIPT_DIR/app.py" "$APP_DIR/app.py"
  cp "$SCRIPT_DIR/index.html" "$APP_DIR/index.html"
fi

cat << EOF_SERVICE > /etc/systemd/system/bandwidth-manager.service
[Unit]
Description=Bandwidth Fleet Manager Pro Engine
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/venv/bin/uvicorn app:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF_SERVICE

chmod 600 /etc/systemd/system/bandwidth-manager.service
systemctl daemon-reload
systemctl enable --now bandwidth-manager.service

PRIMARY_IFACE=$(ip -4 route show default 2>/dev/null | awk '{print $5}' | head -n1 || echo "eth0")
PUB_IP=$(curl -s4 -m 3 --interface "$PRIMARY_IFACE" https://api.ipify.org 2>/dev/null || curl -s4 -m 3 https://icanhazip.com 2>/dev/null || echo "IP_VPS")

echo "=========================================================================="
echo " [OK] ĐÃ THIẾT LẬP THÀNH CÔNG HẠ TẦNG VPS & BANDWIDTH FLEET MANAGER!"
echo " 👉 Địa chỉ Web Dashboard : http://${PUB_IP}:${PORT}"
echo " 👉 Tài khoản đăng nhập   : ${ADMIN_USER}"
echo " 👉 Mật khẩu bảo mật      : (Mật khẩu riêng bạn vừa nhập)"
echo " 👉 Đã bật: ZRAM ZSTD (Ưu tiên 10), Swapfile, BBR, KSM, Anti-Leak."
echo "=========================================================================="