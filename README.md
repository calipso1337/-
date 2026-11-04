# Бот для Telegram-канала

Каждый час с 9:00 до 22:00 по Москве публикует следующий пост из `queue.json`.
Бесплатно, API Claude не нужен.

## Настройка
1. Создай на github.com приватный репозиторий и загрузи в него все файлы, включая папку `.github`.
2. Открой Settings → Secrets and variables → Actions.
3. На вкладке Secrets создай `TELEGRAM_BOT_TOKEN` и вставь токен бота.
4. На вкладке Variables создай `TELEGRAM_CHANNEL` со значением вроде `@my_channel`.
5. Открой Actions → post → Run workflow, чтобы отправить первый пост.

## Формат постов в queue.json
- Текст: `{"type": "text", "text": "..."}`
- Фото: `{"type": "photo", "image": "https://...", "text": "подпись"}`
- Опрос: `{"type": "poll", "question": "...", "options": ["...", "..."]}`

Новые посты добавляй в конец файла. `state.json` хранит номер следующего поста, его трогать не нужно.
