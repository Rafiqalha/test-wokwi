# UTS IoT: MQTT vs HTTP (ESP32 Wokwi + DHT22)

Proyek ini menyiapkan dua skenario dari PDF tugas. Pada keduanya, ESP32 Wokwi
mengirim data sensor dan laptop menerima data terlebih dahulu. Laptop menjalankan
Mosquitto sebagai broker MQTT dan Flask sebagai server HTTP. Penerima menyimpan
log lokal ke SQLite, lalu mengirim salinan ke Supabase lokal dalam Docker. Supabase bukan broker
atau server HTTP untuk ESP32.

```text
MQTT: ESP32 -> Mosquitto laptop -> subscriber Python laptop -> SQLite -> Supabase Docker laptop
HTTP: ESP32 -> Flask laptop -> SQLite -> Supabase Docker laptop
```

## Syarat dari PDF dan pilihan implementasi

PDF meminta mikrokontroler dengan sensor sesuai proyek akhir kelompok, dua
protokol yang dijalankan bergantian, log data, diagram rangkaian, kode, analisis,
screenshot, demo dan presentasi. DHT22 virtual serta Supabase adalah pilihan
implementasi untuk proyek ini. Jika sensor proyek akhir kelompok berbeda,
sesuaikan sensor dan field datanya sebelum dikumpulkan.

## Rangkaian

Lihat [`wokwi/diagram.json`](wokwi/diagram.json): DHT22 VCC ke 3V3, GND ke GND,
SDA ke GPIO 15. Pada mode `RANDOM_SENSOR_MODE 0`, sensor bisa diklik untuk
mengubah suhu dan kelembapan. Pada mode acak, DHT22 tetap tergambar tetapi
nilainya tidak dibaca. Ambil screenshot diagram sebagai bukti rangkaian.

## Persiapan laptop (PowerShell)

1. Pasang Mosquitto dan jalankan dengan konfigurasi proyek:

   ```powershell
   mosquitto -c .\mosquitto.conf -v
   ```

   Jalankan di terminal terpisah. Konfigurasi ini hanya mendengar pada
   `127.0.0.1:1883` dan mengizinkan klien tanpa kata sandi untuk demo lokal.

2. Buat virtual environment dan pasang dependensi:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Jalankan Supabase lokal lewat Docker Desktop. Supabase CLI terpasang sebagai
   dependensi proyek. [Migration tabel](supabase/migrations/20260928000000_sensor_readings.sql)
   dijalankan otomatis ketika stack dimulai. Port API 55421, database 55422,
   dan Studio 55423 berbeda dari Mosquitto (1883) dan Flask (5000).

   ```powershell
   npm.cmd ci
   docker network inspect wokwi-uts-loopback *> $null
   if ($LASTEXITCODE -ne 0) {
     docker network create --driver bridge --opt com.docker.network.bridge.host_binding_ipv4=127.0.0.1 wokwi-uts-loopback
   }
   $env:SUPABASE_HOME = Join-Path (Get-Location) '.supabase-home'
   $env:SUPABASE_TELEMETRY_DISABLED = '1'
   npx.cmd supabase start --network-id wokwi-uts-loopback -x gotrue,realtime,storage-api,imgproxy,mailpit,edge-runtime,logflare,vector,supavisor
   npx.cmd supabase migration up --local
   .\.venv\Scripts\python.exe scripts\configure_local.py
   ```

   Perintah di atas membuat network hanya jika belum ada. Script konfigurasi mengambil
   URL API dan secret key dari stack yang berjalan dan menaruhnya di `.env`
   tanpa mencetak kunci. Berkas ini diabaikan Git. Buka Studio di
   `http://127.0.0.1:55423` untuk memeriksa tabel.

4. Jalankan penerima laptop di terminal lain:

   ```powershell
   .\.venv\Scripts\python.exe server.py
   ```

   Flask mendengar di `127.0.0.1:5000`. Satu program ini sekaligus menjadi
   subscriber MQTT. Setiap baris `DITERIMA MQTT`/`DITERIMA HTTP` adalah waktu
   data sampai ke program laptop, sebelum sinkronisasi Supabase lokal.

## Dashboard real-time

Setelah `server.py` berjalan, buka `http://127.0.0.1:5000/` di browser laptop.
Dashboard ini memakai HTML, CSS, dan JavaScript tanpa framework frontend.
Font Montserrat dimuat dari Google Fonts bila internet tersedia, dengan fallback
font sistem saat offline. Flask hanya menyajikan halaman, snapshot baca-saja di
`/api/dashboard`, dan notifikasi Server-Sent Events di `/api/stream`. Browser
mengambil ulang snapshot saat ada pembacaan atau perubahan status sinkronisasi;
refresh cadangan berjalan setiap 5 detik. Kunci Supabase tetap di proses laptop.

Grafik menampilkan tren suhu/kelembapan, pesan MQTT/HTTP, dan waktu tulis
SQLite (p50/p95). Panel RTT menampilkan median/p95 serta timeout/error dari
laporan pengirim. Nilai pengukuran yang belum tersedia tidak dihitung sebagai
nol. Filter sumber membedakan `dht22`, `random`, `demo`, dan `unknown`; filter
sesi membatasi perangkat dan sesi sebelum 900 baris terbaru dimuat dari SQLite.
Rentang waktu/protokol memfilter grafik, event log, dan ekspor. CSV pembacaan
menyertakan sumber, sesi, dan RTT; tombol **Ekspor RTT** menyertakan laporan
sukses maupun kegagalan, termasuk percobaan yang tidak memiliki pembacaan.
Total/backlog menghitung seluruh riwayat sumber/sesi terpilih, termasuk baris
di luar jendela grafik. Maksimal 900 pembacaan dan 900 laporan RTT dimuat.

Klik event untuk melihat sesi, sumber, RTT, commit SQLite, dan sinkronisasi.
Supabase diperiksa berkala (sekitar 10 detik saat tidak ada backlog), termasuk
ketika tidak ada unggahan. Metrik mempunyai backlog sinkronisasi terpisah.

Status `LIVE SENSOR` berarti ada pembacaan dalam 20 detik terakhir pada
sumber/sesi terpilih. Pilihan **Dimuat** membuka riwayat; pilihan 15 menit,
1 jam, atau 6 jam hanya menampilkan data pada rentang tersebut. Membuka browser
tidak menghasilkan pembacaan. Jalankan Wokwi atau node demo di terminal lain:

```powershell
.\.venv\Scripts\python.exe scripts\sensor_node.py --protocol both
```

Node demo tersebut menghasilkan nilai acak setiap 2 detik dan mengirimnya
bergantian melalui MQTT serta HTTP. Laptop tetap harus menjalankan Mosquitto
dan `server.py`. Jika Mosquitto belum berjalan, gunakan `--protocol http`.
Hentikan node demo dengan Ctrl+C. Datanya memakai `device_id=demo-node-01`
dan ikut tersimpan di SQLite/Supabase; jangan gunakan sebagai bukti pengukuran
ESP32/DHT22 dalam laporan. Untuk melihat data langsung dari ESP32, salin ulang
sketch terbaru ke Wokwi, jalankan simulasi dan Private IoT Gateway.

Waktu tulis lokal dimulai saat penyimpanan lokal dipanggil dan berakhir
setelah commit awal ke SQLite. Ini bukan latensi satu arah ESP32 ke laptop. Waktu
request Supabase adalah durasi unggah batch, bukan durasi tiap pembacaan.
Jika `server.py` sudah berjalan sebelum kode dashboard ditambahkan, hentikan
proses itu dengan Ctrl+C dan jalankan kembali agar route baru aktif.

## Menjalankan Wokwi

1. Buat proyek ESP32 Arduino di Wokwi. Salin isi `wokwi/sketch.ino`,
   `wokwi/diagram.json`, dan `wokwi/libraries.txt` ke file dengan nama sama
   dalam proyek Wokwi. Jika perlu, tambahkan library melalui Library Manager:
   **DHT sensor library for ESPx** dan **PubSubClient**.
2. Jalankan aplikasi **Wokwi Private IoT Gateway** pada laptop. Di editor Wokwi,
   tekan **F1** lalu pilih **Enable Private Wokwi IoT Gateway**. Pastikan log
   gateway menampilkan koneksi klien saat simulasi dimulai. Gunakan Chrome,
   Edge, atau Firefox. `host.wokwi.internal` di sketch menunjuk ke laptop.
3. Untuk MQTT, biarkan `#define USE_MQTT 1` lalu mulai simulasi. Amati Serial
   Monitor, terminal `server.py`, dan bila perlu `mosquitto_sub -h 127.0.0.1
   -t sensor/dht22 -v` di terminal tambahan.
4. Secara bawaan `RANDOM_SENSOR_MODE 0`: ESP32 membaca DHT22 virtual setiap
   2 detik. Klik DHT22 dan ubah slider suhu/kelembapan. Untuk demo acak,
   ubah mode menjadi `1`; payload akan berlabel `source_mode=random`.
   Setiap restart membuat `session_id` baru. Catat sesi MQTT dan HTTP.
5. Untuk HTTP, ubah `#define USE_MQTT 0`, mulai ulang simulasi, dan amati
   Serial Monitor serta terminal `server.py`. Kedua protokol tetap dijalankan
   bergantian agar hasilnya bisa dibandingkan.
6. Periksa tabel `public.sensor_readings` dan `public.delivery_attempts` di Supabase Studio lokal. Kolom `protokol`
   menunjukkan jalurnya dan `waktu_diterima` berasal dari laptop. Bila Docker
   gagal diakses, data tetap ada di `data/sensor.db` dan akan dicoba ulang
   selama program masih berjalan. Gunakan `npx.cmd supabase stop` untuk
   menghentikan stack tanpa menghapus data lokal.

**Catatan laporan:** mode acak berguna untuk menguji aliran realtime dan UI.
Untuk bukti pengukuran sensor DHT22 sesuai tugas, jalankan juga mode
`RANDOM_SENSOR_MODE 0` dan pisahkan hasilnya dari data acak. Tanpa simulasi
Wokwi yang aktif atau perangkat pengirim lain, dashboard tidak membuat
pembacaan sendiri.

**Batas Wokwi:** Public Gateway tidak menjangkau jaringan lokal. Private IoT
Gateway memerlukan paket Wokwi berbayar. Tanpanya, simulasi ESP32 di browser
tidak dapat mendemokan koneksi ke Mosquitto/Flask pada laptop. Untuk demo tanpa
paket itu, gunakan ESP32 fisik pada jaringan laptop atau minta persetujuan
dosen untuk arsitektur demo yang berbeda.

## Format data dan pengukuran

Payload sama untuk kedua protokol, misalnya:

```json
{"device_id":"esp32-01","session_id":"a1b2c3d4e5f60708","source_mode":"dht22","seq":1,"suhu":29.4,"kelembapan":71.2,"sent_ms":5200}
```

`sent_ms` adalah `millis()` pada ESP32 simulasi. Jangan kurangkan nilai ini
dari `waktu_diterima` UTC pada laptop: kedua jam berbeda. `waktu_diterima`
berguna untuk mengurutkan bukti penerimaan dan dihitung sebelum operasi
penyimpanan. Serial Monitor menampilkan RTT dalam ms:

- MQTT: dari `publish` sampai ESP32 menerima ACK aplikasi dari laptop.
- HTTP: dari POST sampai body respons 200 diterima, dengan sesi/nomor urut yang cocok.

Keduanya dicatat sesudah log SQLite lokal dibuat, tetapi sebelum sinkronisasi
Supabase. Gunakan beberapa puluh sampel per mode, abaikan koneksi pertama,
bandingkan median dan rentang RTT, serta laporkan bahwa RTT mencakup perjalanan
pulang dan penanganan di laptop. Ini bukan pengukuran satu arah yang murni.
Kecepatan simulasi Wokwi juga dapat memengaruhi `millis()` terhadap waktu nyata.

Pembacaan baru dideduplikasi berdasarkan `(device_id, session_id, protokol,
seq)`. Pengiriman ulang data identik mengembalikan ACK dan waktu penerimaan
pertama; identitas sama dengan isi berbeda ditolak. Format lama tanpa sesi/sumber
masih diterima sebagai `unknown` dan tidak dideduplikasi. Migrasi tidak menebak
sumber data lama dari `device_id` dan tidak mengisi pengukuran historis.

Setelah ACK/respons diterima, pengirim melaporkan hasil secara terpisah melalui
topik MQTT `sensor/metrics` atau HTTP `POST /metrics`:

```json
{"device_id":"esp32-01","session_id":"a1b2c3d4e5f60708","source_mode":"dht22","seq":1,"protokol":"MQTT","status":"ok","rtt_ms":42}
```

Status `timeout`/`error` memakai `rtt_ms=null`. Laporan disimpan pada
`delivery_attempts`, dicocokkan ke pembacaan berdasarkan identitas, dan disalin
ke Supabase. Laporan ulang identik tidak menambah sampel. Laporan ini dikirim
setelah waktu RTT diambil, sehingga durasi pengiriman metrik tidak masuk RTT.
Pengiriman metrik bersifat best effort: jika laporan juga gagal terkirim,
percobaan tersebut tidak tercatat di dashboard. Hitung kegagalan lengkap dari
Serial Monitor/log pengirim, bukan dari dashboard saja.

Pengujian ini membandingkan koneksi MQTT yang dipertahankan dengan request HTTP
baru pada setiap pembacaan. RTT dapat mencakup pembentukan koneksi HTTP serta
penanganan SQLite. Pilih satu sumber, gunakan kondisi jaringan dan interval sama,
ambil minimal 30 sampel sukses per protokol, dan abaikan sampel pemanasan pertama
per sesi saat menghitung hasil laporan. Dua protokol dijalankan dalam sesi
berbeda; filter **Semua sesi** + **DHT22** dapat menampilkan keduanya. CSV tetap
mengekspor sampel pemanasan sehingga penghapusan sampel harus dijelaskan.

## Validasi lokal dan pembaruan database

Untuk stack Supabase yang sudah ada, jalankan migrasi tambahan tanpa reset:

```powershell
$env:SUPABASE_HOME = Join-Path (Get-Location) '.supabase-home'
npx.cmd supabase migration up --local
```

Restart `server.py` untuk memigrasikan SQLite secara otomatis. Simpan backup
SQLite sebelum menjalankan versi baru pada log penting. Jalankan tes:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
node --test tests/test_dashboard.cjs
node --check dashboard/app.js
```

Untuk pemeriksaan MQTT/HTTP melalui socket nyata, gunakan broker/database
sementara. Perintah berikut tidak menulis ke `data/sensor.db` dan tidak
mengirim ke Supabase atau Wokwi:

```powershell
.\.venv\Scripts\python.exe -B scripts\smoke_local.py --mosquitto D:\Mosquitto\mosquitto.exe
```

Sesuaikan path broker. Opsi `--browser "C:\Program Files\Google\Chrome\Application\chrome.exe"`
menambahkan pemeriksaan browser headless. Hasil lokal tersimpan di
`data/validation/local-smoke.json` dan `local-rtt.csv`; hasil ini adalah bukti
integrasi node demo, bukan bukti ESP32/DHT22 atau hasil tugas. Tambahkan
`--supabase` untuk menguji sinkronisasi ke stack lokal dari `.env`. Opsi ini
mengunggah fixture demo sementara dan membersihkan hanya UUID fixture yang
terbukti belum ada sebelum pengujian. Data historis tetap dipertahankan.
Browser diperiksa dengan font sistem agar pemeriksaan tidak membutuhkan Google Fonts.

## Bukti laporan

Gunakan [`LAPORAN.md`](LAPORAN.md) sebagai kerangka. Isi angka dan screenshot
dari sesi simulasi yang benar-benar dijalankan. Kode lengkap untuk kedua mode
ada dalam satu sketch dengan sakelar `USE_MQTT`; kode laptop ada di `server.py`.

## Rujukan teknis

- [Wokwi: ESP32 WiFi dan Private IoT Gateway](https://docs.wokwi.com/guides/esp32-wifi)
- [Wokwi: DHT22 dan library ESP32](https://docs.wokwi.com/parts/wokwi-dht22)
- [Supabase: API keys](https://supabase.com/docs/guides/getting-started/api-keys)
- [Supabase CLI: pengembangan lokal dengan Docker](https://supabase.com/docs/guides/local-development/cli/getting-started)
