#!/bin/bash
# Paneli bu bilgisayarda başlatır:  bash ~/Documents/panel/baslat.command
# İlk çalıştırmada gerekenleri kurar (birkaç dakika), sonraki açılışlar birkaç saniye sürer.
set -e
cd "$(dirname "$0")"
PORT=8000

echo ""
echo "=== Panel başlatılıyor ==="

# 1) uv (Python'u ve paketleri otomatik kuran küçük araç)
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "→ Kurulum aracı (uv) indiriliyor…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2) Python ortamı ve paketler (sadece ilk seferde uzun sürer)
if [ ! -x .venv/bin/python ]; then
  echo "→ Python 3.12 ve gerekli paketler kuruluyor (ilk seferde 1-3 dakika)…"
  uv venv --python 3.12 .venv -q
fi
uv pip install --python .venv/bin/python -q -r requirements.txt

# 3) Yerel ayarlar (ilk seferde oluşturulur, sonra değişmez)
if [ ! -f .env.local ]; then
  SIFRE=$(LC_ALL=C tr -dc 'a-km-zA-HJ-NP-Z2-9' </dev/urandom | head -c 10)
  {
    echo "ADMIN_USER=fatih"
    echo "ADMIN_PASSWORD=$SIFRE"
    echo "SECRET_KEY=$(openssl rand -hex 32)"
    echo "DATABASE_URL=sqlite:///./data/panel.db"
    echo "COOKIE_SECURE=0"
  } > .env.local
  chmod 600 .env.local
  YENI=1
fi
set -a; . ./.env.local; set +a

# 4) İlk açılışta örnek veriler (istersen)
if [ ! -f data/panel.db ]; then
  mkdir -p data
  read -r -p "Örnek (demo) verilerle doldurulsun mu? Gerçek firmaları sonra ekleyeceksen 'h' de. [e/h]: " DEMO
  if [ "$DEMO" = "e" ] || [ "$DEMO" = "E" ]; then
    .venv/bin/python -m app.demo
  fi
fi

echo ""
echo "======================================================"
echo "  Panel adresi : http://localhost:$PORT"
echo "  Kullanıcı    : $ADMIN_USER"
echo "  Şifre        : $ADMIN_PASSWORD"
echo "  (Şifre panel klasöründeki .env.local dosyasında saklı)"
echo "  Kapatmak için bu pencerede Ctrl+C'ye bas."
echo "======================================================"
echo ""

( sleep 3; open "http://localhost:$PORT" ) &
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $PORT
