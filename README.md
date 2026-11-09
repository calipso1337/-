# Бот для Telegram-канала

Публикует посты строго по расписанию из `config.json` (по умолчанию каждый час с 9:00 до 22:00 по Москве). Работает на GitHub Actions, API Claude для публикации не нужен.

Что выходит в каждый слот:
1. В слотах `meme_slots` выходит мем из r/Brawlstars, если он есть.
2. Иначе выходит свежая новость: новое видео с YouTube-канала Brawl Stars или новая статья из блога Supercell с официальной картинкой. Таких постов не больше `max_news_per_day` в день.
3. Иначе выходит следующий пост из `queue.json`.

Свежие материалы бот собирает сам перед каждым слотом и складывает в `inbox.json`, а уже виденные запоминает в `seen.json`. Эти файлы руками не правь. При первом запуске бот только запоминает старые видео и статьи, чтобы не завалить ими канал.

## Настройка
1. Settings → Secrets and variables → Actions → Secrets: `TELEGRAM_BOT_TOKEN` с токеном бота.
2. Там же на вкладке Variables: `TELEGRAM_CHANNEL`, например `@All_About_Brawl`.
3. Необязательно, если Reddit отвечает 403: создай приложение типа «script» на https://www.reddit.com/prefs/apps и добавь секреты `REDDIT_CLIENT_ID` и `REDDIT_CLIENT_SECRET`.

### Точное время: внешний таймер
GitHub запускает своё расписание с опозданием от 5 минут до часа, а иногда пропускает запуски. Поэтому workflow запускает бесплатный сервис cron-job.org: он срабатывает за минуту до слота, а бот дожидается точной минуты и публикует пост.

1. Создай токен: GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token. В Repository access выбери только этот репозиторий, в Permissions → Actions поставь Read and write.
2. Зарегистрируйся на https://cron-job.org и создай задание:
   - URL: `https://api.github.com/repos/ВЛАДЕЛЕЦ/РЕПОЗИТОРИЙ/actions/workflows/post.yml/dispatches`
   - Расписание: Custom, часовой пояс Europe/Moscow, минута `59`, часы `8-21`. Последний запуск в 21:59 даёт пост в 22:00.
   - Advanced → Request method: `POST`.
   - Headers: `Authorization: Bearer ТОКЕН`, `Accept: application/vnd.github+json`, `Content-Type: application/json`.
   - Request body: `{"ref":"main"}`
3. Нажми Test run: ответ 204 значит, что всё работает. В Actions появится запуск, который дождётся слота.

Если поменяешь слоты в `config.json`, поменяй и время в cron-job.org: запуск должен быть за 1–5 минут до слота. Если таймер не сработал, запасное расписание GitHub опубликует пост с опозданием, но только если оно не больше 20 минут. Иначе слот пропускается, чтобы посты не съезжали.

Run workflow с галочкой «Опубликовать следующий пост прямо сейчас» отправляет пост сразу.

## Формат постов в queue.json
- Текст: `{"type": "text", "text": "<b>Заголовок</b>\nТекст"}`
- Фото из репозитория: `{"type": "photo", "image": "images/post.png", "text": "подпись", "source": "https://...", "source_name": "Supercell"}`
- Фото по ссылке: `{"type": "photo", "image": "https://...", "text": "подпись"}`
- Видео до 50 МБ: `{"type": "video", "video": "videos/post.mp4", "text": "подпись"}`
- Видео с YouTube в посте: `{"type": "text", "text": "...", "video_url": "https://www.youtube.com/watch?v=...", "source": "тот же адрес", "source_name": "YouTube: канал"}`
- Опрос: `{"type": "poll", "question": "...", "options": ["...", "..."]}`
- Опрос с картинкой: добавь `image` и `text`. Картинка выйдет отдельным сообщением перед опросом.

Символы `&` и `<` в тексте пиши как `&amp;` и `&lt;`. Строку источника и кнопку «Поделиться каналом» бот добавляет сам. Уведомление о неофициальном материале нужно держать в описании канала.
- Официальная картинка по ссылке: `{"type": "photo", "image": "https://...", "fallback_image": "images/обложка.png", ...}`. Если Telegram не заберёт ссылку сам, бот скачает картинку и загрузит её файлом.

Новые посты добавляй в конец файла. `state.json` хранит номер следующего поста и последний отработанный слот, его трогать не нужно.

## Проверка
```bash
python3 bot.py --check
python3 -m pytest tests -q
```

Правила для автора постов лежат в `CLAUDE.md`. Обложки рисует `tools/make_image.py`, ему нужен пакет Pillow. Таблицы рисует `tools/make_table.py`. Анимированные ролики делает `tools/make_video.py`, ему нужны Pillow и ffmpeg.
