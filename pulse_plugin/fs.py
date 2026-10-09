"""Хранилище плагина — папка `@<id плагина>` в «Файлах», у `pulse files` и в дереве агентов (контракт 1.3).

Манифест: право `files:provide` и `"files": {"provider": {"title": "Мой диск", "icon": "bi-cloud"}}`. Плагин
описывает своё хранилище наследником FileProvider и подключает его:

    from pulse_plugin import Plugin
    from pulse_plugin.fs import FileProvider, NotFound

    class Disk(FileProvider):
        def list(self, path):            # path — относительно корня хранилища, "" — корень
            return [{"name": "отчёт.pdf", "kind": "file", "size": 1024, "modified_at": "2026-10-10T09:00:00Z"}]
        def stat(self, path): ...
        def read(self, path, offset=0, length=None): ...     # bytes или итератор bytes
        def write(self, path, stream, overwrite=True): ...   # stream — файловый объект (read(n))

    plugin = Plugin()
    plugin.provide_files(Disk())

Пульс ничего не копирует: каждая операция владельца или агента — запрос к /fs/… процесса плагина. Чего хранилище
не умеет (mkdir, move, delete), оставьте как есть — базовый класс ответит «только чтение». Ошибки — исключения
ниже: NotFound (404), Exists (409), NotEmpty (409), ReadOnly (403), FsError (400), NoSpace (507).
"""

import inspect
import re
import tempfile
from collections.abc import Iterable, Iterator
from typing import IO, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import iterate_in_threadpool, run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse

__all__ = ["FileProvider", "FsError", "NotFound", "Exists", "NotEmpty", "ReadOnly", "NoSpace", "mount"]

SPOOL_BYTES = 8 * 1024 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


class FsError(Exception):
    status = 400

    def __init__(self, message: str = ""):
        super().__init__(message or self.__class__.__doc__ or "ошибка хранилища")


class NotFound(FsError):
    """Не найдено"""
    status = 404


class Exists(FsError):
    """Уже существует"""
    status = 409


class NotEmpty(FsError):
    """Папка не пуста"""
    status = 409


class ReadOnly(FsError):
    """Только для чтения"""
    status = 403


class NoSpace(FsError):
    """Нет места в хранилище"""
    status = 507


def entry(name: str, kind: str = "file", size: int | None = None, modified_at: Any = None,
          readonly: bool = False, mime: str | None = None) -> dict:
    """Элемент списка: kind — dir | file; modified_at — ISO-строка или datetime."""
    if hasattr(modified_at, "isoformat"):
        modified_at = modified_at.isoformat()
    return {"name": name, "kind": kind, "size": size if kind == "file" else None, "modified_at": modified_at,
            "readonly": readonly, "mime": mime}


class FileProvider:
    """Хранилище плагина. Пути — относительные, через «/», без «..»; корень — пустая строка."""

    readonly = False                     # всё хранилище только для чтения

    def list(self, path: str) -> list[dict]:
        raise NotFound(path)

    def stat(self, path: str) -> dict:
        """Элемент по пути. По умолчанию — из списка родительской папки."""
        if not path:
            return entry("", "dir", readonly=self.readonly)
        parent, _, name = path.rpartition("/")
        for item in self.list(parent):
            if item.get("name") == name:
                return item
        raise NotFound(path)

    def read(self, path: str, offset: int = 0, length: int | None = None) -> bytes | Iterable[bytes]:
        raise NotFound(path)

    def write(self, path: str, stream: IO[bytes], overwrite: bool = True) -> dict:
        raise ReadOnly("хранилище только для чтения")

    def mkdir(self, path: str) -> dict:
        raise ReadOnly("хранилище только для чтения")

    def move(self, source: str, target: str) -> dict:
        raise ReadOnly("хранилище только для чтения")

    def delete(self, path: str, recursive: bool = False) -> None:
        raise ReadOnly("хранилище только для чтения")


def clean(path: str) -> str:
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise FsError("путь выходит за пределы хранилища")
    return "/".join(parts)


async def _call(fn, *args):
    if inspect.iscoroutinefunction(fn):
        return await fn(*args)
    return await run_in_threadpool(fn, *args)


def mount(app: FastAPI, provider: FileProvider) -> None:
    """Маршруты /fs/… для Пульса (их вызывает только ядро, через свою сеть)."""

    def guard(handler):
        async def wrapper(*args, **kwargs):
            try:
                return await handler(*args, **kwargs)
            except FsError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=exc.status)
        wrapper.__name__ = handler.__name__
        wrapper.__signature__ = inspect.signature(handler)
        return wrapper

    @app.get("/fs/list")
    @guard
    async def fs_list(path: str = "") -> Any:
        items = await _call(provider.list, clean(path))
        return {"items": list(items), "readonly": provider.readonly}

    @app.get("/fs/stat")
    @guard
    async def fs_stat(path: str = "") -> Any:
        return await _call(provider.stat, clean(path))

    @app.get("/fs/read")
    @guard
    async def fs_read(request: Request, path: str) -> Any:
        rel = clean(path)
        info = await _call(provider.stat, rel)
        if info.get("kind") == "dir":
            raise FsError("это папка")
        size = info.get("size")
        offset, length, status = 0, None, 200
        headers = {"Accept-Ranges": "bytes"}
        match = _RANGE.match(request.headers.get("range") or "")
        if match and size is not None and (match.group(1) or match.group(2)):
            start, end = match.group(1), match.group(2)
            if start:
                offset = int(start)
                last = min(int(end), size - 1) if end else size - 1
            else:                                   # bytes=-N — последние N байт
                offset, last = max(size - int(end), 0), size - 1
            if offset >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            length, status = last - offset + 1, 206
            headers["Content-Range"] = f"bytes {offset}-{last}/{size}"
        data = await _call(provider.read, rel, offset, length)
        if isinstance(data, (bytes, bytearray)):
            return Response(bytes(data), status_code=status, headers=headers,
                            media_type="application/octet-stream")
        body = data if hasattr(data, "__aiter__") else iterate_in_threadpool(_iter(data))
        return StreamingResponse(body, status_code=status, headers=headers, media_type="application/octet-stream")

    @app.put("/fs/write")
    @guard
    async def fs_write(request: Request, path: str, overwrite: bool = True) -> Any:
        rel = clean(path)
        if not rel:
            raise FsError("укажите путь к файлу")
        with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES) as spool:
            async for chunk in request.stream():
                spool.write(chunk)
            spool.seek(0)
            return await _call(provider.write, rel, spool, overwrite)

    @app.post("/fs/mkdir")
    @guard
    async def fs_mkdir(request: Request) -> Any:
        body = await request.json()
        return await _call(provider.mkdir, clean(body.get("path", "")))

    @app.post("/fs/move")
    @guard
    async def fs_move(request: Request) -> Any:
        body = await request.json()
        return await _call(provider.move, clean(body.get("source", "")), clean(body.get("target", "")))

    @app.delete("/fs/delete")
    @guard
    async def fs_delete(path: str, recursive: bool = False) -> Any:
        rel = clean(path)
        if not rel:
            raise HTTPException(400, "корень хранилища не удаляется")
        await _call(provider.delete, rel, recursive)
        return {"deleted": rel}


def _iter(data: Iterable[bytes]) -> Iterator[bytes]:
    yield from data
