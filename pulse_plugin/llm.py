"""Шлюз к моделям для агентов Пульса (контракт 1.13): 9Router, LiteLLM и подобные.

Манифест: право `llm:provide` и `"llm": {"gateway": {"title": "9Router"}}`. Плагин отдаёт Пульсу две вещи:

    from pulse_plugin import Plugin
    from pulse_plugin.llm import Connection, Model, Unavailable

    plugin = Plugin()

    def models() -> list[Model]:
        return [Model("ds/deepseek-flash", "DeepSeek V4.1 Flash"), Model("fast-cheap", kind="combo")]

    def connection() -> Connection:
        # Anthropic-совместимый адрес без /v1 (Claude Code сам дописывает /v1/messages)…
        return Connection(base_url="http://127.0.0.1:20128", auth_token=plugin.secret("api_key"))
        # …или сервис плагина из манифеста: Пульс сам найдёт его адрес
        return Connection(service="router", auth_token=...)

    plugin.provide_llm(models=models, connection=connection)

Пульс зовёт GET /llm/models для «+ CLI → Модель через шлюз» и GET /llm/connection перед каждым запуском агента на
такой интеграции: ключ шлюза хранится только в плагине. Шлюз не готов — Unavailable(«почему», 503).
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool

__all__ = ["Connection", "Model", "Unavailable", "mount"]


@dataclass
class Model:
    id: str
    title: str = ""
    kind: str = "model"          # model | combo (цепочка моделей шлюза)


@dataclass
class Connection:
    base_url: str = ""
    service: str = ""            # id сервиса плагина вместо base_url
    auth_token: str = ""


class Unavailable(Exception):
    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


async def _call(fn: Callable):
    return await fn() if inspect.iscoroutinefunction(fn) else await run_in_threadpool(fn)


def mount(app: FastAPI, models: Callable[[], list], connection: Callable[[], Connection]) -> None:
    @app.get("/llm/models")
    async def llm_models() -> dict:
        try:
            items = await _call(models)
        except Unavailable as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        out = []
        for item in items or []:
            if isinstance(item, Model):
                out.append({"id": item.id, "title": item.title, "kind": item.kind})
            elif isinstance(item, dict) and item.get("id"):
                out.append({"id": str(item["id"]), "title": str(item.get("title") or ""),
                            "kind": "combo" if item.get("kind") == "combo" else "model"})
        return {"models": out}

    @app.get("/llm/connection")
    async def llm_connection() -> dict:
        try:
            conn = await _call(connection)
        except Unavailable as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        if not conn.base_url and not conn.service:
            raise HTTPException(503, "Шлюз не настроен: нет адреса")
        out = {"auth_token": conn.auth_token or ""}
        if conn.service:
            out["service"] = conn.service
        else:
            out["base_url"] = conn.base_url.rstrip("/")
        return out
