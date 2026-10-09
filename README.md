# pulse-plugin — SDK плагинов «Пульса»

Всё, что нужно, чтобы написать плагин «Пульса» без доступа к ядру платформы:

- **`pulse_plugin`** — серверная часть плагина: окружение, команды для агентов, клиент API с правами плагина,
  сигналы, своя база (`Plugin`, `CommandError`).
- **`pulse_plugin.fs`** — хранилище плагина папкой `@<id>` в «Файлах» и у агентов (`FileProvider`,
  `plugin.provide_files()`, право `files:provide`).
- **`pulse_plugin.testing`** — тесты без Пульса: подменный API (`fake_api(plugin, monkeypatch)`).
- **`pulse-plugin`** — утилита разработчика: `new`, `check`, `pack`, `dev`, `publish`.
- **[CONTRACT.md](CONTRACT.md)** — справочник контракта: манифест, права, окружение процесса, мост страниц,
  дизайн, формат каталога и выпуск. **`manifest.schema.json`** — JSON Schema манифеста (подсказки в редакторе).

```bash
pip install "pulse-plugin @ git+https://github.com/pulse-assist/pulse-plugin-sdk@v1.3.0"
pulse-plugin new weather --name "Погода"     # плагин из шаблона pulse-assist/pulse-plugin-template
cd pulse-weather && pulse-plugin check
python -m pytest backend                      # тесты с pulse_plugin.testing
pulse-plugin dev --pulse https://мой-пульс/api-адрес --token …   # поставить на свой Пульс, --watch — при правке
git tag v0.1.0 && git push --tags             # CI шаблона собирает релиз: сборка + запись для каталога
pulse-plugin publish ../pulse-plugins --changes "Первая версия"   # затем MR в каталог
```

Как писать плагин — `AGENTS.md` в шаблоне (его читают и Claude Code, и другие агенты).

Пакет выпускается из репозитория ядра (`plugin_sdk/`): правки — через сопровождающих «Пульса»; версия пакета
следует версии контракта плагинов.
