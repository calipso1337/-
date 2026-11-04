# Бот для Telegram-канала

С 9:00 до 22:00 по Москве публикует следующий пост из `queue.json` со случайным интервалом от 1 до 3 часов. Ручной запуск через Run workflow публикует пост сразу.
Посты могут быть с картинками, опросами, эмодзи и HTML-оформлением.
Для публикации API Claude не нужен.

## Настройка
1. Создай на github.com репозиторий и загрузи в него все файлы, включая папку `.github`.
2. Открой Settings → Secrets and variables → Actions.
3. На вкладке Secrets создай `TELEGRAM_BOT_TOKEN` и вставь токен бота.
4. На вкладке Variables создай `TELEGRAM_CHANNEL` со значением вроде `@my_channel`.
5. Открой Actions → post → Run workflow, чтобы отправить следующий пост.

## Формат постов в queue.json
- Текст: `{"type": "text", "text": "<b>Заголовок</b>\nТекст"}`
- Фото из репозитория: `{"type": "photo", "image": "images/post.png", "text": "подпись", "source": "https://...", "source_name": "Supercell"}`
- Фото по ссылке: `{"type": "photo", "image": "https://...", "text": "подпись"}`
- Видео до 50 МБ: `{"type": "video", "video": "videos/post.mp4", "text": "подпись"}`
- Видео с YouTube в посте: `{"type": "text", "text": "...", "video_url": "https://www.youtube.com/watch?v=...", "source": "тот же адрес", "source_name": "YouTube: канал"}`
- Опрос: `{"type": "poll", "question": "...", "options": ["...", "..."]}`
- Опрос с картинкой: добавь `image` и `text`. Картинка выйдет отдельным сообщением перед опросом.

Символы `&` и `<` в тексте пиши как `&amp;` и `&lt;`. Строку источника и кнопку «Поделиться каналом» бот добавляет сам. Уведомление о неофициальном материале нужно держать в описании канала.

Новые посты добавляй в конец файла. `state.json` хранит номер следующего поста, его трогать не нужно.

## Проверка
```bash
python3 bot.py --check
python3 -m pytest tests -q
```

Правила для автора постов лежат в `CLAUDE.md`. Обложки рисует `tools/make_image.py`, ему нужен пакет Pillow. Таблицы рисует `tools/make_table.py`. Анимированные ролики делает `tools/make_video.py`, ему нужны Pillow и ffmpeg.
