# iPandu dan iSmart-LA — hubungan kedua repo

iPandu adalah **fork eksperimental** dari [iSmart-LA](https://github.com/kokoali-bima/iSmart-LA).
Keduanya berbagi sekitar **79% kode** — plumbing Telegram, failover model, sesi,
memori, media, Drive, penjadwalan, mekanisme update, dwibahasa, kontrol biaya.

Titik fork: **v0.2b.76** (`95578c7`), 5 September 2026.

| | iSmart-LA | iPandu |
|---|---|---|
| Peran | **produksi** — agent infrastruktur | **eksperimen** — asisten AI |
| Kestabilan | dijaga ketat, rilis hati-hati | bebas dicoba, boleh rusak |
| Kemampuan infra | ya | ya, tetap dipertahankan |
| Deployment | VM produksi | VM terpisah |
| Kunci SSH | kunci cluster | **pasangan kunci sendiri** |

## Aturan merge: SATU ARAH

```
iSmart-LA (produksi)  ──merge──▶  iPandu (eksperimen)
                      ◀── TIDAK PERNAH ───
```

Perbaikan substrat dikerjakan **sekali** di iSmart-LA, lalu ditarik ke sini:

```bash
git fetch upstream
git merge upstream/master
```

Eksperimen iPandu tidak pernah mengalir balik otomatis. Kalau ada sesuatu di
sini yang terbukti layak masuk produksi, angkat sebagai perubahan tersendiri di
repo iSmart-LA — jangan di-merge terbalik.

Remote `upstream` sudah **dikunci untuk push**:

```
upstream  https://github.com/kokoali-bima/iSmart-LA.git (fetch)
upstream  DISABLED-push-ke-produksi-dilarang           (push)
```

Kalau suatu saat terlihat URL push yang normal di situ, itu bukan kemudahan —
itu pengaman yang hilang.

## Disiplin yang membuat ini bertahan

**Tambah berkas. Jangan restrukturisasi `lite_agent.py`.**

Ini satu-satunya aturan yang menentukan skema ini hidup atau mati. Selama
bentuk 79% substrat itu tetap, `git merge upstream/master` mulus. Begitu iPandu
mengaduk-aduk isi file yang sama, setiap merge jadi pertempuran dan dalam
sebulan orang berhenti melakukannya — lalu kedua repo diam-diam berpisah, dan
setiap bug substrat harus diperbaiki dua kali.

Untuk gambarannya: dalam **dua hari** pada 4–5 September, sepuluh bug yang
sampai ke produksi ditemukan dan diperbaiki di substrat bersama ini. Kalau
merge sudah rusak saat itu, semuanya harus dikerjakan dua kali.

Praktiknya:

- Kemampuan baru (email, WhatsApp, kalender) → **berkas baru**, disambungkan
  lewat protokol penanda yang sudah ada, bukan dengan membedah fungsi lama.
- Perlu mengubah `lite_agent.py`? Pertimbangkan dulu apakah perubahan itu
  sebenarnya milik iSmart-LA. Kalau ya, kerjakan di sana dan tarik ke sini.
- Kalau tetap harus diubah di sini, buat sekecil mungkin dan satu tempat, bukan
  tersebar.

## Versi

iPandu punya garis versi sendiri, mulai `v0.1.0`. Tag iSmart-LA sengaja tidak
dibawa supaya `current_version()` (yang membaca `git describe --tags`) tidak
pernah salah melaporkan versi produksi di mesin asisten. Riwayat 114 commit
tetap utuh, jadi provenance-nya tidak hilang.

## Keamanan: kenapa kunci SSH-nya harus terpisah

iPandu tetap punya kemampuan infrastruktur — itu memang tujuannya. Tapi VM yang
berbeda **belum** memisahkan risiko kalau kuncinya sama: agent eksperimental
yang memegang kunci cluster tetap punya akses produksi penuh.

Jadi iPandu didaftarkan dengan **pasangan kunci sendiri**, hanya di server yang
memang boleh ia sentuh. Mekanismenya sudah ada: `/addserver` membuat kunci
sendiri dan hanya menampilkan public key-nya untuk dipasang.

Satu hal lagi yang berlaku khusus di sini. Begitu iPandu bisa membaca email,
untuk pertama kalinya ada input yang **bisa ditulis oleh penyerang** — siapa pun
bisa mengirim email. Isi email harus diperlakukan sebagai data, bukan
instruksi: penanda apa pun di dalamnya diabaikan, tidak ada `LEARN:`, dan
terutama tidak ada `NEEDS_WRITE:` yang boleh lahir dari isi email.
