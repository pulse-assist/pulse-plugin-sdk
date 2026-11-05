# Контракт плагинов «Пульса» 1.12

Справочник для разработчика плагина без доступа к ядру. Собран из кода и документов ядра — не правьте руками:
обновляется вместе с пакетом `pulse_plugin`. В манифесте плагина: `"pulse": ">=1.12"`.

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
| `files:mount` | видеть и менять все ваши файлы — каталог, карточки, хранилища — как обычную папку, так же как агенты | всё дерево «Файлов» (каталог, карточки, хранилища @…) папкой PULSE_PLUGIN_FS_DIR в процессе плагина — чтение и запись, как у агентов; в Docker — том /pulse/fs |
| `files:provide` | показывать своё хранилище папкой @<плагин> в «Файлах» и агентам — они читают и меняют его файлы | files.provider: Пульс ходит в /fs/… процесса плагина и показывает хранилище папкой @<id> (pulse_plugin.fs.FileProvider) |
| `speech:provide` | распознавать речь: голосовые в Telegram и микрофон в чатах — записи уходят плагину | speech: Пульс шлёт записи (голосовые Telegram, микрофон чатов) на POST /speech/transcribe процесса плагина (pulse_plugin.speech) |
| `embed` | встраивается в страницы Пульса с вашими правами — ставьте только плагины, которым доверяете | GET /plugin-apps/{id}/… — встроенные страницы на адресе Пульса с сессией владельца (iframe с allow-same-origin) |
| `services` | запускать свои сервисы в Docker | запускать свои сервисы в Docker — дополнительные контейнеры из манифеста |
| `jobs` | задания планировщика | /api/jobs…, /api/job-runs… — задания планировщика |
| `chat` | чаты | /api/chats…, /api/chat-messages… — чаты |
| `notify` | уведомления владельцу | POST /api/notify — уведомление владельцу (лента, бот) |
| `network` | ходить в интернет (банки, внешние API) | заявление для владельца: плагин ходит в интернет (платформа сеть не ограничивает) |
| `agents:models` | видеть CLI-интеграции и их модели, менять модель своих агентов | GET /api/plugins/{id}/models — CLI-интеграции и их модели; PATCH /api/plugins/{id}/agents/{ключ} {cli_integration_id, model} — модель своего агента |
| `agents:privileged` | просить для своего агента полный доступ (поиск в интернете, любые команды) — включаете вы | мост pulse.requestPrivileged(ключ) — Пульс спрашивает владельца и включает полный доступ своему агенту; выключить плагин может сам: PATCH …/agents/{ключ} {privileged: false} |
| `bg` | вести фоновые задачи в чатах: запускать своих агентов по шагам, писать итоги, задавать вам вопросы через агента чата | /api/bg-tasks… — свои фоновые задачи в чатах: создать (run_id из X-Pulse-Run), шаги-запуски своих агентов, итоги и этапы, сообщение агенту чата, завершение |

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
| `PULSE_PLUGIN_FS_DIR` | право files:mount: дерево «Файлов» в процессе плагина (Docker — /pulse/fs); пути в нём — те же, что в «Файлах» |
| `PULSE_PLUGIN_FS_HOST_DIR` | право files:mount: то же дерево, как его видят агенты ($PULSE_FS_DIR) — переводите их абсолютные пути в свои |
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
`runtime.health`, команды агентов — `POST /commands/<имя>` с JSON аргументов. Пульс ждёт ответа команды 120 с;
долгой команде (расшифровка часовой записи, большая выгрузка) — `"timeout_sec": 900` в её записи `commands.json`
(до 3600). Агенту всё же удобнее ответ за минуту-две: дольше — отдавайте номер задания и команду «результат».

## Docker: сеть, лимиты и файлы владельца (контракт 1.11)

`runtime.kind = docker` по умолчанию работает в сети хоста. `"network": "isolated"` — своя сеть плагина: интернет
есть, сервисы хоста (Mongo, порты 127.0.0.1) не видны, API Пульса — по `PULSE_API_URL` (relay на шлюзе сети).
Берите её, когда в контейнере выполняется чужой код (ноутбуки, скрипты агентов). `"memory": "4g"`, `"cpus": 2` —
лимиты контейнера (`docker run -m/--cpus`): тяжёлая задача упрётся в лимит, а не уронит сервер.

Право `files:mount` — всё дерево «Файлов» владельца папкой в процессе плагина (`PULSE_PLUGIN_FS_DIR`): каталог,
карточки как Markdown, хранилища `@…` — то же, что видят агенты. Пути внутри — те же, что в «Файлах»
(`Документы/отчёт.csv`). Агенты передают и абсолютные пути своего дерева: `PULSE_PLUGIN_FS_HOST_DIR` — его корень.
Дерево может на минуту пропасть (перезапуск Пульса): `runtime.health` отвечает ошибкой, пока папка не читается, —
Пульс перезапустит контейнер с новым монтированием.

## Страницы: мост `window.pulse` и встраивание (`embed`)

Обычные страницы меню (`ui`) открываются во фрейме **без** `allow-same-origin`: cookie владельца недоступны,
запросы — через мост `window.pulse`, от имени плагина и в пределах его прав.

Право **`embed`** — другое: страница живёт на `/plugin-apps/<id>/…` в одном origin с Пульсом, фрейм с
`allow-same-origin`, сессия владельца. Ставьте только плагины, которым доверяете; публичный каталог принимает
такие плагины только с открытым исходным кодом и после ревью.

Пункт меню может открывать встроенную страницу вместо страницы из `ui` (контракт 1.9): `"app": "/editor"` в
записи `menu` — страница `/plugin-apps/<id>/editor` процесса плагина во фрейме страницы `/p/<id>/<пункт>` (нужны
право `embed` и `runtime`; моста `window.pulse` там нет — страница ходит в API Пульса сама, с сессией владельца).
`page` оставьте: его покажет Пульс, который `app` ещё не знает.

Страницу плагина владелец разворачивает на всё окно и во весь экран (контракт 1.10). Встроенной странице Пульс
даёт ещё и чат рядом — тот же, что в «Чатах» (история, файлы, голос, выбор агента). Сообщения (`postMessage`,
только same-origin): страница шлёт `pulse:hello` и получает `pulse:host` `{features, expanded, fullscreen,
chat_open}` (он же приходит при каждой перемене); `pulse:chat-target` `{path, title?, hint?, placeholder?}` —
показать чат о файле каталога (`path: null` — убрать); `pulse:chat-toggle` `{open?}`, `pulse:expand`
`{expanded?}`, `pulse:fullscreen` `{on?}`. Перед каждым сообщением Пульс спрашивает `pulse:chat-context-request`
`{rid}` — страница отвечает `pulse:chat-context` `{rid, context}`: приписка (что открыто и выделено) уйдёт агенту,
в ленте чата её нет. Нет ответа `pulse:host` — Пульс старше 1.10: страница показывает свой чат.

```
    pulse.plugin(path, {method, params, body})  — запрос к своему серверу (/api/plugins/<id>/api/<path>)
    pulse.api(path, {method, params, body})     — запрос к API Пульса (права из манифеста)
    pulse.command(name, args)                   — команда плагина, как у агентов
    pulse.settings()                            — настройки плагина (без секретов)
    pulse.requestPrivileged(agent, reason)      — попросить владельца о полном доступе своему агенту (agents:privileged)
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

## Распознавание речи (`speech`)

Плагин с правом `speech:provide` и блоком `"speech": {"title": "OpenAI, Groq"}` распознаёт речь для Пульса:
голосовые и «кружочки» боту в Telegram, микрофон в поле ввода чатов. В Python — `pulse_plugin.speech` и
`@plugin.provide_speech`. Расшифровки по просьбе агентов (длинные записи, видео) — обычные команды плагина.

Ядро не знает ни одной модели: распознаёт включённый плагин с правом `speech:provide` и блоком `speech` в
манифесте. Протокол процесса плагина:

    POST /speech/transcribe   multipart: file — запись (аудио или видео), language — «ru», «en» или пусто
                              (определить), source — telegram | chat | agent
                              → {"text", "language", "duration_sec", "model"}

Ошибки — статус и {"detail": "текст"}: 400 — формат не понят, 413 — запись слишком длинная, 503 — не настроено
(нет ключей), 502 — модель не ответила. Распознавателей несколько — берётся выбранный владельцем
(общие настройки, `speech_plugin`; `PUT /api/speech`), иначе первый по id.

## Кто зовёт: владелец или агент (`X-Pulse-Caller`)

Каждый запрос к API и командам плагина приходит с заголовком `X-Pulse-Caller` (контракт 1.7): `owner` — владелец из
браузера (интерфейс Пульса или страница плагина через мост), `agent` — агенты, CLI, скрипты и планировщик, `plugin` —
сам плагин своим токеном. Заголовок ставит Пульс, подделать его снаружи нельзя. Необратимое — подтверждение оплаты,
перевода денег, отправки от имени владельца — принимайте только от `owner`. В Python — `plugin.caller()` и
`plugin.require_owner("Подтверждение оплаты")` (агенту — 403 с понятным текстом).

## Настройки агента от плагина (`agent_settings`, `X-Pulse-Agent-Settings`)

Контракт 1.8. Плагин может объявить в манифесте блок `agent_settings` — те же типы, что у `settings`
(string/number/integer/boolean), без секретов; у поля можно `warning` (предупреждение в UI). Пульс показывает
эти поля в Настройки → Агенты → доступ (с названием плагина) и хранит значения у агента в
`plugin_settings.<id плагина>`.

Когда агент вызывает команды или API **этого** плагина, Пульс ставит заголовок
`X-Pulse-Agent-Settings: {…}` — JSON значений для данного плагина (по `X-Pulse-Run` → агент; подделать нельзя).
Другим плагинам чужие настройки не уходят. В Python — `plugin.agent_settings()` и `plugin.agent_setting("key")`.

Пример в манифесте:

```json
"agent_settings": {
  "full_control": {
    "type": "boolean",
    "title": "Полный контроль браузера",
    "description": "Сессии сразу в write, оплата без подтверждения",
    "default": false,
    "warning": "Агент сможет оформлять заказы без вашего «Разрешить один раз»."
  }
}
```

## Зависимости от других плагинов (`dependencies`)

Контракт 1.12. `"dependencies": {"browser": {"version": ">=0.4", "optional": true, "reason": "сайты аналогов"}}`:
обязательную зависимость Пульс ставит из каталога вместе с плагином (нет в каталоге или версия не подходит — установка
не начнётся) и не даёт удалить, пока стоит зависящий плагин. Необязательную владелец выбирает галочкой при установке
(`POST /api/plugins` с `include: ["browser"]`), без неё плагин должен работать урезанно. Что сейчас установлено —
`GET /api/plugins/<id>/dependencies` → `[{id, name, version, optional, reason, installed, installed_version,
enabled, version_ok, ok}]`; проверяйте перед тем, как звать команды зависимости (`pulse <зависимость> …` у агентов).

## Свои агенты: модели и полный доступ

Контракт 1.12. Агенты плагина (`agents/*.md`) видны ему: `GET /api/plugins/<id>/agents` → `[{id, key, name,
cli_integration_id, integration, model, privileged, is_active}]` (`key` — имя файла без `.md`). С правом
`agents:models` — `GET /api/plugins/<id>/models` (интеграции и их модели) и `PATCH /api/plugins/<id>/agents/<key>`
`{"cli_integration_id": "…", "model": "sonnet"}` — так страница настроек плагина даёт выбрать модель каждому шагу.
Чужих агентов плагин не меняет. В заголовке `agents/<ключ>.md` можно `skills: tz-architect, tz-critic` — скиллы
этого же плагина (`skills/<имя>.md`, обычно с `default: false`), которые Пульс прикрепит к агенту при создании.

Полный доступ (привилегированный режим: поиск в интернете, любые команды) включает только владелец. Право
`agents:privileged` даёт странице плагина `await pulse.requestPrivileged("scout", "зачем")` — Пульс показывает
владельцу окно с названием плагина и агента, по «Разрешить» включает режим и отвечает `{"privileged": true}`
(«Отмена» — `false`). Выключить плагин может сам: `PATCH …/agents/<key>` `{"privileged": false}`.

## Фоновые задачи плагина (`bg`)

Контракт 1.12. Долгая работа из чата — фоновой задачей, как у агентов (§12.10 ядра): полоса над полем ввода, панель
«Ход работы», итоги в чат, остановка владельцем. Команда плагина получает заголовки `X-Pulse-Run` (запуск агента,
который её позвал) и `X-Pulse-Chat` (его чат). Дальше — API с правом `bg`, только свои задачи:

- `POST /api/bg-tasks` `{title, run_id, stages: ["Методолог", …], timeout_sec, on_done, wake}` — задача в чате
  этого запуска; без shell-команды, сразу «идёт». `run_id` — значение `X-Pulse-Run`.
- `POST /api/bg-tasks/<id>/steps` `{agent: "<key>", prompt, title?, timeout_sec?}` → `{id, status: "queued"}` —
  шаг: запуск своего агента в worker; его ход работы виден в задаче. `GET …/steps/<step>` → `status`
  (`queued`, `running`, `done`, `failed`, `stopped`, `interrupted` — перезапуск Пульса, повторите шаг), `result_text`,
  `error`. Одновременно в задаче — до 3 шагов.
- `POST /api/bg-tasks/<id>/progress` `{text, percent?, stage?, files?: ["Plugins/…/итог.md"]}` — итог в чат, номер
  текущего этапа (с нуля) и файлы-итоги (вкладка «Файлы» задачи).
- `POST /api/bg-tasks/<id>/message` `{prompt}` — разбудить агента чата: «задай владельцу эти вопросы, ответы
  передай командой `pulse <id> answer …`». Агент проснётся, как только чат свободен.
- `POST /api/bg-tasks/<id>/finish` `{status: "done"|"failed", text, error?}` — завершить; итог — в чат, агент чата
  проснётся с `on_done`.
- `GET /api/bg-tasks/<id>` — статус: владелец нажал «Остановить» — `stopped`, идущие шаги остановлены; прекратите
  работу. Состояние задачи держите у себя (`PULSE_PLUGIN_DATA` или своя база): после перезапуска Пульса задача
  продолжает идти, а прерванные шаги нужно повторить.

## Проверка подключения (`check`)

`"check": "<команда>"` в манифесте — команда из `commands.json`, которую Пульс вызывает кнопкой «Проверить
подключение» в Настройки → Плагины (и агент — как обычную команду). Ответ — `{"ok": true|false, "checks": [{"title":
"Доступ к API", "ok": true|false, "detail": "что не так и что сделать"}]}`: проверяйте по шагам (ключи заданы, сеть
до сервиса, вход), каждый шаг — строкой, с понятной подсказкой на неудачу. Отвечайте быстро — таймауты по 10–15 с.

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
