"""pulse_plugin — основа серверной части плагина «Пульса» (docs/architecture.md §13).

Пульс запускает процесс плагина (`uvicorn <runtime.entry>`) и передаёт ему окружение: адрес API, токен
плагина (права из манифеста), настройки и секреты, свою базу MongoDB и папки. Плагин:

    from pulse_plugin import Plugin

    plugin = Plugin()
    app = plugin.app                         # FastAPI: /health и /commands/... уже есть

    @plugin.command("transactions list", "Операции за месяц", args={"month": "месяц ГГГГ-ММ"})
    def transactions(args):
        return {"items": [...]}              # ответ команды — JSON (агент вызывает: pulse <id> transactions list --month 2026-10)

    @app.get("/summary")                     # свой API — для страниц плагина: pulse.plugin("/summary")
    def summary():
        return {"total": 42}

    plugin.signal("Превышен бюджет на еду", priority=80, tags=["финансы"])   # сигнал в ленту (signals:create)
"""

import inspect
import json
import os
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

__all__ = ["Plugin", "PulseApi", "PulseApiError", "CommandError"]


class PulseApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class CommandError(Exception):
    """Ошибка команды, понятная агенту: отдаётся как 400 с текстом."""


class PulseApi:
    """Клиент API Пульса с токеном плагина: можно только то, что заявлено в permissions манифеста."""

    def __init__(self, url: str, token: str, timeout: float = 60):
        self._http = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)

    def request(self, method: str, path: str, **kwargs) -> Any:
        response = self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise PulseApiError(f"{response.status_code}: {detail}", response.status_code)
        return response.json() if response.content else None

    def get(self, path: str, **params) -> Any:
        return self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, json=body)

    def patch(self, path: str, body: Any = None) -> Any:
        return self.request("PATCH", path, json=body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)


class Plugin:
    def __init__(self, title: str | None = None):
        env = os.environ
        self.id = env.get("PULSE_PLUGIN_ID", "dev")
        self.name = env.get("PULSE_PLUGIN_NAME", self.id)
        self.version = env.get("PULSE_PLUGIN_VERSION", "0")
        self.settings: dict[str, Any] = json.loads(env.get("PULSE_PLUGIN_SETTINGS") or "{}")
        self.secrets: dict[str, str] = json.loads(env.get("PULSE_PLUGIN_SECRETS") or "{}")
        self.data_dir = Path(env.get("PULSE_PLUGIN_DATA") or Path.cwd() / ".plugin-data")
        self.files_dir = Path(env.get("PULSE_PLUGIN_FILES") or self.data_dir / "files")
        self.folder = env.get("PULSE_PLUGIN_FOLDER", f"Plugins/{self.name}")
        self._commands: dict[str, dict] = {}
        self.app = FastAPI(title=title or self.name)
        self._routes()

    # --- окружение -------------------------------------------------------------------------------------

    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def secret(self, key: str) -> str | None:
        return self.secrets.get(key)

    @cached_property
    def api(self) -> PulseApi:
        return PulseApi(os.environ.get("PULSE_API_URL", "http://127.0.0.1:8000"), os.environ.get("PULSE_PLUGIN_TOKEN", ""))

    @cached_property
    def db(self):
        """Своя база плагина в MongoDB платформы (pymongo)."""
        import pymongo

        url = os.environ.get("PULSE_PLUGIN_MONGO_URL", "mongodb://localhost:27017")
        return pymongo.MongoClient(url)[os.environ.get("PULSE_PLUGIN_MONGO_DB", f"plugin_{self.id}")]

    # --- данные платформы ----------------------------------------------------------------------------

    def signal(self, title: str, body: str = "", priority: int = 60, tags: list[str] | None = None, **fields) -> dict:
        """Сигнал в ленту владельца (право signals:create): приоритет ≥ 85 — срочно, 70–84 — важно."""
        return self.api.post("/api/cards", {"type": "signal", "title": title, "body": body, "priority": priority,
                                            "tags": tags or [], **fields})

    # --- команды ----------------------------------------------------------------------------------------

    def command(self, name: str, description: str = "", args: dict[str, str] | None = None) -> Callable:
        """Команда для агентов: `pulse <id плагина> <name> --арг значение`. Обработчик получает dict аргументов.
        Описание команд для справки агентов — в commands.json плагина (держите их одинаковыми)."""
        def register(fn: Callable) -> Callable:
            self._commands[name] = {"fn": fn, "description": description, "args": args or {}}
            return fn
        return register

    def _routes(self) -> None:
        @self.app.get("/health")
        def health() -> dict:
            return {"ok": True, "plugin": self.id, "version": self.version}

        @self.app.post("/commands/{name:path}")
        async def run_command(name: str, request: Request) -> Any:
            entry = self._commands.get(name.replace("/", " ").strip())
            if entry is None:
                raise HTTPException(404, f"Нет команды «{name}»")
            raw = await request.body()
            args = json.loads(raw) if raw else {}
            try:
                fn = entry["fn"]
                return await fn(args) if inspect.iscoroutinefunction(fn) else await run_in_threadpool(fn, args)
            except CommandError as exc:
                raise HTTPException(400, str(exc)) from exc
