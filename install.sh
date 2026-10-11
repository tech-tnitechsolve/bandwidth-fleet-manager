#!/usr/bin/env bash
#============================================================================
#  install.sh - All-in-One Engine v1.6.0 (7 Platforms + tun2proxy Focused)
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
systemctl stop apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
pkill -9 -f "apt|dpkg" 2>/dev/null || true
rm -f /var/lib/apt/lists/lock /var/lib/dpkg/lock* 2>/dev/null || true
dpkg --configure -a 2>/dev/null || true

export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq
apt-get install -y -qq linux-modules-extra-"$(uname -r)" util-linux curl jq python3 python3-pip python3-venv 2>/dev/null || true

echo "[2/6] Kích hoạt Kernel Modules & Thiết bị TUN..."
modprobe tun 2>/dev/null || true
modprobe tcp_bbr 2>/dev/null || true
mkdir -p /dev/net 2>/dev/null || true
[[ -c /dev/net/tun ]] || mknod /dev/net/tun c 10 200 2>/dev/null || true
chmod 666 /dev/net/tun 2>/dev/null || true

echo "[3/6] Cài đặt Docker Engine..."
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

echo "[4/6] Cài đặt Backend Python & Systemd Service..."
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
ExecStart=${APP_DIR}/venv/bin/uvicorn app:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_SERVICE

chmod 600 /etc/systemd/system/bandwidth-manager.service
systemctl daemon-reload
systemctl enable --now bandwidth-manager.service

echo "[5/6] Mở cổng Firewall ${PORT}..."
iptables -I INPUT -p tcp --dport ${PORT} -j ACCEPT 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then ufw allow ${PORT}/tcp >/dev/null 2>&1 || true; fi

echo "[6/6] Tải trước toàn bộ 8 Docker Images cho hệ thống..."
if [[ -f "$APP_DIR/pull_images.sh" ]]; then
  bash "$APP_DIR/pull_images.sh" || true
fi

PRIMARY_IFACE=$(ip -4 route show default 2>/dev/null | awk '{print $5}' | head -n1 || echo "eth0")
PUB_IP=$(curl -s4 -m 3 --interface "$PRIMARY_IFACE" https://api.ipify.org 2>/dev/null || echo "IP_VPS")

echo "=========================================================================="
echo " [OK] ĐÃ THIẾT LẬP THÀNH CÔNG BANDWIDTH FLEET MANAGER PRO v1.6.0!"
echo " 👉 Địa chỉ Web Dashboard : http://${PUB_IP}:${PORT}"
echo " 👉 Tài khoản             : ${ADMIN_USER}"
echo " 👉 7 Nền tảng sẵn sàng   : Traffmonetizer, Honeygain, Pawns, Repocket, Bitping, EarnFM, PacketStream"
echo "=========================================================================="