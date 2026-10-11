# 🚀 Bandwidth Fleet Manager Pro (NextGen Dashboard)

Hệ thống quản trị hạm đội Proxy & Node chia sẻ băng thông (Bandwidth Sharing) chuyên nghiệp trên VPS Linux. Thiết kế chuyên sâu cho các nền tảng: **Traffmonetizer, Honeygain, IPRoyal Pawns, Repocket, Bitping**.

---

## 🌟 Điểm Vượt Trội Cốt Lõi

* **Kiến trúc 1 TUN = 1 Platform**: Mỗi proxy gắn kèm 1 container `tun2proxy` (Rust Engine) và 1 worker duy nhất. Thêm/sửa/xóa từng node độc lập 100%, không bao giờ làm gián đoạn các node đang chạy.
* **Khống chế Restart tối đa 3 lần (`on-failure:3`)**: Triệt tiêu hoàn toàn vòng lặp crash làm nghẽn máy và chống ban tài khoản dồn dập.
* **Staggered Auto-Boot**: Tự động phục hồi node từ từ sau khi VPS khởi động lại (1s delay mỗi node), không gây nghẽn CPU/Disk I/O.
* **Xem Log Trực Tiếp Trên Web**: Đọc trực tiếp 100 dòng log của container App và TUN ngay trên giao diện web để kiểm soát trạng thái.
* **Cập Nhật 1-Click (OTA Update)**: Bấm nút "Kiểm Tra Cập Nhật" trên giao diện để tự động đồng bộ code mới nhất từ GitHub mà không cần chạm vào terminal.
* **Đo Tải Mạng & Dung Lượng Zero-Probe**: Đo tốc độ mạng vật lý VPS Mbps, cảnh báo nghẽn mạng và đo số GB từng node trực tiếp từ Kernel Linux (`/proc/[PID]/net/dev`) mà không hao tốn 1 byte quota proxy.

---

## ⚡ Hướng Dẫn Cài Đặt 1-Click Trên VPS Mới

Chạy lệnh duy nhất sau trên VPS Ubuntu (22.04 / 24.04) hoặc Debian (11 / 12):

```bash
sudo rm -rf /opt/bandwidth-manager && sudo git clone https://github.com/tech-tnitechsolve/bandwidth-fleet-manager.git /opt/bandwidth-manager && cd /opt/bandwidth-manager && sudo bash install.sh
```

1. Màn hình terminal sẽ yêu cầu bạn nhập mật khẩu bảo mật riêng cho tài khoản `admin`.
2. Mở trình duyệt truy cập: `http://<IP_VPS>:3100` và đăng nhập để quản trị.

---

## 🛠️ Lệnh Cập Nhật Thủ Công Từ Terminal (Nếu cần)

```bash
cd /opt/bandwidth-manager && sudo git reset --hard origin/main && sudo systemctl restart bandwidth-manager
```
```

---

### CÁCH ĐỒNG BỘ NGAY LẬP TỨC TRÊN VPS HIỆN TẠI

Sau khi bạn commit 3 file trên vào kho GitHub của bạn, chạy **đúng 1 dòng lệnh này** trên VPS hiện tại để trải nghiệm ngay phiên bản `v1.3.0`:

```bash
cd /opt/bandwidth-manager && sudo git reset --hard origin/main && sudo systemctl restart bandwidth-manager && sudo systemctl status bandwidth-manager --no-pager
```

Mở lại trình duyệt tại `http://15.235.126.98:3100`:
* Bạn sẽ thấy giao diện hiển thị rõ **Nhiệt độ CPU, Swap chi tiết, Ổ đĩa chi tiết, và Điểm ổn định 100%**.
* Có nút **"Kiểm Tra Cập Nhật"** tự so khớp với GitHub.
* Mỗi node có thêm nút icon **Terminal** để mở hộp thoại xem log container theo thời gian thực!