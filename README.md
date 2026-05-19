# Nargila Counter Bot

Минимальный Telegram-бот, который считает количество кальянов по пользователям в чате.

## Логика

- Бот слушает сообщения.
- Если сообщение содержит **фото** и текст/подпись с `+1`, бот добавляет +1 автору сообщения.
- Счет хранится в SQLite.
- После каждого засчитанного сообщения бот отправляет в чат общий рейтинг.
- Дополнительно доступна команда `/stats`.

## Локальный запуск

1. Создайте бота через BotFather и получите токен.
2. Установите зависимости:

```bash
pip install "python-telegram-bot>=22.0"
```

3. Запустите:

```bash
TELEGRAM_BOT_TOKEN=your_token python main.py
```

## Docker

Сборка контейнера, который сам клонирует репозиторий из GitHub:

```bash
docker build \
  --build-arg GITHUB_REPO=https://github.com/<your-user>/<your-repo>.git \
  --build-arg GITHUB_REF=main \
  -t nargila-counter-bot .
```

Запуск:

```bash
docker run -d \
  --name nargila-counter-bot \
  -e TELEGRAM_BOT_TOKEN=your_token \
  -v nargila-counter-data:/data \
  nargila-counter-bot
```
