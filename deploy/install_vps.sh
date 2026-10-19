#!/usr/bin/env bash
# Hushyor AI bulut serverini VPS'ga o'rnatish yoki yangilash (Ubuntu 22.04 / 24.04, root yoki sudo bilan).
#
#   git clone -b bosqich-10 https://github.com/Nizomiddin0628/theft-detection.git /root/hushyor-src
#   sudo bash /root/hushyor-src/deploy/install_vps.sh panel.dokon.uz admin@dokon.uz            # SQLite
#   sudo bash /root/hushyor-src/deploy/install_vps.sh panel.dokon.uz admin@dokon.uz --postgres # PostgreSQL
#   sudo bash /root/hushyor-src/deploy/install_vps.sh 203.0.113.7 admin@dokon.uz --postgres    # domen yo'q — IP (2.5.0)
#
# Domen o'rniga IP berilsa: 203-0-113-7.sslip.io nomi ishlatiladi (sslip.io bu nomga o'sha IP'ni qaytaradi) — Let's
# Encrypt sertifikati shu nomga olinadi, panel https://203-0-113-7.sslip.io/ da. O'z domeningiz bo'lgach — skriptni
# domen bilan qayta yurgizing va .env dagi CLOUD_PUBLIC_URL ni o'zgartiring.
#
# Yangilash: repo'ni yangilang (git pull) va shu buyruqni qayta yurgizing — .env, baza (cloud/data) saqlanadi.
# Nima qiladi: python3-venv, nginx, certbot (+ postgresql) o'rnatadi; "hushyor" tizim foydalanuvchisi;
# kod /opt/hushyor-ai ga (faqat bulut qismi kerakli kutubxonalar bilan); .env (CLOUD_PUBLIC_URL, admin tokeni,
# PostgreSQL paroli — tasodifiy); systemd xizmati (127.0.0.1:9000) va kundalik zaxira taymeri; nginx + Let's Encrypt
# (HTTPS, HTTP→HTTPS). Boshqa nginx saytlari va Telegram botlaringizga tegmaydi.
#
# 3.0.0: serverda 80/443 portlarini Caddy egallagan bo'lsa (boshqa saytlar Caddy orqali) — nginx/certbot
# O'RNATILMAYDI: Caddyfile'ga faqat shu domen uchun bitta blok qo'shiladi (zaxira, `caddy validate`, xato bo'lsa
# eski holat), sertifikatni Caddy o'zi oladi. Majburlash: HUSHYOR_WEB=caddy yoki HUSHYOR_WEB=nginx.
# 80/443 boshqa dasturda (apache, docker...) bo'lsa — hech narsa o'zgartirilmaydi, skript to'xtaydi.
# Paket o'rnatilganda boshqa xizmatlar avtomatik qayta yoqilmaydi (NEEDRESTART_SUSPEND).
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
MODE="${3:-}"
APP=/opt/hushyor-ai
SRC="$(cd "$(dirname "$0")/.." && pwd)"
SVC=hushyor-cloud

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
    echo "Ishlatish: sudo bash deploy/install_vps.sh <domen yoki IP> <email> [--postgres]"
    exit 1
fi
if [[ $EUID -ne 0 ]]; then
    echo "root (sudo) bilan ishga tushiring"
    exit 1
fi
# 3.0.0: manba — shu skript turgan repo (deploy/ ning ota papkasi). Skript boshqa joyga ko'chirilgan bo'lsa (masalan
# /root/install.sh) SRC "/" bo'lib qolib, butun server /opt/hushyor-ai ga ko'chirilardi — oldindan tekshiriladi
if [[ "$SRC" == "/" || ! -f "$SRC/cloud/server.py" || ! -f "$SRC/deploy/install_vps.sh" || ! -f "$SRC/requirements-cloud.txt" ]]; then
    echo "XATO: manba papka noto'g'ri ($SRC). Skriptni repo ichidan yurgizing: bash <repo>/deploy/install_vps.sh ..."
    exit 1
fi
FREE_MB=$(df -Pm "$(dirname "$APP")" | awk 'NR==2 {print $4}')
if [[ "${FREE_MB:-0}" -lt 1000 ]]; then
    echo "XATO: diskda bo'sh joy kam (${FREE_MB:-?} MB, kamida 1000 MB kerak) — boshqa xizmatlar to'lib qolmasin"
    exit 1
fi
export NEEDRESTART_SUSPEND=1

# Veb-server: Caddy (bor bo'lsa) yoki nginx
WEB="${HUSHYOR_WEB:-}"
PORT_OWNERS=$( (ss -ltnpH 2>/dev/null || true) | awk '$4 ~ /:(80|443)$/' | grep -oE 'users:\(\("[^"]+' | sed 's/users:(("//' | sort -u | tr '\n' ' ' || true)
if [[ -z "$WEB" ]]; then
    # 80/443 kimda bo'lsa — o'sha (Caddy boshqa portda ishlayotgan bo'lsa ham, portlar nginx'da bo'lsa — nginx)
    if [[ "$PORT_OWNERS" == *caddy* ]]; then
        WEB=caddy
    elif [[ "$PORT_OWNERS" == *nginx* ]]; then
        WEB=nginx
    elif systemctl is-active --quiet caddy 2>/dev/null; then
        WEB=caddy
    else
        WEB=nginx
    fi
fi
if [[ "$WEB" != "caddy" && "$WEB" != "nginx" ]]; then
    echo "HUSHYOR_WEB faqat caddy yoki nginx bo'lishi mumkin"
    exit 1
fi
for p in $PORT_OWNERS; do
    if [[ "$p" != "$WEB" ]]; then
        echo "XATO: 80/443 portini \"$p\" egallagan — u bilan ishlash qo'llab-quvvatlanmaydi, hech narsa o'zgartirilmadi."
        echo "Bu domen uchun teskari proksini qo'lda sozlang: https://$DOMAIN → http://127.0.0.1:9000 (docs/DEPLOY.md)"
        exit 1
    fi
done
echo "Veb-server: $WEB"
if [[ "$DOMAIN" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    IP="$DOMAIN"
    DOMAIN="${IP//./-}.sslip.io"
    echo "Domen o'rniga IP berildi ($IP) — $DOMAIN ishlatiladi (sslip.io: shu nom $IP ga yo'naladi)."
    if command -v getent >/dev/null && ! getent hosts "$DOMAIN" | grep -q "$IP"; then
        echo "DIQQAT: $DOMAIN hozir $IP ga yo'nalmayapti (DNS) — sertifikat olinmasligi mumkin."
    fi
fi
if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "Domen noto'g'ri: $DOMAIN"
    exit 1
fi

echo "== 1/7 Paketlar"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# 3.2.0: --no-upgrade — bor paketlar (python3, postgresql-common ...) yangilanmaydi: serverdagi boshqa saytlarga tegilmasin
if [[ "$WEB" == "nginx" ]]; then
    apt-get install -y -qq --no-upgrade python3 python3-venv python3-pip nginx certbot rsync curl >/dev/null
else
    apt-get install -y -qq --no-upgrade python3 python3-venv python3-pip rsync curl >/dev/null
fi
if [[ "$MODE" == "--postgres" ]]; then
    apt-get install -y -qq --no-upgrade postgresql postgresql-client >/dev/null
fi

echo "== 2/7 Foydalanuvchi va kod ($APP)"
id hushyor >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin hushyor
mkdir -p "$APP"
# 3.2.0: joriy (ishlab turgan) kod nusxasi — yangi versiya ishga tushmasa, shunga qaytiladi
PREV="$APP.prev"
if [[ -f "$APP/cloud/server.py" ]]; then
    rsync -a --delete --exclude .venv --exclude /cloud/data --exclude /data --exclude .env "$APP"/ "$PREV"/
fi
rsync -a --delete \
    --exclude .git --exclude .venv --exclude /data --exclude /cloud/data --exclude .env \
    --exclude 'cloud/panel/node_modules' --exclude 'cloud/panel/.next' --exclude 'models/*.onnx' \
    --exclude 'samples' "$SRC"/ "$APP"/
mkdir -p "$APP/cloud/data"
"$(command -v python3)" -m compileall -q "$APP/cloud" >/dev/null || true   # xizmat /opt ga yoza olmaydi
# Kod — root'niki (xizmat o'zgartira olmaydi); cloud/data ga tegilmaydi (qayta ishga tushirishda ishlab turgan
# xizmat bir lahza ham yozish huquqini yo'qotmasin)
# 3.2.0: .env ham chetlab o'tiladi (avval bir lahza root'niki bo'lib, keyingi qadam yiqilsa xizmat .env ni o'qiy olmay,
# bo'sh SQLite bilan ishga tushardi)
find "$APP" \( -path "$APP/cloud/data" -o -path "$APP/.env" \) -prune -o -exec chown root:root {} +
chown hushyor:hushyor "$APP/cloud/data"
chmod 750 "$APP/cloud/data"

echo "== 3/7 Python muhiti (faqat bulut kutubxonalari)"
[[ -x "$APP/.venv/bin/python" ]] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements-cloud.txt"
if [[ "$MODE" == "--postgres" ]]; then
    "$APP/.venv/bin/pip" install -q "psycopg[binary]>=3.1"
fi

echo "== 4/7 Sozlama (.env)"
ENVF="$APP/.env"
if [[ "$MODE" == "--postgres" && -f "$ENVF" && -s "$APP/cloud/data/cloud.db" ]] && ! grep -q '^CLOUD_DB_URL=' "$ENVF"; then
    echo "DIQQAT: bu serverda SQLite bazasi bor ($APP/cloud/data/cloud.db). --postgres bilan bo'sh PostgreSQL bazaga"
    echo "o'tilsa, filiallar, xodimlar va hodisalar yangi bazada bo'lmaydi. Ko'chirish hozircha qo'lda — --postgres'siz"
    echo "qayta ishga tushiring (SQLite davom etadi) yoki eski bazani chetga olib qo'yib qaytadan o'rnating."
    exit 1
fi
if [[ ! -f "$ENVF" ]]; then
    umask 077                                   # admin tokeni bir lahza ham boshqalarga o'qilmasin
    ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    {
        echo "# Hushyor AI bulut serveri — $(date -I). Maxfiy: faqat root va hushyor o'qiydi."
        echo "CLOUD_PUBLIC_URL=https://$DOMAIN"
        echo "CLOUD_ADMIN_TOKEN=$ADMIN_TOKEN"
        echo "CLOUD_TRUSTED_PROXIES=127.0.0.1"
        echo "CLOUD_VAPID_SUB=mailto:$EMAIL"
        echo "CLOUD_TZ_OFFSET=5"
        echo "# Bulut Telegram boti (@BotFather'da ALOHIDA bot):"
        echo "CLOUD_TELEGRAM_BOT_TOKEN="
        echo "# Email (ixtiyoriy): CLOUD_SMTP_HOST, CLOUD_SMTP_PORT, CLOUD_SMTP_USER, CLOUD_SMTP_PASSWORD, CLOUD_SMTP_FROM"
        echo "CLOUD_CLIP_KEEP_DAYS=30"
        echo "CLOUD_CLIP_KEEP_CONFIRMED_DAYS=180"
        echo "# Sayt (/sayt/) aloqa qatorlari va saytdagi so'rovlar (docs/DEMO.md):"
        echo "CLOUD_SITE_PHONE="
        echo "CLOUD_SITE_TELEGRAM="
        echo "CLOUD_SITE_EMAIL="
        echo "CLOUD_LEADS_CHAT="
        echo "CLOUD_LEADS_EMAIL="
    } > "$ENVF"
    umask 022
    echo "   .env yaratildi"
else
    echo "   .env bor — o'zgartirilmadi"
fi
if [[ "$MODE" == "--postgres" ]] && ! grep -q '^CLOUD_DB_URL=' "$ENVF"; then
    PGPASS="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
    sudo -u postgres psql -qtc "SELECT 1 FROM pg_roles WHERE rolname='hushyor'" | grep -q 1 \
        || sudo -u postgres psql -qc "CREATE ROLE hushyor LOGIN PASSWORD '$PGPASS'"
    sudo -u postgres psql -qc "ALTER ROLE hushyor PASSWORD '$PGPASS'"
    sudo -u postgres psql -qtc "SELECT 1 FROM pg_database WHERE datname='hushyor'" | grep -q 1 \
        || sudo -u postgres createdb -O hushyor hushyor
    echo "CLOUD_DB_URL=postgresql+psycopg://hushyor:$PGPASS@127.0.0.1:5432/hushyor" >> "$ENVF"
    echo "   PostgreSQL: baza 'hushyor' tayyor"
fi
chown root:hushyor "$ENVF"
chmod 640 "$ENVF"

echo "== 5/7 systemd xizmati va zaxira taymeri"
install -m 644 "$APP/deploy/systemd/$SVC.service" /etc/systemd/system/
install -m 644 "$APP/deploy/systemd/$SVC-backup.service" /etc/systemd/system/
install -m 644 "$APP/deploy/systemd/$SVC-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now "$SVC-backup.timer" >/dev/null
systemctl enable "$SVC" >/dev/null
systemctl restart "$SVC"
for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:9000/healthz >/dev/null 2>&1 && break
    sleep 1
done
if ! curl -fsS http://127.0.0.1:9000/healthz >/dev/null; then
    journalctl -u "$SVC" -n 40 --no-pager
    if [[ -f "$PREV/cloud/server.py" ]]; then
        # 3.2.0: yangi versiya ishga tushmadi — oldingi kod qaytariladi (baza va .env o'zgarmagan)
        echo "XATO: yangi versiya ishga tushmadi — oldingi versiyaga qaytarilmoqda"
        rsync -a --delete --exclude .venv --exclude /cloud/data --exclude /data --exclude .env "$PREV"/ "$APP"/
        find "$APP" \( -path "$APP/cloud/data" -o -path "$APP/.env" \) -prune -o -exec chown root:root {} +
        systemctl restart "$SVC"
        for _ in $(seq 1 30); do
            curl -fsS http://127.0.0.1:9000/healthz >/dev/null 2>&1 && break
            sleep 1
        done
        curl -fsS http://127.0.0.1:9000/healthz >/dev/null && echo "   oldingi versiya qayta ishlayapti"
    fi
    exit 1
fi
echo "   xizmat ishlayapti: 127.0.0.1:9000"

if [[ "$WEB" == "caddy" ]]; then
    echo "== 6/7 Caddy (HTTPS sertifikatini Caddy o'zi oladi)"
    CF=$(systemctl cat caddy 2>/dev/null | grep -m1 -oP '(?<=--config )\S+' || true)
    CF=${CF:-/etc/caddy/Caddyfile}
    if [[ ! -f "$CF" ]]; then
        echo "XATO: Caddyfile topilmadi ($CF)"
        exit 1
    fi
    if ! command -v caddy >/dev/null; then
        echo "XATO: caddy buyrug'i topilmadi (PATH) — Caddyfile'ga tegilmadi"
        exit 1
    fi
    HOST_RE="${DOMAIN//./\\.}"
    # Aniq shu nom bilan sayt manzili (http(s)://, :port, "a, b {" ro'yxati ham; boshqa saytning nomi ichida kelgani —
    # masalan, xyz.<domen> — hisoblanmaydi). import qilingan fayllar uchun Caddyfile papkasi to'liq ko'riladi.
    CFG_TEXT=$(grep -rhsv --exclude='*.bak-hushyor-*' '^[[:space:]]*#' "$(dirname "$CF")" || true)
    if grep -qE "(^|[[:space:],])(https?://)?${HOST_RE}(:[0-9]+)?([[:space:],{]|$)" <<< "$CFG_TEXT"; then
        echo "   Caddy sozlamasida $DOMAIN bor — o'zgartirilmadi"
    else
        BK="$CF.bak-hushyor-$(date +%Y%m%d-%H%M%S)"
        CLOG=$(mktemp)
        cp "$CF" "$BK"
        { echo; sed "s/__DOMAIN__/$DOMAIN/g" "$APP/deploy/caddy/hushyor-cloud.caddy"; } >> "$CF"
        if caddy validate --config "$CF" --adapter caddyfile > "$CLOG" 2>&1 && systemctl reload caddy >> "$CLOG" 2>&1; then
            echo "   Caddy: $DOMAIN qo'shildi (zaxira: $BK)"
        else
            cp "$BK" "$CF"
            systemctl reload caddy >/dev/null 2>&1 || true
            echo "XATO: Caddy yangi sozlamani qabul qilmadi — Caddyfile eski holatiga qaytarildi:"
            tail -5 "$CLOG"
            exit 1
        fi
        rm -f "$CLOG"
    fi
else
    echo "== 6/7 nginx + HTTPS (Let's Encrypt)"
    if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
        ufw allow 'Nginx Full' >/dev/null         # sertifikat tekshiruvi (80-port) uchun certbot'dan oldin
    fi
    SITE=/etc/nginx/sites-available/hushyor-cloud
    mkdir -p /var/www/html
    if [[ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
        # Sertifikat olinguncha: faqat HTTP (ACME tekshiruvi uchun)
        cat > "$SITE" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 503; }
}
NGINX
        ln -sf "$SITE" /etc/nginx/sites-enabled/hushyor-cloud
        nginx -t && systemctl reload nginx
        certbot certonly --webroot -w /var/www/html -d "$DOMAIN" -m "$EMAIL" --agree-tos -n \
            --deploy-hook "systemctl reload nginx"
    fi
    # 3.2.0: qo'lda o'zgartirilgan sayt fayli saqlanadi; yangi sozlama nginx -t dan o'tmasa — eskisi qaytariladi
    # (aks holda keyingi nginx qayta ishga tushishida serverdagi hamma saytlar to'xtardi)
    NBK=""
    if [[ -f "$SITE" ]]; then
        NBK="$SITE.bak-hushyor-$(date +%Y%m%d-%H%M%S)"
        cp "$SITE" "$NBK"
    fi
    sed "s/__DOMAIN__/$DOMAIN/g" "$APP/deploy/nginx/hushyor-cloud.conf" > "$SITE"
    ln -sf "$SITE" /etc/nginx/sites-enabled/hushyor-cloud
    if nginx -t 2>/dev/null; then
        systemctl reload nginx
    else
        if [[ -n "$NBK" ]]; then cp "$NBK" "$SITE"; else rm -f /etc/nginx/sites-enabled/hushyor-cloud; fi
        nginx -t && systemctl reload nginx
        echo "XATO: yangi nginx sozlamasi xato — eski holat qaytarildi"
        exit 1
    fi
fi

echo "== 7/7 Tekshiruv"
for _ in $(seq 1 30); do
    curl -fsS "https://$DOMAIN/healthz" 2>/dev/null && echo && break
    sleep 2
done
curl -fsS "https://$DOMAIN/healthz" >/dev/null || { echo "XATO: https://$DOMAIN javob bermadi (sertifikat? 80/443 ochiqmi?)"; exit 1; }
echo
echo "Tayyor: https://$DOMAIN/"
if ! sudo -u hushyor "$APP/.venv/bin/python" -c "import sys; sys.path.insert(0, '$APP'); from cloud import db; db.init_db(); sys.exit(0 if db.row('SELECT id FROM users LIMIT 1') else 1)" 2>/dev/null; then
    echo "Birinchi egasini yarating (parol so'raladi):"
    echo "  cd $APP && sudo -u hushyor .venv/bin/python -m cloud.server --init-owner $EMAIL - \"Kompaniya nomi\""
fi
echo "Jurnal:   journalctl -u $SVC -f      yoki  $APP/cloud/data/logs/cloud.log"
echo "Holat:    sudo -u hushyor $APP/.venv/bin/python -m cloud.maintenance status"
echo "Parolni tiklash: cd $APP && sudo -u hushyor .venv/bin/python -m cloud.server --set-password EMAIL"
