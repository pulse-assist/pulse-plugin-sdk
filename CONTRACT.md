# Контракт плагинов «Пульса» 1.3

Справочник для разработчика плагина без доступа к ядру. Собран из кода и документов ядра — не правьте руками:
обновляется вместе с пакетом `pulse_plugin`. В манифесте плагина: `"pulse": ">=1.3"`.

## Манифест `pulse-plugin.json`

```jsonc
{
  "id": "finance",                       // латиница, цифры, «-»; уникален; префикс команд, адресов, базы
  "name": "Финансовый помощник",
  "version": "1.2.0",                    // semver; обновление — тег v1.2.0 в репозитории
  "pulse": ">=1.0",                      // версия контракта плагинов (PLUGIN_API_VERSION платформы)
  "description": "Банки, доходы и расходы, бюджет",
  "permissions": ["cards:read", "cards:write", "signals:create", "files", "network"],
  "runtime": {                           // серверная часть; нет блока — плагин без своего процесса
    "kind": "python",                    // python — процесс с venv; docker — контейнер (любой язык)
    "entry": "finance_plugin.app:app",   // python: ASGI-приложение; docker: "dockerfile": "Dockerfile"
    "path": "backend",                   // python: папка кода — рабочая папка процесса и PYTHONPATH
    "requirements": "backend/requirements.txt",
    "health": "/health"
  },
  "ui": {                                // страницы плагина; сборку описывает плагин
    "dist": "ui/dist",                   // что отдавать
    "build": "npm ci && npm run build"   // необязательно: команда сборки при установке (нет — dist в репозитории)
  },
  "menu": [
    { "id": "ledger", "title": "Финансы", "icon": "bi-wallet2", "page": "index.html#/ledger" },
    { "id": "budget", "title": "Бюджет", "icon": "bi-pie-chart", "page": "index.html#/budget" }
  ],
  "settings": {                          // JSON Schema: форму рисует Пульс; secret — шифруется, маской
    "bank":  { "type": "string", "enum": ["tinkoff", "sber"], "title": "Банк" },
    "token": { "type": "string", "secret": true, "title": "Токен API банка" }
  },
  "skills": ["skills/finance.md"],       // правила для агентов — во встроенные скиллы библиотеки
  "commands": "commands.json",           // команды агентов: pulse finance …
  "jobs": [{ "name": "Выписка из банка", "cron": "0 * * * *", "command": "sync" }],
  "agents": ["agents/finansist.md"],     // необязательно: готовые агенты (инструкции + скиллы)
  "install": "INSTALL.md"                // инструкция развёртывания для агента-установщика
}
```

Полная схема — `manifest.schema.json` рядом (её проверяет `pulse-plugin check`). Пути — внутри плагина, без
`..` и абсолютных; `id` — латиница в нижнем регистре, цифры и «-»; версия — semver.

## Права

Всё, что не разрешено явно, — 403. Свой плагин (`/api/plugins/<id>/…`: свой API, команды, страницы, настройки
без секретов) доступен всегда.

| Право | Владелец видит | Что открывает |
|---|---|---|
| `cards:read` | читать карточки, теги и ленту | GET /api/cards, /api/cards/{id}, /api/tags, /api/graph, /api/signals/feed, /api/links |
| `cards:write` | создавать и менять карточки | всё из cards:read + POST/PATCH/DELETE /api/cards…, /api/links… |
| `signals:create` | заводить сигналы в ленту | POST /api/cards только с type: signal (pulse_plugin: plugin.signal(...)) |
| `files` | свою папку в «Файлах» | GET /api/files… только внутри своей папки Plugins/<Название>/ (писать — прямо на диск, PULSE_PLUGIN_FILES) |
| `files:open` | открывать и сохранять файлы, которые вы открываете в «Файлах» | GET/PUT /api/plugin-files/{ticket} — билеты файлов для открытия и редактирования |
| `files:provide` | показывать своё хранилище папкой @<плагин> в «Файлах» и агентам — они читают и меняют его файлы | files.provider: Пульс ходит в /fs/… процесса плагина и показывает хранилище папкой @<id> (pulse_plugin.fs.FileProvider) |
| `embed` | встраивается в страницы Пульса с вашими правами — ставьте только плагины, которым доверяете | GET /plugin-apps/{id}/… — встроенные страницы на адресе Пульса с сессией владельца (iframe с allow-same-origin) |
| `services` | запускать свои сервисы в Docker | запускать свои сервисы в Docker — дополнительные контейнеры из манифеста |
| `jobs` | задания планировщика | /api/jobs…, /api/job-runs… — задания планировщика |
| `chat` | чаты | /api/chats…, /api/chat-messages… — чаты |
| `notify` | уведомления владельцу | POST /api/notify — уведомление владельцу (лента, бот) |
| `network` | ходить в интернет (банки, внешние API) | заявление для владельца: плагин ходит в интернет (платформа сеть не ограничивает) |

Новое право в новой версии ставится только с подтверждением владельца — добавляйте права осознанно и объясняйте
в README и в `changes` версии.

## Окружение процесса

| Переменная | Что это |
|---|---|
| `PULSE_API_URL` | адрес API Пульса (pulse_plugin: plugin.api) |
| `PULSE_PLUGIN_APP_PATH` | внешний путь страниц плагина: <base_path>/plugin-apps/<id> (для ссылок со встроенной страницы) |
| `PULSE_PLUGIN_DATA` | служебная папка плагина (вне каталога файлов), сохраняется между версиями |
| `PULSE_PLUGIN_FILES` | та же папка на диске — сюда плагин пишет файлы для владельца и агентов |
| `PULSE_PLUGIN_FOLDER` | своя папка в каталоге файлов, путь для API: Plugins/<Название> |
| `PULSE_PLUGIN_ID` | id плагина |
| `PULSE_PLUGIN_MONGO_DB` | своя база плагина (pulse_plugin: plugin.db) |
| `PULSE_PLUGIN_MONGO_URL` | MongoDB платформы |
| `PULSE_PLUGIN_NAME` | название из манифеста |
| `PULSE_PLUGIN_PORT` | порт, который должен слушать сервер плагина |
| `PULSE_PLUGIN_PREVIOUS_VERSION` | с какой версии обновились (пусто — первая установка): перенесите свои данные |
| `PULSE_PLUGIN_SECRETS` | секреты, JSON (plugin.secret(...)); не пишите их в журнал |
| `PULSE_PLUGIN_SETTINGS` | настройки по схеме манифеста, JSON (plugin.settings) |
| `PULSE_PLUGIN_TOKEN` | токен плагина: права из манифеста; держите в секрете |
| `PULSE_PLUGIN_VERSION` | версия, которая сейчас запущена |
| `PULSE_PUBLIC_URL` | внешний адрес Пульса |

Сервер плагина — HTTP-приложение (Python: ASGI через `pulse_plugin`, Docker — любой язык), отвечает на
`runtime.health`, команды агентов — `POST /commands/<имя>` с JSON аргументов.

## Страницы: мост `window.pulse` и встраивание (`embed`)

Обычные страницы меню (`ui`) открываются во фрейме **без** `allow-same-origin`: cookie владельца недоступны,
запросы — через мост `window.pulse`, от имени плагина и в пределах его прав.

Право **`embed`** — другое: страница живёт на `/plugin-apps/<id>/…` в одном origin с Пульсом, фрейм с
`allow-same-origin`, сессия владельца. Ставьте только плагины, которым доверяете; публичный каталог принимает
такие плагины только с открытым исходным кодом и после ревью.

```
    pulse.plugin(path, {method, params, body})  — запрос к своему серверу (/api/plugins/<id>/api/<path>)
    pulse.api(path, {method, params, body})     — запрос к API Пульса (права из манифеста)
    pulse.command(name, args)                   — команда плагина, как у агентов
    pulse.settings()                            — настройки плагина (без секретов)
    pulse.openCard(id), pulse.chat(text)        — открыть карточку, начать чат о плагине
    pulse.navigate("/files?path=@yadisk")       — перейти в раздел Пульса (только свои адреса, от «/»)
    pulse.setState("ledger?month=2026-10")      — своё состояние в адресе страницы (ссылкой можно поделиться)
    pulse.state                                 — состояние, с которым страница открыта
```

## Хранилище плагина: поставщик файлов (`files.provider`)

Плагин с правом `files:provide` и блоком `"files": {"provider": {"title": "Мой диск", "icon": "bi-cloud"}}`
показывает своё хранилище папкой `@<id>` — в «Файлах», у `pulse files` и в смонтированном дереве агентов (FUSE).
Пульс не копирует файлы: каждая операция — запрос к процессу плагина. В Python — `pulse_plugin.fs.FileProvider`
и `plugin.provide_files(...)`.

Хранилище живёт у плагина (облачный диск, сетевая папка): ядро ничего не копирует, а переводит операции каталога
в HTTP-протокол процесса плагина:

    GET    /fs/list?path=            → {"items": [элемент…], "readonly": bool}
    GET    /fs/stat?path=            → элемент
    GET    /fs/read?path=            → содержимое (поток; заголовок Range — часть файла, ответ 206)
    PUT    /fs/write?path=&overwrite= тело — содержимое → элемент
    POST   /fs/mkdir  {"path"}       → элемент
    POST   /fs/move   {"source", "target"} → элемент
    DELETE /fs/delete?path=&recursive=

Элемент — {"name", "kind": "dir"|"file", "size", "modified_at" (ISO), "readonly", "mime"}. Пути — относительно
корня хранилища, через «/», корень — пустая строка. Ошибки — статус и {"detail": "текст"}: 404 — нет такого,
409 — уже есть или папка не пуста, 403 — только чтение, 400 — прочее, 507 — нет места.

## Дизайн страниц

В страницу уже подключены токены и классы платформы — используйте их, а не свои цвета и шрифты.
Токены: `--pulse-background`, `--pulse-background-bg`, `--pulse-background-text`, `--pulse-border`, `--pulse-divider`, `--pulse-hover`, `--pulse-important`, `--pulse-important-bg`, `--pulse-important-text`, `--pulse-knowledge`, `--pulse-knowledge-bg`, `--pulse-muted`, `--pulse-ok`, `--pulse-ok-bg`, `--pulse-ok-text`, `--pulse-panel`, `--pulse-primary`, `--pulse-signal`, `--pulse-signal-bg`, `--pulse-task`, `--pulse-task-bg`, `--pulse-text`, `--pulse-urgent`, `--pulse-urgent-bg`, `--pulse-urgent-text`.
Классы: `pl-badge`, `pl-card`, `pl-dot`, `pl-empty`, `pl-error`, `pl-grid`, `pl-grow`, `pl-h`, `pl-important`, `pl-key`, `pl-kpi-delta`, `pl-kpi-label`, `pl-kpi-value`, `pl-list`, `pl-muted`, `pl-num`, `pl-ok`, `pl-section`, `pl-small`, `pl-stack`, `pl-table`, `pl-tag`, `pl-type-knowledge`, `pl-type-signal`, `pl-type-task`, `pl-urgent`.

## Каталог и выпуск

Сборка — `tar.gz` с `pulse-plugin.json` (в корне или в единственной папке), кодом и собранным интерфейсом
(`pulse-plugin pack`). Версия попадает в каталог записью в JSON репозитория плагинов:

```jsonc
{ "format": 1, "name": "Pulse — публичный",
  "plugins": [{ "id": "itmo",                    // слаг: a-z, 0-9, «-»; совпадает с id манифеста
                "name": "my.itmo", "description": "…", "author": "…", "license": "MIT", "homepage": "…",
                "icon": "bi-calendar-week", "tags": ["учёба"],
                "versions": [{ "version": "0.2.0", "channel": "stable | beta", "pulse": ">=1.1",
                               "url": "https://…/itmo-0.2.0.tar.gz", "sha256": "…", "size": 48213,
                               "permissions": ["signals:create", "network"], "released_at": "2026-10-06",
                               "changes": "…" }] }] }
```

- Платформа сверяет sha256 и размер сборки, id и версию манифеста — с записью каталога.
- Каналы: `stable` и `beta`; версии с несовместимым `pulse` не предлагаются.
- Перед обновлением — снимок данных плагина (`PULSE_PLUGIN_DATA` и его база); не запустилась новая версия
  (health) — данные и версия возвращаются. Свои миграции делайте при старте по `PULSE_PLUGIN_PREVIOUS_VERSION`
  и делайте их повторяемыми.
- Публичный каталог — https://github.com/pulse-assist/pulse-plugins (запись — через MR).
