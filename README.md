# 🚀 Bandwidth Fleet Manager Pro (2026 Master)

Hệ thống quản trị hạm đội Proxy & Node chia sẻ băng thông (Bandwidth Sharing) chuyên nghiệp trên VPS Linux. Thiết kế chuyên sâu cho các nền tảng **Traffmonetizer, Honeygain, IPRoyal Pawns, Repocket, Bitping**.

---

## 🌟 Điểm Vượt Trội So Với Mã Nguồn Cũ

* **Kiến trúc 1 TUN = 1 Platform**: Mỗi proxy gắn kèm 1 container `tun2proxy` (Rust Engine) và 1 worker duy nhất. Thêm/sửa/xóa từng node độc lập 100%, không bao giờ làm gián đoạn các node đang chạy.
* **Bypass triệt để Cổng 53**: Sử dụng cơ chế Fake-IP Virtual DNS trong RAM. Không gửi bất kỳ gói tin DNS cổng 53 nào qua Proxy, vượt qua 100% chính sách chặn cổng của các nhà cung cấp Proxy dân cư/ISP.
* **Zero-External-Probe (0 Byte Hao Tốn)**: Đo đạc lưu lượng thực tế (GB) và số TCP Socket trực tiếp từ Kernel Linux (`/proc/[PID]/net/dev`). Không gửi request probe ra ngoài Internet, bảo toàn dung lượng tuyệt đối cho Proxy dạng IP-Authentication (Whitelist IP).
* **Check Live Trực Tiếp Từ IP Gốc**: Cơ chế kiểm tra trạng thái sống/chết và đo Ping (ms) của Proxy trực tiếp từ card mạng vật lý của VPS bằng luồng đa nhiệm tốc độ cao, không gây nóng CPU.
* **Chống Khóa Tài Khoản (Anti-Ban / Anti-Sybil)**:
  * Tự động sinh `machine-id` riêng biệt cho từng container (mỗi node là một máy tính vật lý độc lập).
  * Độ trễ an toàn 1.5s giữa TUN và App để triệt tiêu lỗi sập socket lúc khởi động.
  * Tắt App trước, tắt TUN sau khi dừng container để không đứt gãy kết nối.
* **Tối Ưu Hóa Hạ Tầng Phần Cứng**: Tự động chia Swapfile theo RAM, nén ZRAM ZSTD (Ưu tiên 10), kích hoạt BBR, KSM, mở firewall cổng 3100.

---

## ⚡ Hướng Dẫn Cài Đặt 1-Click Trên VPS Mới

Chạy lệnh duy nhất sau trên VPS Ubuntu (22.04 / 24.04) hoặc Debian (11 / 12):

```bash
sudo rm -rf /opt/bandwidth-manager && sudo git clone https://github.com/tech-tnitechsolve/bandwidth-fleet-manager.git /opt/bandwidth-manager && cd /opt/bandwidth-manager && sudo bash install.sh