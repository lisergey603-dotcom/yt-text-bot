#!/usr/bin/env bash
# Установка / обновление бота на сервере (Ubuntu). Запуск: bash deploy/install.sh
set -e
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
SERVICE=yt-text-bot

if [ ! -f .env ]; then
  echo "Нет файла .env — создайте его (см. .env.example) и запустите снова."
  exit 1
fi

echo "==> Системные пакеты"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip ffmpeg curl unzip >/dev/null

# yt-dlp нужен JavaScript-движок (Deno), чтобы скачивать звук с YouTube
if ! command -v deno >/dev/null 2>&1; then
  echo "==> Устанавливаю Deno"
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y >/dev/null
fi

echo "==> Python-зависимости"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/pip install -q -U "yt-dlp[default]"   # YouTube часто меняется — держим свежим

echo "==> Служба systemd"
cat > /etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=YouTube → текст, Telegram-бот
After=network-online.target

[Service]
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m bot.main
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
EOF

# Еженедельное обновление yt-dlp (по понедельникам в 5:00)
cat > /etc/cron.d/$SERVICE-ytdlp <<EOF
0 5 * * 1 root $APP_DIR/.venv/bin/pip install -q -U "yt-dlp[default]" && systemctl restart $SERVICE
EOF

systemctl daemon-reload
systemctl enable -q $SERVICE
systemctl restart $SERVICE
sleep 3
systemctl --no-pager --lines=5 status $SERVICE || true
echo
echo "Готово. Логи: journalctl -u $SERVICE -f"
