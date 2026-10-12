#!/usr/bin/env bash
# Hushyor AI — macOS / Linux uchun ishga tushirish
set -e
cd "$(dirname "$0")"

echo ""
echo "  =========================================="
echo "    Hushyor AI - do'kon kameralari uchun AI"
echo "  =========================================="
echo ""

PY=""
for c in python3.12 python3.11 python3.13 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[XATO] Python 3 topilmadi. https://www.python.org/downloads/ dan o'rnating."
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/3] Virtual muhit yaratilmoqda ($PY)..."
  "$PY" -m venv .venv
fi

if [ ! -f ".venv/.installed" ]; then
  echo "[2/3] Kutubxonalar o'rnatilmoqda (birinchi marta 3-10 daqiqa)..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.installed
fi

echo "[3/3] Dastur ishga tushmoqda... http://localhost:8000"
exec .venv/bin/python run.py "$@"
