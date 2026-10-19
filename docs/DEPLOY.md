# Hushyor AI — o'rnatish va joylashtirish (1.8.0; 2.5.0 — pilot va UzCloud; 2.6.0 — sayt va so'rovlar)

Tizim ikki qismdan iborat:

| Qism | Qayerda | Nima qiladi |
|---|---|---|
| **Edge** (`run.py`, `app/`) | Har do'kondagi Windows kompyuter | Kameralar, AI, kliplar, do'kon Telegram boti, lokal panel (`:8000`) |
| **Bulut** (`cloud/`) | Bitta server: VPS yoki egasining kompyuteri | Barcha filiallar paneli (`:9000`), xodimlar va rollar, bildirishnomalar (Telegram, email, push), zaxira |

Bulut bo'lmasa ham edge to'liq ishlaydi. Bulut ulansa, edge ma'lumotni navbat orqali yuboradi. Internet uzilsa, hech narsa yo'qolmaydi (`docs/SYNC_API.md`).

## 1. Do'kon kompyuteri (edge) — Windows

Yangi kompyuter (Python 3.10–3.13 o'rnatilgan, "Add python.exe to PATH" belgilangan):

```powershell
git clone -b bosqich-10 https://github.com/Nizomiddin0628/theft-detection.git C:\hushyor-ai
cd C:\hushyor-ai
.\install_windows.bat
```

`install_windows.bat` → `scripts\windows\install.ps1` quyidagilarni qiladi:

1. `.venv` yaratadi va kutubxonalarni o'rnatadi.
2. `.env` yaratadi va panel parolini so'raydi. Standart `admin123` qoldirilmaydi.
3. Ixtiyoriy: Telegram tokeni va bulut manzili (`CLOUD_URL`, `EDGE_TOKEN`) ni so'raydi.
4. Windows'ga kirganda avtomatik ishga tushadigan vazifa (`HushyorAI`, nazoratchi `--supervise` bilan) qo'shadi.
5. Panelni ochadi.

Qayta ishga tushirsangiz, bor sozlamalar saqlanadi.

**Pilotga tayyorlik (2.5.0):** o'rnatgandan keyin panelda **Pilot** sahifasini oching. Tayyorlik ro'yxatidagi ❌
bandlarni tuzating (parol, Telegram obunachisi, ishlamayotgan kamera, avtomatik ishga tushish, disk, bulut aloqasi),
⚠️ larni iloji boricha (do'kon nomi, zonalar, sanash chizig'i, ish vaqti, saqlash muddati ≥ 30 kun). Keyin
**Pilotni boshlash** — 28 kun kuzatuv boshlanadi (mezonlar sahifada va `docs/PILOT.md` da). Muammo bo'lsa
**Diagnostika paketi** (ZIP: jurnallar va sozlamalar, parol va tokenlarsiz) ni dasturchiga yuboring.

**Yangilash** (yangi versiya ZIP'i kelganda):

```powershell
cd C:\hushyor-ai
powershell -ExecutionPolicy Bypass -File scripts\windows\update.ps1 -Zip "$env:USERPROFILE\Downloads\hushyor_ai_X.Y.Z_yangilanish.zip"
```

`update.ps1` ketma-ket shularni bajaradi:

1. ZIP'ni avval vaqtinchalik papkada ochib tekshiradi. ZIP buzilgan bo'lsa, hech narsa o'zgarmaydi.
2. Vazifalarni to'xtatadi.
3. Zaxira oladi: `.env`, bazalar (WAL fayllari bilan) va almashtiriladigan kod fayllari → `data\backups\update_<vaqt>`.
4. `cloud\panel_dist` ni tozalaydi, shunda eski build qolmaydi.
5. Yangi fayllarni joyiga qo'yadi. `requirements.txt` o'zgargan bo'lsa, kutubxonalarni yangilaydi.
6. Vazifalarni qayta ishga tushiradi va `/healthz` ni tekshiradi.

Kutubxonalar o'rnatilmasa yoki panel 2 daqiqada javob bermasa, kod **avtomatik eski holatga qaytariladi** va eski versiya ishga tushadi. `data\`, `.env` va modellarga skript tegmaydi.

## 2. Bulut serveri — qayerda bo'ladi?

| Variant | Qachon | Afzallik | Kamchilik |
|---|---|---|---|
| **A. Egasining kompyuteri** (Windows, SQLite) | Pilot, 1–3 filial | Bepul, hozir ishlaydi | Kompyuter o'chsa panel va bildirishnomalar to'xtaydi; tashqaridan ochish uchun "oq" IP yoki tunnel kerak |
| **B. VPS** (DigitalOcean va h.k., Ubuntu, nginx + HTTPS) | Doimiy ish, ko'p filial | 24/7, HTTPS, avtomatik zaxira | Oylik to'lov. Shaxsiy ma'lumot chet elda saqlanadi |
| **C. O'zbekistondagi data-markaz** (UzCloud va h.k.) | Tijorat mijozlari | Shaxsiy ma'lumotlarni O'zbekistonda saqlash talabiga mos | Narx va sozlash provayderga bog'liq |

Klipda odamlar tasviri bor, ya'ni bu shaxsiy ma'lumot. O'zbekiston qonunchiligi fuqarolarning shaxsiy ma'lumotlarini O'zbekistondagi serverlarda saqlashni talab qiladi. Shuning uchun tijorat bosqichida C varianti tavsiya etiladi. Huquqiy savolni yurist bilan aniqlang.

### A. Egasining kompyuterida (Windows)

```powershell
cd C:\hushyor-ai
.\.venv\Scripts\python -m cloud.server --init-owner egasi@dokon.uz - "Kompaniya nomi"     # parol so'raladi
powershell -ExecutionPolicy Bypass -File scripts\windows\install_cloud_autostart.ps1          # avtomatik start
```

Panel: `http://localhost:9000`. Tarmoqdagi telefondan ochish uchun: `http://<kompyuter-IP>:9000`. Faqat shu kompyuterdan ochiladigan qilish uchun skriptga `-BindHost 127.0.0.1` qo'shing. Agar edge ham shu kompyuterda bo'lsa, edge'ning `.env` faylida `CLOUD_URL=http://127.0.0.1:9000` bo'ladi.

### B/C. VPS (Ubuntu 22.04 / 24.04) — UzCloud ham

1. Domen yoki subdomen oling (masalan, `panel.dokon.uz`). DNS'da uning `A` yozuvini serverning IP manziliga yo'naltiring.
   **Domen hali yo'q bo'lsa (2.5.0)** — IP manzilning o'zini bering: skript `203-0-113-7.sslip.io` nomini ishlatadi
   (sslip.io bu nomga o'sha IP'ni qaytaradi) va Let's Encrypt sertifikatini shu nomga oladi. Shart: serverda 80 va
   443 portlari tashqaridan ochiq bo'lsin (UzCloud panelida xavfsizlik guruhi / firewall).
2. Serverda quyidagini bajaring:

```bash
apt-get update && apt-get install -y git
git clone -b bosqich-10 https://github.com/Nizomiddin0628/theft-detection.git /root/hushyor-src
sudo bash /root/hushyor-src/deploy/install_vps.sh panel.dokon.uz admin@dokon.uz              # SQLite
sudo bash /root/hushyor-src/deploy/install_vps.sh panel.dokon.uz admin@dokon.uz --postgres   # PostgreSQL (tavsiya)
sudo bash /root/hushyor-src/deploy/install_vps.sh 203.0.113.7 admin@dokon.uz --postgres      # domensiz (UzCloud IP)
```

**Serverda boshqa saytlar Caddy orqali ishlasa (3.0.0)** — skript buni o'zi aniqlaydi: nginx va certbot
o'rnatilmaydi, `/etc/caddy/Caddyfile` ga faqat shu domen uchun bitta blok qo'shiladi (oldin zaxira, keyin
`caddy validate`; xato bo'lsa fayl eski holatiga qaytariladi), sertifikatni Caddy o'zi oladi. Domen sifatida
boshqa saytlaringiz kabi nom bering, masalan `hushyor.185-196-215-179.sslip.io`. 80/443 boshqa dasturda (apache,
docker) bo'lsa — skript hech narsani o'zgartirmay to'xtaydi. Majburlash: `HUSHYOR_WEB=caddy` yoki `HUSHYOR_WEB=nginx`.
Skriptni **faqat repo ichidan** yurgizing (`bash /root/hushyor-src/deploy/install_vps.sh ...`) — boshqa joyga
ko'chirilsa, u o'zi to'xtaydi.

Skript quyidagilarni o'rnatadi:

- Python muhiti (faqat bulut kutubxonalari: `requirements-cloud.txt`).
- `hushyor` tizim foydalanuvchisi.
- `/opt/hushyor-ai/.env`: ommaviy manzil, admin tokeni, ishonchli proksi.
- systemd xizmati (`127.0.0.1:9000`). Xavfsizlik cheklovlari bilan: kodni o'zgartira olmaydi, faqat `cloud/data` ga yozadi.
- Kundalik zaxira taymeri.
- nginx + Let's Encrypt (HTTPS, HTTP→HTTPS, HSTS, so'rov cheklovlari).

Boshqa nginx saytlariga va Telegram botlariga tegmaydi.

3. Birinchi egani yarating (skript buyruqni chiqaradi):
   `cd /opt/hushyor-ai && sudo -u hushyor .venv/bin/python -m cloud.server --init-owner admin@dokon.uz - "Kompaniya"`
4. Bulut Telegram boti uchun @BotFather'da **alohida** bot oching. Edge botining tokeni bilan bir xil bo'lmasin, aks holda ikkalasi "Conflict" beradi. Tokenni `/opt/hushyor-ai/.env` dagi `CLOUD_TELEGRAM_BOT_TOKEN=` ga yozing, so'ng: `sudo systemctl restart hushyor-cloud`.
5. Panelda: **Filiallar → Qo'shish**. (3.0.0: identifikator va ulash buyruq qatoridan ham —
   `.venv\Scripts\python -m app.sync --device-id`, keyin `.venv\Scripts\python -m app.sync --connect https://panel.dokon.uz TOKEN`
   — u aloqani tekshirib `.env` ga o'zi yozadi; so'ng dasturni qayta ishga tushiring.) "Qurilma identifikatori" edge panelida turadi (**Tizim holati → Bulut**). Panel bergan tokenni edge'ning `.env` fayliga yozing:
   ```
   CLOUD_URL=https://panel.dokon.uz
   EDGE_TOKEN=<token>
   ```
   Keyin edge'ni qayta ishga tushiring. **Tizim holati → Bulut → Ulanishni tekshirish** bosilganda "aloqa bor" chiqishi kerak.

**Yangilash:** `cd /root/hushyor-src && git fetch && git checkout <yangi shox> && sudo bash deploy/install_vps.sh panel.dokon.uz admin@dokon.uz`
(o'sha domen yoki IP va o'sha `--postgres` bilan). Bunda `.env` va baza saqlanadi.

**Video sinovi (3.0.0):** panelda «Video sinovi» — video bulutga yuklanadi va filialning do'kon kompyuteri tahlil
qiladi. `.env`: `CLOUD_VIDEO_MAX_MB` (bitta video, sukut 300), `CLOUD_VIDEO_QUOTA_MB` (bulutda tahlil kutayotganlar
jami, 2000), `CLOUD_VIDEO_MIN_FREE_MB` (diskda qolishi shart, 1024). Videolar `cloud/data/video_tests/` da faqat
tahlil tugaguncha turadi. nginx'da yuklash uchun alohida `location` bor (2 GB gacha tana, ilova o'zi cheklaydi).

**Filiallar tayyorligi (2.5.0):** bulut panelida **Filiallar** sahifasida har do'kon kompyuterining tayyorlik ro'yxati
(masalan, "9/11 tayyor · tuzatish kerak: Telegram bot") va pilot kuni ko'rinadi — do'konga bormasdan holatni bilish uchun.

**Sayt va so'rovlar (2.6.0):** sayt `https://<domen>/sayt/` da ochiladi (fayllar `site/`). `/opt/hushyor-ai/.env` ga
aloqa qatorlarini yozing — `CLOUD_SITE_PHONE`, `CLOUD_SITE_TELEGRAM`, `CLOUD_SITE_EMAIL` (berilmagani saytda
ko'rinmaydi) — va so'rovlar boradigan joyni: bulut botini Telegram guruhiga qo'shing, guruhda `/chatid` yozing va
chiqqan raqamni `CLOUD_LEADS_CHAT=` ga yozing (va/yoki `CLOUD_LEADS_EMAIL=`, SMTP sozlangan bo'lsa). Keyin
`sudo systemctl restart hushyor-cloud` va telefondan saytdagi formani yuborib ko'ring — guruhga kelishi kerak.
So'rovlar ro'yxati: `cd /opt/hushyor-ai && sudo -u hushyor .venv/bin/python -m cloud.server --leads`.
Domen ildizida sayt chiqishi kerak bo'lsa — `/etc/nginx/sites-available/hushyor-cloud` dagi `location = /` blokining
izohini oching: kirmagan mehmon `/` da saytni, panelga kirgan xodim — panelni ko'radi (xodimlar `/login/` dan kiradi);
keyin `nginx -t && systemctl reload nginx`.
nginx formani ham cheklaydi (bitta IP dan daqiqasiga 6 ta). Demo va ssenariy: **docs/DEMO.md**.

**Xavfsizlik devori:** faqat 22 (SSH), 80 va 443 portlari ochiq bo'lsin: `ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable`. 9000-port tashqariga ochilmaydi, unga faqat nginx ulanadi.

## 3. Zaxira va tiklash (bulut)

Zaxira avtomatik olinadi: `CLOUD_BACKUP_HOURS` da bir marta (sukut 24 soat). Oxirgi `CLOUD_BACKUP_KEEP` tasi (sukut 7) `cloud/data/backups/` da saqlanadi. VPS'da bunga qo'shimcha systemd taymeri ham ishlaydi. Holatni panelda (**Sozlamalar → Bulut serveri**) yoki `python -m cloud.maintenance status` buyrug'i bilan ko'rish mumkin.

| Baza | Zaxira fayli | Tiklash |
|---|---|---|
| SQLite | `cloud-<vaqt>.db.gz` (3.2.0 dan siqilgan; SQLite backup API, ishlab turganda ham butun; eskilari `.db`) | Xizmatni to'xtating → `-wal`/`-shm` fayllarni o'chiring → faylni ochib `cloud/data/cloud.db` o'rniga qo'ying → ishga tushiring |
| PostgreSQL | `cloud-<vaqt>.pgdump` (`pg_dump`, custom format) | Xizmatni to'xtating → `pg_restore` (pastda) → ishga tushiring |

```bash
# SQLite
sudo systemctl stop hushyor-cloud
cd /opt/hushyor-ai/cloud/data
sudo rm -f cloud.db-wal cloud.db-shm
sudo -u hushyor sh -c 'gunzip -c backups/cloud-20261015-030000.db.gz > cloud.db'
sudo -u hushyor /opt/hushyor-ai/.venv/bin/python -c "import sqlite3; print(sqlite3.connect('cloud.db').execute('PRAGMA integrity_check').fetchone()[0])"
sudo systemctl start hushyor-cloud

# PostgreSQL (zaxira fayli faqat root/hushyor o'qiy oladi — postgres foydalanuvchisiga quvur orqali beriladi;
# --role=hushyor: jadvallar egasi hushyor bo'lib qoladi)
sudo systemctl stop hushyor-cloud
sudo cat /opt/hushyor-ai/cloud/data/backups/cloud-20261015-030000.pgdump | \
    sudo -u postgres pg_restore --clean --if-exists --no-owner --role=hushyor -d hushyor
sudo systemctl start hushyor-cloud
```

Zaxira olishdan oldin diskdagi bo'sh joy tekshiriladi (baza hajmining 2 baravari + `CLOUD_MIN_FREE_GB`); yetmasa zaxira olinmaydi va jurnalda xato. Disk to'lsa avval eski zaxiralar (eng yangi 2 tasi qoladi), keyin eng eski tasdiqlanmagan kliplar o'chiriladi. Kamera holati daqiqalari `CLOUD_METRICS_KEEP_DAYS` (sukut 90) kun saqlanadi; sanash, navbat va oqim — 400 kun.

Zaxiraga VAPID kaliti ham qo'shiladi (`vapid-<vaqt>.pem`): brauzer push obunalari shu kalitga bog'liq, tiklashda uni `cloud/data/vapid_private.pem` ga ko'chiring. Kliplar (`cloud/data/clips/`) zaxiraga kirmaydi, chunki hajmi katta. Ularni muddat bo'yicha avtomatik o'chirish sozlamalari:

- `CLOUD_CLIP_KEEP_DAYS` (30 kun).
- `CLOUD_CLIP_KEEP_CONFIRMED_DAYS` (180 kun) — tasdiqlangan hodisalarning kliplari uzoqroq saqlanadi.
- `CLOUD_MIN_FREE_GB` (2 GB): diskda bundan kam joy qolsa, eng eski tasdiqlanmagan kliplar o'chiriladi.

Hodisa yozuvlari o'chmaydi.

**Zaxirani boshqa joyga ham ko'chiring.** Server diski buzilsa, shu diskdagi zaxira ham yo'qoladi. Eng oddiy usul — kompyuterdan haftasiga bir marta nusxa olish:

```
scp root@SERVER:/opt/hushyor-ai/cloud/data/backups/* D:\hushyor-zaxira\
```

## 4. Tekshirish va kuzatish

| Nima | Qayerda |
|---|---|
| Ishlayaptimi | `https://panel.dokon.uz/healthz`, edge: `http://localhost:8000/healthz` |
| Jurnal (bulut) | `journalctl -u hushyor-cloud -f` yoki `cloud/data/logs/cloud.log` (5 MB × 5) |
| Jurnal (edge) | `data\logs\app.log`, qayta ishga tushishlar — `data\logs\supervisor.log` |
| Kim nima qildi | Bulut panelida: **Xodimlar → Jurnal** (kirish, parol, xodim, token, tekshirish, klip ko'rish) |
| Server holati | Bulut panelida: **Sozlamalar → Bulut serveri** (versiya, baza, disk, zaxira, ogohlantirishlar) |
| Barqarorlik | `python tests\soak.py --report-live data\hushyor.db --hours 72` — jonli tizimning 72 soatlik hisoboti |

Parol unutilsa: `python -m cloud.server --set-password EMAIL` (yangi parol so'raladi; barcha sessiyalar yopiladi).
