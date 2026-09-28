# Laporan UTS: Komparasi MQTT dan HTTP pada ESP32

Nama/kelompok: ...

Tanggal pengujian: ...

Proyek akhir kelompok dan kaitan sensor DHT22: ...

## 1. Arsitektur dan rangkaian

Lampirkan screenshot `wokwi/diagram.json` dalam editor Wokwi. Jelaskan
VCC -> 3V3, GND -> GND, SDA -> GPIO 15 dan perubahan nilai virtual DHT22.

## 2. Kode program

Lampirkan `wokwi/sketch.ino` untuk `USE_MQTT=1` dan `USE_MQTT=0`, serta
`server.py` dan `mosquitto.conf` sebagai kode penerima laptop.

## 3. Data dan analisis

Format JSON: `device_id`, `seq`, `suhu`, `kelembapan`, `sent_ms`.
`protokol` dan `waktu_diterima` ditambahkan oleh laptop.

| Mode | Jumlah sampel | Median RTT (ms) | Rentang RTT (ms) | Catatan |
| --- | ---: | ---: | ---: | --- |
| MQTT | ... | ... | ... | ... |
| HTTP | ... | ... | ... | ... |

- Mana yang lebih cepat pada pengujian ini? ...
- Mana yang lebih mudah dikoding bagi kelompok dan alasannya? ...
- Bagaimana struktur JSON dipakai pada kedua jalur? ...
- Batas pengukuran: RTT MQTT memakai ACK aplikasi; RTT HTTP memakai respons
  server. Keduanya termasuk perjalanan pulang dan pencatatan SQLite lokal.
  `waktu_diterima` tidak dapat sendiri mengukur latensi satu arah.

## 4. Bukti screenshot

- [ ] Diagram rangkaian ESP32 dan DHT22
- [ ] Serial Monitor mode MQTT, payload dan RTT
- [ ] Data masuk di terminal subscriber Python atau MQTT client laptop
- [ ] Serial Monitor mode HTTP, payload dan status 200
- [ ] Log `DITERIMA HTTP` pada Flask di laptop
- [ ] Tabel `sensor_readings` di Supabase Studio lokal (Docker) dengan kedua nilai `protokol`

## 5. Demo

Catat urutan demo: jalankan Mosquitto, `server.py`, Private Gateway,
simulasi MQTT dengan `RANDOM_SENSOR_MODE 0`, ubah DHT22, ganti mode HTTP,
lalu tunjukkan log dan Supabase. Jika memakai mode acak untuk demo dashboard,
beri label data tersebut sebagai simulasi acak.
