#!/bin/bash
# Paneli bu bilgisayarda Docker ile başlatır:  bash ~/Documents/panel/docker-baslat.sh
set -e
cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.local.yml"

if ! docker info >/dev/null 2>&1; then
  echo "✕ Docker çalışmıyor. Docker Desktop uygulamasını açın, sağ üstte balina simgesi sabitlenince tekrar deneyin."
  exit 1
fi

# Yerel ayarlar (ilk seferde oluşturulur)
if [ ! -f .env.local ]; then
  SIFRE=$(LC_ALL=C tr -dc 'a-km-zA-HJ-NP-Z2-9' </dev/urandom | head -c 10)
  {
    echo "ADMIN_USER=fatih"
    echo "ADMIN_PASSWORD=$SIFRE"
    echo "SECRET_KEY=$(openssl rand -hex 32)"
  } > .env.local
  chmod 600 .env.local
fi
set -a; . ./.env.local; set +a
mkdir -p data

echo "→ Panel imajı hazırlanıyor (ilk seferde 1-2 dakika)…"
$COMPOSE build -q

if [ ! -f data/panel.db ]; then
  read -r -p "Örnek (demo) verilerle doldurulsun mu? Gerçek firmaları ekleyeceksen 'h' de. [e/h]: " DEMO
  if [ "$DEMO" = "e" ] || [ "$DEMO" = "E" ]; then
    $COMPOSE run --rm -e DISABLE_SCHEDULER=1 panel python -m app.demo
  fi
fi

$COMPOSE up -d

echo ""
echo "======================================================"
echo "  Panel çalışıyor: http://localhost:8000"
echo "  Kullanıcı : $ADMIN_USER"
echo "  Şifre     : $ADMIN_PASSWORD"
echo "  (Şifre panel klasöründeki .env.local dosyasında saklı)"
echo ""
echo "  Docker Desktop'ta 'panel-local' adıyla görünür."
echo "  Durdurmak : docker compose -f docker-compose.local.yml down"
echo "  Kayıtlar  : docker logs -f panel-local"
echo "======================================================"
sleep 3
open "http://localhost:8000" 2>/dev/null || true
