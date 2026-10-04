# Laporan UTS: Komparasi MQTT dan HTTP pada ESP32

Nama/kelompok: belum diisi.

Tanggal pengujian ESP32/DHT22: belum tersedia.

Proyek akhir kelompok dan kaitan sensor DHT22: perlu diisi sesuai proyek kelompok.

**Status:** implementasi penerima dan dashboard telah diuji lokal. Hasil ESP32/DHT22
melalui Wokwi belum tersedia; angka uji node demo pada bagian 6 tidak digunakan
sebagai hasil eksperimen sensor.

## 1. Arsitektur dan rangkaian

ESP32 mengirim payload sensor melalui salah satu protokol setiap 2 detik.
Pada MQTT, Mosquitto laptop meneruskan pesan ke subscriber Python. Pada HTTP,
Flask menerima POST `/update`. Kedua jalur mencatat pembacaan di SQLite sebelum
mengirim ACK/respons. Worker terpisah menyalin pembacaan dan laporan RTT ke
Supabase lokal dalam Docker; kegagalan sinkronisasi mempertahankan backlog lokal.

Rangkaian pada `wokwi/diagram.json`: VCC DHT22 ke 3V3 ESP32, GND ke GND,
SDA ke GPIO 15. Screenshot editor Wokwi masih perlu dilampirkan.

## 2. Kode program dan identitas data

Firmware: `wokwi/sketch.ino`, sakelar `USE_MQTT=1` untuk MQTT dan `0` untuk HTTP.
Mode bawaan `RANDOM_SENSOR_MODE=0` membaca DHT22; mode `1` menghasilkan data acak.
Kode penerima: `server.py`, `dashboard_state.py`, dan `mosquitto.conf`.

Payload sensor: `device_id`, `session_id`, `source_mode`, `seq`, `suhu`,
`kelembapan`, `sent_ms`. Laptop menambahkan `event_id`, `protokol`, dan
`waktu_diterima`. Sesi baru dibuat ketika pengirim restart. Pengiriman ulang
identik pada perangkat/sesi/protokol/nomor urut sama mempertahankan satu baris.
Identitas yang dipakai ulang dengan data berbeda ditolak.

`source_mode` membedakan DHT22 (`dht22`), ESP32 acak (`random`), dan node demo
laptop (`demo`). Data lama tanpa metadata berlabel `unknown`; asalnya tidak
ditebak dari ID perangkat. Dashboard dapat memfilter sumber serta sesi.

## 3. Metode pengukuran dan hasil sensor

RTT MQTT diukur pengirim dari publish hingga ACK aplikasi diterima kembali.
RTT HTTP diukur dari POST hingga body respons 200 diterima dengan identitas
sesi/nomor urut yang cocok. Setelah waktu diambil, pengirim mengirim laporan
terpisah melalui `sensor/metrics` atau `/metrics`, lalu laptop menyimpannya pada
`delivery_attempts`. Pengiriman laporan metrik tidak dimasukkan ke RTT tersebut.

Status sukses memiliki RTT numerik; timeout/error memiliki RTT kosong. Pengukuran
kosong dikecualikan dari median/p95, sementara pengukuran nol yang valid tetap
masuk. RTT mencakup perjalanan pulang serta pencatatan SQLite; `sent_ms` dan
waktu UTC laptop berasal dari jam berbeda dan tidak boleh dikurangkan.

MQTT mempertahankan koneksi; HTTP membuka request baru tiap pembacaan. Catat
perbedaan ini sebagai bagian kondisi pengujian. Jam simulasi Wokwi dapat berjalan
berbeda dari waktu nyata. Laporan metrik bersifat best effort: dashboard hanya
menghitung laporan yang sampai. Gunakan Serial Monitor/log pengirim untuk
menghitung seluruh percobaan, termasuk laporan yang gagal terkirim.

| Mode DHT22/Wokwi | Sampel sukses | Median RTT | p95 RTT | Timeout/error | Status |
| --- | ---: | --- | --- | --- | --- |
| MQTT | ? | ? | ? | ? | Belum diuji langsung |
| HTTP | ? | ? | ? | ? | Belum diuji langsung |

Prosedur pengisian hasil:

1. Jalankan stack lokal, penerima, dan Private IoT Gateway.
2. Gunakan `RANDOM_SENSOR_MODE=0`, jalankan MQTT, dan catat ID sesi.
3. Ambil minimal 30 sampel sukses; simpan Serial Monitor dan CSV RTT.
4. Ulangi untuk HTTP pada interval/kondisi jaringan sama; catat sesi HTTP.
5. Pilih sumber DHT22, pisahkan sesi uji, dan abaikan sampel pemanasan pertama
   per sesi saat menghitung median/p95/rentang. CSV tetap menyertakan sampel itu.
6. Isi tabel dan jawab protokol mana lebih cepat pada pengujian ini serta mana
   yang lebih mudah diimplementasikan, berdasarkan hasil dan pengalaman kelompok.

## 4. Bukti pengujian sensor yang masih diperlukan

- [ ] Diagram rangkaian ESP32 dan DHT22 dalam editor Wokwi
- [ ] Serial Monitor MQTT dengan payload, sesi, ACK, dan RTT
- [ ] Log penerimaan MQTT pada subscriber Python laptop
- [ ] Serial Monitor HTTP dengan payload, sesi, respons 200, dan RTT
- [ ] Log penerimaan HTTP pada Flask laptop
- [ ] Tabel `sensor_readings` dan `delivery_attempts` berisi hasil DHT22 kedua protokol
- [ ] CSV hasil, perhitungan statistik, serta catatan timeout/error dari pengirim

## 5. Urutan demo

Jalankan Mosquitto, Supabase Docker, migrasi lokal, `server.py`, dan Private
Gateway. Jalankan simulasi MQTT, ubah slider DHT22, dan tunjukkan penerimaan,
RTT, SQLite, serta Supabase. Ganti ke HTTP, mulai ulang simulasi, lalu ulangi.
Filter DHT22 memisahkan bukti sensor dari data acak/demo. Detail perintah dan
batas koneksi Wokwi tersedia di `README.md`.

## 6. Validasi implementasi lokal ? 4 Oktober 2026

Pemeriksaan otomatis lulus: **26 tes Python**, **6 tes JavaScript**, pemeriksaan
sintaks dashboard/helper browser, dan `git diff --check`. Tes mencakup retry
bersamaan, konflik identitas, migrasi schema lama, konsistensi sumber,
RTT/timeout, kegagalan unggah, probe saat backlog kosong, dan pemulihan SSE.

Uji socket nyata menggunakan Mosquitto, Flask, dan SQLite sementara menerima
**12 pembacaan dan 12 laporan RTT**, masing-masing enam MQTT/HTTP. Pengiriman
ulang HTTP mempertahankan baris asli. Sumbernya **demo lokal**, sesi
`a74773b51e224222adbc0dc605fe282f`; interval 0,1 detik untuk pemeriksaan integrasi.

| Protokol demo lokal | Sampel | Median RTT (ms) | Rentang RTT (ms) |
| --- | ---: | ---: | ---: |
| MQTT | 6 | 40.58 | 39.75?44.61 |
| HTTP | 6 | 41.95 | 21.80?58.40 |

Angka di atas memakai seluruh enam sampel, termasuk sampel pertama, dan hanya
membuktikan integrasi lokal. Angka ini tidak cukup untuk menyimpulkan performa
ESP32/DHT22 atau keunggulan umum suatu protokol.

Sinkronisasi nyata ke Supabase lokal lulus untuk 12 pembacaan dan 12 metrik;
fixture sementara dibersihkan berdasarkan UUID yang sebelumnya belum ada.
Migrasi SQLite dan Supabase mempertahankan **298 pembacaan historis**; tabel
metrik historis kosong. Backup SQLite: `data/backups/sensor-before-measurement-sessions.db`.

Browser headless Chrome lulus pada desktop 1440?1200 dan mobile 390?844:
filter sumber/sesi, filter protokol, ekspor CSV RTT, dan tidak ada overflow
horizontal pada halaman. Font sistem dipakai untuk pemeriksaan offline.

Artefak lokal:

- `data/validation/local-smoke.json`: hasil pemeriksaan dan identitas sesi.
- `data/validation/local-rtt.csv`: laporan RTT node demo.
- `data/validation/dashboard-desktop.png` dan `dashboard-mobile.png`.
- `data/validation/dashboard-rtt-desktop.png` dan `dashboard-rtt-mobile.png`.

Firmware belum dikompilasi atau dijalankan langsung di Wokwi dalam sesi ini.
Bukti pada bagian 4 tetap perlu diambil melalui simulasi/perangkat pengirim.
