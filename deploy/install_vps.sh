#!/usr/bin/env bash
# Hushyor AI bulut serverini VPS'ga o'rnatish yoki yangilash (Ubuntu 22.04 / 24.04, root yoki sudo bilan).
#
#   git clone -b bosqich-2 https://github.com/Nizomiddin0628/theft-detection.git /root/hushyor-src
#   sudo bash /root/hushyor-src/deploy/install_vps.sh panel.dokon.uz admin@dokon.uz            # SQLite
#   sudo bash /root/hushyor-src/deploy/install_vps.sh panel.dokon.uz admin@dokon.uz --postgres # PostgreSQL
#
# Yangilash: repo'ni yangilang (git pull) va shu buyruqni qayta yurgizing — .env, baza (cloud/data) saqlanadi.
# Nima qiladi: python3-venv, nginx, certbot (+ postgresql) o'rnatadi; "hushyor" tizim foydalanuvchisi;
# kod /opt/hushyor-ai ga (faqat bulut qismi kerakli kutubxonalar bilan); .env (CLOUD_PUBLIC_URL, admin tokeni,
# PostgreSQL paroli — tasodifiy); systemd xizmati (127.0.0.1:9000) va kundalik zaxira taymeri; nginx + Let's Encrypt
# (HTTPS, HTTP→HTTPS). Boshqa nginx saytlari va Telegram botlaringizga tegmaydi.
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
MODE="${3:-}"
APP=/opt/hushyor-ai
SRC="$(cd "$(dirname "$0")/.." && pwd)"
SVC=hushyor-cloud

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
    echo "Ishlatish: sudo bash deploy/install_vps.sh <domen> <email> [--postgres]"
    exit 1
fi
if [[ $EUID -ne 0 ]]; then
    echo "root (sudo) bilan ishga tushiring"
    exit 1
fi
if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "Domen noto'g'ri: $DOMAIN"
    exit 1
fi

echo "== 1/7 Paketlar"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx certbot rsync curl >/dev/null
if [[ "$MODE" == "--postgres" ]]; then
    apt-get install -y -qq postgresql postgresql-client >/dev/null
fi

echo "== 2/7 Foydalanuvchi va kod ($APP)"
id hushyor >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin hushyor
mkdir -p "$APP"
rsync -a --delete \
    --exclude .git --exclude .venv --exclude data --exclude cloud/data --exclude .env \
    --exclude 'cloud/panel/node_modules' --exclude 'cloud/panel/.next' --exclude 'models/*.onnx' \
    --exclude 'samples' "$SRC"/ "$APP"/
mkdir -p "$APP/cloud/data"
"$(command -v python3)" -m compileall -q "$APP/cloud" >/dev/null || true   # xizmat /opt ga yoza olmaydi
# Kod — root'niki (xizmat o'zgartira olmaydi); cloud/data ga tegilmaydi (qayta ishga tushirishda ishlab turgan
# xizmat bir lahza ham yozish huquqini yo'qotmasin)
find "$APP" -path "$APP/cloud/data" -prune -o -exec chown root:root {} +
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
curl -fsS http://127.0.0.1:9000/healthz >/dev/null || { journalctl -u "$SVC" -n 40 --no-pager; exit 1; }
echo "   xizmat ishlayapti: 127.0.0.1:9000"

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
sed "s/__DOMAIN__/$DOMAIN/g" "$APP/deploy/nginx/hushyor-cloud.conf" > "$SITE"
ln -sf "$SITE" /etc/nginx/sites-enabled/hushyor-cloud
nginx -t
systemctl reload nginx

echo "== 7/7 Tekshiruv"
curl -fsS "https://$DOMAIN/healthz" && echo
echo
echo "Tayyor: https://$DOMAIN/"
if ! sudo -u hushyor "$APP/.venv/bin/python" -c "import sys; sys.path.insert(0, '$APP'); from cloud import db; db.init_db(); sys.exit(0 if db.row('SELECT id FROM users LIMIT 1') else 1)" 2>/dev/null; then
    echo "Birinchi egasini yarating (parol so'raladi):"
    echo "  cd $APP && sudo -u hushyor .venv/bin/python -m cloud.server --init-owner $EMAIL - \"Kompaniya nomi\""
fi
echo "Jurnal:   journalctl -u $SVC -f      yoki  $APP/cloud/data/logs/cloud.log"
echo "Holat:    sudo -u hushyor $APP/.venv/bin/python -m cloud.maintenance status"
echo "Parolni tiklash: cd $APP && sudo -u hushyor .venv/bin/python -m cloud.server --set-password EMAIL"
