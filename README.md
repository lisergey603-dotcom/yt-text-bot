# YouTube → текст (Telegram-бот)

Присылаете ссылку на YouTube — бот присылает текст видео.

- Сначала берёт субтитры YouTube (быстро и бесплатно, есть у большинства роликов).
- Если субтитров нет — скачивает звук и распознаёт речь через Whisper (нужен API-ключ).
- Короткий текст — сообщением, длинный — файлом .txt. Кнопка «С таймкодами» — версия с метками времени.

## Установка на сервер

```bash
cd ~
git clone https://github.com/lisergey603-dotcom/yt-text-bot.git
cd yt-text-bot
cp .env.example .env
read -p "BOT_TOKEN: " T && sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=$T|" .env
bash deploy/install.sh
```

Ключ для распознавания речи (необязательно):

```bash
cd ~/yt-text-bot
read -p "WHISPER_API_KEY: " K && sed -i "s|^WHISPER_API_KEY=.*|WHISPER_API_KEY=$K|" .env
sed -i "s|^WHISPER_BASE_URL=.*|WHISPER_BASE_URL=https://api.proxyapi.ru/openai/v1|" .env
systemctl restart yt-text-bot
```

## Обновление

```bash
cd ~/yt-text-bot && git pull && bash deploy/install.sh
```

## Полезное

- Логи: `journalctl -u yt-text-bot -f`
- Перезапуск: `systemctl restart yt-text-bot`
- Если в ответ на ссылку бот пишет «YouTube заблокировал запрос с сервера» — нужен прокси, впишите его в `YT_PROXY` в `.env`.
