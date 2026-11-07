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
import time
from collections.abc import Callable
from contextvars import ContextVar
from functools import cached_property
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

__all__ = ["Plugin", "PulseApi", "PulseApiError", "CommandError", "FileTicket"]


class TaskStopped(Exception):
    """Владелец остановил фоновую задачу (или вышел её срок) — прекратите работу."""


class BgTask:
    """Фоновая задача плагина (контракт 1.12): шаги — запуски своих агентов, итоги, этапы, вопросы через агента чата.
    Методы синхронные (httpx): ведите задачу в своём потоке или фоновой задаче процесса."""

    def __init__(self, api: "PulseApi", data: dict):
        self.api = api
        self.data = data
        self.id = data["id"]

    def refresh(self) -> dict:
        self.data = self.api.get(f"/api/bg-tasks/{self.id}")
        return self.data

    @property
    def active(self) -> bool:
        return self.refresh()["status"] in ("queued", "running")

    def step(self, agent: str, prompt: str, title: str | None = None, timeout_sec: int = 3600, *,
             integration: str | None = None, model: str | None = None, web: str = "none") -> str:
        """Поставить шаг (запуск своего агента); вернуть id шага.
        Контракт 1.14: integration и model — модель этого шага вместо модели агента (право agents:models);
        web — интернет на шаге (право agents:web): "none", "native" (веб-поиск самой модели) или "browser"
        (только команды pulse browser — правила работы с вкладкой пишите в prompt)."""
        body: dict[str, Any] = {"agent": agent, "prompt": prompt, "timeout_sec": timeout_sec, "web": web}
        if title:
            body["title"] = title
        if integration:
            body["integration"] = integration
        if model:
            body["model"] = model
        return self.api.post(f"/api/bg-tasks/{self.id}/steps", body)["id"]

    def wait_step(self, step_id: str, poll: float = 3.0) -> dict:
        """Дождаться шага: {status, result_text, error}. Задачу остановили — TaskStopped."""
        while True:
            step = self.api.get(f"/api/bg-tasks/{self.id}/steps/{step_id}")
            if step["status"] not in ("queued", "running"):
                if step["status"] == "stopped" and not self.active:
                    raise TaskStopped(self.id)
                return step
            time.sleep(poll)

    def run_step(self, agent: str, prompt: str, title: str | None = None, timeout_sec: int = 3600,
                 retries: int = 1, **options) -> str:
        """Шаг целиком: поставить, дождаться, вернуть ответ агента. Прерван перезапуском Пульса — повторить.
        options — integration, model, web, как у step."""
        for attempt in range(retries + 1):
            step = self.wait_step(self.step(agent, prompt, title, timeout_sec, **options))
            if step["status"] == "done":
                return step.get("result_text") or ""
            if step["status"] != "interrupted" or attempt == retries:
                raise CommandError(f"Шаг «{title or agent}» не удался: {step.get('error') or step['status']}")
        return ""

    def progress(self, text: str = "", *, stage: int | None = None, files: list[str] | None = None,
                 percent: float | None = None) -> dict:
        body: dict[str, Any] = {"text": text}
        if stage is not None:
            body["stage"] = stage
        if files:
            body["files"] = files
        if percent is not None:
            body["percent"] = percent
        self.data = self.api.post(f"/api/bg-tasks/{self.id}/progress", body)
        return self.data

    def message(self, prompt: str) -> dict:
        """Разбудить агента чата: передать владельцу вопросы или итог; ответ вернётся командой плагина."""
        return self.api.post(f"/api/bg-tasks/{self.id}/message", {"prompt": prompt})

    def finish(self, text: str = "", *, failed: bool = False, error: str | None = None) -> dict:
        body: dict[str, Any] = {"status": "failed" if failed else "done", "text": text}
        if error:
            body["error"] = error
        self.data = self.api.post(f"/api/bg-tasks/{self.id}/finish", body)
        return self.data


class PulseApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class CommandError(Exception):
    """Ошибка команды, понятная агенту: отдаётся как 400 с текстом."""


class FileTicket:
    """Access to one catalog file via /api/plugin-files/{ticket} (K4/K6)."""

    def __init__(self, api: "PulseApi", ticket: str, meta: dict[str, Any] | None = None):
        self._api = api
        self.ticket = ticket
        self._meta = meta

    def meta(self) -> dict[str, Any]:
        if self._meta is None:
            self._meta = self._api.get(f"/api/plugin-files/{self.ticket}")
        return self._meta

    def read(self) -> bytes:
        response = self._api._http.get(f"/api/plugin-files/{self.ticket}/content")
        if response.status_code >= 400:
            raise PulseApiError(f"{response.status_code}: {response.text}", response.status_code)
        return response.content

    def write(
        self, data: bytes, *, if_match: str | None = None, on_conflict: str = "error",
    ) -> dict[str, Any]:
        headers = {"If-Match": if_match} if if_match else None
        response = self._api._http.put(
            f"/api/plugin-files/{self.ticket}/content",
            content=data,
            headers=headers,
            params={"on_conflict": on_conflict},
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise PulseApiError(f"{response.status_code}: {detail}", response.status_code)
        return response.json() if response.content else {"saved": True}


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


_CALLER: ContextVar[str] = ContextVar("pulse_caller", default="agent")
_AGENT_SETTINGS: ContextVar[dict] = ContextVar("pulse_agent_settings", default={})
# контракт 1.12: запуск агента и его чат, откуда позвали команду
_RUN: ContextVar[str | None] = ContextVar("pulse_run", default=None)
_CHAT: ContextVar[str | None] = ContextVar("pulse_chat", default=None)
CALLERS = ("owner", "agent", "plugin")


def _parse_agent_settings(request: Request) -> dict:
    """X-Pulse-Agent-Settings от ядра (контракт 1.8); старый X-Pulse-Browser-Full-Control → full_control."""
    raw = (request.headers.get("x-pulse-agent-settings") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return data
    # совместимость с ядром до generic agent_settings
    legacy = (request.headers.get("x-pulse-browser-full-control") or "").strip().lower()
    if legacy in ("1", "true", "yes", "on"):
        return {"full_control": True}
    return {}


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
        # external path of embed pages: <base_path>/plugin-apps/<id>
        self.app_path = env.get("PULSE_PLUGIN_APP_PATH", f"/plugin-apps/{self.id}")
        self._commands: dict[str, dict] = {}
        self.app = FastAPI(title=title or self.name)

        @self.app.middleware("http")
        async def _caller(request: Request, call_next):
            # контракт 1.7: кто зовёт — owner (владелец из браузера), agent (агенты, CLI, планировщик), plugin
            value = (request.headers.get("x-pulse-caller") or "agent").strip().lower()
            token = _CALLER.set(value if value in CALLERS else "agent")
            # контракт 1.8: agent_settings этого плагина у агента запуска (ставит ядро)
            caps = _AGENT_SETTINGS.set(_parse_agent_settings(request))
            run = _RUN.set(request.headers.get("x-pulse-run") or None)
            chat = _CHAT.set(request.headers.get("x-pulse-chat") or None)
            try:
                return await call_next(request)
            finally:
                _CALLER.reset(token)
                _AGENT_SETTINGS.reset(caps)
                _RUN.reset(run)
                _CHAT.reset(chat)

        self._routes()

    # --- окружение -------------------------------------------------------------------------------------

    @staticmethod
    def caller() -> str:
        """Кто зовёт текущий запрос или команду: owner | agent | plugin (нет заголовка — agent)."""
        return _CALLER.get()

    @staticmethod
    def agent_settings() -> dict:
        """Настройки агента для этого плагина (манифест agent_settings, заголовок от ядра). Пусто — не агент."""
        return dict(_AGENT_SETTINGS.get() or {})

    @staticmethod
    def agent_setting(key: str, default: Any = None) -> Any:
        """Одна настройка агента из agent_settings манифеста."""
        return (_AGENT_SETTINGS.get() or {}).get(key, default)

    @staticmethod
    def run_id() -> str | None:
        """Запуск агента, позвавшего команду (X-Pulse-Run, контракт 1.12): по нему ставится фоновая задача в его чат."""
        return _RUN.get()

    @staticmethod
    def chat_id() -> str | None:
        """Чат агента, позвавшего команду (X-Pulse-Chat), или None — команду позвали не из чата."""
        return _CHAT.get()

    def require_owner(self, what: str = "Это действие") -> None:
        """Только владелец из браузера: подтверждение оплаты, вход, необратимое. Агенту — 403 с понятным текстом."""
        if self.caller() != "owner":
            raise HTTPException(403, f"{what} подтверждает только владелец — в интерфейсе Пульса, не агент")

    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def secret(self, key: str) -> str | None:
        return self.secrets.get(key)

    @property
    def fs_dir(self) -> Path | None:
        """Дерево «Файлов» владельца (право files:mount, контракт 1.11): каталог, карточки, хранилища @… — или None."""
        value = os.environ.get("PULSE_PLUGIN_FS_DIR")
        return Path(value) if value else None

    def fs_path(self, path: str) -> Path:
        """Файл владельца в процессе плагина: путь как в «Файлах» («Документы/a.csv») или абсолютный путь агента
        ($PULSE_FS_DIR/…, его корень — PULSE_PLUGIN_FS_HOST_DIR). Выход за дерево — CommandError."""
        root = self.fs_dir
        if root is None:
            raise CommandError("Нет дерева «Файлов»: плагину нужно право files:mount")
        value = str(path or "").strip().replace("\\", "/")
        for prefix in (os.environ.get("PULSE_PLUGIN_FS_HOST_DIR", ""), root.as_posix()):
            prefix = prefix.rstrip("/")
            if prefix and (value == prefix or value.startswith(prefix + "/")):
                value = value[len(prefix):]
                break
        parts = [part for part in value.split("/") if part not in ("", ".")]
        if ".." in parts:
            raise CommandError(f"Путь за пределами «Файлов»: {path}")
        return root.joinpath(*parts)

    def service_url(self, service_id: str) -> str:
        """Internal URL of a sidecar service (PULSE_SERVICE_<ID>_URL)."""
        key = f"PULSE_SERVICE_{service_id.upper().replace('-', '_')}_URL"
        url = os.environ.get(key)
        if not url:
            raise CommandError(f"Сервис «{service_id}» не задан ({key})")
        return url

    def file_ticket(self, ticket: str) -> FileTicket:
        """Open a file ticket issued by the core (Files panel or agent file arg)."""
        return FileTicket(self.api, ticket)

    def file(self, arg: Any) -> FileTicket:
        """Resolve a command file/file-out argument (dict with ticket, or raw ticket string)."""
        if isinstance(arg, dict) and arg.get("ticket"):
            return FileTicket(self.api, arg["ticket"], {
                "name": arg.get("name"), "path": arg.get("path"),
            })
        if isinstance(arg, str) and arg:
            return FileTicket(self.api, arg)
        raise CommandError("Ожидался аргумент файла с билетом")

    def provide_files(self, provider) -> None:
        """Хранилище плагина — папка @<id> в «Файлах» и у агентов (pulse_plugin.fs.FileProvider, право files:provide)."""
        from pulse_plugin.fs import mount

        mount(self.app, provider)

    def provide_llm(self, models, connection) -> None:
        """Шлюз к моделям для агентов (pulse_plugin.llm, право llm:provide, контракт 1.13)."""
        from pulse_plugin.llm import mount

        mount(self.app, models, connection)

    def save_settings(self, settings: dict | None = None, secrets: dict | None = None) -> dict:
        """Свои настройки и секреты (контракт 1.13): Пульс сохранит их и перезапустит процесс с новым окружением."""
        return self.api.request("PUT", f"/api/plugins/{self.id}/settings",
                                json={"settings": settings or {}, "secrets": secrets or {}})

    def provide_speech(self, handler):
        """Распознавание речи для Пульса (pulse_plugin.speech, право speech:provide); годится как декоратор."""
        from pulse_plugin.speech import mount

        mount(self.app, handler)
        return handler

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

    # --- зависимости и свои агенты (контракт 1.12) -------------------------------------------------------

    def dependencies(self) -> dict[str, dict]:
        """Зависимости из манифеста с состоянием: {id: {installed, installed_version, enabled, version_ok, ok, …}}."""
        return {d["id"]: d for d in self.api.get(f"/api/plugins/{self.id}/dependencies")}

    def dependency_ok(self, plugin_id: str) -> bool:
        """Зависимость стоит, включена и подходит по версии — её командами можно пользоваться."""
        return bool(self.dependencies().get(plugin_id, {}).get("ok"))

    def agents(self) -> dict[str, dict]:
        """Свои агенты по ключу (agents/<ключ>.md): интеграция, модель, полный доступ."""
        return {a["key"]: a for a in self.api.get(f"/api/plugins/{self.id}/agents")}

    def models(self) -> list[dict]:
        """CLI-интеграции Пульса и их модели (право agents:models): {id, name, kind, models, native_web}."""
        return self.api.get(f"/api/plugins/{self.id}/models")

    def call(self, plugin_id: str, command: str, timeout: float | None = None, **args) -> Any:
        """Команда плагина-зависимости, как `pulse <плагин> <команда> --арг …` у агента (контракт 1.14, право
        plugins:call; плагин — из dependencies манифеста)."""
        name = command.strip().replace(" ", "/")
        return self.api.request("POST", f"/api/plugins/{plugin_id}/commands/{name}",
                                json={"args": {k: v for k, v in args.items() if v is not None}},
                                timeout=timeout or 600)

    def read_file(self, path: str) -> str:
        """Текст файла из «Файлов» — своей папки (право files) или выбранного в настройке type: file (1.14)."""
        return self.api.request("GET", "/api/files/text", params={"path": path})["text"]

    def set_agent_model(self, key: str, integration_id: str | None = None, model: str | None = None) -> dict:
        """Модель своего агента (agents:models); пустая model — модель интеграции по умолчанию."""
        body: dict[str, Any] = {"model": model or ""}
        if integration_id:
            body["cli_integration_id"] = integration_id
        return self.api.patch(f"/api/plugins/{self.id}/agents/{key}", body)

    def drop_privileged(self, key: str) -> dict:
        """Выключить полный доступ своему агенту. Включает только владелец: pulse.requestPrivileged на странице."""
        return self.api.patch(f"/api/plugins/{self.id}/agents/{key}", {"privileged": False})

    def bg_task(self, title: str, *, run_id: str | None = None, stages: list[str] | None = None,
                timeout_sec: int = 24 * 3600, on_done: str | None = None, wake: bool = True,
                chat_id: str | None = None) -> "BgTask":
        """Фоновая задача в чате агента, позвавшего команду (право bg). run_id — по умолчанию из X-Pulse-Run команды;
        chat_id — чат явно (страница плагина с правом chat сама заводит чат)."""
        run_id = run_id or (None if chat_id else self.run_id())
        if not run_id and not chat_id:
            raise CommandError("Фоновую задачу ставят из чата: команду должен позвать агент в чате")
        body: dict[str, Any] = {"title": title, "stages": stages or [], "timeout_sec": timeout_sec, "wake": wake}
        if run_id:
            body["run_id"] = run_id
        if chat_id:
            body["chat_id"] = chat_id
        if on_done:
            body["on_done"] = on_done
        return BgTask(self.api, self.api.post("/api/bg-tasks", body))

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
