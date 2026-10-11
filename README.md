# 🚀 Bandwidth Fleet Manager Pro v1.6.0

Hệ thống Web GUI quản trị hạm đội Proxy & Node chia sẻ băng thông (Bandwidth Sharing) chuyên nghiệp trên VPS Linux. Thiết kế chuyên sâu và tối ưu hóa 100% cho đúng 7 nền tảng:
**Traffmonetizer, Honeygain, IPRoyal Pawns, Repocket, Bitping, EarnFM, PacketStream**.

---

## 🌟 Điểm Vượt Trội Cốt Lõi

* **Kiến trúc 1 TUN = 1 Platform**: Mỗi proxy gắn kèm 1 container `tun2proxy` (Rust Engine) và 1 worker duy nhất. Thêm/sửa/xóa từng node độc lập 100%, không bao giờ làm gián đoạn các node đang chạy.
* **Bảng Cài Đặt & System Audit**: Tự động rà soát thông số BBR, ZRAM ZSTD, Swap, Firewall, `/dev/net/tun`. Cho phép đặt tên định danh cho VPS (quản lý hàng trăm VPS dễ dàng), đổi mật khẩu trực tiếp trên Web.
* **Cập Nhật OTA 1-Click An Toàn**: Bấm "Check Update" trên Web để tự kéo commit mới từ GitHub mà **không làm ngắt kết nối container đang chạy** và **không tốn 1 byte quota proxy**.
* **Khống chế Restart tối đa 3 lần (`on-failure:3`)**: Triệt tiêu hoàn toàn vòng lặp crash làm nghẽn máy và chống ban tài khoản dồn dập.
* **Staggered Auto-Boot**: Tự động phục hồi node từ từ sau khi VPS khởi động lại (1s delay mỗi node), không gây nghẽn CPU/Disk I/O.
* **Xem Log Trực Tiếp Trên Web**: Đọc trực tiếp 100 dòng log của container App và TUN ngay trên giao diện web.
* **Đo Tải Mạng & Dung Lượng Zero-Probe**: Đo tốc độ mạng vật lý VPS Mbps, cảnh báo nghẽn mạng và đo số GB từng node trực tiếp từ Kernel Linux (`/proc/[PID]/net/dev`).


---

## ⚡ Hướng Dẫn Cài Đặt 1-Click Trên VPS Mới

Chạy lệnh duy nhất sau trên VPS Ubuntu (22.04 / 24.04) hoặc Debian (11 / 12):

```
sudo rm -rf /opt/bandwidth-manager && sudo git clone https://github.com/tech-tnitechsolve/bandwidth-fleet-manager.git /opt/bandwidth-manager && cd /opt/bandwidth-manager && sudo bash install.sh
```

---
## 🔑 Quên Mật Khẩu Đăng Nhập?

Nếu quên mật khẩu Web Dashboard, SSH vào VPS và chạy lệnh:
```
sudo bash /opt/bandwidth-manager/reset_password.sh
```