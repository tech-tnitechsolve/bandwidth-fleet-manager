#!/usr/bin/env bash
#============================================================================
#  install.sh - All-in-One Engine v1.6.0 (High-Concurrency Edition)
#  Hỗ trợ chuẩn 7 nền tảng: Traffmon, Honeygain, Pawns, Repocket, Bitping, EarnFM, PacketStream
#============================================================================
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "[XX] Cần chạy quyền root: sudo bash $0"; exit 1; }

APP_DIR="/opt/bandwidth-manager"
PORT=3100
ADMIN_USER="admin"

echo "=========================================================================="
echo "    BẢO MẬT WEB DASHBOARD QUẢN TRỊ (BANDWIDTH FLEET MANAGER PRO v1.6.0)"
echo "=========================================================================="
ADMIN_PASS=""
while true; do
  read -s -p ">> Nhập MẬT KHẨU quản trị mới cho tài khoản 'admin': " P1
  echo ""
  if [[ -z "$P1" ]] || [[ ${#P1} -lt 6 ]]; then
    echo "[!] Mật khẩu tối thiểu 6 ký tự. Nhập lại!"
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

echo -e "\n[1/6] Dọn khóa APT & Cài đặt gói Kernel Modules..."
systemctl stop apt-daily.service apt-daily-upgrade.service unattended-upgrades.service 2>/dev/null || true
systemctl disable apt-daily.service apt-daily-upgrade.service unattended-upgrades.service 2>/dev/null || true
pkill -9 -f "apt|dpkg" 2>/dev/null || true
rm -f /var/lib/apt/lists/lock /var/lib/dpkg/lock* /var/cache/apt/archives/lock* 2>/dev/null || true
dpkg --configure -a 2>/dev/null || true

export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq
apt-get install -y -qq linux-modules-extra-"$(uname -r)" util-linux e2fsprogs curl jq python3 python3-pip python3-venv 2>/dev/null || true

echo "[2/6] Kích hoạt Kernel Modules & Cấu hình /dev/net/tun..."
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
modprobe tun 2>/dev/null || true

mkdir -p /dev/net 2>/dev/null || true
[[ -c /dev/net/tun ]] || mknod /dev/net/tun c 10 200 2>/dev/null || true
chmod 666 /dev/net/tun 2>/dev/null || true

echo "[3/6] Tối ưu hóa Kernel Sysctl & Giới hạn File Descriptors (1M nofile)..."
ulimit -n 1048576 2>/dev/null || true
mkdir -p /etc/security/limits.d
cat > /etc/security/limits.d/99-fleet-nofile.conf << 'EOF_LIMITS'
* soft nofile 1048576
* hard nofile 1048576
root soft nofile 1048576
root hard nofile 1048576
EOF_LIMITS

cat > /etc/sysctl.d/99-fleet-core.conf << 'EOF_SYSCTL'
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.ip_forward = 1
vm.swappiness = 60
fs.file-max = 2097152
kernel.pid_max = 4194304
net.core.somaxconn = 65535
net.netfilter.nf_conntrack_max = 524288
EOF_SYSCTL
sysctl --system >/dev/null 2>&1 || true

echo "[4/6] Cài đặt Docker & Mở cổng Firewall (${PORT})..."
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

iptables -I INPUT -p tcp --dport ${PORT} -j ACCEPT 2>/dev/null || true
iptables -P FORWARD ACCEPT 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then ufw allow ${PORT}/tcp >/dev/null 2>&1 || true; fi

echo "[5/6] Cài đặt Backend Python & Tạo Systemd Daemon..."
mkdir -p "$APP_DIR/data"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install fastapi uvicorn psutil docker jinja2 pydantic -q

JWT_SECRET=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)
cat << EOF_ENV > "$APP_DIR/.env"
ADMIN_USER=${ADMIN_USER}
ADMIN_PASS=${ADMIN_PASS}
JWT_SECRET=${JWT_SECRET}
EOF_ENV
chmod 600 "$APP_DIR/.env"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$SCRIPT_DIR" != "$APP_DIR" ]] && [[ -f "$SCRIPT_DIR/app.py" ]]; then
  cp -f "$SCRIPT_DIR/app.py" "$APP_DIR/app.py"
  cp -f "$SCRIPT_DIR/index.html" "$APP_DIR/index.html"
  cp -f "$SCRIPT_DIR/pull_images.sh" "$APP_DIR/pull_images.sh" 2>/dev/null || true
fi

cat << EOF_SERVICE > /etc/systemd/system/bandwidth-manager.service
[Unit]
Description=Bandwidth Fleet Manager Pro Engine v1.6.0
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
LimitNOFILE=1048576
LimitNPROC=524288
ExecStart=${APP_DIR}/venv/bin/uvicorn app:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_SERVICE

chmod 600 /etc/systemd/system/bandwidth-manager.service
systemctl daemon-reload
systemctl enable --now bandwidth-manager.service

echo "[6/6] Tải trước 8 Docker Images..."
if [[ -f "$APP_DIR/pull_images.sh" ]]; then
  chmod +x "$APP_DIR/pull_images.sh"
  bash "$APP_DIR/pull_images.sh" || true
fi

PRIMARY_IFACE=$(ip -4 route show default 2>/dev/null | awk '{print $5}' | head -n1 || echo "eth0")
PUB_IP=$(curl -s4 -m 3 --interface "$PRIMARY_IFACE" https://api.ipify.org 2>/dev/null || echo "IP_VPS")

echo "=========================================================================="
echo " [OK] ĐÃ THIẾT LẬP THÀNH CÔNG BANDWIDTH FLEET MANAGER PRO v1.6.0!"
echo " 👉 Địa chỉ Web Dashboard : http://${PUB_IP}:${PORT}"
echo " 👉 Tài khoản đăng nhập   : ${ADMIN_USER}"
echo " 👉 Mật khẩu bảo mật      : (Mật khẩu riêng bạn vừa nhập)"
echo " 👉 Đầy đủ 7 Platforms     : Traffmon, Honeygain, Pawns, Repocket, Bitping, EarnFM, PacketStream"
echo "=========================================================================="