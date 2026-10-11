#!/usr/bin/env bash
#============================================================================
#  reset_password.sh - Khôi phục mật khẩu quản trị Web Dashboard qua Terminal
#============================================================================
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "[XX] Cần chạy quyền root: sudo bash $0"; exit 1; }

ENV_FILE="/opt/bandwidth-manager/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[XX] Không tìm thấy file cấu hình tại $ENV_FILE"
  exit 1
fi

NEW_PASS="${1:-}"

if [[ -z "$NEW_PASS" ]]; then
  echo "=========================================================================="
  echo "         ĐẶT LẠI MẬT KHẨU QUẢN TRỊ (BANDWIDTH FLEET MANAGER)"
  echo "=========================================================================="
  while true; do
    read -s -p ">> Nhập MẬT KHẨU MỚI cho tài khoản 'admin': " P1
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
      NEW_PASS="$P1"
      break
    fi
  done
fi

sed -i "s|^ADMIN_PASS=.*|ADMIN_PASS=${NEW_PASS}|" "$ENV_FILE"
systemctl restart bandwidth-manager

echo "[OK] Đã đặt lại mật khẩu thành công! Hãy đăng nhập lại trên trình duyệt."