-- Query analisis telemetri. Halaman /database menjalankan file ini dalam
-- koneksi baca-saja (pragma query_only) dan menampilkan hasilnya.

-- judul: Query 1 - Pembacaan terbaru satu perangkat dalam 1 jam terakhir
-- tujuan: Memilih kolom, memfilter perangkat dan rentang waktu, lalu mengurutkan data terbaru. Batas waktu dibuat dalam format UTC yang sama sehingga indeks (device_id, waktu_diterima) dapat dipakai.
SELECT waktu_diterima, protokol, seq, suhu, kelembapan
FROM readings
WHERE device_id = 'demo-node-01'
  AND waktu_diterima >= strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '-1 hour')
ORDER BY waktu_diterima DESC
LIMIT 10;

-- judul: Query 2 - Agregasi suhu dan kelembapan per jam
-- tujuan: Meringkas data mentah per jam dengan COUNT, AVG, MIN, dan MAX. Waktu tetap disimpan UTC; query hanya mengubahnya ke WIB (+7 jam) untuk ditampilkan.
SELECT strftime('%Y-%m-%d %H:00', waktu_diterima, '+7 hours') AS jam_wib,
       COUNT(*) AS jumlah,
       ROUND(AVG(suhu), 2) AS suhu_rata,
       MIN(suhu) AS suhu_min,
       MAX(suhu) AS suhu_maks,
       ROUND(AVG(kelembapan), 2) AS kelembapan_rata
FROM readings
WHERE device_id = 'demo-node-01'
GROUP BY jam_wib
ORDER BY jam_wib DESC
LIMIT 12;

-- judul: Query 3 - JOIN alarm dengan perangkat
-- tujuan: Menggabungkan alerts dan devices melalui foreign key device_id untuk menghitung alarm per perangkat, parameter, dan tingkat keparahan.
SELECT d.nama AS perangkat,
       a.parameter,
       a.severity,
       COUNT(*) AS jumlah_alarm,
       MIN(a.nilai) AS nilai_min,
       MAX(a.nilai) AS nilai_maks,
       strftime('%Y-%m-%d %H:%M', MAX(a.waktu), '+7 hours') AS terakhir_wib
FROM alerts AS a
JOIN devices AS d ON d.device_id = a.device_id
GROUP BY d.nama, a.parameter, a.severity
ORDER BY jumlah_alarm DESC;

-- judul: Query 4 - Pemeriksaan kualitas data per sesi
-- tujuan: Mengukur completeness (seq hilang), uniqueness (duplikat), dan konsistensi timestamp (waktu mundur, bukan UTC) untuk setiap sesi pengiriman.
WITH urut AS (
    SELECT device_id, session_id, seq, waktu_diterima,
           LAG(waktu_diterima) OVER (
               PARTITION BY device_id, session_id ORDER BY seq
           ) AS waktu_sebelumnya
    FROM readings
    WHERE session_id IS NOT NULL
)
SELECT device_id,
       substr(session_id, 1, 8) AS sesi,
       strftime('%Y-%m-%d %H:%M', MIN(waktu_diterima), '+7 hours') AS mulai_wib,
       COUNT(*) AS jumlah,
       MAX(seq) - MIN(seq) + 1 AS seharusnya,
       MAX(seq) - MIN(seq) + 1 - COUNT(DISTINCT seq) AS seq_hilang,
       COUNT(*) - COUNT(DISTINCT seq) AS duplikat,
       COALESCE(SUM(waktu_diterima < waktu_sebelumnya), 0) AS waktu_mundur,
       SUM(waktu_diterima NOT LIKE '%+00:00') AS bukan_utc
FROM urut
GROUP BY device_id, session_id
ORDER BY MIN(waktu_diterima);

-- judul: Query 5 - Perbandingan MQTT dan HTTP (lanjutan UTS)
-- tujuan: LEFT JOIN pembacaan dengan laporan RTT berdasarkan identitas pengiriman untuk membandingkan jumlah data, RTT, dan waktu tulis lokal per protokol.
SELECT r.protokol,
       COUNT(*) AS jumlah_pembacaan,
       COUNT(a.event_id) AS laporan_rtt,
       ROUND(AVG(a.rtt_ms), 2) AS rtt_rata_ms,
       ROUND(MIN(a.rtt_ms), 2) AS rtt_min_ms,
       ROUND(MAX(a.rtt_ms), 2) AS rtt_maks_ms,
       ROUND(AVG(r.local_write_ms), 2) AS tulis_lokal_rata_ms
FROM readings AS r
LEFT JOIN delivery_attempts AS a
       ON a.device_id = r.device_id AND a.session_id = r.session_id
      AND a.protokol = r.protokol AND a.seq = r.seq AND a.status = 'ok'
WHERE r.source_mode = 'demo'
GROUP BY r.protokol;
