#!/usr/bin/env bash
#============================================================================
#  pull_images.sh - Tải trước toàn bộ Image cho 7 nền tảng + tun2proxy
#============================================================================
set -eo pipefail

C_G='\033[1;32m'; C_Y='\033[1;33m'; C_R='\033[1;31m'; C_C='\033[1;36m'; C_0='\033[0m'
ARCH=$(uname -m)

echo -e "\n${C_C}=== [BẮT ĐẦU TẢI DOCKER IMAGES (CPU: ${ARCH})] ===${C_0}"

declare -a TARGET_IMAGES=(
  "ghcr.io/tun2proxy/tun2proxy:v0.8.3"
  "honeygain/honeygain:0.6.6"
  "iproyal/pawns-cli:latest"
  "repocket/repocket:latest"
  "bitping/bitpingd:latest"
  "earnfm/earnfm-client:latest"
  "packetstream/psclient:latest"
)

if [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]]; then
  TARGET_IMAGES+=("traffmonetizer/cli_v2:arm64v8")
else
  TARGET_IMAGES+=("traffmonetizer/cli_v2:latest")
fi

for img in "${TARGET_IMAGES[@]}"; do
  echo -ne " -> Đang kéo image: ${C_Y}${img}${C_0} ... "
  success=0
  for i in {1..3}; do
    if docker pull "$img" >/dev/null 2>&1; then
      echo -e "${C_G}[OK]${C_0}"
      success=1
      break
    else
      echo -ne "${C_Y}(Thử lại lần $i)...${C_0} "
      sleep 2
    fi
  done
  if (( success == 0 )); then
    echo -e "${C_R}[THẤT BẠI - Kiểm tra kết nối mạng VPS]${C_0}"
  fi
done

echo -e "${C_G}=== [HOÀN TẤT TẢI IMAGES CHO 7 NỀN TẢNG] ===${C_0}\n"